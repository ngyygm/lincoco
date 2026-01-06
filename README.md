# Lincoco Realtime Voice Chatbot

一个基于语音识别(ASR)、大语言模型(LLM)和语音合成(TTS)的实时语音对话系统，支持自然的人机交互、打断机制、对话历史管理、声纹验证等高级功能。

## ✨ 核心特性

- 🎤 **实时语音识别**：基于 FunASR 的流式语音识别，支持预缓存机制避免丢失开头
- 🔐 **声纹验证**：基于 3D-Speaker 的声纹识别和验证，支持多声纹并行比对、滑动窗口验证、多场景注册
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：使用张量并行计算所有注册音频的相似度
  - 滑动窗口：支持滑动窗口模式，提高声纹验证的准确性和灵活性
  - 多场景录制：支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - 自动注册：首次运行时自动引导用户录制声纹注册音频
  - 嵌入缓存：支持缓存声纹嵌入向量，提高验证速度
  - GPU 加速：支持 CUDA 加速声纹验证
- 💬 **智能对话**：集成 OpenAI API，支持流式文本生成和句子分割
- 🔊 **语音合成**：支持 Edge TTS、CosyVoice 和 MeloTTS（含 ONNX 版本），支持流式和非流式模式
  - **Edge TTS**：微软 Edge 浏览器的免费 TTS 服务，支持多种语言和音色
  - **CosyVoice**：阿里达摩院的高质量语音合成，支持 SFT 模式和零样本克隆
  - **MeloTTS**：Myshell 开源的高质量多语言 TTS，支持中、英、日等多种语言
  - **MeloTTS-ONNX**：基于 ONNX 的优化版本，推理速度更快
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
- 🔄 **事件驱动架构**：采用事件驱动的 v3.0 架构，线程安全、易于扩展
  - StateManager：事件驱动版本的状态管理器，支持原子性状态转换
  - ContextManager：统一管理 TaskContext 的创建、访问和销毁
  - ThreadManager：统一管理所有工作线程的生命周期
  - EventLogger：统一日志处理器，支持日志分类和时间差计算
- 💾 **TTS 缓存系统**：支持文件缓存，按语音分类存储，显著提升响应速度
  - 字典索引：每个语音目录下保存独立的索引文件
  - 按语音分类：按语音（voice）组织主目录
  - LRU 内存缓存：减少重复请求
- 🎨 **WebUI 支持**：CosyVoice 和 MeloTTS 提供完整的 WebUI 界面
- 🌐 **API 服务**：CosyVoice 和 MeloTTS 提供 RESTful API 服务
- 🤖 **MeloTTS 自动管理**：程序启动时自动检测和启动 MeloTTS API 服务，支持模型预加载和内存优化
  - 端口检测：自动检查端口占用情况
  - 模型预加载：服务启动后自动预加载配置中指定的模型语言
  - 内存优化：API 不再保存临时文件到磁盘，所有音频数据在内存中处理

## 🏗️ 系统架构

### 核心流程

```
麦克风采集(含回声消除) → VAD → ASR → 对话调度器 → LLM流式生成 → TTS+播放
```

### 线程架构

系统采用双系统架构（主系统+副系统），包含9个线程：

- **主线程（线程0）**：系统初始化和状态管理
- **常驻后台线程（线程1-3）**：音频采集、VAD后台、事件总线处理
- **按需启动线程（线程4-9）**：ASR识别、LLM+TTS处理、音频播放等

详细架构说明请参考 [PROJECT_OUTLINE.md](voice_chatbot/PROJECT_OUTLINE.md)。

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
- **模型预加载**：服务启动后会自动预加载配置中指定的模型语言，减少首次请求延迟

## 📁 项目结构

