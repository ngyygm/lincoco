"""
优化后的 API 接口（使用 vLLM 加载）
功能：
1. /set_model - 设置参考音频和文本（可选设置文本，默认用文件名）
2. /generate - 非流式生成音频
3. /generate_stream - 实时流式生成音频

优势：
- 默认加载音频后，不需要每次都上传音频
- 不需要ASR识别，直接用文件名作为文本（更准确）
- 启动时自动加载默认音频
- 使用 vLLM 加速推理性能
"""
import time
import io, os, sys
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append('{}/third_party/AcademiCodec'.format(ROOT_DIR))
sys.path.append('{}/third_party/Matcha-TTS'.format(ROOT_DIR))

import numpy as np
from flask import Flask, request, Response
import torch
import torchaudio

from cosyvoice.cli.cosyvoice import AutoModel
from cosyvoice.utils.common import set_all_random_seed
from vllm import ModelRegistry
from cosyvoice.vllm.cosyvoice2 import CosyVoice2ForCausalLM

from flask_cors import CORS
from flask import make_response

import json
import argparse

# 注册 vLLM 模型
ModelRegistry.register_model("CosyVoice2ForCausalLM", CosyVoice2ForCausalLM)

# 全局变量（在 main 中初始化）
cosyvoice = None

# 全局参考音频配置
DEFAULT_REFERENCE_WAV = None
DEFAULT_REFERENCE_TEXT = None
DEFAULT_SAMPLE_RATE = 24000  # CosyVoice2 使用 24000 Hz

# 全局模型配置
CURRENT_MODEL = None
IS_COSYVOICE3 = False  # 标识是否为 CosyVoice3 模型

def get_text_from_filename(wav_path):
    """从wav文件名中提取文本（去掉扩展名）"""
    basename = os.path.basename(wav_path)
    # 移除常见的音频扩展名
    for ext in ['.wav', '.mp3', '.m4a', '.flac', '.ogg']:
        if basename.lower().endswith(ext):
            return basename[:-len(ext)]
    return basename

def load_reference_audio(wav_path, reference_text=None, use_filename_as_text=True):
    """
    加载参考音频用于3s极速复刻

    Args:
        wav_path: 音频文件路径
        reference_text: 参考文本（如果为None且use_filename_as_text=True，则从文件名提取）
        use_filename_as_text: 是否使用文件名作为文本（默认True，避免ASR识别）
    """
    global DEFAULT_REFERENCE_WAV, DEFAULT_REFERENCE_TEXT

    if not wav_path or not os.path.exists(wav_path):
        raise ValueError(f"音频文件不存在: {wav_path}")

    DEFAULT_REFERENCE_WAV = wav_path

    # 确定参考文本
    if reference_text:
        DEFAULT_REFERENCE_TEXT = reference_text
    elif use_filename_as_text:
        DEFAULT_REFERENCE_TEXT = get_text_from_filename(wav_path)
    else:
        DEFAULT_REFERENCE_TEXT = ""

    print(f"✅ 已加载参考音频: {wav_path}")
    print(f"   参考文本: {DEFAULT_REFERENCE_TEXT}")

    return {
        "success": True,
        "wav_path": wav_path,
        "reference_text": DEFAULT_REFERENCE_TEXT
    }

def process_audio(tts_speeches, sample_rate=DEFAULT_SAMPLE_RATE, format="wav"):
    """处理音频数据并返回响应"""
    buffer = io.BytesIO()
    audio_data = torch.concat(tts_speeches, dim=1)
    torchaudio.save(buffer, audio_data, sample_rate, format=format)
    buffer.seek(0)
    return buffer

def create_audio_response(buffer, format="wav"):
    """创建音频响应"""
    if format == "wav":
        return Response(buffer.read(), mimetype="audio/wav")
    else:
        response = make_response(buffer.read())
        response.headers['Content-Type'] = f'audio/{format}'
        response.headers['Content-Disposition'] = f'attachment; filename=sound.{format}'
        return response

app = Flask(__name__)
CORS(app, origins="*")

# ============================================================================
# 接口1: 设置模型（参考音频和文本）
# ============================================================================
@app.route("/set_model", methods=['POST'])
def set_model():
    """
    设置参考音频模型（用于3s极速复刻）

    请求参数（JSON）:
        wav_path: 音频文件路径（必填）
        reference_text: 参考文本（可选，不填则使用文件名）
        use_filename_as_text: 是否使用文件名作为文本（默认true，避免ASR识别）

    返回:
        {
            "success": true,
            "wav_path": "...",
            "reference_text": "..."
        }
    """
    try:
        data = request.get_json()
        if not data:
            return {"error": "请求体必须为JSON格式"}, 400

        wav_path = data.get('wav_path')
        if not wav_path:
            return {"error": "必须提供wav_path参数"}, 400

        reference_text = data.get('reference_text')
        use_filename_as_text = data.get('use_filename_as_text', True)

        result = load_reference_audio(wav_path, reference_text, use_filename_as_text)
        return result, 200

    except Exception as e:
        return {"error": f"设置模型失败: {str(e)}"}, 500

