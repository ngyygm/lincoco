"""
日志系统模块

提供标准化的日志输出功能，支持控制台和文件输出
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py 第41-100行
"""

import os
import sys
import threading
import inspect
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re


# 日志级别常量
LOG_LEVELS = {
    'DEBUG': 0,
    'INFO': 1,
    'WARN': 2,
    'ERROR': 3,
}

# ANSI 颜色代码常量
class Colors:
    """ANSI 颜色代码"""
    # 基础颜色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # 亮色
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    # 重置颜色
    RESET = '\033[0m'
    
    # 样式
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'


# ANSI 支持标志
_ansi_enabled = False

# 在 Windows 上启用 ANSI 转义序列支持
def _enable_windows_ansi():
    """在 Windows 上启用 ANSI 转义序列支持"""
    global _ansi_enabled
    
    if sys.platform == 'win32':
        try:
            import ctypes
            
            # Windows 控制台句柄常量
            STD_OUTPUT_HANDLE = -11
            STD_ERROR_HANDLE = -12
            
            # 控制台模式标志
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            
            kernel32 = ctypes.windll.kernel32
            
            # 获取标准输出句柄
            stdout_handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            stderr_handle = kernel32.GetStdHandle(STD_ERROR_HANDLE)
            
            # 获取当前控制台模式
            def get_console_mode(handle):
                if handle is None or handle == -1:
                    return 0
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    return mode.value
                return 0
            
            # 设置控制台模式，启用虚拟终端处理（ANSI 转义序列）
            def set_console_mode(handle, mode):
                if handle is None or handle == -1:
                    return False
                return kernel32.SetConsoleMode(handle, mode) != 0
            
            # 为 stdout 启用 ANSI（只有在有效句柄时）
            if stdout_handle and stdout_handle != -1:
                stdout_mode = get_console_mode(stdout_handle)
                if stdout_mode:
                    new_stdout_mode = stdout_mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                    if set_console_mode(stdout_handle, new_stdout_mode):
                        _ansi_enabled = True
            
            # 为 stderr 启用 ANSI（只有在有效句柄时）
            if stderr_handle and stderr_handle != -1:
                stderr_mode = get_console_mode(stderr_handle)
                if stderr_mode:
                    new_stderr_mode = stderr_mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                    set_console_mode(stderr_handle, new_stderr_mode)
                
        except Exception:
            # 如果失败，静默忽略（可能不支持或已启用）
            pass
    else:
        # 非 Windows 系统通常默认支持 ANSI
        _ansi_enabled = True


# 初始化时启用 Windows ANSI 支持
_enable_windows_ansi()


def _strip_ansi_codes(text):
    """去除文本中的 ANSI 颜色代码"""
    ansi_escape = re.compile(r'\033\[[0-9;]*m')
    return ansi_escape.sub('', text)


# ============================================================================
# 控制台统一出口与流式通道
# ============================================================================
# 所有控制台输出（普通日志、流式行、inline 行）共用一把模块级锁，
# 避免多线程 print 交错导致半行断裂、"一个字换一行"的混乱输出。
_console_lock = threading.RLock()

# 当前占用控制台"未闭合行"的流式通道（None 表示当前行已闭合）。
# 普通日志输出前必须先闭合流式行；流式通道再次更新时若无未闭合行则另起一行。
_active_stream_channel = None

# 旧 inline=True 日志映射到的通道名（兼容保留，见 Logger.log）
_INLINE_CHANNEL = "__inline__"

# 级别标签配色（只染 [LEVEL] 标签，不整行染色；INFO 用默认色保持清爽）
LEVEL_TAG_COLORS = {
    'INFO': None,
    'DEBUG': Colors.BRIGHT_BLACK,
    'WARN': Colors.YELLOW,
    'ERROR': Colors.RED + Colors.BOLD,
}

