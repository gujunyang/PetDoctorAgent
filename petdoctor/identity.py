"""宠物识别节点：每段会话确认「这次是哪只宠物」，并核对长期记忆。

流程：
1. 本会话已识别（state.active_pet_id）→ 直接刷新该宠物档案与历史案例；
2. 否则从最新用户消息提取宠物名/编号，在用户名下查找：
   - 命中 → 记忆该宠物，加载档案 + 历史案例；
   - 未命中但信息足够（名字 + 物种）→ 自动建档；
   - 信息不足 → 暂存草稿（state.pet_draft）并追问，本轮由图上条件边结束。
"""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from petdoctor import memory
from petdoctor.config import get_llm
from petdoctor.state import PetClinicState


class PetInfo(BaseModel):
    """从用户话语中抽取的宠物信息。"""

    name: str | None = Field(default=None, description="宠物名字")
    pet_id: str | None = Field(default=None, description="宠物编号，如 PET-001")
    species: str | None = Field(default=None, description="物种，如 犬 / 猫")
    breed: str | None = Field(default=None, description="品种")
    age: str | None = Field(default=None, description="年龄")
    weight: str | None = Field(default=None, description="体重")
    allergies: list[str] | None = Field(default=None, description="过敏史列表")


_EXTRACTOR = get_llm().with_structured_output(PetInfo, method="json_mode")


def _last_user_message(state: PetClinicState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def extract_pet_info(text: str) -> dict:
    """用 LLM 从一句话中提取宠物身份信息（json_mode）。"""
    if not text.strip():
        return {}
    prompt = (
        "请从下面这句话中提取宠物身份信息，以 json 格式输出，字段："
        "name(名字)、pet_id(编号)、species(物种，如犬/猫)、breed(品种)、"
        "age(年龄)、weight(体重)、allergies(过敏史字符串数组)。"
        "没有提到的字段留空，不要臆造。\n\n句子：" + text
    )
    try:
        info = _EXTRACTOR.invoke(prompt)
    except Exception:  # noqa: BLE001
        return {}
    return info.model_dump(exclude_none=True) if info else {}


def _merge_draft(draft: dict, info: dict) -> dict:
    for key, value in info.items():
        if value not in (None, "", [], {}):
            draft[key] = value
    return draft


def identify_pet_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """识别当前宠物；未识别则追问（不设 active_pet_id，由条件边结束本轮）。"""
    user_id = memory.user_id_from_config(config)

    # 1) 本会话已识别 → 直接刷新档案与历史案例
    active = state.get("active_pet_id")
    if active:
        pet = memory.get_pet(user_id, active)
        if pet:
            return {
                "pet_profile": pet,
                "pet_history": memory.load_pet_history(user_id, active),
            }

    # 2) 从最新用户消息提取信息并与草稿合并
    info = extract_pet_info(_last_user_message(state))
    draft = _merge_draft(dict(state.get("pet_draft") or {}), info)

    # 3) 匹配已有宠物（优先编号，其次名字）
    pet: dict | None = None
    if draft.get("pet_id"):
        pet = memory.get_pet(user_id, str(draft["pet_id"]))
    if pet is None and draft.get("name"):
        pet = memory.find_pet_by_name(user_id, str(draft["name"]))
    if pet:
        pet_id = pet.get("pet_id") or str(draft.get("pet_id") or "")
        return {
            "active_pet_id": pet_id,
            "pet_profile": pet,
            "pet_history": memory.load_pet_history(user_id, pet_id),
            "pet_draft": {},
        }

    # 4) 信息足够（名字 + 物种）→ 自动建档
    name = str(draft.get("name") or "").strip()
    species = str(draft.get("species") or "").strip()
    if name and species:
        pet_id = memory.create_pet(user_id, draft)
        return {
            "active_pet_id": pet_id,
            "pet_profile": memory.get_pet(user_id, pet_id) or {},
            "pet_history": [],
            "pet_draft": {},
        }

    # 5) 显式给出的编号即使不在本地记忆，也接受为当前宠物（供病历等外部查询）
    if draft.get("pet_id"):
        pet_id = str(draft["pet_id"])
        return {
            "active_pet_id": pet_id,
            "pet_profile": {"pet_id": pet_id},
            "pet_history": [],
            "pet_draft": {},
        }

    # 6) 信息不足 → 追问，本轮结束
    if not name:
        question = "请问这次是哪只宠物？告诉我它的名字（或编号）即可。"
    else:
        question = (
            f"没有找到叫「{name}」的档案。请补充它的物种（猫/犬）、品种、年龄、"
            "体重、过敏史（可留空），我来为它建档。"
        )
    return {"pet_draft": draft, "messages": [AIMessage(content=question)]}
