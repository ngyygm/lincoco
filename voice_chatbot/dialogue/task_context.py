"""
任务上下文模块（v3.0 新架构）

提供任务上下文管理功能，包括任务隔离、有效性检查和线程安全访问
参考：THREAD_SCHEDULING.md
"""

import threading
import time
from typing import Optional, Any, Dict


class TaskCancelledException(Exception):
    """任务取消异常
    
    当任务被取消时，调用 check_valid() 方法会抛出此异常
    """
    pass


class TaskContext:
    """任务上下文类
    
    隔离每个任务的状态和数据，提供线程安全的任务管理。
    旧任务取消时只需调用 invalidate()，所有线程通过检查 is_valid 自动退出。
    
    特性：
    - 任务数据隔离：每个任务有独立的 TaskContext 实例
    - 线程安全：所有状态访问使用锁保护
    - 向后兼容：保留 task_id 属性，现有代码可以继续使用
    - 有效性检查：通过 check_valid() 方法检查任务是否有效
    """
    
    def __init__(self, task_id: int, user_input: str = ""):
        """
        初始化任务上下文
        
        Args:
            task_id: 任务ID（唯一标识）
            user_input: 用户输入（可选）
        """
        self._task_id = task_id
        self._user_input = user_input
        self._response_text = ""
        self._start_time = time.time()
        self._status = "pending"  # pending/processing/playing/completed/cancelled
        self._is_valid = True
        self._lock = threading.Lock()
        
        # 额外的任务数据（用于扩展）
        self._extra_data: Dict[str, Any] = {}
    
    def check_valid(self):
        """
        检查任务有效性，无效时抛出 TaskCancelledException
        
        在工作线程中应该定期调用此方法，如果任务被取消，会立即抛出异常。
        
        Raises:
            TaskCancelledException: 如果任务已被取消
        """
        with self._lock:
            if not self._is_valid:
                raise TaskCancelledException(f"任务 {self._task_id} 已被取消")
    
    def invalidate(self):
        """
        使任务失效（标记为取消）
        
        调用此方法后，所有持有此 TaskContext 的线程在调用 check_valid() 时会抛出异常。
        这是线程安全的操作。
        """
        with self._lock:
            if not self._is_valid:
                # 已经失效，不需要重复操作
                return
            
            self._is_valid = False
            self._status = "cancelled"
    
    @property
    def task_id(self) -> int:
        """
        获取任务ID（向后兼容）
        
        Returns:
            任务ID
        """
        return self._task_id
    
    @property
    def is_valid(self) -> bool:
        """
        获取任务有效性（线程安全）
        
        Returns:
            True 表示任务有效，False 表示任务已取消
        """
        with self._lock:
            return self._is_valid
    
    @property
    def user_input(self) -> str:
        """
        获取用户输入（线程安全）
        
        Returns:
            用户输入文本
        """
        with self._lock:
            return self._user_input
    
    def set_user_input(self, user_input: str):
        """
        设置用户输入（线程安全）
        
        Args:
            user_input: 用户输入文本
        """
        with self._lock:
            self._user_input = user_input
    
    @property
    def response_text(self) -> str:
        """
        获取回答文本（线程安全）
        
        Returns:
            回答文本
        """
        with self._lock:
            return self._response_text
    
    def update_response(self, response_text: str):
        """
        更新回答文本（线程安全）
        
        Args:
            response_text: 回答文本
        """
        with self._lock:
            self._response_text = response_text
    
    @property
    def start_time(self) -> float:
        """
        获取任务开始时间（线程安全）
        
        Returns:
            任务开始时间戳
        """
        with self._lock:
            return self._start_time
    
    @property
    def status(self) -> str:
        """
        获取任务状态（线程安全）
        
        Returns:
            任务状态（pending/processing/playing/completed/cancelled）
        """
        with self._lock:
            return self._status
    
    def update_status(self, status: str):
        """
        更新任务状态（线程安全）
        
        Args:
            status: 任务状态（pending/processing/playing/completed/cancelled）
        """
        with self._lock:
            self._status = status
    
    def get_extra_data(self, key: str, default: Any = None) -> Any:
        """
        获取额外的任务数据（线程安全）
        
        Args:
            key: 数据键
            default: 默认值（如果键不存在）
            
        Returns:
            数据值
        """
        with self._lock:
            return self._extra_data.get(key, default)
    
    def set_extra_data(self, key: str, value: Any):
        """
        设置额外的任务数据（线程安全）
        
        Args:
            key: 数据键
            value: 数据值
        """
        with self._lock:
            self._extra_data[key] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """
        将任务上下文转换为字典（用于日志和调试）
        
        Returns:
            任务上下文字典
        """
        with self._lock:
            return {
                "task_id": self._task_id,
                "user_input": self._user_input,
                "response_text": self._response_text,
                "start_time": self._start_time,
                "status": self._status,
                "is_valid": self._is_valid,
                "extra_data": self._extra_data.copy()
            }
    
    def __repr__(self) -> str:
        """返回任务上下文的字符串表示"""
        with self._lock:
            return (f"TaskContext(task_id={self._task_id}, "
                   f"status={self._status}, "
                   f"is_valid={self._is_valid}, "
                   f"user_input='{self._user_input[:50]}...')")
