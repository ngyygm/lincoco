# 语音对话系统 - 项目大纲文档

## ⚠️ 重要提示（必读）

### 开发环境要求
- **测试必须使用 conda 环境 `voice`** 进行开发和测试
- 激活环境：`conda activate voice`
- 运行程序：`cd d:\APP\ASR-LLM-TTS && python -m voice_chatbot.main`

### 参考实现
- **主要参考文件**：`56_Lincoco_streamASR_openai_edgetts_realTime.py`
- 该文件包含完整的功能实现，可作为模块化重构的参考
- 实现新功能时，优先参考该文件中的实现方式

### 开发规范
- **每次完成功能开发或测试后，必须在 `DEVELOPMENT_LOG.md` 中记录（年月日 时分）**
- 记录内容包括：功能描述、实现方式、测试结果、遇到的问题等
- 修改功能列表时，同步更新 `PROJECT_OUTLINE.md` 中的标记

### 核心目标
- 实现自然的语音对话系统，支持实时语音识别、LLM交互、语音合成和播放
- 支持打断机制、输入合并、对话历史管理等高级功能
- 代码模块化、可维护、可扩展

---

## 📋 项目概述

**目标**：实现自然的语音对话系统，支持实时语音识别、LLM交互、语音合成和播放。

**核心流程**：
```
麦克风采集(含回声消除) → VAD → ASR → 对话调度器 → LLM流式生成 → TTS+播放
                                                          ↓
                                                    播放参考信号 → 麦克风(回声消除)
```

---

## 🏗️ 模块架构

### 系统架构总览

系统采用**主系统（用户）**和**副系统（机器人LLM+TTS线程）**的双系统架构：

- **主系统状态**（用户相关）：IDLE、LISTENING、PLAYING
- **副系统状态**（机器人LLM+TTS线程相关）：None、PROCESSING、PLAYING、PAUSED
- **状态组合规则**：主系统状态和副系统状态可以叠加，各自内部互斥

系统包含9个线程：
- **主线程（线程0）**：系统初始化和状态管理
- **常驻后台线程（线程1-3）**：音频采集、VAD后台、事件总线处理
- **按需启动线程（线程4-9）**：ASR识别、ASR内部识别、LLM+TTS处理、LLM流式输出、TTS生成、音频播放

详细线程架构和交互逻辑请参考 `THREAD_SCHEDULING.md`。

### 1. 音频输入模块 (audio/)
- [•] **microphone.py** - 麦克风采集 ✅
  - [•] 音频采集接口 ✅
  - [•] 实时音频流处理 ✅
  - [•] 音频数据回调机制 ✅
  - [•] 多设备支持 ✅
  - [•] 音频格式转换 ✅
  - [•] 回声消除（AEC - 防止识别电脑外放声音）✅

- [•] **webrtc_vad.py** - 语音活动检测 ✅
  - [•] WebRTC VAD集成 ✅
  - [•] 实时语音活动检测 ✅
  - [•] 语音开始/结束回调 ✅
  - [•] 可配置敏感度 ✅
  - [•] 自适应阈值调整 ✅

- [•] **fun_asr_stream.py** - 流式语音识别 ✅
  - [•] FunASR流式识别 ✅
  - [•] 预缓存机制（避免丢失开头）✅
  - [•] ASR去重器（处理重复文本）✅
  - [•] 部分结果回调 ✅
  - [•] 最终结果回调 ✅
  - [•] 多语言支持 ✅
  - [•] 识别置信度输出 ✅
  - [•] **VAD与ASR配合机制** ✅
    - [•] VAD检测到语音活动时启动ASR（`start_streaming_asr()`）✅
    - [•] ASR的结束由`NO_SPEECH_THRESHOLD`控制（不是VAD直接控制）✅
    - [•] 当用户停止说话超过`NO_SPEECH_THRESHOLD`秒时，调用`save_audio_video()` → `wait_for_streaming_asr_result()` → `stop_streaming_asr()` ✅
    - [•] `stop_streaming_asr()`设置`streaming_asr_speaking = False`，ASR线程检测到此标志后退出 ✅
    - [•] ASR线程退出前处理剩余音频并进行最终识别（`is_final=True`）✅

### 2. 对话管理模块 (dialogue/)
  - [•] **state.py** - 状态机 ✅
    - [•] 主系统状态枚举（IDLE/LISTENING/PLAYING）✅
    - [•] 副系统状态枚举（None/PROCESSING/PLAYING/PAUSED）✅
    - [•] 状态组合规则（主系统和副系统状态可叠加，各自内部互斥）✅
    - [•] 任务信息管理（task_id机制）✅
    - [•] 对话历史管理 ✅
    - [•] 线程安全的状态操作（task_lock、asr_lock、interrupt_lock）✅
    - [•] 状态转换日志 ✅
    - [•] 状态持久化 ✅

