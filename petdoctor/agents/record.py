"""病历查询 Worker：汇总宠物的基本档案、既往问诊记录与外部门诊病历（MCP）。

确定性节点（不依赖 LLM 决策/工具循环）：
- 目标宠物取当前会话已识别的 ``active_pet_id``；
- 档案与既往问诊记录来自长期记忆（PostgresStore）；
- 外部病历来自 MCP 工具 ``get_pet_medical_record``（若可用）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from petdoctor.state import PetClinicState

_medical_tool: Any = None


def configure_tools(tools: list[Any]) -> None:
    """从 MCP 工具中挑出病历查询工具。"""
    global _medical_tool
    _medical_tool = next((t for t in tools if t.name == "get_pet_medical_record"), None)


def _extract_text(raw: Any) -> str:
    """把 MCP 工具返回（可能是内容块列表）转成纯文本。"""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(raw)


def _format_profile(profile: dict) -> str:
    if not profile:
        return "（无基本档案）"
    allergies = profile.get("allergies") or "无"
    if isinstance(allergies, (list, tuple)):
        allergies = "、".join(str(a) for a in allergies) or "无"
    return (
        f"- 名字：{profile.get('name') or '未知'}\n"
        f"- 编号：{profile.get('pet_id') or '未知'}\n"
        f"- 物种/品种：{profile.get('species') or '未知'} {profile.get('breed') or ''}".rstrip() + "\n"
        f"- 年龄/体重：{profile.get('age') or '未知'} / {profile.get('weight') or '未知'}\n"
        f"- 过敏史：{allergies}\n"
        f"- 既往病史：{profile.get('medical_history') or '无'}"
    )


def _format_history(history: list[dict]) -> str:
    if not history:
        return "（暂无问诊记录）"
    lines = []
    for record in history:
        diseases = (record.get("diagnosis") or {}).get("possible_diseases", [])
        date = str(record.get("date", ""))[:10]
        lines.append(
            f"- {date}：症状 {record.get('symptoms', [])}；"
            f"可能疾病 {diseases}；安全标记 {record.get('safety_flag', '')}"
        )
    return "\n".join(lines)


def _record_to_profile(record: dict, pet_id: str | None) -> dict:
    """把 MCP 病历记录转换为本地档案结构。"""
    history = record.get("history")
    if isinstance(history, (list, tuple)):
        history = "；".join(str(item) for item in history)
    return {
        "pet_id": record.get("pet_id") or pet_id,
        "name": record.get("name", ""),
        "species": record.get("species", ""),
        "breed": record.get("breed", ""),
        "age": record.get("age", ""),
        "weight": record.get("weight", ""),
        "allergies": record.get("allergies") or [],
        "medical_history": history or "",
    }


def record_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """汇总并回复当前宠物的病历。"""
    profile = dict(state.get("pet_profile") or {})
    pet_id = state.get("active_pet_id") or profile.get("pet_id")
    history = state.get("pet_history") or []

    external = ""
    mcp_record: dict | None = None
    if pet_id and _medical_tool is not None:
        try:
            external = _extract_text(_medical_tool.invoke({"pet_id": pet_id}))
            try:
                parsed = json.loads(external)
                mcp_record = parsed if isinstance(parsed, dict) else None
            except Exception:  # noqa: BLE001
                mcp_record = None
        except Exception as exc:  # noqa: BLE001
            external = f"（外部病历查询失败：{exc}）"

    # 本地无档案但 MCP 有记录时，用 MCP 记录补全档案，避免显示“未知”
    if not profile.get("name") and mcp_record:
        profile = _record_to_profile(mcp_record, pet_id)
        external = ""

    name = profile.get("name") or pet_id or "该宠物"
    sections = [
        f"【{name} 的病历】",
        "",
        "一、基本档案",
        _format_profile(profile),
        "",
        "二、既往问诊记录",
        _format_history(history),
    ]
    # 外部病历查不到时不显示该段，避免出现「未找到…」的噪声
    if external and "未找到" not in external:
        sections += ["", "三、门诊病历（MCP）", external]

    return {"messages": [AIMessage(content="\n".join(sections))]}
