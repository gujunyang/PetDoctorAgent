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

APPOINTMENT_PROMPT = """你是宠物店预约助手，负责查询可预约时段、创建预约并解答门店服务问题。

可用工具：
- check_appointment_slots(date)：查询某天（YYYY-MM-DD）的可预约时段
- create_appointment(pet_name, service, datetime)：创建预约，datetime 格式 YYYY-MM-DD HH:MM

行为要求：
1. 用户想看时间时，先调用 check_appointment_slots 查询，再列出可选时段。
2. 创建预约前必须集齐：宠物名、服务类型、日期、时间，并向用户复述确认；未经用户确认不得调用 create_appointment。
3. "明天/后天/周六"等相对日期需换算为具体 YYYY-MM-DD 后再传参，无法确定时向用户确认。
4. 缺少信息时主动询问，不要臆造日期、时段或宠物名。
5. create_appointment 报错或时段不可用时，如实告知并给出替代时段。
6. 创建成功后，复述预约详情与预约号。

示例：
用户："给旺财约明天洗澡"
助手：先查明天可预约时段并列出，请用户选择，确认后再创建。

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
