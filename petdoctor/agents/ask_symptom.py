"""问诊 Worker Agent：收集症状信息，基于 RAG 知识库给出初步诊断。

两阶段设计（规避 DeepSeek 思考模式下强制 tool_choice 导致工具死循环的问题）：
1. Agent 阶段：挂载 pet_knowledge_search（及 MCP 病历工具），自由调用后产出自然语言答复；
2. 抽取阶段：用 json_mode 结构化输出把答复抽取为 ``SymptomAssessment``。

接入：长期记忆（宠物档案）与已有 rag_context 注入 Agent 输入；
工具支持在 build_graph 时通过 ``configure_agent`` 动态合并 MCP 工具。
"""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from petdoctor.config import get_llm
from petdoctor.state import PetClinicState, SymptomAssessment
from petdoctor.tools.rag import pet_knowledge_search

ASK_SYMPTOM_PROMPT = """你是宠物问诊专家，负责通过对话收集症状信息并给出初步判断。

职责（只做问诊）：
1. 先确认宠物基本信息（名字、物种、品种、年龄、体重），档案缺失时主动询问。
2. 逐步询问症状细节：何时开始、频率、部位、伴随症状、饮食与排泄、精神状态等。
3. 信息不足时提出追问，一次只问 1-2 个最关键的问题；不要勉强下结论。
4. 若发现中毒、误食、大量出血、呼吸困难、抽搐、意识丧失等紧急情况，在答复中明确提示尽快就医。
5. 若上下文提供了该宠物的既往问诊记录/过敏史，请结合分析；如可用，调用 get_pet_medical_record 查询病历作为参考。

RAG 使用要求：
- 在形成疾病判断前，建议调用 pet_knowledge_search 检索相关知识；检索关键词从用户描述的症状中提取（如"狗 抓痒 脱毛"）。
- 以检索到的知识为诊断依据，不要编造医学结论。

职责边界（只做问诊）：
- 你只负责问诊与初步判断，不推荐任何具体产品/药品/剂量。
- 如需产品推荐，交由产品推荐 Agent 处理，不要在本节点输出推荐话术。

示例：
用户："我家猫三天没吃饭，还一直吐。"
助手：追问"呕吐物是什么颜色？多久吐一次？有没有喝水或腹泻？"，并提示"食欲废绝并持续呕吐超过 24 小时，建议尽快就医"。

请用简洁、专业的中文回答，聚焦"目前的症状、可能的疾病、建议观察或就医"。
"""


def _build_agent(extra_tools: list[Any] | None = None) -> Any:
    return create_agent(
        get_llm(),
        tools=[pet_knowledge_search, *(extra_tools or [])],
        system_prompt=ASK_SYMPTOM_PROMPT,
        name="ask_symptom_agent",
    )


_ASK_SYMPTOM_AGENT = _build_agent()
_ASK_SYMPTOM_EXTRACTOR = get_llm().with_structured_output(SymptomAssessment, method="json_mode")


def configure_agent(extra_tools: list[Any] | None = None) -> None:
    """用合并后的工具列表（含 MCP 工具）重建 Agent。"""
    global _ASK_SYMPTOM_AGENT
    _ASK_SYMPTOM_AGENT = _build_agent(extra_tools)


def _build_agent_input(state: PetClinicState) -> dict:
    """把宠物档案与已有 rag_context 注入 Agent 输入。"""
    messages: list[Any] = list(state.get("messages", []))
    context: list[str] = []

    pet_profile = state.get("pet_profile")
    if pet_profile:
        context.append(f"当前宠物档案：{pet_profile}")
    pet_history = state.get("pet_history")
    if pet_history:
        rows = "\n".join(
            f"- {rec.get('date', '')}: 症状={rec.get('symptoms', [])}，诊断={rec.get('diagnosis', {})}"
            for rec in pet_history
        )
        context.append(f"该宠物以往问诊记录（供参考）：\n{rows}")
    if state.get("rag_context"):
        context.append(f"已有知识库检索上下文（供参考）：\n{state['rag_context']}")
    if context:
        messages = [SystemMessage(content="\n\n".join(context)), *messages]
    return {"messages": messages}