# ============================================================================
# 接口2: 非流式生成音频
# ============================================================================
@app.route("/generate", methods=['POST'])
def generate():
    """
    非流式生成音频（返回完整音频文件）

    请求参数（JSON）:
        text: 要合成的文本（必填）
        speed: 语速（可选，默认1.0）

    返回:
        音频文件（WAV格式）
    """
    try:
        data = request.get_json()
        if not data:
            return {"error": "请求体必须为JSON格式"}, 400

        text = data.get('text')
        if not text:
            return {"error": "必须提供text参数"}, 400

        speed = float(data.get('speed', 1.0))

        # 检查是否已设置参考音频
        if not DEFAULT_REFERENCE_WAV:
            return {
                "error": "未设置参考音频，请先调用 /set_model 接口",
                "hint": "可以使用默认音频，或在启动时放置参考音频文件"
            }, 400

        # 使用 zero_shot 模式生成音频（3s极速复刻）
        tts_speeches = []
        for i in cosyvoice.inference_zero_shot(
            text,
            DEFAULT_REFERENCE_TEXT,
            DEFAULT_REFERENCE_WAV,
            stream=False,
            speed=speed
        ):
            tts_speeches.append(i['tts_speech'])

        # 处理并返回音频
        buffer = process_audio(tts_speeches, format="wav")
        return create_audio_response(buffer), 200

    except Exception as e:
        return {"error": f"生成音频失败: {str(e)}"}, 500

# ============================================================================
# 接口3: 实时流式生成音频
# ============================================================================
@app.route("/generate_stream", methods=['POST'])
def generate_stream():
    """
    实时流式生成音频（边生成边返回）

    请求参数（JSON）:
        text: 要合成的文本（必填）
        speed: 语速（可选，默认1.0）

    返回:
        流式音频数据（OGG格式）
    """
    # 验证输入
    data = request.get_json()
    if not data:
        return {"error": "请求体必须为JSON格式"}, 400

    text = data.get('text')
    if not text:
        return {"error": "必须提供text参数"}, 400

    speed = float(data.get('speed', 1.0))

    # 检查是否已设置参考音频
    if not DEFAULT_REFERENCE_WAV:
        return {
            "error": "未设置参考音频，请先调用 /set_model 接口",
            "hint": "可以使用默认音频，或在启动时放置参考音频文件"
        }, 400

    def generate():
        """流式生成音频"""
        try:
            for i in cosyvoice.inference_zero_shot(
                text,
                DEFAULT_REFERENCE_TEXT,
                DEFAULT_REFERENCE_WAV,
                stream=True,
                speed=speed
            ):
                # 处理每个音频块 - 使用 WAV 格式以避免编码问题
                buffer = process_audio([i['tts_speech']], format="wav")
                yield buffer.read()
        except Exception as e:
            print(f"流式生成错误: {e}")
            # 返回错误信息（但客户端可能已经收到部分音频）
            error_msg = f"Error: {str(e)}".encode()
            yield error_msg

    response = make_response(generate())
    response.headers.update({
        'Content-Type': 'audio/wav',
        'Content-Disposition': 'attachment; filename=sound.wav',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'  # 禁用Nginx缓冲
    })

    return response

# ============================================================================
# 辅助接口：获取/查询当前配置
# ============================================================================
@app.route("/get_model", methods=['GET'])
def get_model():
    """获取当前参考音频配置"""
    return {
        "wav_path": DEFAULT_REFERENCE_WAV,
        "reference_text": DEFAULT_REFERENCE_TEXT,
        "has_reference": DEFAULT_REFERENCE_WAV is not None
    }

@app.route("/health", methods=['GET'])
def health():
    """健康检查"""
    return {
        "status": "ok",
        "model": f"{CURRENT_MODEL} (vLLM)",
        "is_cosyvoice3": IS_COSYVOICE3,
        "has_reference": DEFAULT_REFERENCE_WAV is not None
    }

