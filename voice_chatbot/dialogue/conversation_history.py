"""
对话历史管理模块

ConversationHistory 是对话历史的单一所有者（single owner），
集中管理用户/助手消息的追加、同句合并、裁剪、清空与快照，
收敛原先散落在 manager.py / llm_openai_stream.py / state.py 中的历史管理逻辑。

设计要点：
- 内部使用 threading.Lock 保护所有读写，调用方无需额外加锁
- 只允许原位修改（del 切片裁剪、原位修改 dict 内容），禁止对内部列表 rebind，
  避免持有旧引用的调用方读到永久冻结的历史（P0-1 类回归）
- "待定当前输入"按 dict 对象身份（is）跟踪：record_user 写入的最后一条 user 记录
  即本轮当前输入；snapshot_for_llm 按身份排除它——即使该 dict 在合并窗口内被
  后续输入原位合并，对象身份不变，排除依然正确（天然抗合并竞态）

本模块只依赖标准库（不导入 utils/manager 等重依赖链），便于独立单元测试；
相关日志由调用方（manager）在调用前后输出。
"""

import re
import threading
import time
from typing import Callable, Dict, List, Optional

# 句子结束符结尾模式（移植 state.py DialogueState.add_conversation 的截断标记判断）
_SENTENCE_END_RE = re.compile(r'[。！？.!?\n]+$')


