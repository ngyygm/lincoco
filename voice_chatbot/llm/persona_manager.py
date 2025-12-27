"""
人设管理器模块

提供人设文件的加载、缓存和管理功能，支持变量替换和运行时切换
"""

import os
import threading
from pathlib import Path
from typing import Optional, Dict

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.llm.variable_processor import VariableProcessor


class PersonaManager:
    """人设管理器 - 管理LLM的system prompt人设"""
    
    def __init__(self, config):
        """
        初始化人设管理器
        
        Args:
            config: 配置对象（Config实例）
        """
        self.config = config
        self._lock = threading.Lock()
        
        # 从配置获取参数
        self.persona_dir = config.get("llm.persona_dir", "llm/personas")
        self.default_persona_name = config.get("llm.persona_name", "default")
        self.fallback_prompt = config.get("llm.system_prompt", "")  # 向后兼容的fallback
        
        # 当前使用的人设名称
        self._current_persona_name: Optional[str] = None
        
        # 人设文件缓存（原始内容，未处理变量）
        self._persona_cache: Dict[str, str] = {}
        
        # 初始化变量处理器
        self.variable_processor = VariableProcessor(config)
        
        # 确保人设目录存在
        self._ensure_persona_dir()
        
        # 加载默认人设
        self._current_persona_name = self.default_persona_name
        log_info("PERSONA", f"人设管理器初始化完成，默认人设: {self.default_persona_name}", indent=0)
    
    def _ensure_persona_dir(self):
        """确保人设目录存在"""
        try:
            # 获取人设目录路径（相对于voice_chatbot目录）
            # __file__ 是 voice_chatbot/llm/persona_manager.py
            # parent 是 voice_chatbot/llm
            # parent.parent 是 voice_chatbot
            voice_chatbot_dir = Path(__file__).parent.parent
            persona_path = voice_chatbot_dir / self.persona_dir
            
            # 创建目录（如果不存在）
            persona_path.mkdir(parents=True, exist_ok=True)
            log_debug("PERSONA", f"人设目录: {persona_path}", indent=1)
        except Exception as e:
            log_error("PERSONA", f"创建人设目录失败: {e}", indent=1)
    
    def _get_persona_file_path(self, persona_name: str) -> Path:
        """
        获取人设文件路径
        
        Args:
            persona_name: 人设名称（不含扩展名）
            
        Returns:
            人设文件路径
        """
        # 获取人设目录路径（相对于voice_chatbot目录）
        voice_chatbot_dir = Path(__file__).parent.parent
        # 支持 .md 和 .txt 格式
        for ext in ['.md', '.txt']:
            file_path = voice_chatbot_dir / self.persona_dir / f"{persona_name}{ext}"
            if file_path.exists():
                return file_path
        # 如果都不存在，返回 .md 路径（用于创建）
        return voice_chatbot_dir / self.persona_dir / f"{persona_name}.md"
    
    def _load_persona_file(self, persona_name: str) -> Optional[str]:
        """
        加载人设文件内容（从文件或缓存）
        
        Args:
            persona_name: 人设名称
            
        Returns:
            人设文件内容（原始文本，未处理变量），如果文件不存在返回None
        """
        # 先检查缓存
        with self._lock:
            if persona_name in self._persona_cache:
                return self._persona_cache[persona_name]
        
        # 加载文件
        file_path = self._get_persona_file_path(persona_name)
        
        if not file_path.exists():
            log_warn("PERSONA", f"人设文件不存在: {file_path}，使用fallback", indent=1)
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # 缓存内容
            with self._lock:
                self._persona_cache[persona_name] = content
            
            log_debug("PERSONA", f"加载人设文件: {persona_name} ({len(content)} 字符)", indent=1)
            return content
        except Exception as e:
            log_error("PERSONA", f"加载人设文件失败: {file_path}, 错误: {e}", indent=1)
            return None
    
    def get_persona(self, persona_name: Optional[str] = None) -> str:
        """
        获取处理后人设文本（已替换变量）
        
        Args:
            persona_name: 人设名称，如果为None则使用当前人设
            
        Returns:
            处理后人设文本
        """
        # 确定使用的人设名称
        if persona_name is None:
            persona_name = self._current_persona_name or self.default_persona_name
        
        # 加载人设文件
        raw_content = self._load_persona_file(persona_name)
        
        # 如果文件不存在，使用fallback
        if raw_content is None:
            if persona_name == self.default_persona_name and self.fallback_prompt:
                # 使用配置中的system_prompt作为fallback
                raw_content = self.fallback_prompt
                log_debug("PERSONA", "使用配置中的system_prompt作为fallback", indent=1)
            else:
                # 尝试加载默认人设
                if persona_name != self.default_persona_name:
                    raw_content = self._load_persona_file(self.default_persona_name)
                    if raw_content is None and self.fallback_prompt:
                        raw_content = self.fallback_prompt
                else:
                    raw_content = self.fallback_prompt or ""
        
        # 处理变量替换
        if raw_content:
            processed_content = self.variable_processor.process(raw_content)
            return processed_content
        else:
            log_warn("PERSONA", f"人设内容为空: {persona_name}，返回空字符串", indent=1)
            return ""
    
    def set_persona(self, persona_name: str) -> bool:
        """
        切换人设（运行时动态切换）
        
        Args:
            persona_name: 人设名称
            
        Returns:
            是否切换成功
        """
        with self._lock:
            # 验证人设文件是否存在
            file_path = self._get_persona_file_path(persona_name)
            if not file_path.exists():
                # 如果不存在，检查是否是默认人设（可以使用fallback）
                if persona_name == self.default_persona_name and self.fallback_prompt:
                    self._current_persona_name = persona_name
                    log_info("PERSONA", f"切换人设: {persona_name} (使用fallback)", indent=0)
                    return True
                else:
                    log_error("PERSONA", f"人设文件不存在: {persona_name}，切换失败", indent=1)
                    return False
            
            # 切换人设
            old_persona = self._current_persona_name
            self._current_persona_name = persona_name
            log_info("PERSONA", f"切换人设: {old_persona} -> {persona_name}", indent=0)
            return True
    
    def get_current_persona_name(self) -> Optional[str]:
        """
        获取当前使用的人设名称
        
        Returns:
            当前人设名称
        """
        with self._lock:
            return self._current_persona_name or self.default_persona_name
    
    def list_personas(self) -> list:
        """
        列出所有人设文件
        
        Returns:
            人设名称列表
        """
        try:
            voice_chatbot_dir = Path(__file__).parent.parent
            persona_path = voice_chatbot_dir / self.persona_dir
            
            if not persona_path.exists():
                return []
            
            personas = []
            for file_path in persona_path.iterdir():
                if file_path.is_file() and file_path.suffix in ['.md', '.txt']:
                    personas.append(file_path.stem)
            
            return sorted(set(personas))
        except Exception as e:
            log_error("PERSONA", f"列出人设文件失败: {e}", indent=1)
            return []
    
    def clear_cache(self):
        """清空人设缓存（用于重新加载文件）"""
        with self._lock:
            self._persona_cache.clear()
            log_debug("PERSONA", "人设缓存已清空", indent=1)

