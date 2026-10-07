"""Supervisor 分诊调度节点。

使用 ``langchain.agents.create_agent`` 创建主 Agent，负责分析用户意图并通过
结构化输出 ``SupervisorDecision`` 给出下一跳路由。
"""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from petdoctor.config import get_llm
from petdoctor.state import PetClinicState, SupervisorDecision

# 对话摘要（上下文压缩）参数
SUMMARY_THRESHOLD = 20  # 消息数达到该值触发摘要
KEEP_RECENT = 6  # 摘要后保留的最近消息数

SUMMARY_PROMPT = (
    "你是宠物问诊对话的摘要助手。请用 3-5 句话总结以下对话的关键信息，"
    "只保留对话中真实出现的内容，不要推测或补充。必须尽量保留："
    "宠物名字/编号、物种与年龄、已描述的症状、已给出的诊断或产品推荐、"
    "安全标记（safe/warning/emergency）、用户当前关注点。\n\n对话记录：\n{transcript}"
)

SUPERVISOR_PROMPT = """你是宠物店问诊系统的分诊调度员，负责分析用户最新一轮意图，并路由到唯一合适的专家 Agent。

可用 Agent：
- ask_symptom_agent：用户描述宠物症状、询问疾病/病因/护理时使用。
- recommend_product_agent：用户询问药品、保健品、食品、用品，即"具体买什么/用什么"时使用。
- safe_check_agent：用户当前描述中出现危及生命的紧急情况时使用；问诊/推荐后的常规安全审查由系统自动触发，你无需主动指派。
- appointment_agent：用户想预约门店服务（疫苗、驱虫、洗浴、体检、寄养等）时使用。
- record_agent：用户想查看宠物病历/病史/档案时使用。

路由规则：
1. 以"用户这一轮想做什么"为准：描述症状→ask_symptom_agent；询问具体产品→recommend_product_agent。
2. 症状与产品同时出现时，若核心诉求是"是什么病/怎么治"→ask_symptom_agent；若核心是"买什么/用什么药"→recommend_product_agent。
3. 出现中毒、误食、大量出血、呼吸困难、抽搐、意识丧失等紧急情况→safe_check_agent。
4. 用户同时提出多个诉求时，只选当前最紧急或最先提到的一个，其余留到后续轮次。
5. 打招呼、闲聊、与宠物无关或无需调用专家时→next_agent 填 FINISH，并只在 direct_response 中给出简短中文回复。

判别示例：
- "我家狗一直抓痒，是什么病？" → ask_symptom_agent
- "有什么药可以治猫藓？" → recommend_product_agent
- "帮我给旺财约周六洗澡" → appointment_agent
- "看看旺财之前的病历" → record_agent
- "我家猫好像吃了老鼠药，一直抽搐" → safe_check_agent
- "你好呀" → FINISH，direct_response 填"你好！请告诉我这次是哪只宠物，以及它有什么不舒服～"

输出要求：
- 只做路由判断，不回答医学问题；direct_response 仅用于闲聊或无需专家时。
- 调用专家时，direct_response 留空。

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


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _summarize(messages: list[Any]) -> str:
    """调用 LLM 对旧消息做 3-5 句摘要。"""
    transcript = "\n".join(_message_text(message) for message in messages)
    prompt = SUMMARY_PROMPT.format(transcript=transcript)
    response = get_llm().invoke(prompt)
    return _message_text(response)


def summarize_if_needed(state: PetClinicState) -> tuple[dict, list[Any]]:
    """消息超过阈值时压缩上下文。

    Returns:
        (state 更新字典, 供 Supervisor 使用的消息列表)。未触发时更新为空字典。
    """
    messages = list(state.get("messages", []))
    if len(messages) < SUMMARY_THRESHOLD:
        return {}, messages

    summary = _summarize(messages[:-KEEP_RECENT])
    recent = messages[-KEEP_RECENT:]
    summary_message = SystemMessage(content=f"[历史对话摘要] {summary}")

    compressed = [RemoveMessage(id=REMOVE_ALL_MESSAGES), summary_message, *recent]
    working = [summary_message, *recent]
    return {"messages": compressed, "session_summary": summary}, working


def supervisor_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """Supervisor 节点：产出 ``next_agent`` 决策，必要时附上直接回复。

    为避免「worker → supervisor」循环中重复派发同一 worker：
    - 仅当最后一条是用户消息（新一轮）时调用 LLM 判断意图；
    - worker 执行完返回后，按状态确定性推进：问诊/推荐已产出则转安全审查，审查完成则结束。

    上下文工程：消息达到 ``SUMMARY_THRESHOLD`` 条时，先压缩历史为摘要再决策。
    """
    summary_updates, working_messages = summarize_if_needed(state)
    last = working_messages[-1] if working_messages else None
    is_new_turn = isinstance(last, HumanMessage)

    if not is_new_turn:
        updates = dict(summary_updates)
        if state.get("safety_flag"):
            updates["next_agent"] = "FINISH"
        elif state.get("diagnosis") or state.get("product_recommendations"):
            updates["next_agent"] = "safe_check_agent"
        else:
            updates["next_agent"] = "FINISH"
        return updates

    result = _SUPERVISOR_AGENT.invoke(
        {"messages": working_messages, "pet_profile": state.get("pet_profile", {})},
        config,
    )
    decision = result.get("structured_response")

    updates = dict(summary_updates)
    if decision is None:
        updates["next_agent"] = "FINISH"
        return updates

    updates["next_agent"] = decision.next_agent
    if decision.direct_response:
        reply = AIMessage(content=decision.direct_response)
        if "messages" in summary_updates:
            # 摘要已替换消息列表，直接把回复追加到压缩后的列表
            updates["messages"] = [*summary_updates["messages"], reply]
        else:
            updates["messages"] = [reply]
    return updates
