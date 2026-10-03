"""LangGraph 记忆系统：短期记忆（Checkpointer）+ 长期记忆（Store）。

短期记忆：PostgresSaver 按 ``thread_id`` 保存会话级状态。
长期记忆：PostgresStore 按 ``user_id`` / ``pet_id`` 跨会话保存用户与宠物信息。

未配置 ``DATABASE_URL``（或连接失败）时自动回退到进程内实现
（``InMemorySaver`` / ``InMemoryStore``），便于本地开发。

Namespace 设计：
    ("users", user_id, "profile")            -> 用户基本信息
    ("users", user_id, "pets", pet_id)       -> 宠物档案
    ("users", user_id, "history")            -> 历史问诊记录摘要（key 为 uuid）
"""

from __future__ import annotations

import atexit
import os
import uuid
from contextlib import ExitStack
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

# 确保独立导入 memory 时也能读到 .env 中的 DATABASE_URL
load_dotenv()

_stack = ExitStack()
atexit.register(_stack.close)

_checkpointer: Any = None
_store: Any = None


# ── 连接与单例 ──────────────────────────────────────────────────────────────

def get_database_url() -> str | None:
    """读取 DATABASE_URL；未配置返回 None。"""
    return os.getenv("DATABASE_URL") or None


def get_checkpointer() -> Any:
    """返回短期记忆 Checkpointer（PostgresSaver，失败则回退 InMemorySaver）。"""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    url = get_database_url()
    if url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            _checkpointer = _stack.enter_context(PostgresSaver.from_conn_string(url))
            _checkpointer.setup()  # 首次运行创建表
            print("[memory] 短期记忆：PostgresSaver")
            return _checkpointer
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] PostgresSaver 初始化失败，回退 InMemorySaver：{exc}")

    from langgraph.checkpoint.memory import InMemorySaver

    _checkpointer = InMemorySaver()
    print("[memory] 短期记忆：InMemorySaver（仅进程内）")
    return _checkpointer


def get_store() -> Any:
    """返回长期记忆 Store（PostgresStore，失败则回退 InMemoryStore）。"""
    global _store
    if _store is not None:
        return _store

    url = get_database_url()
    if url:
        try:
            from langgraph.store.postgres import PostgresStore

            _store = _stack.enter_context(PostgresStore.from_conn_string(url))
            _store.setup()
            print("[memory] 长期记忆：PostgresStore")
            return _store
        except Exception as exc:  # noqa: BLE001
            print(f"[memory] PostgresStore 初始化失败，回退 InMemoryStore：{exc}")

    from langgraph.store.memory import InMemoryStore

    _store = InMemoryStore()
    print("[memory] 长期记忆：InMemoryStore（仅进程内）")
    return _store


def set_checkpointer(checkpointer: Any) -> None:
    global _checkpointer
    _checkpointer = checkpointer


def set_store(store: Any) -> None:
    global _store
    _store = store


# ── 会话配置 ────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    """生成会话 ID（uuid4，36 字符 < 255 限制）。"""
    return str(uuid.uuid4())


def make_config(session_id: str | None = None, user_id: str = "default") -> dict:
    """构造 graph.invoke 使用的 config。"""
    thread_id = session_id or new_session_id()
    if len(thread_id) > 255:
        raise ValueError("thread_id 长度不能超过 255 个字符")
    return {"configurable": {"thread_id": thread_id, "user_id": user_id}}


def user_id_from_config(config: Any) -> str:
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    return configurable.get("user_id", "default")


# ── Namespace 与读写 ────────────────────────────────────────────────────────

def _profile_ns(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "profile")


def _pets_ns(user_id: str, pet_id: str) -> tuple[str, ...]:
    return ("users", user_id, "pets", pet_id)


def _pets_prefix(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "pets")


def _pet_history_ns(user_id: str, pet_id: str) -> tuple[str, ...]:
    return ("users", user_id, "pets", pet_id, "history")


def _history_ns(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "history")


