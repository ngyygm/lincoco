"""
上下文管理器模块（v3.0 新架构）

提供统一的任务上下文管理功能，包括任务上下文的创建、访问和销毁
参考：THREAD_SCHEDULING.md, PROJECT_OUTLINE.md
"""

import threading
from typing import Optional

from voice_chatbot.dialogue.task_context import TaskContext
from voice_chatbot.dialogue.events import EventType, EventBus, create_event
from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class ContextManager:
    """上下文管理器类
    
    统一管理任务上下文的创建、访问和销毁，提供线程安全的任务管理。
    创建新任务时自动使旧任务失效，确保任务隔离。
    
    特性：
    - 统一管理：集中管理所有任务上下文的生命周期
    - 自动失效：创建新任务时自动使旧任务失效
    - 线程安全：所有操作使用锁保护
    - 事件集成：通过EventBus发布任务创建和取消事件
    """
    
    def __init__(self, event_bus: Optional[EventBus] = None):
        """
        初始化上下文管理器
        
        Args:
            event_bus: 事件总线（可选，用于发布任务事件）
        """
        self._current_context: Optional[TaskContext] = None
        self._task_id_counter = 0
        self._lock = threading.Lock()
        self._event_bus = event_bus
        
        log_info("CTX", "上下文管理器初始化完成", indent=0)
    
    def create_context(self, user_input: str = "") -> TaskContext:
        """
        创建新任务上下文
        
        创建新上下文时，会自动使当前活动的上下文失效（如果存在）。
        这是线程安全的操作。
        
        Args:
            user_input: 用户输入（可选）
            
        Returns:
            新创建的任务上下文
        """
        with self._lock:
            # 使旧任务失效
            if self._current_context is not None:
                old_task_id = self._current_context.task_id
                self._current_context.invalidate()
                log_info("CTX", f"旧任务已失效: ID={old_task_id}", indent=0)
                
                # 发布TASK_CANCELLED事件
                if self._event_bus is not None:
                    try:
                        cancelled_event = create_event(
                            EventType.TASK_CANCELLED,
                            data={"task_id": old_task_id},
                            source="ContextManager"
                        )
                        self._event_bus.publish(cancelled_event)
                    except Exception as e:
                        log_error("CTX", f"发布TASK_CANCELLED事件失败: {e}", indent=1)
            
            # 创建新任务上下文
            self._task_id_counter += 1
            new_context = TaskContext(
                task_id=self._task_id_counter,
                user_input=user_input
            )
            self._current_context = new_context
            
            log_info("CTX", f"创建新任务上下文: ID={self._task_id_counter}, 输入='{user_input}'", indent=0)
            
            # 发布TASK_CREATED事件
            if self._event_bus is not None:
                try:
                    created_event = create_event(
                        EventType.TASK_CREATED,
                        data={
                            "task_id": self._task_id_counter,
                            "user_input": user_input
                        },
                        source="ContextManager"
                    )
                    self._event_bus.publish(created_event)
                except Exception as e:
                    log_error("CTX", f"发布TASK_CREATED事件失败: {e}", indent=1)
            
            return new_context
    
    def get_current_context(self) -> Optional[TaskContext]:
        """
        获取当前活动的任务上下文（线程安全）
        
        Returns:
            当前任务上下文，如果没有则返回None
        """
        with self._lock:
            return self._current_context
    
    def cancel_current_context(self):
        """
        取消当前任务上下文
        
        使当前活动的任务上下文失效。如果当前没有活动的上下文，则不执行任何操作。
        这是线程安全的操作。
        """
        with self._lock:
            if self._current_context is not None:
                task_id = self._current_context.task_id
                user_input = self._current_context.user_input if hasattr(self._current_context, 'user_input') else ""
                is_valid_before = self._current_context.is_valid
                
                log_info("CTX", f"[CANCEL-001] 准备取消当前任务上下文: ID={task_id}, is_valid={is_valid_before}", indent=0)
                log_info("CTX", f"[CANCEL-001] 任务用户输入: '{user_input}'", indent=1)
                
                # 使上下文失效
                self._current_context.invalidate()
                log_info("CTX", f"[CANCEL-002] 上下文已失效（is_valid=False）", indent=1)
                
                # 清空当前上下文引用
                self._current_context = None
                log_info("CTX", f"[CANCEL-003] 当前上下文引用已清空（_current_context=None）", indent=1)
                
                # 发布TASK_CANCELLED事件
                if self._event_bus is not None:
                    try:
                        cancelled_event = create_event(
                            EventType.TASK_CANCELLED,
                            data={"task_id": task_id},
                            source="ContextManager"
                        )
                        self._event_bus.publish(cancelled_event)
                        log_info("CTX", f"[CANCEL-005] TASK_CANCELLED事件已发布", indent=1)
                    except Exception as e:
                        log_error("CTX", f"[CANCEL-005] 发布TASK_CANCELLED事件失败: {e}", indent=1)
            else:
                log_debug("CTX", "[CANCEL-001] 当前没有活动的任务上下文，无需取消", indent=0)
    
    def get_current_task_id(self) -> int:
        """
        获取当前任务ID
        
        Returns:
            当前任务ID，如果没有当前任务则返回0
        """
        with self._lock:
            if self._current_context is not None:
                return self._current_context.task_id
            return 0
    
    def set_event_bus(self, event_bus: Optional[EventBus]):
        """
        设置事件总线
        
        Args:
            event_bus: 事件总线（如果为None则禁用事件发布）
        """
        with self._lock:
            self._event_bus = event_bus
            log_info("CTX", f"事件总线已{'设置' if event_bus is not None else '移除'}", indent=0)
