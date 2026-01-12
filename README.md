# Lincoco Voice Chatbot

一个基于语音识别(ASR)、大语言模型(LLM)和语音合成(TTS)的实时语音对话系统，支持自然的人机交互、打断机制、对话历史管理、声纹验证等高级功能。

## ✨ 核心特性

- 🎤 **实时语音识别**：基于 FunASR 的流式语音识别，支持预缓存机制避免丢失开头
- 🗣️ **多种 TTS 引擎**：支持 Edge TTS、MeloTTS 和 CosyVoice 三种引擎
  - **Edge TTS**：微软 Edge 浏览器的免费 TTS 服务，支持多种语言和音色
  - **MeloTTS**：Myshell 开源的高质量多语言 TTS，支持中、英、日等多种语言
  - **CosyVoice**：阿里达摩院开源的高质量语音合成，支持流式生成、多场景应用和3s极速复刻
- 🔐 **声纹验证**：基于 3D-Speaker 的声纹识别和验证，支持多声纹并行比对、滑动窗口验证、多场景注册
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：使用张量并行计算所有注册音频的相似度
  - 多场景录制：支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - 自动注册：首次运行时自动引导用户录制声纹注册音频
  - 嵌入缓存：支持缓存声纹嵌入向量，提高验证速度
  - GPU 加速：支持 CUDA 加速声纹验证
- 💬 **智能对话**：集成 OpenAI API，支持流式文本生成和句子分割
- 🎯 **打断机制**：支持延迟验证和 ASR 验证，实现自然的对话打断，智能识别误输入
- 📝 **对话管理**：自动管理对话历史，支持输入合并和上下文管理
  - 播放进度实时跟踪：实时更新已完成句子和当前播放句子
  - 对话历史记录优化：精确跟踪已播放文本，支持打断时记录
- 🎭 **人设系统**：支持多个人设配置，支持运行时动态切换和变量替换
  - 变量处理器：支持 `{{param_name}}` 格式的变量替换
  - 内置参数：当前时间、日期、星期几、用户名等
- 📊 **性能监控**：基于事件系统的统一日志和性能分析，支持响应耗时树形展示
  - 细粒度性能分析：ASR 模型推理时间、LLM API 请求等待时间、TTS 缓存命中状态
  - 事件时间轴：维护完整的事件时间轴记录
  - 序列图记录器：自动生成和记录系统交互序列图
- 🔄 **事件驱动架构**：采用事件驱动的 v3.0 架构，线程安全、易于扩展
  - StateManager：事件驱动版本的状态管理器，支持原子性状态转换
  - ContextManager：统一管理 TaskContext 的创建、访问和销毁
  - ThreadManager：统一管理所有工作线程的生命周期
  - EventLogger：统一日志处理器，支持日志分类和时间差计算
  - SequenceDiagramRecorder：序列图记录器，自动记录和生成系统交互序列图
- 💾 **TTS 缓存系统**：支持文件缓存和内存缓存，显著提升响应速度
  - **LRU 内存缓存**：减少重复请求，可配置最大缓存大小
  - **持久化文件缓存**：所有生成的 TTS 音频保存到文件，永久缓存
  - **自然音频支持**：同一文本可保存多个不同版本，增加语音自然变化
    - 版本数量可配置（如5个版本）
    - 加载时随机选择一个版本
    - 仅支持自然音频的引擎可用（如CosyVoice）
    - 每个文本的多个版本文件使用后缀区分（如`hash_0.mp3`、`hash_1.mp3`等）
  - 按引擎和语音组织：便于管理和清理
  - 自动修复：启动时检查并修复缺失的缓存文件

## 🏗️ 系统架构

### 核心流程

```
麦克风采集 → VAD → ASR → 对话调度器 → LLM流式生成 → TTS+播放
```

### 线程架构

系统采用双系统架构（主系统+副系统），包含9个线程：

- **主线程**：系统初始化和状态管理
- **常驻后台线程**：音频采集、VAD后台、事件总线处理
- **按需启动线程**：ASR识别、LLM+TTS处理、音频播放等

详细架构说明请参考 [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md)。

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Conda（推荐使用 conda 环境管理）

### 安装步骤

1. **克隆项目**
```bash
git clone https://github.com/ngyygm/lincoco.git
cd lincoco
```