```
lincoco/
├── main.py                          # 主程序入口（含MeloTTS自动启动）
├── requirements.txt                 # Python 依赖包
├── README.md                        # 项目说明文档
│
├── plugin/                         # 扩展功能插件目录
│   ├── 3d-speaker/                 # 3D-Speaker 声纹识别模块（完整集成）
│   │   ├── speakerlab/             # 声纹识别核心库
│   │   │   ├── models/             # 模型定义（22个模型文件）
│   │   │   ├── process/            # 处理脚本
│   │   │   └── utils/              # 工具函数
│   │   └── README.md               # 3D-Speaker 官方文档
│   │
│   ├── cosyvoice/                  # CosyVoice 语音合成模块（完整集成）
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
│   └── melotts/                    # MeloTTS 语音合成模块（完整集成）
│       ├── melo/                   # MeloTTS 核心库
│       │   ├── configs/            # 配置文件
│       │   ├── data/               # 数据处理
│       │   ├── text/               # 文本处理（43个文件）
│       │   ├── models/             # 模型定义
│       │   └── infer.py            # 推理接口
│       ├── MeloTTS-ONNX/           # ONNX 优化版本
│       │   ├── melo_onnx/          # ONNX 核心（45个文件）
│       │   └── requirements.txt    # ONNX 依赖
│       ├── api.py                  # HTTP API 服务
│       ├── api_onnx.py             # ONNX API 服务
│       ├── webui.py                # WebUI 界面
│       ├── hf_download/            # HuggingFace 缓存（不提交）
│       ├── docs/                   # 文档
│       └── README.md               # MeloTTS 官方文档
│
├── voice_chatbot/                  # 核心模块目录
│   ├── voice_main.py               # 语音机器人主程序
│   │
│   ├── asr/                        # ASR 模块（重构）
│   │   ├── fun_asr_stream.py       # 流式语音识别（FunASR）
│   │   ├── webrtc_vad.py           # 语音活动检测
│   │   ├── speaker_verification.py # 声纹验证（集成3D-Speaker）
│   │   ├── punctuation_restore.py  # 标点符号恢复
│   │   └── __init__.py             # 模块初始化
│   │
│   ├── dialogue/                   # 对话管理模块（v3.0事件驱动架构）
│   │   ├── manager.py              # 核心调度器（含TraceableLock调试）
│   │   ├── state.py                # 状态机
│   │   ├── state_manager.py        # 事件驱动状态管理器
│   │   ├── events.py               # 事件系统
│   │   ├── context_manager.py      # 上下文管理器
│   │   ├── thread_manager.py       # 线程管理器
│   │   └── task_context.py         # 任务上下文
│   │
│   ├── llm/                        # LLM 模块
│   │   ├── llm_openai_stream.py    # OpenAI 流式生成
│   │   ├── persona_manager.py      # 人设管理器
│   │   ├── variable_processor.py   # 变量处理器
│   │   └── personas/               # 人设文件目录
│   │       ├── default.md          # 默认人设
│   │       ├── assistant.md        # 助手人设
│   │       └── lincoco.md          # Lincoco 人设
│   │
│   ├── tts/                        # TTS 模块
│   │   ├── edge_tts_engine.py      # Edge TTS 引擎
│   │   ├── melo_tts_engine.py      # MeloTTS 引擎（HTTP API调用）
│   │   ├── cosyvoice_engine.py     # CosyVoice 引擎
│   │   ├── tts_file_cache.py       # TTS 文件缓存（字典索引）
│   │   └── __init__.py             # 模块初始化
│   │
│   └── utils/                      # 工具模块
│       ├── config.py              # 配置管理（含MeloTTS配置）
│       ├── logger.py              # 日志系统
│       ├── microphone.py          # 麦克风采集（从audio/移入）
│       ├── audio_player.py        # 音频播放器（从tts/移入）
│       ├── timer.py              # 性能计时器
│       └── __init__.py            # 工具模块初始化
│
├── cache/                          # 缓存目录（不提交音频文件）
│   ├── speaker_verification_audio/ # 声纹注册音频
│   └── tts_cache/                  # TTS 缓存
│       ├── edge_tts_cache/        # Edge TTS 缓存
│       └── melo_tts_cache/        # MeloTTS 缓存
│
├── pretrained/                     # 预训练模型（不提交）
│   └── speech_campplus_sv_zh-cn_16k-common/
│       └── campplus_cn_common.bin
│
├── logs/                           # 日志目录（不提交）
│
├── cleanup_3d_speaker.py          # 清理3D-Speaker多余文件
├── extract_3d_speaker_from_git.py # 从git提取3D-Speaker
├── prepare_minimal_3d_speaker.py  # 准备最小3D-Speaker
│
├── Git快速参考.md                  # Git 快速参考
├── Git提交指南.md                  # Git 提交指南
│
└── voice_chatbot/                  # 核心文档
    ├── PROJECT_OUTLINE.md          # 项目大纲
    ├── REALTIME_INTERACTION_DESIGN.md  # 实时交互设计
    ├── DEVELOPMENT_LOG.md          # 开发日志
    ├── TEST_REPORT.md              # 测试报告
    └── llm/PERSONA_GUIDE.md        # 人设使用指南
```

