# Lincoco Voice Chatbot

一个基于语音识别(ASR)、大语言模型(LLM)和语音合成(TTS)的实时语音对话系统，支持自然的人机交互、打断机制、对话历史管理等高级功能。

## ✨ 核心特性

- 🎤 **实时语音识别**：基于 FunASR 的流式语音识别，支持预缓存机制避免丢失开头
- 💬 **智能对话**：集成 OpenAI API，支持流式文本生成和句子分割
- 🔊 **语音合成**：支持 Edge TTS 和 CosyVoice3，支持流式和非流式模式
- 🎯 **打断机制**：支持延迟验证和 ASR 验证，实现自然的对话打断
- 📝 **对话管理**：自动管理对话历史，支持输入合并和上下文管理
- 🎭 **人设系统**：支持多个人设配置，支持运行时动态切换
- 📊 **性能监控**：基于事件系统的统一日志和性能分析
- 🔄 **事件驱动架构**：采用事件驱动的 v3.0 架构，线程安全、易于扩展

## 🏗️ 系统架构

### 核心流程

```
麦克风采集(含回声消除) → VAD → ASR → 对话调度器 → LLM流式生成 → TTS+播放
                                                          ↓
                                                    播放参考信号 → 麦克风(回声消除)
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

## 📁 项目结构

```
lincoco/
├── main.py                          # 主程序入口
├── requirements.txt                 # Python 依赖包
├── README.md                        # 项目说明文档
│
├── voice_chatbot/                  # 核心模块目录
│   ├── audio/                      # 音频输入模块
│   │   ├── microphone.py           # 麦克风采集（含回声消除）
│   │   ├── webrtc_vad.py          # 语音活动检测
│   │   └── fun_asr_stream.py      # 流式语音识别
│   │
│   ├── dialogue/                   # 对话管理模块
│   │   ├── manager.py             # 核心调度器
│   │   ├── state.py               # 状态机
│   │   ├── events.py              # 事件系统
│   │   ├── state_manager.py       # 状态管理器
│   │   ├── context_manager.py     # 上下文管理器
│   │   ├── thread_manager.py      # 线程管理器
│   │   └── task_context.py        # 任务上下文
│   │
│   ├── llm/                       # LLM 模块
│   │   ├── llm_openai_stream.py  # OpenAI 流式生成
│   │   ├── persona_manager.py    # 人设管理器
│   │   ├── variable_processor.py # 变量处理器
│   │   └── personas/             # 人设文件目录
│   │       ├── default.md        # 默认人设
│   │       ├── assistant.md      # 助手人设
│   │       └── lincoco.md        # Lincoco 人设
│   │
│   ├── tts/                       # TTS 模块
│   │   ├── edge_tts_engine.py    # Edge TTS 引擎
│   │   ├── tts_file_cache.py     # TTS 文件缓存
│   │   └── audio_player.py       # 音频播放器
│   │
│   └── utils/                     # 工具模块
│       ├── config.py             # 配置管理
│       ├── logger.py             # 日志系统
│       ├── event_logger.py       # 事件日志处理器
│       └── performance_monitor.py # 性能监控器
│
└── logs/                          # 日志目录（自动生成）
```

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
tts.engine = "edgetts"                # TTS引擎：edgetts 或 cosyvoice3
tts.tts_file_cache_enabled = True     # 启用TTS文件缓存
tts.enable_trim_silence = True        # 启用静音裁剪
```

#### 对话参数
```python
dialogue.no_speech_threshold = 1.2    # 无语音阈值（秒）
dialogue.audio_stop_delay = 0.3       # 打断延迟验证时间（秒）
```

详细配置说明请参考 [PROJECT_OUTLINE.md](voice_chatbot/PROJECT_OUTLINE.md#-配置参数)。

## 🎯 主要功能

### 1. 实时语音识别

- **流式识别**：基于 FunASR 的实时语音识别
- **预缓存机制**：避免丢失语音开头部分
- **去重处理**：智能处理识别结果的重复和纠错
- **VAD 配合**：VAD 检测到语音活动时自动启动 ASR

### 2. 智能对话管理

- **流式生成**：LLM 流式生成文本，实时响应
- **句子分割**：智能检测完整句子，支持首句快速响应
- **对话历史**：自动管理对话历史，支持上下文理解
- **输入合并**：支持在生成过程中合并新的用户输入

### 3. 打断机制

- **延迟验证**：检测到语音后延迟验证，避免误判
- **ASR 验证**：暂停后通过 ASR 验证是否为有效输入
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
- [DEVELOPMENT_LOG.md](voice_chatbot/DEVELOPMENT_LOG.md) - 开发日志
- [THREAD_SCHEDULING.md](voice_chatbot/THREAD_SCHEDULING.md) - 线程调度架构说明

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[待添加许可证信息]

## ⚠️ 注意事项

- 项目中的 `tts_cache/` 目录包含大量音频缓存文件，已配置 `.gitignore` 忽略音频文件，但保留 `_index.json` 索引文件
- 日志文件保存在 `logs/` 目录，不会被提交到 Git
- 请确保正确配置环境变量（如 OpenAI API Key）

## 🔗 相关链接

- [FunASR](https://github.com/alibaba-damo-academy/FunASR) - 语音识别框架
- [Edge TTS](https://github.com/rany2/edge-tts) - 语音合成服务
- [OpenAI API](https://platform.openai.com/docs) - 大语言模型 API

---

**最后更新**: 2025-01-XX  
**维护者**: [您的名字]