2. **创建 Conda 环境**
```bash
# 创建环境
conda create -n voice python=3.10
conda activate voice

# 安装依赖
pip install -r requirements.txt
```

3. **配置系统**

Lincoco 提供了灵活的配置管理方式，支持多种配置源：

### 配置方式（优先级从高到低）

1. **环境变量** - 最高优先级，用于临时覆盖配置
   - 命名规范：`VOICE_CHATBOT_<SECTION>_<KEY>`
   - 示例：`export VOICE_CHATBOT_LLM_TEMPERATURE=0.9`

2. **config.json 文件** - 推荐方式，便于版本控制和部署
   - 系统会自动查找 `config.json` 或 `voice_chatbot/config.json`
   - 只需包含需要修改的参数，未指定的使用默认值
   - 使用 `python generate_config.py` 生成完整配置文件

3. **默认配置** - 在 `voice_chatbot/utils/config.py` 中定义

### 快速配置

**方式 1：使用 config.json（推荐）**

```bash
# 生成默认配置文件
python generate_config.py

# 编辑 config.json 文件
# 只需修改你需要改变的参数
```

**方式 2：直接修改 config.py**

直接编辑 `voice_chatbot/utils/config.py` 中的 `DEFAULT_CONFIG` 字典。

### 主要配置项

- **LLM 配置**（在 `llm.engines` 部分）
  - API 地址和密钥
  - 模型名称
  - 提供商选择（openai/ollama/glm）

- **TTS 配置**（在 `tts.default_engine` 和 `tts.engines` 部分）
  - 默认引擎选择
  - 各引擎 API 地址
  - 音频参数

- **声纹验证配置**（在 `asr.speaker_verification` 部分）
  - 验证阈值
  - 注册音频路径
  - 滑动窗口参数

### 配置示例

```json
{
  "llm": {
    "provider": "openai",
    "engines": {
      "openai": {
        "api_base": "http://127.0.0.1:5000/v1",
        "api_key": "your-api-key",
        "model_name": "qwen3-1.7b"
      }
    }
  },
  "tts": {
    "default_engine": "cosyvoice_tts",
    "engines": {
      "cosyvoice_tts": {
        "api_url": "http://219.223.187.56:9880"
      }
    }
  }
}
```

### 配置文件说明

系统会自动在以下位置查找 `config.json` 文件：
1. 当前工作目录：`./config.json`
2. voice_chatbot 目录：`./voice_chatbot/config.json`

如果未找到 `config.json` 文件，系统将使用默认配置（不会报错）。

### 代码中使用配置

```python
from voice_chatbot.utils.config import Config

# 自动查找 config.json，如果不存在则使用默认配置
config = Config()

# 访问配置
user_name = config.get("system.user_name")
temperature = config.get("llm.temperature")

# 修改配置
config.set("llm.temperature", 0.9)

# 保存到文件
config.export_config("custom_config.json")
```

### 部分配置示例

只需在 `config.json` 中包含需要修改的参数，其他参数将使用默认值：

```json
{
  "system": {
    "user_name": "我的名字",
    "greeting": {
      "enabled": true,
      "text": "你好，我已准备好与你对话！"
    }
  },
  "llm": {
    "temperature": 0.9
  }
}
```

完整的配置示例请参考 [config.example.json](config.example.json)。

4. **运行程序**
```bash
conda activate voice
python main.py
```

**注意**：
- 如果配置中选择了 MeloTTS 引擎，程序会自动启动 MeloTTS API 服务（默认端口 8101）
- 如果端口已被占用或服务已在运行，程序会检测并使用现有服务
- 模型预加载：服务启动后会自动预加载配置中指定的模型语言，减少首次请求延迟

## 📁 项目结构

