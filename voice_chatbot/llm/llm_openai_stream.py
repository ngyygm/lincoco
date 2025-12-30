"""
LLM流式生成模块（OpenAI API实现）

提供OpenAI API兼容的流式文本生成、句子分割与合并、任务取消检查、标签过滤等功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import re
import time
import threading
import openai
import base64
from typing import Callable, List, Optional, Tuple, Dict, Any
from abc import ABC, abstractmethod

try:
    import langid
    HAS_LANGID = True
except ImportError:
    HAS_LANGID = False

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.llm.persona_manager import PersonaManager


def remove_trailing_comma(text: str) -> str:
    """
    删除文本末尾的逗号（中文逗号，或英文逗号）
    
    Args:
        text: 待处理的文本
        
    Returns:
        删除末尾逗号后的文本
    """
    # 删除末尾的逗号（中文逗号，或英文逗号）及其后的空白字符
    return re.sub(r'[，,]\s*$', '', text)


def split_sentences(text: str) -> List[str]:
    """
    将文本分割成句子
    支持中文和英文的标点符号
    
    Args:
        text: 待分割的文本
        
    Returns:
        句子列表
    """
    # 匹配句子结束符号：。！？.!? 以及换行符
    # 但要注意不要在数字中间分割（如 3.14、1.3）
    # 也要排除列表标号（如 1. 文本、2. 文本）
    # 还要排除省略号（... 或 ..）
    # 使用负向前瞻和负向后顾，确保句号前后不是数字，且不是列表标号格式，且不是省略号
    # 列表标号模式：数字+句号+（空格或中文字符）
    # 省略号模式：两个或三个连续的点（.. 或 ...）
    sentences = re.split(r'(?<=[。！？~])\s*|(?<=\.[^0-9\s\u4e00-\u9fff.])|(?<=[!?])\s*|(?<=\n)\s*', text)
    return [s.strip() for s in sentences if s.strip()]


def get_complete_sentences(buffer: str, is_first_sentence: bool = False, comma_delay_chars: int = 5) -> Tuple[List[str], str]:
    """
    从缓冲区中提取完整的句子
    
    Args:
        buffer: 文本缓冲区
        is_first_sentence: 是否是首句（首句支持逗号切分，后续句子只支持完整句子）
        comma_delay_chars: 首句逗号延迟字符数，遇到逗号后等待此数量的字符，如果期间出现句号等结束符则按结束符切分，否则按逗号切分
        
    Returns:
        (完整句子列表, 剩余的不完整文本)
    """
    # 查找句子结束符（。！？.!?\n），注意英文句号要排除小数、列表标号和省略号
    # 小数：如 1.3、3.14（句号前后都是数字）
    # 列表标号：如 1. 文本、2. 文本（数字+句号+空格/中文字符）
    # 省略号：如 ... 或 ..（两个或三个连续的点）
    # 使用负向前瞻和负向后顾，确保句号前后不是数字，且不是列表标号格式，且不是省略号
    # 列表标号模式：数字+句号+（空格或中文字符）
    # 省略号模式：句号后面紧跟另一个点（.）
    end_pattern = r'[。！？]|(?<![0-9])\.(?![0-9\s\u4e00-\u9fff.])|[!?]|\n'
    end_match = list(re.finditer(end_pattern, buffer))
    comma_match = list(re.finditer(r'[，,]', buffer))
    
    if end_match:
        # 有句子结束符，按句子结束符切分
        last_end = end_match[-1].end()
        complete_text = buffer[:last_end]
        remaining = buffer[last_end:]
        sentences = split_sentences(complete_text)
        # 如果是首句，删除句子末尾的逗号（虽然按句子结束符切分时通常不会有逗号，但为了保险起见）
        if is_first_sentence:
            sentences = [remove_trailing_comma(s) for s in sentences]
            sentences = [s for s in sentences if s.strip()]  # 再次过滤空字符串
        return sentences, remaining
    elif is_first_sentence and comma_match:
        # 只有首句支持按逗号切分，并且支持延迟判断
        # 找到第一个逗号
        first_comma = comma_match[0]
        comma_pos = first_comma.end()  # 逗号后的位置
        
        # 检查逗号后的内容长度是否足够延迟字符数
        chars_after_comma = len(buffer) - comma_pos
        
        if chars_after_comma >= comma_delay_chars:
            # 逗号后已经有足够的字符，检查延迟范围内是否有句子结束符
            check_range = buffer[comma_pos:comma_pos + comma_delay_chars]
            end_in_range = list(re.finditer(end_pattern, check_range))
            
            if end_in_range:
                # 在延迟范围内找到了句子结束符，不按逗号切分，等待按句子结束符切分
                # 这种情况应该等待更多内容，让下次调用时通过上面的end_match分支处理
                return [], buffer
            else:
                # 延迟范围内没有句子结束符，按逗号切分
                complete_text = buffer[:comma_pos]
                remaining = buffer[comma_pos:]
                # 按逗号分割
                sentences = re.split(r'(?<=[，,])\s*', complete_text)
                sentences = [s.strip() for s in sentences if s.strip()]
                # 删除首句切分后句子末尾的逗号
                sentences = [remove_trailing_comma(s) for s in sentences]
                sentences = [s for s in sentences if s.strip()]  # 再次过滤空字符串
                return sentences, remaining
        else:
            # 逗号后的内容还不够延迟字符数，等待更多内容
            return [], buffer
    # 后续句子没有句子结束符时，不切分，等待完整句子
    return [], buffer


def get_text_length(text: str) -> int:
    """
    根据语言类型计算文本长度：
    - 英文等西语：按单词数量（空格分隔）计算
    - 中文、日文等：按字符数量计算
    
    Args:
        text: 文本
        
    Returns:
        文本长度（单词数或字符数）
    """
    if not text or not text.strip():
        return 0
    
    if not HAS_LANGID:
        # 如果没有langid，使用简单启发式：如果包含空格，可能是英文，按单词计算
        if ' ' in text.strip():
            words = text.strip().split()
            return len(words)
        else:
            return len(text)
    
    try:
        language, _ = langid.classify(text)
        # 西语列表：英语、法语、西班牙语、德语、意大利语、葡萄牙语等
        western_languages = ["en", "fr", "es", "de", "it", "pt", "ru", "nl", "sv", "no", "da", "fi", "pl", "cs", "hu", "ro", "bg", "hr", "sk", "sl", "et", "lv", "lt"]
        
        if language in western_languages:
            # 西语：按单词数量计算（空格分隔）
            words = text.strip().split()
            return len(words)
        else:
            # 中文、日文等：按字符数量计算
            return len(text)
    except:
        # 如果语言检测失败，默认按字符数计算
        return len(text)


class TokenUsageStats:
    """Token使用统计类"""
    
    def __init__(self):
        """初始化Token统计"""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.request_count = 0
        self._lock = threading.Lock()
    
    def add_usage(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0):
        """
        添加一次请求的token使用统计
        
        Args:
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
            total_tokens: 总token数
        """
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_tokens += total_tokens
            self.request_count += 1
    
    def get_stats(self) -> Dict[str, int]:
        """
        获取统计信息
        
        Returns:
            包含统计信息的字典
        """
        with self._lock:
            return {
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "request_count": self.request_count
            }
    
    def reset(self):
        """重置统计"""
        with self._lock:
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_tokens = 0
            self.request_count = 0
    
    def output_stats(self):
        """输出统计信息到日志"""
        stats = self.get_stats()
        log_info("TOKEN", f"Token使用统计 [请求数: {stats['request_count']}]", indent=0)
        log_info("TOKEN", f"  输入tokens: {stats['total_prompt_tokens']:,}", indent=1)
        log_info("TOKEN", f"  输出tokens: {stats['total_completion_tokens']:,}", indent=1)
        log_info("TOKEN", f"  总计tokens: {stats['total_tokens']:,}", indent=1)


class MemoryManager(ABC):
    """长期记忆管理器抽象类"""
    
    @abstractmethod
    def store_memory(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        存储记忆
        
        Args:
            content: 记忆内容
            metadata: 元数据（如类型、时间戳等）
            
        Returns:
            记忆ID
        """
        pass
    
    @abstractmethod
    def retrieve_memories(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        检索相关记忆
        
        Args:
            query: 查询文本
            limit: 返回数量限制
            
        Returns:
            记忆列表，每个记忆包含content和metadata
        """
        pass
    
    @abstractmethod
    def inject_to_prompt(self, query: str, limit: int = 5) -> str:
        """
        将检索到的记忆注入到prompt中
        
        Args:
            query: 查询文本
            limit: 检索数量限制
            
        Returns:
            格式化的prompt文本
        """
        pass


class RAGManager(ABC):
    """RAG检索增强管理器抽象类"""
    
    @abstractmethod
    def add_documents(self, documents: List[str], metadatas: Optional[List[Dict[str, Any]]] = None):
        """
        添加文档到知识库
        
        Args:
            documents: 文档列表
            metadatas: 元数据列表（可选）
        """
        pass
    
    @abstractmethod
    def search(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        搜索相关文档
        
        Args:
            query: 查询文本
            limit: 返回数量限制
            
        Returns:
            文档列表，每个文档包含content和metadata
        """
        pass
    
    @abstractmethod
    def inject_to_prompt(self, query: str, limit: int = 3) -> str:
        """
        将检索到的文档注入到prompt中
        
        Args:
            query: 查询文本
            limit: 检索数量限制
            
        Returns:
            格式化的prompt文本
        """
        pass


class LLMStreamGenerator:
    """LLM流式生成器（OpenAI API实现）"""
    
    def __init__(self, config, conversation_history: List[dict], task_lock: threading.Lock, 
                 get_current_task_id: Callable[[], int], 
                 memory_manager: Optional[MemoryManager] = None,
                 rag_manager: Optional[RAGManager] = None,
                 persona_manager: Optional[PersonaManager] = None):
        """
        初始化LLM流式生成器
        
        Args:
            config: 配置对象（Config实例）
            conversation_history: 对话历史列表（会被修改）
            task_lock: 任务锁
            get_current_task_id: 获取当前任务ID的函数
            memory_manager: 记忆管理器（可选）
            rag_manager: RAG管理器（可选）
            persona_manager: 人设管理器（可选，如果不提供则创建新实例）
        """
        self.config = config
        self.conversation_history = conversation_history
        self.task_lock = task_lock
        self.get_current_task_id = get_current_task_id
        self.memory_manager = memory_manager
        self.rag_manager = rag_manager
        
        # Token使用统计
        self.token_stats = TokenUsageStats()
        
        # 从配置获取LLM参数
        self.api_base = config.get("llm.api_base", "http://127.0.0.1:5000/v1")
        self.api_key = config.get("llm.api_key", "sk-1234")
        self.model_name = config.get("llm.model_name", "Qwen3-8B-GGUF.gguf")
        self.max_tokens = config.get("llm.max_tokens", 512)
        self.temperature = config.get("llm.temperature", 0.7)
        self.min_sentence_length = config.get("llm.min_sentence_length", 15)
        self.sentence_length_increment_rate = config.get("llm.sentence_length_increment_rate", 1.5)
        self.max_sentence_length = config.get("llm.max_sentence_length", 200)
        self.if_thinking = config.get("llm.if_thinking", False)
        self.first_sentence_comma_delay_chars = config.get("llm.first_sentence_comma_delay_chars", 5)
        
        # 使用传入的persona_manager，如果没有则创建新实例（向后兼容）
        if persona_manager is not None:
            self.persona_manager = persona_manager
            log_debug("LLM", "使用传入的PersonaManager实例", indent=1)
        else:
            self.persona_manager = PersonaManager(config)
            log_debug("LLM", "创建新的PersonaManager实例", indent=1)
        # system_prompt 现在从 PersonaManager 动态获取，不再直接存储
        
        # 初始化OpenAI客户端
        openai.api_base = self.api_base
        openai.api_key = self.api_key
        
        # 尝试创建新版本客户端
        try:
            self.openai_client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.api_base
            )
            self.use_new_api = True
        except:
            self.use_new_api = False
            self.openai_client = None
        
        log_info("LLM", f"初始化完成 [API: {'新版本' if self.use_new_api else '旧版本'}, 模型: {self.model_name}]", indent=0)
    
    def cleanup(self):
        """
        清理资源
        
        取消所有进行中的请求，清理客户端连接等
        """
        log_info("LLM", "清理LLM资源", indent=0)
        # OpenAI客户端不需要显式清理，但可以在这里添加其他清理逻辑
        # 如果有进行中的请求，应该在这里取消
        # 注意：流式请求的取消需要在调用方处理
    
    def set_persona(self, persona_name: str) -> bool:
        """
        切换人设（运行时动态切换）
        
        Args:
            persona_name: 人设名称（对应llm/personas/目录下的文件名，不含扩展名）
            
        Returns:
            是否切换成功
        """
        return self.persona_manager.set_persona(persona_name)
    
    def get_current_persona_name(self) -> Optional[str]:
        """
        获取当前使用的人设名称
        
        Returns:
            当前人设名称
        """
        return self.persona_manager.get_current_persona_name()
    
    def _check_task_cancelled(self, task_id: int) -> bool:
        """
        检查任务是否被取消
        
        Args:
            task_id: 任务ID
            
        Returns:
            True表示任务已取消，False表示任务有效
        """
        with self.task_lock:
            current_task_id = self.get_current_task_id()
            return task_id != current_task_id
    
    def _filter_redacted_content(self, text: str, in_redacted_tag: bool, redacted_buffer: str) -> Tuple[str, bool, str]:
        """
        过滤<think>...</think>标签及其内容
        
        Args:
            text: 待过滤的文本
            in_redacted_tag: 是否在标签内（状态）
            redacted_buffer: 标签内容缓冲区（状态）
            
        Returns:
            (过滤后的文本, 是否在标签内, 标签内容缓冲区)
        """
        if not text:
            return "", in_redacted_tag, redacted_buffer
        
        result = ""
        remaining = text
        
        while remaining:
            if not in_redacted_tag:
                # 不在标签内，查找<think>
                start_tag = "<think>"
                start_pos = remaining.find(start_tag)
                
                if start_pos != -1:
                    # 找到开始标签，保留之前的内容
                    result += remaining[:start_pos]
                    # 跳过开始标签
                    remaining = remaining[start_pos + len(start_tag):]
                    in_redacted_tag = True
                    redacted_buffer = ""
                    log_debug("LLM", "检测到<think>标签，开始跳过内容", indent=2)
                else:
                    # 没找到开始标签，全部保留
                    result += remaining
                    remaining = ""
            else:
                # 在标签内，查找</think>
                end_tag = "</think>"
                end_pos = remaining.find(end_tag)
                
                if end_pos != -1:
                    # 找到结束标签，丢弃标签内的内容
                    redacted_buffer += remaining[:end_pos]
                    redacted_length = len(redacted_buffer)
                    # 跳过结束标签
                    remaining = remaining[end_pos + len(end_tag):]
                    in_redacted_tag = False
                    redacted_buffer = ""
                    log_debug("LLM", f"已跳过<think>标签内容（{redacted_length}字符）", indent=2)
                else:
                    # 没找到结束标签，继续累积（不处理）
                    redacted_buffer += remaining
                    remaining = ""
        
        return result, in_redacted_tag, redacted_buffer
    
    def _encode_image_base64(self, image_path: str) -> str:
        """
        将图像文件编码为base64格式
        
        Args:
            image_path: 图像文件路径
            
        Returns:
            base64编码的字符串（data URI格式）
        """
        try:
            with open(image_path, "rb") as image_file:
                image_data = image_file.read()
                base64_data = base64.b64encode(image_data).decode('utf-8')
                # 检测图像格式
                if image_path.lower().endswith('.png'):
                    mime_type = 'image/png'
                elif image_path.lower().endswith('.jpg') or image_path.lower().endswith('.jpeg'):
                    mime_type = 'image/jpeg'
                elif image_path.lower().endswith('.gif'):
                    mime_type = 'image/gif'
                elif image_path.lower().endswith('.webp'):
                    mime_type = 'image/webp'
                else:
                    mime_type = 'image/jpeg'  # 默认
                return f"data:{mime_type};base64,{base64_data}"
        except Exception as e:
            log_error("LLM", f"图像编码失败: {e}", indent=1)
            raise
    
    def _build_messages(self, user_input: str, images: Optional[List[str]] = None) -> List[dict]:
        """
        构建OpenAI API的messages格式
        
        Args:
            user_input: 用户输入
            images: 图像列表（可以是文件路径、base64字符串或URL）
            
        Returns:
            messages列表
        """
        messages = []
        
        # 添加system prompt（从PersonaManager动态获取，支持参数实时更新）
        system_prompt = self.persona_manager.get_persona()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        # 添加RAG检索结果（如果启用）
        if self.rag_manager:
            try:
                rag_context = self.rag_manager.inject_to_prompt(user_input)
                if rag_context:
                    # 将RAG上下文添加到system prompt或单独的消息
                    if messages and messages[0].get("role") == "system":
                        messages[0]["content"] += "\n\n" + rag_context
                    else:
                        messages.insert(0, {"role": "system", "content": rag_context})
            except Exception as e:
                log_warn("LLM", f"RAG检索失败: {e}", indent=1)
        
        # 添加记忆（如果启用）
        if self.memory_manager:
            try:
                memory_context = self.memory_manager.inject_to_prompt(user_input)
                if memory_context:
                    # 将记忆上下文添加到system prompt
                    if messages and messages[0].get("role") == "system":
                        messages[0]["content"] += "\n\n" + memory_context
                    else:
                        messages.insert(0, {"role": "system", "content": memory_context})
            except Exception as e:
                log_warn("LLM", f"记忆检索失败: {e}", indent=1)
        
        # 添加历史对话
        with self.task_lock:
            messages.extend(self.conversation_history.copy())
        
        # 构建用户消息
        user_content = user_input
        if not self.if_thinking:
            user_content += " /no_think"
        
        # 如果有多模态内容（图像）
        if images:
            content_list = [{"type": "text", "text": user_content}]
            for image in images:
                if image.startswith("http://") or image.startswith("https://"):
                    # URL格式
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": image}
                    })
                elif image.startswith("data:image"):
                    # 已经是base64格式
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": image}
                    })
                else:
                    # 文件路径，需要编码
                    try:
                        base64_image = self._encode_image_base64(image)
                        content_list.append({
                            "type": "image_url",
                            "image_url": {"url": base64_image}
                        })
                    except Exception as e:
                        log_warn("LLM", f"跳过无效图像: {image}, 错误: {e}", indent=1)
            
            messages.append({"role": "user", "content": content_list})
        else:
            # 纯文本消息
            messages.append({"role": "user", "content": user_content})
        
        return messages
    
    def generate_stream(self, user_input: str, task_id: int, 
                       on_sentence_callback: Callable[[str], None],
                       images: Optional[List[str]] = None) -> str:
        """
        流式生成文本
        
        Args:
            user_input: 用户输入文本
            task_id: 任务ID（用于取消检查）
            on_sentence_callback: 句子回调函数（sentence_text）
            images: 图像列表（可选，支持文件路径、base64或URL）
            
        Returns:
            full_response_text: 完整回答文本
            
        Raises:
            Exception: 生成过程中的异常
        """
        stream_start_time = time.time()
        log_info("LLM", "开始流式生成", indent=0)
        
        # 检查任务是否被取消
        if self._check_task_cancelled(task_id):
            log_warn("LLM", f"任务 {task_id} 在开始前被取消", indent=1)
            return ""
        
        # 构建messages
        messages = self._build_messages(user_input, images=images)
        
        # 输出messages内容（格式化显示）
        log_info("LLM", "准备推理", indent=0)
        log_info("LLM", f"Messages ({len(messages)}条):", indent=1)
        
        # 统计不同类型消息的数量
        system_count = sum(1 for msg in messages if msg.get("role") == "system")
        user_count = sum(1 for msg in messages if msg.get("role") == "user")
        assistant_count = sum(1 for msg in messages if msg.get("role") == "assistant")
        history_count = user_count + assistant_count - 1  # 减去当前用户输入
        
        if system_count > 0:
            log_info("LLM", f"  System消息: {system_count}条", indent=2)
        if history_count > 0:
            log_info("LLM", f"  历史对话: {history_count}轮 ({assistant_count}条回答, {user_count-1}条用户输入)", indent=2)
        
        # 输出每条消息的详细信息
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            if role == "system":
                # 系统消息：显示前200个字符
                content_preview = content
                log_info("LLM", f"  [{i+1}] {role}: {content_preview}", indent=2)
            elif role == "user":
                # 用户消息：检查是否是多模态
                if isinstance(content, list):
                    # 多模态消息
                    text_parts = [item.get("text", "") for item in content if item.get("type") == "text"]
                    image_count = sum(1 for item in content if item.get("type") == "image_url")
                    text_content = " ".join(text_parts)
                    log_info("LLM", f"  [{i+1}] {role}: {text_content} [包含{image_count}张图片]", indent=2)
                else:
                    # 纯文本消息
                    log_info("LLM", f"  [{i+1}] {role}: {content}", indent=2)
            elif role == "assistant":
                # 助手消息：显示前200个字符（去除前导空白字符）
                content_preview = content.lstrip()
                log_info("LLM", f"  [{i+1}] {role}: {content_preview}", indent=2)
            else:
                log_info("LLM", f"  [{i+1}] {role}: {str(content)}", indent=2)
        
        # 再次检查任务是否被取消
        if self._check_task_cancelled(task_id):
            log_warn("LLM", f"任务 {task_id} 在构建messages后被取消", indent=1)
            return ""
        
        # 流式生成状态
        buffer = ""
        sentence_count = 0
        accumulated_sentences = []  # 累积的句子列表
        accumulated_length = 0  # 累积的长度（中文等按字符数，英文等按单词数）
        batch_index = 0  # 当前批次索引（用于计算最小长度要求）
        full_response_text = ""  # 完整的回答文本
        
        # 用于处理<think>标签的状态
        in_redacted_tag = False  # 是否在<think>标签内
        redacted_buffer = ""  # 用于累积标签内的内容（不处理）
        
        def check_and_flush_accumulated():
            """检查累积的句子是否达到最小长度要求，如果达到则放入队列"""
            nonlocal accumulated_sentences, accumulated_length, batch_index, sentence_count
            
            # 检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return  # 任务已取消，不再处理
            
            if not accumulated_sentences:
                return
            
            # 合并所有累积的句子用于检查
            merged_sentence = "".join(accumulated_sentences)
            
            # 第一句话（batch_index=0）的特殊处理：如果以逗号结尾，立即发送
            if batch_index == 0 and merged_sentence.strip():
                # 检查最后一个字符是否是逗号
                if re.search(r'[，,]$', merged_sentence.strip()):
                    # 再次检查任务是否被取消（在回调前）
                    if self._check_task_cancelled(task_id):
                        return  # 任务已取消，不再处理
                    
                    # 删除末尾的逗号
                    merged_sentence = remove_trailing_comma(merged_sentence.strip())
                    
                    sentence_count += 1
                    # 计算从流式开始到现在的耗时
                    elapsed_from_stream = time.time() - stream_start_time
                    log_info("LLM", f"句子{sentence_count} (第一句，逗号切分，长度{accumulated_length}): {merged_sentence} [耗时: {elapsed_from_stream*1000:.1f}ms]", indent=1)
                    
                    # 调用回调函数（传入不带逗号的文本）
                    try:
                        on_sentence_callback(merged_sentence)
                    except Exception as e:
                        log_error("LLM", f"句子回调函数错误: {e}", indent=2)
                    
                    # 重置累积状态
                    accumulated_sentences = []
                    accumulated_length = 0
                    batch_index += 1
                    return  # 第一句已发送，直接返回
            
            # 检查是否超过最大长度（优先检查，防止无限增长）
            if accumulated_length > self.max_sentence_length:
                # 检查是否是单个句子超过最大长度（特殊情况，允许发送）
                if len(accumulated_sentences) == 1:
                    # 单个句子超过最大长度，允许发送
                    if self._check_task_cancelled(task_id):
                        return
                    
                    sentence_count += 1
                    elapsed_from_stream = time.time() - stream_start_time
                    log_info("LLM", f"句子{sentence_count} (单句超长，长度{accumulated_length}，超过最大长度{self.max_sentence_length}): {merged_sentence} [耗时: {elapsed_from_stream*1000:.1f}ms]", indent=1)
                    
                    try:
                        # 只去除开头的空白字符，保留末尾的所有字符（包括空白字符）
                        trimmed_sentence = merged_sentence.lstrip()
                        if trimmed_sentence:  # 确保去除开头空白后还有内容
                            on_sentence_callback(trimmed_sentence)
                    except Exception as e:
                        log_error("LLM", f"句子回调函数错误: {e}", indent=2)
                    
                    accumulated_sentences = []
                    accumulated_length = 0
                    batch_index += 1
                    return
                else:
                    # 多个句子累积超过最大长度，立即发送（即使未达到最小长度）
                    if self._check_task_cancelled(task_id):
                        return
                    
                    sentence_count += 1
                    elapsed_from_stream = time.time() - stream_start_time
                    log_info("LLM", f"句子{sentence_count} (超过最大长度{self.max_sentence_length}，长度{accumulated_length}): {merged_sentence} [耗时: {elapsed_from_stream*1000:.1f}ms]", indent=1)
                    
                    try:
                        # 只去除开头的空白字符，保留末尾的所有字符（包括空白字符）
                        trimmed_sentence = merged_sentence.lstrip()
                        if trimmed_sentence:  # 确保去除开头空白后还有内容
                            on_sentence_callback(trimmed_sentence)
                    except Exception as e:
                        log_error("LLM", f"句子回调函数错误: {e}", indent=2)
                    
                    accumulated_sentences = []
                    accumulated_length = 0
                    batch_index += 1
                    return
            
            # 计算当前批次的最小长度要求
            # 第一句：min_sentence_length，第二句：min_sentence_length * increment_rate，以此类推
            min_length = self.min_sentence_length * (self.sentence_length_increment_rate ** batch_index)
            
            # 如果累积长度达到最小要求，则合并并发送
            if accumulated_length >= min_length:
                # 再次检查任务是否被取消（在回调前）
                if self._check_task_cancelled(task_id):
                    return  # 任务已取消，不再处理
                
                # 合并所有累积的句子
                if merged_sentence.strip():
                    sentence_count += 1
                    # 计算从流式开始到现在的耗时
                    elapsed_from_stream = time.time() - stream_start_time
                    log_info("LLM", f"句子{sentence_count} (合并{len(accumulated_sentences)}句，长度{accumulated_length}): {merged_sentence} [耗时: {elapsed_from_stream*1000:.1f}ms]", indent=1)
                    
                    # 调用回调函数（只去除开头的空白字符，保留末尾的所有字符）
                    try:
                        trimmed_sentence = merged_sentence.lstrip()
                        if trimmed_sentence:  # 确保去除开头空白后还有内容
                            on_sentence_callback(trimmed_sentence)
                    except Exception as e:
                        log_error("LLM", f"句子回调函数错误: {e}", indent=2)
                    
                    # 重置累积状态
                    accumulated_sentences = []
                    accumulated_length = 0
                    batch_index += 1
        
        # Token使用统计
        usage_info = None
        
        # 重试配置
        max_retries = self.config.get("llm.max_retries", 3)
        retry_delay = self.config.get("llm.retry_delay", 1.0)
        request_timeout = self.config.get("llm.request_timeout", 30.0)
        
        # 重试循环（只对API调用失败重试，流式传输过程中的错误不重试）
        last_error = None
        response = None
        
        for retry_count in range(max_retries):
            try:
                # 调用OpenAI API
                if self.use_new_api:
                    response = self.openai_client.chat.completions.create(
                        model=self.model_name,
                        messages=messages,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        stream=True,
                        timeout=request_timeout
                    )
                else:
                    # 使用旧版本API
                    response = openai.ChatCompletion.create(
                        model=self.model_name,
                        messages=messages,
                        max_tokens=self.max_tokens,
                        temperature=self.temperature,
                        stream=True,
                        timeout=request_timeout
                    )
                
                # API调用成功，跳出重试循环
                break
                
            except Exception as e:
                last_error = e
                # 判断是否应该重试
                should_retry = False
                if retry_count < max_retries - 1:
                    # 网络错误、超时错误等可以重试
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ['timeout', 'connection', 'network', 'unreachable', 'refused']):
                        should_retry = True
                        log_warn("LLM", f"API调用错误（可重试）: {e} [重试 {retry_count + 1}/{max_retries}]", indent=0)
                    else:
                        # 其他错误（如API错误、参数错误等）不重试
                        log_error("LLM", f"API调用错误（不可重试）: {e}", indent=0)
                        break
                else:
                    log_error("LLM", f"API调用错误（已达最大重试次数）: {e}", indent=0)
                
                if should_retry:
                    # 等待后重试
                    time.sleep(retry_delay * (retry_count + 1))  # 指数退避
                    log_info("LLM", "准备重试...", indent=1)
                    continue
                else:
                    break
        
        # 如果API调用失败，抛出异常
        if response is None:
            if last_error:
                import traceback
                traceback.print_exc()
                raise last_error
            else:
                raise Exception("API调用失败，未知错误")
        
        # 流式处理（不再重试，因为已经建立了连接）
        try:
            if self.use_new_api:
                for chunk in response:
                    # 捕获usage信息（通常在最后一个chunk中）
                    if hasattr(chunk, 'usage') and chunk.usage:
                        usage_info = chunk.usage
                    # 检查任务是否被取消
                    if self._check_task_cancelled(task_id):
                        log_warn("LLM", f"任务 {task_id} 被取消", indent=1)
                        break
                    
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        # 过滤<think>标签内容
                        filtered_content, in_redacted_tag, redacted_buffer = self._filter_redacted_content(
                            content, in_redacted_tag, redacted_buffer
                        )
                        
                        # 只将过滤后的内容添加到buffer（用于句子分割）
                        if filtered_content:
                            buffer += filtered_content
                        
                        # full_response_text也记录过滤后的内容（用于历史记录）
                        if filtered_content:
                            full_response_text += filtered_content
                        
                        # 检测完整句子（只处理过滤后的内容）
                        if filtered_content:
                            # 判断是否是首句（sentence_count == 0 表示还没有发送过任何句子）
                            is_first = (sentence_count == 0)
                            sentences, buffer = get_complete_sentences(buffer, is_first_sentence=is_first, comma_delay_chars=self.first_sentence_comma_delay_chars)
                            for sentence in sentences:
                                if sentence.strip():
                                    # 将句子加入累积缓冲区
                                    accumulated_sentences.append(sentence)
                                    accumulated_length += get_text_length(sentence)
                                    # 检查是否需要刷新到队列
                                    check_and_flush_accumulated()
            else:
                # 使用旧版本API
                for chunk in response:
                    # 捕获usage信息（旧版本API格式）
                    if 'usage' in chunk:
                        usage_info = chunk['usage']
                    if self._check_task_cancelled(task_id):
                        log_warn("LLM", f"任务 {task_id} 被取消", indent=1)
                        break
                    
                    if 'choices' in chunk and len(chunk['choices']) > 0:
                        delta = chunk['choices'][0].get('delta', {})
                        content = delta.get('content', '')
                        if content:
                            # 过滤<think>标签内容
                            filtered_content, in_redacted_tag, redacted_buffer = self._filter_redacted_content(
                                content, in_redacted_tag, redacted_buffer
                            )
                            
                            # 只将过滤后的内容添加到buffer（用于句子分割）
                            if filtered_content:
                                buffer += filtered_content
                            
                            # full_response_text也记录过滤后的内容（用于历史记录）
                            if filtered_content:
                                full_response_text += filtered_content
                            
                            # 检测完整句子（只处理过滤后的内容）
                            if filtered_content:
                                # 判断是否是首句（sentence_count == 0 表示还没有发送过任何句子）
                                is_first = (sentence_count == 0)
                                sentences, buffer = get_complete_sentences(buffer, is_first_sentence=is_first, comma_delay_chars=self.first_sentence_comma_delay_chars)
                                for sentence in sentences:
                                    if sentence.strip():
                                        # 将句子加入累积缓冲区
                                        accumulated_sentences.append(sentence)
                                        accumulated_length += get_text_length(sentence)
                                        # 检查是否需要刷新到队列
                                        check_and_flush_accumulated()
            
            # 处理剩余的buffer和累积的句子
            # 如果流式结束时还在标签内，说明标签未闭合，丢弃未闭合的标签内容
            if in_redacted_tag:
                log_debug("LLM", f"流式结束但<think>标签未闭合，丢弃{len(redacted_buffer)}字符", indent=2)
                redacted_buffer = ""
                in_redacted_tag = False
            
            # 在处理剩余buffer前检查任务是否被取消
            if self._check_task_cancelled(task_id):
                log_warn("LLM", f"任务 {task_id} 在处理剩余buffer时被取消", indent=1)
                return full_response_text if full_response_text else ""
            
            if buffer:
                # 将剩余的buffer也加入累积缓冲区（保留所有字符，包括空白字符）
                accumulated_sentences.append(buffer)
                accumulated_length += get_text_length(buffer)
            
            # 先尝试正常刷新（可能刚好达到最小长度要求）
            check_and_flush_accumulated()
            
            # 如果还有累积的句子（未达到最小长度要求），强制刷新（即使不够最小长度也要处理）
            if accumulated_sentences:
                # 再次检查任务是否被取消（在强制发送前）
                if self._check_task_cancelled(task_id):
                    log_warn("LLM", f"任务 {task_id} 在强制发送剩余句子时被取消", indent=1)
                    return full_response_text if full_response_text else ""
                
                merged_sentence = "".join(accumulated_sentences)
                if merged_sentence.strip():
                    sentence_count += 1
                    elapsed_from_stream = time.time() - stream_start_time
                    log_info("LLM", f"句子{sentence_count}(末尾，强制发送): {merged_sentence} [耗时: {elapsed_from_stream*1000:.1f}ms]", indent=1)
                    
                    # 调用回调函数（保留原始内容，只去除开头空白，保留末尾字符）
                    if not self._check_task_cancelled(task_id):
                        try:
                            # 只去除开头的空白字符，保留末尾的所有字符（包括空白字符）
                            trimmed_sentence = merged_sentence.lstrip()
                            if trimmed_sentence:  # 确保去除开头空白后还有内容
                                on_sentence_callback(trimmed_sentence)
                        except Exception as e:
                            log_error("LLM", f"句子回调函数错误: {e}", indent=2)
            
            # 记录Token使用统计
            if usage_info:
                if self.use_new_api:
                    prompt_tokens = usage_info.prompt_tokens if hasattr(usage_info, 'prompt_tokens') else 0
                    completion_tokens = usage_info.completion_tokens if hasattr(usage_info, 'completion_tokens') else 0
                    total_tokens = usage_info.total_tokens if hasattr(usage_info, 'total_tokens') else 0
                else:
                    prompt_tokens = usage_info.get('prompt_tokens', 0)
                    completion_tokens = usage_info.get('completion_tokens', 0)
                    total_tokens = usage_info.get('total_tokens', 0)
                
                self.token_stats.add_usage(prompt_tokens, completion_tokens, total_tokens)
                log_info("TOKEN", f"本次使用 [输入: {prompt_tokens}, 输出: {completion_tokens}, 总计: {total_tokens}]", indent=1)
            
            stream_elapsed = time.time() - stream_start_time

            full_response_text = full_response_text.lstrip()
            log_info("LLM", f"流式生成完成，共{sentence_count}个句子 [耗时: {stream_elapsed*1000:.1f}ms]", indent=0)
            log_info("LLM", f"完整回答: {full_response_text}", indent=1)
            
            return full_response_text
            
        except Exception as e:
            stream_elapsed = time.time() - stream_start_time
            log_error("LLM", f"流式生成错误: {e} [耗时: {stream_elapsed*1000:.1f}ms]", indent=0)
            # 如果已经生成了部分内容，返回已生成的内容
            if full_response_text:
                log_warn("LLM", f"返回已生成的部分内容: {full_response_text[:50]}...", indent=1)
                return full_response_text
            import traceback
            traceback.print_exc()
            raise

