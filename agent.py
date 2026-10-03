"""PetDoctorAgent 入口：Supervisor + Worker 多 Agent 问诊系统（含记忆系统）。

用法：
    python agent.py --user alice            # 新会话（thread_id 自动生成 uuid4）
    python agent.py --user alice --session <id>   # 复用已有会话
"""

import argparse
import os

from langchain_core.messages import AIMessage, HumanMessage

import memory
from graph import build_graph


def _print_answer(result: dict) -> None:
    for message in reversed(result.get("messages", [])):
        if isinstance(message, AIMessage) and isinstance(message.content, str):
            if message.content.strip():
                print(message.content)
                return


def main() -> None:
    parser = argparse.ArgumentParser(description="宠物问诊多 Agent 系统")
    parser.add_argument("--user", default=os.getenv("PET_USER_ID", "default"), help="用户 ID")
    parser.add_argument("--session", default=None, help="会话 ID（thread_id），默认生成 uuid4")
    args = parser.parse_args()

    # 默认按 DATABASE_URL 创建 PostgresSaver / PostgresStore（不可用则回退内存）
    app = build_graph()

    session_id = args.session or memory.new_session_id()
    config = memory.make_config(session_id=session_id, user_id=args.user)
    config["recursion_limit"] = 25
    print(f"=== 宠物问诊多 Agent 系统（user={args.user}, session={session_id}）===")
    print("输入 q 退出")

    while True:
        query = input("\n请输入你的问题：").strip()
        if query.lower() in {"q", "quit", "exit"}:
            break
        if not query:
            continue

        result = app.invoke({"messages": [HumanMessage(content=query)]}, config=config)
        print("\n--- 回复 ---")
        _print_answer(result)
        if result.get("safety_flag"):
            print(f"[safety_flag] {result['safety_flag']}")


if __name__ == "__main__":
    main()
