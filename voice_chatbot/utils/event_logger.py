"""
统一事件日志模块

通过事件系统统一管理所有模块的日志输出，实现统一的日志格式和时间记录
"""

import sys
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Set
from pathlib import Path

from voice_chatbot.dialogue.events import Event, EventType, EventFilter
from voice_chatbot.utils.sequence_diagram_recorder import SequenceDiagramRecorder
from voice_chatbot.utils.logger import _get_global_logger


# 启用Windows虚拟终端支持（用于ANSI转义码显示颜色）
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except:
        pass


# 事件类型到日志类别的映射
EVENT_CATEGORY_MAP = {
    # 系统事件
    EventType.SYSTEM_INIT: "MGR",
    EventType.SYSTEM_START: "MGR",
    EventType.SYSTEM_STOP: "MGR",
    
    # VAD事件
    EventType.VAD_SPEECH_START: "VAD",
    EventType.VAD_SPEECH_END: "VAD",
    
    # ASR事件
    EventType.ASR_RECOGNITION_START: "ASR",
    EventType.ASR_MODEL_INFERENCE: "ASR",
    EventType.ASR_PARTIAL_RESULT: "ASR",
    EventType.ASR_STABLE_RESULT: "ASR",
    EventType.ASR_FINAL_RESULT: "ASR",
    EventType.ASR_RECOGNITION_END: "ASR",
    
    # LLM事件
    EventType.LLM_GENERATION_START: "LLM",
    EventType.LLM_REQUEST_SENT: "LLM",
    EventType.LLM_STREAM_START: "LLM",
    EventType.LLM_SENTENCE_GENERATED: "LLM",
    EventType.LLM_FIRST_SENTENCE: "LLM",
    EventType.LLM_GENERATION_COMPLETE: "LLM",
    
    # TTS事件
    EventType.TTS_GENERATION_START: "TTS",
    EventType.TTS_CACHE_HIT: "TTS",
    EventType.TTS_FIRST_AUDIO_READY: "TTS",
    EventType.TTS_GENERATION_COMPLETE: "TTS",
    
    # 播放事件
    EventType.AUDIO_PLAY_START: "PLAY",
    EventType.AUDIO_PLAY_END: "PLAY",
    EventType.AUDIO_PAUSED: "PLAY",
    EventType.AUDIO_RESUMED: "PLAY",
    EventType.AUDIO_PLAY_PROGRESS: "PLAY",
    
    # 任务和状态事件
    EventType.TASK_CREATED: "TASK",
    EventType.TASK_CANCELLED: "TASK",
    EventType.STATE_CHANGED: "STATE",
    EventType.USER_INTERRUPT: "INT",
    
    # 通用日志事件
    EventType.LOG_INFO: "LOG",
    EventType.LOG_WARN: "LOG",
    EventType.LOG_ERROR: "LOG",
    EventType.LOG_DEBUG: "LOG",
}


# 事件类型到日志消息模板的映射
EVENT_MESSAGE_TEMPLATES = {
    # 系统事件
    EventType.SYSTEM_INIT: "系统初始化完成",
    EventType.SYSTEM_START: "系统已启动",
    EventType.SYSTEM_STOP: "系统已停止",
    
    # VAD事件
    EventType.VAD_SPEECH_START: "检测到语音活动",
    EventType.VAD_SPEECH_END: "语音活动结束",
    
    # ASR事件
    EventType.ASR_RECOGNITION_START: "ASR识别开始（任务ID: {task_id}）",
    EventType.ASR_MODEL_INFERENCE: "ASR模型推理",
    EventType.ASR_PARTIAL_RESULT: "部分识别: {text}",
    EventType.ASR_STABLE_RESULT: "稳定识别结果（早期提交）: {text}",
    EventType.ASR_FINAL_RESULT: "最终识别结果: {text}",
    EventType.ASR_RECOGNITION_END: "ASR识别结束",
    
    # LLM事件
    EventType.LLM_GENERATION_START: "LLM生成开始（任务ID: {task_id}）",
    EventType.LLM_REQUEST_SENT: "API请求已发送",
    EventType.LLM_STREAM_START: "开始接收流式响应",
    EventType.LLM_SENTENCE_GENERATED: "句子{sentence_index}: {text} [耗时: {elapsed_ms:.1f}ms]",
    EventType.LLM_FIRST_SENTENCE: "第一句生成完成: {text}",
    EventType.LLM_GENERATION_COMPLETE: "LLM生成完成，共{sentence_count}句 [耗时: {elapsed_ms:.1f}ms]",
    
    # TTS事件
    EventType.TTS_GENERATION_START: "TTS生成开始: {text}",
    EventType.TTS_CACHE_HIT: "TTS缓存命中: {text}",
    EventType.TTS_FIRST_AUDIO_READY: "TTS第一个音频就绪 [生成: {generation_time_ms:.1f}ms, 处理: {processing_time_ms:.1f}ms]",
    EventType.TTS_GENERATION_COMPLETE: "TTS生成完成",
    
    # 播放事件
    EventType.AUDIO_PLAY_START: "开始播放音频: {text}",
    EventType.AUDIO_PLAY_END: "音频播放结束",
    EventType.AUDIO_PAUSED: "音频暂停",
    EventType.AUDIO_RESUMED: "音频恢复",
    
    # 任务和状态事件
    EventType.TASK_CREATED: "任务创建（任务ID: {task_id}）",
    EventType.TASK_CANCELLED: "任务取消（任务ID: {task_id}）",
    EventType.STATE_CHANGED: "状态转换: {from_state} → {to_state}（{reason}）",
    EventType.USER_INTERRUPT: "用户打断",
}


