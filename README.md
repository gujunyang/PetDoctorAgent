# PetDoctorAgent · 宠物问诊多 Agent 系统

基于 **LangGraph** 的宠物店问诊助手：由 Supervisor 调度 5 个专家 Agent（问诊 / 产品推荐 / 预约 / 安全审查 / 病历查询），
结合 **RAG 知识库**、**PostgreSQL 短期+长期记忆** 与 **MCP 业务工具**，覆盖宠物症状咨询、用药推荐、门店预约与病历查询场景。

> ⚠️ 本项目仅用于技术演示，不能替代执业兽医诊断；涉及处方药或紧急情况请及时就医。

> 📖 **使用者请阅读 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)（使用手册）；本文档面向开发者。**

---

## 功能特性

- **Supervisor + 5 Worker**：多 Agent 协作，按用户意图动态路由（问诊 / 产品推荐 / 预约 / 安全审查 / 病历查询）。
- **宠物识别**：每段会话先确认「这次是哪只宠物」，核对档案并载入其历史案例。
- **RAG 检索增强**：RAGMill 本地向量库（SQLite）+ 多语言 embedding + 查询时中文翻译。
- **记忆系统**：PostgresSaver（会话短期记忆，按 `thread_id`）+ PostgresStore（跨会话长期记忆）。
- **MCP 业务工具**：独立的宠物店 MCP Server（streamable-http）提供预约、库存、病历工具。
- **上下文工程**：对话摘要压缩、Top-3 RAG 注入、Agent 职责隔离、执行轨迹日志。
- **提示词工程**：角色 + 职责边界、判别类 Few-shot 示例、结构化抽取字段约束，配套回归脚本验证。
- **安全兜底**：紧急关键词规则匹配 + LLM 风险分级（safe / warning / emergency）。

---

## 系统架构

```
              ┌──────────────────────────────────────────────┐
 START ──────▶│ load_memory  (PostgresStore: 用户信息)          │
              └───────────────────────┬──────────────────────┘
                                      ▼
                            ┌───────────────────┐
                            │   identify_pet    │  确认"这次是哪只宠物"：查库/建档、
                            │ 宠物识别+记忆核对   │  载入档案与以往案例；未识别则追问并结束本轮
                            └─────────┬─────────┘
                                      ▼
                            ┌───────────────────┐
                            │    supervisor     │  ① 上下文摘要压缩(≥20 条)
                            │  分诊调度 / 路由    │  ② 结构化输出 next_agent
                            └─────────┬─────────┘
                   条件边 next_agent   │
   ┌───────────┬───────────┬───────────┬───────────┬───────────┬───────┐
   ▼           ▼           ▼           ▼           ▼           ▼
ask_symptom  recommend  appointment  record    safe_check    END
 (问诊)      (产品推荐)   (预约)     (病历查询)  (安全审查)  (闲聊/结束)
  RAG+病历    RAG+库存    预约工具    档案+MCP    规则+LLM
   │           │           │           │
   └───────────┴───────────┴───────────┘
              返回 supervisor (循环)        safe_check / record → END
                                            (safe/warning 写入历史)
```

- **State**：`petdoctor/state.py:PetClinicState`（`messages / pet_profile / symptoms / diagnosis /
  product_recommendations / safety_flag / next_agent / rag_context / session_summary /
  active_pet_id / pet_draft / pet_history`）。
- **宠物识别**：每段会话先经 `identify_pet` 确认「这次是哪只宠物」——按名字（或编号）核对
  长期记忆；命中则载入档案+以往案例，未命中则引导补全信息后建档。
- **路由**：Supervisor 仅在新用户回合调用 LLM 判断意图；Worker 返回后按状态确定性推进，避免重复派发。
- **病历查询**：`record_agent` 汇总当前宠物的基本档案、既往问诊记录与 MCP 门诊病历
  （用户说“查看XX的病历”即可；给出显式编号如 `PET-001` 时会查询 MCP 病历）。
