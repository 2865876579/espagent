"""
本地测试客户端 - 模拟 ESP32 发送文字消息测试完整链路

用法：
  1. 先启动服务端：python main.py
  2. 再运行本脚本：python test_client.py
  3. 输入文字即可测试对话，AI 回复会保存为 reply.mp3

功能：
  - 跳过 STT（直接发文字），测试 LLM 对话 + TTS 合成
  - 不需要麦克风和 ESP32 硬件
  - 用于验证云端服务是否正常工作

环境变量：
  WS_URL: WebSocket 地址，默认 ws://localhost:8000/ws/esp32
  测试远程服务器时设为 ws://你的服务器IP:8000/ws/esp32
"""
import asyncio
import json
import base64
import websockets
import os


async def test_text_mode():
    """文字模式测试：跳过 STT，直接发文字给 LLM，收到 TTS 音频保存为 mp3"""
    # 连接地址，默认本地，可通过环境变量改为远程服务器
    uri = os.getenv("WS_URL", "ws://localhost:8000/ws/esp32")
    print(f"连接到 {uri} ...")

    async with websockets.connect(uri) as ws:
        print("已连接！输入文字测试对话，输入 quit 退出\n")

        while True:
            text = input("你: ")
            if text.lower() == "quit":
                break

            # 发送文字消息给服务端（type=text 会跳过 STT，直接送 LLM）
            await ws.send(json.dumps({
                "type": "text",
                "text": text
            }))

            # 等待服务端返回
            response = await ws.recv()
            data = json.loads(response)

            if data.get("type") == "tts_audio":
                # 收到 TTS 音频回复
                print(f"AI: {data.get('text')}")
                # 解码 base64 音频并保存为 mp3 文件
                audio_bytes = base64.b64decode(data["audio"])
                filename = "reply.mp3"
                with open(filename, "wb") as f:
                    f.write(audio_bytes)
                print(f"[音频已保存到 {filename}，可以播放听效果]\n")

            elif data.get("type") == "status":
                # 收到状态提示（如"没听清"）
                print(f"[状态] {data.get('msg')}\n")


if __name__ == "__main__":
    asyncio.run(test_text_mode())
