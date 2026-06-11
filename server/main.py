"""
智能枕头云端服务 - WebSocket 主入口

整体架构：
  ESP32（录音/播放） <--WebSocket--> 本服务（STT+LLM+TTS） <--WebSocket--> PC Agent（控制电脑）

本服务负责：
  1. 接收 ESP32 上传的音频，调用讯飞 STT 转成文字
  2. 文字送 DeepSeek LLM，返回回复文本 + 可选的电脑控制命令
  3. 回复文本走 Edge TTS 合成语音，回传 ESP32 播放
  4. 如果 LLM 返回了电脑控制命令，转发给已连接的 PC Agent 执行

WebSocket 端点：
  /ws/esp32     - ESP32 设备连接入口
  /ws/pc_agent  - PC Agent 连接入口
  /health       - HTTP 健康检查
"""
import json
import asyncio
import base64
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from config import SERVER_HOST, SERVER_PORT
from stt_xunfei import recognize
from llm_deepseek import chat
from tts_edge import synthesize

app = FastAPI(title="Smart Pillow Cloud Server")

# 存储已连接的 PC Agent，key 是连接 id，value 是 WebSocket 对象
# 当 LLM 返回电脑控制命令时，会从这里取一个 Agent 转发命令
pc_agents: dict[str, WebSocket] = {}


@app.websocket("/ws/esp32")
async def esp32_endpoint(websocket: WebSocket):
    """
    ESP32 设备的 WebSocket 连接入口

    支持两种消息类型：
    1. {"type": "text", "text": "用户说的话"}
       - 文字模式，跳过 STT，直接送 LLM（用于调试和测试）

    2. {"type": "audio", "audio": "base64编码的PCM音频"}
       - 语音模式，先 STT 转文字，再送 LLM
       - 音频格式要求：16kHz 采样率，16bit，单声道，PCM 原始数据

    3. {"type": "ping"} - 心跳保活

    返回消息类型：
    - {"type": "tts_audio", "format": "mp3", "audio": "base64音频", "text": "回复文字"}
    - {"type": "stt_result", "text": "识别出的文字"}
    - {"type": "status", "msg": "状态提示"}
    - {"type": "pong"}
    """
    await websocket.accept()
    print("[ESP32] 已连接")

    try:
        while True:
            # 等待 ESP32 发来的消息
            message = await websocket.receive_text()
            data = json.loads(message)

            # ========== 文字模式（调试用，跳过 STT）==========
            if data.get("type") == "text":
                text = data["text"]
                print(f"[Text] {text}")

                # 送 LLM 获取回复和可能的电脑控制命令
                result = await chat(text)
                reply = result.get("reply", "")
                pc_command = result.get("pc_command")
                print(f"[LLM] reply={reply}, pc_cmd={pc_command}")

                # 如果 LLM 要求控制电脑，转发给 PC Agent
                if pc_command and pc_agents:
                    agent_ws = next(iter(pc_agents.values()))
                    await agent_ws.send_text(json.dumps({
                        "type": "pc_command",
                        "command": pc_command
                    }))

                # 回复文字转语音，发回 ESP32 播放
                if reply:
                    audio_data = await synthesize(reply)
                    audio_b64_out = base64.b64encode(audio_data).decode()
                    await websocket.send_text(json.dumps({
                        "type": "tts_audio",
                        "format": "mp3",
                        "audio": audio_b64_out,
                        "text": reply
                    }))

            # ========== 语音模式（正式流程：STT -> LLM -> TTS）==========
            elif data.get("type") == "audio":
                # 解码 ESP32 上传的 base64 PCM 音频
                audio_b64 = data["audio"]
                audio_bytes = base64.b64decode(audio_b64)

                # 按 40ms 一帧切分（16kHz * 16bit * 1ch * 0.04s = 1280 字节/帧）
                frame_size = 1280
                frames = [
                    audio_bytes[i:i + frame_size]
                    for i in range(0, len(audio_bytes), frame_size)
                ]

                # 第一步：语音识别（STT）
                text = await recognize(frames)
                if not text.strip():
                    await websocket.send_text(json.dumps(
                        {"type": "status", "msg": "没听清，请再说一次"}
                    ))
                    continue

                print(f"[STT] {text}")
                # 把识别结果先发回去，让 ESP32 屏幕可以显示
                await websocket.send_text(json.dumps(
                    {"type": "stt_result", "text": text}
                ))

                # 第二步：送 LLM 对话
                result = await chat(text)
                reply = result.get("reply", "")
                pc_command = result.get("pc_command")
                print(f"[LLM] reply={reply}, pc_cmd={pc_command}")

                # 如果有电脑控制命令，转发给 PC Agent
                if pc_command and pc_agents:
                    agent_ws = next(iter(pc_agents.values()))
                    await agent_ws.send_text(json.dumps({
                        "type": "pc_command",
                        "command": pc_command
                    }))
                    print(f"[PC Agent] 已发送命令: {pc_command}")

                # 第三步：回复文字转语音（TTS），发回 ESP32 播放
                if reply:
                    audio_data = await synthesize(reply)
                    audio_b64_out = base64.b64encode(audio_data).decode()
                    await websocket.send_text(json.dumps({
                        "type": "tts_audio",
                        "format": "mp3",
                        "audio": audio_b64_out,
                        "text": reply
                    }))

            # ========== 心跳 ==========
            elif data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        print("[ESP32] 已断开")


@app.websocket("/ws/pc_agent")
async def pc_agent_endpoint(websocket: WebSocket):
    """
    PC Agent 的 WebSocket 连接入口

    PC 端运行一个 Agent 程序，连接到这里等待命令。
    当 LLM 判断用户想控制电脑时，命令会通过这个连接下发。

    PC Agent 发来的消息类型：
    - {"type": "result", "result": "执行结果文字"}  执行完毕后返回结果
    - {"type": "ping"}  心跳

    服务端下发的消息类型：
    - {"type": "pc_command", "command": {"action": "...", "params": {...}}}
    - {"type": "pong"}
    """
    await websocket.accept()
    agent_id = str(id(websocket))
    pc_agents[agent_id] = websocket
    print(f"[PC Agent] 已连接 ({agent_id})")

    try:
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            # PC Agent 执行完命令后返回结果
            if data.get("type") == "result":
                print(f"[PC Agent] 执行结果: {data.get('result')}")
                # TODO: 把结果通过 TTS 合成语音，发给 ESP32 播报

            elif data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pc_agents.pop(agent_id, None)
        print(f"[PC Agent] 已断开 ({agent_id})")


@app.get("/health")
async def health():
    """健康检查接口，用于确认服务是否在线"""
    return {
        "status": "ok",
        "esp32_connected": False,
        "pc_agents": len(pc_agents)
    }


if __name__ == "__main__":
    import uvicorn
    # 启动 WebSocket 服务，默认监听 0.0.0.0:8000
    uvicorn.run(app, host=SERVER_HOST, port=int(SERVER_PORT))
