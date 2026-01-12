"""
声纹验证模块

使用 3D-Speaker 进行声纹识别和验证
如果启用声纹锁，ASR 只对符合设定声纹的声音进行识别
"""

import os
import sys
import pathlib
import threading
import hashlib
import numpy as np
import torch
import torchaudio
from typing import Optional, List, Tuple
from pathlib import Path

# 添加 3D-Speaker 到路径
THREE_D_SPEAKER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "plugin", "3D-Speaker")
if os.path.exists(THREE_D_SPEAKER_PATH):
    sys.path.insert(0, THREE_D_SPEAKER_PATH)

try:
    from speakerlab.process.processor import FBank
    from speakerlab.utils.builder import dynamic_import
    HAS_3D_SPEAKER = True
except ImportError:
    HAS_3D_SPEAKER = False
    FBank = None
    dynamic_import = None

# 尝试导入 modelscope（可选，用于从 ModelScope 下载模型）
try:
    from modelscope.hub.snapshot_download import snapshot_download
    HAS_MODELSCOPE = True
except ImportError:
    HAS_MODELSCOPE = False
    snapshot_download = None

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


def _get_text_filename(text: str) -> str:
    """
    根据文本内容生成文件名（使用MD5哈希）
    
    Args:
        text: 文本内容
        
    Returns:
        文件名（不含扩展名）
    """
    # 使用MD5哈希生成文件名，确保相同文本总是生成相同文件名
    text_hash = hashlib.md5(text.encode('utf-8')).hexdigest()
    return f"text_{text_hash}"


