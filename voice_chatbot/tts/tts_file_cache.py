"""
TTS文件缓存管理器

提供TTS音频的持久化文件缓存功能，按语音（voice）组织目录，使用字典索引加快查询速度
"""

import os
import json
import hashlib
import threading
from pathlib import Path
from typing import Optional, Dict, Callable
from pydub import AudioSegment

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.llm.llm_openai_stream import get_text_length


class TTSFileCache:
    """TTS文件缓存管理器（线程安全，带字典索引）"""
    
    def __init__(self, cache_dir: str, enabled: bool = True, max_length: int = 30, engine_name: str = "default"):
        """
        初始化TTS文件缓存管理器
        
        Args:
            cache_dir: 缓存目录路径（相对于项目根目录或绝对路径）
            enabled: 是否启用文件缓存
            max_length: 最大缓存长度（中文按字符数，英文按单词数，超过此长度的TTS音频不保存）
            engine_name: TTS引擎名称（如 "edge_tts"），用于区分不同引擎的缓存，会在cache_dir下创建{engine_name}_cache子目录
        """
        self.enabled = enabled
        self.engine_name = engine_name
        self.max_length = max_length
        self._lock = threading.Lock()
        
        # 构建完整的缓存目录路径：cache_dir/{engine_name}_cache/
        base_cache_dir = Path(cache_dir)
        self.cache_dir = base_cache_dir / f"{engine_name}_cache"
        
        # 字典索引：按语音存储，voice -> {文本内容 -> 文件名}
        self._indexes: Dict[str, Dict[str, str]] = {}
        
        # 确保缓存目录存在
        if self.enabled:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                log_info("TTS", f"TTS文件缓存目录: {self.cache_dir.absolute()} [引擎: {engine_name}, 最大字词长度: {self.max_length}]", indent=0)
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
    
    def _get_cache_path(self, text: str, voice: str) -> Path:
        """
        根据文本和语音生成缓存文件路径
        
        Args:
            text: 文本内容
            voice: 语音名称（用于组织目录结构）
            
        Returns:
            缓存文件路径
        """
        # 使用文本内容的MD5哈希作为文件名
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
        voice_dir = self.cache_dir / voice
        return voice_dir / f"{text_hash}.mp3"
    
    def _get_filename_from_index(self, text: str, voice: str) -> Optional[str]:
        """
        从字典索引获取文件名
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            文件名（不含路径），如果不存在则返回None
        """
        index = self._get_index(voice)
        return index.get(text)
    
    def exists(self, text: str, voice: str) -> bool:
        """
        检查缓存是否存在（先查字典索引，再查文件系统）
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            True表示缓存存在，False表示不存在
        """
        if not self.enabled:
            return False
        
        # 先查字典索引
        filename = self._get_filename_from_index(text, voice)
        if filename:
            cache_path = self.cache_dir / voice / filename
            if cache_path.exists() and cache_path.is_file():
                return True
        
        # 如果索引中没有，尝试通过文件路径查找（兼容旧缓存）
        cache_path = self._get_cache_path(text, voice)
        return cache_path.exists() and cache_path.is_file()
    
    def load(self, text: str, voice: str) -> Optional[AudioSegment]:
        """
        从缓存加载音频（先查字典索引，再查文件系统）
        
        Args:
            text: 文本内容
            voice: 语音名称
            
        Returns:
            AudioSegment对象，如果加载失败则返回None
        """
        if not self.enabled:
            return None
        
        # 先查字典索引获取文件名
        filename = self._get_filename_from_index(text, voice)
        if filename:
            cache_path = self.cache_dir / voice / filename
        else:
            # 如果索引中没有，使用标准路径（兼容旧缓存）
            cache_path = self._get_cache_path(text, voice)
        
        with self._lock:
            try:
                if not cache_path.exists():
                    return None
                
                # 从文件加载音频
                audio = AudioSegment.from_mp3(str(cache_path))
                log_info("TTS", f"从缓存加载音频: {voice}/{cache_path.name} [长度: {len(audio)}ms]", indent=1)
                return audio
            except Exception as e:
                log_warn("TTS", f"加载缓存文件失败: {e}", indent=1)
                return None
    
    def save(self, text: str, audio: AudioSegment, voice: str) -> bool:
        """
        保存音频到缓存（同时更新字典索引）
        
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
        
        cache_path = self._get_cache_path(text, voice)
        filename = cache_path.name
        
        with self._lock:
            try:
                # 确保目录存在（包括语音子目录）
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 导出为MP3格式
                audio.export(str(cache_path), format="mp3")
                
                # 更新字典索引
                index = self._get_index(voice)
                index[text] = filename
                self._save_index(voice, index)
                
                log_info("TTS", f"保存音频到缓存: {voice}/{filename} [长度: {len(audio)}ms]", indent=1)
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
                        for text, filename in index.items():
                            if filename in deleted_filenames:
                                texts_to_remove.append(text)
                        for text in texts_to_remove:
                            del index[text]
                        
                        if deleted_count > 0:
                            self._save_index(voice, index)
                            # 从内存中删除该语音的索引
                            if voice in self._indexes:
                                del self._indexes[voice]
                        log_info("TTS", f"已清空语音 {voice} 的缓存，删除 {deleted_count} 个文件", indent=0)
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
                    log_info("TTS", f"已清空所有缓存，删除 {deleted_count} 个文件", indent=0)
            except Exception as e:
                log_error("TTS", f"清空缓存失败: {e}", indent=0)
    
    def check_and_repair_missing_files(self, generate_func: Optional[Callable[[str, str], Optional[AudioSegment]]] = None):
        """
        检查并修复缺失的缓存文件
        
        检查索引文件中存在但实际文件丢失的情况。如果提供了生成函数，会在后台线程中重新生成这些文件。
        
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
                
                # 检查每个索引条目对应的文件是否存在
                for text, filename in index.items():
                    file_path = voice_dir / filename
                    if not file_path.exists() or not file_path.is_file():
                        total_missing += 1
                        missing_items.append((voice_name, text, filename))
            
            # 报告统计信息
            if total_index_count > 0:
                log_info("TTS", f"缓存检查 [{self.engine_name}]：共 {total_index_count} 条缓存记录", indent=0)
            
            if total_missing > 0:
                log_warn("TTS", f"发现 {total_missing} 个缺失的缓存文件 [{self.engine_name}]（索引中有记录但文件不存在）", indent=1)
                
                # 如果提供了生成函数，启动后台线程修复
                if generate_func is not None:
                    log_info("TTS", f"启动后台线程重新生成 {total_missing} 个缺失的缓存文件 [{self.engine_name}]", indent=1)
                    repair_thread = threading.Thread(
                        target=self._repair_missing_files_in_thread,
                        args=(missing_items, generate_func),
                        daemon=True,
                        name=f"TTS-Cache-Repair-{self.engine_name}"
                    )
                    repair_thread.start()
            elif total_index_count > 0:
                log_info("TTS", f"所有缓存文件完整 [{self.engine_name}]", indent=1)
                    
        except Exception as e:
            log_error("TTS", f"检查缓存文件失败: {e}", indent=0)
    
    def _repair_missing_files_in_thread(self, missing_items: list, generate_func: Callable[[str, str], Optional[AudioSegment]]):
        """
        在后台线程中修复缺失的缓存文件
        
        Args:
            missing_items: 缺失文件列表，每个元素为(voice, text, filename)
            generate_func: 生成函数，接受(text, voice)参数，返回AudioSegment
        """
        try:
            repaired_count = 0
            failed_count = 0
            
            for voice, text, filename in missing_items:
                try:
                    # 调用生成函数生成音频
                    audio_segment = generate_func(text, voice)
                    
                    if audio_segment is None:
                        log_warn("TTS", f"生成音频失败: {voice}/{text[:30]}...", indent=2)
                        failed_count += 1
                        continue
                    
                    # 保存到缓存
                    success = self.save(text, audio_segment, voice)
                    
                    if success:
                        repaired_count += 1
                        if repaired_count % 10 == 0:
                            log_info("TTS", f"已修复 {repaired_count}/{len(missing_items)} 个缺失文件", indent=2)
                    else:
                        failed_count += 1
                        log_warn("TTS", f"保存缓存失败: {voice}/{text[:30]}...", indent=2)
                        
                except Exception as e:
                    failed_count += 1
                    log_error("TTS", f"修复文件失败 {voice}/{text[:30]}...: {e}", indent=2)
            
            log_info("TTS", f"缓存修复完成 [{self.engine_name}]：成功 {repaired_count} 个，失败 {failed_count} 个", indent=1)
            
        except Exception as e:
            log_error("TTS", f"缓存修复线程异常: {e}", indent=1)
