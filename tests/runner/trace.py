"""Trace collector: captures tool calls, node steps, token usage and latency.

Uses LangChain callbacks only; no business code is modified.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

GRAPH_NODES = {
    "load_memory",
    "identify_pet",
    "supervisor",
    "ask_symptom_agent",
    "recommend_product_agent",
    "safe_check_agent",
    "record_agent",
}


class TraceCollector(BaseCallbackHandler):
    """Collect a structured trace for a single graph invocation."""

    def __init__(self) -> None:
        super().__init__()
        self.tool_calls: list[dict[str, Any]] = []
        self.node_steps: list[str] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.llm_calls = 0
        self.errors: list[str] = []
        self._node_runs: set[Any] = set()
        self._tool_runs: dict[Any, dict[str, Any]] = {}
        self._t0 = time.perf_counter()

    # ── node steps ────────────────────────────────────────────────────────
    def on_chain_start(self, serialized: Any, inputs: Any, *, run_id: Any = None,
                       parent_run_id: Any = None, tags: Any = None,
                       metadata: Any = None, **kwargs: Any) -> None:
        node = None
        if isinstance(metadata, dict):
            node = metadata.get("langgraph_node")
        if not node and isinstance(serialized, dict):
            name = serialized.get("name")
            node = name if name in GRAPH_NODES else None
        if node in GRAPH_NODES and run_id not in self._node_runs:
            self._node_runs.add(run_id)
            self.node_steps.append(str(node))

    # ── tool calls ────────────────────────────────────────────────────────
    def on_tool_start(self, serialized: Any, input_str: Any, *, run_id: Any = None,
                      parent_run_id: Any = None, tags: Any = None,
                      metadata: Any = None, inputs: Any = None, **kwargs: Any) -> None:
        name = None
        if isinstance(serialized, dict):
            name = serialized.get("name")
        if not name and isinstance(metadata, dict):
            name = metadata.get("tool_name")
        args = inputs if inputs is not None else input_str
        record = {"name": name, "args": _safe(args), "output": None}
        self.tool_calls.append(record)
        if run_id is not None:
            self._tool_runs[run_id] = record

    def on_tool_end(self, output: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        record = self._tool_runs.get(run_id)
        if record is not None:
            record["output"] = _safe(output, limit=800)

    def on_tool_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        self.errors.append(f"tool_error: {error}")

    # ── token usage ───────────────────────────────────────────────────────
    def on_llm_end(self, response: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        self.llm_calls += 1
        usage = _extract_usage(response)
        if usage:
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        self.errors.append(f"llm_error: {error}")

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def summary(self) -> dict[str, Any]:
        return {
            "tool_calls": self.tool_calls,
            "tool_names": [c["name"] for c in self.tool_calls],
            "node_steps": self.node_steps,
            "steps": len(self.node_steps),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "errors": self.errors,
        }


def _safe(value: Any, limit: int = 400) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = value
    else:
        text = repr(value)
    if isinstance(text, str) and len(text) > limit:
        return text[:limit] + "...(truncated)"
    return text


def _extract_usage(response: Any) -> dict[str, Any] | None:
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if usage:
        return {
            "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    generations = getattr(response, "generations", None) or []
    for group in generations:
        for generation in group:
            message = getattr(generation, "message", None)
            meta = getattr(message, "usage_metadata", None)
            if meta:
                return meta
    return None
