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
from openai import AsyncOpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# DeepSeek 客户端，兼容 OpenAI SDK 格式
client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# 系统提示词：约束 LLM 的输出格式和行为
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

# 对话历史，保持上下文连贯（最多保留最近 20 轮）
conversation_history: list[dict] = []


async def chat(user_text: str) -> dict:
    """
    与 DeepSeek LLM 对话

    参数：
      user_text: 用户说的话（STT 识别结果或直接输入的文字）

    返回：
      dict，格式为 {"reply": "回复文字", "pc_command": null 或 命令对象}

    如果 LLM 返回的不是合法 JSON（偶尔会发生），
    会把整个回复当作 reply，pc_command 设为 null。
    """
    # 追加用户消息到历史
    conversation_history.append({"role": "user", "content": user_text})

    # 只保留最近 20 轮对话，避免 token 超限
    if len(conversation_history) > 20:
        conversation_history[:] = conversation_history[-20:]

    # 调用 DeepSeek API
    response = await client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history,
        temperature=0.7,   # 适度随机性，让回复自然
        max_tokens=300,    # 限制回复长度，语音播报不宜太长
    )

    # 提取 LLM 回复
    assistant_msg = response.choices[0].message.content.strip()
    conversation_history.append({"role": "assistant", "content": assistant_msg})

    # 尝试解析 JSON 格式的回复
    try:
        result = json.loads(assistant_msg)
    except json.JSONDecodeError:
        # LLM 偶尔不按格式输出，兜底处理
        result = {"reply": assistant_msg, "pc_command": None}

    return result