class SpeakerVerification:
    """声纹验证器"""
    
    def __init__(self, config, asr_model=None):
        """
        初始化声纹验证器
        
        Args:
            config: 配置对象（Config实例）
            asr_model: ASR模型（可选，如果不提供则录制时自动加载）
        """
        self.config = config
        self.enabled = config.get("asr.speaker_verification.enabled", False)
        self.asr_model = asr_model  # 保存ASR模型引用
        
        if not self.enabled:
            log_info("SPE", "声纹锁功能已禁用")
            return
        
        if not HAS_3D_SPEAKER:
            log_error("SPE", "3D-Speaker 未安装或路径不正确，声纹锁功能将禁用")
            log_error("SPE", f"请确保 3D-Speaker 位于: {THREE_D_SPEAKER_PATH}", indent=1)
            self.enabled = False
            return
        
        # 从配置获取参数
        self.model_id = config.get("asr.speaker_verification.model_id", "iic/speech_campplus_sv_zh-cn_16k-common")
        base_enroll_audio_path = config.get("asr.speaker_verification.enroll_audio_path", "")
        self.similarity_threshold = config.get("asr.speaker_verification.similarity_threshold", 0.5)
        self.min_audio_length = config.get("asr.speaker_verification.min_audio_length", 1.0)
        self.cache_embedding = config.get("asr.speaker_verification.cache_embedding", True)
        
        # 获取用户名
        self.user_name = config.get("system.user_name", "")
        if not self.user_name:
            log_warn("SPE", "未配置用户名（system.user_name），将使用默认用户名'default'")
            self.user_name = "default"
        
        log_info("SPE", f"当前用户名: {self.user_name}")
        
        # 验证参数
        auto_record = config.get("asr.speaker_verification.auto_record", True)
        
        # 如果路径为空，使用默认路径（speaker_verification_audio目录）
        if not base_enroll_audio_path:
            if auto_record:
                # 使用默认路径（speaker_verification_audio目录）
                default_path = Path("speaker_verification_audio")
                base_enroll_audio_path = str(default_path)
                log_info("SPE", f"未配置注册音频路径，使用默认路径: {base_enroll_audio_path}")
            else:
                log_error("SPE", "声纹锁已启用但未配置注册音频路径（asr.speaker_verification.enroll_audio_path），且自动录制已禁用")
                self.enabled = False
                return
        
        # 处理相对路径：如果是相对路径，确保相对于项目根目录
        base_enroll_path = Path(base_enroll_audio_path)
        if not base_enroll_path.is_absolute():
            # 获取项目根目录（voice_chatbot的父目录）
            project_root = Path(__file__).parent.parent.parent
            base_enroll_audio_path = str(project_root / base_enroll_path)
            log_debug("SPE", f"注册音频基础路径（相对路径已转换为绝对路径）: {base_enroll_audio_path}", indent=1)
        
        # 根据用户名构建用户特定的注册音频路径
        base_enroll_path = Path(base_enroll_audio_path)
        user_enroll_path = base_enroll_path / self.user_name
        self.enroll_audio_path = str(user_enroll_path)
        log_info("SPE", f"用户注册音频路径: {self.enroll_audio_path}")
        
        # 获取朗读列表配置
        reading_texts = config.get("asr.speaker_verification.reading_texts", [])
        if not reading_texts:
            # 如果没有配置朗读列表，使用默认的3个场景
            reading_texts = [
                "欢迎使用语音助手系统，今天天气真不错。",
                "语音识别技术越来越智能了，这真的很令人兴奋。",
                "这是一个安静的测试，请确保声音清晰可辨。",
            ]
            log_warn("SPE", "未配置朗读列表（asr.speaker_verification.reading_texts），使用默认列表", indent=1)
        
        self.reading_texts = reading_texts
        log_info("SPE", f"配置了 {len(self.reading_texts)} 句朗读文本")
        
        # 延迟检查注册音频（不在初始化时执行，等待系统完全初始化后）
        # 注册音频检查将在 check_and_enroll_if_needed() 方法中执行
        self.auto_record = auto_record
        self.enrollment_checked = False  # 标记是否已检查过注册音频
        
        # 模型相关
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.embedding_model = None
        self.feature_extractor = None
        self.enroll_embeddings = []  # 注册音频的嵌入向量列表
        self.base_enroll_audio_path = base_enroll_audio_path  # 保存基础路径，用于用户名变更时重新构建路径
        
        # 只加载声纹模型，不加载注册音频（注册音频将在check_and_enroll_if_needed中加载）
        # 即使没有注册音频，也允许ASR继续工作，后续可以动态加载
        if not self._load_model():
            log_error("SPE", "声纹模型加载失败，声纹锁功能将禁用")
            self.enabled = False
            return
        
        log_info("SPE", "声纹模型加载完成，等待系统初始化完成后检查注册音频")
        log_info("SPE", "注意：即使没有注册音频，ASR也可以正常使用，声纹验证将在注册后自动启用", indent=1)
    
    def check_and_enroll_if_needed(self, asr_model=None, tts_engine=None, audio_player=None):
        """
        检查注册音频是否齐全，如果缺少则进行注册
        此方法应在系统完全初始化后调用（ASR、TTS等模块已加载）
        
        Args:
            asr_model: 已加载的ASR模型（可选，如果不提供则会在录制时加载）
            tts_engine: 已加载的TTS引擎（可选，如果不提供则会在录制时创建）
            audio_player: 已加载的音频播放器（可选，如果不提供则会在录制时创建）
        
        Returns:
            True表示检查/注册成功，False表示失败（但不禁用功能，允许后续重试）
        """
        if not self.enabled:
            return True  # 如果未启用，直接返回成功
        
        if self.enrollment_checked:
            return True  # 已经检查过了，直接返回
        
        # 更新ASR模型引用（如果提供）
        if asr_model is not None:
            self.asr_model = asr_model
            log_info("SPE", "使用已加载的ASR模型进行声纹注册", indent=1)
        
        # 检查注册音频是否齐全
        enroll_path = Path(self.enroll_audio_path)
        missing_indices = []
        
        if not enroll_path.exists():
            # 目录不存在，需要录制所有
            missing_indices = list(range(len(self.reading_texts)))
            log_info("SPE", f"用户注册音频路径不存在: {self.enroll_audio_path}，需要注册")
        elif enroll_path.is_file():
            # 如果是文件，检查文件大小是否为0
            if enroll_path.stat().st_size == 0:
                missing_indices = list(range(len(self.reading_texts)))
                log_info("SPE", "注册音频文件为空，需要注册")
        elif enroll_path.is_dir():
            # 检查是否所有句子都已录制（使用文本哈希匹配）
            existing_files = {f.stem for f in enroll_path.glob("*.wav")}  # 获取所有文件名（不含扩展名）
            
            for i, text in enumerate(self.reading_texts):
                expected_filename = _get_text_filename(text)
                if expected_filename not in existing_files:
                    missing_indices.append(i)
            
            if missing_indices:
                log_info("SPE", f"检测到缺少 {len(missing_indices)} 个音频文件，需要补录")
                for idx in missing_indices:
                    text = self.reading_texts[idx]
                    filename = _get_text_filename(text)
                    log_info("SPE", f"  缺少: {filename}.wav - {text}", indent=1)
            else:
                log_info("SPE", f"所有 {len(self.reading_texts)} 个音频文件已齐全")
        
        needs_recording = len(missing_indices) > 0
        self.missing_indices = missing_indices
        
        if needs_recording:
            if self.auto_record:
                log_info("SPE", "将启动声纹注册录制流程")
                # 保存TTS引擎和音频播放器引用（如果提供）
                self._tts_engine = tts_engine
                self._audio_player = audio_player
                # 尝试录制注册音频
                if not self._record_enroll_audio():
                    log_warn("SPE", "录制注册音频失败，但ASR可以继续使用，可稍后重试")
                    # 不禁用功能，允许后续重试
                    self.enrollment_checked = True
                    return False
            else:
                log_warn("SPE", f"注册音频路径不存在或为空: {self.enroll_audio_path}，且自动录制已禁用")
                # 不禁用功能，允许后续手动注册
                self.enrollment_checked = True
                return False
        else:
            # 如果不需要录制，加载已存在的注册音频
            if not self._load_enroll_audio():
                log_warn("SPE", "注册音频加载失败，声纹验证暂时禁用，但ASR可以继续使用")
                # 不禁用功能，允许后续重试
                self.enrollment_checked = True
                return False
        
        self.enrollment_checked = True
        log_info("SPE", f"声纹注册检查完成，已加载 {len(self.enroll_embeddings)} 个注册声纹")
        
        # 如果有注册音频，启用声纹验证
        if len(self.enroll_embeddings) > 0:
            self.enabled = True
            log_info("SPE", "声纹验证已启用")
        else:
            # 没有注册音频，但保持功能可用（允许后续动态加载）
            log_info("SPE", "声纹验证功能已准备就绪，等待注册音频")
        
        return True
    
    def _load_model(self) -> bool:
        """
        加载 3D-Speaker 模型
        
        Returns:
            True表示加载成功，False表示失败
        """
        try:
            # 模型配置（根据 3D-Speaker 的 infer_sv.py）
            model_configs = {
                'iic/speech_campplus_sv_zh-cn_16k-common': {
                    'obj': 'speakerlab.models.campplus.DTDNN.CAMPPlus',
                    'args': {'feat_dim': 80, 'embedding_size': 192},
                    'model_pt': 'campplus_cn_common.bin',
                    'revision': 'v1.0.0',
                },
                'iic/speech_eres2net_sv_zh-cn_16k-common': {
                    'obj': 'speakerlab.models.eres2net.ERes2Net_huge.ERes2Net',
                    'args': {'feat_dim': 80, 'embedding_size': 192},
                    'model_pt': 'pretrained_eres2net_aug.ckpt',
                    'revision': 'v1.0.5',
                },
                'iic/speech_eres2netv2_sv_zh-cn_16k-common': {
                    'obj': 'speakerlab.models.eres2net.ERes2NetV2.ERes2NetV2',
                    'args': {'feat_dim': 80, 'embedding_size': 192, 'baseWidth': 26, 'scale': 2, 'expansion': 2},
                    'model_pt': 'pretrained_eres2netv2.ckpt',
                    'revision': 'v1.0.1',
                },
            }
            
            if self.model_id not in model_configs:
                log_error("SPE", f"不支持的模型ID: {self.model_id}")
                log_error("SPE", f"支持的模型: {list(model_configs.keys())}", indent=1)
                return False
            
            conf = model_configs[self.model_id]
            
            # 下载模型（如果不存在）
            local_model_dir = os.path.join("pretrained", self.model_id.split('/')[1])
            local_model_dir = pathlib.Path(local_model_dir)
            local_model_dir.mkdir(exist_ok=True, parents=True)
            
            pretrained_model = local_model_dir / conf['model_pt']
            
            if not pretrained_model.exists():
                if not HAS_MODELSCOPE or snapshot_download is None:
                    log_error("SPE", "模型文件不存在且 ModelScope 未安装，无法下载模型")
                    log_error("SPE", "请安装 ModelScope: pip install modelscope", indent=1)
                    log_error("SPE", f"或手动将模型文件放置到: {pretrained_model}", indent=1)
                    return False
                
                log_info("SPE", f"正在从 ModelScope 下载模型: {self.model_id}...")
                try:
                    cache_dir = snapshot_download(self.model_id, revision=conf['revision'])
                    cache_dir = pathlib.Path(cache_dir)
                    
                    # 链接模型文件
                    for src in cache_dir.glob('*'):
                        if conf['model_pt'] in src.name:
                            dst = local_model_dir / src.name
                            if not dst.exists():
                                try:
                                    dst.symlink_to(src)
                                except:
                                    import shutil
                                    shutil.copy2(src, dst)
                            break
                except Exception as e:
                    log_error("SPE", f"模型下载失败: {e}")
                    return False
            
            # 加载模型权重
            pretrained_state = torch.load(pretrained_model, map_location='cpu')
            
            # 创建模型
            model = conf
            embedding_model = dynamic_import(model['obj'])(**model['args'])
            embedding_model.load_state_dict(pretrained_state)
            embedding_model.to(self.device)
            embedding_model.eval()
            
            self.embedding_model = embedding_model
            
            # 创建特征提取器
            self.feature_extractor = FBank(80, sample_rate=16000, mean_nor=True)
            
            log_info("SPE", f"声纹模型加载成功: {self.model_id}")
            return True
            
        except Exception as e:
            log_error("SPE", f"模型加载失败: {e}")
            import traceback
            log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
            return False
    
    def _load_enroll_audio(self) -> bool:
        """
        加载注册音频并提取嵌入向量
        
        Returns:
            True表示加载成功，False表示失败
        """
        try:
            enroll_path = Path(self.enroll_audio_path)
            
            # 收集所有音频文件
            audio_files = []
            if enroll_path.is_file():
                if enroll_path.suffix.lower() == '.wav':
                    audio_files.append(enroll_path)
            elif enroll_path.is_dir():
                audio_files = list(enroll_path.glob('*.wav'))
            else:
                log_error("SPE", f"注册音频路径无效: {self.enroll_audio_path}")
                return False
            
            if not audio_files:
                log_error("SPE", f"未找到注册音频文件: {self.enroll_audio_path}")
                return False
            
            log_debug("SPE", f"找到 {len(audio_files)} 个注册音频文件")
            
            # 提取每个音频的嵌入向量
            self.enroll_embeddings = []
            for audio_file in audio_files:
                try:
                    embedding = self._extract_embedding_from_file(str(audio_file))
                    if embedding is not None:
                        self.enroll_embeddings.append(embedding)
                        log_debug("SPE", f"已加载注册音频: {audio_file.name}", indent=1)
                except Exception as e:
                    log_warn("SPE", f"加载注册音频失败 {audio_file.name}: {e}", indent=1)
            
            if not self.enroll_embeddings:
                log_error("SPE", "未能加载任何注册音频的嵌入向量")
                return False
            
            return True
            
        except Exception as e:
            log_error("SPE", f"加载注册音频失败: {e}")
            import traceback
            log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
            return False
    
    def _load_wav(self, wav_file: str, obj_fs: int = 16000) -> torch.Tensor:
        """
        加载音频文件
        
        Args:
            wav_file: 音频文件路径
            obj_fs: 目标采样率
            
        Returns:
            音频张量
        """
        wav, fs = torchaudio.load(wav_file)
        if fs != obj_fs:
            wav, fs = torchaudio.sox_effects.apply_effects_tensor(
                wav, fs, effects=[['rate', str(obj_fs)]]
            )
        if wav.shape[0] > 1:
            wav = wav[0, :].unsqueeze(0)
        return wav
    
    def _extract_embedding_from_file(self, wav_file: str) -> Optional[np.ndarray]:
        """
        从音频文件提取嵌入向量
        
        Args:
            wav_file: 音频文件路径
            
        Returns:
            嵌入向量（numpy数组），失败返回None
        """
        try:
            wav = self._load_wav(wav_file)
            feat = self.feature_extractor(wav).unsqueeze(0).to(self.device)
            
            with torch.no_grad():
                embedding = self.embedding_model(feat).detach().squeeze(0).cpu().numpy()
            
            return embedding
        except Exception as e:
            log_error("SPE", f"提取嵌入向量失败 {wav_file}: {e}")
            return None
    
    def _extract_embedding_from_audio(self, audio_data: np.ndarray, sample_rate: int = 16000) -> Optional[np.ndarray]:
        """
        从音频数据提取嵌入向量
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
            sample_rate: 采样率
            
        Returns:
            嵌入向量（numpy数组），失败返回None
        """
        try:
            # 检查音频长度
            audio_length = len(audio_data) / sample_rate
            if audio_length < self.min_audio_length:
                log_debug("SPE", f"音频长度不足: {audio_length:.2f}秒 < {self.min_audio_length}秒")
                return None
            
            # 转换为torch tensor
            if len(audio_data.shape) == 1:
                wav = torch.from_numpy(audio_data).unsqueeze(0).float()
            else:
                wav = torch.from_numpy(audio_data).float()
            
            # 如果采样率不对，重采样
            if sample_rate != 16000:
                wav, _ = torchaudio.sox_effects.apply_effects_tensor(
                    wav, sample_rate, effects=[['rate', '16000']]
                )
            
            # 提取特征
            feat = self.feature_extractor(wav).unsqueeze(0).to(self.device)
            
            # 提取嵌入向量
            with torch.no_grad():
                embedding = self.embedding_model(feat).detach().squeeze(0).cpu().numpy()
            
            return embedding
        except Exception as e:
            log_error("SPE", f"提取嵌入向量失败: {e}")
            return None
    
    def _check_user_name_change(self) -> bool:
        """
        检查用户名是否变更，如果变更则重新加载注册音频
        
        Returns:
            True表示用户名已变更并重新加载成功，False表示未变更或重新加载失败
        """
        if not self.enabled:
            return False
        
        # 获取当前配置中的用户名
        current_user_name = self.config.get("system.user_name", "")
        if not current_user_name:
            current_user_name = "default"
        
        # 如果用户名未变更，直接返回
        if current_user_name == self.user_name:
            return False
        
        log_info("SPE", f"检测到用户名变更: {self.user_name} -> {current_user_name}")
        
        # 更新用户名
        old_user_name = self.user_name
        self.user_name = current_user_name
        
        # 重新构建用户特定的注册音频路径
        base_enroll_path = Path(self.base_enroll_audio_path)
        user_enroll_path = base_enroll_path / self.user_name
        self.enroll_audio_path = str(user_enroll_path)
        
        log_info("SPE", f"切换到用户注册音频路径: {self.enroll_audio_path}")
        
        # 检查新用户的注册音频是否齐全
        enroll_path = Path(self.enroll_audio_path)
        missing_indices = []
        
        if not enroll_path.exists():
            # 目录不存在，需要录制所有
            missing_indices = list(range(len(self.reading_texts)))
            log_info("SPE", f"新用户注册音频路径不存在，需要注册")
        elif enroll_path.is_dir():
            # 检查是否所有句子都已录制（使用文本哈希匹配）
            existing_files = {f.stem for f in enroll_path.glob("*.wav")}  # 获取所有文件名（不含扩展名）
            
            for i, text in enumerate(self.reading_texts):
                expected_filename = _get_text_filename(text)
                if expected_filename not in existing_files:
                    missing_indices.append(i)
            
            if missing_indices:
                log_info("SPE", f"新用户缺少 {len(missing_indices)} 个音频文件，需要补录")
            else:
                log_info("SPE", f"新用户所有 {len(self.reading_texts)} 个音频文件已齐全")
        
        needs_recording = len(missing_indices) > 0
        self.missing_indices = missing_indices
        
        # 如果需要注册，尝试自动录制
        if needs_recording:
            auto_record = self.config.get("asr.speaker_verification.auto_record", True)
            if auto_record:
                log_info("SPE", "将启动新用户的声纹注册录制流程")
                if not self._record_enroll_audio():
                    log_error("SPE", "新用户录制注册音频失败，声纹锁功能将禁用")
                    self.enabled = False
                    return False
            else:
                log_error("SPE", f"新用户注册音频路径不存在或为空: {self.enroll_audio_path}，且自动录制已禁用")
                self.enabled = False
                return False
        
        # 重新加载注册音频
        if not self._load_enroll_audio():
            log_error("SPE", "新用户注册音频加载失败，声纹锁功能将禁用")
            self.enabled = False
            return False
        
        log_info("SPE", f"已切换到用户 {self.user_name}，加载了 {len(self.enroll_embeddings)} 个注册声纹")
        return True
    
    def verify(self, audio_data: np.ndarray, sample_rate: int = 16000) -> Tuple[bool, float]:
        """
        验证音频是否匹配注册的声纹
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
            sample_rate: 采样率
        
        Returns:
            (是否匹配, 最高相似度分数)
        """
        if not self.enabled:
            return True, 1.0  # 如果未启用，直接通过
        
        # 如果注册音频还未加载，尝试加载（可能是动态启用声纹）
        if not self.enrollment_checked:
            log_debug("SPE", "注册音频未检查，尝试加载...")
            # 检查注册音频是否存在
            enroll_path = Path(self.enroll_audio_path)
            if enroll_path.exists() and enroll_path.is_dir():
                wav_files = list(enroll_path.glob("*.wav"))
                if wav_files:
                    # 如果有注册音频文件，尝试加载
                    if self._load_enroll_audio():
                        self.enrollment_checked = True
                        log_info("SPE", f"动态加载了 {len(self.enroll_embeddings)} 个注册声纹")
                    else:
                        log_warn("SPE", "注册音频加载失败，声纹验证将暂时禁用")
                        return True, 1.0  # 加载失败时暂时允许通过
                else:
                    # 没有注册音频文件，标记为已检查（避免重复检查）
                    self.enrollment_checked = True
                    log_info("SPE", "未找到注册音频文件，声纹验证暂时禁用（等待注册）")
                    return True, 1.0  # 没有注册音频时暂时允许通过
            else:
                # 路径不存在，标记为已检查
                self.enrollment_checked = True
                log_info("SPE", "注册音频路径不存在，声纹验证暂时禁用（等待注册）")
                return True, 1.0  # 路径不存在时暂时允许通过
        
        # 如果注册音频为空，暂时允许通过
        if not self.enroll_embeddings:
            return True, 1.0
        
        # 检查用户名是否变更
        self._check_user_name_change()
        
        try:
            # 提取嵌入向量
            embedding = self._extract_embedding_from_audio(audio_data, sample_rate)
            if embedding is None:
                return False, 0.0
            
            # 并行计算与所有注册声纹的相似度
            if len(self.enroll_embeddings) > 0:
                # 将所有注册嵌入向量转换为tensor [N, embedding_dim]
                enroll_tensor = torch.stack([
                    torch.from_numpy(emb) for emb in self.enroll_embeddings
                ]).to(self.device)
                
                # 将当前嵌入向量转换为tensor [embedding_dim]，并扩展到 [N, embedding_dim]
                embedding_tensor = torch.from_numpy(embedding).to(self.device)
                embedding_tensor = embedding_tensor.unsqueeze(0).expand(len(self.enroll_embeddings), -1)
                
                # 并行计算所有相似度
                similarity_fn = torch.nn.CosineSimilarity(dim=-1, eps=1e-6)
                similarities = similarity_fn(embedding_tensor, enroll_tensor)
                
                # 获取最大相似度
                max_similarity = similarities.max().item()
            else:
                max_similarity = 0.0
            
            # 判断是否匹配
            is_match = max_similarity >= self.similarity_threshold
            
            log_debug("SPE", f"声纹验证: 相似度={max_similarity:.4f}, 阈值={self.similarity_threshold}, 结果={'通过' if is_match else '拒绝'}")
            
            return is_match, max_similarity
            
        except Exception as e:
            log_error("SPE", f"声纹验证失败: {e}")
            return False, 0.0
    
    def is_enabled(self) -> bool:
        """检查声纹锁是否已启用"""
        return self.enabled
    
    def _record_enroll_audio(self) -> bool:
        """
        录制注册音频（根据配置的朗读列表录制）
        
        Returns:
            True表示录制成功，False表示失败
        """
        try:
            # 确定需要录制的句子索引
            if hasattr(self, 'missing_indices') and self.missing_indices:
                indices_to_record = self.missing_indices
                log_info("SPE", f"开始补录声纹注册音频，需要录制 {len(indices_to_record)} 个句子...")
            else:
                # 如果没有missing_indices，录制所有句子
                indices_to_record = list(range(len(self.reading_texts)))
                log_info("SPE", f"开始录制声纹注册音频流程，共 {len(indices_to_record)} 个句子...")
            
            # 根据配置的朗读列表构建录制场景
            recording_scenes = []
            for idx in indices_to_record:
                sentence_num = idx + 1
                reading_text = self.reading_texts[idx]
                recording_scenes.append({
                    "index": sentence_num,
                    "text_index": idx,  # 在列表中的索引
                    "prompt": f"请朗读以下内容，第{sentence_num}句开始录音",
                    "instruction": f"请用正常语速朗读以下内容（第 {sentence_num}/{len(self.reading_texts)} 句）：\n",
                    "reading_text": reading_text,
                    "scenario_hint": f"\n\n（距离麦克风约3-10厘米，正常说话）"
                })
            
            # 获取TTS引擎和音频播放器（优先使用已加载的，否则创建新的）
            try:
                # 如果已提供TTS引擎和音频播放器，直接使用
                if hasattr(self, '_tts_engine') and self._tts_engine is not None:
                    tts_engine = self._tts_engine
                    log_info("SPE", "使用已加载的TTS引擎进行语音提示", indent=1)
                else:
                    from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                    from voice_chatbot.tts.melo_tts_engine import MeloTTSEngine
                    
                    # 根据配置选择TTS引擎（使用config中的default_engine）
                    tts_engine_name = self.config.get("tts.default_engine", "edgetts").lower()
                    
                    if tts_engine_name == "melotts":
                        tts_engine = MeloTTSEngine(config=self.config, event_bus=None)
                        log_info("SPE", "创建 MeloTTS 引擎进行语音提示", indent=1)
                    elif tts_engine_name == "cosyvoice3":
                        from voice_chatbot.tts.cosyvoice3_engine import CosyVoice3Engine
                        tts_engine = CosyVoice3Engine(config=self.config, event_bus=None)
                        log_info("SPE", "创建 CosyVoice3 引擎进行语音提示", indent=1)
                    elif tts_engine_name in ("cosyvoice_tts", "cosyvoice"):
                        from voice_chatbot.tts.cosyvoice_tts_engine import CosyVoiceTTSEngine
                        tts_engine = CosyVoiceTTSEngine(config=self.config, event_bus=None)
                        log_info("SPE", "创建 CosyVoice TTS 引擎进行语音提示", indent=1)
                    else:  # 默认使用 Edge TTS
                        tts_engine = EdgeTTSEngine(config=self.config, event_bus=None)
                        log_info("SPE", "创建 Edge TTS 引擎进行语音提示", indent=1)
                
                if hasattr(self, '_audio_player') and self._audio_player is not None:
                    audio_player = self._audio_player
                    log_info("SPE", "使用已加载的音频播放器", indent=1)
                else:
                    from voice_chatbot.utils.audio_player import AudioPlayer
                    audio_player = AudioPlayer(config=self.config, event_bus=None)
                    log_info("SPE", "创建音频播放器", indent=1)
            except Exception as e:
                log_error("SPE", f"无法创建TTS引擎或音频播放器: {e}")
                import traceback
                log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
                return False
            
            # 录制参数
            record_duration = self.config.get("asr.speaker_verification.record_duration", 5.0)  # 秒
            sample_rate = self.config.get("audio.sample_rate", 16000)
            recorded_files = []
            
            # 辅助函数：使用VAD裁剪音频
            def trim_audio_with_vad(audio_data, sr):
                """使用VAD裁剪音频的头尾静音"""
                try:
                    import webrtcvad
                    
                    # 创建VAD实例
                    vad = webrtcvad.Vad()
                    vad.set_mode(3)  # 最敏感模式
                    
                    # 将音频转换为int16格式进行VAD检测
                    audio_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
                    audio_bytes = audio_int16.tobytes()
                    
                    # 检测语音开始和结束位置
                    frame_duration_ms = 30
                    frame_size = int(sr * frame_duration_ms / 1000)
                    step = frame_size * 2  # int16每样本2字节
                    
                    speech_start_frame = None
                    speech_end_frame = None
                    
                    for i in range(0, len(audio_bytes) - step + 1, step):
                        chunk = audio_bytes[i:i + step]
                        if len(chunk) == step:
                            try:
                                if vad.is_speech(chunk, sample_rate=sr):
                                    if speech_start_frame is None:
                                        speech_start_frame = i // step
                                    speech_end_frame = i // step
                            except:
                                continue
                    
                    # 裁剪音频（保留前后一些静音，避免切掉语音开头/结尾）
                    padding_frames = 5  # 前后各保留5帧（约150ms）
                    if speech_start_frame is None or speech_end_frame is None:
                        log_warn("SPE", "VAD未检测到语音，使用完整录音", indent=2)
                        return audio_data
                    
                    start_sample = max(0, (speech_start_frame - padding_frames) * frame_size)
                    end_sample = min(len(audio_data), (speech_end_frame + padding_frames + 1) * frame_size)
                    trimmed = audio_data[start_sample:end_sample]
                    log_debug("SPE", f"VAD裁剪: 原始{len(audio_data)/sr:.2f}秒 -> 裁剪后{len(trimmed)/sr:.2f}秒", indent=2)
                    return trimmed
                        
                except ImportError:
                    log_warn("SPE", "webrtcvad未安装，跳过VAD裁剪，使用完整录音", indent=2)
                    return audio_data
                except Exception as e:
                    log_warn("SPE", f"VAD处理失败，使用完整录音: {e}", indent=2)
                    return audio_data
            
            # 辅助函数：录制单个场景的音频
            def record_scene_audio(scene_info: dict, retry_count: int = 0) -> bool:
                """录制单个场景的音频"""
                try:
                    scene_index = scene_info["index"]
                    prompt_text = scene_info["prompt"]
                    instruction = scene_info["instruction"]
                    reading_text = scene_info["reading_text"]
                    scenario_hint = scene_info["scenario_hint"]
                    
                    if retry_count > 0:
                        print("\n" + "="*60)
                        print(f"场景 {scene_index} 录音验证失败！请重新录制...")
                        print("="*60 + "\n")
                    else:
                        print("\n" + "="*60)
                        print(f"声纹注册 - 场景 {scene_index}")
                        print("="*60 + "\n")
                    
                    # 播放提示音频
                    try:
                        log_info("SPE", f"场景{scene_index}生成提示音频...", indent=2)
                        scene_audio, _, _ = tts_engine.generate(prompt_text, return_timing=True)
                        log_info("SPE", f"场景{scene_index}提示音频生成完成", indent=2)
                        
                        play_complete = threading.Event()
                        def on_play_complete(was_interrupted, interrupt_reason):
                            play_complete.set()
                        
                        log_info("SPE", f"场景{scene_index}播放提示音频...", indent=2)
                        if not audio_player.play(scene_audio, task_id=0, on_complete_callback=on_play_complete):
                            log_error("SPE", f"播放场景{scene_index}提示音频失败")
                            return False
                        
                        play_complete.wait(timeout=30.0)
                        if not play_complete.is_set():
                            log_warn("SPE", f"播放场景{scene_index}提示音频超时")
                        
                        log_info("SPE", f"场景{scene_index}提示音频播放完成", indent=2)
                    except Exception as e:
                        log_error("SPE", f"播放场景{scene_index}提示音频失败: {e}")
                        import traceback
                        log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
                        return False
                    
                    # 显示要朗读的内容
                    print(instruction)
                    print(f"  {reading_text}")
                    print(scenario_hint)
                    print("\n" + "="*60 + "\n")
                    
                    # 开始录音
                    recorded_audio = []
                    
                    def on_audio_data(audio_data: np.ndarray):
                        recorded_audio.append(audio_data)
                    
                    try:
                        from voice_chatbot.utils.microphone import MicrophoneRecorder
                        microphone = MicrophoneRecorder(config=self.config, on_audio_data_callback=on_audio_data)
                        
                        log_info("SPE", f"场景{scene_index}开始录音，时长: {record_duration}秒", indent=1)
                        microphone.start()
                        
                        # 等待录音完成
                        import time
                        time.sleep(record_duration)
                        
                        microphone.stop()
                        
                        log_info("SPE", f"场景{scene_index}录音完成", indent=1)
                    except Exception as e:
                        log_error("SPE", f"场景{scene_index}录音失败: {e}")
                        return False
                    
                    if not recorded_audio:
                        log_error("SPE", f"场景{scene_index}未录制到任何音频数据")
                        return False
                    
                    # 合并录音数据
                    log_info("SPE", f"场景{scene_index}合并录音数据，共 {len(recorded_audio)} 个音频块", indent=2)
                    full_audio = np.concatenate(recorded_audio)
                    log_info("SPE", f"场景{scene_index}录音数据合并完成，总长度: {len(full_audio)/sample_rate:.2f}秒", indent=2)
                    
                    # 使用VAD检测并裁剪头尾静音
                    log_info("SPE", f"场景{scene_index}使用VAD裁剪音频...", indent=2)
                    trimmed_audio = trim_audio_with_vad(full_audio, sample_rate)
                    log_info("SPE", f"场景{scene_index}VAD裁剪完成", indent=2)
                    
                    # 使用ASR验证录音内容
                    asr_verified = False
                    try:
                        log_info("SPE", f"场景{scene_index}开始ASR验证...", indent=2)
                        # 使用传入的ASR模型或加载新模型
                        if self.asr_model is not None:
                            asr_model = self.asr_model
                            log_info("SPE", f"场景{scene_index} 使用传入的ASR模型", indent=2)
                        else:
                            from funasr import AutoModel
                            
                            # 加载ASR模型
                            asr_model_path = self.config.get("asr.model_path", "")
                            if not asr_model_path:
                                log_warn("SPE", "ASR模型路径未配置，跳过ASR验证", indent=2)
                                asr_verified = True  # 如果ASR不可用，跳过验证
                            else:
                                log_info("SPE", f"场景{scene_index} 正在加载ASR模型: {asr_model_path}", indent=2)
                                asr_model = AutoModel(model=asr_model_path, device="cuda" if torch.cuda.is_available() else "cpu", disable_pbar=True)
                                log_info("SPE", f"场景{scene_index} ASR模型加载完成", indent=2)
                        
                        if not asr_verified:
                            # 将音频转换为int16格式
                            log_info("SPE", f"场景{scene_index}转换音频格式...", indent=2)
                            audio_int16 = (np.clip(trimmed_audio, -1.0, 1.0) * 32767).astype(np.int16)
                            
                            # 进行ASR识别（不传递cache，确保每次都是全新识别）
                            log_info("SPE", f"场景{scene_index}开始ASR识别...", indent=2)
                            result = asr_model.generate(input=audio_int16, cache={}, batch_size_s=300, )
                            log_info("SPE", f"场景{scene_index}ASR识别完成", indent=2)
                            
                            if result and len(result) > 0:
                                recognized_text = result[0].get('text', '').strip()
                                log_info("SPE", f"场景{scene_index} ASR识别结果: {recognized_text}", indent=2)
                                
                                # 检查识别结果是否与预期文本匹配（允许部分匹配）
                                expected_text = reading_text.strip()
                                
                                # 简单的相似度检查：计算共同字符比例
                                def text_similarity(text1, text2):
                                    """计算两个文本的相似度（0-1）"""
                                    # 移除标点符号和空格
                                    import re
                                    text1_clean = re.sub(r'[^\w]', '', text1)
                                    text2_clean = re.sub(r'[^\w]', '', text2)
                                    
                                    if not text1_clean or not text2_clean:
                                        return 0.0
                                    
                                    # 计算最长公共子序列长度
                                    def lcs_length(s1, s2):
                                        m, n = len(s1), len(s2)
                                        dp = [[0] * (n + 1) for _ in range(m + 1)]
                                        for i in range(1, m + 1):
                                            for j in range(1, n + 1):
                                                if s1[i-1] == s2[j-1]:
                                                    dp[i][j] = dp[i-1][j-1] + 1
                                                else:
                                                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
                                        return dp[m][n]
                                    
                                    lcs = lcs_length(text1_clean, text2_clean)
                                    max_len = max(len(text1_clean), len(text2_clean))
                                    return lcs / max_len if max_len > 0 else 0.0
                                
                                similarity = text_similarity(recognized_text, expected_text)
                                similarity_threshold = 0.5  # 相似度阈值，50%以上认为匹配
                                
                                log_info("SPE", f"场景{scene_index} 文本相似度: {similarity:.2f} (阈值: {similarity_threshold})", indent=2)
                                
                                if similarity >= similarity_threshold:
                                    log_info("SPE", f"场景{scene_index} ASR验证通过", indent=2)
                                    asr_verified = True
                                else:
                                    log_warn("SPE", f"场景{scene_index} ASR验证失败", indent=2)
                                    log_warn("SPE", f"预期: {expected_text}", indent=3)
                                    log_warn("SPE", f"识别: {recognized_text}", indent=3)
                                    asr_verified = False
                            else:
                                log_warn("SPE", "ASR识别结果为空，跳过验证", indent=2)
                                asr_verified = True  # 如果识别失败，跳过验证
                                
                    except ImportError:
                        log_warn("SPE", "funasr未安装，跳过ASR验证", indent=2)
                        asr_verified = True  # 如果ASR不可用，跳过验证
                    except Exception as e:
                        log_warn("SPE", f"ASR验证出错: {e}，跳过验证", indent=2)
                        asr_verified = True  # 如果ASR出错，跳过验证
                    
                    # 如果验证失败，允许重试
                    if not asr_verified:
                        if retry_count < 2:  # 最多重试2次
                            log_info("SPE", f"场景{scene_index} 验证失败，准备重新录制...", indent=1)
                            return record_scene_audio(scene_info, retry_count + 1)
                        else:
                            log_error("SPE", f"场景{scene_index} 已达到最大重试次数，验证失败", indent=1)
                            return False
                    
                    # 保存音频文件
                    try:
                        log_info("SPE", f"场景{scene_index}保存音频文件...", indent=2)
                        enroll_dir = Path(self.enroll_audio_path)
                        enroll_dir.mkdir(parents=True, exist_ok=True)
                        
                        # 使用文本内容生成文件名（基于文本哈希）
                        audio_filename = f"{_get_text_filename(reading_text)}.wav"
                        audio_file_path = enroll_dir / audio_filename
                        
                        # 转换为int16格式并保存为WAV
                        audio_int16 = (np.clip(trimmed_audio, -1.0, 1.0) * 32767).astype(np.int16)
                        audio_tensor = torch.from_numpy(audio_int16).unsqueeze(0)
                        torchaudio.save(str(audio_file_path), audio_tensor, sample_rate)
                        
                        recorded_files.append(audio_file_path)
                        log_info("SPE", f"场景{scene_index} 音频已保存: {audio_file_path}", indent=1)
                        return True
                        
                    except Exception as e:
                        log_error("SPE", f"保存场景{scene_index}音频文件失败: {e}")
                        return False
                        
                except Exception as e:
                    log_error("SPE", f"录制场景{scene_info['index']}失败: {e}")
                    import traceback
                    log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
                    return False
            
            # 遍历所有场景进行录制
            log_info("SPE", f"开始录制 {len(recording_scenes)} 个场景的音频", indent=1)
            for scene in recording_scenes:
                log_info("SPE", f"开始录制场景 {scene['index']}: {scene['reading_text']}", indent=1)
                if not record_scene_audio(scene):
                    log_error("SPE", f"录制场景{scene['index']}失败，声纹注册流程终止")
                    return False
                log_info("SPE", f"场景 {scene['index']} 录制完成", indent=1)
            
            # 所有场景录制完成
            log_info("SPE", f"所有{len(recorded_files)}个场景录制完成")
            log_info("SPE", f"音频文件保存在: {self.enroll_audio_path}", indent=1)
            
            # 录制完成后，立即加载注册音频
            log_info("SPE", "录制完成，正在加载注册音频...", indent=1)
            if self._load_enroll_audio():
                log_info("SPE", f"成功加载 {len(self.enroll_embeddings)} 个注册声纹，声纹验证已启用")
                self.enabled = True
                self.enrollment_checked = True
            else:
                log_error("SPE", "注册音频加载失败")
                return False
            
            print("\n" + "="*60)
            print("声纹注册完成！")
            print(f"已录制 {len(recorded_files)} 个场景的音频：")
            for i, audio_file in enumerate(recorded_files, 1):
                print(f"  {i}. {audio_file.name}")
            print("="*60 + "\n")
            
            return True
            
        except Exception as e:
            log_error("SPE", f"录制注册音频流程失败: {e}")
            import traceback
            log_error("SPE", f"异常堆栈: {traceback.format_exc()}", indent=1)
            return False

