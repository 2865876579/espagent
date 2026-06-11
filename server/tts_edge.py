"""
Edge TTS 文字转语音模块

功能：把文字合成为 MP3 音频，返回字节数据。

优点：
  - 完全免费，不需要 API Key
  - 音质好，支持多种中文音色
  - 基于微软 Edge 浏览器的在线 TTS 服务

常用中文音色（在 .env 里配置 TTS_VOICE）：
  - zh-CN-XiaoxiaoNeural  女声，温柔（默认）
  - zh-CN-YunxiNeural     男声，自然
  - zh-CN-XiaoyiNeural    女声，活泼
  - zh-CN-YunjianNeural   男声，沉稳

查看所有可用音色：命令行运行 edge-tts --list-voices
"""
import edge_tts
import io
from config import TTS_VOICE


async def synthesize(text: str) -> bytes:
    """
    文字转语音

    参数：
      text: 要合成的文字

    返回：
      MP3 格式的音频字节数据

    ESP32 收到后需要解码 MP3 再通过 I2S 播放，
    或者后续改为返回 PCM 让 ESP32 直接播放。
    """
    communicate = edge_tts.Communicate(text, TTS_VOICE)
    buffer = io.BytesIO()
    # 流式接收音频数据块并拼接
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()