### 项目结构变化说明

**新增模块**：
- `plugin/`：扩展功能插件目录，统一管理所有第三方集成模块
  - `plugin/3d-speaker/`：完整集成的声纹识别模块
  - `plugin/cosyvoice/`：完整集成的语音合成模块
  - `plugin/melotts/`：完整集成的MeloTTS及其ONNX优化版本
- `cleanup_3d_speaker.py`：清理3D-Speaker模块多余文件的工具
- `extract_3d_speaker_from_git.py`：从git提取3D-Speaker的工具
- `prepare_minimal_3d_speaker.py`：准备最小3D-Speaker的工具

**模块重构**：
- `3D-Speaker/` → `plugin/3d-speaker/`：声纹识别模块移至plugin目录
- `CosyVoice/` → `plugin/cosyvoice/`：语音合成模块移至plugin目录
- `MeloTTS/` → `plugin/melotts/`：MeloTTS模块移至plugin目录
- `voice_chatbot/audio/` → `voice_chatbot/asr/`：ASR相关功能独立成模块
- `voice_chatbot/audio/microphone.py` → `voice_chatbot/utils/microphone.py`：麦克风采集移入工具模块
- `voice_chatbot/tts/audio_player.py` → `voice_chatbot/utils/audio_player.py`：音频播放器移入工具模块
- 对话管理模块采用v3.0事件驱动架构，新增`state_manager.py`、`context_manager.py`、`thread_manager.py`
- TTS缓存系统重构：采用字典索引，按语音分类存储

## ⚙️ 配置说明

主要配置项在 `voice_chatbot/utils/config.py` 中定义，支持通过环境变量和配置文件进行配置。

### 关键配置参数

#### LLM 配置
```python
llm.persona_name = "default"          # 默认人设名称
llm.user_name = "用户"                # 用户名
llm.max_tokens = 512                  # 最大生成token数
llm.temperature = 0.7                 # 生成温度
```

#### TTS 配置
```python
tts.engine = "melotts"                 # TTS引擎：edgetts、cosyvoice3 或 melotts
tts.tts_file_cache_enabled = True     # 启用TTS文件缓存
tts.enable_trim_silence = True        # 启用静音裁剪

# MeloTTS 特定配置
tts.engines.melo_tts.model_language = "ZH"  # 模型语言：ZH=中文, EN=英文, JP=日文等
tts.engines.melo_tts.api_url = "http://127.0.0.1:8101"  # API 服务地址
```

#### 声纹验证配置
```python
asr.speaker_verification.enabled = True            # 启用声纹验证
asr.speaker_verification.enroll_audio_path = "cache/speaker_verification_audio"  # 注册音频路径
asr.speaker_verification.model_id = "iic/speech_campplus_sv_zh-cn_16k-common"  # 声纹模型
asr.speaker_verification.similarity_threshold = 0.5   # 相似度阈值（0-1）
asr.speaker_verification.min_audio_length = 1.0      # 最小音频长度（秒）
asr.speaker_verification.auto_record = True         # 自动录制注册音频
asr.speaker_verification.cache_embedding = True      # 缓存嵌入向量
asr.speaker_verification.window_length = 2.0        # 声纹验证窗口长度（秒）
asr.speaker_verification.window_stride = 0.5        # 滑动窗口步长（秒）
asr.speaker_verification.sliding_window_enabled = True  # 启用滑动窗口模式
asr.speaker_verification.enrollment_scenes = ["scene_1", "scene_2", "scene_3"]  # 注册场景列表
asr.speaker_verification.parallel_verification = True  # 启用并行验证
```

