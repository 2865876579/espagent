"""
DeepSeek LLM 对话模块

功能：
  1. 接收用户文字，调用 DeepSeek API 生成回复
  2. 通过 System Prompt 约束输出格式为 JSON
  3. JSON 包含两部分：
     - reply: 要通过 TTS 说给用户听的话
     - pc_command: 如果用户想控制电脑，返回结构化命令；否则为 null

DeepSeek API 兼容 OpenAI 格式，直接用 openai 库调用。
注册地址：https://platform.deepseek.com/
"""
import json
from json import JSONDecodeError
from openai import AsyncOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# DeepSeek 客户端，兼容 OpenAI SDK 格式
client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# 系统提示词：约束 LLM 的输出格式和行为
SYSTEM_PROMPT = """你是"小眠"，一个陪伴在用户枕头里的睡眠健康助手。用户躺在床上通过语音和你聊天。

== 性格 ==
- 温柔、耐心，像深夜电台里让人安心的声音
- 懂睡眠医学和健康知识，但不用术语压人，用聊天的方式讲清楚
- 偶尔带点小幽默，自然不刻意
- 回复像朋友：该短就短（晚安回"晚安~好梦"），需要解释就好好说（问"什么是睡眠呼吸暂停"可以多说几句）
- 永远用中文回复

== 能力 ==

1. 直接回答（默认）
   常识、建议、闲聊、情感陪伴——直接聊，不用任何工具。

2. 联网搜索
   只有需要实时信息时：天气、金价、新闻、股票、比赛、政策、价格、汇率等。后台会帮你搜，你只需要在 web_search 里填搜索词。

3. 控制电脑
   用户明确说"打开XX网页/用浏览器搜索/打开文件"时用 pc_command。
   action: "open_url" → {"url": "..."}
   action: "open_file" → {"path": "..."}
   action: "summarize_file" → {"path": "..."}

4. 限制
   邮件、微信等私有内容不能直接读。自然告诉用户怎么帮你（发文本、给文件路径），不反复追问。

== 输出格式 ==
只输出一行 JSON：
{"reply": "语音回复内容", "web_search": null, "pc_command": null}
web_search 非空时：{"query": "搜索词"}
pc_command 非空时：{"action": "open_url", "params": {"url": "..."}}

== 示例 ==
用户："今天金价" → {"reply": "帮你查一下最新金价~", "web_search": {"query": "今天黄金价格"}, "pc_command": null}
用户："什么是ESP32" → {"reply": "ESP32是一款带Wi-Fi和蓝牙的低功耗芯片，很多智能家居设备都在用它~", "web_search": null, "pc_command": null}
用户："怎么睡得好一点" → {"reply": "睡前半小时放下手机，房间凉爽安静，试试白噪音。坚持一周就能感觉到变化~", "web_search": null, "pc_command": null}
用户："晚安" → {"reply": "晚安，做个好梦~", "web_search": null, "pc_command": null}
用户："帮我打开B站" → {"reply": "好的~", "web_search": null, "pc_command": {"action": "open_url", "params": {"url": "https://www.bilibili.com"}}}
"""

MAX_HISTORY_MESSAGES = 20


def _parse_json_reply(text: str) -> dict | None:
    """从模型输出中提取完整 JSON 对象；未完整时返回 None。"""
    candidate = text.strip()
    if not candidate:
        return None

    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidate = "\n".join(lines[1:-1]).strip()

    json_start = candidate.find("{")
    if json_start > 0:
        candidate = candidate[json_start:]

    decoder = json.JSONDecoder()
    try:
        result, end = decoder.raw_decode(candidate)
    except JSONDecodeError:
        return None

    tail = candidate[end:].strip()
    if tail and not tail.startswith("```"):
        return None
    if not isinstance(result, dict):
        return None
    return result


async def _close_stream(stream) -> None:
    close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if close is None:
        return
    maybe_awaitable = close()
    if hasattr(maybe_awaitable, "__await__"):
        await maybe_awaitable


async def chat(user_text: str, history: list[dict] | None = None) -> dict:
    """
    与 DeepSeek LLM 对话

    参数：
      user_text: 用户说的话（STT 识别结果或直接输入的文字）

    返回：
      dict，格式为 {"reply": "回复文字", "pc_command": null 或 命令对象}

    如果 LLM 返回的不是合法 JSON（偶尔会发生），
    会把整个回复当作 reply，pc_command 设为 null。
    """
    if history is None:
        history = []

    # 追加用户消息到当前连接的历史
    history.append({"role": "user", "content": user_text})

    # 只保留最近 20 轮对话，避免 token 超限
    if len(history) > MAX_HISTORY_MESSAGES:
        history[:] = history[-MAX_HISTORY_MESSAGES:]

    # 流式调用 DeepSeek API，尽早拼出完整 JSON 后立即返回。
    stream = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
        max_tokens=1024,   # reasoner 推理过程也消耗 token，要给足空间
        stream=True,
    )

    parts: list[str] = []
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue

        parts.append(delta)
        assistant_msg = "".join(parts).strip()
        result = _parse_json_reply(assistant_msg)
        if result is not None:
            history.append({"role": "assistant", "content": assistant_msg})
            if len(history) > MAX_HISTORY_MESSAGES:
                history[:] = history[-MAX_HISTORY_MESSAGES:]
            await _close_stream(stream)
            return result

    assistant_msg = "".join(parts).strip()
    history.append({"role": "assistant", "content": assistant_msg})
    if len(history) > MAX_HISTORY_MESSAGES:
        history[:] = history[-MAX_HISTORY_MESSAGES:]

    # 如果整段流式输出结束后仍不是合法 JSON，兜底成普通回复。
    result = _parse_json_reply(assistant_msg)
    if result is None:
        result = {"reply": assistant_msg, "pc_command": None}

    return result


async def answer_with_search_results(
    user_text: str,
    query: str,
    search_context: str,
    history: list[dict] | None = None,
) -> str:
    """Use DeepSeek to summarize web search results into a short spoken answer."""
    if history is None:
        history = []

    prompt = f"""用户问题：{user_text}
搜索词：{query}

下面是服务端刚刚联网检索到的结果。请只基于这些结果回答，适合语音播报，控制在 120 字以内。
只要结果里有相关信息，就给出最佳答案，并自然说明来源或时间。
不要轻易说"没查到"；只有搜索结果明显无关或完全没有可用信息时，才说明暂时没查到可靠结论。
如果是价格、天气、新闻、行情类问题，优先提取数字、状态、时间和来源限制。

{search_context}
"""

    stream = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": "你是联网搜索结果总结助手。回答要短、自然、适合语音播报。基于结果尽量给出有用答案，不要编造结果中没有的信息。"},
            *history[-MAX_HISTORY_MESSAGES:],
            {"role": "user", "content": prompt},
        ],
        max_tokens=512,
        stream=True,
    )

    parts: list[str] = []
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            parts.append(delta)

    answer = "".join(parts).strip()
    if answer:
        history.append({"role": "assistant", "content": answer})
        if len(history) > MAX_HISTORY_MESSAGES:
            history[:] = history[-MAX_HISTORY_MESSAGES:]
    return answer
