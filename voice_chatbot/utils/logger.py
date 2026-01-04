"""
日志系统模块

提供标准化的日志输出功能，支持控制台和文件输出
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py 第41-100行
"""

import os
import sys
import threading
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
    """获取当前线程名称"""
    name = threading.current_thread().name
    if not name or name == "MainThread":
        return "Main"
    return name[:10]  # 限制长度


class Logger:
    """日志系统类"""
    
    def __init__(self, log_level='INFO', log_file_enabled=True, log_dir='logs', 
                 filtered_levels=None, filtered_categories=None, category_colors=None):
        """
        初始化日志系统
        
        Args:
            log_level: 日志级别 (DEBUG, INFO, WARN, ERROR)
            log_file_enabled: 是否启用文件输出
            log_dir: 日志文件目录
            filtered_levels: 被过滤的日志级别列表（黑名单），如 ["DEBUG"]
            filtered_categories: 被过滤的日志类别列表（黑名单），如 ["EVENT", "VAD"]
            category_colors: 日志类别专属颜色字典，格式如 {"ASR": "GREEN", "LLM": "CYAN"}
        """
        self.log_level = log_level.upper()
        self.log_level_num = LOG_LEVELS.get(self.log_level, LOG_LEVELS['INFO'])
        self.log_file_enabled = log_file_enabled
        self.log_dir = Path(log_dir)
        self.current_log_file = None
        self.log_file_handle = None
        self._lock = threading.Lock()
        
        # 过滤配置（黑名单）
        self.filtered_levels = set(level.upper() for level in (filtered_levels or []))
        self.filtered_categories = set(category.upper() for category in (filtered_categories or []))
        
        # 类别专属颜色配置（将颜色名称转换为 ANSI 代码）
        self.category_colors = {}
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
        """设置日志级别"""
        level_upper = level.upper()
        if level_upper in LOG_LEVELS:
            self.log_level = level_upper
            self.log_level_num = LOG_LEVELS[level_upper]
        else:
            self.warn("LOGGER", f"无效的日志级别: {level}，保持当前级别: {self.log_level}")
    
    def update_filter(self, filtered_levels=None, filtered_categories=None):
        """
        更新过滤配置
        
        Args:
            filtered_levels: 被过滤的日志级别列表（黑名单），如 ["DEBUG"]
            filtered_categories: 被过滤的日志类别列表（黑名单），如 ["EVENT", "VAD"]
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
        
        # 检查类别过滤（黑名单：在 filtered_categories 中的类别不输出）
        if category and category.upper() in self.filtered_categories:
            return False
        
        # 检查级别阈值（只输出指定级别及以上的日志）
        level_num = LOG_LEVELS.get(level_upper, LOG_LEVELS['INFO'])
        return level_num >= self.log_level_num
    
    def log(self, level, category, message, indent=0, show_thread=True, inline=False, color=None):
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
        
        log_message = f"[{timestamp}]{thread_info} [{level_str}] [{category_str}] {indent_str}{message}"
        
        # 确定使用的颜色：优先使用传入的 color 参数，否则使用 category 专属颜色，最后使用默认颜色
        final_color = None
        if color is not None:
            # 如果传入了 color 参数，使用传入的颜色
            final_color = color
        elif category:
            # 检查 category 是否有专属颜色
            category_upper = category.upper()
            if category_upper in self.category_colors:
                final_color = self.category_colors[category_upper]
        
        # 应用颜色代码
        if final_color is not None:
            colored_message = f"{final_color}{log_message}{Colors.RESET}"
        else:
            colored_message = log_message
        
        # 输出到控制台
        # 确保 ANSI 支持已启用（特别是在 Windows 上）
        if sys.platform == 'win32' and not _ansi_enabled:
            _enable_windows_ansi()
        
        if inline:
            # 同一行更新（使用\r，不换行）
            print(f"\r{colored_message}", end='', flush=True)
        else:
            print(colored_message, flush=True)
        
        # 输出到文件
        if self.log_file_enabled:
            with self._lock:
                self._update_log_file()  # 检查是否需要切换日志文件
                if self.log_file_handle:
                    # 文件输出时去除ANSI颜色代码（如果有）
                    clean_message = _strip_ansi_codes(log_message)
                    self.log_file_handle.write(clean_message + '\n')
                    self.log_file_handle.flush()
    
    def info(self, category, message, indent=0, show_thread=True, inline=False, color=None):
        """信息日志"""
        self.log("INFO", category, message, indent, show_thread, inline, color)
    
    def warn(self, category, message, indent=0, show_thread=True, color=None):
        """警告日志"""
        self.log("WARN", category, message, indent, show_thread, False, color)
    
    def error(self, category, message, indent=0, show_thread=True, color=None):
        """错误日志"""
        self.log("ERROR", category, message, indent, show_thread, False, color)
    
    def debug(self, category, message, indent=0, show_thread=True, color=None):
        """调试日志"""
        self.log("DEBUG", category, message, indent, show_thread, False, color)
    
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


def init_logger(log_level='INFO', log_file_enabled=True, log_dir='logs',
                filtered_levels=None, filtered_categories=None, config=None):
    """
    初始化全局日志实例（使用配置）
    
    Args:
        log_level: 日志级别 (DEBUG, INFO, WARN, ERROR)
        log_file_enabled: 是否启用文件输出
        log_dir: 日志文件目录
        filtered_levels: 被过滤的日志级别列表（黑名单）
        filtered_categories: 被过滤的日志类别列表（黑名单）
        config: 配置对象（可选），如果提供则从中读取过滤参数和类别颜色配置
    """
    global _global_logger
    
    category_colors = None
    
    # 如果提供了 config，从配置读取过滤参数和类别颜色
    if config:
        if filtered_levels is None:
            filtered_levels = config.get("logging.filtered_levels", [])
        if filtered_categories is None:
            filtered_categories = config.get("logging.filtered_categories", [])
        # 读取类别颜色配置
        category_colors = config.get("logging.category_colors", None)
    
    with _logger_lock:
        if _global_logger is None:
            _global_logger = Logger(
                log_level=log_level, 
                log_file_enabled=log_file_enabled, 
                log_dir=log_dir,
                filtered_levels=filtered_levels,
                filtered_categories=filtered_categories,
                category_colors=category_colors
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


def log_info(category, message, indent=0, show_thread=True, inline=False, color=None):
    """信息日志（全局函数）"""
    _get_global_logger().info(category, message, indent, show_thread, inline, color)


def log_warn(category, message, indent=0, show_thread=True, color=None):
    """警告日志（全局函数）"""
    _get_global_logger().warn(category, message, indent, show_thread, color)


def log_error(category, message, indent=0, show_thread=True, color=None):
    """错误日志（全局函数）"""
    _get_global_logger().error(category, message, indent, show_thread, color)


def log_debug(category, message, indent=0, show_thread=True, color=None):
    """调试日志（全局函数）"""
    _get_global_logger().debug(category, message, indent, show_thread, color)


def log_with_timestamp(message):
    """兼容旧代码的日志函数（不推荐使用）"""
    return f"[{get_timestamp()}] {message}"

