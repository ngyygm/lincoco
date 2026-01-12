# Lincoco Voice Chatbot

一个基于语音识别(ASR)、大语言模型(LLM)和语音合成(TTS)的实时语音对话系统，支持自然的人机交互、打断机制、对话历史管理、声纹验证等高级功能。

## ✨ 核心特性

- 🎤 **实时语音识别**：基于 FunASR 的流式语音识别，支持预缓存机制避免丢失开头
- 🗣️ **多种 TTS 引擎**：支持 Edge TTS、MeloTTS 和 CosyVoice 三种引擎
    - 自然音频支持：同一文本可保存多个不同版本，增加语音自然变化
  - **Edge TTS**：微软 Edge 浏览器的免费 TTS 服务，支持多种语言和音色
  - **MeloTTS**：Myshell 开源的高质量多语言 TTS，支持中、英、日等多种语言
  - **CosyVoice**：阿里达摩院开源的高质量语音合成，支持流式生成、多场景应用和3s极速复刻
    - 3s极速复刻：基于参考音频快速克隆语音，实现个性化音色
    - HTTP API调用：通过API服务进行语音合成，支持分布式部署
- 🔐 **声纹验证**：基于 3D-Speaker 的声纹识别和验证，支持多声纹并行比对、滑动窗口验证、多场景注册
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：使用张量并行计算所有注册音频的相似度
  - 滑动窗口：支持滑动窗口模式，提高声纹验证的准确性和灵活性
  - 多场景录制：支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - 自动注册：首次运行时自动引导用户录制声纹注册音频
  - 嵌入缓存：支持缓存声纹嵌入向量，提高验证速度
  - GPU 加速：支持 CUDA 加速声纹验证
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：使用张量并行计算所有注册音频的相似度
  - 滑动窗口：支持滑动窗口模式，提高声纹验证的准确性和灵活性
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

3. **配置环境变量**

创建 `.env` 文件（参考 `.env.example`）并配置必要的参数：
```env
# OpenAI API 配置
OPENAI_API_KEY=your_api_key_here
OPENAI_API_BASE=https://api.openai.com/v1

# 其他配置...
```

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

主要配置项在 `voice_chatbot/utils/config.py` 中定义，支持通过环境变量和配置文件进行配置。

### 关键配置参数

#### LLM 配置
```python
llm.provider = "openai"               # LLM服务提供商：openai、ollama 或 glm
llm.persona_name = "lincoco"          # 默认人设名称
llm.user_name = "林嘿嘿"              # 用户名
llm.max_tokens = 1024                 # 最大生成token数
llm.temperature = 0.7                 # 生成温度
llm.max_history_length = 50           # 最大历史记录长度（对话轮数）
```

#### TTS 配置
```python
tts.default_engine = "cosyvoice_tts"                 # TTS引擎：edge_tts、melo_tts、cosyvoice_tts
tts.tts_file_cache_enabled = True     # 启用TTS文件缓存
tts.enable_trim_silence = True        # 启用静音裁剪

# 自然音频配置
tts.natural_audio_count = 5         # 自然音频数量（0=不启用，>0表示同一文本保存多个版本）
tts.tts_file_cache_auto_repair = True  # 是否自动修复缺失的缓存文件

# MeloTTS 特定配置
tts.engines.melo_tts.model_language = "ZH"  # 模型语言：ZH=中文, EN=英文, JP=日文等
tts.engines.melo_tts.api_url = "http://127.0.0.1:8101"  # API 服务地址

# CosyVoice TTS 特定配置
tts.engines.cosyvoice_tts.api_url = "http://127.0.0.1:9880"  # API 服务地址
tts.engines.cosyvoice_tts.reference_wav_path = None  # 参考音频文件路径（用于3s极速复刻）
tts.engines.cosyvoice_tts.reference_text = None  # 参考文本（可选）
tts.engines.cosyvoice_tts.use_filename_as_text = True  # 是否使用文件名作为参考文本
tts.engines.cosyvoice_tts.speed = 1.0  # 语速
tts.engines.cosyvoice_tts.support_natural_audio = True  # 是否支持自然音频
```

#### 声纹验证配置
```python
asr.speaker_verification.enabled = True            # 启用声纹验证
asr.speaker_verification.enroll_audio_path = "cache/speaker_verification_audio"  # 注册音频路径
asr.speaker_verification.model_id = "iic/speech_campplus_sv_zh-cn_16k-common"  # 声纹模型
asr.speaker_verification.similarity_threshold = 0.3   # 相似度阈值（0-1）
asr.speaker_verification.min_audio_length = 0.5      # 最小音频长度（秒）
asr.speaker_verification.auto_record = True         # 自动录制注册音频
asr.speaker_verification.cache_embedding = True      # 缓存嵌入向量
asr.speaker_verification.record_duration = 8.0      # 录制注册音频时长（秒）
asr.speaker_verification.sliding_window.enabled = True  # 启用滑动窗口模式
asr.speaker_verification.sliding_window.window_length = 0.6  # 窗口长度（秒）
asr.speaker_verification.sliding_window.window_stride = 0.2  # 滑动步长（秒）
```

#### ASR参数
```python
asr.no_speech_threshold = 0.4     # 无语音阈值（秒）
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
