"""Prompt 工程验证脚本：静态校验提示词，并可选做在线路由回归。

用法：
    python scripts/verify_prompts.py           # 仅静态校验（离线，不需要 API Key）
    python scripts/verify_prompts.py --live     # 额外调用 Supervisor 验证路由（需要可用 LLM）

在线用例覆盖易混场景（症状 vs 产品、紧急 vs 普通、闲聊等）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许以 `python scripts/verify_prompts.py` 直接运行时导入项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage  # noqa: E402

from petdoctor.agents import (  # noqa: E402
    ask_symptom,
    recommend_product,
    safe_check,
    supervisor,
)

# 期望路由：(用户输入, 期望 next_agent)
ROUTING_CASES = [
    ("我家狗一直抓痒，是什么病？", "ask_symptom_agent"),
    ("有什么药可以治猫藓？", "recommend_product_agent"),
    ("看看旺财之前的病历", "record_agent"),
    ("我家猫好像吃了老鼠药，一直抽搐", "safe_check_agent"),
    ("你好呀", "FINISH"),
    ("帮我预约明天10点洗澡", "FINISH"),
]

# 静态检查：(名称, 提示词, 必须包含的子串, 禁止出现的子串)
STATIC_CHECKS = [
    (
        "SUPERVISOR_PROMPT",
        supervisor.SUPERVISOR_PROMPT,
        ["判别示例", "FINISH", "direct_response"],
        ["如果上一轮是问诊Agent的输出"],  # 旧的、与代码逻辑冲突的规则
    ),
    (
        "ASK_SYMPTOM_PROMPT",
        ask_symptom.ASK_SYMPTOM_PROMPT,
        ["只做问诊", "不推荐任何具体产品", "示例"],
        [],
    ),
    (
        "RECOMMEND_PRODUCT_PROMPT",
        recommend_product.RECOMMEND_PRODUCT_PROMPT,
        ["禁止跨物种用药", "不得编造", "示例"],
        [],
    ),
    (
        "SAFE_CHECK_PROMPT",
        safe_check.SAFE_CHECK_PROMPT,
        ["正反例", "emergency", "warning", "safe"],
        [],
    ),
]


def run_static() -> int:
    """校验提示词可加载、可格式化且包含关键约束。返回失败数。"""
    failures = 0

    # 占位符格式化校验（避免运行期 KeyError / ValueError）
    try:
        supervisor.SUPERVISOR_PROMPT.format(pet_info="测试")
        supervisor.SUMMARY_PROMPT.format(transcript="测试")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 提示词格式化失败：{exc}")
        failures += 1

    for name, prompt, must_have, must_not in STATIC_CHECKS:
        missing = [s for s in must_have if s not in prompt]
        forbidden = [s for s in must_not if s in prompt]
        if not prompt.strip():
            print(f"[FAIL] {name} 为空")
            failures += 1
        if missing:
            print(f"[FAIL] {name} 缺少关键内容：{missing}")
            failures += 1
        if forbidden:
            print(f"[FAIL] {name} 仍含应删除内容：{forbidden}")
            failures += 1

    if failures == 0:
        print("[OK] 静态校验通过")
    return failures


def run_live() -> int:
    """在线调用 Supervisor，校验易混用例的路由是否正确。返回失败数。"""
    failures = 0
    for text, expected in ROUTING_CASES:
        result = supervisor._SUPERVISOR_AGENT.invoke(
            {"messages": [HumanMessage(content=text)], "pet_profile": {}}
        )
        decision = result.get("structured_response")
        actual = decision.next_agent if decision else None
        ok = actual == expected
        failures += 0 if ok else 1
        print(f"[{'OK' if ok else 'FAIL'}] {text!r} -> {actual}（期望 {expected}）")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt 工程验证")
    parser.add_argument("--live", action="store_true", help="额外执行在线路由回归（需要 LLM）")
    args = parser.parse_args()

    failures = run_static()
    if args.live:
        failures += run_live()

    if failures:
        print(f"\n共 {failures} 项未通过")
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    main()
