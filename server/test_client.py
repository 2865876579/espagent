"""
本地测试脚本 - 模拟 ESP32 发送文字消息测试完整链路
用法：python test_client.py
不需要麦克风，直接输入文字测试 LLM + TTS
"""
import asyncio
import json
import base64
import websockets
import os


async def test_text_mode():
    """文字模式测试：跳过 STT，直接发文字给 LLM"""
    uri = os.getenv("WS_URL", "ws://localhost:8000/ws/esp32")
    print(f"连接到 {uri} ...")

    async with websockets.connect(uri) as ws:
        print("已连接！输入文字测试对话，输入 quit 退出\n")

        while True:
            text = input("你: ")
            if text.lower() == "quit":
                break

            # 发送文字消息（跳过 STT）
            await ws.send(json.dumps({
                "type": "text",
                "text": text
            }))

            # 接收回复
            response = await ws.recv()
            data = json.loads(response)

            if data.get("type") == "tts_audio":
                print(f"AI: {data.get('text')}")
                audio_bytes = base64.b64decode(data["audio"])
                filename = "reply.mp3"
                with open(filename, "wb") as f:
                    f.write(audio_bytes)
                print(f"[音频已保存到 {filename}，可以播放听效果]\n")

            elif data.get("type") == "status":
                print(f"[状态] {data.get('msg')}\n")


if __name__ == "__main__":
    asyncio.run(test_text_mode())
