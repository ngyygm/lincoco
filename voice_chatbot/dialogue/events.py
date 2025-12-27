"""
事件系统模块

提供事件类型定义、事件数据结构、事件时间戳、事件优先级和事件过滤机制
参考：voice_chatbot/dialogue/state.py
"""

import threading
import time
import heapq
import uuid
from enum import Enum
from typing import List, Dict, Optional, Any, Callable
from dataclasses import dataclass, field

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class EventType(Enum):
    """事件类型枚举"""
    # ===== 系统事件 =====
    SYSTEM_INIT = "system_init"                        # 系统初始化
    SYSTEM_START = "system_start"                      # 系统启动
    SYSTEM_STOP = "system_stop"                        # 系统停止
    
    # ===== VAD事件 =====
    VAD_SPEECH_START = "vad_speech_start"              # 语音活动开始
    VAD_SPEECH_END = "vad_speech_end"                  # 语音活动结束
    
    # ===== ASR事件 =====
    ASR_RECOGNITION_START = "asr_recognition_start"    # ASR识别开始
    ASR_MODEL_INFERENCE = "asr_model_inference"        # ASR模型推理（内部细节）
    ASR_PARTIAL_RESULT = "asr_partial_result"          # ASR部分识别结果
    ASR_STABLE_RESULT = "asr_stable_result"            # ASR稳定结果（早期提交优化）
    ASR_FINAL_RESULT = "asr_final_result"              # ASR最终识别结果
    ASR_RECOGNITION_END = "asr_recognition_end"        # ASR识别结束
    
    # ===== LLM事件 =====
    LLM_GENERATION_START = "llm_generation_start"      # LLM生成开始
    LLM_REQUEST_SENT = "llm_request_sent"              # LLM API请求已发送
    LLM_STREAM_START = "llm_stream_start"              # LLM开始收到流式响应
    LLM_SENTENCE_GENERATED = "llm_sentence_generated"  # LLM生成句子
    LLM_FIRST_SENTENCE = "llm_first_sentence"          # LLM第一句生成完成
    LLM_GENERATION_COMPLETE = "llm_generation_complete" # LLM生成完成
    
    # ===== TTS事件 =====
    TTS_GENERATION_START = "tts_generation_start"      # TTS生成开始
    TTS_CACHE_HIT = "tts_cache_hit"                    # TTS缓存命中
    TTS_FIRST_AUDIO_READY = "tts_first_audio_ready"    # TTS第一个音频就绪
    TTS_GENERATION_COMPLETE = "tts_generation_complete" # TTS生成完成
    
    # ===== 音频播放事件 =====
    AUDIO_PLAY_START = "audio_play_start"              # 音频播放开始
    AUDIO_PLAY_END = "audio_play_end"                  # 音频播放结束
    AUDIO_PAUSED = "audio_paused"                      # 音频暂停
    AUDIO_RESUMED = "audio_resumed"                    # 音频恢复
    AUDIO_PLAY_PROGRESS = "audio_play_progress"        # 音频播放进度（流式状态）
    
    # ===== 任务和状态事件 =====
    TASK_CREATED = "task_created"                      # 任务创建
    TASK_CANCELLED = "task_cancelled"                  # 任务取消
    TASK_COMPLETED = "task_completed"                  # 任务完成
    STATE_CHANGED = "state_changed"                    # 状态转换
    USER_INTERRUPT = "user_interrupt"                  # 用户打断
    
    # ===== 线程生命周期事件 =====
    THREAD_STARTED = "thread_started"                  # 线程启动
    THREAD_ENDED = "thread_ended"                      # 线程结束
    
    # ===== 打断相关事件 =====
    INTERRUPT_DETECTED = "interrupt_detected"          # 检测到可能的打断
    INTERRUPT_VALIDATED = "interrupt_validated"        # 打断验证通过（真实打断）
    INTERRUPT_CANCELLED = "interrupt_cancelled"        # 打断取消（误判）
    
    # ===== 通用日志事件（用于非性能关键的日志） =====
    LOG_INFO = "log_info"                              # INFO级别日志
    LOG_WARN = "log_warn"                              # WARN级别日志
    LOG_ERROR = "log_error"                            # ERROR级别日志
    LOG_DEBUG = "log_debug"                            # DEBUG级别日志


