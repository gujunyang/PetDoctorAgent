"""Evaluation harness: builds the graph once, runs regression cases in isolation.

Isolation / reproducibility choices
-----------------------------------
* InMemorySaver + InMemoryStore are injected explicitly, so a missing PostgreSQL
  never blocks the run (business code untouched).
* MCP loading is monkeypatched to ``[]`` (harness-level) and deterministic mock MCP
  tools are injected into the relevant agents, matching the real tool schemas.
* Every case/repeat gets a unique ``user_id`` and ``thread_id`` so memory/history
  cannot leak between runs.
"""

from __future__ import annotations

import os
import traceback
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

os.environ.setdefault("DATABASE_URL", "")  # defensive: never touch a real DB here

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.store.memory import InMemoryStore  # noqa: E402

from petdoctor import memory  # noqa: E402
from petdoctor.tools import mcp_client  # noqa: E402

from . import mock_tools  # noqa: E402
from .trace import TraceCollector  # noqa: E402

# Skip the real MCP connection attempt (would otherwise block ~5s and add noise).
mcp_client.load_mcp_tools = lambda: []  # type: ignore[assignment]

from petdoctor.agents import (  # noqa: E402
    ask_symptom,
    record,
)
from petdoctor.graph import build_graph  # noqa: E402


class Harness:
    """Owns a single compiled graph and runs cases against it."""

    def __init__(self) -> None:
        self._checkpointer = InMemorySaver()
        self._store = InMemoryStore()
        self.app = build_graph(checkpointer=self._checkpointer, store=self._store, trace=False)
        # Inject deterministic mock MCP tools (nodes read module globals at call time).
        ask_symptom.configure_agent(mock_tools.MEDICAL_TOOLS)
        record.configure_tools(mock_tools.ALL_TOOLS)

    # ── setup helpers ─────────────────────────────────────────────────────
    def _preload_pet(self, case, user_id: str) -> str:
        pet_id = f"PET-{case.case_id}"
        info = dict(case.pet_profile or {})
        info["pet_id"] = pet_id
        memory.create_pet(user_id, info)
        return pet_id

    # ── run a single case once ────────────────────────────────────────────
    def run_once(self, case, run_index: int = 0) -> dict[str, Any]:
        mock_tools.reset()
        user_id = f"eval_{case.case_id}_r{run_index}"
        thread_id = f"{case.case_id}-r{run_index}"
        config = memory.make_config(session_id=thread_id, user_id=user_id)
        config["recursion_limit"] = 40

        pet_id = None
        if case.mode == "preloaded" and case.pet_profile:
            pet_id = self._preload_pet(case, user_id)

        transcript: list[str] = []
        flags: list[str] = []
        tool_calls: list[dict] = []
        node_steps: list[str] = []
        input_tokens = output_tokens = total_tokens = 0
        max_latency = 0.0
        errors: list[str] = []
        final_state: dict = {}
        turn_records: list[dict] = []

        for turn_index, content in enumerate(case.turns):
            state_input: dict[str, Any] = {"messages": [HumanMessage(content=content)]}
            if turn_index == 0 and pet_id:
                state_input["active_pet_id"] = pet_id
            collector = TraceCollector()
            config["callbacks"] = [collector]
            try:
                result = self.app.invoke(state_input, config=config)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"turn{turn_index}: {type(exc).__name__}: {exc}")
                errors.append(traceback.format_exc(limit=2))
                turn_records.append({"input": content, "error": str(exc)})
                break

            reply = _last_ai_text(result.get("messages", []))
            transcript.append(reply)
            flag = result.get("safety_flag")
            if flag:
                flags.append(str(flag))
            final_state = result
            tool_calls.extend(collector.tool_calls)
            node_steps.extend(collector.node_steps)
            input_tokens += collector.input_tokens
            output_tokens += collector.output_tokens
            total_tokens += collector.total_tokens
            max_latency = max(max_latency, collector.elapsed_ms)
            turn_records.append({
                "input": content,
                "reply": reply,
                "safety_flag": flag,
                "tool_names": [c["name"] for c in collector.tool_calls],
                "steps": collector.node_steps,
                "elapsed_ms": round(collector.elapsed_ms, 1),
                "tokens": collector.total_tokens,
            })

        observed = {
            "assistant_text": "\n".join(transcript),
            "final_answer": transcript[-1] if transcript else "",
            "flags": flags,
            "final_flag": flags[-1] if flags else None,
            "tool_names": [c["name"] for c in tool_calls],
            "tool_calls": tool_calls,
            "node_steps": node_steps,
            "steps": len(node_steps),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "max_latency_ms": max_latency,
            "errors": errors,
            "diagnosis": final_state.get("diagnosis"),
            "product_recommendations": final_state.get("product_recommendations"),
            "active_pet_id": final_state.get("active_pet_id"),
            "turns": turn_records,
        }
        return observed


def _last_ai_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                return message.content
    return ""