class ConversationHistory:
    """对话历史记录器（唯一所有者）"""

    def __init__(self, max_records: int,
                 merge_window_s: float = 10.0,
                 max_merged_chars: int = 200,
                 now_fn: Callable[[], float] = time.time):
        """
        初始化对话历史记录器

        Args:
            max_records: 最大历史记录长度（对话轮数，每轮包含用户和助手两条消息）
            merge_window_s: 用户输入合并窗口（秒），窗口内的连续输入视为同一句话（VAD切分）
            max_merged_chars: 合并后的最大字符数，超过则视为两次独立输入
            now_fn: 时间函数（可注入，便于测试）
        """
        self._records: List[Dict[str, str]] = []
        self._lock = threading.Lock()
        self.max_records = max_records
        self.merge_window_s = merge_window_s
        self.max_merged_chars = max_merged_chars
        self._now_fn = now_fn
        self._last_user_record_time = 0.0  # 上次用户输入入史时间（0.0表示合并窗口关闭）
        self._pending_input: Optional[Dict[str, str]] = None  # 待定当前输入（按对象身份跟踪）

    def record_user(self, text: str, prev_turn_audible: bool) -> str:
        """
        记录用户输入，返回应作为本轮处理的有效输入文本

        若上一条记录也是用户输入且间隔在合并窗口内（说明上一轮回复尚未生成/播完
        就被新输入顶掉，通常是一句话被VAD静音阈值切开），直接原位合并到最后一条
        而不是追加，避免历史中出现连续的user记录。

        合并条件（全部同时满足）：
        1. 上一条记录是用户输入
        2. 距上次用户输入入史在合并窗口内
        3. 合并后长度不超过 max_merged_chars（超过则视为两次独立输入，正常追加）
        4. 上一话轮机器人未实际播出声音（prev_turn_audible=False；
           机器人播出过声音=独立话轮，连续输入是新的话轮，不是VAD切分）

        注意：合并必须原位修改最后一条 dict（保持对象身份），因为该 dict 可能已被
        记为"待定当前输入"，snapshot_for_llm 依赖对象身份排除它

        Args:
            text: 用户输入文本
            prev_turn_audible: 上一话轮机器人是否实际播出过音频（独立话轮标志）

        Returns:
            本轮处理应使用的文本（合并时为合并后的文本，否则为原文本）
        """
        with self._lock:
            now = self._now_fn()
            last = self._records[-1] if self._records else None

            if (last is not None and last.get("role") == "user"
                    and not prev_turn_audible
                    and now - self._last_user_record_time < self.merge_window_s):
                merged = (last["content"].rstrip("，。,. 、") + "，" + text).strip()
                # 合并后过长则视为两次独立输入，正常追加
                if len(merged) <= self.max_merged_chars:
                    # 原位修改最后一条（保持对象身份，待定输入的排除跟踪依然有效）
                    last["content"] = merged
                    self._last_user_record_time = now
                    self._pending_input = last
                    self._trim()
                    return merged

            record = {"role": "user", "content": text}
            self._records.append(record)
            self._last_user_record_time = now
            # 把本次 user dict 记为"待定当前输入"（身份跟踪）
            self._pending_input = record
            self._trim()
            return text

    def record_assistant(self, text: str, interrupted: bool = False) -> bool:
        """
        记录助手回复（播放了多少就记录多少）

        - 去除前导空白字符，空文本跳过（不入史）
        - 被打断且非空时，若文本不以句子结束符结尾则末尾追加省略号截断标记
          （移植 state.py DialogueState.add_conversation 的设计）
        - 机器人说过话：重置用户输入合并窗口（连续输入是新的话轮，不是VAD切分）
        - 清除"待定当前输入"标记（本轮输入已被回复消费）

        Args:
            text: 回复文本
            interrupted: 是否被打断（打断的回复追加截断标记）

        Returns:
            是否真正记录（空文本跳过时返回False）
        """
        with self._lock:
            # 去除前导空白字符（包括换行符、空格等）；空文本跳过
            text_trimmed = (text or "").lstrip()
            if not text_trimmed:
                return False

            # 被打断的回复：以句子结束符（。！？.!?\n）结尾的完整句子不加标记，
            # 否则末尾追加省略号标记表示被截断
            if interrupted:
                if not _SENTENCE_END_RE.search(text_trimmed.strip()):
                    text_trimmed = text_trimmed + "..."

            self._records.append({"role": "assistant", "content": text_trimmed})
            # 机器人说过话：关闭用户输入合并窗口
            self._last_user_record_time = 0.0
            # 待定当前输入已被本轮回复消费，清除标记
            self._pending_input = None
            self._trim()
            return True

    def clear(self):
        """原位清空历史并复位内部时间戳与待定输入标记"""
        with self._lock:
            self._records.clear()
            self._last_user_record_time = 0.0
            self._pending_input = None

    def snapshot_for_llm(self) -> List[dict]:
        """
        获取供LLM使用的历史快照（副本），排除"待定当前输入"

        排除按 dict 对象身份（is）进行：record_user 写入的记录即使被窗口内的
        后续输入原位合并，对象身份不变，排除依然正确（天然抗合并竞态）。
        当前输入由调用方（LLM构建消息时）单独携带。

        Returns:
            历史副本列表（每条为新建 dict，修改不影响内部状态）
        """
        with self._lock:
            return [
                {"role": r.get("role"), "content": r.get("content")}
                for r in self._records if r is not self._pending_input
            ]

    def snapshot_all(self) -> List[dict]:
        """
        获取全部历史的快照（副本，含待定当前输入；日志/兼容用）

        Returns:
            历史副本列表（每条为新建 dict，修改不影响内部状态）
        """
        with self._lock:
            return [
                {"role": r.get("role"), "content": r.get("content")}
                for r in self._records
            ]

    def __len__(self) -> int:
        """当前历史记录条数"""
        with self._lock:
            return len(self._records)

    def _trim(self):
        """裁剪到最大长度（原位 del 最旧的记录；调用方须已持有 self._lock）

        注意：只允许原位 del 切片，禁止切片重建后 rebind self._records——
        否则持有旧列表引用的调用方会读到永久冻结的历史（P0-1 类回归）
        """
        # max_records是对话轮数，每轮包含用户和助手两条消息
        max_messages = self.max_records * 2
        if len(self._records) > max_messages:
            removed = len(self._records) - max_messages
            del self._records[:removed]