```
lincoco/
├── main.py                          # 主程序入口（含MeloTTS自动启动）
├── requirements.txt                 # Python 依赖包
├── README.md                        # 项目说明文档
│
├── PROJECT_OUTLINE.md               # 项目大纲和架构说明
├── DEVELOPMENT_LOG.md               # 开发日志
├── Git快速参考.md                   # Git 快速参考
├── Git提交指南.md                   # Git 提交指南
│
├── plugin/                         # 扩展功能插件目录
│   ├── 3D-Speaker/                # 3D-Speaker 声纹识别模块（完整集成）
│   │   ├── speakerlab/             # 声纹识别核心库
│   │   │   ├── models/             # 模型定义
│   │   │   ├── process/            # 处理脚本
│   │   │   └── utils/              # 工具函数
│   │   └── README.md               # 3D-Speaker 官方文档
│   │
│   ├── CosyVoice/                 # CosyVoice 语音合成模块（完整集成）
│   │   ├── cosyvoice/              # CosyVoice 核心库
│   │   │   ├── bin/                # 可执行脚本
│   │   │   ├── cli/                # 命令行工具
│   │   │   ├── flow/               # Flow 模型
│   │   │   ├── hifigan/            # HiFi-GAN 模型
│   │   │   ├── llm/                # LLM 组件
│   │   │   ├── transformer/        # Transformer 模型
│   │   │   ├── tokenizer/          # 分词器
│   │   │   ├── utils/              # 工具函数
│   │   │   └── dataset/            # 数据集处理
│   │   ├── api.py                  # API 服务
│   │   ├── webui.py                # WebUI 界面
│   │   ├── examples/               # 示例脚本
│   │   ├── third_party/            # 第三方依赖
│   │   └── README.md               # CosyVoice 官方文档
│   │
│   └── MeloTTS/                   # MeloTTS 语音合成模块（完整集成）
│       ├── melo/                   # MeloTTS 核心库
│       │   ├── configs/            # 配置文件
│       │   ├── data/               # 数据处理
│       │   ├── text/               # 文本处理
│       │   ├── models/             # 模型定义
│       │   └── infer.py            # 推理接口
│       ├── MeloTTS-ONNX/           # ONNX 优化版本
│       │   ├── melo_onnx/          # ONNX 核心
│       │   └── requirements.txt    # ONNX 依赖
│       ├── api.py                  # HTTP API 服务
│       ├── api_onnx.py             # ONNX API 服务
│       ├── webui.py                # WebUI 界面
│       ├── hf_download/            # HuggingFace 缓存（不提交）
│       ├── docs/                   # 文档
│       └── README.md               # MeloTTS 官方文档
│
├── voice_chatbot/                  # 核心模块目录
│   │
│   ├── asr/                        # ASR 模块
│   │   ├── fun_asr_stream.py       # 流式语音识别（FunASR）
│   │   ├── webrtc_vad.py           # 语音活动检测
│   │   ├── speaker_verification.py # 声纹验证（集成3D-Speaker）
│   │   ├── punctuation_restore.py  # 标点符号恢复
│   │   └── __init__.py             # 模块初始化
│   │
│   ├── dialogue/                   # 对话管理模块（v3.0事件驱动架构）
│   │   ├── manager.py              # 核心调度器
│   │   ├── state.py                # 状态机
│   │   ├── state_manager.py        # 事件驱动状态管理器
│   │   ├── events.py               # 事件系统
│   │   ├── context_manager.py      # 上下文管理器
│   │   ├── thread_manager.py       # 线程管理器
│   │   ├── task_context.py         # 任务上下文
│   │   └── voice_main.py          # 核心功能入口
│   │
│   ├── llm/                        # LLM 模块
│   │   ├── llm_openai_stream.py    # OpenAI 流式生成
│   │   ├── persona_manager.py      # 人设管理器
│   │   ├── variable_processor.py   # 变量处理器
│   │   ├── PERSONA_GUIDE.md        # 人设使用指南
│   │   └── personas/               # 人设文件目录
│   │       ├── default.md          # 默认人设
│   │       ├── assistant.md        # 助手人设
│   │       └── lincoco.md          # Lincoco 人设
│   │
│   ├── tts/                        # TTS 模块
│   │   ├── edge_tts_engine.py      # Edge TTS 引擎
│   │   ├── melo_tts_engine.py      # MeloTTS 引擎（HTTP API调用）
│   │   ├── cosyvoice_tts_engine.py  # CosyVoice 引擎（HTTP API调用，支持3s极速复刻）
│   │   ├── tts_file_cache.py       # TTS 文件缓存（支持自然音频多版本）
│   │   └── __init__.py             # 模块初始化
│   │
│   └── utils/                      # 工具模块
│       ├── config.py              # 配置管理
│       ├── logger.py              # 日志系统
│       ├── microphone.py          # 麦克风采集
│       ├── audio_player.py        # 音频播放器
│       ├── timer.py              # 性能计时器
│       ├── event_logger.py        # 统一事件日志处理器
│       ├── performance_monitor.py # 性能监控器
│       ├── sequence_diagram_recorder.py  # 序列图记录器
│       └── __init__.py            # 工具模块初始化
│
├── cache/                          # 缓存目录（不提交音频文件）
│   ├── speaker_verification_audio/ # 声纹注册音频
│   ├── tts_cache/                  # TTS 缓存
│   │   ├── edge_tts_cache/         # Edge TTS 缓存
│   │   └── melo_tts_cache/         # MeloTTS 缓存
│   └── logs/                       # 日志目录（不提交）
│
├── logs/                          # 日志输出目录（不提交）
│
├── pretrained/                     # 预训练模型（不提交）
│   └── speech_campplus_sv_zh-cn_16k-common/
│       └── campplus_cn_common.bin
│
└── .gitignore                     # Git 忽略配置
```

