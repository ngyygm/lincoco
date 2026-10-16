"""
终句debounce模块（P3：源头治理VAD过切分）

EndpointDebouncer 在ASR final结果与"记录+处理"路径之间加入一个可配置的
静默等待窗口（asr.endpoint_debounce_ms）：final碎片先进入缓冲并启动计时，
窗口内又有新的final并入缓冲（'，'连接）重新计时（partial/VAD重新开始说话
由调用方调用cancel()只停表不清缓冲），静默到期才用合并后的文本触发一次
完整处理——避免VAD过切分导致每个碎片都启动一整轮LLM->TTS->播放。

本模块只依赖标准库（不导入utils/manager等重依赖链），便于独立单元测试；
now_fn/timer_factory 可注入，测试时用假时钟/假计时器，不产生真实等待。

线程模型：ASR回调线程调用offer/cancel，timer线程执行fire回调，
内部用一把锁保护缓冲与计时器状态；用"代数(generation)"计数器淘汰
已被取消/替换的迟到触发（threading.Timer的cancel与function执行存在竞态，
单靠Timer.cancel()无法保证回调不再执行）。
"""

import threading
import time
from typing import Callable, List, Optional


class EndpointDebouncer:
    """终句debouncer：缓冲final碎片，静默窗口到期后合并触发一次处理"""

    def __init__(self,
                 delay_s: float,
                 fire_callback: Callable[[str], None],
                 now_fn: Callable[[], float] = time.time,
                 timer_factory=threading.Timer,
                 max_gap_s: Optional[float] = None):
        """
        Args:
            delay_s: 静默等待窗口（秒）；<=0表示禁用debounce，
                     offer()时同步触发fire_callback（行为与未启用debounce完全一致）
            fire_callback: 窗口到期回调，参数为合并后的文本（'，'连接），在timer线程中执行
            now_fn: 时间函数（可注入假时钟，便于测试）
            timer_factory: 计时器工厂（可注入假计时器，便于测试），签名同
                           threading.Timer(interval, function)，返回对象需支持start()/cancel()
            max_gap_s: 缓冲碎片的最大保留间隔（秒），距上次offer超过该值的旧缓冲在下次
                       offer时被丢弃（防止长时间挂起的碎片并入后续新话轮）；None=不启用该保护
        """
        self._delay_s = max(0.0, float(delay_s))
        self._fire_callback = fire_callback
        self._now_fn = now_fn
        self._timer_factory = timer_factory
        self._max_gap_s = max_gap_s

        self._lock = threading.Lock()
        self._buffer: List[str] = []   # 待合并的final碎片
        self._buffer_time = 0.0        # 最近一次offer的时间戳（max_gap_s判定用）
        self._timer = None             # 当前pending的计时器（None=无pending计时）
        self._generation = 0           # 计时器代数，淘汰被取消/替换的迟到触发

    # ---------- 对外接口 ----------

    def set_delay(self, delay_s: float) -> None:
        """
        热更新静默窗口（秒）

        只影响之后offer启动的新计时器；已pending的计时仍按旧窗口到期。
        设为<=0等效禁用：之后offer的文本同步触发（pending的旧计时不受影响，
        如需立即生效可先reset()）
        """
        with self._lock:
            self._delay_s = max(0.0, float(delay_s))

    def set_max_gap(self, max_gap_s: Optional[float]) -> None:
        """热更新缓冲碎片的最大保留间隔（秒），None=关闭该保护"""
        with self._lock:
            self._max_gap_s = max_gap_s

    def offer(self, text: str) -> None:
        """缓冲文本并（重）启计时

        delay_s<=0时同步触发fire_callback（在调用线程执行），不创建计时器，
        与未启用debounce的行为完全一致。

        Args:
            text: 新到的final碎片文本（空白文本忽略）
        """
        text = (text or "").strip()
        if not text:
            return

        # 禁用debounce：立即同步触发，行为等价于不经过debouncer
        if self._delay_s <= 0:
            self._fire_callback(text)
            return

        with self._lock:
            now = self._now_fn()
            # 缓冲过期保护：距上次offer超过max_gap_s的旧缓冲丢弃，
            # 避免长时间挂起的碎片（如VAD重启后没有产生新final）并入后续新话轮
            if (self._buffer and self._max_gap_s is not None
                    and now - self._buffer_time > self._max_gap_s):
                self._buffer.clear()

            self._buffer.append(text)
            self._buffer_time = now

            # 作废旧计时器，为新缓冲启动新计时（代数+1使旧计时的迟到触发失效）
            self._invalidate_timer_locked()
            self._generation += 1
            generation = self._generation
            timer = self._timer_factory(self._delay_s,
                                        lambda: self._on_timer(generation))
            self._timer = timer

        timer.start()

    def cancel(self) -> None:
        """取消pending计时（缓冲保留）

        用于"用户仍在说话"信号（新的partial/VAD重新开始说话）：
        只停表不清缓冲，后续新final到来时并入缓冲重新offer即可继续合并。
        """
        with self._lock:
            self._invalidate_timer_locked()

    def reset(self) -> str:
        """取消pending计时并清空缓冲（stop/清空历史等终态路径使用）

        Returns:
            被丢弃的缓冲合并文本（无缓冲时为''，用于日志排查）
        """
        with self._lock:
            self._invalidate_timer_locked()
            dropped = self._merge_buffer_locked()
            self._buffer.clear()
            return dropped

    def is_pending(self) -> bool:
        """是否存在pending的终句计时（外部查询pending状态）"""
        with self._lock:
            return self._timer is not None

    def pending_text(self) -> str:
        """当前缓冲的合并文本（'，'连接，用于日志排查；无缓冲时为''）"""
        with self._lock:
            return self._merge_buffer_locked()

    # ---------- 内部实现 ----------

    def _merge_buffer_locked(self) -> str:
        """合并缓冲碎片（调用方必须已持有锁）"""
        return "，".join(self._buffer)

    def _invalidate_timer_locked(self):
        """作废当前计时器并使迟到的触发失效（调用方必须已持有锁；不清缓冲）"""
        self._generation += 1
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()

    def _on_timer(self, generation: int):
        """计时到期：代数仍匹配则取出合并文本触发回调（timer线程执行）

        回调在锁外执行，避免fire_callback（内部的"记录+处理"路径）与
        offer/cancel形成锁竞争甚至死锁。
        """
        with self._lock:
            # 代数不匹配=已被cancel()或被新的offer()替换，本次触发作废
            if generation != self._generation or self._timer is None:
                return
            text = self._merge_buffer_locked()
            self._buffer.clear()
            self._timer = None

        if text:
            self._fire_callback(text)
