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
import urllib.parse
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from config import SERVER_HOST, SERVER_PORT
from stt_xunfei import recognize
from llm_deepseek import answer_with_search_results, chat
from tts_edge import synthesize
from web_search import direct_answer_from_results, format_search_results, search_web

app = FastAPI(title="Smart Pillow Cloud Server")
APP_VERSION = "web_search_no_browser_v2"

# 存储已连接的 PC Agent，key 是连接 id，value 是 WebSocket 对象
# 当 LLM 返回电脑控制命令时，会从这里取一个 Agent 转发命令
pc_agents: dict[str, WebSocket] = {}

# 存储已连接的 ESP32 客户端，PC Agent 回传结果时用于播报到设备。
esp32_clients: dict[str, WebSocket] = {}
esp32_send_locks: dict[str, asyncio.Lock] = {}
last_active_esp32_id: str | None = None

REALTIME_KEYWORDS = (
    "天气", "气温", "金价", "黄金", "新闻", "股票", "股价", "汇率", "油价",
    "价格", "行情", "比分", "赛程", "热搜", "今天", "现在", "最新"
)


async def send_json_to_esp32(client_id: str, payload: dict) -> bool:
    """串行发送一条 JSON 消息到指定 ESP32，避免多任务并发写同一 WebSocket。"""
    websocket = esp32_clients.get(client_id)
    lock = esp32_send_locks.get(client_id)
    if websocket is None or lock is None:
        return False

    async with lock:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    return True


async def send_tts_stream_to_esp32(client_id: str, text: str) -> bool:
    """
    把 TTS 音频流式发送到指定 ESP32。

    协议：
      - tts_audio_start：告知一次语音回复开始
      - tts_audio_chunk：多条 MP3 音频块
      - tts_audio_end：告知一次语音回复结束
    """
    websocket = esp32_clients.get(client_id)
    lock = esp32_send_locks.get(client_id)
    if websocket is None or lock is None:
        return False

    async with lock:
        await websocket.send_text(json.dumps({
            "type": "tts_audio_start",
            "format": "mp3",
            "text": text
        }, ensure_ascii=False))

        chunks = 0
        async for audio_chunk in synthesize(text):
            if not audio_chunk:
                continue
            chunks += 1
            await websocket.send_text(json.dumps({
                "type": "tts_audio_chunk",
                "format": "mp3",
                "seq": chunks,
                "audio": base64.b64encode(audio_chunk).decode()
            }, ensure_ascii=False))

        await websocket.send_text(json.dumps({
            "type": "tts_audio_end",
            "format": "mp3",
            "text": text,
            "chunks": chunks
        }, ensure_ascii=False))

    return True


async def send_pc_command(pc_command: dict, client_id: str) -> bool:
    """把 LLM 产生的电脑控制命令转发给任意一个已连接的 PC Agent。"""
    if not pc_agents:
        return False

    agent_ws = next(iter(pc_agents.values()))
    await agent_ws.send_text(json.dumps({
        "type": "pc_command",
        "client_id": client_id,
        "command": pc_command
    }, ensure_ascii=False))
    return True


def pick_esp32_client(client_id: str | None = None) -> str | None:
    """优先选择指定客户端，其次选择最近活跃客户端，最后选择任意在线 ESP32。"""
    if client_id and client_id in esp32_clients:
        return client_id
    if last_active_esp32_id and last_active_esp32_id in esp32_clients:
        return last_active_esp32_id
    return next(iter(esp32_clients.keys()), None)


