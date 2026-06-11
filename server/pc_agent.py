"""
PC Agent - 接收云端 AI 命令并控制电脑

功能：
  1. 通过 WebSocket 连接云端服务，等待命令下发
  2. 收到命令后执行对应的电脑操作
  3. 将执行结果返回给云端服务

支持的命令：
  - open_url: 打开网页
  - search: 用默认浏览器搜索
  - open_file: 打开本地文件
  - summarize_file: 读取文件内容并返回摘要

安全设计：
  - 只允许白名单内的动作
  - 不允许删除文件、发送邮件、付款等危险操作
  - 所有操作都有日志输出

用法：
  python pc_agent.py

环境变量：
  WS_URL: 云端服务地址，默认 ws://localhost:8000/ws/pc_agent
  部署后改为 ws://你的服务器IP:8000/ws/pc_agent
"""
import asyncio
import json
import os
import subprocess
import webbrowser
import websockets


# 云端服务 WebSocket 地址
WS_URL = os.getenv("WS_URL", "ws://localhost:8000/ws/pc_agent")


async def handle_command(command: dict) -> str:
    """
    执行 PC 命令并返回结果

    参数：
      command: {"action": "动作名", "params": {"参数": "值"}}

    返回：
      执行结果的文字描述
    """
    action = command.get("action", "")
    params = command.get("params", {})

    print(f"[执行] action={action}, params={params}")

    if action == "open_url":
        url = params.get("url", "")
        if not url:
            return "缺少 URL 参数"
        webbrowser.open(url)
        return f"已打开网页: {url}"

    elif action == "search":
        query = params.get("query", "")
        if not query:
            return "缺少搜索关键词"
        # 后台搜索，抓取结果摘要返回，不弹浏览器
        try:
            import urllib.request
            import urllib.parse
            import re
            search_url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"
            req = urllib.request.Request(search_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            # 提取搜索结果摘要（简单正则提取）
            abstracts = re.findall(r'<span class="content-right_[^"]*">(.*?)</span>', html)
            if not abstracts:
                abstracts = re.findall(r'<span class=".*?">(.*?)</span>', html)
            # 清理 HTML 标签
            results = []
            for ab in abstracts[:3]:
                clean = re.sub(r'<[^>]+>', '', ab).strip()
                if len(clean) > 10:
                    results.append(clean)
            if results:
                return f"搜索「{query}」的结果：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(results))
            else:
                # 兜底：打开浏览器让用户自己看
                webbrowser.open(search_url)
                return f"没有提取到摘要，已打开浏览器搜索：{query}"
        except Exception as e:
            webbrowser.open(f"https://www.baidu.com/s?wd={query}")
            return f"后台搜索失败({e})，已打开浏览器"

    elif action == "open_file":
        path = params.get("path", "")
        if not path:
            return "缺少文件路径"
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        os.startfile(path)
        return f"已打开文件: {path}"

    elif action == "summarize_file":
        path = params.get("path", "")
        if not path:
            return "缺少文件路径"
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read(2000)
            return f"文件内容前2000字：\n{content}"
        except Exception as e:
            return f"读取文件失败: {e}"

    else:
        return f"不支持的命令: {action}"


async def run():
    """主循环：连接云端服务，等待并执行命令"""
    while True:
        try:
            print(f"[PC Agent] 连接到 {WS_URL} ...")
            async with websockets.connect(WS_URL) as ws:
                print("[PC Agent] 已连接，等待命令...\n")

                while True:
                    message = await ws.recv()
                    data = json.loads(message)

                    if data.get("type") == "pc_command":
                        command = data.get("command", {})
                        client_id = data.get("client_id")
                        print(f"[收到命令] {command}")

                        # 执行命令
                        result = await handle_command(command)
                        print(f"[执行结果] {result}\n")

                        # 返回结果给云端
                        await ws.send(json.dumps({
                            "type": "result",
                            "client_id": client_id,
                            "result": result
                        }, ensure_ascii=False))

                    elif data.get("type") == "pong":
                        pass

        except websockets.exceptions.ConnectionClosed:
            print("[PC Agent] 连接断开，5秒后重连...")
            await asyncio.sleep(5)
        except ConnectionRefusedError:
            print("[PC Agent] 服务器未启动，5秒后重试...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"[PC Agent] 错误: {e}，5秒后重连...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    print("=" * 40)
    print("  智能枕头 PC Agent")
    print("  等待云端 AI 下发电脑控制命令")
    print("=" * 40)
    print()
    asyncio.run(run())