- [•] **events.py** - 事件系统 ✅
  - [•] 事件类型定义 ✅
  - [•] 事件数据结构 ✅
  - [•] 事件时间戳 ✅
  - [•] 事件优先级 ✅
  - [•] 事件过滤机制 ✅
  - [•] **统一事件日志系统** ✅
    - [•] 系统级事件：`SYSTEM_INIT`, `SYSTEM_START`, `SYSTEM_STOP` ✅
    - [•] 细粒度ASR事件：`ASR_MODEL_INFERENCE` ✅
    - [•] 细粒度LLM事件：`LLM_REQUEST_SENT`, `LLM_STREAM_START`, `LLM_SENTENCE_GENERATED` ✅
    - [•] TTS缓存事件：`TTS_CACHE_HIT` ✅
    - [•] 通用日志事件：`LOG_INFO`, `LOG_WARN`, `LOG_ERROR`, `LOG_DEBUG` ✅
    - [•] 所有模块通过事件系统统一输出日志 ✅
    - [•] EventLogger统一格式化和管理日志输出 ✅
    - [•] PerformanceMonitor基于事件时间戳精确计算耗时 ✅

- [•] **task_context.py** - 任务上下文（v3.0 新架构）✅
  - [•] 任务上下文类（TaskContext），隔离每个任务的状态和数据 ✅
  - [•] 任务有效性检查（is_valid 标志）✅
  - [•] 任务取消异常（TaskCancelledException）✅
  - [•] 线程安全的任务状态访问 ✅
  - [•] 任务数据隔离（避免旧线程修改新任务变量）✅

- [•] **context_manager.py** - 上下文管理器（v3.0 新架构）✅
  - [•] 统一管理任务上下文的创建、访问和销毁 ✅
  - [•] 创建新任务时自动使旧任务失效 ✅
  - [•] 线程安全的上下文访问 ✅
  - [•] 上下文生命周期管理 ✅

- [•] **thread_manager.py** - 线程管理器（v3.0 新架构）✅
  - [•] 统一管理所有工作线程的生命周期 ✅
  - [•] 绑定线程到任务上下文 ✅
  - [•] 线程启动/停止的统一接口 ✅
  - [•] 线程状态跟踪（通过事件系统）✅
  - [•] 线程异常处理和恢复 ✅
  - [•] 所有线程操作通过事件系统统一日志输出 ✅
  - [•] 支持常驻线程（线程1-3）和按需启动线程（线程4-9）的统一管理 ✅

- [•] **state_manager.py** - 状态管理器（事件驱动版，v3.0 新架构）✅
  - [•] 通过事件驱动实现原子性状态转换 ✅
  - [•] 自动验证状态组合有效性 ✅
  - [•] 主系统状态转换（IDLE/LISTENING/ROBOT_SPEAKING）✅
  - [•] 副系统状态转换（NONE/PROCESSING/SPEAKING/PAUSED）✅
  - [•] 状态转换事件发布（STATE_CHANGED 事件）✅
  - [•] 状态转换日志（通过事件系统）✅
  - [•] 状态组合规则验证（防止不可能的状态组合）✅

- [ ] **manager.py** - 核心调度器（线程协调中心，v3.0 架构）
  - [ ] 模块初始化与协调
  - [ ] 集成 ThreadManager、StateManager、ContextManager
  - [ ] 主线程（线程0）职责：系统初始化、状态管理、线程协调
  - [ ] 通过 ThreadManager 管理所有线程生命周期
  - [ ] 通过 StateManager 管理状态转换（事件驱动）
  - [ ] 通过 ContextManager 管理任务上下文
  - [ ] 所有线程操作通过事件系统统一日志输出
  - [ ] 常驻后台线程管理（通过 ThreadManager）：
    - [ ] 线程1（音频采集线程）：持续采集音频，发送给VAD和ASR预缓存
    - [ ] 线程2（VAD后台线程）：持续监控音频，检测语音活动，触发回调
    - [ ] 线程3（事件总线处理线程）：持续处理事件队列，按优先级通知订阅者
  - [ ] 按需启动线程管理（通过 ThreadManager）：
    - [ ] 线程4（ASR识别线程）：VAD检测到语音时启动，管理ASR识别流程
    - [ ] 线程5（ASR内部识别线程）：由线程4启动，执行FunASR流式识别
    - [ ] 线程6（LLM+TTS处理线程）：处理用户输入，启动LLM生成和TTS流程
    - [ ] 线程7（LLM流式输出线程）：流式生成LLM输出，检测完整句子
    - [ ] 线程8（TTS生成线程）：生成TTS音频
      - [ ] 非流式模式（EdgeTTS）：等待完整句子 → 生成完整音频 → 一次性放入播放队列
    - [ ] 线程9（音频播放线程）：播放TTS生成的音频，处理打断和暂停
      - [ ] 支持非流式音频播放（完整AudioSegment）
      - [ ] 支持流式音频块播放（连续AudioSegment块）
  - [ ] 用户输入处理（_process_user_input）
  - [ ] 打断机制（延迟验证、ASR验证）
  - [ ] 输入合并逻辑（is_merge参数）
  - [ ] 任务生命周期管理（通过 ContextManager 和 TaskContext）
  - [ ] 错误恢复机制
  - [ ] 性能监控
  - [ ] **人设切换功能**
    - [ ] `switch_persona(name)` 方法支持运行时动态切换人设
    - [ ] `get_current_persona_name()` 方法获取当前人设名称

#### 架构说明（v3.0 新架构）

为了解决锁管理混乱、共享变量污染、线程生命周期难以追踪等问题，引入事件驱动的新架构：

**核心组件**：
- **TaskContext**：任务上下文，隔离每个任务的状态和数据
- **ContextManager**：上下文管理器，统一管理任务上下文的创建、访问和销毁
- **ThreadManager**：线程管理器，统一管理所有工作线程的生命周期
- **StateManager**：状态管理器（事件驱动版），通过事件驱动实现原子性状态转换