# 事件优先级默认值映射
EVENT_PRIORITY_MAP = {
    # 最高优先级：用户打断和任务取消
    EventType.USER_INTERRUPT: 10,
    EventType.TASK_CANCELLED: 9,
    
    # 系统事件
    EventType.SYSTEM_INIT: 8,
    EventType.SYSTEM_START: 8,
    EventType.SYSTEM_STOP: 8,
    
    # ASR事件
    EventType.ASR_FINAL_RESULT: 7,
    EventType.ASR_STABLE_RESULT: 7,  # 稳定结果与最终结果同优先级（早期提交）
    EventType.ASR_RECOGNITION_END: 7,
    EventType.ASR_RECOGNITION_START: 6,
    EventType.ASR_MODEL_INFERENCE: 5,
    EventType.ASR_PARTIAL_RESULT: 2,
    
    # VAD事件
    EventType.VAD_SPEECH_START: 6,
    EventType.VAD_SPEECH_END: 6,
    
    # LLM事件
    EventType.LLM_GENERATION_START: 6,
    EventType.LLM_REQUEST_SENT: 5,
    EventType.LLM_STREAM_START: 5,
    EventType.LLM_FIRST_SENTENCE: 6,
    EventType.LLM_SENTENCE_GENERATED: 5,
    EventType.LLM_GENERATION_COMPLETE: 5,
    
    # TTS事件
    EventType.TTS_GENERATION_START: 5,
    EventType.TTS_CACHE_HIT: 5,
    EventType.TTS_FIRST_AUDIO_READY: 5,
    EventType.TTS_GENERATION_COMPLETE: 5,
    
    # 播放事件
    EventType.AUDIO_PLAY_START: 4,
    EventType.AUDIO_PLAY_PROGRESS: 3,
    EventType.AUDIO_PLAY_END: 4,
    EventType.AUDIO_PAUSED: 4,
    EventType.AUDIO_RESUMED: 4,
    
    # 状态事件
    EventType.STATE_CHANGED: 3,
    EventType.TASK_CREATED: 6,
    EventType.TASK_COMPLETED: 5,
    
    # 线程生命周期事件
    EventType.THREAD_STARTED: 4,
    EventType.THREAD_ENDED: 4,
    
    # 打断相关事件
    EventType.INTERRUPT_DETECTED: 8,
    EventType.INTERRUPT_VALIDATED: 8,
    EventType.INTERRUPT_CANCELLED: 7,
    
    # 通用日志事件（较低优先级，避免阻塞关键事件）
    EventType.LOG_INFO: 2,
    EventType.LOG_WARN: 3,
    EventType.LOG_ERROR: 4,
    EventType.LOG_DEBUG: 1,
}


def get_default_priority(event_type: EventType) -> int:
    """
    获取事件类型的默认优先级
    
    Args:
        event_type: 事件类型
        
    Returns:
        默认优先级（0-10）
    """
    priority = EVENT_PRIORITY_MAP.get(event_type, 5)
    log_debug("EVENT", f"获取事件类型 [{event_type.value}] 的默认优先级: {priority}", indent=2)
    return priority


@dataclass
class Event:
    """事件数据结构"""
    event_type: EventType
    timestamp: float
    priority: int = 5
    data: Dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    event_id: Optional[str] = None
    
    def __post_init__(self):
        """初始化后处理"""
        # 如果没有提供优先级，使用默认值
        original_priority = self.priority
        if self.priority == 5 and self.event_type in EVENT_PRIORITY_MAP:
            self.priority = get_default_priority(self.event_type)
            log_debug("EVENT", f"事件 [{self.event_type.value}] 使用默认优先级: {self.priority}", indent=2)
        
        # 如果没有提供event_id，生成一个
        if self.event_id is None:
            self.event_id = str(uuid.uuid4())
            log_debug("EVENT", f"为事件 [{self.event_type.value}] 生成新ID: {self.event_id}", indent=2)
        
        # 验证优先级范围
        if not (0 <= self.priority <= 10):
            log_warn("EVENT", f"事件 [{self.event_type.value}] 优先级超出范围: {self.priority}，将限制在0-10", indent=1)
            self.priority = max(0, min(10, self.priority))
        
        # 记录事件创建详情
        log_info("EVENT", f"创建事件: {self.event_type.value}", indent=1)
        log_debug("EVENT", f"  事件ID: {self.event_id}", indent=2)
        log_debug("EVENT", f"  优先级: {self.priority} (原始: {original_priority})", indent=2)
        log_debug("EVENT", f"  时间戳: {self.timestamp:.6f}", indent=2)
        log_debug("EVENT", f"  来源: {self.source if self.source else '未指定'}", indent=2)
        if self.data:
            data_preview = str(self.data)[:200]  # 限制数据预览长度
            log_debug("EVENT", f"  数据: {data_preview}{'...' if len(str(self.data)) > 200 else ''}", indent=2)
    
    def to_dict(self) -> Dict[str, Any]:
        """将事件转换为字典"""
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "priority": self.priority,
            "data": self.data,
            "source": self.source,
            "event_id": self.event_id
        }


