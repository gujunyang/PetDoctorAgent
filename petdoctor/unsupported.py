"""暂不支持功能的确定性兜底。

门店预约与库存查询功能已下线：命中相关关键词时直接返回固定话术，
不路由到任何 Worker，也不调用任何工具。

被 ``identify_pet`` 与 ``supervisor`` 共同引用，确保即使用户首句未提供宠物名，
也能明确回复「暂不支持」而不是先追问宠物信息。
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AnyMessage, HumanMessage

# 统一话术：明确告知暂不支持
UNSUPPORTED_REPLY = (
    "抱歉，门店预约与库存查询功能暂未开放，暂时不支持这两项服务。"
    "我可以为您提供症状问诊、用药/产品知识推荐、安全提醒与病历查询。"
)

# 命中任一即视为「暂不支持」请求（预约 / 库存）
UNSUPPORTED_PATTERNS = [
    r"预约",
    r"挂号",
    r"约(个|一下|一个|一次)?(时间|时段|档期|点|号)",
    r"(什么|哪些|哪天|几点)?时段",
    r"有(没有)?(空|档期|位置|位子)",
    r"库存",
    r"有货",
    r"现货",
    r"存货",
    r"还有(多少)?货",
    r"\b(stock|inventory|appointment|booking|book|reserve)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in UNSUPPORTED_PATTERNS]


def is_unsupported(text: str) -> bool:
    """判断一句话是否在请求已下线的预约/库存功能。"""
    if not text:
        return False
    return any(pattern.search(text) for pattern in _COMPILED)


def unsupported_reply(text: str) -> str | None:
    """命中则返回固定话术，否则返回 None。"""
    return UNSUPPORTED_REPLY if is_unsupported(text) else None


def last_user_text(state_payload: Any) -> str:
    """从消息列表（或 state）中取最后一条用户消息文本。"""
    messages = state_payload.get("messages", []) if hasattr(state_payload, "get") else state_payload
    for message in reversed(list(messages or [])):
        if isinstance(message, HumanMessage):
            content: Any = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def unsupported_reply_for_state(state: Any) -> str | None:
    """从图状态中检测并返回话术。"""
    return unsupported_reply(last_user_text(state))


def as_ai_message(text: str) -> AnyMessage:
    from langchain_core.messages import AIMessage

    return AIMessage(content=text)