- **MCP 工具映射**：
  - `ask_symptom_agent` ← `get_pet_medical_record`
  - `recommend_product_agent` ← `check_product_stock`
  - `appointment_agent` ← `check_appointment_slots` / `create_appointment`
  - `record_agent` ← `get_pet_medical_record`

---

## 目录结构

```
PetDoctorAgent/
├── petdoctor/                    # 应用包
│   ├── __init__.py
│   ├── config.py                 # 环境变量 / LLM / RAG 路径配置
│   ├── state.py                  # PetClinicState + 结构化输出模型
│   ├── memory.py                 # 记忆系统（PostgresSaver + PostgresStore，含内存回退）
│   ├── identity.py               # 宠物识别节点（每会话确认是哪只宠物）
│   ├── graph.py                  # 图组装 + 执行轨迹日志(TraceLogger)
│   ├── agents/                   # Supervisor + Worker Agents
│   │   ├── supervisor.py         # 分诊调度 + 对话摘要压缩
│   │   ├── ask_symptom.py        # 问诊（RAG Top-3 注入 + 病历工具）
│   │   ├── recommend_product.py  # 产品推荐（RAG + 库存工具）
│   │   ├── appointment.py        # 预约（MCP 工具）
│   │   ├── safe_check.py         # 安全审查（规则 + LLM）
│   │   └── record.py             # 病历查询（汇总档案/历史/门诊病历）
│   ├── tools/
│   │   ├── rag.py                # pet_knowledge_search（RAGMill 检索 + 中文翻译）
│   │   └── mcp_client.py         # MCP 客户端（异步工具 → 同步桥接）
│   └── mcp_server/
│       └── server.py             # 宠物店业务 MCP Server（streamable-http）
├── main.py                       # 入口（CLI）
├── scripts/
│   ├── download_data.py          # 下载 HuggingFace 数据 → data/raw/
│   ├── build_rag.py              # 构建 RAGMill 向量库 → data/rag/
│   ├── verify_prompts.py         # 提示词静态校验 + 路由回归（--live）
│   ├── start_mcp.sh / .ps1       # 启动 MCP Server
│   └── pg.ps1                    # 便携版 PostgreSQL 启停（Windows）
├── data/
│   ├── raw/                      # 原始语料（不入库）
│   ├── manual/                   # 人工补充资料
│   └── rag/                      # 向量库 pet_knowledge.db（不入库）
├── docs/                         # USER_GUIDE.md / DEVELOPMENT_LOG.md
├── requirements.txt
├── .env.example
└── README.md
```

---

## 快速开始

### 1. 环境要求

- Python 3.11+（开发环境为 3.13）
- 可选：PostgreSQL（无则记忆自动回退到进程内内存）
- 可选：`.venv` 虚拟环境

### 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. 配置环境变量

```powershell
Copy-Item .env.example .env
```

编辑 `.env` 至少填入 LLM 配置（示例，DeepSeek）：

```dotenv
LLM_MODEL_ID=deepseek-flash
LLM_API_KEY=sk-xxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
```

### 4. 初始化 PostgreSQL（可选）

不配置 `DATABASE_URL` 时会自动回退到 `InMemorySaver` / `InMemoryStore`（重启即丢）。
启用持久化记忆：

```dotenv
DATABASE_URL=postgresql://postgres:petpass@localhost:5432/petclinic
```

Windows 便携版 PostgreSQL 可用脚本管理（见 `scripts/pg.ps1`）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start
```

首次运行会自动 `setup()` 建表（checkpoints / store 等）。

### 5. 构建 RAG 知识库（可选）

```powershell
.\.venv\Scripts\python.exe scripts\download_data.py        # 下载数据 → data/raw/
.\.venv\Scripts\python.exe scripts\build_rag.py --rebuild  # 构建向量库 → data/rag/
```

未构建时，`pet_knowledge_search` 会返回「知识库暂不可用」提示，Agent 仍可运行。

### 6. 启动 MCP Server（可选）

```bash
# Linux/macOS
bash scripts/start_mcp.sh
# Windows
powershell -ExecutionPolicy Bypass -File scripts\start_mcp.ps1
```

默认监听 `127.0.0.1:8000`，端点 `http://localhost:8000/mcp`。未启动时图会自动跳过 MCP 工具。