## ⚙️ 配置说明

所有配置项在 `voice_chatbot/utils/config.py` 中定义，**直接修改该文件中的配置参数即可**。

### 配置文件结构

配置文件采用分层结构，包含以下主要部分：

#### 1. 系统配置 (system)
```python
"system": {
    "thread_timeout": 1.0,              # 通用线程等待超时(秒)
    "user_name": "林嘿嘿",              # 用户名(用于人设文件中的{{user_name}}变量替换，以及声纹注册文件名)
    "greeting": {
        "enabled": True,                # 是否启用问候语
        "text": "你好！我是林可可，很高兴见到你！",  # 问候语文本
    },
}
```

#### 2. 基础音频配置 (audio)
```python
"audio": {
    "sample_rate": 16000,              # 音频采样率(Hz)，16000为标准
    "chunk_size": 1024,                 # 每次读取的采样点数(约64ms)
    "channels": 1,                      # 声道数(1=单声道，2=立体声)
}
```

#### 3. VAD语音活动检测配置 (vad)
```python
"vad": {
    "mode": 3,                          # VAD模式(0-3)，数字越大越敏感，3=最敏感
    "activation_rate": 0.1,              # 激活率(0.0-1.0)，越小越敏感(建议0.2-0.4)
    "adaptive_threshold": False,        # 是否启用自适应阈值
    "min_activation_rate": 0.15,        # 最小激活率(自适应阈值时)
    "max_activation_rate": 0.5,         # 最大激活率(自适应阈值时)
    "check_interval": 0.1,              # 检测间隔(秒)
    "thread_sleep": 0.01,               # 线程休眠间隔(秒)
}
```

#### 4. ASR语音识别配置 (asr)
```python
"asr": {
    # ASR模型配置
    "model_path": "paraformer-zh-streaming",  # ASR模型路径
    "language": "zh",                  # 语言设置(默认中文)
    "preload_model": True,             # 是否预加载模型

    # 流式识别参数
    "chunk_size": [0, 10, 5],          # chunk大小配置，影响识别延迟
    "encoder_chunk_look_back": 4,      # encoder上下文回溯chunks数
    "decoder_chunk_look_back": 1,      # decoder上下文回溯chunks数
    "pre_buffer_seconds": 1.0,         # 预缓存时长(秒)

    # ASR队列和超时配置
    "audio_queue_maxsize": 100,        # 音频队列最大大小
    "result_timeout": 30.0,            # ASR结果等待超时(秒)
    "result_time_buffer": 0.1,         # 结果时间缓冲(秒)
    "max_result_age": 60.0,            # 结果最大保留时间(秒)

    # ASR停止条件配置
    "no_speech_threshold": 0.4,        # 无语音阈值(秒)，建议0.8-1.2秒

    # ASR增强功能
    "enable_deduplication": False,      # 是否启用流式去重
    "invalid_recognition_words": ["", "i", "嗯", "啊", "呃", "哦", "额"],  # 无效识别词列表

    # --- 声纹验证配置 ---
    "speaker_verification": {
        "enabled": True,                                     # 是否启用声纹锁
        "model_id": "iic/speech_campplus_sv_zh-cn_16k-common",  # 3D-Speaker模型ID
        "enroll_audio_path": "cache/speaker_verification_audio",  # 注册音频文件夹路径
        "similarity_threshold": 0.4,                          # 相似度阈值(0.0-1.0)
        "min_audio_length": 0.5,                             # 最小音频长度(秒)
        "cache_embedding": True,                             # 是否缓存嵌入向量
        "record_duration": 8.0,                              # 录制注册音频时长(秒)
        "auto_record": True,                                 # 是否自动录制注册音频
        "reading_texts": [                                   # 声纹注册朗读文本列表
            "欢迎使用语音助手系统，今天天气真不错。",
            "语音识别技术越来越智能了，这真的很令人兴奋。",
            "这是一个安静的测试，请确保声音清晰可辨。",
            "开始愉快的对话吧！",
        ],
        # 滑动窗口配置
        "sliding_window": {
            "enabled": True,                 # 是否启用滑动窗口验证
            "window_length": 0.6,            # 窗口长度(秒)
            "window_stride": 0.2,            # 滑动步长(秒)
            "consecutive_hits": 2,           # 连续命中次数
        },
    },

    # --- 标点恢复配置 ---
    "punctuation_restore": {
        "enabled": True,                 # 是否启用标点恢复
        "model": "ct-punc",              # 标点恢复模型名称
    },
}
```

