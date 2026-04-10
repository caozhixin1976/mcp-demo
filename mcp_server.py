"""
MCP Server — ERP库存查询服务
提供5个工具供 Claude 按需调用：
  1. query_inventory_summary     库存总览（聚合，永不返回明细）
  2. query_category_detail       分类明细（分层下钻，带 limit）
  3. fuzzy_search_item           多字段模糊搜索（应对用户含糊描述）
  4. query_low_stock_items       低库存预警明细
  5. get_item_by_code            按物料编号精确查询

运行方式：由 agent.py 通过 stdio 自动启动，无需手动运行
"""
import asyncio
import mysql.connector
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

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

app = Server("erp-inventory-server")

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_inventory_summary",
            description=(
                "查询库存总览：返回各分类（原料/辅料/成品/备件）的物料总数、库存总量和低库存预警数。"
                "这是第一层聚合，永不返回明细行。适合用户问'库存整体情况如何'时调用。"
            ),
            inputSchema={"type": "object", "properties": {}, "required": []}
        ),
        Tool(
            name="query_category_detail",
            description=(
                "查询某分类的库存明细：返回指定分类下的物料列表，支持按关键词过滤和数量限制。"
                "这是第二/三层下钻，当用户想看某类物料的具体情况时调用。"
                "limit 默认 15，最大 30，超大数据时让 Claude 自行控制范围。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "分类名称：原料 / 辅料 / 成品 / 备件，留空则查全部"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "可选过滤关键词，匹配物料名称或规格"
                    },
                    "low_stock_only": {
                        "type": "boolean",
                        "description": "是否只返回低库存物料，默认 false"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限，默认 15，最大 30"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="fuzzy_search_item",
            description=(
                "多字段模糊搜索物料：当用户描述含糊（如'304'、'密封件'、'那个皮带'）时使用。"
                "同时搜索规范名称、短描述、规格型号、别名四个字段，返回最多8条候选供用户确认。"
                "比 query_category_detail 更适合用户说不清楚物料代码时的场景。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "用户给出的模糊描述，如'304'、'密封'、'ABB变频'"
                    }
                },
                "required": ["keyword"]
            }
        ),
        Tool(
            name="query_low_stock_items",
            description="查询低库存预警明细：返回所有库存量低于安全库存的物料，按紧急程度排序。",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "可选，只看某分类的低库存，留空则全部分类"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_item_by_code",
            description="按物料编号精确查询单条物料的完整信息，适合用户已明确物料代码的情况。",
            inputSchema={
                "type": "object",
                "properties": {
                    "item_code": {
                        "type": "string",
                        "description": "物料编号，如 RAW-004、SPA-002"
                    }
                },
                "required": ["item_code"]
            }
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:

    # ── 工具1：库存总览（聚合，不返回明细）─────────────────────────
    if name == "query_inventory_summary":
        rows = query_db("""
            SELECT
                category                                                AS 分类,
                COUNT(*)                                                AS 物料总数,
                SUM(CASE WHEN quantity < safety_stock THEN 1 ELSE 0 END) AS 低库存数,
                ROUND(SUM(quantity), 1)                                 AS 库存总量
            FROM inventory
            GROUP BY category
            ORDER BY 低库存数 DESC
        """)
        total_items = sum(r['物料总数'] for r in rows)
        total_alert = sum(r['低库存数'] for r in rows)
        lines = [f"【库存总览】共 {total_items} 种物料，其中 {total_alert} 项低于安全库存"]
        lines.append("─" * 45)
        for r in rows:
            alert = f" ⚠️ {r['低库存数']}项低库存" if r['低库存数'] > 0 else " ✓"
            lines.append(
                f"  {r['分类']:3s}：{r['物料总数']:2d}种物料  总量{r['库存总量']}{alert}"
            )
        lines.append("─" * 45)
        lines.append("如需查看某分类明细，请告知（如：'看一下备件详情'）")
        return [TextContent(type="text", text="\n".join(lines))]

    # ── 工具2：分类明细（分层下钻）──────────────────────────────────
    elif name == "query_category_detail":
        category     = arguments.get("category", "")
        keyword      = arguments.get("keyword", "")
        low_only     = arguments.get("low_stock_only", False)
        limit        = min(int(arguments.get("limit", 15)), 30)

        conditions = []
        params = []
        if category:
            conditions.append("category = %s")
            params.append(category)
        if keyword:
            conditions.append("(item_name LIKE %s OR spec_model LIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if low_only:
            conditions.append("quantity < safety_stock")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        count_sql = f"SELECT COUNT(*) AS cnt FROM inventory {where}"
        total = query_db(count_sql, tuple(params))[0]['cnt']

        params.append(limit)
        rows = query_db(
            f"""SELECT item_code, item_name, spec_model, category,
                       quantity, safety_stock, unit, warehouse
                FROM inventory {where}
                ORDER BY (quantity/safety_stock) ASC
                LIMIT %s""",
            tuple(params)
        )

        tag = f"[{category}]" if category else "[全部]"
        kw_tag = f" 关键词「{keyword}」" if keyword else ""
        lo_tag = " 仅低库存" if low_only else ""
        lines = [
            f"【{tag}明细{kw_tag}{lo_tag}】共 {total} 条，显示前 {len(rows)} 条",
            "─" * 55
        ]
        for r in rows:
            ratio = r['quantity'] / r['safety_stock'] * 100
            status = f"⚠️{ratio:.0f}%" if r['quantity'] < r['safety_stock'] else f"✓{ratio:.0f}%"
            lines.append(
                f"  {r['item_code']}  {r['item_name']}  {r['spec_model'] or ''}\n"
                f"    库存:{r['quantity']}{r['unit']}  安全:{r['safety_stock']}{r['unit']}  {status}  {r['warehouse']}"
            )
        if total > limit:
            lines.append(f"─ 还有 {total - limit} 条未显示，可继续缩小范围或指定物料编号查询 ─")
        return [TextContent(type="text", text="\n".join(lines))]

    # ── 工具3：多字段模糊搜索（应对含糊描述）────────────────────────
    elif name == "fuzzy_search_item":
        keyword = arguments.get("keyword", "").strip()
        if not keyword:
            return [TextContent(type="text", text="请提供搜索关键词。")]

        # 同时搜索4个字段：规范名称、短描述、规格型号、别名
        rows = query_db("""
            SELECT item_code, item_name, short_desc, spec_model, alias,
                   category, quantity, safety_stock, unit, warehouse
            FROM inventory
            WHERE item_name  LIKE %s
               OR short_desc LIKE %s
               OR spec_model LIKE %s
               OR alias      LIKE %s
            ORDER BY
                CASE WHEN item_name LIKE %s THEN 0
                     WHEN short_desc LIKE %s THEN 1
                     WHEN spec_model LIKE %s THEN 2
                     ELSE 3 END,
                (quantity/safety_stock) ASC
            LIMIT 8
        """, (f"%{keyword}%",) * 4 + (f"%{keyword}%",) * 3)

        if not rows:
            return [TextContent(type="text", text=f"未找到与「{keyword}」匹配的物料。请尝试其他关键词。")]

        lines = [
            f"【模糊搜索】关键词「{keyword}」，找到 {len(rows)} 条候选：",
            "─" * 55,
            "（请告知是哪一条，或补充规格/仓库信息进一步确认）"
        ]
        for i, r in enumerate(rows, 1):
            status = "⚠️低库存" if r['quantity'] < r['safety_stock'] else "✓正常"
            matched_field = ""
            kw_lower = keyword.lower()
            if kw_lower in (r['item_name'] or "").lower():
                matched_field = "命中:名称"
            elif kw_lower in (r['short_desc'] or "").lower():
                matched_field = f"命中:俗称({r['short_desc']})"
            elif kw_lower in (r['spec_model'] or "").lower():
                matched_field = f"命中:规格({r['spec_model']})"
            elif kw_lower in (r['alias'] or "").lower():
                matched_field = f"命中:别名"
            lines.append(
                f"  ⑩{i} {r['item_code']}  {r['item_name']}"
                f"  [{r['spec_model'] or '规格未知'}]"
                f"  {r['category']}  {r['warehouse']}"
                f"  库存:{r['quantity']}{r['unit']}  {status}  {matched_field}"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    # ── 工具4：低库存预警明细 ────────────────────────────────────────
    elif name == "query_low_stock_items":
        category = arguments.get("category", "")
        where = "WHERE quantity < safety_stock"
        params = []
        if category:
            where += " AND category = %s"
            params.append(category)

        rows = query_db(
            f"""SELECT item_code, item_name, category, quantity, safety_stock, unit, warehouse
                FROM inventory {where}
                ORDER BY (quantity/safety_stock) ASC""",
            tuple(params)
        )
        if not rows:
            tag = f"[{category}]" if category else ""
            return [TextContent(type="text", text=f"当前{tag}所有物料库存充足，无预警。")]

        tag = f"[{category}]" if category else ""
        lines = [f"【低库存预警{tag}】共 {len(rows)} 项（按紧急程度排序）：", "─" * 55]
        for r in rows:
            ratio = r['quantity'] / r['safety_stock'] * 100
            lines.append(
                f"  [{r['category']}] {r['item_code']} {r['item_name']}\n"
                f"    当前:{r['quantity']}{r['unit']}  安全:{r['safety_stock']}{r['unit']}"
                f"  占比:{ratio:.0f}%  {r['warehouse']}"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    # ── 工具5：按编号精确查询 ─────────────────────────────────────────
    elif name == "get_item_by_code":
        item_code = arguments.get("item_code", "").strip()
        rows = query_db(
            """SELECT item_code, item_name, short_desc, spec_model, alias,
                      category, quantity, safety_stock, unit, warehouse
               FROM inventory WHERE item_code = %s""",
            (item_code,)
        )
        if not rows:
            return [TextContent(type="text", text=f"未找到物料编号「{item_code}」，请确认编号是否正确。")]
        r = rows[0]
        ratio = r['quantity'] / r['safety_stock'] * 100
        status = f"⚠️低库存（{ratio:.0f}%）" if r['quantity'] < r['safety_stock'] else f"✓正常（{ratio:.0f}%）"
        lines = [
            f"【物料详情】{r['item_code']}",
            f"  规范名称：{r['item_name']}",
            f"  俗称/短描：{r['short_desc'] or '-'}",
            f"  规格型号：{r['spec_model'] or '-'}",
            f"  别名：{r['alias'] or '-'}",
            f"  分类：{r['category']}  仓库：{r['warehouse']}",
            f"  当前库存：{r['quantity']}{r['unit']}",
            f"  安全库存：{r['safety_stock']}{r['unit']}",
            f"  状态：{status}",
        ]
        return [TextContent(type="text", text="\n".join(lines))]

    return [TextContent(type="text", text=f"未知工具: {name}")]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
