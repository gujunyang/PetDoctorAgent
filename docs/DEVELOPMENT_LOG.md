# 开发记录 · PetDoctorAgent

> 本文档记录项目的功能演进、关键设计决策与踩坑，方便日后查询与修改。
> 大改动请在此追加条目，并注明对应 commit。
>
> 记录日期：2026-10-04 ｜ 仓库：https://github.com/gujunyang/PetDoctorAgent

---

## 一、项目定位

基于 **LangGraph** 的宠物店问诊多 Agent 系统：Supervisor 调度 5 个专家 Agent
（问诊 / 产品推荐 / 预约 / 安全审查 / 病历查询），结合 RAG 知识库、PostgreSQL 短期+长期记忆、
MCP 业务工具，覆盖症状咨询、用药推荐、门店预约、病历查询与安全兜底。

- 使用者文档：`docs/USER_GUIDE.md`
- 开发者文档：`README.md`

---

## 二、版本演进概览

| 提交 | 内容 |
|------|------|
| `dbdf4ae` | 初始提交：多 Agent（Supervisor+Workers）+ RAGMill + Postgres 记忆 |
| `449d9b1` | RAG 工具与长期记忆接入各 Agent；扩展安全审查关键词 |
| `1d1c188` | Stage 1：宠物店 MCP Server（streamable-http）+ MCP Client + 启动脚本 |
| `c6d8d7e` | Stage 2：MCP 工具桥接进 Agent + 新增 appointment worker + graph 接线 |
| `7664f31` | Stage 3：`.env.example` 增加 `PET_STORE_MCP_URL` |
| `d588e5b` | 上下文工程：摘要压缩 / Top-3 RAG / 提示词隔离 / 执行轨迹日志 |
| `a05795b` | 新增项目 README |
| `9555211`~`9de54a3` | 工程化重构：迁移为 `petdoctor/` 包（4 次提交） |
| `5a06e47`~`b1378b6` | 宠物识别 + 每宠物历史 + 病历查询（7 次提交） |
| `27e1e1f`,`99e6b53` | 新增使用者手册并从 README 链接 |
| *（未提交）* | Prompt 工程优化：路由 few-shot、职责边界、抽取字段约束、回归脚本 |

---

## 三、当前目录结构

```
PetDoctorAgent/
├── petdoctor/                 # 应用包
│   ├── config.py              # 环境变量 / LLM / RAG 路径
│   ├── state.py               # PetClinicState + 结构化输出模型
│   ├── memory.py              # PostgresSaver + PostgresStore（含内存回退）
│   ├── identity.py            # 宠物识别节点（每会话确认是哪只宠物）
│   ├── graph.py               # 图组装 + 执行轨迹日志 TraceLogger
│   ├── agents/
│   │   ├── supervisor.py      # 分诊调度 + 对话摘要压缩
│   │   ├── ask_symptom.py     # 问诊（RAG Top-3 + 病历工具）
│   │   ├── recommend_product.py
│   │   ├── appointment.py     # 预约（MCP）
│   │   ├── safe_check.py      # 安全审查（规则 + LLM）
│   │   └── record.py          # 病历查询（确定性节点）
│   ├── tools/
│   │   ├── rag.py             # pet_knowledge_search（RAGMill + 翻译）
│   │   └── mcp_client.py      # MCP 客户端（异步工具 → 同步桥接）
│   └── mcp_server/server.py   # 宠物店 MCP Server（streamable-http :8000）
├── main.py                    # CLI 入口
├── scripts/                   # download_data / build_rag / verify_prompts / start_mcp.* / pg.ps1
├── data/{raw,manual,rag}/     # 语料 / 人工资料 / 向量库（raw 与 rag 不入库）
├── docs/                      # USER_GUIDE.md / DEVELOPMENT_LOG.md
├── requirements.txt  .env.example  README.md
```

---

## 四、功能模块记录

### 1. 多 Agent 架构（Supervisor + Workers）

