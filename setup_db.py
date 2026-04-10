"""
初始化 MySQL 测试数据库
模拟钢厂ERP库存数据
运行一次即可：python3 setup_db.py
"""
import mysql.connector

conn = mysql.connector.connect(
    host="localhost", user="root", password="P@ssw0rd"
)
cur = conn.cursor()

# 建库
cur.execute("CREATE DATABASE IF NOT EXISTS erp_demo DEFAULT CHARACTER SET utf8mb4")
cur.execute("USE erp_demo")

# 建表
cur.execute("DROP TABLE IF EXISTS inventory")
cur.execute("""
CREATE TABLE inventory (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    item_code   VARCHAR(20)  NOT NULL COMMENT '物料编号',
    item_name   VARCHAR(100) NOT NULL COMMENT '物料名称',
    category    VARCHAR(50)  NOT NULL COMMENT '分类',
    quantity    DECIMAL(10,2) NOT NULL COMMENT '当前库存量',
    safety_stock DECIMAL(10,2) NOT NULL COMMENT '安全库存量',
    unit        VARCHAR(10)  NOT NULL COMMENT '单位',
    warehouse   VARCHAR(50)  NOT NULL COMMENT '仓库'
)
""")

# 插入模拟数据（钢厂库存场景）
rows = [
    ("RAW-001", "热轧板卷Q235B",    "原料",   1200.0,  500.0,  "吨", "原料仓A"),
    ("RAW-002", "冷轧板卷SPCC",     "原料",    320.0,  400.0,  "吨", "原料仓A"),  # 低库存
    ("RAW-003", "镀锌板DX51D",      "原料",    880.0,  300.0,  "吨", "原料仓B"),
    ("RAW-004", "不锈钢板304",       "原料",     85.0,  200.0,  "吨", "原料仓B"),  # 低库存
    ("RAW-005", "硅钢片B50A600",    "原料",    460.0,  200.0,  "吨", "原料仓A"),
    ("AUX-001", "氩气",             "辅料",    150.0,   50.0,  "瓶", "气体站"),
    ("AUX-002", "氮气",             "辅料",     30.0,   80.0,  "瓶", "气体站"),  # 低库存
    ("AUX-003", "切削液",           "辅料",    200.0,  100.0,  "桶", "辅料仓"),
    ("AUX-004", "砂轮片",           "辅料",    500.0,  200.0,  "片", "辅料仓"),
    ("AUX-005", "焊丝ER70S-6",      "辅料",     18.0,   30.0, "箱", "辅料仓"),   # 低库存
    ("FIN-001", "成品板H型钢200",    "成品",   2300.0,  800.0,  "吨", "成品仓"),
    ("FIN-002", "成品角钢50×5",     "成品",    670.0,  300.0,  "吨", "成品仓"),
    ("FIN-003", "成品槽钢16#",      "成品",    120.0,  400.0,  "吨", "成品仓"),  # 低库存
    ("FIN-004", "成品圆钢φ20",      "成品",   1100.0,  500.0,  "吨", "成品仓"),
    ("SPA-001", "轴承6205",         "备件",    230.0,  100.0,  "个", "备件库"),
    ("SPA-002", "液压缸密封件",      "备件",     12.0,   20.0,  "套", "备件库"),  # 低库存
    ("SPA-003", "变频器ABB-22KW",   "备件",      5.0,    2.0,  "台", "备件库"),
    ("SPA-004", "PLC模块S7-300",    "备件",      2.0,    3.0,  "块", "备件库"),  # 低库存
    ("SPA-005", "电机7.5KW",        "备件",      8.0,    5.0,  "台", "备件库"),
    ("SPA-006", "减速机配件",        "备件",     25.0,   10.0,  "套", "备件库"),
]

cur.executemany(
    "INSERT INTO inventory (item_code,item_name,category,quantity,safety_stock,unit,warehouse) VALUES (%s,%s,%s,%s,%s,%s,%s)",
    rows
)
conn.commit()

print(f"✓ 数据库 erp_demo 初始化完成，共插入 {len(rows)} 条库存记录")
print("  低库存物料（数量 < 安全库存）：")
cur.execute("SELECT item_code, item_name, quantity, safety_stock, unit FROM inventory WHERE quantity < safety_stock")
for r in cur.fetchall():
    print(f"    {r[0]} {r[1]}: 当前{r[2]}{r[4]}，安全库存{r[3]}{r[4]}")

cur.close()
conn.close()
