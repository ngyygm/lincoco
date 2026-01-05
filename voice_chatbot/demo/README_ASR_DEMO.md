# FunASR 流式语音识别 Demo 使用说明

## 简介

这个演示程序展示了如何使用 `FunASRStream` 类进行实时语音识别。它会：
1. 从麦克风实时录制音频
2. 进行流式语音识别
3. 在控制台实时输出识别结果（部分结果和最终结果）

## 安装依赖

### 1. 安装基础依赖

```bash
pip install sounddevice portaudio
```

**注意：** `portaudio` 是 `sounddevice` 的系统依赖。

### Windows 系统
```bash
pip install sounddevice
pip install pyaudio  # 可选，作为备选方案
```

### Linux 系统
```bash
sudo apt-get install libportaudio2
pip install sounddevice
```

### macOS 系统
```bash
brew install portaudio
pip install sounddevice
```

### 2. 确保已安装 FunASR

```bash
pip install funasr
```

## 运行 Demo

### 方式1：直接运行 Python 文件

```bash
python d:\APP\Lincoco\voice_chatbot\demo\asr_stream_demo.py
```

### 方式2：使用 Python 模块方式

```bash
cd d:\APP\Lincoco\voice_chatbot
python -m demo.asr_stream_demo
```

## 使用方法

启动程序后，按照提示操作：

1. **按回车键** - 开始录音和识别
2. **说话** - 对着麦克风说话，识别结果会实时显示
3. **停止说话** - 稍等片刻，程序会自动检测语音结束
4. **查看结果** - 最终识别结果会显示在控制台
5. **重复** - 按回车键开始下一次识别
6. **输入 'q'** - 退出程序

## 控制台输出说明

### 实时识别
```
[实时] 你好 今天 (置信度: 0.95)
```
- `[实时]` - 标识这是流式识别的中间结果
- 显示当前已识别的文本
- 置信度（如果可用）

### 最终结果
```
[最终] 你好今天天气怎么样 (置信度: 0.97)
------------------------------------------------------------
```
- `[最终]` - 标识这是识别的最终结果
- 显示完整的识别文本
- 置信度（如果可用）

### 调试日志（可选）
如果想查看详细的ASR日志，可以取消注释 `asr_stream_demo.py` 中的日志输出部分。

## 配置说明

可以在 `SimpleConfig` 类中修改配置参数：

```python
self.config = {
    "audio.sample_rate": 16000,          # 音频采样率
    "asr.model_path": "",                # ASR模型路径（空字符串使用默认模型）
    "asr.chunk_size": [0, 10, 5],        # 流式识别chunk大小
    "asr.encoder_chunk_look_back": 4,    # 编码器回看
    "asr.decoder_chunk_look_back": 1,    # 解码器回看
    "asr.pre_buffer_seconds": 0.5,       # VAD前预缓存时长
    "asr.language": "zh",                 # 语言设置
    "asr.enable_deduplication": True,    # 是否启用去重
    "asr.audio_queue_maxsize": 100,      # 音频队列最大大小
    "asr.preload_model": True,           # 是否预加载模型
}
```

## 使用自定义模型

如果要使用自定义的 FunASR 模型，修改配置：

```python
"asr.model_path": "path/to/your/model"
```

或者直接在程序启动时指定：

```python
self.config.config["asr.model_path"] = "/path/to/model"
```

## 故障排除

### 问题：找不到麦克风
- 检查麦克风是否已连接
- 检查系统音量设置
- 尝试运行以下命令列出音频设备：

```python
import sounddevice as sd
print(sd.query_devices())
```

### 问题：声音device初始化失败
- Windows: 安装 PortAudio
  ```bash
  # 可以尝试下载预编译的 PortAudio 库
  ```
- Linux: 安装 libportaudio2
  ```bash
  sudo apt-get install libportaudio2
  ```

### 问题：识别结果不准确
- 确保环境安静，减少背景噪音
- 调整麦克风距离和位置
- 检查采样率配置（默认16000Hz）

### 问题：模型加载慢
- 设置 `"asr.preload_model": False` 禁用预加载
- 或者使用本地模型而不是网络下载的模型

## 核心功能说明

### 1. 音频录制
使用 `sounddevice` 库进行实时音频采集，采样率16000Hz，单声道。

### 2. 流式识别
调用 `FunASRStream` 进行实时识别，支持：
- 实时部分结果输出
- 最终结果回调
- ASR去重（可选）

### 3. 事件系统
使用 `EventBus` 进行事件通信：
- `ASR_PARTIAL_RESULT` - 部分识别结果
- `ASR_FINAL_RESULT` - 最终识别结果
- `ASR_RECOGNITION_START` - 识别开始
- `ASR_RECOGNITION_END` - 识别结束

### 4. 线程管理
- 音频录制线程
- 音频输入线程
- ASR识别线程

## 扩展建议

### 集成到自己的项目

```python
from voice_chatbot.audio.fun_asr_stream import FunASRStream
from voice_chatbot.dialogue.events import EventBus, create_event

# 创建配置
config = SimpleConfig()

# 创建事件总线
event_bus = EventBus()

# 创建ASR识别器
asr = FunASRStream(
    config=config,
    on_partial_result=lambda text, conf: print(f"[实时] {text}"),
    on_final_result=lambda text, conf: print(f"[最终] {text}"),
    event_bus=event_bus
)

# 开始识别
asr.start_recognition()

# 喂入音频数据
asr.feed_audio(audio_data)

# 等待结果
result = asr.wait_for_result(timeout=30)
```

## 技术支持

如有问题，请查看：
1. `fun_asr_stream.py` 源代码注释
2. FunASR 官方文档：https://github.com/alibaba-damo-academy/FunASR
3. sounddevice 文档：https://python-sounddevice.readthedocs.io/