# 类别默认配色表（config 的 logging.category_colors 可覆盖同名项并追加新类别）
DEFAULT_CATEGORY_COLORS = {
    'ASR': 'GREEN',
    'TTS': 'BLUE',
    'LLM': 'YELLOW',
    'PLAY': 'MAGENTA',
    'VAD': 'CYAN',
    'CTX': 'BRIGHT_MAGENTA',
    'THREAD': 'BRIGHT_CYAN',
    'PUN': 'YELLOW+BOLD',
    'PERSONA': 'MAGENTA+BOLD',
    'SPE': 'CYAN+BOLD',
    'EVENT': 'BRIGHT_CYAN+BOLD',
    'TIME': 'BRIGHT_GREEN',
    'TASK': 'BRIGHT_YELLOW',
    'WS': 'BRIGHT_BLUE',
    'NET': 'BRIGHT_BLUE',
    'MAIN': 'BRIGHT_WHITE',
}


def _ansi(code):
    """返回可用的 ANSI 颜色代码（不支持 ANSI 的控制台返回空串）"""
    return code if (_ansi_enabled or sys.platform != 'win32') else ''


def _close_active_stream_line():
    """闭合控制台上未结束的流式行（必须在持有 _console_lock 时调用）"""
    global _active_stream_channel
    if _active_stream_channel is not None:
        print()  # 换行收尾
        _active_stream_channel = None


def _parse_color_name(color_name):
    """
    解析颜色名称字符串，返回对应的 ANSI 颜色代码
    
    Args:
        color_name: 颜色名称字符串，如 "GREEN", "RED+BOLD", "CYAN+UNDERLINE" 等
        支持的颜色：BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE,
                   BRIGHT_BLACK, BRIGHT_RED, BRIGHT_GREEN, BRIGHT_YELLOW, 
                   BRIGHT_BLUE, BRIGHT_MAGENTA, BRIGHT_CYAN, BRIGHT_WHITE
        支持的样式：BOLD, DIM, UNDERLINE
        可以组合：如 "GREEN+BOLD", "RED+UNDERLINE" 等
    
    Returns:
        ANSI 颜色代码字符串，如果解析失败返回 None
    """
    if not color_name:
        return None
    
    # 颜色名称到 Colors 属性的映射
    color_map = {
        'BLACK': Colors.BLACK,
        'RED': Colors.RED,
        'GREEN': Colors.GREEN,
        'YELLOW': Colors.YELLOW,
        'BLUE': Colors.BLUE,
        'MAGENTA': Colors.MAGENTA,
        'CYAN': Colors.CYAN,
        'WHITE': Colors.WHITE,
        'BRIGHT_BLACK': Colors.BRIGHT_BLACK,
        'BRIGHT_RED': Colors.BRIGHT_RED,
        'BRIGHT_GREEN': Colors.BRIGHT_GREEN,
        'BRIGHT_YELLOW': Colors.BRIGHT_YELLOW,
        'BRIGHT_BLUE': Colors.BRIGHT_BLUE,
        'BRIGHT_MAGENTA': Colors.BRIGHT_MAGENTA,
        'BRIGHT_CYAN': Colors.BRIGHT_CYAN,
        'BRIGHT_WHITE': Colors.BRIGHT_WHITE,
    }
    
    # 样式映射
    style_map = {
        'BOLD': Colors.BOLD,
        'DIM': Colors.DIM,
        'UNDERLINE': Colors.UNDERLINE,
    }
    
    # 分割颜色和样式（支持 + 号组合）
    parts = [p.strip().upper() for p in color_name.split('+')]
    
    color_code = None
    style_codes = []
    
    for part in parts:
        if part in color_map:
            color_code = color_map[part]
        elif part in style_map:
            style_codes.append(style_map[part])
    
    # 如果找到了颜色，组合颜色和样式
    if color_code:
        return color_code + ''.join(style_codes)
    
    return None


def get_timestamp():
    """获取格式化的时间戳字符串：HH:MM:SS.mmm"""
    now = datetime.now()
    return now.strftime("%H:%M:%S.%f")[:-3]  # 保留毫秒（3位）


def get_thread_id():
    """获取当前线程ID"""
    return threading.current_thread().ident


