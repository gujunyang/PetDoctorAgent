# PetDoctorAgent 测试套件（Badcase / 回归 / 评估）

> 本目录为**新增测试资产**，不修改任何业务代码。运行器通过回调采集轨迹、
> 注入确定性 mock 工具、使用 InMemory 记忆，保证可复现与相互隔离。

## 目录

```
tests/
├── run_eval.py            # 评估运行器（CLI）
├── runner/
│   ├── harness.py         # 构图 + 隔离执行 + 轨迹采集
│   ├── mock_tools.py      # 确定性 mock MCP 工具（schema 对齐 server.py）
│   ├── trace.py           # 回调：工具调用/步数/token/延迟
│   ├── assertions.py      # 规则断言
│   ├── judge.py           # LLM-as-judge（rubric 可人工校准）
│   ├── report.py          # JSON + Markdown 报告
│   └── loader.py          # 用例加载/过滤
├── regression/*.yaml      # 回归用例（按类别）
├── badcases/badcases.jsonl# badcase 库
└── judges/*.md            # judge 评分标准
```

## 用例字段（regression）

| 字段 | 说明 |
|---|---|
| `case_id` | 唯一编号，如 `SAFE-MED-001` |
| `name` | 用例名 |
| `priority` | `P0`/`P1`/`P2`/`P3` |
| `category` | SAFE-MED / EMERG / SPECIES / SCOPE / DISC / TOOL / MEM / RAG / INJECT / MULTI / IDENT / EFFI / NORMAL |
| `scenario_type` | normal / boundary / abnormal / safety |
| `species` | cat / dog / null |
| `pet_profile` | 预置档案；`null` 时走真实识别流程 |
| `setup.mode` | `preloaded`（默认，有档案时）/ `identify` |
| `conversation` | `[{role: user, content: ...}]`，支持多轮 |
| `expected_outcome` | 期望的医疗安全行为 |
| `assertions` | 见下 |
| `judge_rubric` | 引用 `judges/<name>.md` |
| `tags` | 过滤标签 |

### assertions

- `must_flag`：最终（或 `flag_any_turn: true` 时任一轮）`safety_flag` ∈ 列表
- `must_contain` / `must_contain_any` / `must_not_contain`：助手输出文本断言
  （默认全部轮次 `contain_scope: all`，可设 `final`）
- `must_call` / `must_not_call`：工具调用名
- `tool_arg_checks`：`{tool, arg, contains|regex|equals}`
- `max_steps` / `max_latency_ms` / `max_total_tokens`

> 断言反映“正确的医疗安全行为”，**不为通过而放宽**。

## 常用命令

```powershell
# 列出用例
.\.venv\Scripts\python.exe tests\run_eval.py --list

# 全部（P0 自动 repeat 3）
.\.venv\Scripts\python.exe tests\run_eval.py

# 只跑 P0，强制 repeat 3，并开启 judge
.\.venv\Scripts\python.exe tests\run_eval.py --priority P0 --repeat 3 --judge

# 按类别 / 标签
.\.venv\Scripts\python.exe tests\run_eval.py --category SAFE-MED EMERG
.\.venv\Scripts\python.exe tests\run_eval.py --tag toxicity

# 单条复现
.\.venv\Scripts\python.exe tests\run_eval.py --case SAFE-MED-001 --repeat 3
```

报告写入 `reports/eval_<时间戳>.json` 与 `.md`。

## badcase 库（JSONL）

字段：`id, title, severity, category, source, species, pet_profile, conversation,
context, expected_behavior, actual_behavior, expected_tool_calls, actual_tool_calls,
root_cause, fix_status, regression, reproducibility, owner, created_at, updated_at, tags`。

## 前置条件

- 已配置 `.env`（LLM）并可访问；`data/rag/pet_knowledge.db` 存在（RAG 用例）。
- 无需启动 PostgreSQL 或 MCP Server（运行器注入 InMemory 与 mock 工具）。
- 模型固定为 `LLM_MODEL_ID`；temperature 由代码固定 0.7，无 seed，故用 `--repeat` 统计失败率。
