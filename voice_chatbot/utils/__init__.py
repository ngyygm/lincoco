"""
辅助工具模块 (utils)

提供日志系统、时间记录、配置管理、性能监控、音频播放、麦克风采集等功能
"""

from .logger import Logger, Colors, get_timestamp, get_thread_id, get_thread_name, log_info, log_warn, log_error, log_debug, log_with_timestamp, init_logger, update_logger_filter, update_logger_category_colors
from .timer import PerformanceTimer, Timer
from .config import Config
from .audio_player import AudioPlayer, PlaybackStatus, InterruptReason
from .microphone import MicrophoneRecorder

# 注意：EventLogger 和 PerformanceMonitor 不在这里导入，避免循环导入
# 需要时直接从 voice_chatbot.utils.event_logger 和 voice_chatbot.utils.performance_monitor 导入

__all__ = [
    # Logger
    'Logger',
    'Colors',
    'get_timestamp',
    'get_thread_id',
    'get_thread_name',
    'log_info',
    'log_warn',
    'log_error',
    'log_debug',
    'log_with_timestamp',
    'init_logger',
    'update_logger_filter',
    'update_logger_category_colors',
    # Timer
    'PerformanceTimer',
    'Timer',
    # Config
    'Config',
    # Audio
    'AudioPlayer',
    'PlaybackStatus',
    'InterruptReason',
    'MicrophoneRecorder',
]

