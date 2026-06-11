"""讯飞流式语音识别"""
import websocket
import hashlib
import hmac
import base64
import json
import time
from datetime import datetime
from urllib.parse import urlencode, urlparse
from config import XF_APP_ID, XF_API_KEY, XF_API_SECRET


def _create_url():
    url = "wss://iat-api.xfyun.cn/v2/iat"
    now = datetime.utcnow()
    date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    parsed = urlparse(url)
    signature_origin = (
        f"host: {parsed.netloc}\n"
        f"date: {date}\n"
        f"GET {parsed.path} HTTP/1.1"
    )

    signature_sha = hmac.new(
        XF_API_SECRET.encode(), signature_origin.encode(), digestmod=hashlib.sha256
    ).digest()
    signature = base64.b64encode(signature_sha).decode()

    authorization_origin = (
        f'api_key="{XF_API_KEY}", '
        f'algorithm="hmac-sha256", '
        f'headers="host date request-line", '
        f'signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()

    params = {"authorization": authorization, "date": date, "host": parsed.netloc}
    return url + "?" + urlencode(params)


async def recognize(audio_frames: list[bytes]) -> str:
    """
    接收 PCM 音频帧列表，返回识别文本。
    audio_frames: 16kHz 16bit 单声道 PCM 数据块列表
    """
    import asyncio

    result_text = []
    done_event = asyncio.Event()

    def on_message(ws, message):
        data = json.loads(message)
        if data.get("code") != 0:
            done_event.set()
            return
        results = data.get("data", {}).get("result", {}).get("ws", [])
        for w in results:
            for cw in w.get("cw", []):
                result_text.append(cw.get("w", ""))
        if data.get("data", {}).get("status") == 2:
            done_event.set()

    def on_open(ws):
        STATUS_FIRST = 0
        STATUS_CONTINUE = 1
        STATUS_LAST = 2

        common = {"app_id": XF_APP_ID}
        business = {
            "language": "zh_cn",
            "domain": "iat",
            "accent": "mandarin",
            "vad_eos": 3000,
        }

        for i, frame in enumerate(audio_frames):
            if i == 0:
                status = STATUS_FIRST
            elif i == len(audio_frames) - 1:
                status = STATUS_LAST
            else:
                status = STATUS_CONTINUE

            data = {
                "status": status,
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": base64.b64encode(frame).decode(),
            }

            payload = {"data": data}
            if status == STATUS_FIRST:
                payload["common"] = common
                payload["business"] = business

            ws.send(json.dumps(payload))
            time.sleep(0.04)

    def on_error(ws, error):
        done_event.set()

    url = _create_url()
    ws = websocket.WebSocketApp(
        url, on_message=on_message, on_open=on_open, on_error=on_error
    )

    import threading
    t = threading.Thread(target=ws.run_forever)
    t.start()

    loop = asyncio.get_event_loop()
    await asyncio.wait_for(done_event.wait(), timeout=15)
    ws.close()
    t.join(timeout=2)

    return "".join(result_text)
