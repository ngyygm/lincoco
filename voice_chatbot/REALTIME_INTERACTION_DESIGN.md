# 实时语音交互新架构设计文档

## 目录
1. [架构总览](#架构总览)
2. [核心设计要点](#核心设计要点)
3. [时间控制系统](#时间控制系统)
4. [打断处理逻辑](#打断处理逻辑)
5. [ASR唤醒机制](#asr唤醒机制)
6. [代码实现说明](#代码实现说明)
7. [配置参数](#配置参数)

---

## 架构总览

### 系统流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                    实时语音交互系统                              │
└─────────────────────────────────────────────────────────────────┘

┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  麦克风采集   │───▶│   VAD检测     │───▶│  ASR识别     │
│ (线程1)      │    │  (线程2)      │    │  (常驻运行)   │
└──────────────┘    └──────────────┘    └──────────────┘
      │                  │                  │
      │                  │                  │
      │              [语音开始]          [识别结果]
      │                  │                  │
      ▼                  ▼                  ▼
      │            ┌─────────────┐    ┌─────────────┐
      └───────────▶│  Context    │◀───│  ASR监控    │
                  │  时间控制    │    │  (超时唤醒)  │
                  └─────────────┘    └─────────────┘
                         │
                         │ [时间段内文本]
                         ▼
                  ┌─────────────┐
                  │   LLM生成   │
                  │  (线程7)    │
                  └─────────────┘
                         │
                         ▼
                  ┌─────────────┐
                  │   TTS合成   │
                  │  (线程8)    │
                  └─────────────┘
                         │
                         ▼
                  ┌─────────────┐
                  │  音频播放   │
                  │  (线程9)    │
                  └─────────────┘
                         │
                         ▼
                  [播放进度/打断检测]
```

### 状态机

```
主系统状态: IDLE <-> LISTENING <-> ROBOT_SPEAKING
副系统状态: NONE <-> PROCESSING <-> SPEAKING <-> PAUSED

状态转换:
1. IDLE + VAD语音开始 → LISTENING
2. LISTENING + ASR超时唤醒 → IDLE → PROCESSING (LLM+TTS启动)
3. PROCESSING + TTS播放开始 → SPEAKING
4. SPEAKING + VAD/ASR打断 → LISTENING (暂停/取消)
5. PAUSED + ASR验证确认 → LISTENING/PROCESSING (恢复/重启)
```

---

## 核心设计要点

### 0. 打断机制

**VAD打断能力:**
- VAD检测到语音开始时,可以打断LLM+TTS进程
- 使用延迟验证机制(0.5秒)避免误判

**ASR打断能力:**
- ASR识别到新内容时,可以打断LLM+TTS进程
  - 识别到"真实内容"(不在asr.invalid_recognition_words中)才触发打断

**ASR唤醒机制:**
- ASR每次输出结果后,更新`last_asr_update_time`
- ASR监控线程每隔0.1秒检查:
  - 如果距离上次更新超过`asr.no_speech_threshold`(0.8秒)
  - 则唤醒LLM+TTS进程

---

### 1. Context时间控制系统

#### 时间范围控制

```
时间轴:
─────────────────────────────────────────────────────────────────>
  ↓
  text_start_time = 0 (初始状态)
  ↓
  [VAD语音开始] ──→ [ASR首次输出] ──→ [持续累积] ──→ [ASR超时] ──→ 唤醒LLM+TTS
       │                   │               │                │
       ↓                   ↓               ↓                ↓
  如果=0则设置         如果=0则设置     更新end_time    提取[开始,结束]
  =当前VAD时间          =当前ASR时间    =当前ASR时间    时间段内文本
  -偏移量               -偏移量
```

#### 时间设置规则

```python
# 规则2: 文本开始时间设置
if text_start_time == 0:
    # 触发条件1: VAD检测到语音开始
    if VAD检测到语音开始:
        text_start_time = VAD时间 - vad_start_time_offset (0.5秒)
        log_info("VAD检测到语音,设置text_start_time")
    
    # 触发条件2: ASR首次输出识别结果
    elif ASR首次输出识别结果:
        text_start_time = ASR结果时间 - asr_output_time_offset (0.5秒)
        log_info("ASR首次输出,设置text_start_time")

# 规则2续: TTS播放进入"已经说了"阶段后重置
if TTS播放时长 >= min_play_duration_to_record (1.3秒):
    text_start_time = 0
    log_info("播放时长达到阈值,重置text_start_time为0")
    # 清理已使用的ASR结果
    asr.clear_results_before_time(current_time)

# 规则3: 文本结束时间设置
# ASR识别队列每有新结果 → 立即更新
text_end_time = 当前ASR结果时间
log_info("ASR输出新内容,更新text_end_time")
```

#### Context内容拼接

```python
def _prepare_user_input_with_time_range(context):
    """
    基于时间范围准备用户输入
    
    提取 [text_start_time, text_end_time] 时间段内的所有文本内容
    """
    start_time = context.text_start_time
    end_time = context.text_end_time
    
    # 时间范围有效性检查
    if start_time <= 0 or end_time <= 0 or start_time >= end_time:
        log_debug("无效的时间范围,返回空输入")
        return ""
    
    # 从ASR获取时间段内识别结果
    if self.asr and hasattr(self.asr, 'get_results_in_time_range'):
        recognized_text = self.asr.get_results_in_time_range(start_time, end_time)
        log_info(f"时间范围[{start_time:.3f}, {end_time:.3f}]内识别结果: {recognized_text}")
        
        # 清理已使用的ASR结果
        if self.asr and hasattr(self.asr, 'clear_results_before_time'):
            self.asr.clear_results_before_time(end_time)
            remaining_count = len(self.asr.timed_results)
            log_info(f"清理时间{end_time:.3f}之前的ASR结果,剩余{remaining_count}条")
        
        return recognized_text
    else:
        log_warn("ASR模块不支持时间段提取,返回空输入")
        return ""
```

---

### 2. 打断处理逻辑

#### 阶段A: 判断是否为真实内容

```python
# 在 _on_asr_partial_result() 中实现
new_text = event_data.get("new_text", "")
is_real_content = new_text.strip() not in [w.strip() for w in asr.invalid_recognition_words]

if not is_real_content:
    log_debug("ASR识别到无效内容,忽略打断")
    return  # 不是真实内容,忽略
```

#### 阶段i: "什么都没说"阶段

```python
if play_duration < min_play_duration_to_record (1.3秒):
    # 什么都没说阶段: 播放时长 < 1.3秒
    log_info(f"打断: 什么都没说阶段 (播放时长{play_duration:.2f}秒 < 阈值),直接取消任务")
    
    # 更新text_end_time
    context.set_text_end_time(current_time)
    
    # 直接取消任务,不记录历史
    cancel_current_context()
```

#### 阶段ii: "已经说了"阶段

```python
else:
    # 已经说了阶段: 播放时长 >= 1.3秒
    log_info(f"打断: 已经说了{play_duration:.2f}秒,保存已播放内容")
    
    # 更新text_end_time
    context.set_text_end_time(current_time)
    
    # 从AudioPlayer获取已播放的文本长度/内容
    played_text = context.get_extra_data("played_response_text", "")
    if played_text:
        # 将已播放内容截断并保存到对话历史
        with history_lock:
            conversation_history.append({"role": "assistant", "content": played_text})
            _limit_history_length()
        log_info(f"已保存已播放内容到对话历史: '{played_text[:50]}...'")
    
    # 重置text_start_time = 0,清理ASR结果
    context.reset_text_time_range()
    log_info("打断: 已说阶段,重置text_start_time为0")
    if self.asr and hasattr(self.asr, 'clear_results_before_time'):
        self.asr.clear_results_before_time(current_time)
        remaining_count = len(self.asr.timed_results)
        log_info(f"清理时间{current_time:.3f}之前的ASR结果,剩余{remaining_count}条")
    
    # 取消任务
    cancel_current_context()
```

---

### 3. ASR唤醒机制

#### ASR监控线程

```python
def asr_monitor_thread():
    """ASR结果监控线程（监控超时并触发唤醒）"""
    while running:
        try:
            # 检查当前context
            current_context = context_manager.get_current_context()
            if current_context and current_context.is_valid:
                # 计算距离上次ASR更新的时间
                with asr_monitor_lock:
                    time_since_last_update = time.time() - last_asr_update_time

                # 如果距离上次更新超过asr.no_speech_threshold,且text_end_time已设置
                if (time_since_last_update > asr.no_speech_threshold and
                    context.text_end_time > 0):
                    # 检查是否需要唤醒LLM+TTS
                    _check_and_wakeup_llm(current_context)
            
            # 短暂休眠
            time.sleep(0.1)
        except Exception as e:
            log_error(f"ASR监控线程错误: {e}")
            time.sleep(0.1)
```

#### 唤醒检查逻辑

```python
def _check_and_wakeup_llm(context):
    """
    检查是否需要唤醒LLM+TTS流程
    """
    # 检查是否有有效的时间范围
    if context.text_start_time > 0 and context.text_end_time > 0:
        # 准备用户输入
        user_input = _prepare_user_input_with_time_range(context)
        if user_input:
            log_info(f"ASR超时({asr.no_speech_threshold}秒),唤醒LLM+TTS,时间范围[{context.text_start_time:.3f}, {context.text_end_time:.3f}]")
            # 调用_process_user_input处理
            _process_user_input(user_input, is_merge=False)
        else:
            log_debug("ASR超时但无有效输入,忽略唤醒")
```

---

## 代码实现说明

### 关键函数调用关系

```
麦克风采集线程:
  _on_audio_data()
    ├── VAD.feed_audio()
    ├── ASR.feed_audio()
    └── 检查NO_SPEECH_THRESHOLD触发ASR停止

VAD后台线程:
  _on_vad_speech_start()
    ├── 更新last_active_time
    ├── 设置context.text_start_time (如果为0)
    └── 如果LLM+TTS运行中,启动延迟验证

ASR识别线程 (常驻运行):
  generate() → 识别结果
    └── 发布ASR_PARTIAL_RESULT事件

ASR监控线程 (持续运行):
  检查last_asr_update_time超时
    └── _check_and_wakeup_llm()
        └── _prepare_user_input_with_time_range()
            └── ASR.get_results_in_time_range()

事件总线处理线程:
  ASR_PARTIAL_RESULT事件
    └── _on_asr_partial_result()
        ├── 更新last_asr_update_time
        ├── 设置context.text_end_time
        ├── 设置context.text_start_time (如果为0)
        └── _handle_asr_interrupt()
            ├── 检查播放时长
            ├── 阶段i: 什么都没说 → 取消任务
            └── 阶段ii: 已经说了 → 保存历史+取消任务

  PLAYBACK_PROGRESS事件
    └── _on_playback_progress()
        ├── 更新played_response_text
        ├── 检查播放时长是否达到阈值
        │   └── 如果达到: 重置text_start_time=0,清理ASR结果
        └── 句子跟踪机制

LLM+TTS流程 (线程6):
  _process_user_input()
    └── _prepare_user_input_with_time_range()
    └── _start_inference_flow()
        ├── 启动LLM线程 (线程7)
        └── 启动TTS线程 (线程8)
            └── 启动播放线程 (线程9)
```

### 文件结构

```
voice_chatbot/
├── dialogue/
│   ├── manager.py              # 核心调度器 (实现打断、唤醒、时间控制)
│   ├── context_manager.py      # 上下文管理器
│   ├── task_context.py         # 任务上下文 (text_start_time, text_end_time)
│   ├── state_manager.py        # 状态管理器
│   └── events.py               # 事件定义
├── audio/
│   ├── microphone.py           # 麦克风采集
│   ├── webrtc_vad.py           # VAD检测
│   └── fun_asr_stream.py       # ASR识别 (常驻运行,支持时间段提取)
├── llm/
│   └── llm_openai_stream.py    # LLM流式生成
├── tts/
│   ├── audio_player.py         # 音频播放器
│   └── tts_file_cache.py       # TTS缓存
└── utils/
    └── config.py               # 配置管理
```

---

## 配置参数

### 对话参数 (dialogue)

```python
{
  "dialogue": {
    "min_play_duration_to_record": 1.3,     # 最小播放时长阈值(秒),低于此值不记录历史
    "audio_stop_delay": 0.5,                # 延迟验证时间(秒),VAD打断时的延迟验证
    "min_silent_duration": 0.8,             # 最小静音时长(秒),延迟验证期间静音检测
    "vad_start_time_offset": 0.5,           # VAD开始时间偏移(秒)
    "asr_output_time_offset": 0.5,          # ASR输出时间偏移(秒)
  }
}
```

### ASR参数 (asr)

```python
{
  "asr": {
    "chunk_size": [0, 10, 5],               # ASR chunk大小配置
    "pre_buffer_seconds": 0.5,              # 预缓存时长(秒)
    "result_time_buffer": 0.5,              # 结果时间缓冲(秒),时间段提取时的扩展
    "result_timeout": 30.0,                 # ASR结果等待超时(秒)
    "max_result_age": 60.0,                 # 结果最大保留时间(秒)
    "no_speech_threshold": 0.8,            # 无语音阈值(秒),用户停止说话后等待多久才认为说话结束并触发ASR停止
    "invalid_recognition_words": ["", "i", "嗯", "啊", "呃", "哦", "额"],  # 无效识别词列表,会被过滤
  }
}
```

### VAD参数 (vad)

```python
{
  "vad": {
    "mode": 3,                              # VAD模式(0-3),3最敏感
    "activation_rate": 0.25,                # VAD激活率(0.0-1.0)
    "check_interval": 0.2,                  # VAD检测间隔(秒)
    "thread_sleep": 0.01,                  # VAD线程休眠间隔(秒)
  }
}
```

---

## 状态转换说明

### 正常对话流程

```
1. 系统启动: IDLE
   ↓
2. 用户说话: IDLE + VAD语音开始 → LISTENING
   - VAD设置 text_start_time = VAD时间 - 0.5秒
   ↓
3. ASR识别: LISTENING + ASR首次输出
   - ASR设置 text_start_time = ASR时间 - 0.5秒 (如果为0)
   - ASR持续更新 text_end_time = 当前ASR时间
   ↓
4. ASR超时: LISTENING + 超过no_speech_threshold无新内容
   - ASR监控线程触发唤醒
   - 提取[text_start_time, text_end_time]时间段内文本
   - IDLE → PROCESSING (LLM+TTS启动)
   ↓
5. LLM生成: PROCESSING
   ↓
6. TTS播放: PROCESSING → SPEAKING
   ↓
7. 播放完成: SPEAKING → IDLE
   - 重置 text_start_time = 0
   - 清理ASR结果
```

### 打断流程 (什么都没说)

```
1. 机器人正在说话: ROBOT_SPEAKING / SPEAKING
   ↓
2. 用户打断: VAD检测到语音开始
   - 设置 pending_interrupt = True
   - 启动延迟验证(0.5秒)
   ↓
3. ASR输出新内容: 检查播放时长
   - 如果播放时长 < min_play_duration_to_record (1.3秒)
   - 判断为"什么都没说"阶段
   - 直接取消任务,不记录历史
   ↓
4. 重置状态: SPEAKING → LISTENING
   - text_start_time = 0
```

### 打断流程 (已经说了)

```
1. 机器人正在说话: ROBOT_SPEAKING / SPEAKING
   ↓
2. 用户打断: VAD检测到语音开始
   - 设置 pending_interrupt = True
   - 启动延迟验证(0.5秒)
   ↓
3. ASR输出新内容: 检查播放时长
   - 如果播放时长 >= min_play_duration_to_record (1.3秒)
   - 判断为"已经说了"阶段
   - 保存已播放内容到对话历史
   - 重置 text_start_time = 0
   - 清理ASR结果
   ↓
4. 重启对话: SPEAKING → LISTENING → PROCESSING
```

---

## 总结

### 核心特性

1. **时间范围控制**: 通过text_start_time和text_end_time精确控制传入LLM的文本内容
2. **双重打断机制**: VAD和ASR都可以打断LLM+TTS进程,提供更灵活的交互
3. **智能唤醒**: ASR监控线程基于asr.no_speech_threshold自动唤醒LLM+TTS
4. **分阶段打断**: 根据播放时长区分"什么都没说"和"已经说了"两个阶段,分别处理
5. **上下文管理**: 使用TaskContext隔离每个任务,确保任务间互不干扰

### 关键优势

1. **响应速度快**: ASR常驻运行,VAD和ASR实时响应
2. **打断准确**: 延迟验证机制+播放时长判断,避免误打断
3. **上下文完整**: 时间范围控制确保传入LLM的文本准确无误
4. **历史管理**: 智能记录已播放内容,保持对话连贯性
5. **可配置性强**: 所有阈值和时间参数均可通过配置文件调整

---

## 附录

### 事件类型

```python
class EventType(Enum):
    # 系统事件
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    
    # VAD事件
    VAD_SPEECH_START = "VAD_SPEECH_START"
    VAD_SPEECH_END = "VAD_SPEECH_END"
    VAD_SPEECH_ACTIVE = "VAD_SPEECH_ACTIVE"
    
    # ASR事件
    ASR_PARTIAL_RESULT = "ASR_PARTIAL_RESULT"
    ASR_FINAL_RESULT = "ASR_FINAL_RESULT"
    
    # 播放事件
    PLAYBACK_START = "PLAYBACK_START"
    PLAYBACK_PROGRESS = "PLAYBACK_PROGRESS"
    PLAYBACK_COMPLETE = "PLAYBACK_COMPLETE"
    PLAYBACK_ERROR = "PLAYBACK_ERROR"
    
    # 任务事件
    TASK_CREATED = "TASK_CREATED"
    TASK_CANCELLED = "TASK_CANCELLED"
    INTERRUPT_DETECTED = "INTERRUPT_DETECTED"
```

### 线程列表

```
线程1: 音频采集线程 (MicrophoneRecorder)
线程2: VAD后台线程 (WebRTCVAD)
线程3: 事件总线处理线程 (EventBus)
线程4: ASR识别线程 (FunASRStream, 常驻运行)
线程5: ASR监控线程 (asr_monitor_thread)
线程6: LLM+TTS处理线程 (inference_flow)
线程7: LLM流式输出线程 (llm_stream_thread)
线程8: TTS生成线程 (tts_thread)
线程9: 音频播放线程 (player_thread)
```

---

*文档版本: v1.0*
*更新日期: 2025-01-03*





