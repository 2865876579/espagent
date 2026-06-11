"""Edge TTS 文字转语音"""
import edge_tts
import io
from config import TTS_VOICE


async def synthesize(text: str) -> bytes:
    """文字转语音，返回 MP3 音频字节"""
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()
