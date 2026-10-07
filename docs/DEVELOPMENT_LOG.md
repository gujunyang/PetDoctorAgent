# 开发记录 · 宠医通

> 本文档记录项目的功能演进、关键设计决策与踩坑，方便日后查询与修改。
> 大改动请在此追加条目，并注明对应 commit。
>
> 记录日期：2026-10-04 ｜ 最近更新：2026-10-08 ｜ 仓库：https://github.com/gujunyang/PetDoctorAgent

---

## 一、项目定位

基于 **LangGraph** 的宠物店问诊多 Agent 系统：Supervisor 调度 4 个专家 Agent
（问诊 / 产品推荐 / 安全审查 / 病历查询），结合 RAG 知识库、PostgreSQL 短期+长期记忆、
MCP 业务工具，覆盖症状咨询、用药推荐、病历查询与安全兜底。
门店预约与库存查询已下线，命中相关请求统一回复「暂不支持」（`petdoctor/unsupported.py`）。

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
| `939bc8b`~`2883807` | Prompt 工程优化：路由 few-shot、职责边界、抽取字段约束、`verify_prompts.py` 回归（3 次提交） |
| `ad01b9a` | 新增 badcase 回归评估体系（`tests/`）：59 用例 / 14 类 / badcase 库 / LLM-judge / mock MCP |
| `ad01b9a` | 下线门店预约与库存查询：删除 appointment agent 与 MCP 预约/库存工具，新增「暂不支持」兜底 |
| *（本次）* | 项目更名 **宠医通**（slogan：一只 AI，看护所有毛孩子），同步全部文档标题与入口提示 |

---

## 三、当前目录结构

```
宠医通/
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
│   │   ├── safe_check.py      # 安全审查（规则 + LLM）
│   │   └── record.py          # 病历查询（确定性节点）
│   ├── unsupported.py         # 已下线功能（预约/库存）统一「暂不支持」兜底
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

- 图流程：`START → load_memory → identify_pet → supervisor → {ask_symptom | recommend_product | record | safe_check} → ...`
- Supervisor 使用 `create_agent(..., response_format=SupervisorDecision)` 输出路由决策；
  **仅在新用户回合调用 LLM**，Worker 返回后按状态确定性推进，避免重复派发。
- `ask_symptom`/`recommend_product` 均采用**两阶段**：工具 Agent 产出文本 → `json_mode` 抽取结构化结果。
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
  - `get_pet_medical_record(pet_id)`（预约/库存工具已下线）
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
- [x] 自动化测试：已新增 `tests/`（61 回归用例 + badcase 库 + `run_eval.py` + LLM-judge），
  并输出 `reports/EVALUATION_REPORT.md`；`scripts/verify_prompts.py` 仍作为轻量提示词回归保留。
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
939bc8b refactor(prompts): tighten agent prompts with clear boundaries and few-shot examples
227e6b7 test(prompts): add static and live route regression script
2883807 docs: document prompt engineering and verification workflow
ad01b9a feat(eval): add regression and judge evaluation harness
```

---

## 十、Badcase 回归评估体系（新增，未改业务代码）

### 目标与架构

为多 Agent 系统建立可复现、可回归的 badcase 评估：**规则断言 + LLM-as-judge + 人工** 三层，
覆盖 14 类场景。运行器通过回调采集轨迹、注入 mock MCP 工具、使用 InMemory 记忆，保证离线可跑、相互隔离。

```
tests/
├── run_eval.py            # CLI：过滤 / repeat / judge / 报告
├── runner/                # harness / mock_tools / trace / assertions / judge / report / loader
├── regression/*.yaml      # 59 条回归用例
├── badcases/badcases.jsonl# 17 条 badcase
└── judges/*.md            # 9 份评分标准
reports/EVALUATION_REPORT.md + eval_latest.json
patches/SUGGESTED_FIXES.md # 修复建议（未应用）
```

### 用例设计要点

- 字段：`case_id / priority / category / scenario_type / species / pet_profile / conversation /
  expected_outcome / assertions(must_flag / must_contain(_any) / must_not_contain / must_call /
  tool_arg_checks / max_steps / max_latency_ms) / judge_rubric / tags`。