#### 5. LLM大语言模型配置 (llm)
```python
"llm": {
    # LLM基础参数
    "provider": "openai",               # 服务提供商: openai、ollama 或 glm
    "max_tokens": 1024,                 # 最大生成token数
    "temperature": 0.7,                # 生成温度(0.0-2.0)
    "max_retries": 3,                   # API调用最大重试次数
    "retry_delay": 1.0,                 # 重试延迟(秒)
    "request_timeout": 30.0,            # 请求超时(秒)
    "if_thinking": False,              # 是否启用思考模式

    # 流式输出参数
    "min_sentence_length": 8,                      # 最小句子长度(字符数)
    "first_sentence_comma_delay_chars": 10,         # 首句逗号延迟字符数
    "sentence_length_increment_rate": 2,           # 句子长度增量倍率
    "max_sentence_length": 200,                    # 最大句子长度(字符数)

    # 人设配置
    "system_prompt": "你叫林可可...",              # 系统提示词(fallback)
    "persona_name": "lincoco",                     # 默认人设名称
    "persona_dir": "llm/personas",                 # 人设文件目录

    # 上下文配置
    "max_history_length": 50,                      # 最大历史记录长度(对话轮数)

    # --- LLM引擎配置 ---
    "engines": {
        "openai": {                                # OpenAI兼容引擎
            "api_base": "http://127.0.0.1:5000/v1",  # API基础URL
            "api_key": "",                        # API密钥
            "model_name": "qwen/qwen3-30b-a3b",    # 模型名称
        },
        "ollama": {                                # Ollama本地引擎
            "api_base": "http://219.223.187.56:11434/v1",  # API基础URL
            "api_key": "",                        # API密钥(本地通常为空)
            "model_name": "gemma3:27b",            # 模型名称
        },
        "glm": {                                   # 智谱GLM引擎
            "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",  # API基础URL
            "api_key": "",                        # API密钥
            "model_name": "glm-4-flash-250414",    # 模型名称
        },
    },
}
```

**重要提示**：如果使用 OpenAI 兼容的本地服务（如 vLLM、LM Studio），请修改 `llm.engines.openai` 中的 `api_base` 和 `model_name`。

