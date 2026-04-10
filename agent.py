"""
Deep Agent 主程序
演示完整流程：SKILL.md发现 → 技能匹配 → Claude调用 → MCP工具执行

运行方式：python3 agent.py
"""
import asyncio
import os
import sys
from pathlib import Path
import yaml
import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not ANTHROPIC_API_KEY:
    raise ValueError("请设置环境变量 ANTHROPIC_API_KEY，例如：export ANTHROPIC_API_KEY='sk-ant-...'")
SKILLS_DIR = Path(__file__).parent / "skills"
MCP_SERVER  = Path(__file__).parent / "mcp_server.py"

# ══════════════════════════════════════════════════════════════
# STEP 1: SKILL.md 发现与加载
# 关键设计：初始只读 frontmatter（描述），匹配后才加载完整指令
# ══════════════════════════════════════════════════════════════

def load_skill_metadata() -> list[dict]:
    """扫描 skills/ 目录，只读取每个 SKILL.md 的 frontmatter（元数据）"""
    skills = []
    for skill_file in SKILLS_DIR.glob("*/SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        # 解析 YAML frontmatter（--- 之间的部分）
        if content.startswith("---"):
            end = content.index("---", 3)
            meta = yaml.safe_load(content[3:end])
            meta["_file"] = skill_file
            meta["_loaded"] = False  # 完整内容尚未加载
            skills.append(meta)
    return skills

def load_full_skill(skill: dict) -> str:
    """按需加载：匹配到技能后，才读取完整 SKILL.md 内容"""
    content = skill["_file"].read_text(encoding="utf-8")
    # 去掉 frontmatter，只返回正文指令
    end = content.index("---", 3)
    return content[end + 3:].strip()

def match_skill(user_input: str, skills: list[dict]) -> dict | None:
    """
    技能路由：让 Claude 读技能描述，自主决定调用哪个技能
    这是 SKILL.md 架构的核心：LLM 本身就是路由器
    """
    # 构建技能目录（仅描述，不含完整指令）
    skill_catalog = "\n".join([
        f"- [{s['name']}]: {s['description']}"
        for s in skills
    ])

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",  # 路由用轻量模型，省钱
        max_tokens=50,
        system="你是技能路由器。根据用户输入，从技能列表中选择最匹配的技能名称。只输出技能名称，不要任何解释。如果没有匹配的技能，输出 none",
        messages=[{
            "role": "user",
            "content": f"用户输入：{user_input}\n\n可用技能：\n{skill_catalog}"
        }]
    )
    matched_name = resp.content[0].text.strip().lower()
    for s in skills:
        if s["name"].lower() == matched_name:
            return s
    return None

# ══════════════════════════════════════════════════════════════
# STEP 2: MCP 工具调用循环
# ══════════════════════════════════════════════════════════════

async def run_agent(user_input: str, system_prompt: str, mcp_tools: list, session: ClientSession) -> str:
    """
    Agent 主循环：
    1. 调用 Claude（带 MCP 工具列表）
    2. 如果 Claude 要调用工具 → 执行工具 → 把结果还给 Claude
    3. 重复直到 Claude 给出最终回答
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # 把 MCP 工具格式转换为 Anthropic API 格式
    claude_tools = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.inputSchema
        }
        for t in mcp_tools
    ]

    messages = [{"role": "user", "content": user_input}]

    print(f"\n{'─'*50}")
    print(f"[Agent] 开始推理，可用工具：{[t['name'] for t in claude_tools]}")

    # 工具调用循环（最多5轮，防止死循环）
    for turn in range(5):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            tools=claude_tools,
            messages=messages
        )

        # Claude 给出了最终回答
        if resp.stop_reason == "end_turn":
            final_text = "".join(
                b.text for b in resp.content if hasattr(b, "text")
            )
            return final_text

        # Claude 要调用工具
        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    print(f"\n┌─ 第{turn+1}轮工具调用 {'─'*35}")
                    print(f"│  工具名称：{block.name}")
                    print(f"│  传入参数：{block.input if block.input else '（无参数）'}")
                    print(f"├─ MCP Server 返回 {'─'*35}")
                    result = await session.call_tool(block.name, block.input)
                    result_text = result.content[0].text if result.content else "无返回"
                    for line in result_text.splitlines():
                        print(f"│  {line}")
                    print(f"└{'─'*50}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text
                    })

            messages.append({"role": "user", "content": tool_results})

    return "（Agent 达到最大推理轮数）"

# ══════════════════════════════════════════════════════════════
# STEP 3: 主入口
# ══════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  MCP Demo — Deep Agent + SKILL.md")
    print("  数据来源：MySQL erp_demo 库存表")
    print("=" * 60)

    # 加载技能目录（只有描述，不含完整指令）
    skills = load_skill_metadata()
    print(f"\n[SKILL] 发现 {len(skills)} 个技能：")
    for s in skills:
        print(f"  - {s['name']}: {s['description'][:40]}...")

    # 启动 MCP Server（subprocess via stdio）
    server_params = StdioServerParameters(
        command="python3",
        args=[str(MCP_SERVER)]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            print(f"\n[MCP] Server 已连接，可用工具：{[t.name for t in mcp_tools]}")

            # 交互循环
            print("\n输入你的问题（输入 exit 退出）：")
            while True:
                try:
                    user_input = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n退出")
                    break

                if user_input.lower() in ("exit", "quit", "q"):
                    break
                if not user_input:
                    continue

                # ① 技能路由：LLM读描述，选择技能
                print(f"\n[SKILL] 正在匹配技能...")
                matched = match_skill(user_input, skills)

                if not matched:
                    print("[SKILL] 未匹配到合适的技能，使用通用模式回答")
                    system_prompt = "你是一个ERP系统助手。"
                else:
                    print(f"[SKILL] 匹配到技能: {matched['name']}")
                    # ② 按需加载：匹配后才读取完整 SKILL.md
                    full_instructions = load_full_skill(matched)
                    system_prompt = full_instructions
                    print(f"[SKILL] 已加载完整技能指令（{len(full_instructions)}字符）")

                # ③ 运行 Agent（Claude + MCP 工具调用循环）
                answer = await run_agent(user_input, system_prompt, mcp_tools, session)

                print(f"\n{'═'*60}")
                print("【Agent 回答】")
                print(answer)
                print('═'*60)

if __name__ == "__main__":
    asyncio.run(main())