def extract_search_query_from_url(url: str) -> str | None:
    """Return query text if URL is a search-engine result page."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    params = urllib.parse.parse_qs(parsed.query)

    search_param_names: tuple[str, ...] | None = None
    if "baidu.com" in host:
        search_param_names = ("wd", "word", "q")
    elif "bing.com" in host:
        search_param_names = ("q",)
    elif "google." in host:
        search_param_names = ("q",)
    elif "duckduckgo.com" in host:
        search_param_names = ("q",)
    elif "sogou.com" in host:
        search_param_names = ("query", "keyword", "q")
    elif "so.com" in host or "haosou.com" in host:
        search_param_names = ("q",)

    if not search_param_names:
        return None

    for name in search_param_names:
        values = params.get(name)
        if values and values[0].strip():
            return urllib.parse.unquote_plus(values[0]).strip()
    return None


def should_force_web_search(user_text: str, result: dict) -> bool:
    """Fallback for realtime/search questions when the model does not emit web_search."""
    if any(keyword in user_text for keyword in REALTIME_KEYWORDS):
        return True

    reply = str(result.get("reply", ""))
    if any(word in reply for word in ("正在帮你搜索", "帮你搜索", "帮你查", "查一下")):
        return True

    return False


def get_web_search_query(result: dict, user_text: str) -> str | None:
    """Extract the background web-search query from new or legacy LLM output."""
    web_search = result.get("web_search")
    if isinstance(web_search, dict):
        query = web_search.get("query")
        if query:
            return str(query).strip()
    elif isinstance(web_search, str) and web_search.strip():
        return web_search.strip()

    # Backward compatibility: older prompts used pc_command.search.
    pc_command = result.get("pc_command")
    if isinstance(pc_command, dict) and pc_command.get("action") == "search":
        params = pc_command.get("params", {})
        query = params.get("query") if isinstance(params, dict) else None
        if query:
            result["pc_command"] = None
            return str(query).strip()

    if isinstance(pc_command, dict) and pc_command.get("action") == "open_url":
        params = pc_command.get("params", {})
        url = params.get("url") if isinstance(params, dict) else None
        if url:
            query = extract_search_query_from_url(str(url))
            if query:
                result["pc_command"] = None
                return query

    if should_force_web_search(user_text, result):
        result["pc_command"] = None
        return user_text.strip()

    return None


async def handle_ai_result(client_id: str, user_text: str, result: dict, history: list[dict]) -> None:
    """Handle LLM output: background web search, PC command, and spoken reply."""
    query = get_web_search_query(result, user_text)
    if query:
        print(f"[WebSearch] query={query}")
        await send_json_to_esp32(client_id, {
            "type": "status",
            "msg": f"正在联网查询：{query}"
        })
        results = await search_web(query)
        if not results:
            reply = "我刚才没查到可靠结果，可以换个关键词再问我一次。"
        else:
            reply = direct_answer_from_results(query, results)
            if not reply:
                search_context = format_search_results(query, results)
                reply = await answer_with_search_results(user_text, query, search_context, history)
                if not reply:
                    reply = "我查到了结果，但暂时没能整理成回答。"

        await send_tts_stream_to_esp32(client_id, reply)
        return

    reply = result.get("reply", "")
    pc_command = result.get("pc_command")
    print(f"[LLM] reply={reply}, pc_cmd={pc_command}")

    if pc_command:
        sent = await send_pc_command(pc_command, client_id)
        if not sent:
            await send_json_to_esp32(client_id, {
                "type": "status",
                "msg": "没有在线 PC Agent，无法执行电脑控制命令"
            })

    if reply:
        await send_tts_stream_to_esp32(client_id, reply)


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
    - {"type": "tts_audio_start", "format": "mp3", "text": "回复文字"}
    - {"type": "tts_audio_chunk", "format": "mp3", "seq": 1, "audio": "base64音频块"}
    - {"type": "tts_audio_end", "format": "mp3", "text": "回复文字", "chunks": 12}
    - {"type": "stt_result", "text": "识别出的文字"}
    - {"type": "status", "msg": "状态提示"}
    - {"type": "pong"}
    """
    global last_active_esp32_id

    await websocket.accept()
    client_id = str(id(websocket))
    esp32_clients[client_id] = websocket
    esp32_send_locks[client_id] = asyncio.Lock()
    last_active_esp32_id = client_id
    history: list[dict] = []
    print(f"[ESP32] 已连接 ({client_id})")

    try:
        while True:
            # 等待 ESP32 发来的消息
            message = await websocket.receive_text()
            data = json.loads(message)
            last_active_esp32_id = client_id

            try:
                # ========== 文字模式（调试用，跳过 STT）==========
                if data.get("type") == "text":
                    text = data["text"]
                    print(f"[Text] {text}")

                    result = await chat(text, history)
                    await handle_ai_result(client_id, text, result, history)

                # ========== 语音模式（正式流程：STT -> LLM -> TTS）==========
                elif data.get("type") == "audio":
                    audio_b64 = data["audio"]
                    audio_bytes = base64.b64decode(audio_b64)

                    frame_size = 1280
                    frames = [
                        audio_bytes[i:i + frame_size]
                        for i in range(0, len(audio_bytes), frame_size)
                    ]

                    text = await recognize(frames)
                    if not text.strip():
                        await send_json_to_esp32(client_id, {
                            "type": "status",
                            "msg": "没听清，请再说一次"
                        })
                        continue

                    print(f"[STT] {text}")
                    await send_json_to_esp32(client_id, {
                        "type": "stt_result",
                        "text": text
                    })

                    result = await chat(text, history)
                    await handle_ai_result(client_id, text, result, history)

                # ========== 心跳 ==========
                elif data.get("type") == "ping":
                    await send_json_to_esp32(client_id, {"type": "pong"})

            except Exception as e:
                print(f"[ERROR] 处理消息出错: {e}")
                import traceback
                traceback.print_exc()
                try:
                    await send_json_to_esp32(client_id, {
                        "type": "status",
                        "msg": f"处理出错: {str(e)[:100]}"
                    })
                except Exception:
                    pass

    except WebSocketDisconnect:
        print(f"[ESP32] 已断开 ({client_id})")
    finally:
        esp32_clients.pop(client_id, None)
        esp32_send_locks.pop(client_id, None)
        if last_active_esp32_id == client_id:
            last_active_esp32_id = pick_esp32_client()


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
                result_text = data.get("result", "")
                print(f"[PC Agent] 执行结果: {result_text}")

                target_id = pick_esp32_client(data.get("client_id"))
                if target_id and result_text:
                    sent = await send_tts_stream_to_esp32(target_id, result_text)
                    if sent:
                        print(f"[ESP32] 已播报 PC Agent 结果 -> {target_id}")
                elif not target_id:
                    print("[ESP32] 没有在线客户端，无法播报 PC Agent 结果")

            elif data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}, ensure_ascii=False))

    except WebSocketDisconnect:
        pc_agents.pop(agent_id, None)
        print(f"[PC Agent] 已断开 ({agent_id})")


@app.get("/health")
async def health():
    """健康检查接口，用于确认服务是否在线"""
    return {
        "status": "ok",
        "esp32_connected": bool(esp32_clients),
        "esp32_clients": len(esp32_clients),
        "pc_agents": len(pc_agents)
    }


if __name__ == "__main__":
    import uvicorn
    # 启动 WebSocket 服务，默认监听 0.0.0.0:8000
    print(f"[ESPAgent] version={APP_VERSION}")
    uvicorn.run(
        app,
        host=SERVER_HOST,
        port=int(SERVER_PORT),
        ws_ping_interval=30,
        ws_ping_timeout=120
    )