def get_thread_name():
    """获取当前线程名称（去掉默认后缀的函数信息，保持简短）"""
    name = threading.current_thread().name
    if not name or name == "MainThread":
        return "Main"
    # 默认线程名形如 "Thread-1 (spam)"，去掉括号部分避免截断后残留
    if "(" in name:
        name = name.split("(", 1)[0].strip()
    return name[:12] or "T"


def get_caller_source():
    """
    获取调用者的源代码文件名（不含路径和扩展名）
    自动跳过logger.py内部的调用栈，找到实际的调用者
    
    Returns:
        文件名（不含路径和扩展名），如 "manager"、"fun_asr_stream"，如果无法获取则返回 None
    """
    try:
        # 获取调用栈
        frame = inspect.currentframe()
        # 跳过当前函数（get_caller_source本身）
        if frame is None:
            return None
        frame = frame.f_back
        
        # 跳过logger.py内部的调用栈，找到实际的调用者
        logger_file = Path(__file__).name  # logger.py
        while frame is not None:
            frame = frame.f_back
            if frame is None:
                break
            
            filename = frame.f_code.co_filename
            if not filename:
                continue
            
            # 检查是否是logger.py文件
            caller_file = Path(filename).name
            if caller_file != logger_file:
                # 找到了非logger.py的调用者
                # 提取文件名（不含路径和扩展名）
                filepath = Path(filename)
                return filepath.stem  # 获取不含扩展名的文件名
        
        return None
        
    except Exception:
        # 如果获取失败，返回None
        return None


