"""Supervisor + Worker 多 Agent 问诊系统的图组装（含记忆系统）。

流程：
    START -> load_memory -> supervisor
    supervisor --(条件边: next_agent)--> ask_symptom_agent / recommend_product_agent / safe_check_agent / END
    ask_symptom_agent / recommend_product_agent --> supervisor   （循环）
    safe_check_agent --> END

记忆：
    - 短期记忆 checkpointer（PostgresSaver）按 thread_id 保存会话状态；
    - 长期记忆 store（PostgresStore）由 load_memory 注入宠物档案、
      safe_check 写回问诊历史摘要。
    两者未显式传入时，默认从 memory 模块按 DATABASE_URL 创建（不可用则回退进程内实现）。
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

import memory
from agents.ask_symptom_agent import ask_symptom_node
from agents.recommend_product_agent import recommend_product_node
from agents.safe_check_agent import safe_check_node
from agents.supervisor import supervisor_node
from state import PetClinicState

WORKER_NODES = {"ask_symptom_agent", "recommend_product_agent", "safe_check_agent"}


def route_from_supervisor(state: PetClinicState) -> str:
    """读取 Supervisor 的 ``next_agent`` 决策，返回对应的下一跳节点名。"""
    next_agent = state.get("next_agent", "FINISH")
    return next_agent if next_agent in WORKER_NODES else END


def load_memory_node(state: PetClinicState, config: RunnableConfig) -> dict:
    """执行前从长期记忆读取用户/宠物档案并注入 State。"""
    user_id = memory.user_id_from_config(config)
    updates: dict = {}

    pet_profile = memory.load_pet_profile(user_id)
    if pet_profile:
        updates["pet_profile"] = pet_profile

    user_profile = memory.load_user_profile(user_id)
    if user_profile and not updates.get("pet_profile"):
        updates["pet_profile"] = user_profile

    return updates


def build_graph(checkpointer: Any = None, store: Any = None):
    """组装并编译多 Agent 图。

    Args:
        checkpointer: 短期记忆存储器；None 时按 DATABASE_URL 创建 PostgresSaver。
        store: 长期记忆存储；None 时按 DATABASE_URL 创建 PostgresStore。
    """
    if checkpointer is None:
        checkpointer = memory.get_checkpointer()
    else:
        memory.set_checkpointer(checkpointer)

    if store is None:
        store = memory.get_store()
    else:
        memory.set_store(store)

    workflow = StateGraph(PetClinicState)

    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("ask_symptom_agent", ask_symptom_node)
    workflow.add_node("recommend_product_agent", recommend_product_node)
    workflow.add_node("safe_check_agent", safe_check_node)

    # 入口：先加载记忆，再进入 Supervisor
    workflow.add_edge(START, "load_memory")
    workflow.add_edge("load_memory", "supervisor")

    # Supervisor 通过条件边路由到对应 Worker（或直接结束）
    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "ask_symptom_agent": "ask_symptom_agent",
            "recommend_product_agent": "recommend_product_agent",
            "safe_check_agent": "safe_check_agent",
            END: END,
        },
    )

    # Worker 执行完返回 Supervisor（循环）；安全审查结束后直接结束
    workflow.add_edge("ask_symptom_agent", "supervisor")
    workflow.add_edge("recommend_product_agent", "supervisor")
    workflow.add_edge("safe_check_agent", END)

    return workflow.compile(checkpointer=checkpointer, store=store)
