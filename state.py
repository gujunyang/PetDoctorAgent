"""多 Agent 宠物问诊系统的统一状态定义。

PetClinicState 是 Supervisor 与所有 Worker Agent 共享的状态契约，
同时定义了各 Agent 结构化输出所使用的 Pydantic 模型。
"""

from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class PetProfile(TypedDict, total=False):
    """宠物档案。"""

    species: str
    breed: str
    age: str
    weight: str
    allergies: list[str]
    medical_history: str


class Diagnosis(TypedDict, total=False):
    """问诊结果。"""

    possible_diseases: list[str]
    confidence: float
    care_advice: str


class ProductRecommendation(TypedDict, total=False):
    """单条产品推荐。"""

    name: str
    category: str
    reason: str
    usage: str
    price: str


class PetClinicState(TypedDict):
    """Supervisor + Worker 多 Agent 架构的共享状态。"""

    messages: Annotated[list[AnyMessage], add_messages]  # 对话消息历史
    pet_profile: NotRequired[dict]  # 宠物档案
    symptoms: NotRequired[list[str]]  # 当前收集到的症状列表
    diagnosis: NotRequired[dict]  # 问诊结果
    product_recommendations: NotRequired[list[dict]]  # 产品推荐列表
    safety_flag: NotRequired[str]  # 安全标记 "safe" / "warning" / "emergency"
    next_agent: NotRequired[str]  # Supervisor 路由决策
    rag_context: NotRequired[str]  # RAG 检索到的上下文
    session_summary: NotRequired[str]  # 会话摘要（用于上下文压缩）


class SupervisorDecision(BaseModel):
    """Supervisor 的路由决策。"""

    next_agent: Literal[
        "ask_symptom_agent",
        "recommend_product_agent",
        "safe_check_agent",
        "FINISH",
    ] = Field(description="下一个要调用的 Agent；FINISH 表示直接结束")
    direct_response: str = Field(
        default="",
        description="当用户只是打招呼或闲聊、无需调用 Agent 时的直接回复",
    )


class SymptomAssessment(BaseModel):
    """问诊 Agent 的结构化输出。"""

    symptoms: list[str] = Field(default_factory=list, description="提取到的症状列表")
    possible_diseases: list[str] = Field(default_factory=list, description="可能的疾病")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="诊断置信度 0-1")
    care_advice: str = Field(default="", description="护理建议")
    needs_more_info: bool = Field(default=False, description="是否还需追问更多信息")
    reply: str = Field(default="", description="给用户的自然语言回复")


class ProductItem(BaseModel):
    """单条产品推荐（结构化）。"""

    name: str = Field(description="产品名称")
    category: str = Field(default="", description="类别：药品/保健品/食品等")
    reason: str = Field(default="", description="推荐理由")
    usage: str = Field(default="", description="用法用量")
    price: str = Field(default="", description="参考价格")


class ProductList(BaseModel):
    """产品推荐 Agent 的结构化输出。"""

    recommendations: list[ProductItem] = Field(default_factory=list)
    reply: str = Field(default="", description="给用户的自然语言回复")


class SafetyReview(BaseModel):
    """安全 Agent 的结构化输出。"""

    safety_flag: Literal["safe", "warning", "emergency"]
    reason: str = Field(default="", description="判定理由")
    advice: str = Field(default="", description="给用户的建议")