- 图流程：`START → load_memory → identify_pet → supervisor → {ask_symptom | recommend_product | appointment | record | safe_check} → ...`
- Supervisor 使用 `create_agent(..., response_format=SupervisorDecision)` 输出路由决策；
  **仅在新用户回合调用 LLM**，Worker 返回后按状态确定性推进，避免重复派发。
- `ask_symptom`/`recommend_product`/`appointment` 均采用**两阶段**：工具 Agent 产出文本 → `json_mode` 抽取结构化结果。
- 相关文件：`petdoctor/agents/*.py`、`petdoctor/graph.py`、`petdoctor/state.py`。

### 2. RAG 知识库管道

- `scripts/download_data.py`：下载 HuggingFace 数据集（karenwky/pet-health-symptoms、SparkleDark/Everything_about_dogs），每条记录存 `data/raw/{condition}_{index}.txt`。
- `scripts/build_rag.py`：RAGMill `ingest → chunk → embed → store`，产出 SQLite 向量库 `data/rag/pet_knowledge.db`。
- `petdoctor/tools/rag.py`：`pet_knowledge_search` 工具，惰性加载向量库与 embedding 模型。
- 数据规模：约 12,590 条 → 12,590 chunks（dim 384）。

### 3. 多语言检索 + 查询时翻译

- embedding 模型改用 `Xenova/paraphrase-multilingual-MiniLM-L12-v2`（384 维，50+ 语言）。
- 检索结果默认用 LLM 翻译为中文（`PET_RAG_TRANSLATE=0` 可关），翻译走 `json_mode`。
- 换模型后必须 `build_rag.py --rebuild`。

### 4. 记忆系统（短期 + 长期）

- `petdoctor/memory.py`：
  - 短期：`PostgresSaver`（按 `thread_id`）。
  - 长期：`PostgresStore`，Namespace：
    - `("users", uid, "profile")` 用户信息
    - `("users", uid, "pets", pet_id)` 宠物档案（key=`profile`）
    - `("users", uid, "pets", pet_id, "history")` 该宠物问诊历史（按宠物隔离）
  - 未配置 `DATABASE_URL` 或连接失败 → 自动回退 `InMemorySaver`/`InMemoryStore`。
- 便携版 PostgreSQL 16.15：`C:\Users\28481\pgsql`，用 `scripts/pg.ps1 start|stop|status` 管理（非服务，重启需手动启动）。

### 5. MCP 业务工具

- `petdoctor/mcp_server/server.py`（FastMCP，`streamable-http`，`127.0.0.1:8000`，端点 `/mcp`）暴露：
  - `check_appointment_slots(date)`、`create_appointment(pet_name, service, datetime)`
  - `check_product_stock(product_name)`、`get_pet_medical_record(pet_id)`
- `petdoctor/tools/mcp_client.py`：`get_mcp_tools()`（异步）；`load_mcp_tools()` 把**异步 MCP 工具桥接为可同步调用**，使整图保持同步（`app.invoke`）。
- 工具映射与注入：`graph._configure_agents()` 按名称合并到对应 Agent。

### 6. 上下文工程

- **摘要压缩**：`messages ≥ 20` 时摘要旧消息为 `[历史对话摘要]`，仅保留最近 6 条（`supervisor.py`）。
- **Top-3 RAG 注入**：问诊节点预检索并只注入最相关 3 条。
- **提示词隔离**：各 Agent prompt 含明确「职责边界」（问诊不推荐产品、推荐不诊断、安全只审查）。
- **执行轨迹**：`graph.TraceLogger`（BaseCallbackHandler）记录节点起止与耗时；`build_graph(trace=True)` 通过 `with_config({"callbacks": [...]})` 附加。

### 7. 宠物识别（每会话确认对象）

- `petdoctor/identity.py`：`identify_pet` 节点。
  - 已识别（`active_pet_id`）→ 刷新档案 + 历史案例；
  - 否则从用户消息抽取名字/编号：命中 → 载入；名字+物种齐全 → 建档；否则暂存 `pet_draft` 并追问（本轮结束）。
  - 显式编号（如 `PET-001`）即使不在本地记忆也接受，供外部/病历查询。