class Logger:
    """日志系统类"""
    
    def __init__(self, log_file_enabled=True, log_dir='logs', 
                 filtered_levels=None, filtered_categories=None, category_colors=None,
                 show_source=True):
        """
        初始化日志系统
        
        Args:
            log_file_enabled: 是否启用文件输出
            log_dir: 日志文件目录
            filtered_levels: 允许输出的日志级别列表（白名单），如 ["INFO", "DEBUG"]
            filtered_categories: 允许输出的日志类别列表（白名单），如 ["ASR", "TTS", "LLM"]
            category_colors: 日志类别专属颜色字典，格式如 {"ASR": "GREEN", "LLM": "CYAN"}
            show_source: 是否显示代码来源前缀（文件名），默认为True
        """
        self.log_file_enabled = log_file_enabled
        self.log_dir = Path(log_dir)
        self.current_log_file = None
        self.log_file_handle = None
        self._lock = threading.Lock()
        self.show_source = show_source
        
        # 过滤配置（白名单）
        self.filtered_levels = set(level.upper() for level in (filtered_levels or []))
        self.filtered_categories = set(category.upper() for category in (filtered_categories or []))
        
        # 类别专属颜色配置：内置默认表打底，config 传入的同名项覆盖、新类别追加
        self.category_colors = {}
        for category, color_name in DEFAULT_CATEGORY_COLORS.items():
            color_code = _parse_color_name(color_name)
            if color_code:
                self.category_colors[category.upper()] = color_code
        if category_colors:
            for category, color_name in category_colors.items():
                color_code = _parse_color_name(color_name)
                if color_code:
                    self.category_colors[category.upper()] = color_code
        
        # 创建日志目录
        if self.log_file_enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._update_log_file()
    
    def _update_log_file(self):
        """更新日志文件（按日期分割）"""
        if not self.log_file_enabled:
            return
        
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"voice_chatbot_{today}.log"
        
        # 如果日期变化，关闭旧文件，打开新文件
        if log_file != self.current_log_file:
            if self.log_file_handle:
                self.log_file_handle.close()
            
            self.current_log_file = log_file
            # 追加模式打开文件
            self.log_file_handle = open(log_file, 'a', encoding='utf-8')
    
    def set_level(self, level):
        """设置日志级别（已废弃，使用 filtered_levels 控制输出）"""
        # 此方法已废弃，不再使用 log_level 阈值过滤
        pass
    
    def update_filter(self, filtered_levels=None, filtered_categories=None):
        """
        更新过滤配置
        
        Args:
            filtered_levels: 允许输出的日志级别列表（白名单），如 ["INFO", "DEBUG"]
            filtered_categories: 允许输出的日志类别列表（白名单），如 ["ASR", "TTS", "LLM"]
        """
        if filtered_levels is not None:
            self.filtered_levels = set(level.upper() for level in filtered_levels)
        if filtered_categories is not None:
            self.filtered_categories = set(category.upper() for category in filtered_categories)
    
    def _should_log(self, level, category=None):
        """
        判断是否应该输出日志
        
        Args:
            level: 日志级别
            category: 日志类别（可选）
            
        Returns:
            True表示应该输出，False表示被过滤
        """
        level_upper = level.upper()
        
        # 检查级别过滤（白名单：只输出在 filtered_levels 中的级别）
        # 如果 filtered_levels 不为空，则只有在该列表中的级别才输出
        if self.filtered_levels and level_upper not in self.filtered_levels:
            return False
        
        # 检查类别过滤（白名单：只输出在 filtered_categories 中的类别）
        # 如果 filtered_categories 不为空，则只有在该列表中的类别才输出
        if category and self.filtered_categories and category.upper() not in self.filtered_categories:
            return False
        
        # 完全由 filtered_levels 和 filtered_categories 控制输出
        return True
    
    def log(self, level, category, message, indent=0, show_thread=True, inline=False, color=None, source=None):
        """
        标准化日志输出

        Args:
            level: 日志级别 (INFO, WARN, ERROR, DEBUG)
            category: 日志类别 (ASR, TTS, LLM, PLAY, VAD, TASK等)
            message: 日志消息
            indent: 缩进级别 (0-3)
            show_thread: 是否显示线程信息
            inline: 是否在同一行更新（使用\r，用于流式输出）
            color: 颜色代码（可选），如果提供则使用该颜色，否则使用默认颜色
            source: 代码来源（文件名，不含路径和扩展名），如果为None则自动获取
        """
        if not self._should_log(level, category):
            return

        timestamp = get_timestamp()
        thread_info = ""
        if show_thread:
            thread_id = get_thread_id()
            thread_name = get_thread_name()
            thread_info = f"[T:{thread_name}|{thread_id%10000:04d}]"

        indent_str = "  " * indent
        level_str = level.ljust(5)
        category_str = category.ljust(6)

        # 获取代码来源（如果启用且未提供）
        source_str = ""
        if self.show_source:
            if source is None:
                source = get_caller_source()  # 自动跳过logger.py内部的调用栈
            if source:
                source_str = f"[{source}] "

        log_message = f"[{timestamp}]{thread_info} [{level_str}] [{category_str}] {source_str}{indent_str}{message}"

        # 配色策略：只染标签，不整行染色——
        #   时间戳/线程/来源 用暗色（BRIGHT_BLACK）降低视觉噪音
        #   [LEVEL] 标签按级别配色（INFO 默认色保持清爽）
        #   [CATEGORY] 标签按类别配色（color 参数优先，其次类别专属色）
        #   正文使用终端默认色
        reset = _ansi(Colors.RESET)
        dim = _ansi(Colors.BRIGHT_BLACK)
        level_upper = level.upper()
        level_color = _ansi(LEVEL_TAG_COLORS.get(level_upper) or '')

        category_color = ''
        if color is not None:
            category_color = color
        elif category:
            category_color = self.category_colors.get(category.upper(), '')

        head_seg = f"{dim}[{timestamp}]{thread_info}{reset}"
        level_seg = f"{_ansi(level_color)}[{level_str}]{reset}"
        cat_seg = f"{_ansi(category_color)}[{category_str}]{reset}"
        colored_message = f"{head_seg} {level_seg} {cat_seg} "
        if source_str:
            colored_message += f"{dim}{source_str}{reset}"
        colored_message += f"{indent_str}{message}"

        # 输出到控制台（统一走 _console_lock，与流式行互斥，避免交错断行）
        # 确保 ANSI 支持已启用（特别是在 Windows 上）
        if sys.platform == 'win32' and not _ansi_enabled:
            _enable_windows_ansi()

        global _active_stream_channel
        with _console_lock:
            if inline:
                # 同一行流式更新（使用\r，不换行）：兼容旧 inline 语义，
                # 映射到专用通道，普通日志输出前会自动闭合该行
                if _active_stream_channel != _INLINE_CHANNEL:
                    _close_active_stream_line()
                    _active_stream_channel = _INLINE_CHANNEL
                print(f"\r{colored_message}", end='', flush=True)
            else:
                # 普通日志：先闭合未结束的流式行，再输出完整行
                _close_active_stream_line()
                print(colored_message, flush=True)

        # 输出到文件（流式 inline 更新不写文件，避免刷屏；最终文本走普通日志落盘）
        if self.log_file_enabled and not inline:
            with self._lock:
                self._update_log_file()  # 检查是否需要切换日志文件
                if self.log_file_handle:
                    # 文件输出时去除ANSI颜色代码（如果有）
                    clean_message = _strip_ansi_codes(log_message)
                    self.log_file_handle.write(clean_message + '\n')
                    self.log_file_handle.flush()

        # 广播到 Web 端（如果 WebSocket 服务器已设置）
        # 注意：inline 日志也会广播到 Web 端，Web 端将流式消息渲染到固定状态条而非日志流
        with _websocket_server_lock:
            if _websocket_server:
                try:
                    _websocket_server.broadcast_log(
                        level=level,
                        category=category,
                        message=message,
                        timestamp=timestamp,
                        source=source,
                        inline=inline,  # 传递 inline 标记（旧流式消息兼容）
                        indent=indent   # 缩进级别，Web 端按层级渲染
                    )
                except Exception:
                    # 避免日志广播失败影响主流程
                    pass
    
    def info(self, category, message, indent=0, show_thread=True, inline=False, color=None, source=None):
        """信息日志"""
        self.log("INFO", category, message, indent, show_thread, inline, color, source)
    
    def warn(self, category, message, indent=0, show_thread=True, color=None, source=None):
        """警告日志"""
        self.log("WARN", category, message, indent, show_thread, False, color, source)
    
    def error(self, category, message, indent=0, show_thread=True, color=None, source=None):
        """错误日志"""
        self.log("ERROR", category, message, indent, show_thread, False, color, source)
    
    def debug(self, category, message, indent=0, show_thread=True, color=None, source=None):
        """调试日志"""
        self.log("DEBUG", category, message, indent, show_thread, False, color, source)
    
    def write_to_file(self, message: str):
        """
        直接写入消息到日志文件（用于特殊格式的日志，如 sequenceDiagram）
        
        Args:
            message: 要写入的消息
        """
        if self.log_file_enabled:
            with self._lock:
                self._update_log_file()
                if self.log_file_handle:
                    self.log_file_handle.write(message)
                    self.log_file_handle.flush()
    
    def close(self):
        """关闭日志文件"""
        if self.log_file_handle:
            self.log_file_handle.close()
            self.log_file_handle = None