def load_user_profile(user_id: str) -> dict:
    """读取用户基本信息。"""
    try:
        item = get_store().get(_profile_ns(user_id), "info")
    except Exception:  # noqa: BLE001
        return {}
    return item.value if item else {}


def save_user_profile(user_id: str, profile: dict) -> None:
    get_store().put(_profile_ns(user_id), "info", profile)


def load_pet_profile(user_id: str, pet_id: str = "default") -> dict:
    """读取宠物档案（品种、年龄、体重、过敏史、既往病史）。"""
    try:
        item = get_store().get(_pets_ns(user_id, pet_id), "profile")
    except Exception:  # noqa: BLE001
        return {}
    return item.value if item else {}


def save_pet_profile(user_id: str, pet_profile: dict, pet_id: str = "default") -> None:
    get_store().put(_pets_ns(user_id, pet_id), "profile", pet_profile)


def get_pet(user_id: str, pet_id: str) -> dict | None:
    """按 pet_id 读取宠物档案。"""
    try:
        item = get_store().get(_pets_ns(user_id, pet_id), "profile")
    except Exception:  # noqa: BLE001
        return None
    return item.value if item else None


def find_pet_by_name(user_id: str, name: str) -> dict | None:
    """按名字在该用户名下查找宠物（精确匹配优先，其次包含匹配）。"""
    target = (name or "").strip().lower()
    if not target:
        return None
    try:
        items = get_store().search(_pets_prefix(user_id), limit=100)
    except Exception:  # noqa: BLE001
        return None
    pets = [item.value for item in items if item.value]
    for pet in pets:
        if str(pet.get("name", "")).strip().lower() == target:
            return pet
    for pet in pets:
        if target in str(pet.get("name", "")).strip().lower():
            return pet
    return None


def create_pet(user_id: str, info: dict) -> str:
    """为宠物建档，返回 pet_id。"""
    pet_id = info.get("pet_id") or f"PET-{uuid.uuid4().hex[:6].upper()}"
    pet = {
        "pet_id": pet_id,
        "name": info.get("name", ""),
        "species": info.get("species", ""),
        "breed": info.get("breed", ""),
        "age": info.get("age", ""),
        "weight": info.get("weight", ""),
        "allergies": info.get("allergies") or [],
        "medical_history": info.get("medical_history", ""),
        "created_at": datetime.now().isoformat(),
    }
    save_pet_profile(user_id, pet, pet_id)
    return pet_id


def load_pet_history(user_id: str, pet_id: str, limit: int = 5) -> list[dict]:
    """读取该宠物以往的问诊记录摘要（用于辅助推理）。"""
    try:
        items = get_store().search(_pet_history_ns(user_id, pet_id), limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [item.value for item in items]


def save_diagnosis_summary(state: dict, config: Any, safety_flag: str | None = None) -> str | None:
    """把本次问诊摘要写入历史记录，返回记录 key。

    已识别宠物时写入该宠物名下的历史（``pets/<pet_id>/history``），否则写入用户级历史。
    """
    user_id = user_id_from_config(config)
    pet_id = state.get("active_pet_id")
    summary = {
        "date": datetime.now().isoformat(),
        "pet_id": pet_id,
        "symptoms": state.get("symptoms", []),
        "diagnosis": state.get("diagnosis", {}),
        "safety_flag": safety_flag or state.get("safety_flag", ""),
        "session_summary": state.get("session_summary", ""),
    }
    key = str(uuid.uuid4())
    namespace = _pet_history_ns(user_id, pet_id) if pet_id else _history_ns(user_id)
    try:
        get_store().put(namespace, key, summary)
    except Exception as exc:  # noqa: BLE001
        print(f"[memory] 写入问诊历史失败：{exc}")
        return None
    return key


def load_history(user_id: str, limit: int = 10) -> list[dict]:
    """读取最近的历史问诊记录摘要。"""
    try:
        items = get_store().search(_history_ns(user_id), limit=limit)
    except Exception:  # noqa: BLE001
        return []
    return [item.value for item in items]
