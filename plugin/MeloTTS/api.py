# oip install fastapi
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import io
import soundfile

from melo.api import TTS  # 确保 TTS 类导入正确

# 初始化 FastAPI
app = FastAPI()

# 定义 TTS 模型实例
tts_model = None

class TTSConfig(BaseModel):
    language: str = Field('ZH', description="使用的语言，如 'EN', 'ZH', 'JP', 等")
    device: str = Field("auto", description="设备选择，默认为 'auto'，自动选择设备. cpu cuda", example="auto")
    use_hf: bool = Field(True, description="是否使用 Hugging Face 模型，默认为 True", example=True)
    config_path: str = Field(None, description="配置文件路径，可为空", example="melo/logs/config/config.json")
    ckpt_path: str = Field(None, description="检查点文件路径，可为空", example="melo/logs/config/G_1000.pth")

class SynthesizeRequest(BaseModel):
    text: str = Field("要合成的文本", description="要合成的文本")
    speaker_id: int = Field(None, description="说话人 ID（可选，如果不提供则使用模型默认的第一个说话人）")
    speaker_name: str = Field(None, description="说话人名称（可选，如 'ZH', 'EN-US' 等，优先级高于 speaker_id）")
    sdp_ratio: float = Field(0.2, description="SDP（Source-Dependent Parameter）比例，默认为 0.2", example=0.3)
    noise_scale: float = Field(0.6, description="噪声比例，默认为 0.6", example=0.7)
    noise_scale_w: float = Field(0.8, description="噪声宽度比例，默认为 0.8", example=0.9)
    speed: float = Field(1.0, description="语速，默认为 1.0", example=1.2)

def init_tts_model_with_config(language='ZH', device='auto', use_hf=True, config_path=None, ckpt_path=None):
    """初始化 TTS 模型的辅助函数"""
    global tts_model
    try:
        # 初始化 TTS 模型
        tts_model = TTS(
            language=language,
            device=device,
            use_hf=use_hf,
            config_path=config_path,
            ckpt_path=ckpt_path
        )
        return True, "TTS model initialized successfully."
    except Exception as e:
        return False, str(e)

@app.on_event("startup")
async def startup_event():
    """应用启动时自动初始化中文 TTS 模型"""
    print("正在初始化中文 TTS 模型...")
    success, message = init_tts_model_with_config(language='ZH', device='auto', use_hf=True)
    if success:
        print(f"✓ {message}")
    else:
        print(f"✗ 初始化失败: {message}")

@app.post("/init")
async def init_tts_model(config: TTSConfig):
    """重新初始化 TTS 模型"""
    global tts_model
    success, message = init_tts_model_with_config(
        language=config.language,
        device=config.device,
        use_hf=config.use_hf,
        config_path=config.config_path,
        ckpt_path=config.ckpt_path
    )
    if success:
        return {"success": True, "message": message}
    else:
        raise HTTPException(status_code=500, detail=message)

@app.get("/speakers")
async def get_speakers():
    """获取当前模型的可用说话人列表"""
    if not tts_model:
        raise HTTPException(status_code=400, detail="TTS model not initialized.")
    
    try:
        spk2id = tts_model.hps.data.spk2id
        # 返回说话人名称到ID的映射
        return {
            "speakers": {name: int(speaker_id) for name, speaker_id in spk2id.items()},
            "default_speaker": list(spk2id.keys())[0] if spk2id else None,
            "default_speaker_id": int(list(spk2id.values())[0]) if spk2id else 0
        }
    except AttributeError:
        raise HTTPException(status_code=400, detail="模型未正确初始化，无法获取说话人列表")

@app.post("/tts")
async def synthesize_tts(request: SynthesizeRequest):
    if not tts_model:
        raise HTTPException(status_code=400, detail="TTS model not initialized.")
    
    try:
        # 确定 speaker_id：优先使用 speaker_name，其次使用 speaker_id，最后使用模型默认的第一个说话人
        speaker_id = None
        if request.speaker_name:
            # 通过说话人名称获取 speaker_id（与 WebUI 一致）
            try:
                spk2id = tts_model.hps.data.spk2id
                if request.speaker_name in spk2id:
                    speaker_id = spk2id[request.speaker_name]
                else:
                    raise HTTPException(status_code=400, detail=f"说话人名称 '{request.speaker_name}' 不存在。可用说话人: {list(spk2id.keys())}")
            except AttributeError:
                raise HTTPException(status_code=400, detail="模型未正确初始化，无法获取说话人列表")
        elif request.speaker_id is not None:
            speaker_id = request.speaker_id
        else:
            # 使用模型默认的第一个说话人（与 WebUI 一致）
            try:
                spk2id = tts_model.hps.data.spk2id
                speaker_id = list(spk2id.values())[0] if spk2id else 0
            except AttributeError:
                speaker_id = 0
        
        # 使用 TTS 模型合成语音，不保存到文件，直接返回音频数据
        audio = tts_model.tts_to_file(
            text=request.text,
            speaker_id=speaker_id,
            sdp_ratio=request.sdp_ratio,
            noise_scale=request.noise_scale,
            noise_scale_w=request.noise_scale_w,
            speed=request.speed,
            output_path=None  # 不保存到文件，直接返回音频数据
        )

        # 将音频 numpy 数组转换为 WAV 字节流
        audio_stream = io.BytesIO()
        soundfile.write(audio_stream, audio, tts_model.hps.data.sampling_rate, format='WAV')
        audio_stream.seek(0)  # 重置流位置到开头

        return StreamingResponse(audio_stream, media_type="audio/wav")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 启动 FastAPI
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8101)
