# MCP Demo — Deep Agent + SKILL.md 架构文档

> 项目路径：`/root/caozhixin/mcp-demo/`
> 创建日期：2026-04-10

---

## 一、整体架构图

```
你的自然语言输入
        │
        ▼
┌─────────────────────────────────┐
│       SKILL.md 技能路由          │  ← 扫描 skills/ 目录，只读描述（节省Token）
│  用 Claude Haiku 匹配最合适技能  │
└────────────────┬────────────────┘
                 │ 匹配后
                 ▼
┌─────────────────────────────────┐
│       按需加载完整技能指令        │  ← 此时才读 SKILL.md 正文
│  构建专业化 System Prompt        │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│       Claude Sonnet（主模型）    │  ← 携带工具列表 + 技能指令
│  自主决定调用哪个工具、调几次    │
└────────────────┬────────────────┘
                 │ Tool Call
                 ▼
┌─────────────────────────────────┐
│         MCP Server              │  ← stdio 子进程通信
│  query_inventory_summary        │
│  query_low_stock_items          │
│  search_item                    │
└────────────────┬────────────────┘
                 │ SQL查询
                 ▼
┌─────────────────────────────────┐
│     MySQL erp_demo 数据库        │  ← 20条模拟钢厂库存数据
│     inventory 表                │
└─────────────────────────────────┘
                 │ 返回数据
                 ▼
        结构化分析报告输出
```

---

## 二、目录结构

```
mcp-demo/
├── agent.py                        # Deep Agent 主程序
├── mcp_server.py                   # MCP Server（库存查询工具）
├── setup_db.py                     # 初始化 MySQL 测试数据
├── doc/
│   └── demo架构.md                 # 本文档
└── skills/                         # 技能目录（SKILL.md 核心所在）
    ├── inventory_analysis/
    │   └── SKILL.md                # 库存分析技能
    └── purchase_risk/
        └── SKILL.md                # 采购风险技能
```

---

## 三、核心模块说明

### 模块 1：SKILL.md 技能文件

每个技能是一个独立文件夹下的 `SKILL.md`，结构分**两段**，中间用 `---` 分隔：

```
---                               ← 第一段开始（frontmatter 元数据）
name: inventory-analysis          ← 技能唯一名称
description: 分析仓库库存状况...  ← ★ 路由的关键，LLM 读这行决定是否调用
triggers: [库存, 缺货, 补货...]   ← 触发关键词（辅助说明）
negative: [采购单, 审批, 合同]    ← 排除关键词（防止误匹配）
---                               ← 第一段结束

# 库存分析技能                    ← 第二段（完整指令，匹配前不加载）
## 你的角色
## 分析流程
## 输出格式（必须遵守）
```

**为什么要分两段？—— 渐进式披露**

| 时机 | 加载内容 | Token 消耗 |
|------|---------|-----------|
| 启动时 | 只读 description（1行描述） | 极少 |
| 匹配后 | 加载完整指令（全文） | 按需 |

如果有 50 个技能，每次只加载 50 行描述做路由，而不是把 50 个完整技能全部塞给 Claude，既节省 Token，又避免上下文污染。这是本架构最核心的设计思想。

---

### 模块 2：技能路由（agent.py — match_skill）

```
用户输入
    │
    ▼
构建技能目录（仅描述，不含完整指令）
    │
    ▼
调用 Claude Haiku（轻量、便宜）
    │  系统提示："你是技能路由器，选择最匹配的技能名称，只输出名称"
    ▼
返回技能名称（如 "inventory-analysis"）
    │
    ▼
按需加载该技能的完整 SKILL.md 正文
```

**传统做法 vs SKILL.md 做法对比：**

| 传统做法 | SKILL.md 做法 |
|---------|--------------|
| 用关键词规则匹配（`if "库存" in text`） | LLM 读描述自主判断 |
| 规则多了维护困难 | 加技能只需新建文件夹 |
| 无法理解语义 | 能理解"盘点一下仓库"≈"库存分析" |
| 用大模型做路由，贵 | 用 Haiku（小模型）路由，省钱 |

**扩展方式**：在 `skills/` 下新建文件夹和 SKILL.md，路由自动发现，无需修改 `agent.py` 任何代码。

---

### 模块 3：MCP Server（mcp_server.py）

**MCP 是什么？**

MCP（Model Context Protocol）是 Anthropic 制定的开放标准协议，专门解决：**AI 模型如何以统一方式调用外部工具**。

- 没有 MCP：每个工具单独写对接代码，每换一个 AI 模型就要重写
- 有了 MCP：工具写一次，所有支持 MCP 的 AI 模型都能调用