- 问诊/推荐 Agent 会注入宠物档案与**历史案例**辅助推理。

### 8. 病历查询（record_agent）

- `petdoctor/agents/record.py`：确定性节点，汇总
  基本档案 + 既往问诊记录 + MCP 门诊病历，输出可读中文。
- 本地无档案但 MCP 有记录时，用 MCP 记录补全档案；MCP 查不到则省略该段。

### 9. 工程化重构

- 从根目录扁平模块迁移为 `petdoctor/` 包；入口 `agent.py` → `main.py`。
- Agent 文件去掉冗余 `_agent` 后缀（`ask_symptom.py` / `recommend_product.py` / `appointment.py` / `safe_check.py`）。
- 统一绝对导入 `from petdoctor.xxx import ...`；`git mv` 保留历史。

### 10. 提示词工程（Prompt Engineering）

- **集中管理**：各 Agent 的 system prompt 为模块顶部命名常量（`SUPERVISOR_PROMPT`、`ASK_SYMPTOM_PROMPT` 等）；
  摘要 prompt 规范化新增 `SUMMARY_PROMPT`。
- **统一原则**：角色 + 职责 → 职责边界 → Few-shot 示例 → 输出/事实性约束。
- **路由**（`supervisor.py`）：删除与代码确定性推进冲突的旧规则（"上一轮问诊→safe_check"）；
  明确「症状 vs 产品」判别、多意图优先级、`direct_response`/`FINISH` 关系；内嵌 6 条判别示例；
  紧急情况可直接路由 `safe_check_agent`。
- **抽取**（`ask_symptom._extract_assessment` / `recommend_product._extract_products` / `identity.extract_pet_info`）：
  逐字段约束、缺失留空、显式禁止臆造（尤其价格、编号）。
- **安全**（`safe_check.py`）：补充正反例与药物过量/幼老特殊场景，细化 warning 判定。
- **回归脚本**：`scripts/verify_prompts.py`——默认离线静态校验（可加载/可格式化/关键约束），
  `--live` 追加真实 LLM 路由回归（症状/产品/预约/病历/紧急/闲聊 6 用例）。

### 11. 文档

- `README.md`：开发者视角（架构、快速开始、模块、脚本、注意事项）。
- `docs/USER_GUIDE.md`：使用者手册（功能、示例对话、FAQ、免责声明）。

---

## 五、关键设计决策与踩坑

| 问题 | 现象 | 解决 |
|------|------|------|
| DeepSeek 思考模式不支持强制 `tool_choice` | `create_agent` 结构化输出报 400 | `get_llm()` 对 DeepSeek 加 `extra_body={"thinking":{"type":"disabled"}}`（`LLM_DISABLE_THINKING=0` 还原） |
| 工具 Agent + `response_format` 死循环 | 反复只调 RAG 工具、从不调结构化工具 | 改为**两阶段**：工具 Agent 产出文本，再 `json_mode` 抽取 |
| 自定义 `state_schema` 导致结构化输出丢通道 | 带 `response_format` 的 Agent 死循环 | 子 Agent 不传 `state_schema`；Supervisor 因 `dynamic_prompt` 中间件自带 `AgentState` 而保留 |
| Supervisor 重复派发同一 Worker | `supervisor→ask→supervisor→ask` 循环 | Supervisor 仅新回合调 LLM，Worker 返回后确定性推进 |
| MCP 工具是异步专属 | 同步调用 `NotImplementedError` | `mcp_client` 用 `StructuredTool(func=asyncio.run(...), coroutine=...)` 桥接 |
| 消息替换 | 直接返回新列表会被 `add_messages` 追加 | 用 `RemoveMessage(id=REMOVE_ALL_MESSAGES)` 清空再写入 |
| RAG 路径错位 | 迁移到包后 `data/` 指到 `petdoctor/data` | `config.PROJECT_ROOT = Path(__file__).resolve().parents[1]` |
| Postgres 安装无管理员权限 | 无法注册系统服务 | 用 EDB 便携二进制 `initdb` + `pg_ctl`（用户态） |
| Windows PowerShell 5.1 解析非 ASCII 脚本报错 | `scripts/pg.ps1` 解析失败 | 脚本改为纯 ASCII 输出 |
| 后台启动 MCP/Postgres 导致命令挂起 | 进程持有 stdout 句柄 | 用 `Start-Process -WindowStyle Hidden` 分离，日志重定向到文件 |
| Prompt 规则与代码逻辑冲突 | 旧 Supervisor prompt 要求"上一轮问诊→safe_check"，但代码已确定性推进，导致重复/矛盾 | 删除该规则；路由 prompt 只针对新用户回合，并补判别示例 |
| 抽取阶段字段幻觉 | 价格/编号等未提及信息被编造进结构化结果 | 抽取 prompt 逐字段约束 + "缺失留空/不得臆造" + few-shot |
| 判别类任务不稳定 | 症状 vs 产品、safe/warning 边界模糊 | 在路由与安全 prompt 内嵌正反例；新增 `verify_prompts.py --live` 回归 |

