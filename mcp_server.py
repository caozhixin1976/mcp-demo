"""
MCP Server — ERP库存查询服务
提供3个工具供 Claude 按需调用

运行方式：由 agent.py 通过 stdio 自动启动，无需手动运行
"""
import asyncio
import mysql.connector
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── 数据库连接 ──────────────────────────────────────────────
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "P@ssw0rd",
    "database": "erp_demo"
}

def query_db(sql: str, params: tuple = ()):
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ── MCP Server ──────────────────────────────────────────────
app = Server("erp-inventory-server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_inventory_summary",
            description="查询库存总览：返回各分类（原料/辅料/成品/备件）的库存数量和低库存预警统计",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="query_low_stock_items",
            description="查询低库存物料清单：返回所有库存量低于安全库存的物料详情",
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="search_item",
            description="按关键词搜索物料：根据物料名称或编号查询库存详情",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如物料名称或编号"}
                },
                "required": ["keyword"]
            }
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:

    if name == "query_inventory_summary":
        rows = query_db("""
            SELECT
                category                                    AS 分类,
                COUNT(*)                                    AS 物料总数,
                SUM(CASE WHEN quantity < safety_stock THEN 1 ELSE 0 END) AS 低库存数量,
                ROUND(SUM(quantity), 1)                     AS 库存总量
            FROM inventory
            GROUP BY category
            ORDER BY 低库存数量 DESC
        """)
        lines = ["【库存总览】"]
        for r in rows:
            alert = f" ⚠️ {r['低库存数量']}项缺货" if r['低库存数量'] > 0 else " ✓"
            lines.append(f"  {r['分类']}: {r['物料总数']}种物料，总量{r['库存总量']}{alert}")
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "query_low_stock_items":
        rows = query_db("""
            SELECT item_code, item_name, category, quantity, safety_stock, unit, warehouse
            FROM inventory
            WHERE quantity < safety_stock
            ORDER BY (quantity / safety_stock) ASC
        """)
        if not rows:
            return [TextContent(type="text", text="当前所有物料库存充足，无预警。")]
        lines = [f"【低库存预警】共 {len(rows)} 项："]
        for r in rows:
            ratio = r['quantity'] / r['safety_stock'] * 100
            lines.append(
                f"  [{r['category']}] {r['item_code']} {r['item_name']}: "
                f"当前{r['quantity']}{r['unit']} / 安全库存{r['safety_stock']}{r['unit']} "
                f"({ratio:.0f}%) — {r['warehouse']}"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "search_item":
        keyword = arguments.get("keyword", "")
        rows = query_db("""
            SELECT item_code, item_name, category, quantity, safety_stock, unit, warehouse
            FROM inventory
            WHERE item_name LIKE %s OR item_code LIKE %s
        """, (f"%{keyword}%", f"%{keyword}%"))
        if not rows:
            return [TextContent(type="text", text=f"未找到包含「{keyword}」的物料。")]
        lines = [f"【搜索结果】关键词「{keyword}」，共 {len(rows)} 条："]
        for r in rows:
            status = "⚠️低库存" if r['quantity'] < r['safety_stock'] else "✓正常"
            lines.append(
                f"  {r['item_code']} {r['item_name']}: "
                f"{r['quantity']}{r['unit']}（安全库存:{r['safety_stock']}）{status}"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    return [TextContent(type="text", text=f"未知工具: {name}")]

# ── 启动 ────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