# 全局日志实例（延迟初始化）
_global_logger = None
_logger_lock = threading.Lock()

# WebSocket 服务器实例（用于广播日志到 Web 端）
_websocket_server = None
_websocket_server_lock = threading.Lock()


def init_logger(log_file_enabled=True, log_dir='logs',
                filtered_levels=None, filtered_categories=None, config=None):
    """
    初始化全局日志实例（使用配置）
    
    Args:
        log_file_enabled: 是否启用文件输出
        log_dir: 日志文件目录
        filtered_levels: 允许输出的日志级别列表（白名单），如 ["INFO", "DEBUG"]
        filtered_categories: 允许输出的日志类别列表（白名单），如 ["ASR", "TTS", "LLM"]
        config: 配置对象（可选），如果提供则从中读取过滤参数和类别颜色配置
    """
    global _global_logger
    
    category_colors = None
    show_source = True
    
    # 如果提供了 config，从配置读取过滤参数和类别颜色
    if config:
        if filtered_levels is None:
            filtered_levels = config.get("logging.filtered_levels", [])
        if filtered_categories is None:
            filtered_categories = config.get("logging.filtered_categories", [])
        # 读取类别颜色配置
        category_colors = config.get("logging.category_colors", None)
        # 读取是否显示来源配置
        show_source = config.get("logging.show_source", True)
    
    with _logger_lock:
        if _global_logger is None:
            _global_logger = Logger(
                log_file_enabled=log_file_enabled, 
                log_dir=log_dir,
                filtered_levels=filtered_levels,
                filtered_categories=filtered_categories,
                category_colors=category_colors,
                show_source=show_source
            )
        else:
            # 如果 Logger 已存在，更新过滤配置和类别颜色
            _global_logger.update_filter(filtered_levels, filtered_categories)
            if category_colors:
                # 更新类别颜色
                for category, color_name in category_colors.items():
                    color_code = _parse_color_name(color_name)
                    if color_code:
                        _global_logger.category_colors[category.upper()] = color_code
            # 更新是否显示来源
            _global_logger.show_source = show_source


