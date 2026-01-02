"""
线程管理器模块（v3.0 新架构）

提供统一的线程生命周期管理功能，包括线程启动、停止、状态跟踪和异常处理
参考：THREAD_SCHEDULING.md, PROJECT_OUTLINE.md
"""

import threading
import time
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, field

from voice_chatbot.dialogue.task_context import TaskContext
from voice_chatbot.dialogue.events import EventType, EventBus, create_event
from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


@dataclass
class ThreadInfo:
    """线程信息
    
    存储线程的元数据，包括线程对象、绑定的任务上下文、线程类型等。
    """
    name: str                                    # 线程名称
    thread: threading.Thread                    # 线程对象
    context: Optional[TaskContext] = None       # 绑定的任务上下文
    thread_type: str = "on_demand"              # 线程类型：'resident' 或 'on_demand'
    start_time: float = field(default_factory=time.time)  # 启动时间
    is_alive: bool = True                       # 是否存活
    
    def __post_init__(self):
        """初始化后处理"""
        if self.start_time == 0:
            self.start_time = time.time()


class ThreadManager:
    """线程管理器类
    
    统一管理所有工作线程的生命周期，支持常驻线程（线程1-3）和按需启动线程（线程4-9）的统一管理。
    
    特性：
    - 统一管理：集中管理所有线程的生命周期
    - 上下文绑定：启动线程时绑定 TaskContext，支持任务隔离
    - 事件集成：通过 EventBus 发布线程生命周期事件
    - 异常处理：捕获线程执行异常，记录日志并发布事件
    - 线程安全：所有操作使用锁保护
    """
    
    def __init__(self, event_bus: Optional[EventBus] = None):
        """
        初始化线程管理器
        
        Args:
            event_bus: 事件总线（可选，用于发布线程事件）
        """
        self._threads: Dict[str, ThreadInfo] = {}  # 线程注册表：名称 -> ThreadInfo
        self._lock = threading.Lock()              # 保护线程注册表的锁
        self._event_bus = event_bus                # 事件总线
        
        log_info("THREAD", "线程管理器初始化完成", indent=0)
    
    def set_event_bus(self, event_bus: Optional[EventBus]):
        """
        设置事件总线
        
        Args:
            event_bus: 事件总线（如果为None则禁用事件发布）
        """
        with self._lock:
            self._event_bus = event_bus
            log_info("THREAD", f"事件总线已{'设置' if event_bus is not None else '移除'}", indent=0)
    
    def start_thread(self, 
                    name: str, 
                    target: Callable, 
                    context: Optional[TaskContext] = None,
                    thread_type: str = "on_demand",
                    daemon: bool = True,
                    **kwargs) -> threading.Thread:
        """
        启动线程并绑定上下文
        
        启动线程时，会自动注册线程信息，绑定任务上下文（如果提供），并发布 THREAD_STARTED 事件。
        
        Args:
            name: 线程名称（唯一标识）
            target: 线程目标函数
            context: 任务上下文（可选，用于绑定线程到任务）
            thread_type: 线程类型，'resident'（常驻）或 'on_demand'（按需启动）
            daemon: 是否为daemon线程
            **kwargs: 传递给线程目标函数的额外参数
            
        Returns:
            创建的线程对象
            
        Raises:
            ValueError: 如果线程名称已存在
        """
        with self._lock:
            # 检查线程名称是否已存在
            if name in self._threads:
                existing_thread = self._threads[name]
                # 检查线程是否真的还在运行（需要同时检查is_alive标志和线程对象的实际状态）
                thread_actually_alive = existing_thread.thread.is_alive() if existing_thread.thread else False
                
                # 如果线程实际还在运行，则不允许启动新线程
                if thread_actually_alive:
                    raise ValueError(f"线程 '{name}' 已存在且正在运行")
                
                # 线程已结束，移除旧记录（即使is_alive标志为True，但线程实际已结束）
                if existing_thread.is_alive and not thread_actually_alive:
                    log_info("THREAD", f"检测到线程记录状态不一致（is_alive=True但线程已结束），清理记录: {name}", indent=1)
                else:
                    log_debug("THREAD", f"移除已结束的线程记录: {name}", indent=1)
                del self._threads[name]
            
            # 包装目标函数，添加异常处理和上下文检查
            def wrapped_target(*args, **inner_kwargs):
                """包装的目标函数，添加异常处理和上下文检查"""
                thread_started = False
                try:
                    # 发布线程启动事件（在线程内部，确保线程已启动）
                    if self._event_bus is not None:
                        try:
                            started_event = create_event(
                                EventType.THREAD_STARTED,
                                data={
                                    "thread_name": name,
                                    "task_id": context.task_id if context else 0,
                                    "thread_type": thread_type,
                                    "daemon": daemon
                                },
                                source="ThreadManager"
                            )
                            self._event_bus.publish(started_event)
                        except Exception as e:
                            log_error("THREAD", f"发布THREAD_STARTED事件失败: {e}", indent=2)
                    
                    thread_started = True
                    # 线程内部启动日志（线程已实际开始执行）
                    thread_id = threading.current_thread().ident
                    log_info("THREAD", f"线程内部启动确认: {name} [线程ID: {thread_id}]", indent=1)
                    
                    # 执行目标函数
                    if context is not None:
                        # 如果绑定了上下文，将上下文作为第一个参数传递
                        target(context, *args, **inner_kwargs, **kwargs)
                    else:
                        target(*args, **inner_kwargs, **kwargs)
                    
                except Exception as e:
                    # 捕获线程执行异常
                    end_time = time.time()
                    thread_id = threading.current_thread().ident
                    import traceback
                    error_traceback = traceback.format_exc()
                    
                    # 详细的异常日志
                    log_error("THREAD", f"========== 线程异常结束 ==========", indent=1)
                    log_error("THREAD", f"线程名称: {name}", indent=2)
                    log_error("THREAD", f"线程ID: {thread_id}", indent=2)
                    log_error("THREAD", f"线程类型: {thread_type}", indent=2)
                    log_error("THREAD", f"任务ID: {context.task_id if context else 'N/A'}", indent=2)
                    log_error("THREAD", f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time)) if start_time else 'N/A'}", indent=2)
                    log_error("THREAD", f"异常时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}", indent=2)
                    if start_time:
                        duration = end_time - start_time
                        log_error("THREAD", f"运行时长: {duration:.3f}秒", indent=2)
                    log_error("THREAD", f"目标函数: {target.__name__ if hasattr(target, '__name__') else str(target)}", indent=2)
                    log_error("THREAD", f"异常类型: {type(e).__name__}", indent=2)
                    log_error("THREAD", f"异常信息: {str(e)}", indent=2)
                    log_error("THREAD", f"异常堆栈:\n{error_traceback}", indent=2)
                    log_error("THREAD", f"结束原因: 执行异常", indent=2)
                    log_error("THREAD", f"==============================", indent=1)
                    
                    # 发布异常事件（可选，可以通过事件系统记录）
                    if self._event_bus is not None:
                        try:
                            error_event = create_event(
                                EventType.LOG_ERROR,
                                data={
                                    "message": f"线程执行异常: {name}",
                                    "error": str(e),
                                    "thread_name": name,
                                    "task_id": context.task_id if context else 0
                                },
                                source="ThreadManager"
                            )
                            self._event_bus.publish(error_event)
                        except Exception:
                            pass  # 忽略事件发布失败
                finally:
                    # 线程结束时更新状态并发布事件
                    end_time = time.time()
                    start_time = None
                    with self._lock:
                        if name in self._threads:
                            thread_info = self._threads[name]
                            thread_info.is_alive = False
                            start_time = thread_info.start_time
                    
                    if thread_started:
                        # 发布线程结束事件
                        duration = end_time - start_time if start_time else 0
                        if self._event_bus is not None:
                            try:
                                ended_event = create_event(
                                    EventType.THREAD_ENDED,
                                    data={
                                        "thread_name": name,
                                        "task_id": context.task_id if context else 0,
                                        "thread_type": thread_type,
                                        "duration": duration
                                    },
                                    source="ThreadManager"
                                )
                                self._event_bus.publish(ended_event)
                            except Exception as e:
                                log_error("THREAD", f"发布THREAD_ENDED事件失败: {e}", indent=2)
                        
                        # 详细的线程结束日志
                        thread_id = threading.current_thread().ident
                        log_info("THREAD", f"========== 线程结束 ==========", indent=1)
                        log_info("THREAD", f"线程名称: {name}", indent=2)
                        log_info("THREAD", f"线程ID: {thread_id}", indent=2)
                        log_info("THREAD", f"线程类型: {thread_type}", indent=2)
                        log_info("THREAD", f"任务ID: {context.task_id if context else 'N/A'}", indent=2)
                        log_info("THREAD", f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time)) if start_time else 'N/A'}", indent=2)
                        log_info("THREAD", f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}", indent=2)
                        log_info("THREAD", f"运行时长: {duration:.3f}秒", indent=2)
                        log_info("THREAD", f"结束原因: 正常结束", indent=2)
                        log_info("THREAD", f"==============================", indent=1)
            
            # 创建线程对象
            thread = threading.Thread(
                target=wrapped_target,
                name=name,
                daemon=daemon,
                args=kwargs.pop('args', ()),
                kwargs={k: v for k, v in kwargs.items() if k != 'args'}
            )
            
            # 注册线程信息
            start_time = time.time()
            thread_info = ThreadInfo(
                name=name,
                thread=thread,
                context=context,
                thread_type=thread_type,
                start_time=start_time,
                is_alive=True
            )
            self._threads[name] = thread_info
            
            # 启动线程
            thread.start()
            
            # 详细的启动日志
            log_info("THREAD", f"========== 线程启动 ==========", indent=0)
            log_info("THREAD", f"线程名称: {name}", indent=1)
            log_info("THREAD", f"线程ID: {thread.ident if thread.ident else 'N/A'}", indent=1)
            log_info("THREAD", f"线程类型: {thread_type}", indent=1)
            log_info("THREAD", f"Daemon线程: {daemon}", indent=1)
            log_info("THREAD", f"任务ID: {context.task_id if context else 'N/A'}", indent=1)
            log_info("THREAD", f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))}", indent=1)
            log_info("THREAD", f"目标函数: {target.__name__ if hasattr(target, '__name__') else str(target)}", indent=1)
            if kwargs:
                log_info("THREAD", f"额外参数: {kwargs}", indent=1)
            log_info("THREAD", f"当前活跃线程数: {threading.active_count()}", indent=1)
            log_info("THREAD", f"==============================", indent=0)
            
            return thread
    
    def stop_thread(self, name: str, timeout: float = 2.0) -> bool:
        """
        停止指定线程
        
        停止线程并等待其结束（带超时）。如果线程绑定了任务上下文，会先使上下文失效。
        
        Args:
            name: 线程名称
            timeout: 等待超时时间（秒）
            
        Returns:
            True 表示成功停止，False 表示超时或线程不存在
        """
        stop_start_time = time.time()
        
        with self._lock:
            if name not in self._threads:
                log_warn("THREAD", f"========== 停止线程失败 ==========", indent=0)
                log_warn("THREAD", f"线程名称: {name}", indent=1)
                log_warn("THREAD", f"失败原因: 线程不存在", indent=1)
                log_warn("THREAD", f"==============================", indent=0)
                return False
            
            thread_info = self._threads[name]
            thread = thread_info.thread
            thread_id = thread.ident
            start_time = thread_info.start_time
            thread_type = thread_info.thread_type
            task_id = thread_info.context.task_id if thread_info.context else None
            
            # 详细的停止开始日志
            log_info("THREAD", f"========== 开始停止线程 ==========", indent=0)
            log_info("THREAD", f"线程名称: {name}", indent=1)
            log_info("THREAD", f"线程ID: {thread_id if thread_id else 'N/A'}", indent=1)
            log_info("THREAD", f"线程类型: {thread_type}", indent=1)
            log_info("THREAD", f"任务ID: {task_id if task_id else 'N/A'}", indent=1)
            log_info("THREAD", f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time)) if start_time else 'N/A'}", indent=1)
            log_info("THREAD", f"当前状态: {'运行中' if thread.is_alive() else '已结束'}", indent=1)
            log_info("THREAD", f"超时设置: {timeout}秒", indent=1)
            
            # 如果绑定了上下文，使上下文失效（这会触发任务取消）
            if thread_info.context is not None:
                try:
                    thread_info.context.invalidate()
                    log_info("THREAD", f"任务上下文已失效: 任务ID={thread_info.context.task_id}", indent=1)
                except Exception as e:
                    log_error("THREAD", f"使任务上下文失效失败: {e}", indent=1)
        
        # 等待线程结束（不在锁内，避免死锁）
        if thread.is_alive():
            log_info("THREAD", f"等待线程结束...", indent=1)
            thread.join(timeout=timeout)
            if thread.is_alive():
                stop_end_time = time.time()
                log_warn("THREAD", f"========== 停止线程超时 ==========", indent=0)
                log_warn("THREAD", f"线程名称: {name}", indent=1)
                log_warn("THREAD", f"线程ID: {thread_id if thread_id else 'N/A'}", indent=1)
                log_warn("THREAD", f"线程类型: {thread_type}", indent=1)
                log_warn("THREAD", f"任务ID: {task_id if task_id else 'N/A'}", indent=1)
                log_warn("THREAD", f"超时时间: {timeout}秒", indent=1)
                log_warn("THREAD", f"等待时长: {stop_end_time - stop_start_time:.3f}秒", indent=1)
                log_warn("THREAD", f"线程状态: 仍在运行", indent=1)
                log_warn("THREAD", f"==============================", indent=0)
                return False
        
        # 更新状态
        stop_end_time = time.time()
        with self._lock:
            if name in self._threads:
                self._threads[name].is_alive = False
                if start_time:
                    total_duration = stop_end_time - start_time
                else:
                    total_duration = 0
        
        # 详细的停止成功日志
        log_info("THREAD", f"========== 线程已停止 ==========", indent=0)
        log_info("THREAD", f"线程名称: {name}", indent=1)
        log_info("THREAD", f"线程ID: {thread_id if thread_id else 'N/A'}", indent=1)
        log_info("THREAD", f"线程类型: {thread_type}", indent=1)
        log_info("THREAD", f"任务ID: {task_id if task_id else 'N/A'}", indent=1)
        log_info("THREAD", f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time)) if start_time else 'N/A'}", indent=1)
        log_info("THREAD", f"停止时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stop_end_time))}", indent=1)
        log_info("THREAD", f"总运行时长: {total_duration:.3f}秒", indent=1)
        log_info("THREAD", f"停止等待时长: {stop_end_time - stop_start_time:.3f}秒", indent=1)
        log_info("THREAD", f"停止原因: 主动停止", indent=1)
        log_info("THREAD", f"当前活跃线程数: {threading.active_count()}", indent=1)
        log_info("THREAD", f"==============================", indent=0)
        return True
    
    def stop_threads_by_context(self, context: TaskContext, timeout: float = 2.0) -> int:
        """
        停止绑定到指定上下文的所有线程
        
        查找所有绑定到指定任务上下文的线程，并停止它们。这通常用于任务取消时。
        
        Args:
            context: 任务上下文
            timeout: 等待超时时间（秒）
            
        Returns:
            停止的线程数量
        """
        stop_start_time = time.time()
        
        # 先查找所有相关线程
        with self._lock:
            threads_to_stop = [
                (name, thread_info)
                for name, thread_info in self._threads.items()
                if thread_info.context is not None and thread_info.context.task_id == context.task_id
            ]
        
        if not threads_to_stop:
            log_debug("THREAD", f"========== 停止线程（按上下文）==========", indent=0)
            log_debug("THREAD", f"任务ID: {context.task_id}", indent=1)
            log_debug("THREAD", f"结果: 没有找到绑定到该任务的线程", indent=1)
            log_debug("THREAD", f"==============================", indent=0)
            return 0
        
        log_info("THREAD", f"========== 停止线程（按上下文）==========", indent=0)
        log_info("THREAD", f"任务ID: {context.task_id}", indent=1)
        log_info("THREAD", f"找到线程数: {len(threads_to_stop)}个", indent=1)
        log_info("THREAD", f"线程列表: {[name for name, _ in threads_to_stop]}", indent=1)
        log_info("THREAD", f"超时设置: {timeout}秒", indent=1)
        
        # 使上下文失效（这会触发任务取消）
        try:
            context.invalidate()
            log_info("THREAD", f"任务上下文已失效", indent=1)
        except Exception as e:
            log_error("THREAD", f"使任务上下文失效失败: {e}", indent=1)
        
        # 停止所有相关线程
        stopped_count = 0
        failed_count = 0
        for name, thread_info in threads_to_stop:
            if self.stop_thread(name, timeout=timeout):
                stopped_count += 1
            else:
                failed_count += 1
        
        stop_end_time = time.time()
        log_info("THREAD", f"========== 停止完成（按上下文）==========", indent=0)
        log_info("THREAD", f"任务ID: {context.task_id}", indent=1)
        log_info("THREAD", f"总线程数: {len(threads_to_stop)}", indent=1)
        log_info("THREAD", f"成功停止: {stopped_count}个", indent=1)
        log_info("THREAD", f"停止失败: {failed_count}个", indent=1)
        log_info("THREAD", f"总耗时: {stop_end_time - stop_start_time:.3f}秒", indent=1)
        log_info("THREAD", f"==============================", indent=0)
        
        return stopped_count
    
    def stop_all_threads(self, timeout: float = 2.0) -> int:
        """
        停止所有线程
        
        停止所有注册的线程。先停止按需启动的线程，再停止常驻线程。
        
        Args:
            timeout: 等待超时时间（秒）
            
        Returns:
            停止的线程数量
        """
        stop_start_time = time.time()
        
        with self._lock:
            all_threads = list(self._threads.items())
        
        if not all_threads:
            log_debug("THREAD", f"========== 停止所有线程 ==========", indent=0)
            log_debug("THREAD", f"结果: 没有需要停止的线程", indent=1)
            log_debug("THREAD", f"==============================", indent=0)
            return 0
        
        # 先停止按需启动的线程，再停止常驻线程
        on_demand_threads = [(name, info) for name, info in all_threads if info.thread_type == "on_demand"]
        resident_threads = [(name, info) for name, info in all_threads if info.thread_type == "resident"]
        
        log_info("THREAD", f"========== 停止所有线程 ==========", indent=0)
        log_info("THREAD", f"总线程数: {len(all_threads)}个", indent=1)
        log_info("THREAD", f"  按需启动线程: {len(on_demand_threads)}个", indent=1)
        log_info("THREAD", f"  常驻线程: {len(resident_threads)}个", indent=1)
        log_info("THREAD", f"超时设置: {timeout}秒", indent=1)
        log_info("THREAD", f"停止顺序: 先按需启动线程，后常驻线程", indent=1)
        
        stopped_count = 0
        failed_count = 0
        
        # 停止按需启动的线程
        if on_demand_threads:
            log_info("THREAD", f"开始停止按需启动线程...", indent=1)
            for name, thread_info in on_demand_threads:
                if self.stop_thread(name, timeout=timeout):
                    stopped_count += 1
                else:
                    failed_count += 1
        
        # 停止常驻线程
        if resident_threads:
            log_info("THREAD", f"开始停止常驻线程...", indent=1)
            for name, thread_info in resident_threads:
                if self.stop_thread(name, timeout=timeout):
                    stopped_count += 1
                else:
                    failed_count += 1
        
        stop_end_time = time.time()
        log_info("THREAD", f"========== 停止所有线程完成 ==========", indent=0)
        log_info("THREAD", f"总线程数: {len(all_threads)}个", indent=1)
        log_info("THREAD", f"成功停止: {stopped_count}个", indent=1)
        log_info("THREAD", f"停止失败: {failed_count}个", indent=1)
        log_info("THREAD", f"总耗时: {stop_end_time - stop_start_time:.3f}秒", indent=1)
        log_info("THREAD", f"当前活跃线程数: {threading.active_count()}", indent=1)
        log_info("THREAD", f"==============================", indent=0)
        
        return stopped_count
    
    def get_thread_info(self, name: str) -> Optional[ThreadInfo]:
        """
        获取线程信息
        
        Args:
            name: 线程名称
            
        Returns:
            线程信息，如果不存在则返回None
        """
        with self._lock:
            return self._threads.get(name)
    
    def get_threads_by_context(self, context: TaskContext) -> List[ThreadInfo]:
        """
        获取绑定到指定上下文的所有线程
        
        Args:
            context: 任务上下文
            
        Returns:
            绑定到该上下文的所有线程信息列表
        """
        with self._lock:
            return [
                thread_info
                for thread_info in self._threads.values()
                if thread_info.context is not None and thread_info.context.task_id == context.task_id
            ]
    
    def get_all_threads(self) -> Dict[str, ThreadInfo]:
        """
        获取所有线程信息
        
        Returns:
            所有线程信息的字典（线程名称 -> ThreadInfo）
        """
        with self._lock:
            return self._threads.copy()
    
    def get_thread_count(self) -> Dict[str, int]:
        """
        获取线程统计信息
        
        Returns:
            包含线程统计信息的字典：
            - total: 总线程数
            - resident: 常驻线程数
            - on_demand: 按需启动线程数
            - alive: 存活线程数
        """
        with self._lock:
            total = len(self._threads)
            resident = sum(1 for info in self._threads.values() if info.thread_type == "resident")
            on_demand = sum(1 for info in self._threads.values() if info.thread_type == "on_demand")
            alive = sum(1 for info in self._threads.values() if info.is_alive and info.thread.is_alive())
            
            return {
                "total": total,
                "resident": resident,
                "on_demand": on_demand,
                "alive": alive
            }
    
    def cleanup_dead_threads(self):
        """
        清理已死亡的线程记录
        
        移除所有已结束的线程记录，释放内存。
        """
        with self._lock:
            dead_threads = [
                name
                for name, thread_info in self._threads.items()
                if not thread_info.is_alive or not thread_info.thread.is_alive()
            ]
            
            for name in dead_threads:
                del self._threads[name]
            
            if dead_threads:
                log_debug("THREAD", f"清理已死亡的线程记录: {len(dead_threads)}个", indent=0)