**架构优势**：
- **锁管理集中化**：每个Manager 1个锁，集中管理（vs 旧架构：4种锁，87处使用）
- **变量隔离**：每个任务独立TaskContext（vs 旧架构：无隔离，旧线程可能修改新任务变量）
- **状态管理集中化**：集中在StateManager，事件驱动（vs 旧架构：分散在各方法）
- **可追踪性**：所有变化通过事件记录（vs 旧架构：难追踪）
- **统一日志输出**：所有线程操作通过事件系统统一输出日志

**线程管理流程**：
1. 创建任务 → ContextManager.create_context() → 自动使旧任务失效
2. 启动线程 → ThreadManager.start_thread() → 绑定到任务上下文 → 发布 THREAD_STARTED 事件
3. 状态转换 → StateManager.transition_*() → 验证状态组合 → 发布 STATE_CHANGED 事件
4. 线程操作 → 通过事件系统统一日志输出
5. 任务取消 → TaskContext.invalidate() → 所有线程通过检查 is_valid 自动退出


### 3. LLM模块 (llm/)
- [•] **llm_openai_stream.py** - LLM流式生成（OpenAI API实现）✅
  - [•] OpenAI API兼容 ✅
  - [•] 流式文本生成 ✅
  - [•] 句子分割与合并 ✅
    - [•] **首句切分规则**：第一句话支持按逗号（，,）切分，实现快速响应 ✅
    - [•] **后续句子切分规则**：后续句子必须等待完整句子结束符（。！？.!?\n）才切分 ✅
    - [•] **英文句号识别优化**：智能识别英文句号，排除小数（如1.3）和列表标号（如1. 文本、2. 文本）✅
  - [•] 任务取消检查 ✅
  - [•] 标签过滤（<think>标签）✅
  - [•] 多模态支持（图像base64+URL）✅
  - [•] 历史对话管理 ✅
  - [•] 长期记忆机制（接口预留）✅
  - [•] RAG检索增强（接口预留）✅
  - [•] 上下文窗口管理 ✅
  - [•] Token使用统计 ✅
  - [•] **人设管理系统集成** ✅
    - [•] 支持从文件加载人设（Markdown格式）✅
    - [•] 支持运行时动态切换人设 ✅
    - [•] 支持参数替换（{{param_name}}格式）✅

- [•] **persona_manager.py** - 人设管理器 ✅
  - [•] 人设文件加载和缓存 ✅
  - [•] 支持Markdown格式人设文件 ✅
  - [•] 运行时人设切换 ✅
  - [•] 集成变量处理器，自动替换参数 ✅
  - [•] 线程安全的人设管理 ✅
  - [•] 人设列表查询功能 ✅

- [•] **variable_processor.py** - 变量处理器 ✅
  - [•] 识别和替换 {{param_name}} 格式变量 ✅
  - [•] 内置参数支持：
    - [•] {{current_time}} - 当前时间（HH:MM:SS）✅
    - [•] {{current_date}} - 当前日期（YYYY-MM-DD）✅
    - [•] {{weekday}} - 星期几（中文格式）✅
    - [•] {{user_name}} - 用户名（从配置读取）✅
  - [•] 可扩展的自定义参数处理器接口 ✅
  - [•] 线程安全的变量替换 ✅

