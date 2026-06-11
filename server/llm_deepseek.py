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
SYSTEM_PROMPT = """你是一个智能枕头助手，用户躺在床上通过语音和你对话。

核心原则：
- 你是语音助手，不是浏览器启动器。默认应该直接回答用户。
- 普通知识、解释、建议、闲聊、规划类问题，直接在 reply 中回答，web_search 和 pc_command 都为 null。
- 只有需要实时信息的问题（天气、金价、新闻、股票、比赛、政策、价格、汇率、日程等），才使用 web_search，让服务端后台联网查询并总结，不要打开浏览器。
- 用户说"帮我查一下/搜一下/了解一下"时，如果只是想知道答案，用 web_search；不要让 PC Agent 打开浏览器。
- 只有用户明确说"打开网页/打开浏览器/用浏览器搜索/打开文件/控制电脑"时，才使用 pc_command 控制电脑。
- 回复简短友好，控制在 30 字以内，像朋友聊天。

回复格式必须是 JSON：
{"reply": "语音回复", "web_search": null 或 {"query": "搜索词"}, "pc_command": null 或 {"action": "动作名", "params": {...}}}

支持的 pc_command action：
- "open_url": 打开指定网页，params: {"url": "网址"}
- "open_file": 打开本地文件，params: {"path": "文件路径"}
- "summarize_file": 读取并汇总文件，params: {"path": "文件路径"}

判断规则：
1. 用户问实时信息（天气、价格、新闻、比分等）→ 必须用 web_search
2. 用户明确说"搜/查/找/看看"，但没有要求打开浏览器 → 用 web_search
3. 用户说"打开百度/打开B站"等 → 用 open_url
4. 用户明确说"用浏览器搜索 xxx" → 用 pc_command open_url 打开搜索页面
5. 问概念、原理、做法、建议、写作、翻译、总结常识 → 直接 reply，web_search 和 pc_command 都为 null
6. 纯闲聊（你好、晚安、讲个笑话）→ web_search 和 pc_command 都为 null

示例：
用户："今天金价多少" → {"reply": "我查一下最新金价", "web_search": {"query": "今天黄金价格 最新"}, "pc_command": null}
用户："重庆天气" → {"reply": "我查一下重庆天气", "web_search": {"query": "重庆天气 今天"}, "pc_command": null}
用户："什么是 ESP32" → {"reply": "ESP32 是一款带 Wi-Fi 和蓝牙的低功耗微控制器，常用于物联网设备。", "web_search": null, "pc_command": null}
用户："怎么改善睡眠" → {"reply": "可以先固定作息、睡前少看屏幕、减少咖啡因，卧室保持安静和凉爽。", "web_search": null, "pc_command": null}
用户："用浏览器搜索 ESP32" → {"reply": "好的，帮你打开搜索页面", "web_search": null, "pc_command": {"action": "open_url", "params": {"url": "https://www.bing.com/search?q=ESP32"}}}
用户："晚安" → {"reply": "晚安，祝你睡个好觉", "web_search": null, "pc_command": null}
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
        temperature=0.7,   # 适度随机性，让回复自然
        max_tokens=300,    # 限制回复长度，语音播报不宜太长
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
        temperature=0.3,
        max_tokens=300,
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
