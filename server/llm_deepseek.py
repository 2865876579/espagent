"""DeepSeek LLM 对话"""
from openai import AsyncOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

SYSTEM_PROMPT = """你是一个智能枕头助手。用户通过语音和你对话，你需要：
1. 正常闲聊时，简短友好地回复，控制在50字以内。
2. 如果用户想控制电脑（搜索资料、打开网页、汇总文件、打开文档等），返回结构化命令。

回复格式必须是 JSON：
{
  "reply": "你要说给用户听的话",
  "pc_command": null 或 {"action": "动作名", "params": {"参数": "值"}}
}

支持的 pc_command action：
- "open_url": 打开网页，params: {"url": "网址"}
- "search": 搜索资料，params: {"query": "搜索词"}
- "summarize_file": 汇总文件，params: {"path": "文件路径"}
- "open_file": 打开文件，params: {"path": "文件路径"}

示例：
用户说"帮我搜一下睡眠呼吸暂停"
回复：{"reply": "好的，正在帮你搜索睡眠呼吸暂停的资料", "pc_command": {"action": "search", "params": {"query": "睡眠呼吸暂停"}}}

用户说"今天天气真好"
回复：{"reply": "是呀，天气好心情也好，早点休息哦", "pc_command": null}
"""

conversation_history: list[dict] = []


async def chat(user_text: str) -> dict:
    conversation_history.append({"role": "user", "content": user_text})

    if len(conversation_history) > 20:
        conversation_history[:] = conversation_history[-20:]

    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history,
        temperature=0.7,
        max_tokens=300,
    )

    assistant_msg = response.choices[0].message.content.strip()
    conversation_history.append({"role": "assistant", "content": assistant_msg})

    import json
    try:
        result = json.loads(assistant_msg)
    except json.JSONDecodeError:
        result = {"reply": assistant_msg, "pc_command": None}

    return result
