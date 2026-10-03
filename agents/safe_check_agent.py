"""安全审查 Worker Agent：审查问诊/推荐结果并给出安全标记。"""

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

import memory
from config import get_llm
from state import PetClinicState, SafetyReview

EMERGENCY_KEYWORDS = ["中毒", "大量出血", "呼吸困难", "抽搐", "意识丧失"]

EMERGENCY_REPLY = "请立即带宠物去最近的宠物医院"

SAFE_CHECK_PROMPT = """你是宠物医疗安全审查员。
职责：审查问诊/产品推荐结果，判断风险等级。

判定标准：
- emergency：出现中毒、大量出血、呼吸困难、抽搐、意识丧失等危及生命的情况。
- warning：存在明显风险或需要专业兽医介入，但暂不危及生命。
- safe：常规情况。

输出为结构化结果：safety_flag、reason、advice。
"""

_SAFE_CHECK_AGENT = create_agent(
    get_llm(),
    tools=[],
    system_prompt=SAFE_CHECK_PROMPT,
    response_format=SafetyReview,
    name="safe_check_agent",
)


def _emergency_keyword(state: PetClinicState) -> str | None:
    """确定性关键词扫描，命中后直接判定为 emergency。"""
    texts: list[str] = []
    texts.extend(
        message.content if isinstance(message.content, str) else str(message.content)
        for message in state.get("messages", [])
    )
    texts.extend(str(s) for s in state.get("symptoms", []))
    texts.append(str(state.get("diagnosis", "")))
    haystack = " ".join(texts)
    return next((kw for kw in EMERGENCY_KEYWORDS if kw in haystack), None)


def safe_check_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """安全审查节点：命中紧急关键词直接告警，否则由 LLM 分级。

    审查结束后，若 safety_flag 为 safe / warning，则把本次问诊摘要写入长期记忆。
    """
    keyword = _emergency_keyword(state)
    if keyword:
        flag = "emergency"
        reply = f"检测到紧急症状「{keyword}」，{EMERGENCY_REPLY}！"
    else:
        result = _SAFE_CHECK_AGENT.invoke({"messages": state["messages"]}, config)
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
