"""
对话管理模块 (dialogue)

提供状态管理、事件系统、核心调度器、任务上下文管理等功能
"""

from .state import DialogueState, SystemState, MainSystemState, SubSystemState, TaskInfo
from .state_manager import StateManager
from .events import EventType, Event, EventFilter, EventBus, create_event, get_default_priority
from .task_context import TaskContext, TaskCancelledException
from .context_manager import ContextManager
from .thread_manager import ThreadManager, ThreadInfo

# 导入核心调度器
from .manager import DialogueManager

__all__ = [
    # 状态管理
    'DialogueState',
    'SystemState',  # 向后兼容
    'MainSystemState',
    'SubSystemState',
    'TaskInfo',
    # 状态管理器（v3.0 新架构）
    'StateManager',
    # 事件系统
    'EventType',
    'Event',
    'EventFilter',
    'EventBus',
    'create_event',
    'get_default_priority',
    # 任务上下文（v3.0 新架构）
    'TaskContext',
    'TaskCancelledException',
    'ContextManager',
    # 线程管理器（v3.0 新架构）
    'ThreadManager',
    'ThreadInfo',
    # 核心调度器
    'DialogueManager',
]

