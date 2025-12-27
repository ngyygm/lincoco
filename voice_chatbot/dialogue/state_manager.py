"""
状态管理器模块（事件驱动版，v3.0 新架构）

提供事件驱动版本的状态管理器，通过事件驱动实现原子性状态转换、自动验证状态组合有效性。
参考：THREAD_SCHEDULING.md, PROJECT_OUTLINE.md
"""

import threading
import time
from typing import Optional

from voice_chatbot.dialogue.state import MainSystemState, SubSystemState
from voice_chatbot.dialogue.events import EventType, EventBus, create_event


class StateManager:
    """状态管理器类（事件驱动版，v3.0 新架构）
    
    统一管理主系统状态和副系统状态，通过事件驱动实现原子性状态转换。
    
    特性：
    - 原子性状态转换：使用锁保护，确保状态转换的原子性
    - 状态组合验证：自动验证状态组合有效性，防止不可能的状态组合
    - 事件驱动：通过 EventBus 发布 STATE_CHANGED 事件
    - 状态恢复：检测到无效状态组合时自动恢复
    - 统一日志：所有日志通过事件系统输出
    """
    
    def __init__(self, event_bus: Optional[EventBus] = None):
        """
        初始化状态管理器
        
        Args:
            event_bus: 事件总线（可选，用于发布状态转换事件）
        """
        # 状态存储
        self._main_state = MainSystemState.IDLE
        self._sub_state = SubSystemState.NONE
        
        # 线程安全
        self._lock = threading.Lock()
        
        # 事件总线
        self._event_bus = event_bus
        
        # 状态转换历史（用于调试）
        self._transition_history: list = []
        self._max_history = 100
    
    def set_event_bus(self, event_bus: Optional[EventBus]):
        """
        设置事件总线
        
        Args:
            event_bus: 事件总线（如果为None则禁用事件发布）
        """
        with self._lock:
            self._event_bus = event_bus
    
    def get_main_state(self) -> MainSystemState:
        """
        获取当前主系统状态（用户相关）
        
        Returns:
            当前主系统状态
        """
        with self._lock:
            return self._main_state
    
    def get_sub_state(self) -> SubSystemState:
        """
        获取当前副系统状态（机器人LLM+TTS线程相关）
        
        Returns:
            当前副系统状态
        """
        with self._lock:
            return self._sub_state
    
    def _validate_state_combination(self, main_state: MainSystemState, sub_state: SubSystemState) -> bool:
        """
        验证状态组合是否有效
        
        根据 THREAD_SCHEDULING.md 中的定义，检查不可能的状态组合。
        
        Args:
            main_state: 主系统状态
            sub_state: 副系统状态
            
        Returns:
            True表示状态组合有效，False表示无效
        """
        # 不可能的状态组合（根据 THREAD_SCHEDULING.md）
        invalid_combinations = [
            (MainSystemState.IDLE, SubSystemState.PAUSED),
            (MainSystemState.ROBOT_SPEAKING, SubSystemState.NONE),
            (MainSystemState.ROBOT_SPEAKING, SubSystemState.PROCESSING),
            (MainSystemState.ROBOT_SPEAKING, SubSystemState.PAUSED),
        ]
        
        return (main_state, sub_state) not in invalid_combinations
    
    def _recover_invalid_state(self, main_state: MainSystemState, sub_state: SubSystemState) -> tuple[MainSystemState, SubSystemState]:
        """
        恢复无效状态组合
        
        当检测到无效状态组合时，根据恢复规则自动恢复。
        
        Args:
            main_state: 无效的主系统状态
            sub_state: 无效的副系统状态
            
        Returns:
            (恢复后的主系统状态, 恢复后的副系统状态)
        """
        # 根据 THREAD_SCHEDULING.md 中的恢复规则
        if main_state == MainSystemState.IDLE and sub_state == SubSystemState.PAUSED:
            # IDLE + PAUSED → IDLE + NONE
            return (MainSystemState.IDLE, SubSystemState.NONE)
        elif main_state == MainSystemState.ROBOT_SPEAKING and sub_state == SubSystemState.NONE:
            # ROBOT_SPEAKING + NONE → IDLE + NONE
            return (MainSystemState.IDLE, SubSystemState.NONE)
        elif main_state == MainSystemState.ROBOT_SPEAKING and sub_state == SubSystemState.PROCESSING:
            # ROBOT_SPEAKING + PROCESSING → IDLE + PROCESSING
            return (MainSystemState.IDLE, SubSystemState.PROCESSING)
        elif main_state == MainSystemState.ROBOT_SPEAKING and sub_state == SubSystemState.PAUSED:
            # ROBOT_SPEAKING + PAUSED → LISTENING + PAUSED
            return (MainSystemState.LISTENING, SubSystemState.PAUSED)
        else:
            # 未知的无效组合，恢复到初始状态
            return (MainSystemState.IDLE, SubSystemState.NONE)
    
    def _publish_state_changed_event(self, 
                                     old_main_state: MainSystemState,
                                     new_main_state: MainSystemState,
                                     old_sub_state: SubSystemState,
                                     new_sub_state: SubSystemState,
                                     reason: str,
                                     transition_type: str):
        """
        发布状态转换事件
        
        Args:
            old_main_state: 旧主系统状态
            new_main_state: 新主系统状态
            old_sub_state: 旧副系统状态
            new_sub_state: 新副系统状态
            reason: 转换原因
            transition_type: 转换类型（"main", "sub", "both"）
        """
        if self._event_bus is None:
            return
        
        # 创建状态转换事件
        event = create_event(
            event_type=EventType.STATE_CHANGED,
            data={
                "old_main_state": old_main_state.value,
                "new_main_state": new_main_state.value,
                "old_sub_state": old_sub_state.value,
                "new_sub_state": new_sub_state.value,
                "reason": reason,
                "transition_type": transition_type
            },
            source="StateManager"
        )
        
        # 发布事件
        self._event_bus.publish(event)
        
        # 通过事件系统记录日志
        log_message = f"状态转换 [{transition_type}]: 主系统 {old_main_state.value} → {new_main_state.value}, 副系统 {old_sub_state.value} → {new_sub_state.value}"
        if reason:
            log_message += f" ({reason})"
        
        log_event = create_event(
            event_type=EventType.LOG_INFO,
            data={"message": log_message},
            source="StateManager"
        )
        self._event_bus.publish(log_event)
    
    def _record_transition(self, 
                          old_main_state: MainSystemState,
                          new_main_state: MainSystemState,
                          old_sub_state: SubSystemState,
                          new_sub_state: SubSystemState,
                          reason: str,
                          transition_type: str):
        """
        记录状态转换历史（用于调试）
        
        Args:
            old_main_state: 旧主系统状态
            new_main_state: 新主系统状态
            old_sub_state: 旧副系统状态
            new_sub_state: 新副系统状态
            reason: 转换原因
            transition_type: 转换类型
        """
        transition = {
            "timestamp": time.time(),
            "old_main_state": old_main_state.value,
            "new_main_state": new_main_state.value,
            "old_sub_state": old_sub_state.value,
            "new_sub_state": new_sub_state.value,
            "reason": reason,
            "transition_type": transition_type
        }
        
        self._transition_history.append(transition)
        
        # 限制历史记录数量
        if len(self._transition_history) > self._max_history:
            self._transition_history.pop(0)
    
    def transition_main_state(self, new_state: MainSystemState, reason: str = "") -> bool:
        """
        转换主系统状态
        
        执行状态转换前会验证状态组合有效性。如果新状态组合无效，会尝试自动恢复。
        
        Args:
            new_state: 新主系统状态
            reason: 转换原因（可选）
            
        Returns:
            True表示转换成功，False表示转换失败或状态未变化
        """
        with self._lock:
            old_main_state = self._main_state
            old_sub_state = self._sub_state
            
            # 如果状态未变化，不执行转换
            if old_main_state == new_state:
                return False
            
            # 验证新状态组合
            if not self._validate_state_combination(new_state, old_sub_state):
                # 状态组合无效，尝试恢复
                recovered_main, recovered_sub = self._recover_invalid_state(new_state, old_sub_state)
                
                # 通过事件系统记录警告
                if self._event_bus:
                    warn_event = create_event(
                        event_type=EventType.LOG_WARN,
                        data={
                            "message": f"检测到无效状态组合: 主系统 {new_state.value} + 副系统 {old_sub_state.value}，自动恢复为: 主系统 {recovered_main.value} + 副系统 {recovered_sub.value}"
                        },
                        source="StateManager"
                    )
                    self._event_bus.publish(warn_event)
                
                # 使用恢复后的状态
                new_state = recovered_main
                # 如果副系统状态也需要恢复，同时更新
                if recovered_sub != old_sub_state:
                    self._sub_state = recovered_sub
            
            # 执行状态转换
            self._main_state = new_state
            
            # 记录转换历史
            self._record_transition(
                old_main_state, new_state,
                old_sub_state, self._sub_state,
                reason, "main"
            )
            
            # 发布状态转换事件
            self._publish_state_changed_event(
                old_main_state, new_state,
                old_sub_state, self._sub_state,
                reason, "main"
            )
            
            return True
    
    def transition_sub_state(self, new_state: SubSystemState, reason: str = "") -> bool:
        """
        转换副系统状态
        
        执行状态转换前会验证状态组合有效性。如果新状态组合无效，会尝试自动恢复。
        
        Args:
            new_state: 新副系统状态
            reason: 转换原因（可选）
            
        Returns:
            True表示转换成功，False表示转换失败或状态未变化
        """
        with self._lock:
            old_main_state = self._main_state
            old_sub_state = self._sub_state
            
            # 如果状态未变化，不执行转换
            if old_sub_state == new_state:
                return False
            
            # 验证新状态组合
            if not self._validate_state_combination(old_main_state, new_state):
                # 状态组合无效，尝试恢复
                recovered_main, recovered_sub = self._recover_invalid_state(old_main_state, new_state)
                
                # 通过事件系统记录警告
                if self._event_bus:
                    warn_event = create_event(
                        event_type=EventType.LOG_WARN,
                        data={
                            "message": f"检测到无效状态组合: 主系统 {old_main_state.value} + 副系统 {new_state.value}，自动恢复为: 主系统 {recovered_main.value} + 副系统 {recovered_sub.value}"
                        },
                        source="StateManager"
                    )
                    self._event_bus.publish(warn_event)
                
                # 使用恢复后的状态
                new_state = recovered_sub
                # 如果主系统状态也需要恢复，同时更新
                if recovered_main != old_main_state:
                    self._main_state = recovered_main
            
            # 执行状态转换
            self._sub_state = new_state
            
            # 记录转换历史
            self._record_transition(
                old_main_state, self._main_state,
                old_sub_state, new_state,
                reason, "sub"
            )
            
            # 发布状态转换事件
            self._publish_state_changed_event(
                old_main_state, self._main_state,
                old_sub_state, new_state,
                reason, "sub"
            )
            
            return True
    
    def transition_both(self, main_state: MainSystemState, sub_state: SubSystemState, reason: str = "") -> bool:
        """
        同时转换主系统和副系统状态（原子操作）
        
        这是一个原子操作，确保主系统和副系统状态同时转换，不会出现中间状态。
        执行状态转换前会验证状态组合有效性。
        
        Args:
            main_state: 新主系统状态
            sub_state: 新副系统状态
            reason: 转换原因（可选）
            
        Returns:
            True表示转换成功，False表示转换失败或状态未变化
        """
        with self._lock:
            old_main_state = self._main_state
            old_sub_state = self._sub_state
            
            # 如果状态都未变化，不执行转换
            if old_main_state == main_state and old_sub_state == sub_state:
                return False
            
            # 验证新状态组合
            if not self._validate_state_combination(main_state, sub_state):
                # 状态组合无效，尝试恢复
                recovered_main, recovered_sub = self._recover_invalid_state(main_state, sub_state)
                
                # 通过事件系统记录警告
                if self._event_bus:
                    warn_event = create_event(
                        event_type=EventType.LOG_WARN,
                        data={
                            "message": f"检测到无效状态组合: 主系统 {main_state.value} + 副系统 {sub_state.value}，自动恢复为: 主系统 {recovered_main.value} + 副系统 {recovered_sub.value}"
                        },
                        source="StateManager"
                    )
                    self._event_bus.publish(warn_event)
                
                # 使用恢复后的状态
                main_state = recovered_main
                sub_state = recovered_sub
            
            # 执行状态转换（原子操作）
            self._main_state = main_state
            self._sub_state = sub_state
            
            # 记录转换历史
            self._record_transition(
                old_main_state, main_state,
                old_sub_state, sub_state,
                reason, "both"
            )
            
            # 发布状态转换事件
            self._publish_state_changed_event(
                old_main_state, main_state,
                old_sub_state, sub_state,
                reason, "both"
            )
            
            return True
    
    def get_transition_history(self, limit: Optional[int] = None) -> list:
        """
        获取状态转换历史（用于调试）
        
        Args:
            limit: 限制返回的记录数量，如果为None则返回全部
            
        Returns:
            状态转换历史列表（副本）
        """
        with self._lock:
            history = self._transition_history.copy()
            if limit is not None:
                history = history[-limit:]
            return history
    
    def reset(self):
        """重置状态（恢复到初始状态）"""
        with self._lock:
            old_main_state = self._main_state
            old_sub_state = self._sub_state
            
            self._main_state = MainSystemState.IDLE
            self._sub_state = SubSystemState.NONE
            self._transition_history.clear()
            
            # 发布重置事件
            if self._event_bus:
                reset_event = create_event(
                    event_type=EventType.STATE_CHANGED,
                    data={
                        "old_main_state": old_main_state.value,
                        "new_main_state": MainSystemState.IDLE.value,
                        "old_sub_state": old_sub_state.value,
                        "new_sub_state": SubSystemState.NONE.value,
                        "reason": "状态重置",
                        "transition_type": "both"
                    },
                    source="StateManager"
                )
                self._event_bus.publish(reset_event)
                
                log_event = create_event(
                    event_type=EventType.LOG_INFO,
                    data={
                        "message": f"状态已重置: 主系统 {old_main_state.value} → IDLE, 副系统 {old_sub_state.value} → NONE"
                    },
                    source="StateManager"
                )
                self._event_bus.publish(log_event)
