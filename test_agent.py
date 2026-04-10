"""
test_agent.py — 三项功能测试（非交互式）
测试1：大数据分层查询
测试2：模糊描述 数字型关键词（304）
测试3：模糊描述 别名口语（黄油）

运行方式：python3 test_agent.py
"""
import asyncio
import os
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

TEST_CASES = [
    {
        "id": 1,
        "label": "大数据分层查询",
        "input": "库存整体情况怎么样？备件这块详细说一下",
    },
    {
        "id": 2,
        "label": "模糊描述 · 数字型关键词",
        "input": "304的库存情况怎么样",
    },
    {
        "id": 3,
        "label": "模糊描述 · 别名口语「黄油」",
        "input": "黄油还有多少",
    },
]

# ── SKILL 加载（与 agent.py 保持一致）────────────────────────────────

def load_skill_metadata() -> list[dict]:
    skills = []
    for skill_file in SKILLS_DIR.glob("*/SKILL.md"):
        content = skill_file.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.index("---", 3)
            meta = yaml.safe_load(content[3:end])
            meta["_file"] = skill_file
            skills.append(meta)
    return skills

def load_full_skill(skill: dict) -> str:
    content = skill["_file"].read_text(encoding="utf-8")
    end = content.index("---", 3)
    return content[end + 3:].strip()

def match_skill(user_input: str, skills: list[dict]) -> dict | None:
    skill_catalog = "\n".join([f"- [{s['name']}]: {s['description']}" for s in skills])
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        system="你是技能路由器。根据用户输入，从技能列表中选择最匹配的技能名称。只输出技能名称，不要任何解释。如果没有匹配的技能，输出 none",
        messages=[{"role": "user", "content": f"用户输入：{user_input}\n\n可用技能：\n{skill_catalog}"}]
    )
    matched_name = resp.content[0].text.strip().lower()
    for s in skills:
        if s["name"].lower() == matched_name:
            return s
    return None

# ── Agent 推理循环（与 agent.py 保持一致）────────────────────────────

async def run_agent(user_input: str, system_prompt: str, mcp_tools: list, session: ClientSession) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    claude_tools = [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in mcp_tools
    ]
    messages = [{"role": "user", "content": user_input}]

    for turn in range(5):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system_prompt,
            tools=claude_tools,
            messages=messages
        )

        if resp.stop_reason == "end_turn":
            return "".join(b.text for b in resp.content if hasattr(b, "text"))

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    print(f"    ┌─ 第{turn+1}轮工具调用")
                    print(f"    │  工具：{block.name}  参数：{block.input if block.input else '(无)'}")
                    result = await session.call_tool(block.name, block.input)
                    result_text = result.content[0].text if result.content else "无返回"
                    for line in result_text.splitlines():
                        print(f"    │  {line}")
                    print(f"    └{'─'*45}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text
                    })
            messages.append({"role": "user", "content": tool_results})

    return "（Agent 达到最大推理轮数）"

# ── 主入口 ──────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("  MCP Demo — 功能测试（3项）")
    print("=" * 65)

    skills = load_skill_metadata()

    server_params = StdioServerParameters(command="python3", args=[str(MCP_SERVER)])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = (await session.list_tools()).tools
            print(f"[MCP] Server 已连接，工具：{[t.name for t in mcp_tools]}\n")

            passed = 0
            for tc in TEST_CASES:
                print(f"{'━'*65}")
                print(f"【测试 {tc['id']}】{tc['label']}")
                print(f"  用户输入：「{tc['input']}」")
                print()

                # 技能路由
                matched = match_skill(tc["input"], skills)
                if matched:
                    print(f"  [SKILL] 命中：{matched['name']}")
                    system_prompt = load_full_skill(matched)
                else:
                    print(f"  [SKILL] 未命中，使用通用模式")
                    system_prompt = "你是一个ERP系统助手。"
                print()

                answer = await run_agent(tc["input"], system_prompt, mcp_tools, session)

                print()
                print(f"  【Agent 回答】")
                for line in answer.splitlines():
                    print(f"  {line}")
                passed += 1
                print()

            print("=" * 65)
            print(f"  测试完成：{passed}/{len(TEST_CASES)} 项通过")
            print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