# ============================================================================
# 主程序：启动时加载默认音频
# ============================================================================
if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='CosyVoice API 服务（vLLM加速）')
    parser.add_argument('--port', type=int, default=9880, help='服务端口号（默认: 9880）')
    parser.add_argument('--model', type=str, default='CosyVoice2-0.5B',
                        help='模型名称（默认: CosyVoice2-0.5B，可选: Fun-CosyVoice3-0.5B）')
    parser.add_argument('--fp16', action='store_true', default=True, help='使用FP16精度（默认: True）')
    parser.add_argument('--no-fp16', action='store_true', help='禁用FP16精度')
    args = parser.parse_args()

    # 更新全局模型配置
    CURRENT_MODEL = args.model
    IS_COSYVOICE3 = 'CosyVoice3' in args.model or 'Fun-CosyVoice3' in args.model

    # 根据模型类型设置默认参数
    if IS_COSYVOICE3:
        # CosyVoice3 默认不使用 JIT，且默认 FP16=False
        default_fp16 = False
        default_load_jit = False
        model_name_display = "CosyVoice3"
    else:
        # CosyVoice2 默认使用 JIT 和 FP16=True
        default_fp16 = True
        default_load_jit = True
        model_name_display = "CosyVoice2"

    # 确定 FP16 设置（CosyVoice3 默认禁用，CosyVoice2 默认启用）
    if args.no_fp16:
        use_fp16 = False
    elif args.fp16:
        use_fp16 = True
    else:
        use_fp16 = default_fp16

    print("=" * 60)
    print(f"{model_name_display} API 服务启动（vLLM加速）")
    print("=" * 60)
    print(f"模型: {CURRENT_MODEL}")
    print(f"vLLM 加载模式: 已启用")
    print(f"FP16 精度: {'启用' if use_fp16 else '禁用'}")

    # 在 main 中初始化模型（vLLM 需要 spawn 模式，必须在 main 中初始化）
    model_dir = f'pretrained_models/{CURRENT_MODEL}'
    print(f"\n正在加载 {model_name_display} 模型（使用 vLLM）...")
    print(f"模型路径: {model_dir}")

    # 根据模型类型使用不同的加载参数
    if IS_COSYVOICE3:
        # CosyVoice3 加载方式（参考 vllm_example.py）
        cosyvoice = AutoModel(
            model_dir=model_dir,
            load_trt=True,
            load_vllm=True,
            fp16=use_fp16
        )
    else:
        # CosyVoice2 加载方式（参考 vllm_example.py）
        cosyvoice = AutoModel(
            model_dir=model_dir,
            load_jit=True,
            load_trt=True,
            load_vllm=True,
            fp16=use_fp16
        )

    print("✅ 模型加载完成\n")

    # 默认音频文件名（就是音频内容）
    default_wav_file = "他们是不是都问你是不是直男，这个可以呀，这个很好.wav"

    # 尝试多个可能的路径
    possible_paths = [
        os.path.join(ROOT_DIR, default_wav_file),
        os.path.join(ROOT_DIR, "audios", default_wav_file),
    ]

    # 查找默认音频
    default_wav_path = None
    for path in possible_paths:
        if os.path.exists(path):
            default_wav_path = path
            break

    # 加载默认音频
    if default_wav_path:
        try:
            load_reference_audio(
                wav_path=default_wav_path,
                reference_text=None,  # 自动从文件名提取
                use_filename_as_text=True
            )
            print(f"\n✅ 默认参考音频已自动加载")
        except Exception as e:
            print(f"\n❌ 加载默认音频失败: {e}")
    else:
        print(f"\n⚠️  默认音频文件未找到")
        print(f"   请将文件放在以下位置之一：")
        for path in possible_paths:
            print(f"   - {path}")

    print("\n" + "=" * 60)
    print("API 接口说明：")
    print("  1. POST /set_model - 设置参考音频模型")
    print("  2. POST /generate - 非流式生成音频")
    print("  3. POST /generate_stream - 实时流式生成音频")
    print("  辅助接口:")
    print("    GET /get_model - 获取当前参考音频配置")
    print("    GET /health - 健康检查")
    print("=" * 60)
    print(f"\n服务启动地址: http://0.0.0.0:{args.port}")
    optimization_features = ["vLLM 加速"]
    if use_fp16:
        optimization_features.append("FP16")
    optimization_features.append("TensorRT")
    print(f"性能优化: {' + '.join(optimization_features)}")
    print(f"当前模型: {CURRENT_MODEL}")
    print()

    app.run(host='0.0.0.0', port=args.port, threaded=True)

# 使用 CosyVoice2-0.5B（默认模型）:
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880 --model CosyVoice2-0.5B
#
# 使用 Fun-CosyVoice3-0.5B:
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880 --model Fun-CosyVoice3-0.5B
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880 --model Fun-CosyVoice3-0.5B --fp16  # 启用FP16
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880 --model Fun-CosyVoice3-0.5B --no-fp16  # 禁用FP16（默认）
#
# 多GPU启动不同服务:
# CUDA_VISIBLE_DEVICES=0 python api_optimized_vllm.py --port 9880
# CUDA_VISIBLE_DEVICES=1 python api_optimized_vllm.py --port 9881 --model Fun-CosyVoice3-0.5B
# CUDA_VISIBLE_DEVICES=2 python api_optimized_vllm.py --port 9882