- 断言反映“正确的医疗安全行为”，P0 安全断言宁严勿宽。
- `temperature=0.7` 无 seed，故 P0 用例默认 `--repeat 3`，以失败率判定。
- 测试与业务解耦：`build_graph(checkpointer=InMemorySaver(), store=InMemoryStore())`，
  monkeypatch 掉 MCP 连接，图构建后 `configure_agent/configure_tools` 注入 mock 工具。

### 关键决策与踩坑

| 问题 | 现象 | 解决 |
|---|---|---|
| Postgres 未启动时图初始化阻塞 | `get_checkpointer` 挂起 >60s | 运行器显式注入 InMemorySaver/Store（BC-006 记录为业务侧待修） |
| MCP 未启动拖慢构图 | `load_mcp_tools` 约 4.6s 超时 | harness 层 monkeypatch + 注入 mock 工具 |
| 中文否定语境误报 | 安全拒绝回答含“不建议吃”却命中 `must_not_contain:"可以吃"` | 断言增加否定/举例语境感知 |
| 效率阈值误伤安全用例 | 单轮正常 16 步，初版 `max_steps:14` | 校准为单轮 ≤24 / 2 轮 ≤36（仅效率断言） |
| 安全同义词覆盖不足 | “切勿/请勿/咨询兽医”未命中关键词 | 补充合法安全同义词后复跑通过 |

### 最近一轮结果（功能下线前，`deepseek-flash`, git `2883807`）

- 61 用例 / 103 运行：总通过率 **96.1%**，**P0 95.2%（60/63）**，通过/失败/flaky = 59/2/0。
- 平均 11.4 步 / 3312 token；延迟 P50 2229ms / P95 10819ms。
- **Agent 真实缺陷 2 个**：
  - `IDENT-001`（P0）：首句急诊未报宠物名时被 `identify_pet` 门控拦截，`safe_check` 未执行 → 补丁 D1。
  - `TOOL-003`（P1，后因功能下线关闭）：用户显式确认后仍反复确认、漏调 `create_appointment`。
- **测试用例问题 4 类**（已修）：否定语境误报、`max_steps` 过紧、安全同义词不足、确认语义歧义。

### 待办（Agent 侧修复，见 patches）

1. `identify_pet` / 条件边：无宠物时的紧急情况兜底（P0）。
2. 扩充 `EMERGENCY_KEYWORDS`（毒物名 + 尿闭/难产/中暑等，P0）。
3. ~~预约「显式确认即创建」规则~~（预约功能已下线，不再适用）。
4. 全局免责/就医提示兜底、Postgres 连接超时快速回退（P1）。
5. 摘要保留关键字段、跨物种用药硬校验、seed 支持、补充 `data/manual/` 语料（P2）。

---

## 十一、功能下线：门店预约与库存查询

### 变更内容

- 删除 `petdoctor/agents/appointment.py`（预约 Worker）。
- 从 MCP Server 删除 `check_appointment_slots` / `create_appointment` / `check_product_stock` 工具及演示数据，仅保留 `get_pet_medical_record`。
- `graph.py` 移除 appointment 节点、条件边与工具映射；`state.py` 的 `SupervisorDecision.next_agent` 移除 `appointment_agent`；
  `supervisor` 提示词移除预约路由与示例；`recommend_product` 移除库存工具依赖。
- 新增 `petdoctor/unsupported.py`：命中「预约 / 挂号 / 时段 / 库存 / 有货 / 现货 / stock / appointment」等关键词时，
  由 `identify_pet` 与 `supervisor` **确定性**回复 `UNSUPPORTED_REPLY`（暂不支持），不进入任何 Worker、不调用任何工具；
  且即使用户首句未提供宠物名，也直接回复暂不支持，而不是先追问宠物。
- `scripts/verify_prompts.py` 移除预约提示词静态校验，路由用例「预约 → FINISH」。

### 影响与验证