def update_logger_filter(config):
    """
    更新全局日志实例的过滤配置（从配置对象读取）
    
    Args:
        config: 配置对象（Config实例）
    """
    global _global_logger
    if _global_logger is None:
        return
    
    filtered_levels = config.get("logging.filtered_levels", [])
    filtered_categories = config.get("logging.filtered_categories", [])
    _global_logger.update_filter(filtered_levels, filtered_categories)


def update_logger_category_colors(config):
    """
    更新全局日志实例的类别颜色配置（从配置对象读取）
    
    Args:
        config: 配置对象（Config实例）
    """
    global _global_logger
    if _global_logger is None:
        return
    
    category_colors = config.get("logging.category_colors", None)
    if category_colors:
        # 更新类别颜色
        for category, color_name in category_colors.items():
            color_code = _parse_color_name(color_name)
            if color_code:
                _global_logger.category_colors[category.upper()] = color_code


def _get_global_logger():
    """获取全局日志实例（单例模式）"""
    global _global_logger
    if _global_logger is None:
        with _logger_lock:
            if _global_logger is None:
                _global_logger = Logger()
    return _global_logger


def log_info(category, message, indent=0, show_thread=True, inline=False, color=None, source=None):
    """信息日志（全局函数）"""
    _get_global_logger().info(category, message, indent, show_thread, inline, color, source)


def log_warn(category, message, indent=0, show_thread=True, color=None, source=None):
    """警告日志（全局函数）"""
    _get_global_logger().warn(category, message, indent, show_thread, color, source)


def log_error(category, message, indent=0, show_thread=True, color=None, source=None):
    """错误日志（全局函数）"""
    _get_global_logger().error(category, message, indent, show_thread, color, source)


def log_debug(category, message, indent=0, show_thread=True, color=None, source=None):
    """调试日志（全局函数）"""
    _get_global_logger().debug(category, message, indent, show_thread, color, source)


# ============================================================================
# 流式通道 API（显式的"开始更新/结束"语义，替代 inline=True 的裸 \r 刷新）
#
# 用法：
#   log_stream('asr_partial', '今天天气', label='识别中', category='ASR')
#   log_stream_end('asr_partial', '今天天气怎么样', label='识别中', category='ASR')
#
# 控制台：通道独占一行用 \r 原地刷新；期间插入的普通日志会先闭合该行，
#         通道下次更新时另起一行（有锁保证，不会出现半行交错）。
# Web 端：更新消息刷新固定状态徽标（不进入日志流）；end 消息移除徽标，
#         并将最终文本作为一条普通日志落定（不被后续日志冲掉）。
# ============================================================================

def _broadcast_stream(channel, action, text, label, category, timestamp, indent):
    """广播流式通道消息到 Web 端"""
    with _websocket_server_lock:
        if _websocket_server:
            try:
                _websocket_server.broadcast_log(
                    level="INFO",
                    category=category or "",
                    message=text,
                    timestamp=timestamp,
                    source=None,
                    inline=(action == "update"),  # 旧 Web 端兼容标记
                    indent=indent,
                    stream=channel,
                    stream_action=action,
                    stream_label=label,
                )
            except Exception:
                pass