#### 6. TTS语音合成配置 (tts)
```python
"tts": {
    # TTS引擎选择
    "default_engine": "cosyvoice_tts",            # 当前使用的TTS引擎: edge_tts、melotts、cosyvoice_tts

    # TTS音频处理
    "silence_threshold": -40,                    # 静音检测阈值(dB)
    "min_silence_len": 30,                       # 最小静音长度(毫秒)
    "enable_trim_silence": True,                 # 是否裁剪静音
    "trim_keep_start": 20,                       # 裁剪时开头固定保留长度(毫秒)
    "trim_keep_end": 350,                        # 裁剪时结尾固定保留长度(毫秒)
    "enable_normalize_loudness": True,            # 是否启用响度归一化
    "playback_check_interval": 0.05,             # 播放检查间隔(秒)
    "playback_status_update_interval": 0.1,      # 播放状态更新间隔(秒)

    # TTS性能统计
    "enable_performance_stats": True,            # 是否启用性能统计

    # --- TTS缓存配置 ---
    "cache_enabled": True,                       # 是否启用内存缓存
    "cache_max_size": None,                      # 最大缓存条目数(None=无上限)

    # --- TTS文件缓存配置 ---
    "tts_file_cache_enabled": True,              # 是否启用文件缓存(持久化)
    "tts_file_cache_dir": "cache/tts_cache",     # TTS缓存目录路径
    "tts_file_cache_max_length": 20,             # 最大缓存长度(字符数，超过不保存)
    "tts_file_cache_auto_repair": True,          # 是否自动修复缺失的缓存文件
    "natural_audio_count": 5,                    # 自然音频数量(0=不启用，>0表示同一文本保存多个版本)

    # --- TTS引擎配置 ---
    "engines": {
        "edge_tts": {                            # EdgeTTS引擎
            "is_streaming": False,               # 是否流式引擎
            "support_natural_audio": False,       # 是否支持自然音频
            "voice_map": {                       # 语言-语音映射表
                "zh": "zh-CN-XiaoyiNeural",
                "en": "en-US-AnaNeural",
                "ja": "zh-CN-XiaoyiNeural",
                "fr": "fr-FR-DeniseNeural",
                "es": "es-ES-ElviraNeural",
                "de": "de-DE-KatjaNeural",
            },
            "connection_timeout": 30.0,           # 连接超时时间(秒)
            "enable_session_reuse": True,         # 是否启用会话复用
        },

        "melo_tts": {                            # MeloTTS引擎
            "is_streaming": False,               # 是否流式引擎
            "support_natural_audio": False,       # 是否支持自然音频
            "api_url": "http://127.0.0.1:8101",  # API服务地址
            "api_endpoint": "/tts",              # API端点路径
            "init_endpoint": "/init",            # 模型初始化端点路径
            "timeout": 30.0,                     # HTTP请求超时时间(秒)
            "model_language": "ZH",              # 模型语言(ZH=中文, EN=英文, JP=日文等)
            "speaker_name_map": {                 # 语言到说话人名称映射
                "zh": "ZH",
                "en": "EN-US",
            },
            "default_speaker_name": None,        # 默认说话人名称
            "sdp_ratio": 0.2,                    # SDP比例(控制语音稳定性)
            "noise_scale": 0.6,                   # 噪声比例(控制语音表现力)
            "noise_scale_w": 0.8,                # 噪声宽度比例(控制语音自然度)
            "speed": 1.0,                         # 语速(1.0为正常速度)
        },

        "cosyvoice_tts": {                       # CosyVoice引擎
            "is_streaming": False,               # 是否流式引擎
            "support_natural_audio": True,        # 是否支持自然音频
            "api_url": "http://219.223.187.56:9880",  # API服务地址
            "set_model_endpoint": "/set_model",  # 设置参考音频端点路径
            "generate_endpoint": "/generate",    # 生成音频端点路径
            "get_model_endpoint": "/get_model",  # 获取当前参考音频配置端点路径
            "timeout": 30.0,                     # HTTP请求超时时间(秒)
            "reference_wav_path": None,          # 参考音频文件路径(用于3s极速复刻)
            "reference_text": None,              # 参考文本(可选)
            "use_filename_as_text": True,         # 是否使用文件名作为参考文本
            "speed": 1.0,                        # 语速
        },
    },
}
```

**TTS引擎选择**：通过修改 `tts.default_engine` 来选择使用的 TTS 引擎（`edge_tts`、`melotts` 或 `cosyvoice_tts`）。

