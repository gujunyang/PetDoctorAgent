# 4.1 Supervisor：谁来分配任务

日期：2026-11-08。状态：已讲解／答疑中，未确认理解。按用户要求与 4.2、4.3 连续讲解，章末统一答疑。

## 核心结论

- Supervisor 是本项目的集中调度角色，选择当前要执行的专家，不负责代替全部专家回答。
- 具体实现分三层：supervisor_node 负责控制逻辑；内部 _SUPERVISOR_AGENT 负责需要模型判断的路由；图条件边读取 next_agent 执行转移。
- 新用户回合可调用模型；Worker 返回后按状态确定性推进；已下线请求有规则拦截。摘要步骤在分支判断之前执行，可能单独调用模型。
- 路由依据当前诉求，不是见到症状词就必定问诊。提示词要求多诉求只选最紧急或最先提到的一个，不实现完整自动任务规划。
- 模型决策字段受输出模型约束，但结构合法不保证意图判断正确。

## 术语

- Supervisor：调度角色；Worker：具体任务处理角色。
- LLM（Large Language Model）：大语言模型。
- 意图路由：判断用户当前希望完成的工作并选择处理节点。
- 动态提示词：调用时读取数据，生成本次使用的指令内容。
- 结构化输出：以约定字段返回结果，便于程序读取。

## 实现与关键函数

- [supervisor.py](../../petdoctor/agents/supervisor.py)：SUPERVISOR_PROMPT、_supervisor_prompt、_SUPERVISOR_AGENT、supervisor_node。
- [state.py](../../petdoctor/state.py)：SupervisorDecision，字段 next_agent / direct_response。
- [graph.py](../../petdoctor/graph.py)：route_from_supervisor；允许的 Worker 集合。
- [unsupported.py](../../petdoctor/unsupported.py)：unsupported_reply。
- [identity.py](../../petdoctor/identity.py)：图进入调度前的身份处理，可能先结束本轮。

构建代码要点：

```python
_SUPERVISOR_AGENT = create_agent(
    get_llm(),
    tools=[],
    middleware=[_supervisor_prompt],
    state_schema=PetClinicState,
    response_format=SupervisorDecision,
    name="supervisor",
)
```

tools=[] 表示未显式挂载业务工具，不代表框架绝不会通过工具机制实现结构化输出。middleware 中的动态提示函数将 pet_profile 格式化后填入指令。

## 分支速查

| 条件 | 处理 |
|---|---|
| 不是新用户消息，已有 safety_flag | next_agent=FINISH |
| 不是新用户消息，无安全标记但有诊断/推荐 | next_agent=safe_check_agent |
| 不是新用户消息，以上均不成立 | FINISH |
| 新用户消息命中下线功能 | 固定回复并 FINISH |
| 其他新用户消息 | 调用内部路由 Agent，读取 structured_response |
| 结构化决策缺失 | FINISH，未必生成新的解释性回复 |

示例（提示词预期，非实测）：“旺财为什么挠？”→问诊；“给旺财选什么用品？”→推荐；“查看旺财病历”→病历。

## 协作关系

```mermaid
flowchart LR
    A[消息与宠物档案] --> B[调度节点的规则与模型判断]
    B --> C[写入下一节点字段]
    C --> D[图路由读取字段]
    D --> E[所选专家或结束]
```

省略上游身份门控、摘要和专家返回路径。

## 易混淆点与限制

- 节点函数、内部模型 Agent、图路由函数不是同一对象。
- 用户说“你好”可能先被身份节点追问，不能把 Supervisor 提示词示例当作入口保证。
- Worker 返回调度时不再调用意图路由 Agent，但可能有摘要调用。
- 旧 safety_flag 导致后续审查被跳过的静态风险见 3.3；本节不重复修复。
- 通用系统也可采用纯规则或其他调度方式，本项目选择模型与规则结合。

## 验证与答疑

仅静态核对源码，未运行模型或评估；示例不代表实测。重要答疑：暂无。
