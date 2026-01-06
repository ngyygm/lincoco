# 语音对话系统 - 项目大纲文档

## ⚠️ 重要提示（必读）

### 开发环境要求
- **测试必须使用 conda 环境 `voice`** 进行开发和测试
- 激活环境：`conda activate voice`
- 运行程序：`cd d:\APP\Lincoco && python main.py`

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
麦克风采集 → VAD → ASR → 对话调度器 → LLM流式生成 → TTS+播放
```

---

## 🏗️ 模块架构

### 系统架构总览

系统采用**主系统（用户）**和**副系统（机器人LLM+TTS线程）**的双系统架构：

- **主系统状态**（用户相关）：IDLE、LISTENING、PLAYING
- **副系统状态**（机器人LLM+TTS线程相关）：None、PROCESSING、PLAYING、PAUSED
- **状态组合规则**：主系统状态和副系统状态可以叠加，各自内部互斥

系统采用事件驱动的线程管理架构：
- **常驻后台线程**：音频采集线程、VAD后台线程、事件总线处理线程
- **按需启动线程**：ASR识别线程、ASR内部识别线程、LLM+TTS处理线程、TTS生成线程、音频播放线程

详细线程架构和交互逻辑请参考 `manager.py` 实现。

### 1. 音频输入模块 (asr/)
- [•] **fun_asr_stream.py** - 流式语音识别 ✅
  - [•] FunASR流式识别 ✅
  - [•] 预缓存机制（避免丢失开头）✅
  - [•] ASR去重器（处理重复文本）✅
  - [•] 部分结果回调 ✅
  - [•] 最终结果回调 ✅
  - [•] 多语言支持 ✅
  - [•] 识别置信度输出 ✅
  - [•] **VAD与ASR配合机制** ✅
    - [•] VAD检测到语音活动时启动ASR ✅
    - [•] ASR的结束由`NO_SPEECH_THRESHOLD`控制 ✅
    - [•] 用户停止说话超过`NO_SPEECH_THRESHOLD`秒时触发ASR停止 ✅
    - [•] ASR线程退出前处理剩余音频并进行最终识别 ✅

- [•] **webrtc_vad.py** - 语音活动检测 ✅
  - [•] WebRTC VAD集成 ✅
  - [•] 实时语音活动检测 ✅
  - [•] 语音开始/结束回调 ✅
  - [•] 可配置敏感度 ✅
  - [•] 自适应阈值调整 ✅

- [•] **speaker_verification.py** - 声纹验证 ✅
  - [•] 3D-Speaker集成 ✅
  - [•] 多声纹支持 ✅
  - [•] 并行比对 ✅
  - [•] 滑动窗口验证 ✅
  - [•] 自动注册 ✅
  - [•] 嵌入缓存 ✅

- [•] **punctuation_restore.py** - 标点符号恢复 ✅
  - [•] ASR结果标点恢复 ✅

### 2. 对话管理模块 (dialogue/)

- [•] **state.py** - 状态机 ✅
  - [•] 主系统状态枚举（IDLE/LISTENING/PLAYING）✅
  - [•] 副系统状态枚举（None/PROCESSING/PLAYING/PAUSED）✅
  - [•] 状态组合规则 ✅
  - [•] 任务信息管理 ✅
  - [•] 对话历史管理 ✅
  - [•] 线程安全的状态操作 ✅
  - [•] 状态转换日志 ✅

- [•] **events.py** - 事件系统 ✅
  - [•] 事件类型定义 ✅
  - [•] 事件数据结构 ✅
  - [•] 事件时间戳 ✅
  - [•] 事件优先级 ✅
  - [•] 事件过滤机制 ✅
  - [•] **统一事件日志系统** ✅
    - [•] 系统级事件 ✅
    - [•] 细粒度ASR事件 ✅
    - [•] 细粒度LLM事件 ✅
    - [•] TTS缓存事件 ✅
    - [•] 通用日志事件 ✅
    - [•] EventLogger统一格式化和管理日志输出 ✅
    - [•] PerformanceMonitor基于事件时间戳精确计算耗时 ✅

- [•] **task_context.py** - 任务上下文 ✅
  - [•] 任务上下文类，隔离每个任务的状态和数据 ✅
  - [•] 任务有效性检查 ✅
  - [•] 任务取消异常 ✅
  - [•] 线程安全的任务状态访问 ✅
  - [•] 任务数据隔离 ✅

- [•] **context_manager.py** - 上下文管理器 ✅
  - [•] 统一管理任务上下文的创建、访问和销毁 ✅
  - [•] 创建新任务时自动使旧任务失效 ✅
  - [•] 线程安全的上下文访问 ✅
  - [•] 上下文生命周期管理 ✅

- [•] **thread_manager.py** - 线程管理器 ✅
  - [•] 统一管理所有工作线程的生命周期 ✅
  - [•] 绑定线程到任务上下文 ✅
  - [•] 线程启动/停止的统一接口 ✅
  - [•] 线程状态跟踪（通过事件系统）✅
  - [•] 线程异常处理和恢复 ✅
  - [•] 支持常驻线程和按需启动线程的统一管理 ✅

- [•] **state_manager.py** - 状态管理器 ✅
  - [•] 通过事件驱动实现原子性状态转换 ✅
  - [•] 自动验证状态组合有效性 ✅
  - [•] 主系统状态转换 ✅
  - [•] 副系统状态转换 ✅
  - [•] 状态转换事件发布 ✅
  - [•] 状态转换日志（通过事件系统）✅
  - [•] 状态组合规则验证 ✅

- [•] **manager.py** - 核心调度器 ✅
  - [•] 模块初始化与协调 ✅
  - [•] 集成 ThreadManager、StateManager、ContextManager ✅
  - [•] 主线程（线程0）职责：系统初始化、状态管理、线程协调 ✅
  - [•] 通过 ThreadManager 管理所有线程生命周期 ✅
  - [•] 通过 StateManager 管理状态转换（事件驱动）✅
  - [•] 通过 ContextManager 管理任务上下文 ✅
  - [•] 所有线程操作通过事件系统统一日志输出 ✅
  - [•] 常驻后台线程管理 ✅
    - [•] 音频采集线程：持续采集音频，发送给VAD和ASR预缓存 ✅
    - [•] VAD后台线程：持续监控音频，检测语音活动，触发回调 ✅
    - [•] 事件总线处理线程：持续处理事件队列，按优先级通知订阅者 ✅
  - [•] 按需启动线程管理 ✅
    - [•] ASR识别线程：VAD检测到语音时启动，管理ASR识别流程 ✅
    - [•] ASR内部识别线程：由ASR识别线程启动，执行FunASR流式识别 ✅
    - [•] LLM+TTS处理线程：处理用户输入，启动LLM生成和TTS流程 ✅
    - [•] TTS生成线程：生成TTS音频 ✅
    - [•] 音频播放线程：播放TTS生成的音频，处理打断和暂停 ✅
  - [•] 用户输入处理 ✅
  - [•] 打断机制（延迟验证、ASR验证）✅
  - [•] 输入合并逻辑 ✅
  - [•] 任务生命周期管理（通过 ContextManager 和 TaskContext）✅
  - [•] 错误恢复机制 ✅
  - [•] 性能监控 ✅
  - [•] **人设切换功能** ✅
    - [•] `switch_persona(name)` 方法支持运行时动态切换人设 ✅
    - [•] `get_current_persona_name()` 方法获取当前人设名称 ✅

#### 架构说明（事件驱动架构）

为了解决锁管理混乱、共享变量污染、线程生命周期难以追踪等问题，引入事件驱动的架构：

**核心组件**：
- **TaskContext**：任务上下文，隔离每个任务的状态和数据
- **ContextManager**：上下文管理器，统一管理任务上下文的创建、访问和销毁
- **ThreadManager**：线程管理器，统一管理所有工作线程的生命周期
- **StateManager**：状态管理器，通过事件驱动实现原子性状态转换

**架构优势**：
- **锁管理集中化**：每个Manager 1个锁，集中管理
- **变量隔离**：每个任务独立TaskContext
- **状态管理集中化**：集中在StateManager，事件驱动
- **可追踪性**：所有变化通过事件记录
- **统一日志输出**：所有线程操作通过事件系统统一输出日志

**线程管理流程**：
1. 创建任务 → ContextManager.create_context() → 自动使旧任务失效
2. 启动线程 → ThreadManager.start_thread() → 绑定到任务上下文 → 发布 THREAD_STARTED 事件
3. 状态转换 → StateManager.transition_*() → 验证状态组合 → 发布 STATE_CHANGED 事件
4. 线程操作 → 通过事件系统统一日志输出
5. 任务取消 → TaskContext.invalidate() → 所有线程通过检查 is_valid 自动退出

### 3. LLM模块 (llm/)

- [•] **llm_openai_stream.py** - LLM流式生成 ✅
  - [•] OpenAI API兼容 ✅
  - [•] 流式文本生成 ✅
  - [•] 句子分割与合并 ✅
    - [•] **首句切分规则**：第一句话支持按逗号（，,）切分，实现快速响应 ✅
    - [•] **后续句子切分规则**：后续句子必须等待完整句子结束符（。！？.!?\n）才切分 ✅
    - [•] **英文句号识别优化**：智能识别英文句号，排除小数和列表标号 ✅
  - [•] 任务取消检查 ✅
  - [•] 标签过滤 ✅
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
  - [•] 示例人设文件（assistant.md、lincoco.md）✅

### 4. TTS模块 (tts/)

- [•] **edge_tts_engine.py** - Edge TTS生成引擎 ✅
  - [•] Edge TTS集成 ✅
  - [•] 多语言语音选择 ✅
  - [•] 静音裁剪 ✅
  - [•] 内存处理（无文件I/O）✅
  - [•] 语音情感控制（接口预留）✅
  - [•] 语速调整 ✅
  - [•] 音调调整 ✅
  - [•] 非流式生成模式 ✅
  - [•] **TTS文件缓存集成** ✅
    - [•] 持久化文件缓存（所有句子都缓存）✅
    - [•] 缓存检查：生成前检查缓存，存在则直接加载 ✅
    - [•] 缓存保存：生成后自动保存到缓存 ✅
  - [•] **LRU内存缓存** ✅
    - [•] 线程安全的LRU缓存 ✅
    - [•] 可配置最大缓存大小 ✅

- [•] **melo_tts_engine.py** - MeloTTS生成引擎 ✅
  - [•] MeloTTS HTTP API集成 ✅
  - [•] 多语言语音选择 ✅
  - [•] 静音裁剪 ✅
  - [•] 内存处理（无文件I/O）✅
  - [•] 语速调整 ✅
  - [•] 非流式生成模式 ✅
  - [•] **TTS文件缓存集成** ✅
    - [•] 持久化文件缓存 ✅
    - [•] 缓存检查和保存 ✅
  - [•] **LRU内存缓存** ✅
    - [•] 线程安全的LRU缓存 ✅

- [•] **tts_file_cache.py** - TTS文件缓存管理器 ✅
  - [•] 持久化文件缓存：所有生成的TTS音频保存到项目文件夹 ✅
  - [•] 目录组织：
    - [•] 按引擎和语音组织主目录：`{cache_dir}/{engine}/{voice}/` ✅
    - [•] 后续句子按长度阶梯分类：`{cache_dir}/{engine}/{voice}/{length_range}/{hash}.mp3` ✅
  - [•] 长度阶梯分类：根据配置的长度阶梯列表自动分类 ✅
  - [•] 最大长度限制：超过配置的最大长度的TTS音频不保存 ✅
  - [•] 文件命名：使用文本内容的MD5哈希作为文件名，确保唯一性 ✅
  - [•] 线程安全：使用 `threading.Lock` 保护文件操作 ✅
  - [•] 缓存统计：提供缓存统计信息查询功能 ✅
  - [•] 缓存清理：支持按语音、长度范围筛选清理缓存 ✅
  - [•] 自动修复：启动时检查并修复缺失的缓存文件 ✅

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
  - [•] 播放进度事件 ✅
  - [•] 文本位置估算（当前句子字符位置）✅

### 5. 辅助工具模块 (utils/)

- [•] **config.py** - 配置管理 ✅
  - [•] 配置文件加载（JSON）✅
  - [•] 参数验证 ✅
  - [•] 默认值管理 ✅
  - [•] 环境变量支持 ✅
  - [•] 深度合并配置 ✅

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

- [•] **microphone.py** - 麦克风采集 ✅
  - [•] 音频采集接口 ✅
  - [•] 实时音频流处理 ✅
  - [•] 音频数据回调机制 ✅
  - [•] 多设备支持 ✅
  - [•] 音频格式转换 ✅

- [•] **sequence_diagram_recorder.py** - 流程图记录器 ✅
  - [•] 记录系统交互流程 ✅
  - [•] 生成时序图 ✅
  - [•] 支持流程超时处理 ✅

### 6. 主程序 (main.py)

- [•] **main.py** - 主程序入口 ✅
  - [•] 系统初始化 ✅
  - [•] MeloTTS API 自动启动 ✅
  - [•] 端口占用检测 ✅
  - [•] 信号处理（优雅关闭）✅
  - [•] 命令行参数解析 ✅
  - [•] 配置文件加载 ✅
  - [•] 日志初始化 ✅

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
  - 在音频采集线程中，持续检测VAD
  - 当检测到语音活动且之前是静音时，启动ASR
  - 更新last_active_time记录最后活动时间
- **ASR的结束控制**:
  - **不是由VAD直接控制**，而是由`NO_SPEECH_THRESHOLD`参数控制
  - 在音频采集线程中，持续检查`time.time() - last_active_time > NO_SPEECH_THRESHOLD`
  - 当超过阈值时，调用ASR停止
  - ASR线程退出前处理剩余音频并进行最终识别
- **设计优势**:
  - 避免用户短暂停顿（如换气）时误判为结束
  - 给用户足够时间完成一句话，提高识别准确性
  - 通过`NO_SPEECH_THRESHOLD`可调节响应速度和误判率

### 3. 打断机制
- **延迟验证**: 检测到语音后延迟验证，避免误判
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
- **任务状态**: 跟踪任务的处理状态
- **线程安全**: 使用锁保护任务状态
- **线程间任务取消**: 所有子线程持续检查task_id，不匹配时立即停止

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

### 8. ASR去重
- **重叠检测**: 检测新文本与已累积文本的重叠部分
- **纠错处理**: 识别模型纠错情况，替换错误部分
- **累积文本**: 维护完整的识别文本

### 9. 静音裁剪
- **开头静音**: 裁剪TTS生成音频开头的静音
- **结尾静音**: 裁剪TTS生成音频结尾的静音
- **内存处理**: 全程在内存中处理，无文件I/O

### 10. 声纹验证
- **问题**: 确保只有授权用户的声音能被识别
- **解决方案**: 使用3D-Speaker进行声纹识别和验证
  - **多声纹支持**: 支持多个注册音频文件，只要有一个匹配就通过验证
  - **并行比对**: 使用张量并行计算所有注册音频的相似度
  - **滑动窗口**: 支持滑动窗口模式，提高声纹验证的准确性和灵活性
  - **多场景录制**: 支持多场景声纹录制（安静环境、背景噪音、远距离等），提高鲁棒性
  - **自动注册**: 首次运行时自动引导用户录制声纹注册音频
  - **嵌入缓存**: 支持缓存声纹嵌入向量，提高验证速度
- **实现方式**: 
  - 使用3D-Speaker库进行声纹特征提取和比对
  - 在ASR识别结果返回前进行声纹验证
  - 只有通过声纹验证的音频才会触发对话处理

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

### 3. TTS缓存优化
- **LRU内存缓存**: 减少重复请求，可配置最大缓存大小
- **持久化文件缓存**: 所有生成的TTS音频保存到文件，永久缓存
- **按引擎和语音组织**: 便于管理和清理
- **自动修复**: 启动时检查并修复缺失的缓存文件

---

## ⚠️ 注意事项

### 1. 线程安全
- [•] 所有共享状态使用锁保护 ✅
- [•] 主系统和副系统状态管理（状态组合规则、状态转换）✅
- [•] 任务ID检查确保任务有效性 ✅
- [•] 线程间任务取消机制 ✅
- [•] 注意回调函数中的线程安全 ✅
- [•] 避免在回调中执行耗时操作 ✅

### 2. 资源管理
- [•] 及时清理音频资源 ✅
- [•] 限制队列大小 ✅
- [•] 注意内存泄漏（保持Sound对象引用）✅
- [•] 及时停止不需要的线程 ✅
- [•] ASR线程唯一性管理 ✅
- [•] 线程生命周期管理 ✅

### 3. 错误处理
- [•] 基本的异常捕获 ✅
- [•] 错误恢复机制 ✅
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
  - [•] 自动计算事件间时间差 ✅
  - [•] 支持控制台和文件输出 ✅
  - [•] 自动过滤高频事件 ✅

---

## 🔧 配置参数

### 音频参数
- `sample_rate`: 16000 (采样率)
- `chunk_size`: 1024 (音频块大小)
- `channels`: 1 (声道数)

### VAD参数
- `mode`: 3 (VAD敏感度，0-3)
- `activation_rate`: 0.1 (VAD激活率，0.0-1.0)
- `adaptive_threshold`: False (是否启用自适应阈值)
- `min_activation_rate`: 0.15 (最小激活率)
- `max_activation_rate`: 0.5 (最大激活率)
- `check_interval`: 0.1 (VAD检测间隔，秒)
- `thread_sleep`: 0.01 (VAD线程休眠间隔，秒)

### ASR参数
- `model_path`: "paraformer-zh-streaming" (ASR模型路径)
- `language`: "zh" (ASR语言设置)
- `preload_model`: True (是否预加载模型)
- `chunk_size`: [0, 10, 5] (ASR chunk配置)
- `encoder_chunk_look_back`: 4
- `decoder_chunk_look_back`: 1
- `pre_buffer_seconds`: 1.0 (预缓存时长，秒)
- `audio_queue_maxsize`: 100 (音频队列最大大小)
- `result_timeout`: 30.0 (ASR结果等待超时，秒)
- `result_time_buffer`: 0.1 (结果时间缓冲，秒)
- `max_result_age`: 60.0 (结果最大保留时间，秒)
- `no_speech_threshold`: 0.4 (无语音阈值，秒)
- `enable_deduplication`: False (是否启用ASR去重)
- `invalid_recognition_words`: ["", "i", "嗯", "啊", "呃", "哦", "额"] (无效识别词列表)

### 声纹验证参数
- `enabled`: True (是否启用声纹锁)
- `model_id`: "iic/speech_campplus_sv_zh-cn_16k-common" (3D-Speaker模型ID)
- `enroll_audio_path`: "cache/speaker_verification_audio" (注册音频路径)
- `similarity_threshold`: 0.3 (相似度阈值，0.0-1.0)
- `min_audio_length`: 0.5 (最小音频长度，秒)
- `cache_embedding`: True (是否缓存嵌入向量)
- `record_duration`: 8.0 (录制注册音频时长，秒)
- `auto_record`: True (是否自动录制注册音频)
- `sliding_window.enabled`: True (是否启用滑动窗口)
- `sliding_window.window_length`: 0.6 (窗口长度，秒)
- `sliding_window.window_stride`: 0.2 (滑动步长，秒)

### 标点恢复参数
- `enabled`: True (是否启用标点恢复)
- `model`: "ct-punc" (标点恢复模型名称)

### LLM参数
- `provider`: "openai" (LLM服务提供商：openai、ollama、glm)
- `max_tokens`: 1024 (最大生成token数)
- `temperature`: 0.7 (生成温度)
- `max_retries`: 3 (API调用最大重试次数)
- `retry_delay`: 1.0 (重试延迟，秒)
- `request_timeout`: 30.0 (请求超时，秒)
- `if_thinking`: False (是否启用思考模式)
- `min_sentence_length`: 8 (最小句子长度，字符数)
- `first_sentence_comma_delay_chars`: 5 (首句逗号延迟字符数)
- `sentence_length_increment_rate`: 2 (句子长度增量倍率)
- `max_sentence_length`: 200 (最大句子长度，字符数)
- `system_prompt`: "..." (系统提示词，fallback)
- `persona_name`: "lincoco" (默认人设名称)
- `persona_dir`: "llm/personas" (人设文件目录)
- `user_name`: "林嘿嘿" (用户名)
- `max_history_length`: 50 (最大历史记录长度，对话轮数)

### LLM引擎配置
- **OpenAI引擎**:
  - `api_base`: "http://127.0.0.1:5000/v1" (API基础URL)
  - `api_key`: "" (API密钥)
  - `model_name`: "qwen3-1.7b" (模型名称)
- **Ollama引擎**:
  - `api_base`: "http://219.223.187.56:11434/v1" (API基础URL)
  - `api_key`: "" (API密钥)
  - `model_name`: "gemma3:27b" (模型名称)
- **GLM引擎**:
  - `api_base`: "https://open.bigmodel.cn/api/coding/paas/v4" (API基础URL)
  - `api_key`: "" (API密钥)
  - `model_name`: "glm-4-flash-250414" (模型名称)

### TTS参数
- `default_engine`: "melotts" (默认TTS引擎：edge_tts、melo_tts)
- `silence_threshold`: -40 (静音检测阈值，dB)
- `min_silence_len`: 30 (最小静音长度，毫秒)
- `enable_trim_silence`: True (是否裁剪静音)
- `trim_keep_start`: 20 (裁剪时开头固定保留长度，毫秒)
- `trim_keep_end`: 350 (裁剪时结尾固定保留长度，毫秒)
- `enable_normalize_loudness`: True (是否启用响度归一化)
- `playback_check_interval`: 0.05 (播放检查间隔，秒)
- `playback_status_update_interval`: 0.1 (播放状态更新间隔，秒)
- `enable_performance_stats`: True (是否启用性能统计)
- `cache_enabled`: True (是否启用TTS内存缓存)
- `cache_max_size`: None (最大缓存条目数)
- `tts_file_cache_enabled`: True (是否启用TTS文件缓存)
- `tts_file_cache_dir`: "cache/tts_cache" (TTS缓存目录路径)
- `tts_file_cache_max_length`: 20 (最大缓存长度)
- `tts_file_cache_auto_repair`: True (是否自动修复缺失的缓存文件)

### EdgeTTS引擎配置
- `is_streaming`: False (是否流式引擎)
- `voice_map`: 语言-语音映射表
- `connection_timeout`: 30.0 (连接超时时间，秒)
- `enable_session_reuse`: True (是否启用会话复用)

### MeloTTS引擎配置
- `is_streaming`: False (是否流式引擎)
- `api_url`: "http://127.0.0.1:8101" (MeloTTS API服务地址)
- `api_endpoint`: "/tts" (API端点路径)
- `init_endpoint`: "/init" (模型初始化端点路径)
- `timeout`: 30.0 (HTTP请求超时时间，秒)
- `model_language`: "ZH" (要加载的模型语言)
- `speaker_name_map`: 语言到说话人名称的映射
- `default_speaker_name`: None (默认说话人名称)
- `sdp_ratio`: 0.2 (SDP比例)
- `noise_scale`: 0.6 (噪声比例)
- `noise_scale_w`: 0.8 (噪声宽度比例)
- `speed`: 1.0 (语速)

### 日志配置
- `file_enabled`: False (是否启用文件日志)
- `log_dir`: "cache/logs" (日志文件目录)
- `enable_console`: True (是否输出到控制台)
- `show_delta`: True (是否显示与上一事件的时间差)
- `show_source`: True (是否显示代码来源前缀)
- `filtered_levels`: ["INFO"] (允许输出的日志级别列表)
- `filtered_categories`: ["THREAD", "EVENT", "MIC", "TIME", "CTX", "MANAGER", "VAD", "PUN"] (被过滤的日志类别列表)
- `category_colors`: 日志类别专属颜色配置
- `enable_sequence_diagram`: True (是否启用sequenceDiagram记录)
- `sequence_diagram_flow_timeout`: 30.0 (流程超时时间，秒)
- `performance_analysis.enabled`: True (是否启用响应耗时详细分析)
- `performance_analysis.show_details`: True (是否输出各个阶段的细节)

### 系统参数
- `thread_timeout`: 1.0 (通用线程等待超时，秒)
- `greeting.enabled`: True (是否启用问候语)
- `greeting.text`: "你好！我是林可可，很高兴见到你！" (问候语文本)

---

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
cd d:\APP\Lincoco
python main.py
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
- torch
- torchaudio
- pyyaml
- loguru
- requests
- aiohttp
- tqdm
- jieba
- pypinyin
- gradio

### 环境检查
```bash
# 检查conda环境
conda env list

