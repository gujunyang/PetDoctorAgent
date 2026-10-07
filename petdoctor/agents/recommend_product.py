"""产品推荐 Worker Agent：根据诊断结果或用户需求推荐产品。

两阶段设计（规避 DeepSeek 思考模式下强制 tool_choice 导致工具死循环的问题）：
1. Agent 阶段：挂载 pet_knowledge_search，自由调用后产出自然语言答复；
2. 抽取阶段：用 json_mode 结构化输出把答复抽取为 ``ProductList``。

接入：宠物档案、诊断结果与已有 rag_context 注入 Agent 输入。
说明：库存查询功能已下线，不再调用任何库存工具。
"""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from petdoctor.config import get_llm
from petdoctor.state import PetClinicState, ProductList
from petdoctor.tools.rag import pet_knowledge_search

RECOMMEND_PRODUCT_PROMPT = """你是宠物产品推荐专家，根据用户的症状或诊断结果推荐合适的商品。

硬性约束：
1. 推荐前先确认物种与年龄阶段（幼年/成年/老年），禁止跨物种用药（如犬用不可用于猫）。
2. 必须先调用 pet_knowledge_search 检索相关药品/保健品信息；检索未覆盖的信息标注"以实物说明为准"。
3. 每条推荐必须说明：适用物种、年龄范围、用法用量、禁忌症与注意事项。
4. 涉及处方药时，必须提示"请咨询兽医后使用"。
5. 不得编造产品、成分或价格；不提供库存、是否缺货等门店实时信息。

职责边界（只做产品推荐）：
- 你只负责产品推荐，不做疾病诊断；如需诊断，交由问诊 Agent 处理，不要输出诊断结论。

示例：
用户："猫藓用什么药？"
助手：检索后推荐外用抗真菌药，注明"外用药需戴伊丽莎白圈防止舔舐""幼猫、孕猫用药前请先咨询兽医"，并确认库存。

请用简洁、专业的中文列出推荐产品（名称、类别、理由、用法、参考价格）。
"""


def _build_agent(extra_tools: list[Any] | None = None) -> Any:
    return create_agent(
        get_llm(),
        tools=[pet_knowledge_search, *(extra_tools or [])],
        system_prompt=RECOMMEND_PRODUCT_PROMPT,
        name="recommend_product_agent",
    )


_RECOMMEND_PRODUCT_AGENT = _build_agent()
_RECOMMEND_PRODUCT_EXTRACTOR = get_llm().with_structured_output(ProductList, method="json_mode")


def configure_agent(extra_tools: list[Any] | None = None) -> None:
    """用合并后的工具列表重建 Agent（默认仅 pet_knowledge_search）。"""
    global _RECOMMEND_PRODUCT_AGENT
    _RECOMMEND_PRODUCT_AGENT = _build_agent(extra_tools)


def _build_agent_input(state: PetClinicState) -> dict:
    """把宠物档案、诊断结果与已有 rag_context 注入 Agent 输入。"""
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
    if state.get("diagnosis"):
        context.append(f"问诊诊断结果：{state['diagnosis']}")
    if state.get("rag_context"):
        context.append(f"已有知识库检索上下文（供参考）：\n{state['rag_context']}")
    if context:
        messages = [SystemMessage(content="\n\n".join(context)), *messages]
    return {"messages": messages}


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


def _extract_products(answer: str, messages: list[Any]) -> ProductList | None:
    """第二阶段：把推荐答复抽取为结构化 ProductList。"""
    prompt = (
        "你是宠物产品信息抽取助手。请严格以 json 输出，字段如下："
        "recommendations(对象数组，每项含 name、category、reason、usage、price；"
        "只提取回复中真实提到的产品，缺失字段填空字符串，不要编造价格)、"
        "reply(字符串，给用户的最终中文回复，直接复用助手答复)。\n\n"
        f"对话记录：\n{_render_dialogue(messages)}\n\n助手最终答复：\n{answer}"
    )
    try:
        return _RECOMMEND_PRODUCT_EXTRACTOR.invoke(prompt)
    except Exception:
        return None


def recommend_product_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """产品推荐节点：产出结构化推荐列表，并将 RAG 结果写入 rag_context。"""
    result = _RECOMMEND_PRODUCT_AGENT.invoke(_build_agent_input(state), config)
    messages = result.get("messages", [])
    answer = _last_ai_text(messages)
    rag_context = _collect_rag_context(messages)

    product_list = _extract_products(answer, state["messages"])
    if product_list is None:
        return {
            "product_recommendations": [],
            "rag_context": rag_context,
            "messages": [AIMessage(content=answer or "产品推荐暂时无法完成，请稍后再试。")],
        }

    recommendations = [
        item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for item in product_list.recommendations
    ]
    reply = product_list.reply or answer or "已为您生成产品推荐。"
    return {
        "product_recommendations": recommendations,
        "rag_context": rag_context,
        "messages": [AIMessage(content=reply)],
    }