class EventFilter:
    """事件过滤器"""
    
    def __init__(self, 
                 event_types: Optional[List[EventType]] = None,
                 min_priority: int = 0,
                 max_priority: int = 10,
                 sources: Optional[List[str]] = None,
                 custom_filter: Optional[Callable[[Event], bool]] = None):
        """
        初始化事件过滤器
        
        Args:
            event_types: 允许的事件类型列表，如果为None则不过滤
            min_priority: 最小优先级（包含）
            max_priority: 最大优先级（包含）
            sources: 允许的事件来源列表，如果为None则不过滤
            custom_filter: 自定义过滤函数，接收Event返回bool
        """
        self.event_types = set(event_types) if event_types else None
        self.min_priority = min_priority
        self.max_priority = max_priority
        self.sources = set(sources) if sources else None
        self.custom_filter = custom_filter
        
        # 记录过滤器初始化详情
        log_debug("EVENT", "初始化事件过滤器", indent=1)
        if self.event_types:
            event_types_str = ", ".join([et.value for et in self.event_types])
            log_debug("EVENT", f"  事件类型: [{event_types_str}]", indent=2)
        else:
            log_debug("EVENT", f"  事件类型: 全部", indent=2)
        log_debug("EVENT", f"  优先级范围: [{self.min_priority}, {self.max_priority}]", indent=2)
        if self.sources:
            sources_str = ", ".join(self.sources)
            log_debug("EVENT", f"  来源: [{sources_str}]", indent=2)
        else:
            log_debug("EVENT", f"  来源: 全部", indent=2)
        log_debug("EVENT", f"  自定义过滤器: {'是' if self.custom_filter else '否'}", indent=2)
    
    def matches(self, event: Event) -> bool:
        """
        检查事件是否匹配过滤器
        
        Args:
            event: 事件对象
            
        Returns:
            True表示匹配，False表示不匹配
        """
        # 按事件类型过滤
        if self.event_types is not None:
            if event.event_type not in self.event_types:
                return False
        
        # 按优先级过滤
        if not (self.min_priority <= event.priority <= self.max_priority):
            return False
        
        # 按来源过滤
        if self.sources is not None:
            if event.source is None or event.source not in self.sources:
                return False
        
        # 自定义过滤函数
        if self.custom_filter is not None:
            if not self.custom_filter(event):
                return False
        
        return True


