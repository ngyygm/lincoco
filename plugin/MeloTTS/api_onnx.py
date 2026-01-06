# ONNX 版本的 TTS API
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import io
import soundfile

try:
    from melo_onnx import MeloTTS_ONNX
except ImportError:
    # 如果导入失败，尝试其他可能的名称
    try:
        from melo_onnx import MeloTTX_ONNX as MeloTTS_ONNX
    except ImportError:
        raise ImportError("无法导入 melo_onnx 模块，请确保已安装 melo_onnx 包")

# 初始化 FastAPI
app = FastAPI()

# 定义 TTS 模型实例
tts_model = None

class TTSConfig(BaseModel):
    model_path: str = Field(..., description="ONNX 模型文件夹路径", example=r"C:\Users\Administrator\.cache\modelscope\hub\models\seasonstudio\melotts_zh_mix_en_onnx")

class SynthesizeRequest(BaseModel):
    text: str = Field("要合成的文本", description="要合成的文本")
    speaker: str = Field(None, description="说话人（可选，如果不提供则使用模型默认的第一个说话人）")
    speaker_index: int = Field(None, description="说话人索引（可选，优先级低于 speaker）")

def init_tts_model_with_config(model_path: str):
    """初始化 ONNX TTS 模型的辅助函数"""
    global tts_model
    try:
        # 初始化 ONNX TTS 模型
        tts_model = MeloTTS_ONNX(model_path)
        return True, "TTS model initialized successfully."
    except Exception as e:
        return False, str(e)

@app.on_event("startup")
async def startup_event():
    """应用启动时的提示信息"""
    print("ONNX TTS API 已启动，请使用 /init 端点初始化模型")

@app.post("/init")
async def init_tts_model(config: TTSConfig):
    """初始化 ONNX TTS 模型"""
    global tts_model
    success, message = init_tts_model_with_config(model_path=config.model_path)
    if success:
        return {
            "success": True, 
            "message": message,
            "speakers": list(tts_model.speakers) if tts_model and hasattr(tts_model, 'speakers') else [],
            "sample_rate": tts_model.sample_rate if tts_model and hasattr(tts_model, 'sample_rate') else None
        }
    else:
        raise HTTPException(status_code=500, detail=message)

@app.get("/speakers")
async def get_speakers():
    """获取当前模型的可用说话人列表"""
    if not tts_model:
        raise HTTPException(status_code=400, detail="TTS model not initialized. Please call /init first.")
    
    try:
        speakers = tts_model.speakers if hasattr(tts_model, 'speakers') else []
        return {
            "speakers": list(speakers) if speakers else [],
            "default_speaker": speakers[0] if speakers else None,
            "sample_rate": tts_model.sample_rate if hasattr(tts_model, 'sample_rate') else None
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法获取说话人列表: {str(e)}")

@app.post("/tts")
async def synthesize_tts(request: SynthesizeRequest):
    """合成语音"""
    if not tts_model:
        raise HTTPException(status_code=400, detail="TTS model not initialized. Please call /init first.")
    
    try:
        # 确定说话人：优先使用 speaker，其次使用 speaker_index，最后使用模型默认的第一个说话人
        speaker = None
        if request.speaker:
            # 通过说话人名称获取
            try:
                speakers = tts_model.speakers if hasattr(tts_model, 'speakers') else []
                if request.speaker in speakers:
                    speaker = request.speaker
                else:
                    raise HTTPException(
                        status_code=400, 
                        detail=f"说话人 '{request.speaker}' 不存在。可用说话人: {list(speakers)}"
                    )
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"无法获取说话人列表: {str(e)}")
        elif request.speaker_index is not None:
            # 通过索引获取
            try:
                speakers = tts_model.speakers if hasattr(tts_model, 'speakers') else []
                if 0 <= request.speaker_index < len(speakers):
                    speaker = speakers[request.speaker_index]
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=f"说话人索引 {request.speaker_index} 超出范围。可用索引: 0-{len(speakers)-1}"
                    )
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"无法获取说话人列表: {str(e)}")
        else:
            # 使用模型默认的第一个说话人
            try:
                speakers = tts_model.speakers if hasattr(tts_model, 'speakers') else []
                speaker = speakers[0] if speakers else None
                if speaker is None:
                    raise HTTPException(status_code=400, detail="模型没有可用的说话人")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"无法获取默认说话人: {str(e)}")
        
        # 使用 ONNX TTS 模型合成语音
        audio = tts_model.speak(request.text, speaker)

        # 获取采样率
        sample_rate = tts_model.sample_rate if hasattr(tts_model, 'sample_rate') else 22050

        # 将音频 numpy 数组转换为 WAV 字节流
        audio_stream = io.BytesIO()
        soundfile.write(audio_stream, audio, sample_rate, format='WAV')
        audio_stream.seek(0)  # 重置流位置到开头

        return StreamingResponse(audio_stream, media_type="audio/wav")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 启动 FastAPI
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8102)

