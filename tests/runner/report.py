"""Aggregate evaluation results and render human-readable Markdown reports."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def summarize(results: list[dict], meta: dict) -> dict:
    total_runs = sum(len(r["runs"]) for r in results)
    passed_runs = sum(sum(1 for run in r["runs"] if run["passed"]) for r in results)

    by_priority: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [passed_runs, runs]
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for result in results:
        for run in result["runs"]:
            for bucket, key in ((by_priority, result["priority"]), (by_category, result["category"])):
                bucket[key][1] += 1
                if run["passed"]:
                    bucket[key][0] += 1

    p0 = by_priority.get("P0", [0, 0])
    p0_runs = p0[1]
    p0_passed = p0[0]

    steps = [run["observed"]["steps"] for r in results for run in r["runs"]]
    tokens = [run["observed"]["total_tokens"] for r in results for run in r["runs"]]
    latencies = [run["observed"]["max_latency_ms"] for r in results for run in r["runs"]]

    # tool analysis
    missing_calls = Counter()
    wrong_calls = Counter()
    arg_errors = Counter()
    for r in results:
        for run in r["runs"]:
            if run["passed"]:
                continue
            for a in run["assertions"]:
                if not a["passed"] and a["name"].startswith("must_call:"):
                    missing_calls[a["name"].split(":", 1)[1]] += 1
                if not a["passed"] and a["name"].startswith("must_not_call:"):
                    wrong_calls[a["name"].split(":", 1)[1]] += 1
                if not a["passed"] and a["name"].startswith("tool_arg:"):
                    arg_errors[a["name"].split(":", 1)[1]] += 1

    failures = []
    for r in results:
        if r["status"] == "pass":
            continue
        failed_runs = [run for run in r["runs"] if not run["passed"]]
        first = failed_runs[0]
        failures.append({
            "case_id": r["case_id"],
            "name": r["name"],
            "priority": r["priority"],
            "category": r["category"],
            "status": r["status"],
            "pass_rate": r["pass_rate"],
            "expected": r["expected_outcome"],
            "actual": _actual(first),
            "failed_assertions": [a["name"] for a in first["assertions"] if not a["passed"]],
            "root_cause_guess": _root_cause(first),
            "reproduce": f".\\.venv\\Scripts\\python.exe tests\\run_eval.py --case {r['case_id']} --repeat 3",
            "judge": first.get("judge"),
        })

    return {
        "meta": meta,
        "totals": {
            "cases": len(results),
            "runs": total_runs,
            "passed_runs": passed_runs,
            "pass_rate": (passed_runs / total_runs) if total_runs else 0.0,
            "case_pass": sum(1 for r in results if r["status"] == "pass"),
            "case_fail": sum(1 for r in results if r["status"] == "fail"),
            "case_flaky": sum(1 for r in results if r["status"] == "flaky"),
            "p0_runs": p0_runs,
            "p0_passed_runs": p0_passed,
            "p0_pass_rate": (p0_passed / p0_runs) if p0_runs else None,
            "p0_fail_cases": sum(1 for r in results if r["priority"] == "P0" and r["status"] != "pass"),
        },
        "by_priority": {k: {"passed": v[0], "runs": v[1],
                            "rate": (v[0] / v[1]) if v[1] else 0.0}
                        for k, v in sorted(by_priority.items())},
        "by_category": {k: {"passed": v[0], "runs": v[1],
                            "rate": (v[0] / v[1]) if v[1] else 0.0}
                        for k, v in sorted(by_category.items())},
        "cost": {
            "avg_steps": (sum(steps) / len(steps)) if steps else 0.0,
            "avg_tokens": (sum(tokens) / len(tokens)) if tokens else 0.0,
            "total_tokens": sum(tokens),
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
        },
        "tool_analysis": {
            "missing_calls": missing_calls.most_common(10),
            "wrong_calls": wrong_calls.most_common(10),
            "arg_errors": arg_errors.most_common(10),
        },
        "failures": failures,
        "results": results,
    }


def _actual(run: dict) -> str:
    obs = run["observed"]
    parts = [f"safety_flag={obs.get('final_flag')}", f"steps={obs['steps']}"]
    if obs.get("final_answer"):
        parts.append("final_answer=" + obs["final_answer"][:400].replace("\n", " "))
    if obs.get("errors"):
        parts.append("errors=" + "; ".join(obs["errors"])[:300])
    return " | ".join(parts)


def _root_cause(run: dict) -> str:
    names = [a["name"] for a in run["assertions"] if not a["passed"]]
    joined = " ".join(names)
    if "must_flag" in joined:
        return "安全护栏/急诊路由（safe_check 未命中或未分级）"
    if "must_not_contain" in joined:
        return "安全护栏未拦截危险内容（越界/有毒建议/免责缺失）"
    if "must_contain" in joined:
        return "免责或必要提示缺失（未提示线下就诊/兽医）"
    if "must_call" in joined:
        return "工具调用缺失（漏调，或 MCP 工具未注入）"
    if "tool_arg" in joined:
        return "工具参数错误（单位/物种/日期/相对日期换算）"
    if "max_steps" in joined or "max_latency" in joined:
        return "编排效率（步数/延迟超标）"
    if "must_not_call" in joined:
        return "工具错误调用（不该调用却调用）"
    if run["observed"].get("errors"):
        return "运行异常（异常/超时，见 errors）"
    return "待人工分析（LLM 语义质量，建议看 judge）"


def render_markdown(summary: dict) -> str:
    t = summary["totals"]
    meta = summary["meta"]
    lines: list[str] = []
    lines.append("# 宠物问诊 Agent · Badcase 评估报告")
    lines.append("")
    lines.append(f"- 生成时间：{meta.get('generated_at')}")
    lines.append(f"- 模型：`{meta.get('model')}` | git：`{meta.get('git')}`")
    lines.append(f"- 过滤：{meta.get('filters')} | repeat：P0 默认 3，其余默认 1")
    lines.append("")

    lines.append("## 一、总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 用例数 / 运行数 | {t['cases']} / {t['runs']} |")
    lines.append(f"| 总通过率 | {t['pass_rate']*100:.1f}% ({t['passed_runs']}/{t['runs']}) |")
    lines.append(f"| 通过/失败/Flaky 用例 | {t['case_pass']} / {t['case_fail']} / {t['case_flaky']} |")
    if t["p0_pass_rate"] is not None:
        mark = "🟢" if t["p0_pass_rate"] == 1.0 else "🔴"
        lines.append(f"| **P0 通过率** | {mark} **{t['p0_pass_rate']*100:.1f}%** ({t['p0_passed_runs']}/{t['p0_runs']}) |")
        lines.append(f"| **P0 失败用例数（漏检）** | {mark if t['p0_fail_cases'] else '🟢'} **{t['p0_fail_cases']}** |")
    lines.append("")

    lines.append("### 按优先级")
    lines.append("")
    lines.append("| 优先级 | 通过/运行 | 通过率 |")
    lines.append("|---|---|---|")
    for key, val in summary["by_priority"].items():
        lines.append(f"| {key} | {val['passed']}/{val['runs']} | {val['rate']*100:.1f}% |")
    lines.append("")

    lines.append("### 按类别（失败分布）")
    lines.append("")
    lines.append("| 类别 | 通过/运行 | 通过率 |")
    lines.append("|---|---|---|")
    for key, val in summary["by_category"].items():
        flag = " 🔴" if val["rate"] < 1.0 else ""
        lines.append(f"| {key}{flag} | {val['passed']}/{val['runs']} | {val['rate']*100:.1f}% |")
    lines.append("")

    cost = summary["cost"]
    lines.append("## 二、成本与延迟")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 平均步数 | {cost['avg_steps']:.1f} |")
    lines.append(f"| 平均 token | {cost['avg_tokens']:.0f} |")
    lines.append(f"| 总 token | {cost['total_tokens']} |")
    lines.append(f"| 延迟 P50 / P95 | {cost['p50_latency_ms']:.0f}ms / {cost['p95_latency_ms']:.0f}ms |")
    lines.append("")

    ta = summary["tool_analysis"]
    lines.append("## 三、工具调用分析")
    lines.append("")
    lines.append(f"- 漏调 Top：{ta['missing_calls'] or '无'}")
    lines.append(f"- 错调 Top：{ta['wrong_calls'] or '无'}")
    lines.append(f"- 参数错误 Top：{ta['arg_errors'] or '无'}")
    lines.append("")

    lines.append("## 四、失败清单")
    lines.append("")
    if not summary["failures"]:
        lines.append("无失败用例。")
    for f in summary["failures"]:
        if f["priority"] == "P0":
            lines.append(f"### 🔴🔴🔴 P0 · {f['case_id']} · {f['name']}")
        elif f["priority"] == "P1":
            lines.append(f"### 🔴 P1 · {f['case_id']} · {f['name']}")
        else:
            lines.append(f"### {f['priority']} · {f['case_id']} · {f['name']}")
        lines.append("")
        lines.append(f"- 状态：{f['status']}（通过率 {f['pass_rate']*100:.0f}%）")
        lines.append(f"- 期望：{f['expected']}")
        lines.append(f"- 实际：{f['actual']}")
        lines.append(f"- 失败断言：{f['failed_assertions']}")
        lines.append(f"- 根因猜测：{f['root_cause_guess']}")
        if f.get("judge"):
            lines.append(f"- Judge：{f['judge']}")
        lines.append(f"- 复现：`{f['reproduce']}`")
        lines.append("")

    lines.append("## 五、版本对比")
    lines.append("")
    prev = meta.get("previous") or {}
    if prev:
        delta = t["pass_rate"] - prev.get("pass_rate", 0.0)
        lines.append(f"- 上一版本：{prev.get('file')} 通过率 {prev.get('pass_rate', 0)*100:.1f}%")
        lines.append(f"- 本次通过率 {t['pass_rate']*100:.1f}%（Δ {delta*100:+.1f}pp）")
    else:
        lines.append("无历史结果可对比。")
    lines.append("")
    return "\n".join(lines)


def write_outputs(summary: dict, tag: str = "") -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{tag}" if tag else ""
    json_path = REPORTS_DIR / f"eval_{stamp}{suffix}.json"
    md_path = REPORTS_DIR / f"eval_{stamp}{suffix}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(summary), encoding="utf-8")
    return json_path, md_path


def find_previous_result(exclude: Path | None = None) -> dict | None:
    files = sorted(REPORTS_DIR.glob("eval_*.json"))
    for path in reversed(files):
        if exclude and path == exclude:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        return {"file": path.name, "pass_rate": data.get("totals", {}).get("pass_rate", 0.0)}
    return None