**Server 结构：**

```python
@app.list_tools()       # ← 告诉 Claude "我有哪些工具"
async def list_tools():
    return [工具A, 工具B, 工具C]

@app.call_tool()        # ← Claude 要调工具时，执行这里
async def call_tool(name, arguments):
    if name == "query_inventory_summary":
        # 查 MySQL，返回结果
```

**本 Demo 提供的工具：**

| 工具名 | 功能 | 返回内容 |
|--------|------|---------|
| `query_inventory_summary` | 库存总览 | 各分类数量 + 低库存统计 |
| `query_low_stock_items` | 低库存明细 | 低于安全库存的物料列表 |
| `search_item(keyword)` | 物料搜索 | 按名称/编号搜索结果 |

**为什么用 stdio 通信？**

```
agent.py（父进程）
    │  启动子进程
    ▼
mcp_server.py（子进程）
    ├── 父进程写入请求 → 子进程从 stdin 读取
    └── 子进程写入结果 → 父进程从 stdout 读取
```

| 通信方式 | 特点 | 适用场景 |
|---------|------|---------|
| **stdio（本 Demo）** | 进程内通信，零网络开销，延迟极低 | 内网部署、单机运行 |
| HTTP + SSE | 网络通信，支持远程调用 | 分布式、多人共享 |

内网场景选 stdio：不需要开端口，不需要网络配置，直接跑。

---

### 模块 4：Agent 工具调用循环（agent.py — run_agent）

关键是理解 **`stop_reason`** 这个信号——Claude 用它告诉 agent.py 下一步该做什么。

**完整循环示意：**

```
① 你问："有哪些物料低于安全库存？"
        │
        ▼
② Claude 收到：问题 + 技能指令 + 工具列表
   Claude 回复：stop_reason = "tool_use"
   （意思是："我需要调工具，先别结束"）
        │
        ▼
③ agent.py 看到 tool_use，执行工具：
   → session.call_tool("query_inventory_summary")
   → MCP Server 查 MySQL，返回数据
        │
        ▼
④ 把工具结果塞回消息历史，再次调用 Claude
   Claude 回复：stop_reason = "end_turn"
   （意思是："我拿到数据了，给你最终答案"）
        │
        ▼
⑤ 输出结构化分析报告
```

**两个关键信号：**

| stop_reason | 含义 | agent.py 的动作 |
|------------|------|----------------|
| `"tool_use"` | Claude 要调工具，还没结束 | 执行工具 → 把结果还给 Claude → 继续循环 |
| `"end_turn"` | Claude 给出最终回答 | 提取文本，结束循环，输出给用户 |

**为什么需要"循环"？**

一次复杂问题可能需要多轮工具调用，Claude 自己决定调几次、调哪个：

```
第1轮：调用 query_inventory_summary（看总体）
第2轮：调用 query_low_stock_items（看明细）
第3轮：调用 search_item("不锈钢")（看具体物料）
第4轮：stop_reason = end_turn → 给出综合报告
```

agent.py 只负责"执行工具并把结果还回去"，推理决策完全由 Claude 主导。

---

## 四、数据库说明

数据库：`erp_demo`，表：`inventory`

| 字段 | 类型 | 说明 |
|------|------|------|
| item_code | VARCHAR(20) | 物料编号 |
| item_name | VARCHAR(100) | 物料名称 |
| category | VARCHAR(50) | 分类（原料/辅料/成品/备件）|
| quantity | DECIMAL | 当前库存量 |
| safety_stock | DECIMAL | 安全库存量 |
| unit | VARCHAR(10) | 单位 |
| warehouse | VARCHAR(50) | 仓库名称 |

模拟数据：20条，包含7条低库存预警物料

---

## 五、运行方式

```bash
# 第一次运行：初始化数据库
python3 setup_db.py

# 启动 Agent（需配置 ANTHROPIC_API_KEY）
export ANTHROPIC_API_KEY="sk-ant-..."
python3 agent.py
```

**示例问题：**
- `查询当前库存状态，有哪些物料低于安全库存？`
- `原料仓情况怎么样`
- `搜索一下不锈钢`

---

## 六、核心设计原则

| 原则 | 说明 |
|------|------|
| LLM 即路由器 | 不用规则引擎，让模型读描述自主决策 |
| 渐进式披露 | 初始只加载描述，匹配后才加载完整指令 |
| AI 不做计算 | 本地 SQL 预聚合，AI 只做推理和解释 |
| 写操作隔离 | 本 Demo 只有读操作，写操作需 Human-in-Loop |