class EventBus:
    """事件总线 - 负责事件的发布和订阅"""
    
    def __init__(self, config=None):
        """
        初始化事件总线
        
        Args:
            config: 配置对象（可选）
        """
        self.config = config
        
        # 订阅者列表：[(handler, filter), ...]
        self._subscribers: List[tuple] = []
        self._subscribers_lock = threading.Lock()
        
        # 事件队列（优先级队列）：[(priority, timestamp, sequence, event), ...]
        # 注意：heapq是最小堆，所以使用负优先级实现最大堆
        # 添加sequence用于避免比较Event对象
        self._event_queue: List[tuple] = []
        self._queue_lock = threading.Lock()
        self._sequence_counter = 0  # 序列计数器，用于避免比较Event对象
        
        # 事件处理线程
        self._processing = False
        self._processing_thread: Optional[threading.Thread] = None
        
        # 事件统计
        self._event_count = 0
        self._dropped_count = 0
        
        log_info("EVENT", "事件总线初始化完成", indent=0)
        log_debug("EVENT", f"  配置对象: {'已提供' if self.config else '未提供'}", indent=1)
        log_debug("EVENT", f"  初始队列大小: 0", indent=1)
        log_debug("EVENT", f"  初始订阅者数量: 0", indent=1)
    
    def publish(self, event: Event):
        """
        发布事件
        
        Args:
            event: 事件对象
        """
        log_info("EVENT", f"发布事件: {event.event_type.value}", indent=0)
        log_debug("EVENT", f"  事件ID: {event.event_id}", indent=1)
        log_debug("EVENT", f"  优先级: {event.priority}", indent=1)
        log_debug("EVENT", f"  来源: {event.source if event.source else '未指定'}", indent=1)
        log_debug("EVENT", f"  时间戳: {event.timestamp:.6f}", indent=1)
        
        with self._queue_lock:
            # 使用负优先级实现最大堆（优先级高的先处理）
            # 添加sequence避免比较Event对象
            self._sequence_counter += 1
            queue_size_before = len(self._event_queue)
            heapq.heappush(self._event_queue, (-event.priority, event.timestamp, self._sequence_counter, event))
            self._event_count += 1
            queue_size_after = len(self._event_queue)
            
            log_debug("EVENT", f"  序列号: {self._sequence_counter}", indent=1)
            log_debug("EVENT", f"  队列大小: {queue_size_before} -> {queue_size_after}", indent=1)
            log_debug("EVENT", f"  总事件数: {self._event_count}", indent=1)
        
        if event.data:
            data_preview = str(event.data)[:200]  # 限制数据预览长度
            log_debug("EVENT", f"  数据预览: {data_preview}{'...' if len(str(event.data)) > 200 else ''}", indent=1)
    
    def subscribe(self, handler: Callable[[Event], None], 
                  filter: Optional[EventFilter] = None):
        """
        订阅事件
        
        Args:
            handler: 事件处理函数，接收Event参数
            filter: 事件过滤器，如果为None则接收所有事件
        """
        handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
        log_info("EVENT", f"添加订阅者: {handler_name}", indent=0)
        log_debug("EVENT", f"  处理函数: {handler_name}", indent=1)
        log_debug("EVENT", f"  过滤器: {'已提供' if filter else '无（接收所有事件）'}", indent=1)
        
        with self._subscribers_lock:
            subscribers_count_before = len(self._subscribers)
            self._subscribers.append((handler, filter))
            subscribers_count_after = len(self._subscribers)
            log_debug("EVENT", f"  订阅者数量: {subscribers_count_before} -> {subscribers_count_after}", indent=1)
    
    def unsubscribe(self, handler: Callable[[Event], None]):
        """
        取消订阅事件
        
        Args:
            handler: 要取消的事件处理函数
        """
        handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
        log_info("EVENT", f"移除订阅者: {handler_name}", indent=0)
        log_debug("EVENT", f"  处理函数: {handler_name}", indent=1)
        
        with self._subscribers_lock:
            subscribers_count_before = len(self._subscribers)
            self._subscribers = [(h, f) for h, f in self._subscribers if h != handler]
            subscribers_count_after = len(self._subscribers)
            removed_count = subscribers_count_before - subscribers_count_after
            log_debug("EVENT", f"  订阅者数量: {subscribers_count_before} -> {subscribers_count_after}", indent=1)
            log_debug("EVENT", f"  移除数量: {removed_count}", indent=1)
    
    def clear_subscribers(self):
        """清空所有订阅者"""
        with self._subscribers_lock:
            subscribers_count_before = len(self._subscribers)
            self._subscribers.clear()
            log_info("EVENT", "已清空所有订阅者", indent=0)
            log_debug("EVENT", f"  清空前订阅者数量: {subscribers_count_before}", indent=1)
    
    def start_processing(self):
        """启动事件处理线程"""
        if self._processing:
            log_warn("EVENT", "事件处理线程已在运行", indent=0)
            log_debug("EVENT", f"  线程名称: {self._processing_thread.name if self._processing_thread else '未知'}", indent=1)
            log_debug("EVENT", f"  线程ID: {self._processing_thread.ident if self._processing_thread else '未知'}", indent=1)
            log_debug("EVENT", f"  线程存活状态: {self._processing_thread.is_alive() if self._processing_thread else '未知'}", indent=1)
            return
        
        log_info("EVENT", "启动事件处理线程", indent=0)
        self._processing = True
        self._processing_thread = threading.Thread(
            target=self._process_events,
            daemon=True,
            name="EventBus-Processor"
        )
        self._processing_thread.start()
        log_info("EVENT", "事件处理线程已启动", indent=0)
        log_debug("EVENT", f"  线程名称: {self._processing_thread.name}", indent=1)
        log_debug("EVENT", f"  线程ID: {self._processing_thread.ident}", indent=1)
        log_debug("EVENT", f"  守护线程: {self._processing_thread.daemon}", indent=1)
    
    def stop_processing(self):
        """停止事件处理线程"""
        if not self._processing:
            log_warn("EVENT", "事件处理线程未运行", indent=0)
            log_debug("EVENT", f"  线程状态: 未运行", indent=1)
            return
        
        log_info("EVENT", "停止事件处理线程", indent=0)
        log_debug("EVENT", f"  线程名称: {self._processing_thread.name if self._processing_thread else '未知'}", indent=1)
        log_debug("EVENT", f"  线程ID: {self._processing_thread.ident if self._processing_thread else '未知'}", indent=1)
        
        self._processing = False
        
        if self._processing_thread and self._processing_thread.is_alive():
            log_debug("EVENT", f"  等待线程结束（超时: 2.0秒）", indent=1)
            thread_alive_before = self._processing_thread.is_alive()
            self._processing_thread.join(timeout=2.0)
            thread_alive_after = self._processing_thread.is_alive()
            log_debug("EVENT", f"  线程存活状态: {thread_alive_before} -> {thread_alive_after}", indent=1)
            if thread_alive_after:
                log_warn("EVENT", "  线程未在超时时间内结束", indent=2)
        else:
            log_debug("EVENT", f"  线程不存在或已结束", indent=1)
        
        log_info("EVENT", "事件处理线程已停止", indent=0)
    
    def _process_events(self):
        """事件处理线程主循环"""
        log_info("EVENT", "事件处理线程启动", indent=1)
        
        while self._processing:
            try:
                # 从队列获取事件（优先级最高的）
                event = None
                with self._queue_lock:
                    if self._event_queue:
                        _, _, _, event = heapq.heappop(self._event_queue)
                
                if event is None:
                    # 队列为空，短暂休眠
                    time.sleep(0.01)
                    continue
                
                # 记录事件处理开始
                log_debug("EVENT", f"开始处理事件: {event.event_type.value}", indent=2)
                log_debug("EVENT", f"  事件ID: {event.event_id}", indent=3)
                log_debug("EVENT", f"  优先级: {event.priority}", indent=3)
                log_debug("EVENT", f"  来源: {event.source if event.source else '未指定'}", indent=3)
                log_debug("EVENT", f"  时间戳: {event.timestamp:.6f}", indent=3)
                
                # 获取当前订阅者列表（线程安全）
                with self._subscribers_lock:
                    subscribers = self._subscribers.copy()
                
                log_debug("EVENT", f"  订阅者数量: {len(subscribers)}", indent=3)
                
                # 通知所有匹配的订阅者
                notified_count = 0
                filtered_count = 0
                for handler, event_filter in subscribers:
                    handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
                    try:
                        # 检查过滤器类型
                        if event_filter is not None and not hasattr(event_filter, 'matches'):
                            log_error("EVENT", f"事件过滤器类型错误: {type(event_filter)}, 期望 EventFilter 实例", indent=2)
                            log_error("EVENT", f"  处理函数: {handler_name}", indent=3)
                            log_error("EVENT", f"  事件类型: {event.event_type.value if event else 'Unknown'}", indent=3)
                            filtered_count += 1
                            continue
                        
                        # 检查过滤器
                        if event_filter is None:
                            log_debug("EVENT", f"  通知订阅者 (无过滤器): {handler_name}", indent=3)
                            handler(event)
                            notified_count += 1
                        elif event_filter.matches(event):
                            log_debug("EVENT", f"  通知订阅者 (匹配过滤器): {handler_name}", indent=3)
                            handler(event)
                            notified_count += 1
                        else:
                            log_debug("EVENT", f"  跳过订阅者 (不匹配过滤器): {handler_name}", indent=3)
                            filtered_count += 1
                    except Exception as e:
                        # 增强错误日志，包含更多上下文信息
                        error_details = {
                            "handler": handler.__name__ if hasattr(handler, '__name__') else str(handler),
                            "filter_type": type(event_filter).__name__ if event_filter else "None",
                            "event_type": event.event_type.value if event else "Unknown",
                            "error": str(e)
                        }
                        log_error("EVENT", f"事件处理函数执行错误: {error_details['error']}", indent=2)
                        log_error("EVENT", f"  处理函数: {error_details['handler']}", indent=3)
                        log_error("EVENT", f"  过滤器类型: {error_details['filter_type']}", indent=3)
                        log_error("EVENT", f"  事件类型: {error_details['event_type']}", indent=3)
                        import traceback
                        log_error("EVENT", f"  异常堆栈:\n{traceback.format_exc()}", indent=3)
                        filtered_count += 1
                
                # 记录事件处理结果
                log_debug("EVENT", f"事件处理完成: {event.event_type.value}", indent=2)
                log_debug("EVENT", f"  已通知订阅者: {notified_count}", indent=3)
                log_debug("EVENT", f"  已过滤订阅者: {filtered_count}", indent=3)
                log_debug("EVENT", f"  总订阅者: {len(subscribers)}", indent=3)
                
                if notified_count == 0:
                    log_warn("EVENT", f"事件无订阅者处理: {event.event_type.value}", indent=2)
                
            except Exception as e:
                log_error("EVENT", f"事件处理线程错误: {e}", indent=1)
                time.sleep(0.1)
        
        log_info("EVENT", "事件处理线程结束", indent=1)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取事件统计信息
        
        Returns:
            统计信息字典
        """
        log_debug("EVENT", "获取事件统计信息", indent=0)
        
        with self._queue_lock:
            queue_size = len(self._event_queue)
        
        with self._subscribers_lock:
            subscriber_count = len(self._subscribers)
        
        stats = {
            "total_events": self._event_count,
            "dropped_events": self._dropped_count,
            "queue_size": queue_size,
            "subscriber_count": subscriber_count,
            "processing": self._processing
        }
        
        log_debug("EVENT", f"  总事件数: {stats['total_events']}", indent=1)
        log_debug("EVENT", f"  丢弃事件数: {stats['dropped_events']}", indent=1)
        log_debug("EVENT", f"  队列大小: {stats['queue_size']}", indent=1)
        log_debug("EVENT", f"  订阅者数量: {stats['subscriber_count']}", indent=1)
        log_debug("EVENT", f"  处理状态: {stats['processing']}", indent=1)
        
        return stats


# 便捷函数：创建事件
def create_event(event_type: EventType, 
                 data: Optional[Dict[str, Any]] = None,
                 source: Optional[str] = None,
                 priority: Optional[int] = None) -> Event:
    """
    创建事件对象（便捷函数）
    
    Args:
        event_type: 事件类型
        data: 事件数据
        source: 事件来源
        priority: 事件优先级（如果为None则使用默认值）
        
    Returns:
        事件对象
    """
    log_debug("EVENT", f"create_event: 创建事件 {event_type.value}", indent=0)
    log_debug("EVENT", f"  来源: {source if source else '未指定'}", indent=1)
    
    if data is None:
        data = {}
        log_debug("EVENT", f"  数据: 空", indent=1)
    else:
        data_preview = str(data)[:200]
        log_debug("EVENT", f"  数据: {data_preview}{'...' if len(str(data)) > 200 else ''}", indent=1)
    
    if priority is None:
        priority = get_default_priority(event_type)
        log_debug("EVENT", f"  优先级: {priority} (默认)", indent=1)
    else:
        log_debug("EVENT", f"  优先级: {priority} (指定)", indent=1)
    
    timestamp = time.time()
    log_debug("EVENT", f"  时间戳: {timestamp:.6f}", indent=1)
    
    event = Event(
        event_type=event_type,
        timestamp=timestamp,
        priority=priority,
        data=data,
        source=source
    )
    
    log_debug("EVENT", f"  事件ID: {event.event_id}", indent=1)
    return event