#### 7. 日志配置 (logging)
```python
"logging": {
    # 文件日志
    "file_enabled": False,               # 是否启用文件日志
    "log_dir": "cache/logs",             # 日志文件目录路径

    # 控制台日志
    "enable_console": True,              # 是否输出到控制台
    "show_delta": True,                  # 是否显示与上一事件的时间差
    "show_source": True,                 # 是否显示代码来源前缀

    # 日志过滤配置
    "filtered_levels": ["INFO"],         # 允许输出的日志级别列表(DEBUG, INFO, WARN, ERROR)
    "filtered_categories": ["ASR", "TTS", "LLM", "PLAY", "MAIN"],  # 允许输出的日志类别列表

    # 日志类别专属颜色配置
    "category_colors": {
        "ASR": "GREEN",
        "TTS": "BLUE",
        "LLM": "YELLOW",
        "PLAY": "MAGENTA",
        "VAD": "CYAN",
        "CTX": "BRIGHT_MAGENTA",
        "THREAD": "BRIGHT_CYAN",
        "MANAGER": "RED",
        # ... 更多颜色配置
    },

    # SequenceDiagram流程记录
    "enable_sequence_diagram": True,     # 是否启用sequenceDiagram记录
    "sequence_diagram_flow_timeout": 30.0,  # 流程超时时间(秒)

    # 性能分析配置
    "performance_analysis": {
        "enabled": True,                 # 是否启用响应耗时详细分析
        "show_details": True,            # 是否输出各个阶段的细节
    },
}
```

### 配置方法

1. **直接修改配置文件**：编辑 `voice_chatbot/utils/config.py`，修改相应配置项的值
2. **保存并重启**：修改配置后保存文件，重启程序即可生效
3. **配置验证**：程序启动时会自动验证配置参数的合法性，如有错误会提示

### 常见配置场景

#### 场景1：使用 OpenAI 官方 API
```python
"llm": {
    "provider": "openai",
    "engines": {
        "openai": {
            "api_base": "https://api.openai.com/v1",
            "api_key": "sk-xxxxxxxxxxxxxxxxxxxx",
            "model_name": "gpt-4",
        },
    },
}
```

#### 场景2：使用本地 vLLM 服务
```python
"llm": {
    "provider": "openai",
    "engines": {
        "openai": {
            "api_base": "http://127.0.0.1:8000/v1",
            "api_key": "empty",  # 本地服务可以使用空字符串
            "model_name": "Qwen/Qwen2.5-7B-Instruct",
        },
    },
}
```

#### 场景3：禁用声纹验证
```python
"asr": {
    "speaker_verification": {
        "enabled": False,  # 设置为False即可禁用
    },
}
```

#### 场景4：切换 TTS 引擎
```python
"tts": {
    "default_engine": "edge_tts",  # 或 "melo_tts", "cosyvoice_tts"
}
```

