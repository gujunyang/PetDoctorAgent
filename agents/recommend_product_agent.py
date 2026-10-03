"""产品推荐 Worker Agent：根据诊断结果或用户需求推荐产品。

两阶段设计（规避 DeepSeek 思考模式下强制 tool_choice 导致工具死循环的问题）：
1. Agent 阶段：仅挂载 pet_knowledge_search 工具，自由调用后产出自然语言答复；
2. 抽取阶段：用 json_mode 结构化输出把答复抽取为 ``ProductList``。
"""

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from config import get_llm
from state import PetClinicState, ProductList
from tools.rag_tool import pet_knowledge_search

RECOMMEND_PRODUCT_PROMPT = """你是宠物产品推荐专家。
职责：根据诊断结果或用户需求，推荐合适的宠物药品、保健品或食品。

行为要求：
1. 先确认适用物种和年龄（幼年/成年/老年），避免跨物种用药。
2. 需要产品知识时调用 pet_knowledge_search 工具检索商品知识库。
3. 每条推荐需说明理由、用法用量与注意事项。
4. 若涉及处方药或紧急情况，提醒用户咨询执业兽医。

请用简洁、专业的中文列出推荐产品（名称、类别、理由、用法、参考价格）。
"""

_RECOMMEND_PRODUCT_AGENT = create_agent(
    get_llm(),
    tools=[pet_knowledge_search],
    system_prompt=RECOMMEND_PRODUCT_PROMPT,
    name="recommend_product_agent",
)

_RECOMMEND_PRODUCT_EXTRACTOR = get_llm().with_structured_output(ProductList, method="json_mode")


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
    """产品推荐节点：产出结构化推荐列表。"""
    result = _RECOMMEND_PRODUCT_AGENT.invoke({"messages": state["messages"]}, config)
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
