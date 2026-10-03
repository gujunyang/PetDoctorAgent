"""Supervisor 分诊调度节点。

使用 ``langchain.agents.create_agent`` 创建主 Agent，负责分析用户意图并通过
结构化输出 ``SupervisorDecision`` 给出下一跳路由。
"""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from config import get_llm
from state import PetClinicState, SupervisorDecision

SUPERVISOR_PROMPT = """你是一个宠物店问诊系统的分诊调度员。你的职责是分析用户意图并路由到对应的专家Agent。

可用Agent：
- ask_symptom_agent: 当用户描述宠物症状、询问疾病相关问题时使用
- recommend_product_agent: 当用户询问宠物药品、保健品、食品推荐时使用
- safe_check_agent: 当问诊Agent给出诊断结果后，自动调用进行安全审查

路由规则：
1. 如果用户描述症状（如"我的狗一直抓痒"），路由到 ask_symptom_agent
2. 如果用户询问产品（如"有什么药可以治猫藓"），路由到 recommend_product_agent
3. 如果用户只是打招呼或闲聊，直接回复
4. 如果上一轮是问诊Agent的输出，调用 safe_check_agent

当前宠物档案：{pet_info}
"""


def _format_pet_info(pet_profile: Any) -> str:
    """把宠物档案 dict 格式化为可读的一句话。"""
    if not pet_profile or not isinstance(pet_profile, dict):
        return "暂无档案"

    species = pet_profile.get("species") or "未知物种"
    breed = pet_profile.get("breed") or ""
    age = pet_profile.get("age") or ""
    allergies = pet_profile.get("allergies") or "无"
    if isinstance(allergies, (list, tuple)):
        allergies = "、".join(str(a) for a in allergies) or "无"

    name = f"{species} {breed}".strip()
    age_part = f"{age}岁" if age else "年龄未知"
    return f"{name}，{age_part}，过敏史：{allergies}"


@dynamic_prompt
def _supervisor_prompt(request: ModelRequest) -> str:
    """从共享状态中读取宠物档案（长期记忆），动态生成 System Prompt。"""
    state: Any = request.state or {}
    pet_profile = state.get("pet_profile") if hasattr(state, "get") else None
    return SUPERVISOR_PROMPT.format(pet_info=_format_pet_info(pet_profile))


_SUPERVISOR_AGENT = create_agent(
    get_llm(),
    tools=[],
    middleware=[_supervisor_prompt],
    state_schema=PetClinicState,
    response_format=SupervisorDecision,
    name="supervisor",
)


def supervisor_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """Supervisor 节点：产出 ``next_agent`` 决策，必要时附上直接回复。

    为避免「worker → supervisor」循环中重复派发同一 worker：
    - 仅当最后一条是用户消息（新一轮）时调用 LLM 判断意图；
    - worker 执行完返回后，按状态确定性推进：问诊/推荐已产出则转安全审查，审查完成则结束。
    """
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    is_new_turn = isinstance(last, HumanMessage)

    if not is_new_turn:
        if state.get("safety_flag"):
            return {"next_agent": "FINISH"}
        if state.get("diagnosis") or state.get("product_recommendations"):
            return {"next_agent": "safe_check_agent"}
        return {"next_agent": "FINISH"}

    result = _SUPERVISOR_AGENT.invoke(
        {"messages": messages, "pet_profile": state.get("pet_profile", {})},
        config,
    )
    decision = result.get("structured_response")

    if decision is None:
        return {"next_agent": "FINISH"}

    updates: dict = {"next_agent": decision.next_agent}
    if decision.direct_response:
        updates["messages"] = [AIMessage(content=decision.direct_response)]
    return updates
