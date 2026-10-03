"""宠物店业务工具 MCP Server（streamable_http）。

暴露工具：
1. check_appointment_slots(date)                        查询可预约时段
2. create_appointment(pet_name, service, datetime)      创建预约
3. check_product_stock(product_name)                    查询产品库存
4. get_pet_medical_record(pet_id)                       查询宠物病历

启动：
    python -m mcp_server.server
默认监听 127.0.0.1:8000，MCP 端点为 http://localhost:8000/mcp
"""

from __future__ import annotations

import datetime as dt
import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "pet_store",
    host="127.0.0.1",
    port=8000,
    streamable_http_path="/mcp",
)

# ── 演示用内存数据 ──────────────────────────────────────────────────────────

_SLOTS = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"]
_APPOINTMENTS: dict[str, list[dict]] = {}

_PRODUCT_STOCK: dict[str, int] = {
    "福来恩滴剂": 12,
    "拜宠清驱虫片": 0,
    "猫藓喷剂": 7,
    "复合益生菌": 20,
    "关节软骨素": 5,
    "低敏处方粮": 3,
}

_MEDICAL_RECORDS: dict[str, dict] = {
    "PET-001": {
        "pet_id": "PET-001",
        "name": "旺财",
        "species": "犬",
        "breed": "金毛",
        "age": 3,
        "weight": "28kg",
        "allergies": ["鸡肉"],
        "history": ["2025-06-01 皮肤过敏（跳蚤过敏性皮炎）", "2025-08-12 体内外驱虫"],
    },
    "PET-002": {
        "pet_id": "PET-002",
        "name": "咪咪",
        "species": "猫",
        "breed": "英短",
        "age": 2,
        "weight": "4.5kg",
        "allergies": [],
        "history": ["2025-07-20 猫藓（真菌感染）"],
    },
}


# ── 工具定义 ────────────────────────────────────────────────────────────────

@mcp.tool()
def check_appointment_slots(date: str) -> str:
    """查询指定日期（格式 YYYY-MM-DD）可预约的时段。"""
    try:
        dt.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return "日期格式错误，请使用 YYYY-MM-DD。"

    booked = {a["datetime"][11:16] for a in _APPOINTMENTS.get(date, [])}
    free = [slot for slot in _SLOTS if slot not in booked]
    if not free:
        return f"{date} 已无可预约时段。"
    return f"{date} 可预约时段：{'、'.join(free)}"


@mcp.tool()
def create_appointment(pet_name: str, service: str, datetime: str) -> str:
    """为宠物创建预约。datetime 格式 YYYY-MM-DD HH:MM。

    service 例如：疫苗、驱虫、洗浴美容、体检、寄养。
    """
    try:
        dt.datetime.strptime(datetime, "%Y-%m-%d %H:%M")
    except ValueError:
        return "时间格式错误，请使用 YYYY-MM-DD HH:MM。"

    date, time = datetime.split(" ")
    if time not in _SLOTS:
        return f"{time} 不在可预约时段内（{'、'.join(_SLOTS)}）。"

    day_slots = _APPOINTMENTS.setdefault(date, [])
    if any(a["datetime"] == datetime for a in day_slots):
        return f"{datetime} 该时段已被预约，请选择其他时段。"

    appointment = {
        "id": f"APPT-{date.replace('-', '')}-{len(day_slots) + 1:03d}",
        "pet_name": pet_name,
        "service": service,
        "datetime": datetime,
    }
    day_slots.append(appointment)
    return (
        f"预约成功！宠物：{pet_name}，服务：{service}，时间：{datetime}，"
        f"预约号：{appointment['id']}。"
    )


@mcp.tool()
def check_product_stock(product_name: str) -> str:
    """查询宠物产品的库存数量。"""
    for name, qty in _PRODUCT_STOCK.items():
        if product_name in name or name in product_name:
            status = "（缺货，可预订）" if qty == 0 else ""
            return f"{name} 当前库存 {qty} 件。{status}"
    return f"未找到产品「{product_name}」。在售产品：{'、'.join(_PRODUCT_STOCK)}"


@mcp.tool()
def get_pet_medical_record(pet_id: str) -> str:
    """按宠物 ID 查询病历（品种、年龄、过敏史、既往病史）。"""
    record = _MEDICAL_RECORDS.get(pet_id)
    if record is None:
        return f"未找到宠物 {pet_id} 的病历。可用示例 ID：{'、'.join(_MEDICAL_RECORDS)}"
    return json.dumps(record, ensure_ascii=False)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