- 回归用例删除 TOOL-001/002/003、EFFI-002、NORMAL-004、MULTI-004；新增 `UNSUP-001~004`（预约/库存拒绝）。
- 测试运行器中 `mock_tools.py`、`harness.py` 同步移除预约/库存 mock。
- 其余核心功能（问诊 / 产品推荐 / 病历查询 / 安全审查 / 记忆 / 上下文）不受影响。
- 文档同步：README、USER_GUIDE、PRD（F3/F4、6.4、权限矩阵、用例）、`data/manual/README`。

---

## 十二、本轮对话变更全记录（2026-10-08）

> 本文档第十、十一节为本轮工作的分项说明；本节为**本轮全部改动的完整清单**，
> 覆盖：项目探索 → 测试方案 → 测试资产 → 评估运行器 → 执行评估 → 功能下线 → 文档同步。
> 模型 `deepseek-flash`，基线 git `2883807`。

### 12.1 工作阶段

1. **探索与理解**：产出《项目理解摘要》（架构/调用方式/可测试点/风险）。
2. **测试方案设计**：13 类 badcase 分类（后新增 UNSUP，共 14 类）、P0–P3 优先级、规则+judge+人工三层评估、指标定义。
3. **测试资产构建**：回归用例、badcase 库、judge rubric。
4. **评估运行器**：`tests/run_eval.py` + `tests/runner/*`，支持过滤/repeat/轨迹/mock/judge/报告。
5. **执行评估**：三轮运行（初始 83.5% → 用例校准 95.1% → 同义词修复 96.1%）。
6. **功能下线**：移除门店预约与库存查询，新增「暂不支持」兜底，复跑 97.0%。
7. **文档同步**：README / USER_GUIDE / PRD / data/manual / patches / reports。

### 12.2 新增文件

- `petdoctor/unsupported.py`：预约/库存请求的确定性「暂不支持」兜底（被 `identity.py`、`supervisor.py` 引用）。
- `tests/`（测试资产，不进入运行时）：
  - `tests/run_eval.py`：评估运行器 CLI。
  - `tests/runner/`：`harness.py`（构图/隔离/轨迹）、`mock_tools.py`（病历 mock）、`trace.py`（工具/步数/token/延迟回调）、
    `assertions.py`（规则断言，含否定语境感知）、`judge.py`（LLM-as-judge）、`report.py`（JSON+Markdown）、`loader.py`。
  - `tests/regression/*.yaml`：回归用例（14 类）。
  - `tests/badcases/badcases.jsonl`：badcase 库（17 条）。
  - `tests/judges/*.md`：9 份 judge 评分标准。
  - `tests/README.md`：测试套件说明。
- `reports/EVALUATION_REPORT.md`：评估总报告；`reports/eval_latest.json`：最近一次原始数据。
- `patches/SUGGESTED_FIXES.md`：业务侧修复建议（未应用）。

### 12.3 修改文件（逐项）

| 文件 | 本轮修改 |
|---|---|
| `petdoctor/identity.py` | 接入「暂不支持」兜底（首句预约/库存直接拒绝）；此前未改动 |
| `petdoctor/agents/supervisor.py` | 接入「暂不支持」兜底；路由 prompt 移除预约 Agent/示例 |
| `petdoctor/agents/recommend_product.py` | 移除库存工具依赖与话术 |
| `petdoctor/graph.py` | 删除 appointment 节点/条件边/工具映射；更新模块 docstring |
| `petdoctor/state.py` | `SupervisorDecision.next_agent` 移除 `appointment_agent` |
| `petdoctor/mcp_server/server.py` | 仅保留 `get_pet_medical_record`，删除预约/库存工具与演示数据 |
| `scripts/verify_prompts.py` | 移除预约 prompt 校验；路由用例「预约 → FINISH」 |
| `tests/runner/mock_tools.py` / `harness.py` / `trace.py` | 同步移除预约/库存 mock 与节点 |
| `README.md` | 5→4 Worker；删除预约/库存描述与工具表；新增「测试与评估」章节 |
| `docs/USER_GUIDE.md` | 功能表/示例/FAQ 更新为「预约与库存暂未开放」 |
| `data/manual/README.md` | 标注无需补充预约/库存数据 |
| `PRD文档/…PRD-V2.md` | F3（去库存）、F4（已下线）、6.1/6.4、权限矩阵、UC-3/UC-6、附录 A |
| `docs/DEVELOPMENT_LOG.md` | 新增本记录（第十、十一、十二节）及版本表条目 |
| `reports/EVALUATION_REPORT.md` | 合并三轮结果与功能下线变更，精简报告文件 |

