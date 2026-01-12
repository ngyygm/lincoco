"""
TTS文件缓存管理器

提供TTS音频的持久化文件缓存功能，按语音（voice）组织目录，使用字典索引加快查询速度
"""

import os
import json
import hashlib
import threading
import random
from pathlib import Path
from typing import Optional, Dict, Callable, List
from pydub import AudioSegment

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.llm.llm_openai_stream import get_text_length


class TTSFileCache:
    """TTS文件缓存管理器（线程安全，带字典索引）"""
    
    def __init__(self, cache_dir: str, enabled: bool = True, max_length: int = 30, engine_name: str = "default", natural_audio_count: int = 0):
        """
        初始化TTS文件缓存管理器
        
        Args:
            cache_dir: 缓存目录路径（相对于项目根目录或绝对路径）
            enabled: 是否启用文件缓存
            max_length: 最大缓存长度（中文按字符数，英文按单词数，超过此长度的TTS音频不保存）
            engine_name: TTS引擎名称（如 "edge_tts"），用于区分不同引擎的缓存，会在cache_dir下创建{engine_name}_cache子目录
            natural_audio_count: 自然音频数量（0=不启用，>0表示同一文本保存多个不同版本的音频，每次随机选择）
        """
        self.enabled = enabled
        self.engine_name = engine_name
        self.max_length = max_length
        self.natural_audio_count = max(0, natural_audio_count)  # 确保非负
        self._lock = threading.Lock()
        
        # 构建完整的缓存目录路径：cache_dir/{engine_name}_cache/
        base_cache_dir = Path(cache_dir)
        self.cache_dir = base_cache_dir / f"{engine_name}_cache"
        
        # 字典索引：按语音存储，voice -> {文本内容 -> 文件名}
        # 注意：对于自然音频，索引中存储的是基础文件名（不含_id后缀），实际文件可能有多个版本
        self._indexes: Dict[str, Dict[str, str]] = {}
        
        # 确保缓存目录存在
        if self.enabled:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                natural_info = f", 自然音频数量: {self.natural_audio_count}" if self.natural_audio_count > 0 else ""
                log_debug("TTS", f"TTS文件缓存目录: {self.cache_dir.absolute()} [引擎: {engine_name}, 最大字词长度: {self.max_length}{natural_info}]", indent=0)
            except Exception as e:
                log_error("TTS", f"创建缓存目录失败: {e}", indent=0)
                self.enabled = False
    
    def _get_index_file(self, voice: str) -> Path:
        """
        获取指定语音的索引文件路径
        
        Args:
            voice: 语音名称
            
        Returns:
            索引文件路径
        """
        voice_dir = self.cache_dir / voice
        return voice_dir / "_index.json"
    
    def _load_index(self, voice: str) -> Dict[str, str]:
        """
        从文件加载指定语音的字典索引
        
        Args:
            voice: 语音名称
            
        Returns:
            索引字典：文本内容 -> 文件名
        """
        index_file = self._get_index_file(voice)
        if not index_file.exists():
            return {}
        
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                index = json.load(f)
            log_debug("TTS", f"加载语音 {voice} 的缓存索引: {len(index)} 条记录", indent=1)
            return index
        except Exception as e:
            log_warn("TTS", f"加载语音 {voice} 的缓存索引失败: {e}", indent=1)
            return {}
    
    def _save_index(self, voice: str, index: Dict[str, str]):
        """
        保存指定语音的字典索引到文件
        
        Args:
            voice: 语音名称
            index: 索引字典
        """
        index_file = self._get_index_file(voice)
        try:
            # 确保语音目录存在
            index_file.parent.mkdir(parents=True, exist_ok=True)
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_error("TTS", f"保存语音 {voice} 的缓存索引失败: {e}", indent=1)
    
    def _get_index(self, voice: str) -> Dict[str, str]:
        """
        获取指定语音的索引字典（懒加载）
        
        Args:
            voice: 语音名称
            
        Returns:
            索引字典：文本内容 -> 文件名
        """
        if voice not in self._indexes:
            self._indexes[voice] = self._load_index(voice)
        return self._indexes[voice]
    
    def _get_cache_path(self, text: str, voice: str, audio_id: Optional[int] = None) -> Path:
        """
        根据文本和语音生成缓存文件路径
        
        Args:
            text: 文本内容
            voice: 语音名称（用于组织目录结构）
            audio_id: 音频ID（用于自然音频，如果为None则不添加后缀）
            
        Returns:
            缓存文件路径
        """
        # 使用文本内容的MD5哈希作为文件名
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        voice_dir = self.cache_dir / voice
        if audio_id is not None:
            return voice_dir / f"{text_hash}_{audio_id}.mp3"
        else:
            return voice_dir / f"{text_hash}.mp3"
    
    def _get_filename_from_index(self, text: str, voice: str) -> Optional[str]:
        """
        从字典索引获取基础文件名（不含.mp3扩展名和_id后缀）
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            基础文件名（不含路径、.mp3扩展名和_id后缀），如果不存在则返回None
        """
        index = self._get_index(voice)
        value = index.get(text)
        
        if not value:
            return None
        
        # 兼容新格式：如果索引中存储的是字典格式
        if isinstance(value, dict):
            return value.get("hash")
        
        # 兼容旧格式：如果索引中存储的是字符串
        filename = value
        if filename.endswith('.mp3'):
            filename = filename[:-4]
        return filename
    
    def _get_saved_ids_from_index(self, text: str, voice: str) -> List[int]:
        """
        从字典索引获取已保存的音频版本ID列表
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            已保存的ID列表，如果不存在或不是自然音频模式则返回空列表
        """
        index = self._get_index(voice)
        value = index.get(text)
        
        if not value:
            return []
        
        # 兼容新格式：如果索引中存储的是字典格式
        if isinstance(value, dict):
            return value.get("saved_ids", [])
        
        # 兼容旧格式：如果索引中存储的是字符串，返回空列表(不支持版本追踪)
        return []
    
    def _find_all_audio_files(self, text: str, voice: str) -> List[Path]:
        """
        查找指定文本的所有音频文件（包括自然音频的不同版本）
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            所有匹配的音频文件路径列表
        """
        # 直接计算文本的MD5哈希（与_get_cache_path中的逻辑一致）
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        
        voice_dir = self.cache_dir / voice
        if not voice_dir.exists():
            return []
        
        # 查找所有匹配的文件（带_id后缀的）
        # 匹配模式：{text_hash}_{id}.mp3
        # 注意：为了兼容旧缓存，也查找无后缀的基础文件
        audio_files = []
        
        # 先查找基础文件（无_id后缀，兼容旧缓存）
        base_file = voice_dir / f"{text_hash}.mp3"
        if base_file.exists() and base_file.is_file():
            audio_files.append(base_file)
        
        # 查找所有带_id后缀的文件
        # 如果natural_audio_count > 0，查找更多可能的ID（考虑配置变更的情况）
        max_id = max(self.natural_audio_count + 10, 20) if self.natural_audio_count > 0 else 10
        for i in range(max_id):
            id_file = voice_dir / f"{text_hash}_{i}.mp3"
            if id_file.exists() and id_file.is_file():
                audio_files.append(id_file)
        
        return audio_files
    
    def _get_base_hash_from_index(self, text: str, voice: str) -> Optional[str]:
        """
        从索引获取基础哈希值（用于查找文件）
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            基础哈希值（不含扩展名），如果索引中没有则通过计算文本MD5得到
        """
        # 先从索引获取
        base_name = self._get_filename_from_index(text, voice)
        if base_name:
            return base_name
        
        # 如果索引中没有，直接计算文本的MD5哈希
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        return text_hash
    
    def exists(self, text: str, voice: str) -> bool:
        """
        检查缓存是否存在（先查字典索引，再查文件系统）
        如果启用了自然音频，需要检查文件数量是否达到预定数量
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            True表示缓存存在且数量足够，False表示不存在或数量不足
        """
        if not self.enabled:
            return False
        
        # 如果启用了自然音频，需要检查文件数量是否达到预定数量
        if self.natural_audio_count > 0:
            audio_files = self._find_all_audio_files(text, voice)
            # 只有当文件数量达到预定数量时，才认为缓存存在
            return len(audio_files) >= self.natural_audio_count
        
        # 未启用自然音频，使用_find_all_audio_files查找（兼容带_id后缀的文件）
        audio_files = self._find_all_audio_files(text, voice)
        # 只要找到至少一个文件就认为缓存存在
        return len(audio_files) > 0
    
    def load(self, text: str, voice: str) -> Optional[AudioSegment]:
        """
        从缓存加载音频（先查字典索引，再查文件系统）
        如果启用了自然音频，需要检查文件数量是否达到预定数量，达到后才随机选择一个版本
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            AudioSegment对象，如果加载失败或数量不足则返回None
        """
        if not self.enabled:
            return None
        
        # 如果启用了自然音频，需要检查文件数量是否达到预定数量
        if self.natural_audio_count > 0:
            audio_files = self._find_all_audio_files(text, voice)
            # 如果文件数量未达到预定数量，返回None（需要生成新文件）
            if len(audio_files) < self.natural_audio_count:
                log_debug("TTS", f"自然音频缓存数量不足: {voice}/{text} [当前: {len(audio_files)}, 需要: {self.natural_audio_count}]", indent=1)
                return None
            
            # 文件数量达到预定数量，随机选择一个文件
            selected_file = random.choice(audio_files)
            
            with self._lock:
                try:
                    audio = AudioSegment.from_mp3(str(selected_file))
                    log_debug("TTS", f"从缓存加载音频(自然音频): {voice}/{selected_file.name} [长度: {len(audio)}ms, 共{len(audio_files)}个版本]", indent=1)
                    return audio
                except Exception as e:
                    log_warn("TTS", f"加载缓存文件失败: {e}", indent=1)
                    return None
        
        # 未启用自然音频，使用_find_all_audio_files查找（兼容带_id后缀的文件）
        audio_files = self._find_all_audio_files(text, voice)
        if not audio_files:
            return None
        
        # 选择第一个找到的文件（如果有多个，优先选择基础文件，否则选择第一个）
        # 基础文件（无_id后缀）优先
        base_file = None
        for file_path in audio_files:
            file_name = file_path.name
            # 检查是否是基础文件（无_id后缀）
            text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
            if file_name == f"{text_hash}.mp3":
                base_file = file_path
                break
        
        # 如果找到基础文件，使用它；否则使用第一个文件
        selected_file = base_file if base_file else audio_files[0]
        
        with self._lock:
            try:
                audio = AudioSegment.from_mp3(str(selected_file))
                log_debug("TTS", f"从缓存加载音频: {voice}/{selected_file.name} [长度: {len(audio)}ms]", indent=1)
                return audio
            except Exception as e:
                log_warn("TTS", f"加载缓存文件失败: {e}", indent=1)
                return None
    
    def save(self, text: str, audio: AudioSegment, voice: str) -> bool:
        """
        保存音频到缓存（同时更新字典索引）
        如果启用了自然音频，会检查已有版本数量，如果未达到上限则保存新版本
        
        Args:
            text: 文本内容
            audio: AudioSegment对象
            voice: 语音名称
            
        Returns:
            True表示保存成功，False表示保存失败
        """
        if not self.enabled:
            return False
        
        # 使用get_text_length()计算文本长度（中文按字符，英文按单词）
        text_length = get_text_length(text)
        if text_length > self.max_length:
            log_debug("TTS", f"文本长度 {text_length} 超过最大缓存长度 {self.max_length}，不保存缓存", indent=1)
            return False
        
        with self._lock:
            try:
                # 确保目录存在（包括语音子目录）
                voice_dir = self.cache_dir / voice
                voice_dir.mkdir(parents=True, exist_ok=True)
                
                # 获取基础文件名（不含_id后缀）
                base_path = self._get_cache_path(text, voice)
                base_filename = base_path.name
                
                # 如果启用了自然音频，需要检查已有版本数量
                if self.natural_audio_count > 0:
                    # 优先从索引获取已保存的ID列表（更可靠）
                    existing_ids = set()
                    saved_ids_from_index = self._get_saved_ids_from_index(text, voice)

                    audio_files = self._find_all_audio_files(text, voice)

                    for file_path in audio_files:
                        file_name = file_path.name
                        base_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
                        if file_name == base_filename:
                            existing_ids.add(0)
                        elif file_name.startswith(f"{base_hash}_") and file_name.endswith('.mp3'):
                            try:
                                id_str = file_name[len(f"{base_hash}_"):-4]  # 去掉前缀和后缀
                                audio_id = int(id_str)
                                existing_ids.add(audio_id)
                            except ValueError:
                                pass
                    
                    # 如果已达到上限，不保存新版本
                    if len(audio_files) >= self.natural_audio_count:
                        log_debug("TTS", f"自然音频版本数已达上限({self.natural_audio_count})，不保存新版本: {voice}/{base_filename}", indent=1)
                        return False
                    
                    # 找到下一个可用的ID（从0开始）
                    next_id = 0
                    while next_id in existing_ids:
                        next_id += 1
                    
                    # 使用带ID的文件名保存（所有文件都使用_id后缀，包括第一个文件使用_0）
                    cache_path = self._get_cache_path(text, voice, audio_id=next_id)
                    filename = cache_path.name
                else:
                    # 未启用自然音频，使用标准路径（无ID后缀）
                    cache_path = base_path
                    filename = base_filename
                
                # 导出为MP3格式
                audio.export(str(cache_path), format="mp3")
                
                # 更新字典索引
                index = self._get_index(voice)
                # 去掉.mp3扩展名，只保留文件名
                base_name_without_ext = base_filename[:-4] if base_filename.endswith('.mp3') else base_filename
                
                # 如果启用了自然音频，使用新的索引结构来追踪版本数量
                if self.natural_audio_count > 0:
                    # 获取当前索引值
                    old_value = index.get(text)
                    
                    # 构建新的索引值（字典格式）
                    new_index_value = {
                        "hash": base_name_without_ext,
                        "saved_ids": list(existing_ids | {next_id})  # 包含新ID的列表
                    }
                    
                    # 更新索引
                    index[text] = new_index_value
                    self._save_index(voice, index)
                else:
                    # 未启用自然音频，使用旧的简单格式
                    old_value = index.get(text)
                    if old_value != base_name_without_ext and (not isinstance(old_value, dict) or old_value.get("hash") != base_name_without_ext):
                        index[text] = base_name_without_ext
                        self._save_index(voice, index)
                
                log_debug("TTS", f"保存音频到缓存: {voice}/{filename} [长度: {len(audio)}ms]", indent=1)
                return True
            except Exception as e:
                log_error("TTS", f"保存缓存文件失败: {e}", indent=1)
                return False
    
    def get_cache_stats(self) -> dict:
        """
        获取缓存统计信息（按语音目录统计）
        
        Returns:
            包含缓存统计的字典
        """
        if not self.enabled:
            return {
                "enabled": False,
                "cache_dir": str(self.cache_dir),
                "file_count": 0,
                "total_size": 0,
                "index_count": 0,
                "voice_stats": {}
            }
        
        try:
            # 统计所有语音目录
            voice_stats = {}
            total_files = 0
            total_size = 0
            
            # 遍历所有语音子目录
            total_index_count = 0
            for voice_dir in self.cache_dir.iterdir():
                if voice_dir.is_dir():
                    voice_name = voice_dir.name
                    # 排除索引文件
                    cache_files = [f for f in voice_dir.glob("*.mp3") if f.name != "_index.json"]
                    file_count = len(cache_files)
                    folder_size = sum(f.stat().st_size for f in cache_files if f.is_file())
                    
                    # 获取该语音的索引数量
                    index = self._get_index(voice_name)
                    index_count = len(index)
                    total_index_count += index_count
                    
                    voice_stat = {
                        "file_count": file_count,
                        "total_size": folder_size,
                        "total_size_mb": folder_size / (1024 * 1024),
                        "index_count": index_count
                    }
                    voice_stats[voice_name] = voice_stat
                    total_files += file_count
                    total_size += folder_size
            
            return {
                "enabled": True,
                "cache_dir": str(self.cache_dir.absolute()),
                "file_count": total_files,
                "total_size": total_size,
                "total_size_mb": total_size / (1024 * 1024),
                "index_count": total_index_count,
                "voice_stats": voice_stats
            }
        except Exception as e:
            log_warn("TTS", f"获取缓存统计失败: {e}", indent=1)
            return {
                "enabled": True,
                "cache_dir": str(self.cache_dir),
                "file_count": 0,
                "total_size": 0,
                "index_count": sum(len(idx) for idx in self._indexes.values()),
                "voice_stats": {},
                "error": str(e)
            }
    
    def clear_cache(self, voice: Optional[str] = None):
        """
        清空缓存文件（同时清空字典索引）
        
        Args:
            voice: 如果指定，只清空该语音的缓存；否则清空所有语音的缓存
        """
        if not self.enabled:
            return
        
        with self._lock:
            try:
                deleted_count = 0
                
                if voice:
                    # 只清空指定语音的缓存
                    voice_dir = self.cache_dir / voice
                    if voice_dir.exists() and voice_dir.is_dir():
                        cache_files = list(voice_dir.glob("*.mp3"))
                        deleted_filenames = set()
                        for cache_file in cache_files:
                            try:
                                cache_file.unlink()
                                deleted_count += 1
                                deleted_filenames.add(cache_file.name)
                            except Exception as e:
                                log_warn("TTS", f"删除缓存文件失败 {cache_file.name}: {e}", indent=1)
                        
                        # 从索引中删除该语音的条目（通过匹配文件名）
                        index = self._get_index(voice)
                        texts_to_remove = []
                        for text, value in index.items():
                            # 兼容新格式（字典）和旧格式（字符串）
                            if isinstance(value, dict):
                                filename = value.get("hash", "")
                            else:
                                filename = value
                            
                            if filename in deleted_filenames:
                                texts_to_remove.append(text)
                        for text in texts_to_remove:
                            del index[text]
                        
                        if deleted_count > 0:
                            self._save_index(voice, index)
                            # 从内存中删除该语音的索引
                            if voice in self._indexes:
                                del self._indexes[voice]
                        log_debug("TTS", f"已清空语音 {voice} 的缓存，删除 {deleted_count} 个文件", indent=0)
                else:
                    # 清空所有缓存
                    for voice_dir in self.cache_dir.iterdir():
                        if voice_dir.is_dir():
                            # 删除所有mp3文件（排除索引文件）
                            cache_files = [f for f in voice_dir.glob("*.mp3") if f.name != "_index.json"]
                            for cache_file in cache_files:
                                try:
                                    cache_file.unlink()
                                    deleted_count += 1
                                except Exception as e:
                                    log_warn("TTS", f"删除缓存文件失败 {cache_file.name}: {e}", indent=1)
                            
                            # 删除索引文件
                            index_file = voice_dir / "_index.json"
                            if index_file.exists():
                                try:
                                    index_file.unlink()
                                except Exception as e:
                                    log_warn("TTS", f"删除索引文件失败: {e}", indent=1)
                    
                    # 清空内存中的索引
                    self._indexes.clear()
                    log_debug("TTS", f"已清空所有缓存，删除 {deleted_count} 个文件", indent=0)
            except Exception as e:
                log_error("TTS", f"清空缓存失败: {e}", indent=0)
    
    def check_and_repair_missing_files(self, generate_func: Optional[Callable[[str, str], Optional[AudioSegment]]] = None):
        """
        检查并修复缺失的缓存文件
        
        检查索引文件中存在但实际文件丢失的情况。如果提供了生成函数，会在后台线程中重新生成这些文件。
        如果启用了自然音频，还会检查每个文本的文件数量是否达到预定数量，不足的也会修复。
        
        Args:
            generate_func: 可选的生成函数，接受(text: str, voice: str)参数，返回AudioSegment对象
                          如果为None，则只检查并报告缺失的文件，不进行修复
        """
        if not self.enabled:
            return
        
        try:
            total_missing = 0
            total_index_count = 0
            missing_items = []  # [(voice, text, filename), ...]
            insufficient_items = []  # [(voice, text, current_count, target_count), ...] 用于自然音频数量不足的情况
            
            # 遍历所有语音目录，检查索引文件
            if not self.cache_dir.exists():
                return
            
            for voice_dir in self.cache_dir.iterdir():
                if not voice_dir.is_dir():
                    continue
                
                voice_name = voice_dir.name
                # 直接加载索引文件（不依赖内存缓存）
                index = self._load_index(voice_name)
                
                if not index:
                    continue
                
                total_index_count += len(index)
                
                # 检查每个索引条目
                for text, value in index.items():
                    # 兼容新格式（字典）和旧格式（字符串）
                    if isinstance(value, dict):
                        base_filename = value.get("hash", "")
                        saved_ids = set(value.get("saved_ids", []))
                    else:
                        base_filename = value
                        saved_ids = set()  # 旧格式不支持版本追踪
                    
                    # 使用_find_all_audio_files查找所有相关文件（包括带_id后缀的）
                    # 因为索引中存储的是基础文件名（不含扩展名），但实际文件可能带_id后缀和.mp3扩展名
                    audio_files = self._find_all_audio_files(text, voice_name)
                    
                    # 如果未启用自然音频，只要找到至少一个文件就认为存在
                    # 如果启用了自然音频，需要检查文件数量是否达到预定数量
                    if self.natural_audio_count > 0:
                        # 启用了自然音频，检查文件数量
                        # 优先从索引中的saved_ids获取数量（更准确）
                        if isinstance(value, dict):
                            current_count = len(saved_ids)
                            # 确保索引中的ID对应的文件都存在
                            actual_files_count = len(audio_files)
                            if actual_files_count != current_count:
                                # 索引和文件系统不一致，使用文件系统的数量
                                current_count = actual_files_count
                        else:
                            # 旧格式，使用文件系统的数量
                            current_count = len(audio_files)
                        
                        if current_count < self.natural_audio_count:
                            # 文件数量不足
                            insufficient_items.append((voice_name, text, current_count, self.natural_audio_count))
                    else:
                        # 未启用自然音频，只要找到至少一个文件就认为存在
                        if len(audio_files) == 0:
                            # 没有找到任何文件，算作缺失
                            total_missing += 1
                            missing_items.append((voice_name, text, base_filename))
            
            # 报告统计信息
            if total_index_count > 0:
                log_debug("TTS", f"缓存检查 [{self.engine_name}]：共 {total_index_count} 条缓存记录", indent=0)
            
            if total_missing > 0:
                log_warn("TTS", f"发现 {total_missing} 个缺失的缓存文件 [{self.engine_name}]（索引中有记录但文件不存在）", indent=1)
            
            if insufficient_items:
                log_warn("TTS", f"发现 {len(insufficient_items)} 个自然音频缓存数量不足 [{self.engine_name}]（需要修复到预定数量）", indent=1)
            
            # 合并需要修复的项目
            all_repair_items = []
            if total_missing > 0:
                # 缺失的文件，需要修复
                for voice, text, filename in missing_items:
                    all_repair_items.append((voice, text, "missing"))
            
            if insufficient_items:
                # 数量不足的，需要补充到预定数量
                for voice, text, current_count, target_count in insufficient_items:
                    # 计算需要补充的数量
                    need_count = target_count - current_count
                    for _ in range(need_count):
                        all_repair_items.append((voice, text, "insufficient"))
            
            if all_repair_items:
                # 如果提供了生成函数，启动后台线程修复
                if generate_func is not None:
                    log_debug("TTS", f"启动后台线程修复 {len(all_repair_items)} 个缓存文件 [{self.engine_name}]（缺失: {total_missing}, 数量不足: {len(insufficient_items)}）", indent=1)
                    repair_thread = threading.Thread(
                        target=self._repair_missing_files_in_thread,
                        args=(all_repair_items, generate_func),
                        daemon=True,
                        name=f"TTS-Cache-Repair-{self.engine_name}"
                    )
                    repair_thread.start()
            elif total_index_count > 0:
                log_debug("TTS", f"所有缓存文件完整 [{self.engine_name}]", indent=1)
                    
        except Exception as e:
            log_error("TTS", f"检查缓存文件失败: {e}", indent=0)
    
    def _repair_missing_files_in_thread(self, repair_items: list, generate_func: Callable[[str, str], Optional[AudioSegment]]):
        """
        在后台线程中修复缺失的缓存文件
        
        Args:
            repair_items: 需要修复的项目列表，每个元素为(voice, text, reason)
                         reason可以是"missing"（文件缺失）或"insufficient"（数量不足）
            generate_func: 生成函数，接受(text, voice)参数，返回AudioSegment
        """
        try:
            repaired_count = 0
            failed_count = 0
            
            for voice, text, reason in repair_items:
                try:
                    # 调用生成函数生成音频
                    audio_segment = generate_func(text, voice)
                    
                    if audio_segment is None:
                        log_warn("TTS", f"生成音频失败: {voice}/{text[:30]}...", indent=2)
                        failed_count += 1
                        continue
                    
                    repaired_count += 1
                    log_debug("TTS", f"已修复 {repaired_count}/{len(repair_items)} 个缓存文件", indent=2)
                        
                except Exception as e:
                    failed_count += 1
                    log_error("TTS", f"修复文件失败 {voice}/{text[:30]}...: {e}", indent=2)
            
            log_debug("TTS", f"缓存修复完成 [{self.engine_name}]：成功 {repaired_count} 个，失败 {failed_count} 个", indent=1)
            
        except Exception as e:
            log_error("TTS", f"缓存修复线程异常: {e}", indent=1)