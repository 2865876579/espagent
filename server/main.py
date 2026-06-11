"""智能枕头云端服务 - WebSocket 主入口"""
import json
import asyncio
import base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from config import SERVER_HOST, SERVER_PORT
from stt_xunfei import recognize
from llm_deepseek import chat
from tts_edge import synthesize

app = FastAPI(title="Smart Pillow Cloud Server")

# 已连接的 PC Agent 客户端
pc_agents: dict[str, WebSocket] = {}


@app.websocket("/ws/esp32")
async def esp32_endpoint(websocket: WebSocket):
    """ESP32 连接入口：接收音频，返回 TTS 音频"""
    await websocket.accept()
    print("[ESP32] 已连接")

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "text":
                text = data["text"]
                print(f"[Text] {text}")

                result = await chat(text)
                reply = result.get("reply", "")
                pc_command = result.get("pc_command")
                print(f"[LLM] reply={reply}, pc_cmd={pc_command}")

                if pc_command and pc_agents:
                    agent_ws = next(iter(pc_agents.values()))
                    await agent_ws.send_text(json.dumps({
                        "type": "pc_command",
                        "command": pc_command
                    }))

                if reply:
                    audio_data = await synthesize(reply)
                    audio_b64_out = base64.b64encode(audio_data).decode()
                    await websocket.send_text(json.dumps({
                        "type": "tts_audio",
                        "format": "mp3",
                        "audio": audio_b64_out,
                        "text": reply
                    }))

            elif data.get("type") == "audio":
                audio_b64 = data["audio"]
                audio_bytes = base64.b64decode(audio_b64)

                frame_size = 1280  # 40ms at 16kHz 16bit mono
                frames = [
                    audio_bytes[i:i + frame_size]
                    for i in range(0, len(audio_bytes), frame_size)
                ]

                # STT
                text = await recognize(frames)
                if not text.strip():
                    await websocket.send_text(json.dumps(
                        {"type": "status", "msg": "没听清，请再说一次"}
                    ))
                    continue

                print(f"[STT] {text}")
                await websocket.send_text(json.dumps(
                    {"type": "stt_result", "text": text}
                ))

                # LLM
                result = await chat(text)
                reply = result.get("reply", "")
                pc_command = result.get("pc_command")
                print(f"[LLM] reply={reply}, pc_cmd={pc_command}")

                # 如果有 PC 命令，转发给 PC Agent
                if pc_command and pc_agents:
                    agent_ws = next(iter(pc_agents.values()))
                    await agent_ws.send_text(json.dumps({
                        "type": "pc_command",
                        "command": pc_command
                    }))
                    print(f"[PC Agent] 已发送命令: {pc_command}")

                # TTS
                if reply:
                    audio_data = await synthesize(reply)
                    audio_b64_out = base64.b64encode(audio_data).decode()
                    await websocket.send_text(json.dumps({
                        "type": "tts_audio",
                        "format": "mp3",
                        "audio": audio_b64_out,
                        "text": reply
                    }))

            elif data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        print("[ESP32] 已断开")


@app.websocket("/ws/pc_agent")
async def pc_agent_endpoint(websocket: WebSocket):
    """PC Agent 连接入口：接收电脑控制命令，返回执行结果"""
    await websocket.accept()
    agent_id = str(id(websocket))
    pc_agents[agent_id] = websocket
    print(f"[PC Agent] 已连接 ({agent_id})")

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "result":
                print(f"[PC Agent] 执行结果: {data.get('result')}")
                # TODO: 可以把结果通过 TTS 播报给 ESP32

            elif data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pc_agents.pop(agent_id, None)
        print(f"[PC Agent] 已断开 ({agent_id})")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "esp32_connected": False,
        "pc_agents": len(pc_agents)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=int(SERVER_PORT))