### 7. 运行

```powershell
.\.venv\Scripts\python.exe main.py --user alice
# 复用会话：
.\.venv\Scripts\python.exe main.py --user alice --session <session-id>
```

`thread_id` 默认自动生成 uuid4（长度 < 255）。输入 `q` 退出。

---

## 配置项（`.env`）

| 变量 | 说明 | 默认 |
|------|------|------|
| `LLM_MODEL_ID` | 模型 ID | `gpt-4o-mini` |
| `LLM_API_KEY` | 模型 API Key | — |
| `LLM_BASE_URL` | OpenAI 兼容端点 | `https://api.openai.com/v1` |
| `LLM_DISABLE_THINKING` | 对 DeepSeek 关闭思考模式（其不支持强制 tool_choice） | `1` |
| `TAVILY_API_KEY` | Tavily 搜索（**当前代码未使用**，历史保留） | — |
| `DATABASE_URL` | Postgres 连接串（短期+长期记忆共用） | 空（回退内存） |
| `PET_USER_ID` | 默认用户 ID | `default` |
| `PET_STORE_MCP_URL` | MCP Server 地址 | `http://localhost:8000/mcp` |
| `RAGMILL_EMBEDDING_MODEL` | 本地 embedding 模型 | `Xenova/paraphrase-multilingual-MiniLM-L12-v2` |
| `PET_RAG_DB` | 向量库路径 | `./data/rag/pet_knowledge.db` |
| `PET_RAG_TRANSLATE` | 检索结果查询时翻译为中文 | `1` |
| `RAGMILL_CHUNK_SIZE` / `RAGMILL_OVERLAP` | 切分参数 | `500` / `50` |

---

## 核心模块

### 宠物识别与记忆核对（`petdoctor/identity.py`）

- 每段会话确认「这次是哪只宠物」，按名字（可带编号）在用户名下核对：
  - 命中 → 载入档案 + 该宠物以往问诊记录（注入问诊 Agent 辅助推理）；
  - 未命中但已给出名字 + 物种 → 自动建档（`PET-xxxxxx`）；
  - 信息不足 → 暂存草稿（`pet_draft`）并追问，本轮结束等用户补充。
- 会话内识别一次后记住，后续轮次不再追问；问诊记录按宠物隔离存储（`pets/<pet_id>/history`）。

### 记忆系统（`petdoctor/memory.py`）

- Namespace 设计：
  - `("users", user_id, "profile")` 用户基本信息
  - `("users", user_id, "pets", pet_id)` 宠物档案（品种、年龄、体重、过敏史、既往病史，key=`profile`）
  - `("users", user_id, "pets", pet_id, "history")` 该宠物问诊历史（按宠物隔离）
  - `("users", user_id, "history")` 用户级历史（未识别宠物时的回退）
- `load_memory` 节点在每轮开始注入宠物档案；`safe_check_agent` 在 `safe`/`warning` 时写回历史。
- 未配置/连接失败自动回退 `InMemorySaver` / `InMemoryStore`。

### RAG（`petdoctor/tools/rag.py` + `scripts/build_rag.py`）

- RAGMill：`ingest → chunk → embed → store`，SQLite 向量库，本地 ONNX embedding（离线）。
- 多语言模型支持中英文检索；检索结果可查询时翻译为中文（`PET_RAG_TRANSLATE`）。
- 问诊节点预检索 **Top-3** 注入上下文，控制注入长度。

### MCP（`petdoctor/mcp_server/server.py` + `petdoctor/tools/mcp_client.py`）

| 工具 | 说明 |
|------|------|
| `check_appointment_slots(date)` | 查询某天可预约时段（YYYY-MM-DD） |
| `create_appointment(pet_name, service, datetime)` | 创建预约（YYYY-MM-DD HH:MM） |
| `check_product_stock(product_name)` | 查询产品库存 |
| `get_pet_medical_record(pet_id)` | 查询宠物病历 |