---

## 六、环境与运行速查

```powershell
# 依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 记忆数据库（可选）
powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 start

# MCP Server（可选）
powershell -ExecutionPolicy Bypass -File scripts\start_mcp.ps1     # 或 bash scripts/start_mcp.sh

# 知识库（可选，首次）
.\.venv\Scripts\python.exe scripts\download_data.py
.\.venv\Scripts\python.exe scripts\build_rag.py --rebuild

# 运行
.\.venv\Scripts\python.exe main.py --user alice
```

关键环境变量（详见 `.env.example`）：`LLM_MODEL_ID`/`LLM_API_KEY`/`LLM_BASE_URL`、
`DATABASE_URL`、`PET_STORE_MCP_URL`、`RAGMILL_EMBEDDING_MODEL`、`PET_RAG_TRANSLATE`、`PET_USER_ID`。

---

## 七、已知限制 / 待办（TODO）

- [ ] 会话内**不支持中途切换宠物**（识别后固定为 `active_pet_id`）。
- [ ] 新宠物建档条件为「名字 + 物种」，其余字段（品种/年龄/体重）非必填。
- [ ] MCP `get_pet_medical_record` 为演示数据（PET-001/PET-002）；用户自建宠物编号与 MCP 不通用。
- [ ] 病历工具/库存工具依赖 MCP Server 运行；未启动则自动降级。
- [ ] `pet_history` 目前取最近 5 条；如需全量/分页可扩展。
- [ ] 无自动化测试（仅手工/脚本验证）；已有 `scripts/verify_prompts.py` 做提示词回归，建议补 `tests/`。
- [ ] 未提供宠物档案的“修改”入口（只能新建）。

---

## 八、安装资产清单与卸载/恢复

> 全部为**用户态 / 项目内**安装：未注册 Windows 服务、未改动系统注册表。
> **卸载 = 停止进程 + 删除对应目录**，项目源码保留即恢复原样。

### 资产位置与体积（本机实测）

