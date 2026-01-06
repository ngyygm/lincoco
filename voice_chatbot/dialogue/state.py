"""
状态机模块

提供状态枚举、任务信息管理、对话历史管理、线程安全的状态操作、状态转换日志和状态持久化
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import threading
import time
import json
import re
from enum import Enum
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class MainSystemState(Enum):
    """主系统状态枚举（用户相关）"""
    IDLE = "idle"              # 空闲状态，等待用户输入
    LISTENING = "listening"    # 正在监听用户语音，VAD检测到语音活动，ASR正在识别
    ROBOT_SPEAKING = "robot_speaking"  # 机器人正在说话，机器人正在播放生成的音频回答


class SubSystemState(Enum):
    """副系统状态枚举（机器人LLM+TTS线程相关）"""
    NONE = "none"              # 无活动，没有正在处理的LLM+TTS任务
    PROCESSING = "processing"  # 正在处理，LLM正在生成回答，或TTS正在生成音频
    SPEAKING = "speaking"      # 正在播放，第一段TTS音频生成完成，开始播放
    PAUSED = "paused"          # 处理、播放已暂停（等待验证），检测到用户可能打断


# 向后兼容：保留 SystemState 作为别名（映射到主系统状态）
# 注意：为了兼容性，我们保留旧的 SystemState，但建议使用 MainSystemState 和 SubSystemState
class SystemState(Enum):
    """系统状态枚举（向后兼容，已废弃，请使用 MainSystemState 和 SubSystemState）"""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: int
    user_input: str = ""
    response_text: str = ""
    start_time: float = 0.0
    status: str = "pending"  # pending/processing/playing/completed/cancelled
    
    def __post_init__(self):
        """初始化后处理"""
        if self.start_time == 0.0:
            self.start_time = time.time()


class DialogueState:
    """对话状态管理器"""
    
    def __init__(self, config):
        """
        初始化对话状态管理器
        
        Args:
            config: 配置对象（Config实例）
        """
        self.config = config
        
        # 从配置获取参数
        self.max_history_length = config.get("llm.max_history_length", 50)  # 最大历史记录长度（对话轮数）
        
        # 状态管理（主系统和副系统）
        self._main_state = MainSystemState.IDLE
        self._sub_state = SubSystemState.NONE
        self._state_lock = threading.Lock()
        
        # 任务管理
        self._current_task_id = 0
        self._current_task: Optional[TaskInfo] = None
        self._task_history: List[TaskInfo] = []  # 任务历史记录（可选）
        
        # 对话历史管理
        self._conversation_history: List[Dict[str, str]] = []
        self._history_lock = threading.Lock()
        
        # 状态转换历史（用于日志和调试）
        self._state_transition_history: List[Dict[str, Any]] = []
        self._max_transition_history = 100  # 最多保存100条转换记录

        log_info("STATE", "对话状态管理器初始化完成", indent=0)
    
    def get_main_state(self) -> MainSystemState:
        """
        获取当前主系统状态（用户相关）
        
        Returns:
            当前主系统状态
        """
        with self._state_lock:
            return self._main_state
    
    def get_sub_state(self) -> SubSystemState:
        """
        获取当前副系统状态（机器人LLM+TTS线程相关）
        
        Returns:
            当前副系统状态
        """
        with self._state_lock:
            return self._sub_state
    
    def set_main_state(self, new_state: MainSystemState, reason: str = ""):
        """
        设置主系统状态（带日志记录）
        
        Args:
            new_state: 新主系统状态
            reason: 状态转换原因（可选）
        """
        with self._state_lock:
            old_state = self._main_state
            if old_state == new_state:
                # 状态未变化，不记录
                return
            
            self._main_state = new_state
            
            # 记录状态转换
            transition = {
                "timestamp": time.time(),
                "type": "main",
                "from_state": old_state.value,
                "to_state": new_state.value,
                "reason": reason,
                "sub_state": self._sub_state.value if self._sub_state.value is not None else "none"
            }
            self._state_transition_history.append(transition)
            
            # 限制历史记录数量
            if len(self._state_transition_history) > self._max_transition_history:
                self._state_transition_history.pop(0)

            # 输出状态转换日志
            log_info("STATE", f"主系统状态转换: {old_state.value} → {new_state.value}" + (f" ({reason})" if reason else ""), indent=0)

    def set_sub_state(self, new_state: SubSystemState, reason: str = ""):
        """
        设置副系统状态（带日志记录）
        
        Args:
            new_state: 新副系统状态
            reason: 状态转换原因（可选）
        """
        with self._state_lock:
            old_state = self._sub_state
            if old_state == new_state:
                # 状态未变化，不记录
                return
            
            self._sub_state = new_state
            
            # 记录状态转换
            transition = {
                "timestamp": time.time(),
                "type": "sub",
                "from_state": old_state.value,
                "to_state": new_state.value,
                "reason": reason,
                "main_state": self._main_state.value
            }
            self._state_transition_history.append(transition)
            
            # 限制历史记录数量
            if len(self._state_transition_history) > self._max_transition_history:
                self._state_transition_history.pop(0)

            # 输出状态转换日志
            log_info("STATE", f"副系统状态转换: {old_state.value} → {new_state.value}" + (f" ({reason})" if reason else ""), indent=0)

    # 向后兼容方法
    def get_state(self) -> SystemState:
        """
        获取当前系统状态（向后兼容方法，返回主系统状态映射）
        
        注意：此方法已废弃，请使用 get_main_state() 和 get_sub_state()
        
        Returns:
            当前系统状态（映射到旧 SystemState 枚举）
        """
        with self._state_lock:
            # 将主系统状态映射到旧 SystemState
            if self._main_state == MainSystemState.IDLE:
                return SystemState.IDLE
            elif self._main_state == MainSystemState.LISTENING:
                return SystemState.LISTENING
            elif self._main_state == MainSystemState.ROBOT_SPEAKING:
                # 如果副系统是 PROCESSING 或 PAUSED，返回对应状态
                if self._sub_state == SubSystemState.PROCESSING:
                    return SystemState.PROCESSING
                elif self._sub_state == SubSystemState.PAUSED:
                    return SystemState.PAUSED
                else:
                    return SystemState.PLAYING
            else:
                return SystemState.IDLE
    
    def set_state(self, new_state: SystemState, reason: str = ""):
        """
        设置系统状态（向后兼容方法）
        
        注意：此方法已废弃，请使用 set_main_state() 和 set_sub_state()
        
        Args:
            new_state: 新状态（旧 SystemState 枚举）
            reason: 状态转换原因（可选）
        """
        with self._state_lock:
            # 将旧 SystemState 映射到新的主系统和副系统状态
            if new_state == SystemState.IDLE:
                self.set_main_state(MainSystemState.IDLE, reason)
                self.set_sub_state(SubSystemState.NONE, reason)
            elif new_state == SystemState.LISTENING:
                self.set_main_state(MainSystemState.LISTENING, reason)
            elif new_state == SystemState.PROCESSING:
                self.set_sub_state(SubSystemState.PROCESSING, reason)
            elif new_state == SystemState.PLAYING:
                self.set_main_state(MainSystemState.ROBOT_SPEAKING, reason)
                self.set_sub_state(SubSystemState.SPEAKING, reason)
            elif new_state == SystemState.PAUSED:
                self.set_sub_state(SubSystemState.PAUSED, reason)
    
    def get_current_task_id(self) -> int:
        """
        获取当前任务ID
        
        Returns:
            当前任务ID
        """
        with self._state_lock:
            return self._current_task_id
    
    def create_new_task(self, user_input: str = "") -> int:
        """
        创建新任务
        
        Args:
            user_input: 用户输入（可选）
            
        Returns:
            新任务ID
        """
        with self._state_lock:
            self._current_task_id += 1
            self._current_task = TaskInfo(
                task_id=self._current_task_id,
                user_input=user_input,
                status="pending"
            )
            
            # 可选：保存到任务历史
            if len(self._task_history) > 100:  # 限制历史记录数量
                self._task_history.pop(0)
            self._task_history.append(self._current_task)
            
            log_info("TASK", f"创建新任务: ID={self._current_task_id}, 输入='{user_input[:50]}...'", indent=0)
            return self._current_task_id
    
    def cancel_current_task(self):
        """取消当前任务"""
        with self._state_lock:
            if self._current_task:
                self._current_task.status = "cancelled"
                log_info("TASK", f"取消任务: ID={self._current_task.task_id}", indent=0)
    
    def update_task_status(self, status: str):
        """
        更新当前任务状态
        
        Args:
            status: 任务状态（pending/processing/playing/completed/cancelled）
        """
        with self._state_lock:
            if self._current_task:
                self._current_task.status = status
                log_debug("TASK", f"更新任务状态: ID={self._current_task.task_id}, 状态={status}", indent=1)
    
    def update_task_response(self, response_text: str):
        """
        更新当前任务的回答文本
        
        Args:
            response_text: 回答文本
        """
        with self._state_lock:
            if self._current_task:
                self._current_task.response_text = response_text
                log_debug("TASK", f"更新任务回答: ID={self._current_task.task_id}, 长度={len(response_text)}", indent=1)
    
    def get_current_task(self) -> Optional[TaskInfo]:
        """
        获取当前任务信息
        
        Returns:
            当前任务信息，如果没有则返回None
        """
        with self._state_lock:
            return self._current_task
    
    def add_conversation(self, user_input: str, response: str, 
                        is_interrupted: bool = False, play_duration: float = 0.0):
        """
        添加对话到历史
        
        Args:
            user_input: 用户输入
            response: 回答文本
            is_interrupted: 是否被打断
            play_duration: 播放时长（秒），用于判断是否记录
        """
        with self._history_lock:
            # 播放了多少就记录多少，不再检查播放时长阈值
            
            # 去除前导空白字符（包括换行符、空格等）
            response_trimmed = response.lstrip()
            
            # 如果被打断，检查是否需要添加"..."标记
            # 如果响应文本以句子结束符结尾（完整句子），则不需要添加"..."
            if is_interrupted:
                # 检查是否以句子结束符结尾（。！？.!?\n）
                sentence_end_pattern = r'[。！？.!?\n]+$'
                if re.search(sentence_end_pattern, response_trimmed.strip()):
                    # 以完整句子结尾，不需要添加"..."
                    response_with_mark = response_trimmed
                else:
                    # 不是完整句子，添加"..."标记
                    response_with_mark = response_trimmed + "..."
            else:
                response_with_mark = response_trimmed
            
            # 添加到历史
            if response_with_mark:
                self._conversation_history.append({"role": "assistant", "content": response_with_mark})
                
                # 限制历史记录长度（max_history_length是对话轮数，每轮包含用户和助手两条消息）
                max_messages = self.max_history_length * 2
                if len(self._conversation_history) > max_messages:
                    # 删除最旧的记录，保持历史记录长度不超过限制
                    removed_count = len(self._conversation_history) - max_messages
                    self._conversation_history = self._conversation_history[removed_count:]
                    log_debug("HIST", f"历史记录超过最大长度({self.max_history_length}轮)，已删除最旧的{removed_count}条记录，当前历史记录数: {len(self._conversation_history)}", indent=1)
                
                if is_interrupted:
                    log_info("HIST", f"打断 - 用户: {user_input}", indent=1)
                    log_info("HIST", f"打断 - 回答({len(response)}字,播放{play_duration:.1f}秒): {response}", indent=1)
                else:
                    log_info("HIST", f"完成 - 用户: {user_input}", indent=1)
                    log_info("HIST", f"完成 - 回答: {response_with_mark}", indent=1)
    
    def get_conversation_history(self) -> List[Dict[str, str]]:
        """
        获取对话历史
        
        Returns:
            对话历史列表（副本）
        """
        with self._history_lock:
            return self._conversation_history.copy()
    
    def get_conversation_history_ref(self) -> List[Dict[str, str]]:
        """
        获取对话历史的引用（用于需要直接修改的场景）
        
        注意：返回的是原始列表的引用，调用者可以直接修改
        但修改时需要注意线程安全
        
        Returns:
            对话历史列表（引用）
        """
        return self._conversation_history
    
    def clear_conversation_history(self):
        """清空对话历史"""
        with self._history_lock:
            self._conversation_history.clear()
            log_info("HIST", "对话历史已清空", indent=0)
    
    def get_state_transition_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取状态转换历史

        Args:
            limit: 限制返回的记录数量，如果为None则返回全部

        Returns:
            状态转换历史列表（副本）
        """
        with self._state_lock:
            history = self._state_transition_history.copy()
            if limit is not None:
                history = history[-limit:]
            return history

    def reset(self):
        """重置状态（恢复到初始状态）"""
        with self._state_lock:
            old_main_state = self._main_state
            old_sub_state = self._sub_state
            self._main_state = MainSystemState.IDLE
            self._sub_state = SubSystemState.NONE
            self._current_task_id = 0
            self._current_task = None
            self._task_history.clear()
            self._state_transition_history.clear()
            
            log_info("STATE", f"状态已重置: 主系统 {old_main_state.value} → IDLE, 副系统 {old_sub_state.value} → none", indent=0)
        
        with self._history_lock:
            self._conversation_history.clear()
        
        log_info("STATE", "所有状态已重置", indent=0)

