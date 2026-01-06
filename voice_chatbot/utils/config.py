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

    # 默认配置（按重要性排序：基础配置 -> 核心功能 -> 输出功能 -> 辅助功能）
    DEFAULT_CONFIG = {
        # ====== 1. 基础配置 ======
        # 系统级基准参数
        "system": {
            "thread_timeout": 1.0,  # 通用线程等待超时(秒),用于各类线程join操作
        },

        # ====== 2. 基础音频配置 ======
        # 最基础的音频参数,影响整个系统的输入质量
        "audio": {
            "sample_rate": 16000,  # 音频采样率(Hz),常用值:16000,越高音质越好但计算量越大
            "chunk_size": 1024,  # 每次读取的采样点数,约64ms(在16kHz采样率下),影响实时性和延迟
            "channels": 1,  # 声道数,1=单声道,2=立体声,语音识别通常使用单声道
        },

        # ====== 3. VAD语音活动检测配置 ======
        # 核心功能,决定何时开始/停止语音识别,直接影响对话流畅度和准确性
        "vad": {
            "mode": 3,  # VAD模式(0-3),数字越大越敏感,3=最敏感。降低此值会减少误判但可能漏检
            "activation_rate": 0.1,  # VAD激活率(0.0-1.0),检测窗口内语音块占比超过此值才判定为有语音
                                   # 值越小越敏感,容易检测到声音但可能误判环境音
                                   # 建议值:0.2-0.4,声音小或感觉过滤太多可降低(如0.2-0.3)
            "adaptive_threshold": False,  # 是否启用自适应阈值(根据环境自动调整激活率),适合环境噪声变化大的场景
            "min_activation_rate": 0.15,  # 最小激活率(0.0-1.0),自适应阈值时的下限
            "max_activation_rate": 0.5,  # 最大激活率(0.0-1.0),自适应阈值时的上限
            "check_interval": 0.1,  # VAD检测间隔(秒),检测窗口大小,值越小响应越快但CPU占用更高
            "thread_sleep": 0.01,  # VAD线程休眠间隔(秒)
        },

        # ====== 4. ASR语音识别配置 ======
        # 核心功能,将语音转换为文本,包含声纹锁和标点恢复等增强功能
        "asr": {
            # ASR模型配置
            "model_path": "paraformer-zh-streaming",  # ASR模型路径
            "language": "zh",  # ASR语言设置(默认中文)
            "preload_model": True,  # 是否在启动时预加载模型,首次识别会更快但启动时间增加

            # 流式识别参数
            "chunk_size": [0, 10, 5],  # ASR chunk大小配置,[0, 10, 5] = 600ms,[0, 8, 4] = 480ms
                                     # 影响识别延迟和准确性,需根据模型特性调整
            "encoder_chunk_look_back": 4,  # encoder self-attention lookback chunks数量,用于上下文理解
            "decoder_chunk_look_back": 1,  # decoder cross-attention lookback chunks数量,用于解码时参考历史
            "pre_buffer_seconds": 0.5,  # 预缓存时长(秒),保留VAD检测前的音频避免丢失开头

            # ASR队列和超时配置
            "audio_queue_maxsize": 100,  # 音频队列最大大小,用于缓存待识别的音频块
            "result_timeout": 30.0,  # ASR结果等待超时(秒),统一的ASR超时参数
            "result_time_buffer": 0.1,  # 结果时间缓冲(秒),考虑ASR识别延迟,用于时间段提取时扩展时间范围
                                     # 增大到0.5秒可确保ASR有足够时间输出最后一个字
            "max_result_age": 60.0,  # 结果最大保留时间(秒),超过此时间的自动清理,防止内存泄漏

            # ASR停止条件配置
            "no_speech_threshold": 0.4,  # 无语音阈值(秒),用户停止说话后等待多久才认为说话结束并触发ASR停止
                                       # 值越大越不容易误判(如换气时),但响应会变慢
                                       # 建议值:0.8-1.2秒,语速慢可设更大

            # ASR增强功能
            "enable_deduplication": False,  # 是否启用ASR流式去重,处理流式识别中的重复文本

            # ASR结果过滤配置
            "invalid_recognition_words": ["", "i", "嗯", "啊", "呃", "哦", "额"],  # 无效识别词列表,会被过滤
                                                                       # 这些词会被认为是无效输入,不会触发对话处理

            # --- 声纹锁配置 ---
            # 如果开启,ASR只对符合设定声纹的声音进行识别,提供安全保护
            "speaker_verification": {
                "enabled": True,  # 是否启用声纹锁功能
                "model_id": "iic/speech_campplus_sv_zh-cn_16k-common",  # 3D-Speaker模型ID(从ModelScope加载)
                "enroll_audio_path": "cache/speaker_verification_audio",  # 注册音频文件夹路径
                                                                   # 支持多个.wav文件,默认保存在speaker_verification_audio目录下
                "similarity_threshold": 0.3,  # 相似度阈值(0.0-1.0),超过此值才认为是同一说话人,值越大越严格
                "min_audio_length": 0.5,  # 最小音频长度(秒),用于声纹验证的音频片段至少需要此长度
                "cache_embedding": True,  # 是否缓存注册音频的嵌入向量(避免重复计算)
                "record_duration": 8.0,  # 录制注册音频的时长(秒),默认8秒
                "auto_record": True,  # 如果注册音频不存在,是否自动启动录制流程

                # 滑动窗口声纹验证
                "sliding_window": {
                    "enabled": True,  # 是否启用滑动窗口验证(True=使用滑动窗口,False=使用固定窗口)
                    "window_length": 0.6,  # 窗口长度(秒),默认0.5秒
                    "window_stride": 0.2,  # 滑动步长(秒),默认0.2秒
                },
            },

            # --- 标点恢复配置 ---
            # 对最终识别结果进行标点符号恢复处理,提升可读性
            "punctuation_restore": {
                "enabled": True,  # 是否启用ASR标点恢复
                "model": "ct-punc",  # 标点恢复模型名称,默认为"ct-punc"
            },
        },

        # ====== 5. LLM大语言模型配置 ======
        # 核心功能,生成对话回复,支持多引擎和动态人设
        "llm": {
            # LLM基础参数
            "provider": "openai",  # LLM服务提供商:openai、ollama 或 glm
            "max_tokens": 1024,  # 最大生成token数,控制回复长度
            "temperature": 0.7,  # 生成温度(0.0-2.0),值越大输出越随机,值越小输出越确定
            "max_retries": 3,  # API调用最大重试次数
            "retry_delay": 1.0,  # 重试延迟(秒)
            "request_timeout": 30.0,  # 请求超时(秒)
            "if_thinking": False,  # 是否启用思考模式(某些模型支持,如Qwen3)

            # 流式输出参数
            "min_sentence_length": 8,  # 最小句子长度(字符数或单词数,根据语言类型),用于流式句子合并
            "first_sentence_comma_delay_chars": 5,  # 首句逗号延迟字符数,遇到逗号后等待此数量的字符
                                                  # 如果期间出现句号等结束符则按结束符切分,否则按逗号切分
            "sentence_length_increment_rate": 2,  # 句子长度增量倍率,用于动态调整后续句子的最小长度
            "max_sentence_length": 200,  # 最大句子长度(字符数或单词数,根据语言类型),防止句子无限增长

            # 人设配置
            "system_prompt": "你叫林可可,是16岁的女高中生,性格活泼开朗,谈吐风趣。你正在和用户聊天,回答简短,控制在300字以内,不要用表情。",  # 系统提示词(保留作为向后兼容的fallback)
            "persona_name": "lincoco",  # 默认人设名称(对应llm/personas/目录下的文件名,不含扩展名)
            "persona_dir": "llm/personas",  # 人设文件目录(相对于项目根目录)
            "user_name": "林嘿嘿",  # 用户名(用于人设文件中的{{user_name}}变量替换)

            # 上下文配置
            "max_history_length": 50,  # 最大历史记录长度(对话轮数,每轮包含用户和助手两条消息)
                                     # 超过此数量会删除最旧的记录,防止内存无限增长
                                     # 影响LLM的上下文窗口大小

            # --- LLM引擎配置 ---
            "engines": {
                "openai": {  # OpenAI兼容引擎配置
                    "api_base": "http://127.0.0.1:5000/v1",  # API基础URL(OpenAI兼容格式)
                    "api_key": "",  # API密钥
                    "model_name": "qwen/qwen3-30b-a3b",  # 模型名称
                },
                "ollama": {  # Ollama本地引擎配置
                    "api_base": "http://219.223.187.56:11434/v1",  # API基础URL(OpenAI兼容格式,默认本地ollama服务)
                    "api_key": "",  # API密钥(本地ollama通常不需要,留空即可)
                    "model_name": "gemma3:27b",  # 模型名称(格式:模型名:标签,如"gemma3:27b"、"llama2"、"qwen"等)
                },
                "glm": {  # 智谱GLM引擎配置
                    "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",  # API基础URL
                    "api_key": "",  # API密钥
                    "model_name": "glm-4-flash-250414",  # 模型名称
                },
            },
        },

        # ====== 6. TTS语音合成配置 ======
        # 输出功能,将文本转换为语音,支持多引擎和性能优化
        "tts": {
            # TTS引擎选择
            "default_engine": "melotts",  # 当前使用的TTS引擎(edgetts、melotts)

            # TTS音频处理
            "silence_threshold": -40,  # 静音检测阈值(dB),用于检测音频中的静音段
            "min_silence_len": 30,  # 最小静音长度(毫秒),静音段超过此长度才进行裁剪
            "enable_trim_silence": True,  # 是否裁剪静音,减少空白时间
            "trim_keep_start": 20,  # 裁剪时开头固定保留长度(毫秒),防止误删
            "trim_keep_end": 350,  # 裁剪时结尾固定保留长度(毫秒),防止截断
            "enable_normalize_loudness": True,  # 是否启用响度归一化(统一音频响度,在裁剪静音之后应用)
            "playback_check_interval": 0.05,  # 播放检查间隔(秒),用于检测播放状态
            "playback_status_update_interval": 0.1,  # 播放状态更新间隔(秒),用于流式状态返回

            # TTS性能统计
            "enable_performance_stats": True,  # 是否启用性能统计,输出TTS生成耗时等性能信息

            # --- TTS缓存配置 ---
            "cache_enabled": True,  # 是否启用TTS内存缓存(减少重复请求)
            "cache_max_size": None,  # 最大缓存条目数(None表示不设上限,不淘汰旧缓存)

            # --- TTS文件缓存配置 ---
            "tts_file_cache_enabled": True,  # 是否启用TTS文件缓存(持久化,所有句子都缓存)
            "tts_file_cache_dir": "cache/tts_cache",  # TTS缓存目录路径(支持绝对路径,如"D:/cache/tts";相对路径则相对于项目根目录)
            "tts_file_cache_max_length": 20,  # 最大缓存长度(中文按字符数,英文按单词数,超过此长度的TTS音频不保存)
            "tts_file_cache_auto_repair": True,  # 是否自动修复缺失的缓存文件(True:启动时检查并修复,False:仅在实际使用时生成)

            # --- TTS引擎配置 ---
            "engines": {
                # EdgeTTS引擎配置
                "edge_tts": {
                    "is_streaming": False,  # 是否流式引擎(False=非流式)
                    "voice_map": {  # 语言-语音映射表
                        "zh": "zh-CN-XiaoyiNeural",  # 中文语音
                        "en": "en-US-AnaNeural",  # 英文语音
                        "ja": "zh-CN-XiaoyiNeural",  # 日文语音
                        "fr": "fr-FR-DeniseNeural",  # 法文语音
                        "es": "es-ES-ElviraNeural",  # 西班牙语语音
                        "de": "de-DE-KatjaNeural",  # 德文语音
                    },
                    "connection_timeout": 30.0,  # 连接超时时间(秒)
                    "enable_session_reuse": True,  # 是否启用会话复用(减少连接开销)
                },

                # MeloTTS引擎配置
                "melo_tts": {
                    "is_streaming": False,  # 是否流式引擎(False=非流式)
                    "api_url": "http://127.0.0.1:8101",  # MeloTTS API服务地址
                    "api_endpoint": "/tts",  # API端点路径
                    "init_endpoint": "/init",  # 模型初始化端点路径
                    "timeout": 30.0,  # HTTP请求超时时间(秒)
                    "model_language": "ZH",  # 要加载的模型语言(ZH=中文,EN=英文,JP=日文等),启动时会调用/init接口加载此语言的模型
                    "speaker_name_map": {  # 语言到说话人名称的映射(推荐使用,与WebUI一致,优先级高于speaker_id_map)
                        "zh": "ZH",  # 中文使用'ZH'说话人
                        "en": "EN-US",  # 英文使用'EN-US'说话人(可根据模型调整)
                    },
                    "speaker_id_map": {  # 语言到speaker_id的映射(已废弃,推荐使用speaker_name_map)
                        "zh": 0,
                        "en": 0,
                    },
                    "default_speaker_id": 0,  # 默认speaker_id(已废弃,推荐使用default_speaker_name)
                    "default_speaker_name": None,  # 默认说话人名称(如'ZH',如果不提供则使用模型默认的第一个说话人)
                    "sdp_ratio": 0.2,  # SDP比例(默认值,控制语音稳定性)
                    "noise_scale": 0.6,  # 噪声比例(默认值,控制语音表现力)
                    "noise_scale_w": 0.8,  # 噪声宽度比例(默认值,控制语音自然度)
                    "speed": 1.0,  # 语速(默认值,1.0为正常速度,大于1.0加快,小于1.0减慢)
                },
            },
        },

        # ====== 7. 日志配置 ======
        # 辅助功能,用于调试、监控和性能分析
        "logging": {
            # 文件日志
            "file_enabled": False,  # 是否启用文件日志,日志会保存到文件中
            "log_dir": "cache/logs",  # 日志文件目录路径(相对于项目根目录)

            # 控制台日志
            "enable_console": True,  # 是否输出到控制台(EventLogger用)
            "show_delta": True,  # 是否显示与上一事件的时间差(EventLogger用)
            "show_source": True,  # 是否显示代码来源前缀(如"manager.py"、"fun_asr_stream.py"),与类别区分开

            # 日志过滤配置
            "filtered_levels": ["INFO"],  # 允许输出的日志级别列表(白名单,如["DEBUG"]),只有在此列表中的级别才会输出,为空则不过滤级别
            "filtered_categories": ["THREAD", "EVENT", "MIC", "TIME", "CTX", "MANAGER", "VAD", "PUN"],  # 被过滤的日志类别列表(黑名单),这些类别的日志将不输出

            # 日志类别专属颜色配置
            # 支持的颜色名称: BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE,
            # BRIGHT_BLACK, BRIGHT_RED, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE, BRIGHT_MAGENTA, BRIGHT_CYAN, BRIGHT_WHITE
            # 可以组合样式: 如"GREEN+BOLD","RED+UNDERLINE"等
            "category_colors": {
                "ASR": "GREEN",
                "TTS": "BLUE",
                "LLM": "YELLOW",
                "PLAY": "MAGENTA",
                "VAD": "CYAN",
                "CTX": "BRIGHT_MAGENTA",
                "THREAD": "BRIGHT_CYAN",
                "MANAGER": "RED",
                "PUN": "YELLOW+BOLD",
                "PERSONA": "MAGENTA+BOLD",
                "SPE": "CYAN+BOLD",
                "EVENT": "BRIGHT_CYAN+BOLD",
            },

            # SequenceDiagram流程记录(图形化流程记录,EventLogger用)
            "enable_sequence_diagram": True,  # 是否启用sequenceDiagram记录
            "sequence_diagram_flow_timeout": 30.0,  # 流程超时时间(秒),超过此时间自动结束流程记录

            # 性能分析配置
            "performance_analysis": {
                "enabled": True,  # 是否启用响应耗时详细分析,在第一个TTS播放前输出
                "show_details": False,  # 是否输出各个阶段的细节(True=显示详细阶段信息,False=只显示总耗时)
            },
        },
    }

    def __init__(self, config_file: Optional[str] = None):
        """
        初始化配置管理器

        Args:
            config_file: 配置文件路径(JSON格式),如果为None则使用默认配置
        """
        self.config = self.DEFAULT_CONFIG.copy()
        self.config_file = config_file

        # 加载配置文件
        if config_file and os.path.exists(config_file):
            self.load_from_file(config_file)

        # 从环境变量加载配置(覆盖文件配置)
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
        # 验证系统参数
        assert self.config["system"]["thread_timeout"] > 0, "system.thread_timeout必须大于0"

        # 验证音频参数
        assert self.config["audio"]["sample_rate"] > 0, "sample_rate必须大于0"
        assert self.config["audio"]["chunk_size"] > 0, "chunk_size必须大于0"

        # 验证VAD参数
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

        # 验证ASR参数
        assert self.config["asr"]["result_timeout"] > 0, "asr.result_timeout必须大于0"
        assert self.config["asr"]["no_speech_threshold"] > 0, "asr.no_speech_threshold必须大于0"

        # 验证LLM参数
        assert self.config["llm"]["max_history_length"] > 0, "llm.max_history_length必须大于0"
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

