"""宠物店业务工具 MCP Server（streamable_http）。

暴露工具：
1. get_pet_medical_record(pet_id)                       查询宠物病历

说明：门店预约（check_appointment_slots / create_appointment）与库存查询
（check_product_stock）功能已下线，相关工具不再提供。

启动：
    python -m petdoctor.mcp_server.server
默认监听 127.0.0.1:8000，MCP 端点为 http://localhost:8000/mcp
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "pet_store",
    host="127.0.0.1",
    port=8000,
    streamable_http_path="/mcp",
)

# ── 演示用内存数据 ──────────────────────────────────────────────────────────

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