# 如果voice环境不存在，创建它
conda create -n voice python=3.10
conda activate voice

# 安装依赖
pip install -r requirements.txt
```

---

## 📚 文档要求

- [•] 项目结构说明 ✅
- [•] 模块接口文档 ✅
- [•] API使用文档 ✅
- [•] 配置说明文档 ✅
- [•] 故障排查文档 ✅

---

## 📝 当前实现状态

### 代码位置
`voice_chatbot` 文件夹下已实现所有核心模块。

### 参考实现
功能在 `56_Lincoco_streamASR_openai_edgetts_realTime.py` 文件中已有实现，已作为参考完成模块化重构。✅

### 已实现功能
- [•] **工具模块**：日志系统、性能统计、时间记录、配置管理、统一事件日志系统 ✅
- [•] **音频输入模块**：麦克风采集、VAD检测、流式ASR识别、声纹验证、标点恢复、事件驱动日志 ✅
- [•] **对话管理模块**：状态管理、事件系统、核心调度器、统一日志管理 ✅
- [•] **LLM模块**：流式生成、句子分割、标签过滤、历史对话管理、细粒度事件 ✅
- [•] **TTS模块**：Edge TTS集成、MeloTTS集成、多语言选择、静音裁剪、内存处理、音频播放、事件驱动日志、LRU缓存、文件缓存 ✅
- [•] **主程序**：系统启动和运行、MeloTTS自动启动 ✅
- [•] **统一事件日志系统**：所有模块通过事件系统统一输出日志，精确记录时间，输出详细耗时分析 ✅

### 待实现功能
- [ ] **CosyVoice引擎集成**：CosyVoice TTS引擎集成
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

**最后更新**: 2026-01-07
**文档版本**: 2.0.0
**维护者**: ngyygm
