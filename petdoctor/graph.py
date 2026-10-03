"""Supervisor + Worker 多 Agent 问诊系统的图组装（含记忆系统与 MCP 工具）。

流程：
    START -> load_memory -> supervisor
    supervisor --(条件边: next_agent)--> ask_symptom_agent / recommend_product_agent
                                        / safe_check_agent / appointment_agent / END
    ask_symptom_agent / recommend_product_agent / appointment_agent --> supervisor（循环）
    safe_check_agent --> END

记忆：
    - 短期记忆 checkpointer（PostgresSaver）按 thread_id 保存会话状态；
    - 长期记忆 store（PostgresStore）由 load_memory 注入宠物档案、
      safe_check 写回问诊历史摘要。

MCP：
    图初始化时从 MCP Server 获取业务工具，按名称合并到对应 Agent：
    - ask_symptom_agent       <- get_pet_medical_record
    - recommend_product_agent <- check_product_stock
    - appointment_agent       <- check_appointment_slots / create_appointment
    MCP 服务未启动时自动降级（跳过 MCP 工具，图仍可用）。

MCP 工具是异步工具，``tools/mcp_client.load_mcp_tools()`` 会将其桥接为同步工具，
因此整图保持同步，使用 ``app.invoke(...)`` 调用即可。
"""

import logging
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from petdoctor import memory
from petdoctor.agents import appointment, ask_symptom, recommend_product, record
from petdoctor.agents.record import record_node
from petdoctor.identity import identify_pet_node
from petdoctor.agents.appointment import appointment_node
from petdoctor.agents.ask_symptom import ask_symptom_node
from petdoctor.agents.recommend_product import recommend_product_node
from petdoctor.agents.safe_check import safe_check_node
from petdoctor.agents.supervisor import supervisor_node
from petdoctor.state import PetClinicState
from petdoctor.tools import mcp_client

WORKER_NODES = {
    "ask_symptom_agent",
    "recommend_product_agent",
    "safe_check_agent",
    "appointment_agent",
    "record_agent",
}

# MCP 工具 -> 目标 Agent 的映射
MCP_MEDICAL_TOOLS = {"get_pet_medical_record"}
MCP_PRODUCT_TOOLS = {"check_product_stock"}
MCP_APPOINTMENT_TOOLS = {"check_appointment_slots", "create_appointment"}


logger = logging.getLogger("petdoctor.trace")


def setup_logging(level: int = logging.INFO) -> None:
    """配置根日志（供执行轨迹日志使用）。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _truncate(value: Any, limit: int = 300) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


class TraceLogger(BaseCallbackHandler):
    """记录每个节点（chain）的开始/结束时间与输出。"""

    def __init__(self, trace_logger: logging.Logger | None = None) -> None:
        self.logger = trace_logger or logger
        self._started: dict[Any, tuple[str, float]] = {}

    def _name(self, serialized: Any, metadata: Any) -> str:
        if isinstance(serialized, dict) and serialized.get("name"):
            return str(serialized["name"])
        if isinstance(metadata, dict) and metadata.get("langgraph_node"):
            return str(metadata["langgraph_node"])
        return "chain"

    def on_chain_start(
        self,
        serialized: Any,
        inputs: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        tags: Any = None,
        metadata: Any = None,
        **kwargs: Any,
    ) -> None:
        name = self._name(serialized, metadata)
        self._started[run_id] = (name, time.perf_counter())
        self.logger.info("Node started: %s", name)

    def on_chain_end(
        self,
        outputs: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        name, started_at = self._started.pop(run_id, ("chain", None))
        elapsed = time.perf_counter() - started_at if started_at else 0.0
        self.logger.info("Node ended: %s (%.3fs) outputs=%s", name, elapsed, _truncate(outputs))

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        name, _ = self._started.pop(run_id, ("chain", None))
        self.logger.error("Node error: %s: %s", name, error)


def route_from_supervisor(state: PetClinicState) -> str:
    """读取 Supervisor 的 ``next_agent`` 决策，返回对应的下一跳节点名。"""
    next_agent = state.get("next_agent", "FINISH")
    return next_agent if next_agent in WORKER_NODES else END


def route_after_identify(state: PetClinicState) -> str:
    """已识别宠物则进入 Supervisor；否则（已追问用户）结束本轮等待回答。"""
    return "supervisor" if state.get("active_pet_id") else END


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


def _configure_agents(mcp_tools: list[Any]) -> None:
    """把 MCP 工具按名称合并到对应 Agent 的 tools。"""
    ask_symptom.configure_agent(mcp_client.select_tools(mcp_tools, MCP_MEDICAL_TOOLS))
    recommend_product.configure_agent(mcp_client.select_tools(mcp_tools, MCP_PRODUCT_TOOLS))
    appointment.configure_agent(mcp_client.select_tools(mcp_tools, MCP_APPOINTMENT_TOOLS))
    record.configure_tools(mcp_tools)


def _resolve_memory(checkpointer: Any, store: Any) -> tuple[Any, Any]:
    if checkpointer is None:
        checkpointer = memory.get_checkpointer()
    else:
        memory.set_checkpointer(checkpointer)

    if store is None:
        store = memory.get_store()
    else:
        memory.set_store(store)

    return checkpointer, store


def _assemble(checkpointer: Any, store: Any) -> Any:
    workflow = StateGraph(PetClinicState)

    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("identify_pet", identify_pet_node)
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("ask_symptom_agent", ask_symptom_node)
    workflow.add_node("recommend_product_agent", recommend_product_node)
    workflow.add_node("safe_check_agent", safe_check_node)
    workflow.add_node("appointment_agent", appointment_node)
    workflow.add_node("record_agent", record_node)

    workflow.add_edge(START, "load_memory")
    workflow.add_edge("load_memory", "identify_pet")

    # 先确认「这次是哪只宠物」；未识别则结束本轮（已追问用户）
    workflow.add_conditional_edges(
        "identify_pet",
        route_after_identify,
        {"supervisor": "supervisor", END: END},
    )

    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "ask_symptom_agent": "ask_symptom_agent",
            "recommend_product_agent": "recommend_product_agent",
            "safe_check_agent": "safe_check_agent",
            "appointment_agent": "appointment_agent",
            "record_agent": "record_agent",
            END: END,
        },
    )

    workflow.add_edge("ask_symptom_agent", "supervisor")
    workflow.add_edge("recommend_product_agent", "supervisor")
    workflow.add_edge("appointment_agent", "supervisor")
    workflow.add_edge("safe_check_agent", END)
    workflow.add_edge("record_agent", END)

    return workflow.compile(checkpointer=checkpointer, store=store)


def build_graph(checkpointer: Any = None, store: Any = None, trace: bool = True) -> Any:
    """同步构建入口。

    Args:
        checkpointer: 短期记忆存储器；None 时按 DATABASE_URL 创建 PostgresSaver。
        store: 长期记忆存储；None 时按 DATABASE_URL 创建 PostgresStore。
        trace: 是否附加执行轨迹日志回调。``compile()`` 不接受 callbacks，
            故通过 ``with_config({"callbacks": [...]})`` 附加（等价于编译期传入）。
    """
    checkpointer, store = _resolve_memory(checkpointer, store)
    mcp_tools = mcp_client.load_mcp_tools()
    _configure_agents(mcp_tools)

    graph = _assemble(checkpointer, store)
    if trace:
        setup_logging()
        return graph.with_config({"callbacks": [TraceLogger()]})
    return graph
