"""
基于 Gradio 的流式音频播放界面
调用 api_optimized.py 的接口实现：
1. /set_model - 设置参考音频
2. /generate - 非流式生成音频
3. /generate_stream - 实时流式生成音频
"""
import gradio as gr
import requests
import io
import torch
import torchaudio
import numpy as np
import json
import sys
import time


class StreamingAudioPlayer:
    """音频播放器 - 调用 API 的各种接口"""
    
    def __init__(self, api_url="http://localhost:9880"):
        self.api_url = api_url.rstrip('/')
        self.sample_rate = 24000  # CosyVoice2 使用 24000 Hz
        
    def check_api_health(self):
        """检查 API 服务是否可用"""
        try:
            response = requests.get(f"{self.api_url}/health", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def get_current_model(self):
        """获取当前模型配置"""
        try:
            response = requests.get(f"{self.api_url}/get_model", timeout=2)
            if response.status_code == 200:
                return response.json()
            return None
        except:
            return None
    
    def set_reference_audio(self, wav_path, reference_text=None):
        """设置参考音频"""
        data = {
            "wav_path": wav_path,
            "use_filename_as_text": True
        }
        if reference_text:
            data["reference_text"] = reference_text
            
        try:
            response = requests.post(
                f"{self.api_url}/set_model",
                json=data,
                timeout=10
            )
            return response.status_code == 200, response.json() if response.status_code != 200 else response.json()
        except Exception as e:
            return False, {"error": f"设置参考音频失败: {str(e)}"}
    
    def generate_audio(self, text, speed=1.0):
        """
        非流式生成音频
        
        Args:
            text: 要合成的文本
            speed: 语速
            
        Returns:
            tuple: (sample_rate, audio_data) 或 None
            str: 错误信息
        """
        data = {
            "text": text,
            "speed": speed
        }
        
        try:
            response = requests.post(
                f"{self.api_url}/generate",
                json=data,
                timeout=120
            )
            
            if response.status_code != 200:
                error_info = response.json() if 'application/json' in response.headers.get('Content-Type', '') else {}
                error_msg = error_info.get('error', f"API 返回错误状态码: {response.status_code}")
                return (self.sample_rate, np.zeros(self.sample_rate)), f"❌ {error_msg}"
            
            # 读取音频数据
            audio_buffer = io.BytesIO(response.content)
            waveform, sr = torchaudio.load(audio_buffer)
            
            # 转换为目标采样率
            if sr != self.sample_rate:
                resampler = torchaudio.transforms.Resample(sr, self.sample_rate)
                waveform = resampler(waveform)
            
            # 转换为 numpy 数组
            audio_data = waveform.numpy().flatten()
            return (self.sample_rate, audio_data), "✅ 音频生成完成"
            
        except requests.exceptions.Timeout:
            return (self.sample_rate, np.zeros(self.sample_rate)), "❌ 请求超时"
        except requests.exceptions.ConnectionError:
            return (self.sample_rate, np.zeros(self.sample_rate)), "❌ 无法连接到 API 服务"
        except Exception as e:
            return (self.sample_rate, np.zeros(self.sample_rate)), f"❌ 生成音频失败: {str(e)}"
    
    def generate_streaming_audio(self, text, speed=1.0):
        """
        流式生成音频 - 返回纯 PCM 数据（不含 WAV 头）
        
        Args:
            text: 要合成的文本
            speed: 语速
            
        Yields:
            tuple: (sample_rate, audio_data, status_msg)
        """
        data = {
            "text": text,
            "speed": speed
        }
        
        try:
            # 发送流式请求
            response = requests.post(
                f"{self.api_url}/generate_stream",
                json=data,
                stream=True,
                timeout=120
            )
            
            if response.status_code != 200:
                error_info = response.json() if 'application/json' in response.headers.get('Content-Type', '') else {}
                error_msg = error_info.get('error', f"API 返回错误状态码: {response.status_code}")
                yield (self.sample_rate, np.zeros(self.sample_rate)), f"❌ {error_msg}"
                return
            
            # 处理流式音频数据 - API 每次返回一个完整的 WAV 块
            buffer = bytearray()
            
            for chunk in response.iter_content(chunk_size=4096):
                if not chunk:
                    continue
                
                buffer.extend(chunk)
                
                # 检查是否有完整的 WAV 文件
                # 首先检查是否有 RIFF 头
                if len(buffer) < 12:
                    continue
                    
                if buffer[0:4] != b'RIFF' or buffer[8:12] != b'WAVE':
                    # 不是有效的 WAV 数据，可能是错误信息
                    try:
                        error_text = buffer.decode('utf-8', errors='ignore')
                        if 'Error:' in error_text:
                            yield (self.sample_rate, np.zeros(self.sample_rate)), f"❌ {error_text}"
                            return
                    except:
                        pass
                    buffer = bytearray()
                    continue
                
                # 尝试读取 WAV 文件大小（从 RIFF 头）
                if len(buffer) < 44:  # WAV 最小头大小
                    continue
                    
                try:
                    # 尝试解析并加载 WAV 数据
                    wav_io = io.BytesIO(buffer)
                    try:
                        waveform, sr = torchaudio.load(wav_io)
                        
                        # 转换为目标采样率
                        if sr != self.sample_rate:
                            resampler = torchaudio.transforms.Resample(sr, self.sample_rate)
                            waveform = resampler(waveform)
                        
                        # 转换为 numpy 数组并 yield
                        # 这里返回纯 PCM 数据，不含 WAV 头
                        audio_data = waveform.numpy().flatten()
                        yield audio_data, "🎵 正在播放..."
                        
                        # 清空缓冲区，处理下一个 WAV 块
                        buffer = bytearray()
                        
                    except Exception:
                        # 数据不完整，继续等待更多数据
                        pass
                        
                except Exception:
                    pass
                        
        except requests.exceptions.Timeout:
            yield np.zeros(self.sample_rate), "❌ 请求超时"
        except requests.exceptions.ConnectionError:
            yield np.zeros(self.sample_rate), "❌ 无法连接到 API 服务"
        except Exception as e:
            yield np.zeros(self.sample_rate), f"❌ 生成音频失败: {str(e)}"


def generate_audio_streaming(text, player, speed):
    """
    Gradio 流式生成音频函数
    
    Args:
        text: 要合成的文本
        player: StreamingAudioPlayer 实例
        speed: 语速
        
    Yields:
        tuple: (音频数据, 状态信息)
    """
    # 验证输入
    if not text or text.strip() == "":
        yield (24000, np.zeros(24000)), "⚠️ 请输入文本"
        return
    
    if not player.check_api_health():
        yield (24000, np.zeros(24000)), "❌ API 服务未运行，请先启动 api_optimized.py"
        return
    
    # 检查参考音频状态，但不强制要求（API 默认可能已有）
    model_info = player.get_current_model()
    if model_info and model_info.get('has_reference'):
        ref_info = f"参考音频: {model_info.get('wav_path', '未知')}"
    else:
        ref_info = "使用 API 默认音频或未设置参考音频"
    
    # 累积所有音频块
    accumulated_audio = []
    
    # 记录开始时间
    start_time = time.time()
    first_audio_time = None  # 记录第一个音频块的时间
    
    try:
        for audio_data, msg in player.generate_streaming_audio(text, speed):
            # audio_data 现在是纯 PCM 数据（numpy 数组），直接累积
            accumulated_audio.append(audio_data)
            
            # 记录第一个音频块的时间
            if first_audio_time is None:
                first_audio_time = time.time()
                time_to_first = first_audio_time - start_time
                time_str = f"⏱️ **首次响应**: {time_to_first:.2f}s"
                block_info = f"🎵 已生成 **{len(accumulated_audio)}** 个音频块"
                status_display = f"{block_info}\n{time_str}\n{ref_info}"
            else:
                # 计算当前总耗时
                current_time = time.time()
                total_time = current_time - start_time
                time_str = f"⏱️ **首次响应**: {(first_audio_time - start_time):.2f}s\n⏱️ **总耗时**: {total_time:.2f}s"
                block_info = f"🎵 已生成 **{len(accumulated_audio)}** 个音频块"
                status_display = f"{block_info}\n{time_str}"
            
            # 返回累积的完整音频和状态
            if accumulated_audio:
                # 拼接所有累积的音频块
                full_audio = np.concatenate(accumulated_audio)
                yield (24000, full_audio), status_display
            
    except Exception as e:
        yield (24000, np.zeros(24000)), f"❌ 生成失败: {str(e)}"


def generate_audio_normal(text, player, speed):
    """
    Gradio 非流式生成音频函数
    
    Args:
        text: 要合成的文本
        player: StreamingAudioPlayer 实例
        speed: 语速
        
    Returns:
        tuple: (音频数据, 状态信息)
    """
    if not text or text.strip() == "":
        return (24000, np.zeros(24000)), "⚠️ 请输入文本"
    
    if not player.check_api_health():
        return (24000, np.zeros(24000)), "❌ API 服务未运行，请先启动 api_optimized.py"
    
    # 检查参考音频状态，但不强制要求（API 默认可能已有）
    model_info = player.get_current_model()
    if model_info and model_info.get('has_reference'):
        ref_info = f"参考音频: {model_info.get('wav_path', '未知')}"
    else:
        ref_info = "使用 API 默认音频或未设置参考音频"
    
    # 记录开始时间
    start_time = time.time()
    
    # 非流式生成
    audio_data, status_msg = player.generate_audio(text, speed)
    
    # 计算总耗时
    total_time = time.time() - start_time
    
    # 格式化状态信息
    final_status = f"✅ {status_msg}\n\n⏱️ **总耗时**: {total_time:.2f}s\n\nℹ️ {ref_info}"
    
    return audio_data, final_status


def set_reference_audio_action(wav_path, player):
    """
    设置参考音频的动作
    
    Args:
        wav_path: 音频文件路径
        player: StreamingAudioPlayer 实例
        
    Returns:
        str: 状态信息
    """
    if not wav_path or wav_path.strip() == "" or wav_path == "请选择参考音频或者自己上传":
        return "⚠️ 请选择参考音频（留空则使用 API 默认音频）"
    
    success, result = player.set_reference_audio(wav_path)
    
    if success:
        model_info = player.get_current_model()
        ref_text = model_info.get('reference_text', '未知') if model_info else '未知'
        return f"✅ 参考音频已设置\n文件: {wav_path}\n文本: {ref_text}"
    else:
        error_msg = result.get('error', '未知错误')
        return f"❌ 设置失败: {error_msg}"


def create_ui(api_url="http://localhost:9880"):
    """创建 Gradio 界面"""
    
    # 初始化播放器
    player = StreamingAudioPlayer(api_url=api_url)
    
    # 定义界面
    with gr.Blocks(title="CosyVoice2 音频生成") as demo:
        gr.Markdown("# 🎙️ CosyVoice2 音频生成")
        gr.Markdown("### 基于 API 的流式/非流式音频生成")
        
        # 状态显示
        status_display = gr.Markdown("⚠️ 正在检查 API 服务状态...")
        
        with gr.Row():
            # 左侧：输入区域
            with gr.Column(scale=1):
                gr.Markdown("### 📝 输入设置")
                
                # 文本输入
                text_input = gr.Textbox(
                    label="输入要合成的文本",
                    lines=3,
                    value="hello hello~ [breath] 听得到吗？[breath] きこえていますか？[breath] 初次见面，请多关照呀！ [breath] 这里是嘉然Diana，大家也可以叫我<strong>蒂娜</strong> 是你们<strong>最</strong> 甜甜甜的小草莓~CosyVoice迎来全面升级，提供更准、更稳、更快、 更好的语音生成能力。CosyVoice is undergoing a comprehensive upgrade, providing more accurate, stable, faster, and better voice generation capabilities.",
                    placeholder="请输入要合成的文本..."
                )
                
                # 语速调节
                speed_slider = gr.Slider(
                    minimum=0.5,
                    maximum=2.0,
                    value=1.0,
                    step=0.1,
                    label="语速调节"
                )
                
        # 参考音频设置（可选但推荐）
        with gr.Accordion("🎵 参考音频设置（推荐设置）", open=False):
            wav_path_input = gr.Textbox(
                label="参考音频文件路径",
                placeholder="例如: /path/to/reference.wav，留空则使用 API 默认音频",
                value=""
            )
            
            set_reference_btn = gr.Button("📌 设置参考音频", variant="primary")
            reference_status = gr.Markdown("")
            reference_warning = gr.Markdown("⚠️ 注意：如果 API 未加载默认音频，您需要先设置参考音频才能生成语音。")
        
        # 生成按钮
        with gr.Row():
            generate_stream_btn = gr.Button("▶️ 流式生成", variant="primary", size="lg")
            generate_normal_btn = gr.Button("▶️ 非流式生成", variant="secondary", size="lg")
        
        # 右侧：输出区域
        with gr.Column(scale=1):
            gr.Markdown("### 🔊 音频输出")
            
            # 流式音频输出
            audio_output_stream = gr.Audio(
                label="流式音频（累积播放）",
                streaming=False,  # 改为 False，因为我们自己累积音频
                autoplay=True,
                show_label=True,
                visible=True,
                type="numpy"  # 使用 numpy 类型
            )
            
            # 非流式音频输出
            audio_output_normal = gr.Audio(
                label="完整音频",
                streaming=False,
                autoplay=True,
                show_label=True,
                visible=True
            )
            
            # 状态信息
            output_status = gr.Markdown("")
        
        # 模型信息显示
        model_info_display = gr.Markdown("ℹ️ 模型信息：加载中...")
        
        # 刷新按钮
        with gr.Row():
            refresh_btn = gr.Button("🔄 刷新状态")
        
        # 事件绑定
        def update_status():
            """更新状态显示"""
            api_status = "✅ API 服务运行中" if player.check_api_health() else "❌ API 服务未运行"
            model_info = player.get_current_model()
            
            # 格式化模型信息
            if model_info:
                model_text = json.dumps(model_info, ensure_ascii=False, indent=2)
                # 添加友好的参考音频状态说明
                if model_info.get('has_reference'):
                    ref_status = f"✅ 已加载参考音频\n文件: {model_info.get('wav_path', '未知')}\n文本: {model_info.get('reference_text', '未知')}"
                else:
                    ref_status = "⚠️ 未设置参考音频\n请在下方设置参考音频，或确保 API 已加载默认音频"
                model_text = f"{ref_status}\n\n```json\n{model_text}\n```"
            else:
                model_text = "⚠️ 无法获取模型信息"
            
            return api_status, model_text
        
        demo.load(fn=update_status, outputs=[status_display, model_info_display])
        
        refresh_btn.click(fn=update_status, outputs=[status_display, model_info_display])
        
        set_reference_btn.click(
            fn=lambda path: set_reference_audio_action(path, player),
            inputs=[wav_path_input],
            outputs=[reference_status]
        )
        
        # 直接在闭包中定义函数，确保 player 被正确捕获
        def stream_generator(text, speed):
            """流式生成包装函数 - 作为生成器使用"""
            yield from generate_audio_streaming(text, player, speed)
        
        def normal_generator(text, speed):
            """非流式生成包装函数"""
            return generate_audio_normal(text, player, speed)
        
        # 流式生成
        generate_stream_btn.click(
            fn=stream_generator,
            inputs=[text_input, speed_slider],
            outputs=[audio_output_stream, output_status]
        )
        
        # 非流式生成
        generate_normal_btn.click(
            fn=normal_generator,
            inputs=[text_input, speed_slider],
            outputs=[audio_output_normal, output_status]
        )
        
        # 添加使用说明
        gr.Markdown("""
        ---
        ### 📖 使用说明
        
        1. **启动 API 服务**
           - 首先运行 `python api_optimized_vllm.py --port 9880`
           - 确保 API 服务正常运行
        
        2. **生成模式选择**
           - **流式生成**：实时累积并播放已生成的音频，可以看到生成进度
           - **非流式生成**：完整生成后再播放，一次性获取完整音频
        
        3. **参考音频（可选）**
           - 可以手动设置参考音频文件路径
           - 如果不设置，使用 API 启动时加载的默认音频
           - 参考音频用于 3s 极速复刻
        
        4. **生成音频**
           - 在文本输入框中输入要合成的文本
           - 选择生成模式
           - 调整语速（可选）
           - 点击"生成音频"按钮
        
        ### ⏱️ 性能指标说明
        - **首次响应时间**（仅流式模式）：从点击生成到第一个音频块返回的时间，反映 API 的响应速度
        - **总耗时**：从点击生成到完成整个音频生成的时间
        - 流式模式的首次响应时间通常远短于非流式模式的总耗时
        
        ### ⚠️ 注意事项
        - API 服务必须在本地运行（默认端口 9880）
        - 参考音频文件必须存在且格式正确（如果设置的话）
        - 流式模式会累积所有音频块并播放完整音频
        - 首次播放可能需要几秒钟初始化
        - 长文本生成时，流式模式可以实时看到进度更新
        """)
    
    return demo


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='CosyVoice2 流式播放界面')
    parser.add_argument('--port', type=int, default=7860, help='Gradio 端口号（默认: 7860）')
    parser.add_argument('--api_url', type=str, default='http://localhost:9880', help='API 服务地址')
    parser.add_argument('--share', action='store_true', help='是否创建公共链接')
    args = parser.parse_args()
    
    # 创建并启动界面
    demo = create_ui(api_url=args.api_url)
    
    print("=" * 60)
    print("CosyVoice2 流式播放界面启动")
    print("=" * 60)
    print(f"📡 Gradio 地址: http://0.0.0.0:{args.port}")
    print(f"🔗 API 服务地址: {args.api_url}")
    print("=" * 60)
    
    demo.queue(max_size=10)
    demo.launch(
        server_name='0.0.0.0',
        server_port=args.port,
        share=args.share
    )
