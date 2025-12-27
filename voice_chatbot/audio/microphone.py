"""
麦克风采集模块

提供音频采集、实时流处理、回调机制、多设备支持、格式转换和回声消除功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import threading
import time
from typing import Optional, Callable, List, Dict, Any
from collections import deque

import pyaudio
import numpy as np

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class MicrophoneRecorder:
    """麦克风采集器"""
    
    def __init__(self, config, on_audio_data_callback: Optional[Callable[[np.ndarray], None]] = None):
        """
        初始化麦克风采集器
        
        Args:
            config: 配置对象（Config实例）
            on_audio_data_callback: 音频数据回调函数，接收float32格式的numpy数组
        """
        self.config = config
        self.on_audio_data_callback = on_audio_data_callback
        
        # 从配置获取音频参数
        self.sample_rate = config.get("audio.sample_rate", 16000)
        self.chunk_size = config.get("audio.chunk_size", 1024)
        self.channels = config.get("audio.channels", 1)
        
        # 从配置获取回声消除参数
        self.enable_echo_cancellation = config.get("audio_filter.enable_echo_cancellation", True)
        self.echo_cancellation_strength = config.get("audio_filter.echo_cancellation_strength", 0.8)
        self.playback_reference_delay = config.get("audio_filter.playback_reference_delay", 0)
        
        # PyAudio实例和流
        self.pyaudio_instance: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        self.device_index: Optional[int] = None  # None表示使用默认设备
        
        # 状态控制
        self.recording = False
        self.recording_thread: Optional[threading.Thread] = None
        self.recording_lock = threading.Lock()
        
        # 回声消除相关
        self.playback_reference_buffer: deque = deque(maxlen=int(self.sample_rate * 2))  # 最多保存2秒的参考信号
        self.playback_reference_lock = threading.Lock()
        
        log_info("MIC", "麦克风采集器初始化完成", indent=0)
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """
        列出所有可用的音频输入设备
        
        Returns:
            设备信息列表，每个元素包含：
            - index: 设备索引
            - name: 设备名称
            - channels: 最大输入通道数
            - sample_rate: 默认采样率
        """
        devices = []
        try:
            p = pyaudio.PyAudio()
            device_count = p.get_device_count()
            
            for i in range(device_count):
                try:
                    info = p.get_device_info_by_index(i)
                    if info['maxInputChannels'] > 0:  # 只列出输入设备
                        devices.append({
                            'index': i,
                            'name': info['name'],
                            'channels': info['maxInputChannels'],
                            'sample_rate': int(info['defaultSampleRate']),
                            'host_api': info['hostApi']
                        })
                except Exception as e:
                    log_debug("MIC", f"获取设备 {i} 信息失败: {e}", indent=1)
                    continue
            
            p.terminate()
        except Exception as e:
            log_error("MIC", f"列出设备失败: {e}", indent=0)
        
        return devices
    
    def get_device_info(self, device_index: int) -> Optional[Dict[str, Any]]:
        """
        获取指定设备的信息
        
        Args:
            device_index: 设备索引
            
        Returns:
            设备信息字典，如果设备不存在则返回None
        """
        try:
            p = pyaudio.PyAudio()
            info = p.get_device_info_by_index(device_index)
            p.terminate()
            
            if info['maxInputChannels'] > 0:
                return {
                    'index': device_index,
                    'name': info['name'],
                    'channels': info['maxInputChannels'],
                    'sample_rate': int(info['defaultSampleRate']),
                    'host_api': info['hostApi']
                }
        except Exception as e:
            log_error("MIC", f"获取设备 {device_index} 信息失败: {e}", indent=0)
        
        return None
    
    def set_device(self, device_index: int) -> bool:
        """
        设置使用的输入设备
        
        Args:
            device_index: 设备索引，如果为None则使用默认设备
            
        Returns:
            True表示设置成功，False表示失败
        """
        if self.recording:
            log_warn("MIC", "正在录音中，无法切换设备", indent=0)
            return False
        
        # 验证设备是否存在
        device_info = self.get_device_info(device_index)
        if device_info is None:
            log_error("MIC", f"设备 {device_index} 不存在或不是输入设备", indent=0)
            return False
        
        self.device_index = device_index
        log_info("MIC", f"设置输入设备: {device_info['name']} (索引: {device_index})", indent=0)
        return True
    
    def get_current_device(self) -> Optional[Dict[str, Any]]:
        """
        获取当前使用的设备信息
        
        Returns:
            设备信息字典，如果使用默认设备则返回None
        """
        if self.device_index is None:
            return None
        
        return self.get_device_info(self.device_index)
    
    def set_playback_reference(self, audio_data: np.ndarray):
        """
        设置播放参考信号（用于回声消除）
        
        Args:
            audio_data: 播放的音频数据（float32格式，1D数组）
        """
        if not self.enable_echo_cancellation:
            return
        
        with self.playback_reference_lock:
            # 将音频数据添加到缓冲区（带延迟补偿）
            # 延迟补偿：根据配置的延迟参数，可能需要调整数据的位置
            if len(audio_data) > 0:
                # 如果配置了延迟，可以在这里处理
                # 简单实现：直接添加到缓冲区
                self.playback_reference_buffer.extend(audio_data)
                log_debug("MIC", f"更新播放参考信号，缓冲区长度: {len(self.playback_reference_buffer)}", indent=2)
    
    def _apply_echo_cancellation(self, mic_data: np.ndarray) -> np.ndarray:
        """
        应用回声消除算法
        
        Args:
            mic_data: 麦克风输入的音频数据（float32格式）
            
        Returns:
            处理后的音频数据（float32格式）
        """
        if not self.enable_echo_cancellation:
            return mic_data
        
        with self.playback_reference_lock:
            if len(self.playback_reference_buffer) == 0:
                # 没有参考信号，直接返回原始数据
                return mic_data
            
            # 获取参考信号（取与mic_data相同长度的数据）
            reference_length = len(mic_data)
            if len(self.playback_reference_buffer) < reference_length:
                # 参考信号不足，直接返回原始数据
                return mic_data
            
            # 从缓冲区末尾取数据（最新的参考信号）
            reference_data = np.array(list(self.playback_reference_buffer)[-reference_length:])
            
            # 简单的相关性检测和抑制算法
            # 计算相关性（使用归一化互相关）
            mic_normalized = mic_data / (np.linalg.norm(mic_data) + 1e-10)
            ref_normalized = reference_data / (np.linalg.norm(reference_data) + 1e-10)
            correlation = np.dot(mic_normalized, ref_normalized)
            
            # 如果相关性高，说明可能是回声
            if correlation > 0.3:  # 相关性阈值
                # 计算抑制系数
                suppression_factor = self.echo_cancellation_strength * correlation
                
                # 抑制回声：从麦克风输入中减去参考信号的一部分
                filtered_data = mic_data - reference_data * suppression_factor
                
                # 限制幅度，避免过度抑制
                filtered_data = np.clip(filtered_data, -1.0, 1.0)
                
                log_debug("MIC", f"回声消除: 相关性={correlation:.3f}, 抑制系数={suppression_factor:.3f}", indent=2)
                return filtered_data
            else:
                # 相关性低，不是回声，返回原始数据
                return mic_data
    
    def _convert_to_float32(self, audio_data: bytes) -> np.ndarray:
        """
        将int16格式的音频数据转换为float32格式
        
        Args:
            audio_data: int16格式的音频数据（bytes）
            
        Returns:
            float32格式的numpy数组
        """
        # 从bytes转换为int16数组
        audio_int16 = np.frombuffer(audio_data, dtype=np.int16)
        # 转换为float32并归一化到[-1.0, 1.0]
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        return audio_float32
    
    def _recording_thread_func(self):
        """录音线程函数"""
        try:
            self.pyaudio_instance = pyaudio.PyAudio()
            
            # 打开音频流
            self.stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                input_device_index=self.device_index,
                stream_callback=None
            )
            
            log_info("MIC", "音频流已打开，开始采集", indent=1)
            
            while self.recording:
                try:
                    # 读取音频数据（使用非阻塞读取以便快速响应停止信号）
                    # 注意：stream.read()是阻塞的，但我们可以通过检查stream.is_active()
                    # 以及在异常处理中捕获停止时的错误来优雅退出
                    if not self.stream.is_active():
                        break
                    
                    audio_data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                    
                    # 转换为float32格式
                    audio_float32 = self._convert_to_float32(audio_data)
                    
                    # 应用回声消除
                    audio_float32 = self._apply_echo_cancellation(audio_float32)
                    
                    # 调用回调函数
                    if self.on_audio_data_callback:
                        try:
                            self.on_audio_data_callback(audio_float32)
                        except Exception as e:
                            log_error("MIC", f"音频数据回调函数错误: {e}", indent=2)
                
                except OSError as e:
                    # 流被关闭时会抛出OSError，这是正常的停止流程
                    if self.recording:
                        log_debug("MIC", f"音频流已关闭: {e}", indent=1)
                    break
                except Exception as e:
                    if self.recording:  # 只有在还在录音时才记录错误
                        log_error("MIC", f"读取音频数据错误: {e}", indent=1)
                    break
            
        except Exception as e:
            log_error("MIC", f"录音线程错误: {e}", indent=0)
        finally:
            # 清理资源
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except:
                    pass
                self.stream = None
            
            if self.pyaudio_instance:
                try:
                    self.pyaudio_instance.terminate()
                except:
                    pass
                self.pyaudio_instance = None
            
            log_info("MIC", "录音线程结束", indent=1)
    
    def start(self) -> bool:
        """
        开始录音
        
        Returns:
            True表示启动成功，False表示失败
        """
        with self.recording_lock:
            if self.recording:
                log_warn("MIC", "已经在录音中", indent=0)
                return False
            
            self.recording = True
            self.recording_thread = threading.Thread(target=self._recording_thread_func, daemon=True, name="MicrophoneRecorder")
            self.recording_thread.start()
            
            log_info("MIC", "开始录音", indent=0)
            return True
    
    def stop(self):
        """停止录音"""
        with self.recording_lock:
            if not self.recording:
                return
            
            self.recording = False
        
        # 主动关闭音频流以解除read()的阻塞（在锁外执行，避免死锁）
        if self.stream:
            try:
                self.stream.stop_stream()
            except:
                pass
        
        # 等待录音线程结束
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2.0)
        
        log_info("MIC", "停止录音", indent=0)
    
    def is_recording(self) -> bool:
        """
        检查是否正在录音
        
        Returns:
            True表示正在录音，False表示未录音
        """
        with self.recording_lock:
            return self.recording