# 需要过滤（不输出日志）的事件类型
FILTERED_EVENT_TYPES: Set[EventType] = {
    EventType.AUDIO_PLAY_PROGRESS,  # 播放进度事件太频繁，不输出
    EventType.ASR_PARTIAL_RESULT,   # 部分识别结果太频繁，不输出
}


class EventLogger:
    """
    统一事件日志处理器
    
    订阅所有事件，根据事件类型格式化输出日志，并记录事件时间轴
    """
    
    def __init__(self, 
                 enable_console: bool = True,
                 enable_file: bool = True,
                 log_dir: str = "logs",
                 show_delta: bool = True,
                 filtered_events: Optional[Set[EventType]] = None,
                 enable_sequence_diagram: bool = True,
                 filtered_levels: Optional[List[str]] = None,
                 filtered_categories: Optional[List[str]] = None,
                 config: Optional[Any] = None,
                 logger=None):
        """
        初始化事件日志处理器
        
        Args:
            enable_console: 是否输出到控制台（已废弃，由 Logger 统一管理，保留以保持向后兼容）
            enable_file: 是否输出到文件（已废弃，由 Logger 统一管理，保留以保持向后兼容）
            log_dir: 日志文件目录（已废弃，由 Logger 统一管理，保留以保持向后兼容）
            show_delta: 是否显示与上一事件的时间差
            filtered_events: 需要过滤的事件类型集合
            enable_sequence_diagram: 是否启用sequenceDiagram记录
            filtered_levels: 被过滤的日志级别列表（已废弃，由 Logger 统一管理，保留以保持向后兼容）
            filtered_categories: 被过滤的日志类别列表（已废弃，由 Logger 统一管理，保留以保持向后兼容）
            config: 配置对象（可选，用于读取配置）
            logger: Logger 实例（可选，如果为 None 则使用全局 Logger）
        """
        # 使用传入的 logger 或全局 logger
        self.logger = logger or _get_global_logger()
        
        # 注意：enable_console, enable_file, log_dir, filtered_levels, filtered_categories 
        # 这些参数已废弃，现在由 Logger 统一管理，但保留参数以保持向后兼容
        
        self.show_delta = show_delta
        self.filtered_events = filtered_events or FILTERED_EVENT_TYPES
        
        # 从配置读取设置
        if config:
            self.enable_sequence_diagram = config.get("logging.enable_sequence_diagram", enable_sequence_diagram)
            self.flow_timeout = config.get("logging.sequence_diagram_flow_timeout", 30.0)
        else:
            self.enable_sequence_diagram = enable_sequence_diagram
            self.flow_timeout = 30.0
        
        # 事件时间轴
        self.event_timeline: List[Event] = []
        self.last_event_time: float = 0
        self._lock = threading.Lock()
        
        # sequenceDiagram记录器（按task_id管理）
        self.sequence_recorders: Dict[Optional[int], SequenceDiagramRecorder] = {}
        self.current_flow_task_id: Optional[int] = None
        self.last_flow_event_time: float = 0
        
        # 关键流程的开始和结束事件
        self.flow_start_events = {
            EventType.VAD_SPEECH_START,
            EventType.USER_INTERRUPT,
        }
        self.flow_end_events = {
            EventType.AUDIO_PLAY_END,
            EventType.TASK_CANCELLED,
        }
    
    def get_event_filter(self) -> EventFilter:
        """
        获取事件过滤器（订阅所有事件）
        
        Returns:
            事件过滤器
        """
        # 订阅所有事件，内部处理过滤
        return EventFilter()
    
    def handle_event(self, event: Event):
        """
        处理事件（事件监听器回调函数）
        
        Args:
            event: 事件对象
        """
        # 检查是否需要过滤（事件类型过滤）
        if event.event_type in self.filtered_events:
            return
        
        # 获取日志级别和类别
        log_level = self._get_log_level(event.event_type)
        category = EVENT_CATEGORY_MAP.get(event.event_type, "EVENT")
        
        # 过滤检查由 Logger 统一处理（通过 _should_log 方法）
        # 这里不再重复检查 filtered_levels 和 filtered_categories
        
        with self._lock:
            # 计算与上一事件的时间差
            delta_ms = 0
            if self.last_event_time > 0:
                delta_ms = (event.timestamp - self.last_event_time) * 1000
            
            # 记录到时间轴
            self.event_timeline.append(event)
            self.last_event_time = event.timestamp
            
            # sequenceDiagram记录（如果启用且 Logger 启用了文件输出）
            if self.enable_sequence_diagram and self.logger.log_file_enabled:
                self._handle_sequence_diagram(event)
            
            # 格式化消息
            message = self._format_message(event)
            
            # 添加时间差信息（如果启用）
            if self.show_delta and delta_ms > 0:
                message = f"{message} (+{delta_ms:.0f}ms)"
            
            # 获取缩进级别
            indent = event.data.get("indent", 0)
            
            # 使用 Logger 输出日志
            self.logger.log(log_level, category, message, indent=indent, show_thread=True)
    
    def _format_message(self, event: Event) -> str:
        """
        根据事件类型格式化消息
        
        Args:
            event: 事件对象
            
        Returns:
            格式化的消息字符串
        """
        # 通用日志事件直接使用data中的message
        if event.event_type in (EventType.LOG_INFO, EventType.LOG_WARN, 
                                 EventType.LOG_ERROR, EventType.LOG_DEBUG):
            return event.data.get("message", str(event.data))
        
        # 使用模板格式化
        template = EVENT_MESSAGE_TEMPLATES.get(event.event_type)
        if template:
            try:
                return template.format(**event.data)
            except KeyError:
                # 如果数据不完整，返回原始数据
                return f"{event.event_type.value}: {event.data}"
        
        # 无模板时返回默认格式
        return f"{event.event_type.value}: {event.data}"
    
    def _get_log_level(self, event_type: EventType) -> str:
        """
        根据事件类型确定日志级别
        
        Args:
            event_type: 事件类型
            
        Returns:
            日志级别字符串
        """
        if event_type == EventType.LOG_DEBUG:
            return "DEBUG"
        elif event_type == EventType.LOG_WARN:
            return "WARN"
        elif event_type == EventType.LOG_ERROR:
            return "ERROR"
        else:
            return "INFO"
    
    def get_timeline(self) -> List[Event]:
        """
        获取事件时间轴
        
        Returns:
            事件列表
        """
        with self._lock:
            return self.event_timeline.copy()
    
    def clear_timeline(self):
        """清空事件时间轴"""
        with self._lock:
            self.event_timeline.clear()
            self.last_event_time = 0
    
    def _handle_sequence_diagram(self, event: Event):
        """
        处理sequenceDiagram记录
        
        Args:
            event: 事件对象
        """
        # 获取任务ID
        task_id = event.data.get("task_id")
        
        # 检测流程开始事件
        if event.event_type in self.flow_start_events:
            # 创建新的记录器
            flow_name = self._get_flow_name(event.event_type)
            recorder = SequenceDiagramRecorder(flow_name=flow_name)
            recorder.start_flow(task_id=task_id)
            self.sequence_recorders[task_id] = recorder
            self.current_flow_task_id = task_id
            self.last_flow_event_time = event.timestamp
        
        # 检测流程结束事件
        if event.event_type in self.flow_end_events:
            # 结束当前流程
            if task_id in self.sequence_recorders:
                recorder = self.sequence_recorders[task_id]
                # 添加结束事件
                recorder.add_event(
                    event_type=event.event_type.value,
                    timestamp=event.timestamp,
                    data=event.data
                )
                # 生成并输出sequenceDiagram
                self._output_sequence_diagram(recorder, task_id)
                # 清理记录器
                del self.sequence_recorders[task_id]
                if self.current_flow_task_id == task_id:
                    self.current_flow_task_id = None
        
        # 检查流程超时
        if self.current_flow_task_id is not None:
            time_since_last_event = event.timestamp - self.last_flow_event_time
            if time_since_last_event > self.flow_timeout:
                # 流程超时，结束记录
                recorder = self.sequence_recorders.get(self.current_flow_task_id)
                if recorder and not recorder.is_empty():
                    recorder.add_note("Manager", "流程超时，自动结束")
                    self._output_sequence_diagram(recorder, self.current_flow_task_id)
                if self.current_flow_task_id in self.sequence_recorders:
                    del self.sequence_recorders[self.current_flow_task_id]
                self.current_flow_task_id = None
        
        # 记录事件到当前流程的sequenceDiagram
        if self.current_flow_task_id is not None:
            recorder = self.sequence_recorders.get(self.current_flow_task_id)
            if recorder:
                # 判断是否是关键事件（需要记录到sequenceDiagram）
                if self._is_key_event(event.event_type):
                    recorder.add_event(
                        event_type=event.event_type.value,
                        timestamp=event.timestamp,
                        data=event.data
                    )
                    # 添加特殊注释（如早期提交）
                    if event.event_type == EventType.ASR_STABLE_RESULT:
                        recorder.add_note("Manager", "检测到VAD已结束 + 结果稳定")
                    elif event.event_type == EventType.ASR_FINAL_RESULT:
                        # 检查是否已通过早期提交处理
                        if recorder.task_id == task_id:
                            recorder.add_note("Manager", "已处理，跳过")
                self.last_flow_event_time = event.timestamp
    
    def _is_key_event(self, event_type: EventType) -> bool:
        """
        判断是否是关键事件（需要记录到sequenceDiagram）
        
        Args:
            event_type: 事件类型
            
        Returns:
            True表示是关键事件
        """
        key_events = {
            EventType.VAD_SPEECH_START,
            EventType.VAD_SPEECH_END,
            EventType.ASR_RECOGNITION_START,
            EventType.ASR_STABLE_RESULT,
            EventType.ASR_FINAL_RESULT,
            EventType.ASR_RECOGNITION_END,
            EventType.LLM_GENERATION_START,
            EventType.LLM_GENERATION_COMPLETE,
            EventType.TTS_GENERATION_START,
            EventType.TTS_GENERATION_COMPLETE,
            EventType.AUDIO_PLAY_START,
            EventType.AUDIO_PLAY_END,
            EventType.USER_INTERRUPT,
            EventType.TASK_CREATED,
            EventType.TASK_CANCELLED,
        }
        return event_type in key_events
    
    def _get_flow_name(self, event_type: EventType) -> str:
        """
        根据开始事件类型获取流程名称
        
        Args:
            event_type: 开始事件类型
            
        Returns:
            流程名称
        """
        if event_type == EventType.VAD_SPEECH_START:
            return "正常对话流程"
        elif event_type == EventType.USER_INTERRUPT:
            return "打断流程"
        else:
            return "关键流程"
    
    def _output_sequence_diagram(self, recorder: SequenceDiagramRecorder, task_id: Optional[int]):
        """
        输出sequenceDiagram到日志文件
        
        Args:
            recorder: 序列图记录器
            task_id: 任务ID
        """
        if recorder.is_empty():
            return
        
        diagram_text = recorder.generate_diagram()
        if not diagram_text:
            return
        
        # 生成时间戳
        dt = datetime.fromtimestamp(time.time())
        timestamp = dt.strftime("%H:%M:%S.%f")[:-3]
        
        # 使用 Logger 写入到文件
        task_info = f"（任务ID: {task_id}）" if task_id is not None else ""
        duration = recorder.get_flow_duration()
        duration_str = f" [耗时: {duration:.3f}秒]" if duration else ""
        
        sequence_log = f"\n[{timestamp}] [SEQUENCE] {recorder.flow_name}{task_info}{duration_str} 序列图:\n"
        sequence_log += "```mermaid\n"
        sequence_log += diagram_text
        sequence_log += "\n```\n\n"
        
        self.logger.write_to_file(sequence_log)
    
    def close(self):
        """关闭事件日志处理器"""
        # 输出所有未完成的sequenceDiagram
        if self.enable_sequence_diagram and self.logger.log_file_enabled:
            with self._lock:
                for task_id, recorder in list(self.sequence_recorders.items()):
                    if not recorder.is_empty():
                        recorder.add_note("Manager", "系统关闭，流程未完成")
                        self._output_sequence_diagram(recorder, task_id)
                self.sequence_recorders.clear()
        
        # 注意：Logger 的文件句柄由 Logger 自己管理，这里不需要关闭


# 便捷函数：通过事件系统发送日志
def create_log_event(level: str, category: str, message: str, 
                     indent: int = 0, source: Optional[str] = None) -> Event:
    """
    创建日志事件
    
    Args:
        level: 日志级别（INFO, WARN, ERROR, DEBUG）
        category: 日志类别
        message: 日志消息
        indent: 缩进级别
        source: 事件来源
        
    Returns:
        事件对象
    """
    from voice_chatbot.dialogue.events import create_event
    
    level_upper = level.upper()
    if level_upper == "DEBUG":
        event_type = EventType.LOG_DEBUG
    elif level_upper == "WARN":
        event_type = EventType.LOG_WARN
    elif level_upper == "ERROR":
        event_type = EventType.LOG_ERROR
    else:
        event_type = EventType.LOG_INFO
    
    return create_event(
        event_type=event_type,
        data={
            "message": message,
            "category": category,
            "indent": indent,
        },
        source=source
    )

