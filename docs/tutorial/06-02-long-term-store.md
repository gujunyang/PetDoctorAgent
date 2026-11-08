# 6.2 长期记忆：怎样区分用户和宠物

日期：2026-11-08。状态：已讲解／答疑中，未确认理解。

## 核心结论

- Store 保存程序明确写入的用户/宠物档案及问诊历史，按业务身份组织，便于跨会话读取。
- Namespace（命名空间）是元组路径；key 是该空间内的记录名；value 是实际数据。目录类比不是磁盘目录的真实实现。
- user_id 指用户，pet_id 指宠物，thread_id 指会话；一个用户可有多只宠物、多次会话。
- Store 接入图不意味着会自动从全部聊天中提取并保存事实，业务代码必须调用 put/get/search。
- 当前检索主要是命名空间和字符串匹配，不是向量语义记忆；search 方法名本身不能证明使用语义搜索。

## 关键文件与函数

- [memory.py](../../petdoctor/memory.py)：get_store、_profile_ns、_pets_ns、_pet_history_ns、create_pet、get_pet、find_pet_by_name、load_pet_history、save_diagnosis_summary。
- [identity.py](../../petdoctor/identity.py)：身份与 Store 数据衔接。
- [graph.py](../../petdoctor/graph.py)：load_memory_node、_resolve_memory。

## 数据组织

| 内容 | namespace | key |
|---|---|---|
| 用户资料 | ("users", user_id, "profile") | "info" |
| 宠物档案 | ("users", user_id, "pets", pet_id) | "profile" |
| 宠物问诊历史 | ("users", user_id, "pets", pet_id, "history") | 每条生成的唯一标识 |
| 未识别宠物的用户级历史回退 | ("users", user_id, "history") | 每条生成的唯一标识 |

档案读写源码：

```python
get_store().put(_pets_ns(user_id, pet_id), "profile", pet_profile)
```

```python
item = get_store().get(_pets_ns(user_id, pet_id), "profile")
```

同 namespace 与 key 对应同一条记录；不同宠物使用不同空间。历史使用新 key，因此可以保存多条记录。value 是程序选择写入的字典，不是完整会话自动镜像。

## 查询逻辑与持久化

- get_pet 按 user_id/pet_id 精确读取档案。
- find_pet_by_name 在用户 pets 前缀下 search(limit=100)，先做名字精确匹配，再做包含匹配；同名消歧和全量翻页未实现，前缀结果还可能包含子空间记录。
- load_pet_history 默认 limit=5，没有显式按业务 date 排序或按当前症状排名，不能直接称为最近五条或最相关五条。
- get_store 尝试 PostgresStore，初始化失败或无配置时使用 InMemoryStore。长期是业务用途，InMemoryStore 重启会丢；同进程复用 Store 时不同 thread_id 可以共享其业务记录。
- saver/store 分别初始化，不能仅凭一个初始化日志断定另一个也已持久化。
- get_store 缓存在模块全局；业务函数显式调用它。graph 的 store 参数不是所有业务读写的自动替代。

## 隔离与鉴权

- 命名空间按传入 user_id 分组，支持逻辑隔离，但 main 的 --user 是用户提供的字符串，没有登录验证。
- 检查点路径主要使用 thread_id；代码没有将 user_id/thread_id 所有权绑定验证。不能承诺不同 --user 即完整隔离全部数据。
- MCP 演示病历工具按 pet_id 查询，是另一条数据路径，不受本地 Store 命名空间自动保护。
- 本节只解释已见实现边界，不进行权限修复或真实数据操作。

## 易混淆点

- 短期与长期均可使用同一个 PostgreSQL 服务，但数据用途与读取入口不同。
- 新会话可从 Store 找回宠物档案，不会自动恢复旧会话全部消息。
- save_user_profile 是可调用的辅助函数，未见当前业务流程自动收集并写入完整主人档案。
- 多个读函数将异常转换为空结果，看到空档案未必证明数据库中绝对没有记录。

## 验证与答疑

仅静态阅读当前实现，未查看或修改真实数据库、未验证持久化。重要答疑：暂无。
