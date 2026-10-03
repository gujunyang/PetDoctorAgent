"""产品推荐 Worker Agent：根据诊断结果或用户需求推荐产品。

两阶段设计（规避 DeepSeek 思考模式下强制 tool_choice 导致工具死循环的问题）：
1. Agent 阶段：挂载 pet_knowledge_search 与 MCP 库存工具，自由调用后产出自然语言答复；
2. 抽取阶段：用 json_mode 结构化输出把答复抽取为 ``ProductList``。

接入：宠物档案、诊断结果与已有 rag_context 注入 Agent 输入；
工具支持在 build_graph 时通过 ``configure_agent`` 合并 MCP 工具（check_product_stock）。
"""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from config import get_llm
from state import PetClinicState, ProductList
from tools.rag_tool import pet_knowledge_search

RECOMMEND_PRODUCT_PROMPT = """你是宠物产品推荐专家，根据用户的宠物症状或诊断结果推荐产品。

行为要求：
1. 推荐前先调用 pet_knowledge_search 检索相关药品/保健品信息。
2. 如可用，调用 check_product_stock 确认产品是否有货，缺货时提示可预订或换品。
3. 必须确认：适用物种、年龄范围、用法用量、禁忌症。
4. 先确认适用物种和年龄（幼年/成年/老年），避免跨物种用药。
5. 每条推荐需说明理由、用法用量与注意事项。
6. 如果产品涉及处方药，必须提示"请咨询兽医后使用"。

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
    """用合并后的工具列表（含 MCP 工具 check_product_stock）重建 Agent。"""
    global _RECOMMEND_PRODUCT_AGENT
    _RECOMMEND_PRODUCT_AGENT = _build_agent(extra_tools)


def _build_agent_input(state: PetClinicState) -> dict:
    """把宠物档案、诊断结果与已有 rag_context 注入 Agent 输入。"""
    messages: list[Any] = list(state.get("messages", []))
    context: list[str] = []

    pet_profile = state.get("pet_profile")
    if pet_profile:
        context.append(f"当前宠物档案：{pet_profile}")
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
        "你是宠物产品信息抽取助手。请以 json 格式输出，字段严格对应："
        "recommendations(对象数组，每项含 name、category、reason、usage、price)、"
        "reply(给用户的中文最终回复)。\n\n"
        f"对话记录：\n{_render_dialogue(messages)}\n\n助手最终答复：\n{answer}"
    )
    try:
        return _RECOMMEND_PRODUCT_EXTRACTOR.invoke(prompt)
    except Exception:
        return None


def recommend_product_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """产品推荐节点：产出结构化推荐列表，并将 RAG/库存结果写入 rag_context。"""
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
