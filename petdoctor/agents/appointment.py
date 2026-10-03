"""预约 Worker Agent：处理门店预约（查询时段 / 创建预约）。

工具来自 MCP Server（check_appointment_slots、create_appointment），
在 build_graph 时通过 ``configure_agent`` 注入。

说明：
- MCP 工具经 ``tools/mcp_client.load_mcp_tools()`` 桥接为可同步调用的工具，故节点保持同步；
- 与其它 Worker 一样采用「工具 Agent 产出文本」的方式，避免强制 tool_choice 死循环。
"""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from petdoctor.config import get_llm
from petdoctor.state import PetClinicState

APPOINTMENT_PROMPT = """你是宠物店预约助手。
职责：查询可预约时段、为宠物创建预约、答复门店服务相关问题。

可用工具：
- check_appointment_slots(date)：查询某天（YYYY-MM-DD）的可预约时段
- create_appointment(pet_name, service, datetime)：创建预约，datetime 格式 YYYY-MM-DD HH:MM

行为要求：
1. 用户想看时间时，先调用 check_appointment_slots。
2. 信息齐全（宠物名、服务类型、日期时间）且用户确认后，调用 create_appointment 创建。
3. 缺少信息时主动询问（日期、宠物名、服务类型、时间），不要臆造。
4. 创建成功后，复述预约详情与预约号。

请用简洁、专业的中文回复。
"""


def _build_agent(extra_tools: list[Any] | None = None) -> Any:
    return create_agent(
        get_llm(),
        tools=list(extra_tools or []),
        system_prompt=APPOINTMENT_PROMPT,
        name="appointment_agent",
    )


_APPOINTMENT_AGENT = _build_agent()


def configure_agent(extra_tools: list[Any] | None = None) -> None:
    """用 MCP 预约工具重建 Agent。"""
    global _APPOINTMENT_AGENT
    _APPOINTMENT_AGENT = _build_agent(extra_tools)


def _build_agent_input(state: PetClinicState) -> dict:
    """注入宠物档案作为上下文。"""
    messages: list[Any] = list(state.get("messages", []))
    pet_profile = state.get("pet_profile")
    if pet_profile:
        messages = [SystemMessage(content=f"当前宠物档案：{pet_profile}"), *messages]
    return {"messages": messages}


def _last_ai_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                return message.content
    return ""


def appointment_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """预约节点：调用 MCP 工具查询/创建预约并回复。"""
    result = _APPOINTMENT_AGENT.invoke(_build_agent_input(state), config)
    answer = _last_ai_text(result.get("messages", []))
    return {"messages": [AIMessage(content=answer or "预约服务暂时不可用，请稍后再试。")]}