| 资产 | 路径 | 体积 | 说明 |
|------|------|------|------|
| Python 虚拟环境 | `<项目>\.venv` | ~671 MB | 所有 pip 依赖 |
| PostgreSQL 程序 | `C:\Users\28481\pgsql\pgsql` | 与下项合计 ~899 MB | EDB 便携二进制 16.15 |
| PostgreSQL 数据 | `C:\Users\28481\pgsql\data` | — | 数据库文件（含 `petclinic`） |
| PostgreSQL 日志 | `C:\Users\28481\pgsql\server.log` | — | 启动/运行日志 |
| RAG 向量库 | `<项目>\data\rag\pet_knowledge.db` | ~26 MB | RAGMill SQLite |
| 原始语料 | `<项目>\data\raw\` | 12590 个 `.txt` | 可由脚本重建 |
| RAGMill 模型缓存 | `C:\Users\28481\.cache\ragmill` | ~152 MB | 本地 ONNX embedding 模型 |
| HuggingFace 缓存 | `C:\Users\28481\.cache\huggingface` | ~2 MB | `datasets` 下载缓存 |
| 密钥/配置 | `<项目>\.env` | — | 内含 LLM/DB 等密钥，**勿提交** |

> PostgreSQL 目录由 `scripts/pg.ps1` 的 `$env:USERPROFILE\pgsql` 决定，可用环境变量 `PET_PG_ROOT` 覆盖。

### 连接信息

- 主机/端口：`127.0.0.1:5432`
- 超级用户：`postgres` ／ 密码：`petpass`
- 数据库：`petclinic`
- 连接串：`DATABASE_URL=postgresql://postgres:petpass@localhost:5432/petclinic`
- 表由首次运行自动创建：`checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations / store / store_migrations`
- 未配置 `DATABASE_URL` 时记忆自动回退进程内，不产生任何磁盘残留。

### 后台进程

| 进程 | 端口 | 启动方式 |
|------|------|----------|
| PostgreSQL | 5432 | `scripts/pg.ps1 start` |
| MCP Server | 8000 | `scripts/start_mcp.ps1`（或 `bash scripts/start_mcp.sh`） |

两者均非系统服务，**重启后不会自动启动**；验证系统无残留服务：`Get-Service postgresql*` 应为空。

### 卸载 / 恢复原样

```powershell
# 1) 停止后台进程
powershell -ExecutionPolicy Bypass -File scripts\pg.ps1 stop
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

# 2) 删除 PostgreSQL（含数据）
Remove-Item -Recurse -Force "$env:USERPROFILE\pgsql"

# 3) 删除 Python 虚拟环境
Remove-Item -Recurse -Force ".venv"

# 4) 删除可重建的生成数据与模型缓存（可选）
Remove-Item -Recurse -Force "data\raw", "data\rag"
Remove-Item -Recurse -Force "$env:USERPROFILE\.cache\ragmill"

# 5) 删除 HuggingFace 缓存（可选）
Remove-Item -Recurse -Force "$env:USERPROFILE\.cache\huggingface"
```

- 如需重新安装：见「六、环境与运行速查」。
- 仅清理数据、保留环境：删 `data\raw`、`data\rag`，并 `scripts\pg.ps1 start` 后在库中删除 `petclinic` 或相关表即可。

---

## 九、提交清单（按时间）

```
dbdf4ae Initial commit: multi-agent pet clinic system
449d9b1 Wire RAG tool and long-term memory into agents; expand safe-check rules
1d1c188 Stage 1: pet store MCP server + MCP client + start scripts
c6d8d7e Stage 2: bridge MCP tools into agents; wire appointment worker
7664f31 Stage 3: document PET_STORE_MCP_URL in .env.example
d588e5b Context engineering: summarization, top-3 RAG, prompt isolation, trace logging
a05795b docs: add project README
9555211 refactor: move core modules into petdoctor package
4af026d refactor: move and rename agent modules into petdoctor.agents
97b0467 refactor: move tools and mcp_server into petdoctor package
9de54a3 refactor: move entrypoint to main.py; update graph wiring, scripts and docs
5a06e47 feat(memory): per-pet profiles, name lookup, per-pet diagnosis history
26695d2 feat(state): add active_pet_id/pet_draft/pet_history and record_agent route
8feef83 feat(identity): identify pet per session and load profile/past cases
2b68020 feat(agents): inject pet identity and history into symptom/product agents
ea2de66 feat(agents): add record_agent for pet medical record query
86d8e43 feat(graph): wire identify_pet and record_agent nodes
b1378b6 feat(supervisor,docs): route record intent; document pet identity and record query
27e1e1f docs: add end-user guide (features and usage)
99e6b53 docs: link user guide from README
4866036 docs: add development log (features, decisions, install assets, uninstall guide)
```