详细配置说明请参考 [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md#-配置参数)。

## 🎯 主要功能

### 1. 实时语音识别

- **流式识别**：基于 FunASR 的实时语音识别
- **预缓存机制**：避免丢失语音开头部分
- **去重处理**：智能处理识别结果的重复和纠错
- **VAD 配合**：VAD 检测到语音活动时自动启动 ASR
- **声纹验证**：基于 3D-Speaker 的声纹识别，支持多声纹并行比对
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：优化声纹验证算法，使用张量并行计算所有注册音频的相似度
  - 滑动窗口：支持滑动窗口模式，提高声纹验证的准确性和灵活性
  - 多场景录制：支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - 自动注册：首次运行时自动引导用户录制声纹注册音频
  - 嵌入缓存：支持缓存声纹嵌入向量，提高验证速度
- **预缓存集成**：声纹验证正确使用预缓存的音频（VAD 检测前的音频）
- **无缝衔接**：声纹验证和 ASR 识别的音频无缝衔接，无重复或遗漏

### 2. 智能对话管理

- **流式生成**：LLM 流式生成文本，实时响应
- **句子分割**：智能检测完整句子，支持首句快速响应
- **对话历史**：自动管理对话历史，支持上下文理解
- **输入合并**：支持在生成过程中合并新的用户输入

### 3. 打断机制

- **延迟验证**：检测到语音后延迟验证，避免误判
- **ASR 验证**：暂停后通过 ASR 验证是否为有效输入
- **误识别处理**：当 ASR 识别结果为 0 个字符时，自动判断为误识别，继续 LLM+TTS 处理，不中断当前任务
- **状态管理**：精确的状态转换和任务取消机制

### 4. 人设系统

- **多 personae**：支持多个人设配置文件
- **动态切换**：运行时动态切换人设
- **参数替换**：支持时间、日期、用户名等参数自动替换
- **Markdown 格式**：人设文件支持 Markdown 格式

### 5. TTS 缓存系统

- **文件缓存**：持久化缓存所有生成的 TTS 音频
- **按引擎分类**：根据不同 TTS 引擎（Edge TTS、MeloTTS、CosyVoice）分别存储
- **按长度分类**：根据文本长度自动分类存储
- **自然音频支持**：同一文本可保存多个不同版本，增加语音自然变化
  - 版本数量可配置（如5个版本）
  - 加载时随机选择一个版本
  - 仅支持自然音频的引擎可用（如CosyVoice）
  - 每个文本的多个版本文件使用后缀区分（如`hash_0.mp3`、`hash_1.mp3`等）
  - 引擎配置中通过`support_natural_audio`标识是否支持自然音频
- **缓存命中**：生成前检查缓存，提高响应速度
- **目录组织**：按语音和长度范围组织缓存文件
  - `cache/tts_cache/edge_tts_cache/` - Edge TTS 缓存
  - `cache/tts_cache/melo_tts_cache/` - MeloTTS 缓存
  - `cache/tts_cache/cosyvoice_cache/` - CosyVoice 缓存（支持自然音频）
  - 每个引擎目录下按语音名称和长度范围组织

## 📊 性能特性

- **统一事件日志**：基于事件系统的统一日志管理
- **细粒度性能分析**：精确记录各阶段耗时（ASR、LLM、TTS等）
- **内存优化**：音频预缓存、内存播放、及时清理
- **线程安全**：完整的锁机制和任务隔离

## 🔧 开发环境

### 开发要求

- **测试环境**：必须使用 conda 环境 `voice` 进行开发和测试
- **激活环境**：`conda activate voice`
- **运行程序**：`python main.py`（位于项目根目录）

### 开发规范

- 每次完成功能开发或测试后，必须在 `DEVELOPMENT_LOG.md` 中记录
- 修改功能列表时，同步更新 `PROJECT_OUTLINE.md` 中的标记
- 所有模块通过事件系统统一输出日志

## 📚 文档

- [PROJECT_OUTLINE.md](PROJECT_OUTLINE.md) - 详细的项目架构和功能说明
- [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) - 开发日志
- [PERSONA_GUIDE.md](voice_chatbot/llm/PERSONA_GUIDE.md) - 人设系统使用指南

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[待添加许可证信息]

## ⚠️ 注意事项

- 项目中的 `cache/tts_cache/` 目录包含大量音频缓存文件，已配置 `.gitignore` 忽略音频文件，但保留 `_index.json` 索引文件
- `plugin/` 目录包含所有扩展功能模块：
  - `plugin/MeloTTS/` 目录是独立的 git 仓库，不会被提交到主仓库（如需使用，请单独克隆或作为子模块添加）
  - `plugin/MeloTTS/hf_download/` 目录包含 Hugging Face 模型下载缓存，不会被提交到 Git
  - `plugin/MeloTTS/out/` 目录包含输出文件，不会被提交到 Git
  - `plugin/CosyVoice/.git/` 是 CosyVoice 的内部 git 仓库，不会被提交到主仓库
  - `plugin/3D-Speaker/.git/` 是 3D-Speaker 的内部 git 仓库，不会被提交到主仓库
- 日志文件保存在 `cache/logs/` 和 `logs/` 目录，不会被提交到 Git
- 请确保正确配置环境变量（如 OpenAI API Key）
- **MeloTTS 自动启动**：如果配置中选择了 MeloTTS 引擎，程序会在启动时自动检测端口占用情况并启动 API 服务
- **MeloTTS 模型预加载**：服务启动后自动预加载配置中指定的模型语言，避免首次请求时的模型加载延迟

## 🔗 相关链接

- [FunASR](https://github.com/alibaba-damo-academy/FunASR) - 语音识别框架
- [Edge TTS](https://github.com/rany2/edge-tts) - 语音合成服务
- [MeloTTS](https://github.com/myshell-ai/MeloTTS) - 高质量多语言语音合成
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) - 高质量语音合成（阿里达摩院）
- [3D-Speaker](https://github.com/alibaba-damo-academy/3D-Speaker) - 声纹识别和验证
- [OpenAI API](https://platform.openai.com/docs) - 大语言模型 API

---

**最后更新**: 2026-01-07
**维护者**: ngyygm
