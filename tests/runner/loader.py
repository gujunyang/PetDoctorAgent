"""Load and normalize regression cases from tests/regression/*.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REGRESSION_DIR = Path(__file__).resolve().parents[1] / "regression"


@dataclass
class Case:
    case_id: str
    name: str
    priority: str
    category: str
    scenario_type: str = "normal"
    species: str | None = None
    pet_profile: dict | None = None
    preconditions: list[str] = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    conversation: list[dict] = field(default_factory=list)
    expected_outcome: str = ""
    assertions: dict = field(default_factory=dict)
    judge_rubric: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None

    @property
    def mode(self) -> str:
        return str(self.setup.get("mode", "preloaded" if self.pet_profile else "identify"))

    @property
    def repeat(self) -> int:
        return int(self.setup.get("repeat", 3 if self.priority == "P0" else 1))

    @property
    def turns(self) -> list[str]:
        return [str(t.get("content", "")) for t in self.conversation]


def _normalize(raw: dict) -> Case:
    data = dict(raw)
    if "conversation" not in data and "input" in data:
        data["conversation"] = [{"role": "user", "content": data.pop("input")}]
    data.setdefault("case_id", data.get("id"))
    data.setdefault("priority", "P1")
    data.setdefault("category", "UNKNOWN")
    data.setdefault("assertions", {})
    if isinstance(data.get("tags"), str):
        data["tags"] = [data["tags"]]
    known = set(Case.__dataclass_fields__)  # type: ignore[attr-defined]
    data = {k: v for k, v in data.items() if k in known}
    return Case(**data)


def load_cases(paths: list[Path] | None = None) -> list[Case]:
    files = paths if paths else sorted(REGRESSION_DIR.glob("*.yaml"))
    cases: list[Case] = []
    for path in files:
        if path.is_dir():
            continue
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or []
        if isinstance(data, dict):
            data = data.get("cases", [])
        for item in data:
            cases.append(_normalize(item))
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    return cases


def filter_cases(cases: list[Case], priority: list[str] | None = None,
                 category: list[str] | None = None, tags: list[str] | None = None,
                 case_ids: list[str] | None = None,
                 scenario: list[str] | None = None) -> list[Case]:
    result = cases
    if priority:
        wanted = {p.upper() for p in priority}
        result = [c for c in result if c.priority.upper() in wanted]
    if category:
        wanted = {p.upper() for p in category}
        result = [c for c in result if c.category.upper() in wanted]
    if scenario:
        wanted = {p.lower() for p in scenario}
        result = [c for c in result if c.scenario_type.lower() in wanted]
    if tags:
        wanted = {t.lower() for t in tags}
        result = [c for c in result if wanted & {t.lower() for t in c.tags}]
    if case_ids:
        wanted = set(case_ids)
        result = [c for c in result if c.case_id in wanted]
    return result
