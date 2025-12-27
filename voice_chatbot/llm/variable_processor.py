"""
变量处理器模块

提供文本中的变量替换功能，支持 {{param_name}} 格式的参数替换
支持内置参数：current_time、current_date、weekday、user_name 等
"""

import re
import threading
from datetime import datetime
from typing import Dict, Callable, Any, Optional

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class VariableProcessor:
    """变量处理器 - 处理文本中的变量替换"""
    
    # 变量匹配模式：{{variable_name}}
    VARIABLE_PATTERN = re.compile(r'\{\{(\w+)\}\}')
    
    def __init__(self, config: Optional[Any] = None):
        """
        初始化变量处理器
        
        Args:
            config: 配置对象（可选，用于获取user_name等配置参数）
        """
        self.config = config
        self._lock = threading.Lock()
        
        # 注册内置参数处理函数
        self._builtin_processors: Dict[str, Callable[[], str]] = {
            'current_time': self._get_current_time,
            'current_date': self._get_current_date,
            'weekday': self._get_weekday,
            'user_name': self._get_user_name,
        }
        
        # 自定义参数处理器（可扩展）
        self._custom_processors: Dict[str, Callable[[], str]] = {}
    
    def _get_current_time(self) -> str:
        """获取当前时间（格式：HH:MM:SS）"""
        return datetime.now().strftime("%H:%M:%S")
    
    def _get_current_date(self) -> str:
        """获取当前日期（格式：YYYY-MM-DD）"""
        return datetime.now().strftime("%Y-%m-%d")
    
    def _get_weekday(self) -> str:
        """获取星期几（中文格式：星期一、星期二等）"""
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        weekday_index = datetime.now().weekday()
        return weekdays[weekday_index]
    
    def _get_user_name(self) -> str:
        """获取用户名（从配置读取）"""
        if self.config:
            try:
                user_name = self.config.get("llm.user_name", "用户")
                return user_name if user_name else "用户"
            except Exception as e:
                log_warn("VAR", f"获取用户名失败: {e}，使用默认值", indent=1)
                return "用户"
        return "用户"
    
    def register_processor(self, name: str, processor: Callable[[], str]):
        """
        注册自定义参数处理器
        
        Args:
            name: 参数名称（不包含大括号）
            processor: 处理函数，返回字符串值
        """
        with self._lock:
            self._custom_processors[name] = processor
            log_debug("VAR", f"注册自定义参数处理器: {name}", indent=1)
    
    def process(self, text: str) -> str:
        """
        处理文本中的变量替换
        
        Args:
            text: 包含变量的文本
            
        Returns:
            替换变量后的文本
        """
        if not text:
            return text
        
        def replace_var(match):
            """替换单个变量的函数"""
            var_name = match.group(1)
            
            # 先查找自定义处理器
            if var_name in self._custom_processors:
                try:
                    with self._lock:
                        processor = self._custom_processors[var_name]
                        return processor()
                except Exception as e:
                    log_warn("VAR", f"自定义参数处理器 {var_name} 执行失败: {e}，保留原始变量", indent=1)
                    return match.group(0)  # 保留原始变量
            
            # 再查找内置处理器
            if var_name in self._builtin_processors:
                try:
                    processor = self._builtin_processors[var_name]
                    return processor()
                except Exception as e:
                    log_warn("VAR", f"内置参数处理器 {var_name} 执行失败: {e}，保留原始变量", indent=1)
                    return match.group(0)  # 保留原始变量
            
            # 未找到处理器，保留原始变量
            log_warn("VAR", f"未找到参数处理器: {var_name}，保留原始变量", indent=1)
            return match.group(0)
        
        # 替换所有匹配的变量
        try:
            result = self.VARIABLE_PATTERN.sub(replace_var, text)
            return result
        except Exception as e:
            log_error("VAR", f"变量替换失败: {e}，返回原始文本", indent=1)
            return text
    
    def get_available_variables(self) -> list:
        """
        获取所有可用的变量名称列表
        
        Returns:
            变量名称列表
        """
        with self._lock:
            variables = list(self._builtin_processors.keys())
            variables.extend(self._custom_processors.keys())
            return sorted(set(variables))