#### ASR参数
```python
asr.no_speech_threshold = 0.8     # 无语音阈值（秒）
```

详细配置说明请参考 [PROJECT_OUTLINE.md](voice_chatbot/PROJECT_OUTLINE.md#-配置参数)。

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
- **按长度分类**：根据文本长度自动分类存储
- **缓存命中**：生成前检查缓存，提高响应速度
- **目录组织**：按语音和长度范围组织缓存文件

## 📊 性能特性

- **统一事件日志**：基于事件系统的统一日志管理
- **细粒度性能分析**：精确记录各阶段耗时（ASR、LLM、TTS等）
- **内存优化**：音频预缓存、内存播放、及时清理
- **线程安全**：完整的锁机制和任务隔离

## 🔧 开发环境

### 开发要求

- **测试环境**：必须使用 conda 环境 `voice` 进行开发和测试
- **激活环境**：`conda activate voice`
- **运行程序**：`python main.py`

### 开发规范

- 每次完成功能开发或测试后，必须在 `DEVELOPMENT_LOG.md` 中记录
- 修改功能列表时，同步更新 `PROJECT_OUTLINE.md` 中的标记
- 所有模块通过事件系统统一输出日志

## 📚 文档

- [PROJECT_OUTLINE.md](voice_chatbot/PROJECT_OUTLINE.md) - 详细的项目架构和功能说明
- [REALTIME_INTERACTION_DESIGN.md](voice_chatbot/REALTIME_INTERACTION_DESIGN.md) - 实时语音交互架构设计文档
- [DEVELOPMENT_LOG.md](voice_chatbot/DEVELOPMENT_LOG.md) - 开发日志
- [THREAD_SCHEDULING.md](voice_chatbot/THREAD_SCHEDULING.md) - 线程调度架构说明
- [PERSONA_GUIDE.md](voice_chatbot/llm/PERSONA_GUIDE.md) - 人设系统使用指南

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[待添加许可证信息]

## ⚠️ 注意事项

- 项目中的 `tts_cache/` 目录包含大量音频缓存文件，已配置 `.gitignore` 忽略音频文件，但保留 `_index.json` 索引文件
- `plugin/` 目录包含所有扩展功能模块：
  - `plugin/melotts/` 目录是独立的 git 仓库，不会被提交到主仓库（如需使用，请单独克隆或作为子模块添加）
  - `plugin/melotts/hf_download/` 目录包含 Hugging Face 模型下载缓存，不会被提交到 Git
  - 其他插件模块的缓存文件也会被 `.gitignore` 忽略
- 日志文件保存在 `logs/` 目录，不会被提交到 Git
- 请确保正确配置环境变量（如 OpenAI API Key）
- **MeloTTS 自动启动**：如果配置中选择了 MeloTTS 引擎，程序会在启动时自动检测端口占用情况并启动 API 服务，无需手动运行 `start-api-backend.bat`
- **MeloTTS 模型预加载**：服务启动后自动预加载配置中指定的模型语言，避免首次请求时的模型加载延迟
- **MeloTTS 内存优化**：API 服务不再保存临时文件到磁盘，所有音频数据在内存中处理，提高性能

## 🔗 相关链接

- [FunASR](https://github.com/alibaba-damo-academy/FunASR) - 语音识别框架
- [Edge TTS](https://github.com/rany2/edge-tts) - 语音合成服务
- [MeloTTS](https://github.com/myshell-ai/MeloTTS) - 高质量多语言语音合成
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) - 高质量语音合成（阿里达摩院）
- [3D-Speaker](https://github.com/alibaba-damo-academy/3D-Speaker) - 声纹识别和验证
- [OpenAI API](https://platform.openai.com/docs) - 大语言模型 API

---

**最后更新**: 2026-01-05  
**维护者**: ngyygm

## 🆕 最新更新

### 核心功能增强

- 🔐 **声纹验证功能**：完整集成 3D-Speaker 声纹识别模块，支持基于声纹的访问控制
  - 多声纹支持：支持多个注册音频文件，只要有一个匹配就通过验证
  - 并行比对：优化声纹验证算法，使用张量并行计算所有注册音频的相似度
  - 滑动窗口：支持滑动窗口模式，提高声纹验证的准确性和灵活性
  - 多场景录制：支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - 自动注册：首次运行时自动引导用户录制声纹注册音频
  - 嵌入缓存：支持缓存声纹嵌入向量，提高验证速度
  - GPU 加速：支持 CUDA 加速声纹验证
  - 多模型支持：支持 CAMPlus、ERes2Net、ERes2NetV2 等多种模型
- 📦 **3D-Speaker 集成**：完整集成 3D-Speaker 声纹识别库（位于 `plugin/3d-speaker/`）
  - 模型下载：支持从 ModelScope 自动下载声纹模型
  - 完整的训练脚本：提供 VoxCeleb、CNCeleb 等数据集的训练示例
- 🎯 **声纹验证流程优化**：优化声纹验证与 ASR 识别的集成流程
  - 预缓存集成：声纹验证正确使用预缓存的音频（VAD 检测前的音频）
  - 滑动窗口验证：声纹验证通过后，从验证通过的窗口起始位置开始 ASR 识别
  - 无重复音频：确保声纹验证和 ASR 识别的音频无缝衔接，无重复或遗漏

### TTS 引擎增强

- 🚀 **MeloTTS 引擎**：新增 MeloTTS 语音合成引擎支持（位于 `plugin/melotts/`）
  - HTTP API 调用：通过 HTTP API 调用 MeloTTS 服务
  - 多语言支持：自动语言检测和说话人选择（中文、英文、日文等）
  - 参数配置：支持语速、SDP 比例、噪声比例等参数配置
  - 双重缓存：LRU 内存缓存 + 文件缓存，减少重复请求
  - 响度归一化：自动进行音频响度归一化，确保播放质量
  - 自动启动：程序启动时自动检测和启动 MeloTTS API 服务
  - 模型预加载：服务启动后自动预加载配置中指定的模型语言
  - 内存优化：API 不再保存临时文件到磁盘，所有音频数据在内存中处理
  - ONNX 版本：提供 MeloTTS-ONNX 版本，支持 ONNX 推理
- 🎨 **CosyVoice 引擎**：新增 CosyVoice 语音合成引擎支持（位于 `plugin/cosyvoice/`）
  - SFT 模式：支持预设音色的 SFT 模式
  - 零样本克隆：支持零样本语音克隆模式
  - 流式生成：支持流式音频生成
  - 多语言检测：支持多语言检测和音色自动选择
  - WebUI：提供完整的 WebUI 界面
  - API 服务：提供 RESTful API 服务
- 📊 **TTS 缓存优化**：重构 TTS 文件缓存系统
  - 字典索引：每个语音目录下保存独立的索引文件，提升查询效率
  - 简化结构：取消长度阶梯分类，统一存储在语音目录下
  - 按语音分类：按语音（voice）组织主目录
  - 自动加载：每个语音的索引按需加载，减少内存占用

### 对话系统优化

- 🐛 **场景6播放恢复修复**：修复延迟验证后无效输入时播放线程无法恢复播放的问题
  - 将播放线程的等待时间从 0.1 秒延长到最多 1 秒
  - 改为循环检查副系统状态和 pending_interrupt 标记
  - 确保场景 6 处理完成后能够正确恢复播放
- ✨ **对话历史记录优化**：重构对话历史记录机制
  - 使用 `completed_sentences` 和 `played_response_text` 精确跟踪已播放文本
  - 支持打断时记录已播放部分
  - 确保对话历史的准确性
- 🎯 **任务取消时记录用户输入**：在任务取消时自动记录用户输入到对话历史，确保对话上下文的完整性
- 📊 **播放进度实时跟踪**：增强播放进度跟踪机制
  - 实时更新已完成句子和当前播放句子
  - 支持同一行更新显示
  - 支持更精确的打断和历史记录
- 🔍 **详细的日志输出**：改进 TaskContext 的日志输出，详细记录 assistant 相关内容的更新

### 性能与稳定性

- 🔧 **简化输入处理流程**：移除快速连续输入合并逻辑，简化 `_process_user_input` 方法，提升代码可维护性
- ⚙️ **ASR 去重配置化**：添加 `enable_deduplication` 配置选项，可灵活控制是否启用 ASR 流式去重功能
- 🎯 **误识别处理优化**：当 ASR 识别结果为 0 个字符时，自动判断为误识别，继续 LLM+TTS 处理，不中断当前任务
- ⚡ **性能监控增强**：基于事件系统的统一日志和性能分析
  - 树形结构清晰展示 6 个关键时间节点和阶段耗时
  - 细粒度性能分析：ASR 模型推理时间、LLM API 请求等待时间、TTS 缓存命中状态
  - 事件时间轴：维护完整的事件时间轴记录
  - 支持 sequenceDiagram 格式的图形化流程记录

### LLM 与句子处理

- 🎭 **首句切分优化**：优化首句逗号切分逻辑
  - 首句快速响应：支持按逗号切分，实现快速响应
  - 延迟判断：遇到第一个逗号时，等待一定数量的字符检查是否有句子结束符
  - 智能决策：如果延迟范围内有句子结束符，等待按句子结束符切分（保持完整句子）；如果没有，按逗号切分（快速响应）
- 🎯 **英文句号识别优化**：智能识别英文句号，排除小数和列表标号
- 🚫 **省略号识别修复**：修复省略号 `...` 被误判为句子结束符的问题

### 人设系统

- 🎭 **人设管理系统**：支持多个人设配置，支持运行时动态切换
  - 变量处理器：支持 `{{param_name}}` 双大括号格式的变量替换
  - 内置参数：当前时间、日期、星期几、用户名等
  - 自定义参数：支持注册自定义参数处理器
  - Markdown 格式：人设文件支持 Markdown 格式
  - Fallback 机制：人设文件不存在时，自动使用配置中的 `system_prompt`

### 架构重构

- 🔄 **事件驱动架构**：采用事件驱动的 v3.0 架构，线程安全、易于扩展
  - StateManager：事件驱动版本的状态管理器，支持原子性状态转换
  - ContextManager：统一管理 TaskContext 的创建、访问和销毁
  - ThreadManager：统一管理所有工作线程的生命周期
  - EventLogger：统一日志处理器，支持日志分类和时间差计算
- 📋 **标准化操作文档**：创建标准化操作文档，记录每次代码修改后的标准操作步骤

### 配置管理

- ⚙️ **配置参数整理优化**：统一超时参数，新建独立 VAD section，早期提交参数改为比例关系
  - 统一超时参数：合并多个超时参数为 `dialogue.thread_timeout` 和 `asr.result_timeout`
  - VAD section：将分散的 VAD 参数统一到新的 `vad` section
  - 早期提交：使用 `stable_result_ratio` 配置自动计算早期提交时间

### 工具与文档

- 📚 **完整文档**：
  - PROJECT_OUTLINE.md：详细的项目架构和功能说明
  - REALTIME_INTERACTION_DESIGN.md：实时语音交互架构设计文档
  - DEVELOPMENT_LOG.md：详细的开发日志
  - PERSONA_GUIDE.md：人设系统使用指南
  - Git快速参考.md：Git 快速参考
  - Git提交指南.md：Git 提交指南
- 🧪 **测试完善**：创建完整的测试脚本和测试报告

---

**最后更新**: 2026-01-07
**维护者**: ngyygm
