"""
FunASR流式语音识别Demo
实时从麦克风录音并输出识别结果
"""

import sys
import os
import time
import threading
import numpy as np
import queue

# 添加父目录到路径，以便导入voice_chatbot模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice_chatbot.audio.fun_asr_stream import FunASRStream
from voice_chatbot.dialogue.events import EventType, EventBus, create_event

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False
    print("警告: sounddevice未安装，请运行: pip install sounddevice")
    print("或者使用: pip install sounddevice portaudio")


class SimpleEventBus(EventBus):
    """简单的事件总线实现（仅用于demo）"""
    
    def __init__(self):
        self.subscribers = {}
        self.lock = threading.Lock()
    
    def subscribe(self, event_type: EventType, callback):
        """订阅事件"""
        with self.lock:
            if event_type not in self.subscribers:
                self.subscribers[event_type] = []
            self.subscribers[event_type].append(callback)
    
    def publish(self, event):
        """发布事件"""
        with self.lock:
            event_type = event.type
            if event_type in self.subscribers:
                for callback in self.subscribers[event_type]:
                    try:
                        callback(event)
                    except Exception as e:
                        print(f"[ERROR] 事件回调错误: {e}")


class SimpleConfig:
    """简单的配置对象（用于demo）"""
    
    def __init__(self):
        # ASR配置
        self.config = {
            "audio.sample_rate": 16000,
            "asr.model_path": "",  # 留空使用默认模型
            "asr.chunk_size": [0, 10, 5],
            "asr.encoder_chunk_look_back": 4,
            "asr.decoder_chunk_look_back": 1,
            "asr.pre_buffer_seconds": 0.5,
            "asr.language": "zh",
            "asr.enable_deduplication": True,
            "asr.audio_queue_maxsize": 100,
            "asr.preload_model": True,
        }
    
    def get(self, key: str, default=None):
        """获取配置值"""
        return self.config.get(key, default)


class ASRStreamDemo:
    """FunASR流式识别Demo"""
    
    def __init__(self):
        self.config = SimpleConfig()
        self.event_bus = SimpleEventBus()
        
        # 音频相关
        self.sample_rate = self.config.get("audio.sample_rate", 16000)
        self.chunk_size = int(self.sample_rate * 0.1)  # 100ms
        self.audio_queue = queue.Queue()
        self.recording = False
        self.recording_thread = None
        
        # 创建ASR识别器
        self.asr = FunASRStream(
            config=self.config,
            on_partial_result=self.on_partial_result,
            on_final_result=self.on_final_result,
            event_bus=self.event_bus
        )
        
        # 订阅事件（用于显示调试信息）
        self.event_bus.subscribe(EventType.LOG_DEBUG, self.on_log_event)
        self.event_bus.subscribe(EventType.LOG_INFO, self.on_log_event)
        self.event_bus.subscribe(EventType.LOG_WARN, self.on_log_event)
        self.event_bus.subscribe(EventType.LOG_ERROR, self.on_log_event)
        
        # 控制标志
        self.running = False
    
    def on_partial_result(self, text: str, confidence: float = None):
        """部分识别结果回调（实时输出）"""
        if text:
            conf_str = f" (置信度: {confidence:.2f})" if confidence is not None else ""
            print(f"\r[实时] {text}{conf_str}", end='', flush=True)
    
    def on_final_result(self, text: str, confidence: float = None):
        """最终识别结果回调"""
        print()  # 换行
        conf_str = f" (置信度: {confidence:.2f})" if confidence is not None else ""
        print(f"[最终] {text}{conf_str}")
        print("-" * 60)
    
    def on_log_event(self, event):
        """日志事件回调（可选，用于调试）"""
        # 如果想查看详细日志，取消下面的注释
        # level = event.type.name.replace("LOG_", "")
        # category = event.data.get("category", "")
        # message = event.data.get("message", "")
        # indent = event.data.get("indent", 0)
        # indent_str = "  " * indent
        # print(f"[{level}] [{category}]{indent_str}{message}")
        pass
    
    def audio_callback(self, indata, frames, time_info, status):
        """音频录制回调函数"""
        if status:
            print(f"[WARNING] 音频状态: {status}")
        
        # 转换为float32格式
        audio_float32 = indata.astype(np.float32)
        
        # 将音频数据放入队列
        if self.recording:
            self.audio_queue.put(audio_float32)
    
    def audio_feed_thread_func(self):
        """音频输入线程函数"""
        while self.recording:
            try:
                # 从队列获取音频并喂给ASR
                audio_chunk = self.audio_queue.get(timeout=0.1)
                self.asr.feed_audio(audio_chunk)
            except queue.Empty:
                continue
    
    def start_recording(self):
        """开始录音"""
        if not HAS_SOUNDDEVICE:
            print("[ERROR] sounddevice未安装，无法进行录音")
            return False
        
        if self.recording:
            print("[WARNING] 已经在录音中")
            return False
        
        print(f"[INFO] 开始录音 (采样率: {self.sample_rate}Hz)")
        self.recording = True
        
        # 启动音频输入流
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.float32,
                callback=self.audio_callback
            )
            self.stream.start()
            
            # 启动音频输入线程
            self.recording_thread = threading.Thread(
                target=self.audio_feed_thread_func,
                daemon=True
            )
            self.recording_thread.start()
            
            return True
        except Exception as e:
            print(f"[ERROR] 启动录音失败: {e}")
            self.recording = False
            return False
    
    def stop_recording(self):
        """停止录音"""
        if not self.recording:
            return
        
        print("\n[INFO] 停止录音...")
        self.recording = False
        
        # 停止音频流
        if hasattr(self, 'stream'):
            self.stream.stop()
            self.stream.close()
    
    def run(self):
        """运行demo"""
        print("=" * 60)
        print("FunASR流式语音识别Demo")
        print("=" * 60)
        print("\n使用说明:")
        print("  1. 按回车键开始录音")
        print("  2. 开始说话...")
        print("  3. 停止说话后自动结束识别")
        print("  4. 按回车键开始下一次识别")
        print("  5. 输入 'q' 退出")
        print("\n注意: 请确保麦克风已连接")
        print("=" * 60)
        
        if not HAS_SOUNDDEVICE:
            print("\n[错误] sounddevice未安装，请运行:")
            print("  pip install sounddevice portaudio")
            return
        
        self.running = True
        
        while self.running:
            try:
                user_input = input("\n按回车开始识别，或输入 'q' 退出: ").strip()
                
                if user_input.lower() == 'q':
                    print("[INFO] 退出Demo")
                    break
                
                # 开始录音
                if not self.start_recording():
                    continue
                
                # 启动ASR识别
                if not self.asr.start_recognition():
                    print("[ERROR] 启动ASR识别失败")
                    self.stop_recording()
                    continue
                
                print("[INFO] 正在聆听，请说话...")
                
                # 等待识别完成
                result = self.asr.wait_for_result(timeout=30)
                
                # 停止录音
                self.stop_recording()
                
                if result:
                    print(f"\n[识别结果] {result}")
                else:
                    print("\n[识别结果] （空结果）")
                
            except KeyboardInterrupt:
                print("\n[INFO] 用户中断")
                break
            except Exception as e:
                print(f"\n[ERROR] 发生错误: {e}")
                import traceback
                traceback.print_exc()
        
        # 清理资源
        self.cleanup()
        print("[INFO] Demo结束")
    
    def cleanup(self):
        """清理资源"""
        self.running = False
        self.stop_recording()
        
        # 清空音频队列
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except:
                pass


def main():
    """主函数"""
    demo = ASRStreamDemo()
    demo.run()


if __name__ == "__main__":
    main()

