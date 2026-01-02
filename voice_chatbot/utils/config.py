"""
配置管理模块

提供配置文件加载、环境变量支持、参数验证等功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py 第105-275行
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, Optional


class Config:
    """配置管理类"""
    
    # 默认配置
    DEFAULT_CONFIG = {
        "audio": {
            "sample_rate": 16000,  # 音频采样率（Hz），常用值：16000
            "chunk_size": 1024,  # 每次读取的采样点数，约64ms（在16kHz采样率下）
            "channels": 1,  # 声道数，1=单声道，2=立体声
        },
        "vad": {  # VAD语音活动检测配置（独立section）
            "mode": 3,  # VAD模式（0-3），数字越大越敏感，3=最敏感。降低此值会减少误判但可能漏检
            "activation_rate": 0.25,  # VAD激活率（0.0-1.0），检测窗口内语音块占比超过此值才判定为有语音。值越小越敏感，容易检测到声音但可能误判环境音；值越大越严格。建议0.2-0.4，声音小或感觉过滤太多可降低（如0.2-0.3）
            "adaptive_threshold": False,  # 是否启用自适应阈值（根据环境自动调整激活率），适合环境噪声变化大的场景
            "min_activation_rate": 0.15,  # 最小激活率（0.0-1.0），自适应阈值时的下限
            "max_activation_rate": 0.5,  # 最大激活率（0.0-1.0），自适应阈值时的上限
            "check_interval": 0.2,  # VAD检测间隔（秒），检测窗口大小，值越小响应越快但CPU占用更高
            "thread_sleep": 0.01,  # VAD线程休眠间隔（秒）
        },
        "audio_filter": {
            "enable_echo_cancellation": True,  # 是否启用回声消除（AEC），用于去除播放音频对麦克风的影响
            "echo_cancellation_strength": 0.2,  # 回声消除强度（0.0-1.0），值越大消除效果越强
            "playback_reference_delay": 0,  # 播放参考信号延迟（采样点数），用于补偿音频延迟
        },
        "asr": {
            "chunk_size": [0, 10, 5],  # ASR chunk大小配置，[0, 10, 5] = 600ms，[0, 8, 4] = 480ms
            "encoder_chunk_look_back": 4,  # encoder self-attention lookback chunks数量，用于上下文理解
            "decoder_chunk_look_back": 1,  # decoder cross-attention lookback chunks数量，用于解码时参考历史
            "pre_buffer_seconds": 0.5,  # 预缓存时长（秒），保留VAD检测前的音频避免丢失开头
            "model_path": "D:\\APP\\models\\Qwen\\paraformer-zh-streaming",  # ASR模型路径
            "language": "zh",  # ASR语言设置（默认中文）
            "audio_queue_maxsize": 100,  # 音频队列最大大小
            "preload_model": True,  # 是否在启动时预加载模型
            "result_timeout": 30.0,  # ASR结果等待超时（秒），统一的ASR超时参数
            "result_time_buffer": 0.5,  # 结果时间缓冲（秒），考虑ASR识别延迟，用于时间段提取时扩展时间范围（增大到0.5秒确保ASR有足够时间输出最后一个字）
            "max_result_age": 60.0,  # 结果最大保留时间（秒），超过此时间的自动清理，防止内存泄漏
        },
        "dialogue": {
            "no_speech_threshold": 0.8,  # 无语音阈值（秒），用户停止说话后等待多久才认为说话结束并触发ASR停止，值越大越不容易误判（如换气时），但响应会变慢。建议0.8-1.2秒，语速慢可设更大。从0.4秒改为1.0秒以避免过早停止ASR
            "min_audio_length": 0.3,  # 最小语音时长窗口（秒），用于判断是否为真实语音活动
            "audio_stop_delay": 0.5,  # 延迟验证时间（秒）
            "min_play_duration_to_record": 1.3,  # 最小播放时长阈值（秒），播放时长小于此值时不记录到对话历史（实际判断时需要达到此值，已包含audio_stop_delay=0.5秒）
            "invalid_recognition_words": ["", "i", "嗯", "啊", "呃", "哦", "额"],  # 无效识别词列表
            "interrupt_check_interval": 0.1,  # 打断检测间隔（秒）
            "min_silent_duration": 0.8,  # 最小静音时长（秒），延迟验证期间的静音检测阈值，建议与no_speech_threshold配合
            "sentence_queue_maxsize": 100,  # 句子队列最大大小
            "audio_play_queue_maxsize": 50,  # 音频播放队列最大大小
            "auto_save_state": False,  # 是否自动保存对话状态
            "state_file": "dialogue_state.json",  # 对话状态文件路径
            "max_history_length": 50,  # 最大历史记录长度（对话轮数，每轮包含用户和助手两条消息），超过此数量会删除最旧的记录
            "enable_latency_analysis": True,  # 是否启用响应耗时详细分析，在第一个TTS播放前输出，使用不同颜色表示不同节点
            "vad_start_time_offset": 0.2,  # VAD语音开始时的时间偏移（秒），向前计算以确保包含语音内容
            "asr_output_time_offset": 0.5,  # ASR首次输出时的时间偏移（秒），向前计算以覆盖识别延迟
            # 超时参数（统一管理）
            "thread_timeout": 1.0,  # 通用线程等待超时（秒），用于各类线程join操作（已优化：真正取消任务时立即清理引用，减少兜底超时）
            "llm_timeout": 300.0,  # LLM生成超时时间（秒，默认5分钟）
            "tts_timeout": 5.0,  # TTS生成超时时间（秒）
            "player_timeout": 10.0,  # 音频播放线程超时时间（秒）
            "speech_rate_offset": -0.3,  # 文字阅读速度计算偏差值（字符/秒），用于精细化调整
        },
        "llm": {
            "api_base": "http://127.0.0.1:5000/v1",  # LLM API基础URL（OpenAI兼容格式）
            "api_key": "sk-1234",  # LLM API密钥
            "model_name": "qwen3-30b-a3b",  # LLM模型名称
            "max_tokens": 1024,  # 最大生成token数，控制回复长度
            "temperature": 0.7,  # 生成温度（0.0-2.0），值越大输出越随机，值越小输出越确定
            "min_sentence_length": 8,  # 最小句子长度（字符数或单词数，根据语言类型），用于流式句子合并
            "sentence_length_increment_rate": 2,  # 句子长度增量倍率，用于动态调整后续句子的最小长度
            "max_sentence_length": 200,  # 最大句子长度（字符数或单词数，根据语言类型），防止句子无限增长
            "first_sentence_comma_delay_chars": 8,  # 首句逗号延迟字符数，遇到逗号后等待此数量的字符，如果期间出现句号等结束符则按结束符切分，否则按逗号切分
            "system_prompt": "你叫林可可，是16岁的女高中生，性格活泼开朗，谈吐风趣。你正在和用户聊天，回答简短，控制在300字以内，不要用表情。",  # 系统提示词，定义AI助手的角色和对话风格（保留作为向后兼容的fallback）
            "persona_name": "lincoco",  # 默认人设名称（对应llm/personas/目录下的文件名，不含扩展名）
            "persona_dir": "llm/personas",  # 人设文件目录（相对于项目根目录）
            "user_name": "林嘿嘿",  # 用户名（用于人设文件中的{{user_name}}变量替换）
            "if_thinking": False,  # 是否启用思考模式（某些模型支持，如Qwen3）
            "max_retries": 3,  # API调用最大重试次数
            "retry_delay": 1.0,  # 重试延迟（秒）
            "request_timeout": 30.0,  # 请求超时（秒）
        },
        "tts": {
            "enable_performance_stats": True,  # 是否启用性能统计，输出TTS生成耗时等性能信息
            "silence_threshold": -40,  # 静音检测阈值（dB）
            "min_silence_len": 30,  # 最小静音长度（毫秒）
            "enable_trim_silence": True,  # 是否裁剪静音
            "trim_keep_start": 20,  # 裁剪时开头固定保留长度（毫秒）
            "trim_keep_end": 350,  # 裁剪时结尾固定保留长度（毫秒）
            "enable_normalize_loudness": True,  # 是否启用响度归一化（统一音频响度，在裁剪静音之后应用）
            "playback_check_interval": 0.05,  # 播放检查间隔（秒）
            "playback_status_update_interval": 0.1,  # 播放状态更新间隔（秒），用于流式状态返回
            "default_engine": "melotts",  # 当前使用的TTS引擎（edgetts、cosyvoice3 或 melotts）
            # TTS缓存配置（所有引擎共享）
            "cache_enabled": True,  # 是否启用TTS内存缓存（减少重复请求）
            "cache_max_size": None,  # 最大缓存条目数（None表示不设上限，不淘汰旧缓存）
            # TTS文件缓存配置（所有句子都缓存，所有引擎共享）
            "tts_file_cache_enabled": True,  # 是否启用TTS文件缓存（持久化，所有句子都缓存）
            "tts_file_cache_dir": "tts/tts_cache",  # TTS缓存目录路径（支持绝对路径，如"D:/cache/tts"；相对路径则相对于项目根目录）
            "tts_file_cache_max_length": 20,  # 最大缓存长度（中文按字符数，英文按单词数，超过此长度的TTS音频不保存）
            "tts_file_cache_auto_repair": True,  # 是否自动修复缺失的缓存文件（True：启动时检查并修复，False：仅在实际使用时生成）
            # TTS引擎配置
            "engines": {
                "edge_tts": {  # EdgeTTS引擎配置
                    "is_streaming": False,  # 是否流式引擎（False=非流式）
                    "voice_map": {  # 语言-语音映射表
                        # "ja": "ja-JP-NanamiNeural",
                        "ja": "zh-CN-XiaoyiNeural",
                        "fr": "fr-FR-DeniseNeural",
                        "es": "es-ES-ElviraNeural",
                        "de": "de-DE-KatjaNeural",
                        "zh": "zh-CN-XiaoyiNeural",
                        "en": "en-US-AnaNeural",
                    },
                    # EdgeTTS性能优化配置
                    "connection_timeout": 30.0,  # 连接超时时间（秒）
                    "enable_session_reuse": True,  # 是否启用会话复用（减少连接开销）
                },
                "cosyvoice3": {  # CosyVoice3引擎配置
                    "is_streaming": True,  # 是否流式引擎（True=流式）
                    "model_path": "D:\\APP\\models\\Qwen\\Fun-CosyVoice3-0.5B-2512",  # CosyVoice3模型路径（必须配置）
                    "reference_audio_path": "",  # 零样本克隆参考音频路径（可选）
                    "prompt_text": "",  # 零样本克隆提示文本（可选）
                    "enable": False,  # 是否启用CosyVoice3
                    "fp16": True,  # 是否使用fp16加速
                    "load_jit": True,  # 是否加载JIT模型
                    "default_mode": "sft",  # 默认模式：sft（预设音色）或 zero_shot（零样本克隆）
                    "default_spk": "中文女",  # SFT模式默认音色
                    "default_speed": 1.0,  # 默认语速（1.0为正常速度）
                    "target_sample_rate": 16000,  # 目标采样率
                    "voice_map": {  # 语言-音色映射表
                        "zh": "中文女",
                        "en": "英文女",
                        "ja": "日语男",
                        "ko": "韩语女",
                        "yue": "粤语女",
                    },
                },
                "melo_tts": {  # MeloTTS引擎配置
                    "is_streaming": False,  # 是否流式引擎（False=非流式）
                    "api_url": "http://127.0.0.1:8101",  # MeloTTS API 服务地址
                    "api_endpoint": "/tts",  # API 端点路径
                    "init_endpoint": "/init",  # 模型初始化端点路径
                    "timeout": 30.0,  # HTTP 请求超时时间（秒）
                    "model_language": "ZH",  # 要加载的模型语言（ZH=中文, EN=英文, JP=日文等），启动时会调用 /init 接口加载此语言的模型
                    "speaker_name_map": {  # 语言到说话人名称的映射（推荐使用，与 WebUI 一致，优先级高于 speaker_id_map）
                        "zh": "ZH",  # 中文使用 'ZH' 说话人
                        "en": "EN-US",  # 英文使用 'EN-US' 说话人（可根据模型调整）
                    },
                    "speaker_id_map": {  # 语言到 speaker_id 的映射（已废弃，推荐使用 speaker_name_map）
                        "zh": 0,
                        "en": 0,
                    },
                    "default_speaker_id": 0,  # 默认 speaker_id（已废弃，推荐使用 default_speaker_name）
                    "default_speaker_name": None,  # 默认说话人名称（如 'ZH'，如果不提供则使用模型默认的第一个说话人）
                    "sdp_ratio": 0.2,  # SDP 比例（默认值）
                    "noise_scale": 0.6,  # 噪声比例（默认值）
                    "noise_scale_w": 0.8,  # 噪声宽度比例（默认值）
                    "speed": 1.0,  # 语速（默认值，1.0为正常速度）
                },
            },
        },
        "api": {  # API服务配置
            "enabled": False,  # 是否启用API服务
            "host": "0.0.0.0",  # API服务监听地址
            "port": 6000,  # API服务端口
            "cors_enabled": True,  # 是否启用跨域支持
            "cors_origins": ["*"],  # CORS允许的来源
            "max_text_length": 5000,  # 最大文本长度
            "default_format": "wav",  # 默认音频格式（wav, mp3, pcm）
        },
        "logging": {  # 统一日志配置（Logger 和 EventLogger）
            "level": "INFO",  # 日志级别（DEBUG, INFO, WARN, ERROR），只输出此级别及以上的日志
            "file_enabled": True,  # 是否启用文件日志，日志会保存到文件中
            "log_dir": "logs",  # 日志文件目录路径（相对于项目根目录）
            "enable_console": True,  # 是否输出到控制台（EventLogger用）
            "show_delta": True,  # 是否显示与上一事件的时间差（EventLogger用）
            "enable_sequence_diagram": True,  # 是否启用sequenceDiagram记录（图形化流程记录，EventLogger用）
            "sequence_diagram_flow_timeout": 30.0,  # 流程超时时间（秒），超过此时间自动结束流程记录（EventLogger用）
            # 日志过滤配置（黑名单模式）
            "filtered_levels": [],  # 被过滤的日志级别列表（如 ["DEBUG"]），这些级别的日志将不输出
            "filtered_categories": ["THREAD", "EVENT", "MIC", "TIME", "MANAGER"],  # 被过滤的日志类别列表（如 ["THREAD", "EVENT", "MANAGER", "VAD", "CTX", "MIC"]），这些类别的日志将不输出
            # 日志类别专属颜色配置（字典格式：{"CATEGORY": "COLOR_NAME"}，如 {"ASR": "GREEN", "LLM": "CYAN"}）
            # 支持的颜色名称：BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE,
            # BRIGHT_BLACK, BRIGHT_RED, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE, BRIGHT_MAGENTA, BRIGHT_CYAN, BRIGHT_WHITE
            # 可以组合样式：如 "GREEN+BOLD", "RED+UNDERLINE" 等
            "category_colors": {
                "ASR": "GREEN",
                "TTS": "BLUE",
                "LLM": "YELLOW",
                "PLAY": "MAGENTA",
                "VAD": "CYAN",
                "TASK": "BRIGHT_CYAN",
                "MANAGER": "RED",
            },
        },
    }
    
    def __init__(self, config_file: Optional[str] = None):
        """
        初始化配置管理器
        
        Args:
            config_file: 配置文件路径（JSON格式），如果为None则使用默认配置
        """
        self.config = self.DEFAULT_CONFIG.copy()
        self.config_file = config_file
        
        # 加载配置文件
        if config_file and os.path.exists(config_file):
            self.load_from_file(config_file)
        
        # 从环境变量加载配置（覆盖文件配置）
        self.load_from_env()
        
        # 验证配置
        self.validate()
    
    def load_from_file(self, config_file: str):
        """
        从JSON文件加载配置
        
        Args:
            config_file: 配置文件路径
        """
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
            
            # 深度合并配置
            self._deep_merge(self.config, file_config)
        except Exception as e:
            print(f"警告：加载配置文件失败: {e}，使用默认配置")
    
    def load_from_env(self):
        """从环境变量加载配置（覆盖文件配置）"""
        # 环境变量命名规范：VOICE_CHATBOT_<SECTION>_<KEY>
        # 例如：VOICE_CHATBOT_AUDIO_SAMPLE_RATE
        
        for section, values in self.config.items():
            if not isinstance(values, dict):
                continue
            
            for key in values.keys():
                env_key = f"VOICE_CHATBOT_{section.upper()}_{key.upper()}"
                env_value = os.getenv(env_key)
                
                if env_value is not None:
                    # 尝试转换类型
                    original_value = values[key]
                    converted_value = self._convert_type(env_value, type(original_value))
                    self.config[section][key] = converted_value
    
    def _convert_type(self, value: str, target_type: type) -> Any:
        """
        将字符串值转换为目标类型
        
        Args:
            value: 字符串值
            target_type: 目标类型
            
        Returns:
            转换后的值
        """
        if target_type == bool:
            return value.lower() in ('true', '1', 'yes', 'on')
        elif target_type == int:
            return int(value)
        elif target_type == float:
            return float(value)
        elif target_type == list:
            # 尝试解析JSON列表
            try:
                return json.loads(value)
            except:
                # 如果不是JSON，按逗号分割
                return [item.strip() for item in value.split(',')]
        else:
            return value
    
    def _deep_merge(self, base: Dict, override: Dict):
        """
        深度合并字典
        
        Args:
            base: 基础字典（会被修改）
            override: 覆盖字典
        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def validate(self):
        """验证配置参数"""
        # 验证音频参数
        assert self.config["audio"]["sample_rate"] > 0, "sample_rate必须大于0"
        assert self.config["audio"]["chunk_size"] > 0, "chunk_size必须大于0"
        
        # 验证VAD参数（独立section）
        assert 0 <= self.config["vad"]["mode"] <= 3, "vad.mode必须在0-3之间"
        assert 0.0 <= self.config["vad"]["activation_rate"] <= 1.0, \
            "vad.activation_rate必须在0.0-1.0之间"
        assert 0.0 <= self.config["vad"]["min_activation_rate"] <= 1.0, \
            "vad.min_activation_rate必须在0.0-1.0之间"
        assert 0.0 <= self.config["vad"]["max_activation_rate"] <= 1.0, \
            "vad.max_activation_rate必须在0.0-1.0之间"
        assert self.config["vad"]["min_activation_rate"] <= self.config["vad"]["max_activation_rate"], \
            "vad.min_activation_rate必须小于等于vad.max_activation_rate"
        assert self.config["vad"]["check_interval"] > 0, "vad.check_interval必须大于0"
        assert self.config["vad"]["thread_sleep"] > 0, "vad.thread_sleep必须大于0"
        
        # 验证音频过滤参数
        assert 0.0 <= self.config["audio_filter"]["echo_cancellation_strength"] <= 1.0, \
            "echo_cancellation_strength必须在0.0-1.0之间"
        
        # 验证ASR参数
        assert self.config["asr"]["result_timeout"] > 0, "asr.result_timeout必须大于0"
        
        # 验证对话参数
        assert self.config["dialogue"]["no_speech_threshold"] > 0, "no_speech_threshold必须大于0"
        assert self.config["dialogue"]["min_audio_length"] > 0, "min_audio_length必须大于0"
        assert self.config["dialogue"]["audio_stop_delay"] >= 0, "audio_stop_delay必须大于等于0"
        assert self.config["dialogue"]["max_history_length"] > 0, "max_history_length必须大于0"
        assert self.config["dialogue"]["thread_timeout"] > 0, "thread_timeout必须大于0"
        assert self.config["dialogue"]["llm_timeout"] > 0, "llm_timeout必须大于0"
        assert self.config["dialogue"]["tts_timeout"] > 0, "tts_timeout必须大于0"
        assert self.config["dialogue"]["player_timeout"] > 0, "player_timeout必须大于0"
        
        # 验证LLM参数
        assert self.config["llm"]["max_tokens"] > 0, "max_tokens必须大于0"
        assert 0.0 <= self.config["llm"]["temperature"] <= 2.0, "temperature必须在0.0-2.0之间"
        assert self.config["llm"]["min_sentence_length"] > 0, "min_sentence_length必须大于0"
        assert self.config["llm"]["sentence_length_increment_rate"] > 0, \
            "sentence_length_increment_rate必须大于0"
        assert self.config["llm"]["max_sentence_length"] > 0, "max_sentence_length必须大于0"
        assert self.config["llm"]["max_sentence_length"] >= self.config["llm"]["min_sentence_length"], \
            "max_sentence_length必须大于等于min_sentence_length"
        
        # 验证TTS参数
        assert self.config["tts"]["min_silence_len"] > 0, "min_silence_len必须大于0"
        assert self.config["tts"]["playback_check_interval"] > 0, \
            "playback_check_interval必须大于0"
        assert self.config["tts"]["trim_keep_start"] >= 0, \
            "trim_keep_start必须大于等于0"
        assert self.config["tts"]["trim_keep_end"] >= 0, \
            "trim_keep_end必须大于等于0"
        # 验证TTS性能优化参数
        cache_max_size = self.config["tts"].get("cache_max_size")
        assert cache_max_size is None or cache_max_size > 0, \
            "cache_max_size必须为None（无上限）或大于0的整数"
        # 验证EdgeTTS引擎特定配置
        if "engines" in self.config["tts"] and "edge_tts" in self.config["tts"]["engines"]:
            edge_tts_config = self.config["tts"]["engines"]["edge_tts"]
            if "connection_timeout" in edge_tts_config:
                assert edge_tts_config["connection_timeout"] > 0, \
                    "edge_tts.connection_timeout必须大于0"
        
        # 验证日志配置参数
        valid_levels = ["DEBUG", "INFO", "WARN", "ERROR"]
        assert self.config["logging"]["level"] in valid_levels, \
            f"logging.level必须是 {valid_levels} 之一"
        assert isinstance(self.config["logging"]["filtered_levels"], list), \
            "logging.filtered_levels必须是列表"
        assert all(level in valid_levels for level in self.config["logging"]["filtered_levels"]), \
            f"logging.filtered_levels中的级别必须是 {valid_levels} 之一"
        assert isinstance(self.config["logging"]["filtered_categories"], list), \
            "logging.filtered_categories必须是列表"
        assert self.config["logging"]["sequence_diagram_flow_timeout"] > 0, \
            "logging.sequence_diagram_flow_timeout必须大于0"
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        获取配置值（支持嵌套路径）
        
        Args:
            path: 配置路径，使用点号分隔，如 "audio.sample_rate"
            default: 默认值（如果路径不存在）
            
        Returns:
            配置值
        """
        keys = path.split('.')
        value = self.config
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, path: str, value: Any):
        """
        设置配置值（支持嵌套路径）
        
        Args:
            path: 配置路径，使用点号分隔，如 "audio.sample_rate"
            value: 配置值
        """
        keys = path.split('.')
        config = self.config
        
        # 创建嵌套路径（如果不存在）
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        # 设置值
        config[keys[-1]] = value
    
    def save_to_file(self, config_file: str):
        """
        保存配置到文件
        
        Args:
            config_file: 配置文件路径
        """
        try:
            # 确保目录存在
            config_path = Path(config_file)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"错误：保存配置文件失败: {e}")
    
    def get_all(self) -> Dict:
        """获取所有配置"""
        return self.config.copy()
    
    def __getitem__(self, key: str) -> Any:
        """支持字典式访问"""
        return self.config[key]
    
    def __setitem__(self, key: str, value: Any):
        """支持字典式设置"""
        self.config[key] = value

