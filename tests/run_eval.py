"""PetDoctorAgent regression evaluator (CLI).

Usage
-----
    .\\.venv\\Scripts\\python.exe tests\\run_eval.py --list
    .\\.venv\\Scripts\\python.exe tests\\run_eval.py --priority P0 --repeat 3
    .\\.venv\\Scripts\\python.exe tests\\run_eval.py --category SAFE-MED EMERG
    .\\.venv\\Scripts\\python.exe tests\\run_eval.py --tag toxicity --judge
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("PET_RAG_TRANSLATE", "1")

from tests.runner import assertions as assertions_mod  # noqa: E402
from tests.runner import report as report_mod  # noqa: E402
from tests.runner.harness import Harness  # noqa: E402
from tests.runner.judge import judge_case  # noqa: E402
from tests.runner.loader import filter_cases, load_cases  # noqa: E402


def _git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PetDoctorAgent badcase evaluator")
    parser.add_argument("--priority", nargs="+", help="P0/P1/P2/P3")
    parser.add_argument("--category", nargs="+", help="category id, e.g. SAFE-MED EMERG")
    parser.add_argument("--tag", nargs="+", help="tag filter")
    parser.add_argument("--scenario", nargs="+", help="normal/boundary/abnormal/safety")
    parser.add_argument("--case", nargs="+", help="specific case_id(s)")
    parser.add_argument("--repeat", type=int, default=None, help="override repeat count")
    parser.add_argument("--limit", type=int, default=None, help="limit case count")
    parser.add_argument("--judge", action="store_true", help="enable LLM-as-judge")
    parser.add_argument("--judge-all", action="store_true", help="judge every run (costly)")
    parser.add_argument("--list", action="store_true", help="list cases and exit")
    parser.add_argument("--tag-report", default="", help="report filename tag")
    parser.add_argument("--no-report", action="store_true", help="skip writing reports")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases()
    cases = filter_cases(
        cases,
        priority=args.priority,
        category=args.category,
        tags=args.tag,
        case_ids=args.case,
        scenario=args.scenario,
    )
    if args.limit:
        cases = cases[: args.limit]

    if args.list:
        print(f"{'case_id':<18} {'pri':<4} {'category':<10} {'scenario':<10} name")
        for c in cases:
            print(f"{c.case_id:<18} {c.priority:<4} {c.category:<10} {c.scenario_type:<10} {c.name}")
        print(f"\n共 {len(cases)} 条")
        return 0

    if not cases:
        print("没有匹配的用例。")
        return 1

    repeat_override = f" --repeat {args.repeat}" if args.repeat else " (P0=3, 其余=1)"
    print(f"=== 运行 {len(cases)} 条用例{repeat_override} | judge={args.judge} ===")

    harness = Harness()
    results = []
    for index, case in enumerate(cases, 1):
        repeat = args.repeat if args.repeat else case.repeat
        runs = []
        for run_index in range(repeat):
            observed = harness.run_once(case, run_index)
            assertion_results = assertions_mod.evaluate(case.assertions, observed)
            passed = all(a["passed"] for a in assertion_results) and not observed["errors"]
            run = {"run_index": run_index, "observed": observed,
                   "assertions": assertion_results, "passed": passed, "judge": None}
            runs.append(run)

        passed_runs = sum(1 for r in runs if r["passed"])
        total_runs = len(runs)
        pass_rate = passed_runs / total_runs if total_runs else 0.0
        if pass_rate == 1.0:
            status = "pass"
        elif pass_rate == 0.0:
            status = "fail"
        else:
            status = "flaky"

        if args.judge and case.judge_rubric:
            if args.judge_all:
                target = runs
            else:
                target = [r for r in runs if not r["passed"]]
            for run in target:
                run["judge"] = judge_case(case.judge_rubric, run["observed"], case)

        flag = "PASS" if status == "pass" else ("FLAKY" if status == "flaky" else "FAIL")
        red = " ***P0***" if status != "pass" and case.priority == "P0" else ""
        print(f"[{index:>3}/{len(cases)}] {flag:<5} {case.case_id:<18} "
              f"rate={pass_rate*100:>3.0f}% {case.name}{red}")

        results.append({
            "case_id": case.case_id,
            "name": case.name,
            "priority": case.priority,
            "category": case.category,
            "scenario_type": case.scenario_type,
            "species": case.species,
            "tags": case.tags,
            "expected_outcome": case.expected_outcome,
            "runs": runs,
            "pass_rate": pass_rate,
            "passed": passed_runs,
            "status": status,
        })

    previous = report_mod.find_previous_result()
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": os.getenv("LLM_MODEL_ID", "unknown"),
        "git": _git_rev(),
        "filters": {
            "priority": args.priority, "category": args.category, "tag": args.tag,
            "scenario": args.scenario, "case": args.case, "repeat": args.repeat,
            "judge": args.judge,
        },
        "previous": previous,
    }
    summary = report_mod.summarize(results, meta)

    t = summary["totals"]
    print("\n=== 总览 ===")
    print(f"用例 {t['cases']} | 通过率 {t['pass_rate']*100:.1f}% "
          f"({t['passed_runs']}/{t['runs']}) | 通过/失败/flaky "
          f"{t['case_pass']}/{t['case_fail']}/{t['case_flaky']}")
    if t["p0_pass_rate"] is not None:
        print(f"P0 通过率 {t['p0_pass_rate']*100:.1f}% | P0 失败用例 {t['p0_fail_cases']}")

    if not args.no_report:
        json_path, md_path = report_mod.write_outputs(summary, args.tag_report)
        print(f"\n已写出：{json_path}")
        print(f"已写出：{md_path}")

    return 0 if t["case_fail"] == 0 and t["case_flaky"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