### 12.4 删除文件

- `petdoctor/agents/appointment.py`（预约 Worker，整文件删除）。
- 回归用例：`TOOL-001`、`TOOL-002`、`TOOL-003`、`EFFI-002`、`NORMAL-004`、`MULTI-004`。
- 报告中转文件（合并后删除）：smoke/full/final 等中间 `eval_*.md/json`。

### 12.5 功能变更

- **测试体系**（新增）：可复现 badcase 回归，规则断言 + LLM-judge + 人工；mock MCP + InMemory 记忆隔离；
  P0 默认 `--repeat 3`，以失败率判定。
- **测试用例校准**（修正测量误报，非放宽安全）：否定语境感知、`max_steps` 阈值、安全同义词。
- **功能下线**（产品变更）：门店预约与库存查询移除；命中关键词统一回复「暂不支持」。
- **业务逻辑未变**：问诊 / 产品推荐 / 病历查询 / 安全审查 / 记忆 / 上下文 均保持原实现。

### 12.6 测试与评估结果

| 轮次 | 用例 | 总通过率 | P0 | P0 失败用例 | Flaky |
|---|---|---|---|---|---|
| run1 初始套件 | 61 | 83.5% | 82.5% | 5 | 3 |
| run2 用例校准 | 61 | 95.1% | 93.7% | 2 | 1 |
| run3 同义词修复 | 61 | 96.1% | 95.2% | 1 | 0 |
| **run4 功能下线后** | **59** | **97.0%** | **95.2%** | **1** | **0** |

- 唯一未解决 Agent 缺陷：`IDENT-001`（P0，首句急诊被宠物识别门拦截）。
- 新增 `UNSUP-001~004` 本轮全部通过。
- 成本/延迟（run4）：平均 11.0 步 / 3062 token；P50 2336ms / P95 11141ms。

### 12.7 未决事项

- `IDENT-001` 急诊门控兜底（P0，见 `patches/SUGGESTED_FIXES.md` D1）。
- 紧急关键词扩充（毒物名、尿闭、难产、中暑等）。
- 全局免责兜底、Postgres 连接超时、跨物种用药硬校验、评测 seed 支持、补充 `data/manual/` 语料。
- 测试报告运行器默认会按时间戳新增文件；如需保持精简，建议后续统一输出到 `reports/EVALUATION_REPORT.md` + `eval_latest.json`。

---

## 十三、项目更名：宠医通（2026-10-08）

### 变更内容

- 项目品牌名由 `PetDoctorAgent` 更改为 **宠医通**，slogan：**一只 AI，看护所有毛孩子。**
- 同步更新全部文档标题、入口提示与项目代号：
  - 文档：`README.md`、`docs/USER_GUIDE.md`、`docs/DEVELOPMENT_LOG.md`、`PRD文档/…PRD-V2.md`、
    `tests/README.md`、`reports/EVALUATION_REPORT.md`。
  - 代码展示名：`main.py`（docstring / CLI 描述 / 启动提示）、`petdoctor/__init__.py`、`tests/__init__.py`、`tests/run_eval.py`。
  - 目录树示例：`PetDoctorAgent/` → `宠医通/`。
- PRD 项目代号字段与文档名称由 `PetDoctorAgent` 更新为 `宠医通`。

### 未改动（有意保留）

- Python 包名 `petdoctor/`、模块与导入路径不变（避免破坏引用）。
- GitHub 仓库地址 `https://github.com/gujunyang/PetDoctorAgent` 不变（远程路径）。
- 历史提交信息中的旧名称不变（保持历史真实）。

