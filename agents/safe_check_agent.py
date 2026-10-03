"""安全审查 Worker Agent：规则匹配 + LLM 审查，不使用 RAG 工具。

流程：
1. 规则匹配：对最后一条用户消息（及已提取症状）做紧急关键词扫描；
2. LLM 审查：未命中关键词时，结合诊断/推荐结果由 LLM 判定 safe/warning/emergency；
3. 审查完成后（safe/warning）把问诊摘要写入长期记忆。
"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

import memory
from config import get_llm
from state import PetClinicState, SafetyReview

EMERGENCY_KEYWORDS = [
    "中毒", "误食", "大量出血", "呼吸困难", "抽搐",
    "意识丧失", "昏迷", "瘫痪", "持续呕吐", "便血",
    "poison", "seizure", "unconscious", "bleeding heavily",
]

EMERGENCY_REPLY = "⚠️ 紧急情况！请立即带宠物去最近的宠物医院。"

SAFE_CHECK_PROMPT = """你是宠物医疗安全审查员。
职责：审查问诊/产品推荐结果，判断风险等级。

判定标准：
- emergency：出现中毒、误食、大量出血、呼吸困难、抽搐、意识丧失、昏迷、瘫痪、
  持续呕吐、便血等危及生命的情况。
- warning：存在明显风险或需要专业兽医介入，但暂不危及生命。
- safe：常规情况。

只输出结构化结果：safety_flag、reason、advice。
"""

_SAFE_CHECK_AGENT = create_agent(
    get_llm(),
    tools=[],
    system_prompt=SAFE_CHECK_PROMPT,
    response_format=SafetyReview,
    name="safe_check_agent",
)


def _get_last_user_message(state: PetClinicState) -> str:
    """取最后一条用户消息文本。"""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def _emergency_keyword(state: PetClinicState) -> str | None:
    """对最后一条用户消息与已提取症状做紧急关键词扫描。"""
    haystack_parts = [_get_last_user_message(state)]
    haystack_parts.extend(str(s) for s in state.get("symptoms", []))
    haystack = " ".join(haystack_parts).lower()
    return next((kw for kw in EMERGENCY_KEYWORDS if kw.lower() in haystack), None)


def safe_check_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """安全审查节点：规则匹配命中即 emergency，否则交由 LLM 审查。"""
    keyword = _emergency_keyword(state)
    if keyword:
        flag = "emergency"
        reply = f"{EMERGENCY_REPLY}（命中关键词：{keyword}）"
    else:
        review_input = [
            SystemMessage(
                content=(
                    "请审查以下问诊/产品推荐内容的安全性。\n"
                    f"症状：{state.get('symptoms', [])}\n"
                    f"诊断结果：{state.get('diagnosis', {})}\n"
                    f"产品推荐：{state.get('product_recommendations', [])}"
                )
            ),
            *list(state.get("messages", [])),
        ]
        result = _SAFE_CHECK_AGENT.invoke({"messages": review_input}, config)
        review = result.get("structured_response")
        if review is None:
            flag = "warning"
            reply = "安全审查未完成，建议咨询执业兽医。"
        else:
            flag = review.safety_flag
            reply = review.advice or review.reason or "安全审查完成。"

    # 长期记忆：safe / warning 写入问诊历史摘要，emergency 不写入
    if flag in {"safe", "warning"}:
        memory.save_diagnosis_summary(state, config, safety_flag=flag)

    return {"safety_flag": flag, "messages": [AIMessage(content=reply)]}