def _render_stream_line(label, text, indent, category_color):
    """构建流式行的着色文本（轻量格式：[时间] ▸ 标签 正文）"""
    reset = _ansi(Colors.RESET)
    dim = _ansi(Colors.BRIGHT_BLACK)
    timestamp = get_timestamp()
    return (
        f"{dim}[{timestamp}]{reset} "
        f"{_ansi(category_color)}{label} ▸{reset} "
        f"{'  ' * indent}{text}"
    )


@contextmanager
def console_exclusive():
    """
    独占控制台输出（用于整块格式化输出，如性能分析报告）

    持有控制台锁并先闭合活动流式行，保证整块输出不与日志/流式刷新交错
    """
    with _console_lock:
        _close_active_stream_line()
        yield


def log_stream(channel, text, label=None, category=None, indent=1):
    """
    流式输出更新

    Args:
        channel: 通道标识（同一通道原地刷新，如 'asr_partial' / 'play_text'）
        text: 当前流式文本（增量全量均可，Web 端直接替换显示）
        label: 显示标签（如 '识别中'），默认使用 channel
        category: 日志类别（决定标签颜色与过滤）
        indent: 缩进级别
    """
    logger = _get_global_logger()
    label = label or channel
    category_upper = (category or "").upper()
    category_color = logger.category_colors.get(category_upper, '')

    # 控制台：类别被过滤则不渲染（Web 端仍可收到广播）
    if logger._should_log("INFO", category or ""):
        global _active_stream_channel
        with _console_lock:
            line = _render_stream_line(label, text, indent, category_color)
            if _active_stream_channel != channel:
                # 通道没有未闭合行（首次更新或被其他日志顶掉）：先闭合当前行再另起
                _close_active_stream_line()
                _active_stream_channel = channel
            print(f"\r{line}", end='', flush=True)

    _broadcast_stream(channel, "update", text, label, category, get_timestamp(), indent)


def log_stream_end(channel, final_text=None, label=None, category=None, indent=1):
    """
    结束流式通道

    Args:
        final_text: 最终完整文本（控制台闭合前刷新为该文本；Web 端落定为日志行）
    """
    global _active_stream_channel
    logger = _get_global_logger()
    label = label or channel
    category_upper = (category or "").upper()
    category_color = logger.category_colors.get(category_upper, '')

    with _console_lock:
        if _active_stream_channel == channel:
            # 通道持有未闭合行：刷新为最终文本后换行收尾
            if final_text:
                line = _render_stream_line(label, final_text, indent, category_color)
                print(f"\r{line}", end='', flush=True)
            print()
            _active_stream_channel = None
        elif final_text and logger._should_log("INFO", category or ""):
            # 通道行已被中间日志顶掉（或从未开始）：以完整日志行输出最终结果
            line = _render_stream_line(label, final_text, indent, category_color)
            print(line, flush=True)

    # 最终文本落盘（流式中间更新不写文件，只写最终结果）
    if final_text and logger.log_file_enabled:
        with logger._lock:
            logger._update_log_file()
            if logger.log_file_handle:
                clean = f"[{get_timestamp()}] [{(category or '').ljust(6)}] {'  ' * indent}{label}: {final_text}"
                logger.log_file_handle.write(_strip_ansi_codes(clean) + '\n')
                logger.log_file_handle.flush()

    # end 消息始终广播（即使控制台被过滤），确保 Web 端状态徽标被移除；
    # 最终文本随 end 消息携带，Web 端据此落定为一条暗色收尾日志行
    _broadcast_stream(channel, "end", final_text or "", label, category, get_timestamp(), indent)


def log_with_timestamp(message):
    """兼容旧代码的日志函数（不推荐使用）"""
    return f"[{get_timestamp()}] {message}"


def set_websocket_server(websocket_server):
    """
    设置 WebSocket 服务器实例，用于广播日志到 Web 端

    Args:
        websocket_server: WebSocketServer 实例
    """
    global _websocket_server
    with _websocket_server_lock:
        _websocket_server = websocket_server


def get_websocket_server():
    """获取当前的 WebSocket 服务器实例"""
    with _websocket_server_lock:
        return _websocket_server