MCP 工具是异步专属，`mcp_client.load_mcp_tools()` 将其桥接为可同步调用的工具，整图保持同步。

### 上下文工程

- **摘要压缩**：`messages ≥ 20` 时将历史摘要为 `[历史对话摘要]`，仅保留最近 6 条（`petdoctor/agents/supervisor.py`）。
- **Top-3 RAG 注入**：问诊节点只注入最相关 3 条（`petdoctor/agents/ask_symptom.py`）。
- **职责隔离**：各 Agent 的 system prompt 含明确「职责边界」，不越界。
- **执行轨迹**：`petdoctor/graph.py:TraceLogger` 记录每个节点开始/结束时间与输出；
  `build_graph(trace=True)`（默认）通过 `with_config({"callbacks":[...]})` 附加。

### 提示词工程

各 Agent 的 system prompt 集中为模块顶部的命名常量（如 `SUPERVISOR_PROMPT`、`ASK_SYMPTOM_PROMPT`），
遵循统一原则：

- **角色 + 职责**：开头声明角色，再列「只做什么 / 不做什么」。
- **职责边界**：问诊不推荐、推荐不诊断、安全只分级，避免越界串话。
- **Few-shot 示例**：路由（Supervisor）与安全分级等判别类 prompt 内嵌正反例，降低误判。
- **输出约束**：抽取阶段（`*_extract_*`）逐字段约束、缺失留空、显式禁止臆造，配合 `state.py` 的
  Pydantic 模型保证结构稳定。
- **事实性约束**：摘要与抽取 prompt 要求「只保留真实出现的内容，不要推测」。

| 模块 | 提示词 | 作用 |
|------|--------|------|
| `supervisor.py` | `SUPERVISOR_PROMPT` | 意图路由（含判别示例；紧急情况可直接转安全审查） |
| `supervisor.py` | `SUMMARY_PROMPT` | 上下文摘要（事实性约束） |
| `ask_symptom.py` | `ASK_SYMPTOM_PROMPT` | 问诊追问，不推荐产品 |
| `recommend_product.py` | `RECOMMEND_PRODUCT_PROMPT` | 产品推荐，禁跨物种用药 |
| `appointment.py` | `APPOINTMENT_PROMPT` | 预约，强调确认与相对日期换算 |
| `safe_check.py` | `SAFE_CHECK_PROMPT` | 风险分级（含正反例） |
| `identity.py` | 内联抽取 prompt | 宠物身份信息抽取 |

回归验证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_prompts.py         # 离线静态校验
.\.venv\Scripts\python.exe scripts\verify_prompts.py --live  # 含真实 LLM 路由回归
```

---

## 脚本一览

| 脚本 | 用途 |
|------|------|
| `scripts/download_data.py [--limit N]` | 下载 HF 数据集并保存为 `data/raw/{condition}_{index}.txt` |
| `scripts/build_rag.py [--rebuild]` | 构建/重建 RAGMill 向量库 |
| `scripts/verify_prompts.py [--live]` | 提示词静态校验；`--live` 执行真实 LLM 路由回归 |
| `scripts/start_mcp.sh` / `start_mcp.ps1` | 启动 MCP Server |
| `scripts/pg.ps1 start\|stop\|status\|restart` | 便携版 PostgreSQL 管理（Windows） |

---

## 注意事项

- 运行 Agent 需要可用的 LLM API Key。
- DeepSeek 思考模式不支持强制 `tool_choice`，代码默认对 DeepSeek 关闭（`LLM_DISABLE_THINKING=0` 可还原）。
- `.env` 含密钥，已被 `.gitignore` 忽略，切勿提交。
- `data/raw/`、`data/rag/` 为可重建数据，未纳入版本控制。
- MCP 工具为异步工具，故客户端做了 sync 桥接；若整图改用异步调用需相应调整。

---

## License

仅用于学习与演示。第三方依赖与数据集请遵循各自的许可协议。