def _get_last_user_message(state: PetClinicState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def inject_rag_context(state: PetClinicState) -> dict:
    """预检索：用最后一条用户消息做 RAG，只保留 Top-3 结果写入 rag_context。

    只注入前 3 条检索结果，控制注入到 Agent 的上下文长度。
    """
    query = _get_last_user_message(state)
    if not query:
        return {}
    try:
        context = pet_knowledge_search.invoke({"query": query, "top_k": 3})
    except Exception:
        return {}
    context = context if isinstance(context, str) else str(context)
    return {"rag_context": context} if context.strip() else {}


def _collect_rag_context(messages: list[Any]) -> str:
    """从子 Agent 的中间消息中提取 RAG 工具返回内容。"""
    parts: list[str] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            content = message.content
            parts.append(content if isinstance(content, str) else str(content))
    return "\n\n".join(parts).strip()


def _last_ai_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                return message.content
    return ""


def _render_dialogue(messages: list[Any]) -> str:
    lines: list[str] = []
    for message in messages:
        if isinstance(message, (HumanMessage, AIMessage)) and isinstance(message.content, str):
            if message.content.strip():
                role = "用户" if isinstance(message, HumanMessage) else "助手"
                lines.append(f"{role}：{message.content}")
    return "\n".join(lines)


def _extract_assessment(answer: str, messages: list[Any]) -> SymptomAssessment | None:
    """第二阶段：把问诊答复抽取为结构化 SymptomAssessment。"""
    prompt = (
        "你是宠物问诊信息抽取助手。请严格以 json 输出，字段如下："
        "symptoms(字符串数组，仅提取对话中真实出现的症状)、"
        "possible_diseases(字符串数组，仅填助手明确提到的可能疾病，没有则空数组)、"
        "confidence(0 到 1 之间的小数)、"
        "care_advice(字符串，助手给出的护理/就医建议，没有则空字符串)、"
        "needs_more_info(布尔，助手是否仍在追问信息)、"
        "reply(字符串，给用户的最终中文回复，直接复用助手答复)。"
        "不要臆造对话中未出现的内容。\n\n"
        f"对话记录：\n{_render_dialogue(messages)}\n\n助手最终答复：\n{answer}"
    )
    try:
        return _ASK_SYMPTOM_EXTRACTOR.invoke(prompt)
    except Exception:
        return None


def ask_symptom_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """问诊节点：预注入 Top-3 RAG 上下文，产出症状列表与初步诊断。"""
    rag_updates = inject_rag_context(state)
    working_state: dict = {**state, **rag_updates}

    result = _ASK_SYMPTOM_AGENT.invoke(_build_agent_input(working_state), config)
    messages = result.get("messages", [])
    answer = _last_ai_text(messages)
    # 只保留注入的 Top-3；未注入时回退到子 Agent 的工具返回
    rag_context = rag_updates.get("rag_context") or _collect_rag_context(messages)

    assessment = _extract_assessment(answer, state["messages"])
    if assessment is None:
        return {
            "symptoms": [],
            "diagnosis": {},
            "rag_context": rag_context,
            "messages": [AIMessage(content=answer or "问诊暂时无法完成，请稍后再试。")],
        }

    diagnosis = {
        "possible_diseases": assessment.possible_diseases,
        "confidence": assessment.confidence,
        "care_advice": assessment.care_advice,
    }
    reply = assessment.reply or answer or "已完成初步问诊，请补充更多信息以便进一步判断。"
    return {
        "symptoms": assessment.symptoms,
        "diagnosis": diagnosis,
        "rag_context": rag_context,
        "messages": [AIMessage(content=reply)],
    }