- [•] **personas/** - 人设文件目录 ✅
  - [•] 支持Markdown格式（.md）和文本格式（.txt）✅
  - [•] 默认人设文件（default.md）✅
  - [•] 示例人设文件（assistant.md）✅


### 4. TTS模块 (tts/)
- [•] **edge_tts_engine.py** - Edge TTS生成引擎 ✅
  - [•] Edge TTS集成 ✅
  - [•] 多语言语音选择 ✅
  - [•] 静音裁剪 ✅
  - [•] 内存处理（无文件I/O）✅
  - [•] 语音情感控制（接口预留）✅
  - [•] 语速调整 ✅
  - [•] 音调调整 ✅
  - [•] 非流式生成模式（等待完整句子后生成完整音频）✅
  - [•] **TTS文件缓存集成** ✅
    - [•] 持久化文件缓存（所有句子都缓存）✅
    - [•] 缓存检查：生成前检查缓存，存在则直接加载 ✅
    - [•] 缓存保存：生成后自动保存到缓存（不超过最大长度）✅
    - [•] 首句标识：支持 `is_first_sentence` 参数，区分首句和后续句子 ✅
    - [•] 按语音组织：缓存按语音（voice）组织目录结构 ✅

- [•] **tts_file_cache.py** - TTS文件缓存管理器 ✅
  - [•] 持久化文件缓存：所有生成的TTS音频保存到项目文件夹 ✅
  - [•] 目录组织：
    - [•] 按语音（voice）组织主目录：`{cache_dir}/{voice}/` ✅
    - [•] 首句单独保存：`{cache_dir}/{voice}/first_sentence/{hash}.mp3` ✅
    - [•] 后续句子按长度阶梯分类：`{cache_dir}/{voice}/{length_range}/{hash}.mp3` ✅
  - [•] 长度阶梯分类：根据配置的长度阶梯列表自动分类（如 0-10字、10-20字等）✅
  - [•] 最大长度限制：超过配置的最大长度的TTS音频不保存 ✅
  - [•] 文件命名：使用文本内容的MD5哈希作为文件名，确保唯一性 ✅
  - [•] 线程安全：使用 `threading.Lock` 保护文件操作 ✅
  - [•] 缓存统计：提供缓存统计信息查询功能 ✅
  - [•] 缓存清理：支持按语音、长度范围、首句筛选清理缓存 ✅

- [•] **audio_player.py** - 音频播放器 ✅
  - [•] Pygame音频播放 ✅
  - [•] 暂停/恢复功能 ✅
  - [•] 停止功能 ✅
  - [•] 打断检测 ✅
  - [•] 播放完成回调 ✅
  - [•] 音量控制 ✅
  - [•] 播放进度跟踪 ✅
  - [•] 音频可视化（接口预留）✅
  - [•] 流式播放状态返回（通过事件系统）✅
  - [•] 播放进度事件（AUDIO_PLAY_PROGRESS）✅
  - [•] 文本位置估算（当前句子字符位置）✅
  - [ ] 支持流式音频块播放


### 5. 辅助工具模块 (utils/)
- [•] **logger.py** - 日志系统 ✅
  - [•] 标准化日志格式 ✅
  - [•] 日志级别控制 ✅
  - [•] 线程信息显示 ✅
  - [•] 时间戳格式化 ✅
  - [•] 日志文件输出 ✅
  - [•] 性能日志（通过timer.py实现）✅

- [•] **timer.py** - 时间记录 ✅
  - [•] 响应耗时统计 ✅
  - [•] 各阶段耗时分析 ✅
  - [•] 性能指标输出 ✅
  - [•] 时间戳管理 ✅

- [•] **event_logger.py** - 统一事件日志处理器 ✅
  - [•] 通过事件系统统一管理所有模块的日志输出 ✅
  - [•] 根据事件类型格式化日志消息 ✅
  - [•] 计算事件间的时间差（delta）✅
  - [•] 支持控制台和文件输出 ✅
  - [•] 维护事件时间轴 ✅
  - [•] 自动过滤高频事件（如播放进度）✅

- [•] **performance_monitor.py** - 性能监控器 ✅
  - [•] 通过事件系统统一管理时间记录 ✅
  - [•] 使用事件时间戳精确计算各阶段耗时 ✅
  - [•] 维护完整的事件时间轴 ✅
  - [•] 输出详细的响应耗时分析 ✅
  - [•] 支持模块内部细粒度耗时统计（ASR模型推理、LLM请求等待等）✅

- [•] **config.py** - 配置管理 ✅
  - [•] 配置文件加载（JSON）✅
  - [•] 参数验证 ✅
  - [•] 默认值管理 ✅
  - [•] 环境变量支持 ✅


---

## ⚙️ 关键功能细节

### 1. 状态系统

#### 1.1 主系统状态（用户相关）
- **IDLE**: 主系统空闲，等待用户输入
- **LISTENING**: VAD检测到语音活动，ASR正在识别用户说话内容
- **PLAYING**: 机器人正在播放生成的音频回答

#### 1.2 副系统状态（机器人LLM+TTS线程相关）
- **None**: 副系统无活动，没有正在处理的LLM+TTS任务
- **PROCESSING**: LLM正在生成回答，或TTS正在生成音频
- **PLAYING**: 第一段TTS音频生成完成，开始播放（同时主系统状态变为PLAYING）
- **PAUSED**: 检测到用户可能打断，播放已暂停，正在等待ASR验证

#### 1.3 状态转换规则
- **主系统状态转换**:
  - IDLE → LISTENING: VAD检测到语音开始时
  - IDLE → PLAYING: 检测到TTS生成结果返回，开始播放
  - LISTENING → IDLE: VAD检测到语音结束时，ASR识别完成
  - PLAYING → IDLE: 所有音频播放完成（副系统状态变为None时）
- **副系统状态转换**:
  - None → PROCESSING: ASR识别到有效用户输入，开始处理LLM+TTS
  - PROCESSING → PLAYING: 第一段TTS音频生成完成，开始播放
  - PROCESSING + 主系统LISTENING → PAUSED: LLM或TTS处理时，检测到用户可能打断
  - PLAYING + 主系统LISTENING → PAUSED: 检测到用户可能打断，延迟验证通过后暂停播放
  - PAUSED → PLAYING: ASR验证失败（无效输入），恢复播放
  - PAUSED → None: 任务被取消，或ASR验证成功（有效输入）重新处理
  - PLAYING → None: 全部播放完毕，关闭线程

### 2. VAD与ASR配合机制
- **VAD检测到语音活动**: 
  - 在音频采集线程中，每0.5秒检测一次VAD
  - 当检测到语音活动且之前是静音时，调用`start_streaming_asr()`启动ASR
  - 更新`last_active_time = time.time()`记录最后活动时间
- **ASR的结束控制**:
  - **不是由VAD直接控制**，而是由`NO_SPEECH_THRESHOLD`参数控制
  - 在音频采集线程中，持续检查`time.time() - last_active_time > NO_SPEECH_THRESHOLD`
  - 当超过阈值时，调用`save_audio_video()` → `wait_for_streaming_asr_result()` → `stop_streaming_asr()`
  - `stop_streaming_asr()`设置`streaming_asr_speaking = False`，ASR线程检测到此标志后退出
  - ASR线程退出前处理剩余音频并进行最终识别（`is_final=True`）
- **设计优势**:
  - 避免用户短暂停顿（如换气）时误判为结束
  - 给用户足够时间完成一句话，提高识别准确性
  - 通过`NO_SPEECH_THRESHOLD`可调节响应速度和误判率

### 3. 打断机制
- **延迟验证**: 检测到语音后延迟AUDIO_STOP_DELAY秒再暂停，避免误判
  - **重要**: 在延迟验证期间，所有线程（播放、处理、生成等）继续正常运行，完全不受影响
  - 只有延迟验证通过后，才会暂停播放或取消任务
  - 如果延迟验证失败（检测到持续静音），所有操作继续正常进行，完全不受影响
- **持续检测**: 延迟期间持续检查语音活动状态
- **静音误判**: 如果延迟期间检测到持续静音，判断为误判，取消暂停
- **ASR验证**: 暂停后等待ASR结果，验证是否为有效输入
- **状态转换**: 主系统LISTENING + 副系统PLAYING/PROCESSING → PAUSED
- **播放时长判断**: 根据播放时长决定是否记录到历史

### 4. 任务管理
- **任务ID机制**: 每个任务有唯一的递增task_id
  - 新任务创建时，task_id自动递增
  - 任务被取消时，task_id会递增（创建新任务）
  - 子线程通过检查task_id是否匹配来判断任务是否有效
  - 如果task_id不匹配，子线程立即停止工作
- **任务取消**: 通过检查task_id是否匹配来判断任务是否有效
- **任务状态**: 跟踪任务的处理状态（listening/pending/processing/playing/paused/completed/cancelled）
- **线程安全**: 使用锁保护任务状态（task_lock、asr_lock、interrupt_lock）
- **线程间任务取消**: 所有子线程（线程6-9）持续检查task_id，不匹配时立即停止

### 5. 线程交互场景
- **正常对话流程**: 主系统IDLE → LISTENING → IDLE，副系统None → PROCESSING → PLAYING → None
- **播放时被打断**: 主系统PLAYING → LISTENING，副系统PLAYING → PAUSED
- **处理时被打断**: 主系统IDLE → LISTENING，副系统PROCESSING → PAUSED → None
- **输入合并流程**: 用户在第一句话还没处理完时继续说话，合并新旧输入


### 6. 对话历史管理
- **完整对话**: 播放完成时记录完整对话
- **打断对话**: 播放被打断时记录已播放部分（添加"..."标记）
- **播放时长判断**: 播放时长<MIN_PLAY_DURATION_TO_RECORD时不记录
- **历史格式**: [{"role": "user/assistant", "content": "..."}]


### 7. 流式处理
- **LLM流式生成**: 实时接收token，检测完整句子
- **句子合并**: 根据最小长度要求合并短句
- **TTS并行**: LLM生成句子的同时，并行进行TTS生成
- **播放队列**: 使用队列管理TTS生成和播放的同步

#### 7.1 TTS流式生成模式
系统支持两种TTS生成模式，可通过配置选择：

**非流式模式（EdgeTTS）**:
- 等待完整句子文本输入
- 生成完整音频后一次性返回
- 适合网络TTS服务，稳定性好
- 首字延迟较大（需要等待完整生成）

**流式模式（CosyVoice3）**:
- **直接对接LLM流式输出**，不需要等待完整句子
- LLM生成的文本流实时传递给TTS，有多少文本就生成多少音频
- 开启`stream=True`后，在音频生成过程中逐块返回音频数据
- 首个音频块生成后立即放入播放队列并启动播放，实现最低延迟
- 后续音频块持续生成和播放，实现真正的实时响应
- 适合本地模型，响应速度快

**流式TTS工作流程**:
1. LLM流式生成文本token → 实时传递给TTS（不等待完整句子）
2. TTS累积文本到一定长度（或时间间隔）→ 调用CosyVoice3流式生成
3. 首个音频块生成 → 立即放入播放队列 → 启动播放线程
4. LLM继续生成 → TTS继续生成音频块 → 播放线程持续播放
5. 形成真正的pipeline：LLM生成 → TTS生成 → 播放 三者并行
6. 任务取消检查：在每个音频块生成前后检查task_id

**性能优势**:
- **最低延迟**：不需要等待完整句子，文本流实时转换为语音流
- **真正的实时响应**：LLM、TTS、播放三者并行，延迟极低
- **本地推理无网络延迟**
- **自然流畅**：文本到语音的转换几乎实时进行

**与非流式模式的区别**:
- **非流式**：LLM → 完整句子 → TTS完整音频 → 播放（需要等待多个环节）
- **流式**：LLM → 文本流 → TTS音频流 → 播放（三者并行，延迟最低）


### 8. ASR去重
- **重叠检测**: 检测新文本与已累积文本的重叠部分
- **纠错处理**: 识别模型纠错情况，替换错误部分
- **累积文本**: 维护完整的识别文本


### 9. 静音裁剪
- **开头静音**: 裁剪TTS生成音频开头的静音
- **结尾静音**: 裁剪TTS生成音频结尾的静音
- **内存处理**: 全程在内存中处理，无文件I/O


### 10. 回声消除
- **问题**: 电脑外放声音会被麦克风采集，导致识别到机器人自己的声音
- **解决方案**: 实现回声消除（AEC）功能
  - **播放参考信号**: 捕获正在播放的音频作为参考信号
  - **实时过滤**: 从麦克风输入中实时去除播放音频的回声
  - **自适应抑制**: 根据环境自适应调整过滤强度
  - **延迟补偿**: 处理播放和采集之间的延迟差异
- **实现方式**: 
  - 使用音频过滤库（如speexdsp、webrtc-aec等）
  - 或实现简单的音频相关性检测和抑制
  - 在音频进入VAD/ASR之前进行过滤处理

### 11. 人设管理系统
- **人设文件管理**: 
  - 人设文件存储在 `llm/personas/` 目录下，支持Markdown格式（.md）和文本格式（.txt）
  - 通过配置文件 `llm.persona_name` 指定默认人设
  - 人设文件内容会被缓存，提高加载速度
- **参数替换功能**:
  - 支持 `{{param_name}}` 格式的变量替换
  - 内置参数：
    - `{{current_time}}` - 当前时间（HH:MM:SS格式）
    - `{{current_date}}` - 当前日期（YYYY-MM-DD格式）
    - `{{weekday}}` - 星期几（中文格式：星期一、星期二等）
    - `{{user_name}}` - 用户名（从配置 `llm.user_name` 读取）
  - 参数在每次调用LLM时实时替换，确保时间等参数始终最新
  - 支持自定义参数处理器扩展
- **运行时切换**:
  - 通过 `DialogueManager.switch_persona(name)` 方法可以运行时动态切换人设
  - 切换后立即生效，下次LLM调用时使用新人设
  - 支持通过 `get_current_persona_name()` 查询当前人设
- **向后兼容**:
  - 保留 `llm.system_prompt` 配置项作为fallback
  - 当人设文件不存在时，自动使用配置中的 `system_prompt`


---

## 📊 性能优化

### 1. 响应耗时优化
- **统一事件日志系统**: 通过事件系统统一管理时间记录，使用事件时间戳精确计算各阶段耗时 ✅
- **细粒度耗时分析**: 
  - ASR识别（含模型推理时间）✅
  - LLM生成（含API请求等待时间、流式响应时间）✅
  - 队列等待（线程调度时间）✅
  - TTS生成和处理（含缓存命中状态）✅
  - 其他开销（格式转换、队列操作等）✅
- **首字延迟**: 从VAD检测到语音停止到机器人说出第一个字的时间 ✅
- **LLM生成**: 第一个句子生成耗时（从ASR识别完成到LLM第一句生成）✅
- **TTS生成**: 第一个TTS生成耗时（含生成和处理时间）✅
- **队列等待**: 线程调度等待时间 ✅


### 2. 内存优化
- **音频预缓存**: 使用环形缓冲区，限制大小
- **内存播放**: TTS和播放全程在内存中，无文件I/O
- **队列管理**: 及时清理完成的队列项


### 3. 线程优化
- **线程池**: 考虑使用线程池管理TTS生成线程
- **异步处理**: 部分操作可以异步化


---

## ⚠️ 注意事项

### 1. 线程安全
- [ ] 所有共享状态使用锁保护（task_lock、asr_lock、interrupt_lock等）
- [ ] 主系统和副系统状态管理（状态组合规则、状态转换）
- [ ] 任务ID检查确保任务有效性（task_id机制）
- [ ] 线程间任务取消机制（所有子线程持续检查task_id）
- [ ] 注意回调函数中的线程安全
- [ ] 避免在回调中执行耗时操作

### 2. 资源管理
- [ ] 及时清理音频资源
- [ ] 限制队列大小（sentence_queue、audio_play_queue）
- [ ] 注意内存泄漏（保持Sound对象引用）
- [ ] 及时停止不需要的线程（daemon线程管理）
- [ ] ASR线程唯一性管理（同一时间只能有一个ASR识别任务）
- [ ] 线程生命周期管理（常驻线程 vs 按需启动线程）

### 3. 错误处理
- [•] 基本的异常捕获 ✅
- [•] 需要更完善的错误恢复机制 ✅
- [•] 网络错误重试 ✅
- [•] 模型加载失败处理 ✅

### 4. 配置管理
- [•] 参数硬编码，需要配置文件 ✅
- [•] 环境变量支持 ✅
- [•] 参数验证 ✅

### 5. 日志系统
- [•] 统一的日志格式 ✅
- [•] 日志级别控制 ✅
- [•] 性能日志输出 ✅
- [•] **统一事件日志系统** ✅
  - [•] 所有模块通过事件系统统一输出日志 ✅
  - [•] EventLogger统一格式化和管理日志输出 ✅
  - [•] 自动计算事件间时间差（delta）✅
  - [•] 支持控制台和文件输出 ✅
  - [•] 自动过滤高频事件 ✅

---

## 🔧 配置参数

### 音频参数
- `sample_rate`: 16000 (采样率)
- `chunk_size`: 1024 (音频块大小)
- `vad_mode`: 3 (VAD敏感度，0-3)

### 音频过滤参数
- `enable_echo_cancellation`: True (是否启用回声消除)
- `echo_cancellation_strength`: 0.8 (回声消除强度，0.0-1.0)
- `playback_reference_delay`: 0 (播放参考信号延迟补偿，毫秒)

### ASR参数
- `asr_chunk_size`: [0, 10, 5] (ASR chunk配置)
- `encoder_chunk_look_back`: 4
- `decoder_chunk_look_back`: 1
- `pre_buffer_seconds`: 0.5 (预缓存时长)

### 对话参数
- `no_speech_threshold`: 1.2 (无语音阈值，秒。用户停止说话后等待多久才认为说话结束，触发ASR停止。值越大越不容易误判（如换气时），但响应会变慢)
- `min_audio_length`: 0.5 (最小音频长度，秒)
- `audio_stop_delay`: 0.3 (延迟验证时间，秒。在延迟验证期间，所有线程继续正常运行，完全不受影响。只有延迟验证通过后，才会暂停播放或取消任务)
- `min_play_duration_to_record`: 0.5 (最小播放时长阈值，秒。播放时长小于此值时不记录到对话历史)

### LLM参数
- `max_tokens`: 512
- `temperature`: 0.7
- `min_sentence_length`: 15 (第一句话最小长度)
- `sentence_length_increment_rate`: 1.5 (句子长度增量倍率)
- `max_sentence_length`: 200 (最大句子长度，字符数或单词数，防止句子无限增长)
- `persona_name`: "default" (默认人设名称，对应llm/personas/目录下的文件名，不含扩展名)
- `persona_dir`: "llm/personas" (人设文件目录，相对于项目根目录)
- `user_name`: "用户" (用户名，用于人设文件中的{{user_name}}变量替换)
- `system_prompt`: "..." (系统提示词，保留作为向后兼容的fallback，当人设文件不存在时使用)

### TTS参数
- `engine`: "edgetts" 或 "cosyvoice3" (TTS引擎选择，edgetts使用非流式，cosyvoice3使用流式)
- `silence_threshold`: -40 (静音检测阈值，dB)
- `min_silence_len`: 30 (最小静音长度，毫秒)
- `enable_trim_silence`: True (是否裁剪静音)
- `trim_keep_start`: 20 (裁剪时开头固定保留长度，毫秒)
- `trim_keep_end`: 300 (裁剪时结尾固定保留长度，毫秒)
- `playback_status_update_interval`: 0.1 (播放状态更新间隔，秒。用于流式状态返回，值越小更新越频繁，但会增加事件系统负载)
- `tts_file_cache_enabled`: True (是否启用TTS文件缓存，持久化缓存所有生成的TTS音频)
- `tts_file_cache_dir`: "tts_cache" (TTS缓存目录路径，相对于项目根目录)
- `tts_file_cache_length_steps`: [10, 20, 30] (长度阶梯列表，用于按长度分类缓存文件，最后一个值作为最大缓存长度)

### CosyVoice3参数（当engine="cosyvoice3"时使用）
- `cosyvoice3.model_dir`: "D:/APP/models/Qwen/Fun-CosyVoice3-0.5B-2512" (模型目录路径)
- `cosyvoice3.reference_audio_path`: (参考音频路径，用于零样本语音克隆)
- `cosyvoice3.reference_text`: "你是一个有用的助手。" (参考音频对应的文本)
- `cosyvoice3.enable_streaming`: True (是否启用流式生成，建议True以降低延迟)
- `cosyvoice3.stream_chunk_size`: 25 (流式chunk大小，token数)
- `cosyvoice3.speed`: 1.0 (语速，1.0为正常速度)
- `cosyvoice3.sample_rate`: 24000 (CosyVoice3输出采样率，Hz)
- `cosyvoice3.target_sample_rate`: 24000 (目标采样率，Hz，可转换为其他值)
- `cosyvoice3.load_jit`: True (是否加载JIT优化模型)
- `cosyvoice3.load_onnx`: False (是否加载ONNX模型)
- `cosyvoice3.fp16`: True (是否使用FP16精度)

### API服务参数
- `api.enabled`: False (是否启用API服务)
- `api.host`: "0.0.0.0" (API服务监听地址)
- `api.port`: 6000 (API服务端口)
- `api.cors_enabled`: True (是否启用跨域支持)
- `api.auth_enabled`: False (是否启用API认证)
- `api.api_key`: "" (API密钥，如果启用认证)


### 无效词列表
- `invalid_recognition_words`: ["", "i", "嗯", "啊", "呃", "哦", "额"]


## 🧪 测试要求

### 单元测试
- [•] 各模块独立测试 ✅
- [•] 状态机测试 ✅
- [•] 事件系统测试 ✅

### 集成测试
- [•] 完整对话流程测试 ✅
- [•] 打断流程测试 ✅
- [•] 输入合并测试 ✅
- [•] 错误处理测试 ✅

### 性能测试
- [•] 响应延迟测试 ✅
- [•] 内存使用测试 ✅
- [•] 并发测试 ✅

---

## 🐍 环境配置

### Conda环境
**要求使用conda环境 `voice`**

```bash
# 激活conda环境
conda activate voice

# 运行程序
cd d:\APP\ASR-LLM-TTS
python -m voice_chatbot.main
```

### 依赖包
- pyaudio
- webrtcvad
- funasr
- edge-tts
- pygame
- pydub
- openai
- numpy
- langid
- langdetect

### 环境检查
```bash
# 检查conda环境
conda env list

# 如果voice环境不存在，创建它
conda create -n voice python=3.10
conda activate voice

# 安装依赖
pip install pyaudio webrtcvad funasr edge-tts pygame pydub openai numpy langid langdetect
```

---

## 📚 文档要求

- [ ] 项目结构说明
- [ ] 模块接口文档
- [ ] API使用文档
- [ ] 配置说明文档
- [ ] 故障排查文档


---

## 📝 当前实现状态

### 代码位置
`voice_chatbot` 文件夹下已实现所有核心模块。

### 参考实现
功能在 `56_Lincoco_streamASR_openai_edgetts_realTime.py` 文件中已有实现，已作为参考完成模块化重构。✅

### 已实现功能
- [•] **工具模块 (utils/)**：日志系统、性能统计、时间记录、配置管理、统一事件日志系统 ✅
- [•] **音频输入模块 (audio/)**：麦克风采集、VAD检测、流式ASR识别、事件驱动日志 ✅
- [•] **对话管理模块 (dialogue/)**：状态管理、事件系统、核心调度器、统一日志管理 ✅
- [•] **LLM模块 (llm/)**：流式生成、句子分割、标签过滤、历史对话管理、细粒度事件 ✅
- [•] **TTS模块 (tts/)**：Edge TTS集成、多语言选择、静音裁剪、内存处理、音频播放、事件驱动日志 ✅
- [•] **主程序 (main.py)**：系统启动和运行 ✅
- [•] **统一事件日志系统**：所有模块通过事件系统统一输出日志，精确记录时间，输出详细耗时分析 ✅

### 待实现功能
- [ ] **TTS模块增强**：CosyVoice3引擎集成、流式TTS生成、零样本语音克隆
- [ ] **API服务模块**：流式TTS API接口、LLM API接口，统一对外提供服务
- [ ] **LLM本地服务**：本地模型加载、自动启动OpenAI兼容服务
- [ ] **测试**：集成测试、性能测试
- [ ] **文档**：API文档、配置说明、故障排查文档

### 下一步工作建议
1. 进行集成测试，验证完整系统运行
2. 完善错误处理和异常恢复机制
3. 优化性能，减少响应延迟
4. 编写API文档和配置说明
5. 添加故障排查文档

---

---

## 🆕 新增功能规划

### 1. 流式TTS API服务

**目标**：提供统一的TTS API接口，支持流式和非流式生成，供外部调用。

**实现方案**：
- 创建 `voice_chatbot/api/tts_api_server.py`
- 使用FastAPI框架
- 统一接口：`POST /v1/tts/stream`
- 支持EdgeTTS和CosyVoice3两种引擎
- 流式音频响应（StreamingResponse）
- 支持WAV/MP3/PCM格式

**API接口设计**：
```python
POST /v1/tts/stream
{
    "text": "要合成的文本",
    "engine": "edgetts" | "cosyvoice3",
    "voice": "语音ID（可选）",
    "language": "zh" | "en" | ...,
    "stream": true,  # 是否流式返回
    "format": "wav" | "mp3" | "pcm"
}
Response: StreamingResponse (音频流)
```

**是否需要改动现有模块**：
- ✅ **TTS模块**：需要添加API封装层，但核心引擎无需改动
- ✅ **配置模块**：需要添加API相关配置项
- ❌ **其他模块**：无需改动，API服务独立运行

### 2. LLM本地服务加载

**目标**：自动加载本地LLM模型，启动OpenAI兼容服务，供外部调用。

**实现方案**：
- 创建 `voice_chatbot/llm/llm_local_loader.py`
- 支持多种模型格式（GGUF、PyTorch、HuggingFace）
- 自动启动本地服务（如使用llama.cpp、text-generation-webui等）
- 或集成到项目内部，直接加载模型
- 与现有`llm_openai_stream.py`集成

**功能特性**：
- 模型自动加载和初始化
- 服务健康检查
- 支持服务启动/停止
- 内存管理（GPU/CPU自动选择）
- OpenAI API兼容接口

**是否需要改动现有模块**：
- ✅ **LLM模块**：需要新增`llm_local_loader.py`，但`llm_openai_stream.py`无需改动（只需修改api_base配置）
- ✅ **配置模块**：需要添加本地LLM服务配置项
- ✅ **主程序**：可选择是否启动本地LLM服务
- ❌ **其他模块**：无需改动，通过API调用方式统一

### 3. 统一API服务入口

**目标**：整合TTS和LLM API，提供统一的服务入口。

**实现方案**：
- 创建 `voice_chatbot/api/api_server.py`
- 整合TTS API和LLM API
- 提供统一的API路由和文档
- 支持服务状态监控

**是否需要改动现有模块**：
- ✅ **新增模块**：创建api/目录和相关文件
- ❌ **现有模块**：无需改动，API服务作为独立层

### 4. 模块改动总结

**需要新增的模块**：
1. `voice_chatbot/api/tts_api_server.py` - TTS API服务
2. `voice_chatbot/api/api_server.py` - 统一API入口
3. `voice_chatbot/llm/llm_local_loader.py` - 本地LLM加载器（可选）

**需要改动的现有模块**：
1. `voice_chatbot/utils/config.py` - 添加API和本地LLM配置项
2. `voice_chatbot/main.py` - 可选择启动API服务
3. `voice_chatbot/tts/__init__.py` - 导出API相关接口（可选）

**无需改动的模块**：
- ✅ 所有现有功能模块（audio/、dialogue/、现有TTS/LLM引擎）保持原样
- ✅ 现有功能逻辑完全不变，API服务作为附加功能

---

**最后更新**: 2025-01-XX
**文档版本**: 1.3.0
**维护者**: AI Assistant

