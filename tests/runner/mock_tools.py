"""Deterministic mock MCP tools for offline, reproducible evaluation.

Mirrors the schema/name of ``petdoctor/mcp_server/server.py`` (medical record only)
so tool-call assertions can run without starting a server. No business code changes.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

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


def reset() -> None:
    """Reset mutable state so repeated runs stay independent."""
    return None


@tool("get_pet_medical_record", description="按宠物 ID 查询病历（品种、年龄、过敏史、既往病史）。")
def get_pet_medical_record(pet_id: str) -> str:
    record = _MEDICAL_RECORDS.get(pet_id)
    if record is None:
        return f"未找到宠物 {pet_id} 的病历。可用示例 ID：{'、'.join(_MEDICAL_RECORDS)}"
    return json.dumps(record, ensure_ascii=False)


MEDICAL_TOOLS = [get_pet_medical_record]
ALL_TOOLS = [*MEDICAL_TOOLS]

