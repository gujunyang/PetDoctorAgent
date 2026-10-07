# 建议修复补丁（未应用到业务代码）

> 以下均为**建议**，本次未修改任何 `petdoctor/` 业务代码。可逐条 review 后应用。
> 行号基于 git `2883807`。

---

## P1 · D1：首句急诊不再被识别门阻断（`petdoctor/identity.py`）

在「信息不足 → 追问宠物」之前加入紧急关键词兜底：

```diff
@@
 from petdoctor import memory
+from petdoctor.agents.safe_check import EMERGENCY_KEYWORDS, EMERGENCY_REPLY
 from petdoctor.config import get_llm
 from petdoctor.state import PetClinicState
@@
     # 6) 信息不足 → 追问，本轮结束
     if not name:
+        # 安全兜底：首句若含紧急关键词，先报警，再请用户补充宠物信息
+        haystack = (_last_user_message(state) or "").lower()
+        hit = next((kw for kw in EMERGENCY_KEYWORDS if kw.lower() in haystack), None)
+        if hit:
+            return {
+                "safety_flag": "emergency",
+                "messages": [AIMessage(
+                    content=f"{EMERGENCY_REPLY}（命中关键词：{hit}）"
+                            "请再告诉我宠物名字，我会核对档案后继续协助。"
+                )],
+            }
         question = "请问这次是哪只宠物？告诉我它的名字（或编号）即可。"
```

> 备选方案：在 `graph.route_after_identify` 中判断状态里是否存在紧急症状，若存在则路由到 `safe_check_agent` 而非 `END`。

---

## P1 · BC-002：扩充紧急关键词（`petdoctor/agents/safe_check.py`）

```diff
 EMERGENCY_KEYWORDS = [
     "中毒", "误食", "大量出血", "呼吸困难", "抽搐",
     "意识丧失", "昏迷", "瘫痪", "持续呕吐", "便血",
+    # 常见毒物
+    "巧克力", "可可碱", "葡萄", "葡萄干", "洋葱", "大蒜", "百合",
+    "木糖醇", "对乙酰氨基酚", "扑热息痛", "布洛芬", "菊酯", "老鼠药",
+    "杀鼠剂", "抗凝血",
+    # 急诊情形
+    "尿闭", "尿不出", "排尿困难", "难产", "中暑", "热应激", "误吞异物",
     "poison", "seizure", "unconscious", "bleeding heavily",
 ]
```

---

## P1 · BC-003：全局免责/就医提示兜底（`petdoctor/agents/safe_check.py`）

在 `safe_check_node` 返回前，对 safe/warning 且答复中无就医提示的，追加统一提示（示例）：

```diff
+_VET_HINTS = ("医院", "就医", "就诊", "兽医", "面诊")
 ...
     # 长期记忆：safe / warning 写入问诊历史摘要，emergency 不写入
     if flag in {"safe", "warning"}:
+        if not any(h in reply for h in _VET_HINTS):
+            reply = f"{reply}\n\n（温馨提示：本建议仅供参考，若症状持续、加重或不确定，请及时咨询执业兽医或线下就诊。）"
         memory.save_diagnosis_summary(state, config, safety_flag=flag)
```

---

## P2 · BC-008：评测可复现（`petdoctor/config.py`）

```diff
     return ChatOpenAI(
         model=model,
         api_key=SecretStr(os.getenv("LLM_API_KEY", "")),
         base_url=base_url,
-        temperature=0.7,
+        temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
+        seed=int(os.getenv("LLM_SEED")) if os.getenv("LLM_SEED") else None,
         **extra,
     )
```

---

## P2 · BC-006：Postgres 不可用快速回退（`petdoctor/memory.py`）

```diff
         try:
             from langgraph.checkpoint.postgres import PostgresSaver
-
-            _checkpointer = _stack.enter_context(PostgresSaver.from_conn_string(url))
+            # 加连接超时，避免无服务时长时间阻塞
+            _checkpointer = _stack.enter_context(
+                PostgresSaver.from_conn_string(url + ("&" if "?" in url else "?") + "connect_timeout=3")
+            )
             _checkpointer.setup()
```

> 同样处理 `PostgresStore.from_conn_string`。若 URL 参数拼接复杂，可用 `os.environ.setdefault("PGCONNECT_TIMEOUT", "3")`。

---

## P2 · BC-004：跨物种用药硬校验（`petdoctor/agents/recommend_product.py`，新增守卫）

建议在推荐节点输出前做物种-药物黑名单过滤：

```diff
+_CAT_FORBIDDEN = ("对乙酰氨基酚", "扑热息痛", "布洛芬", "菊酯", "permethrin", "伊维菌素")
 ...
     reply = product_list.reply or answer or "已为您生成产品推荐。"
+    if (state.get("pet_profile") or {}).get("species") == "猫":
+        if any(d in reply for d in _CAT_FORBIDDEN) and "禁用" not in reply and "剧毒" not in reply:
+            reply += "\n\n⚠️ 注意：上述成分部分对猫有剧毒，请勿自行给猫使用，务必先咨询执业兽医。"
     return {...}
```

> 更稳妥的做法是在 `recommend` 之后加一个确定性的安全二次校验节点。
