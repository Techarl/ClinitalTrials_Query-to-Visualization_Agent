# 第二轮 review 收尾：F1–F4

审查基准 `10f5161`；本轮从该版本开始，没有额外框架或扩大实验。修复源码冻结为 `118a0e5`，prompt 标记为 planner-v3。原始语义/决策/Harness/检索报告保持原生成版本，不把历史结果改写成新实现的结果。

| 项目 | 修复 | 验证 |
| --- | --- | --- |
| F1 直方图区间 | Fraction 构造精确边界，每个边界只转一次浮点；按同一输出边界序列二分入桶，末桶右闭。不使用 epsilon。不能可靠表示边界时拒绝输出图表。 | 三组原复现 [0,9,18]/14、[0,15]/11、[0,33,44]/20；零值、同值、缺失、多 cohort；真实 Neo4j 双 cohort 路径。 |
| F1 oracle 共因错误 | oracle 使用高精度 Decimal 构造区间并逐区间匹配；另用原始 enrollment 直接检查**输出声明区间**，每个有效 NCT 在各自 cohort 中恰好一次。 | 故意同时让生产分桶和 expected-table oracle 接受错误桶，额外区间不变量仍必须拒绝该产物。 |
| F2 scope 覆盖 | 当前显式 scope > confirmed_intervention_scope > 原始/default；用 fields_set 区分省略与显式 population，pending 累计保存确认值。Context 不再把每轮未填写的默认值展示为新输入。 | 双向修改、追加一次澄清、省略字段、重启、历史澄清父节点，以及立即进入比较执行。 |
| F3 调用丢失 | 独立追加 model_events 表，在外部调用前原子保存 request；结果/失败也持久化。计划、异常 finalize 和恢复共用 journal/checkpoint 对账，展示 trace 被覆盖也不丢 attempt。 | 真实 OpenAI SDK + 本地 MockTransport：坏 JSON、错误响应形状、未预期异常、超时；checkpoint 外层故障、trace 覆盖、重启。 |
| F3 已知结果恢复 | provider 结果已提交但 checkpoint 尚未提交时，恢复复用既有 decision；未知或缺失 usage 保持 unknown，错误仅记录类型。 | 注入模型响应已保存后的进程崩溃，恢复没有第二次模型调用；异常正文标记不得出现在最终运行记录。 |
| F4 预算释放 | 缺失、部分、无效或不一致的 token/cache 用量保留原 reservation，标 completed_usage_unknown；只有完整可信用量结算，重复结算幂等。 | None/空字典/部分字段/负值/bool/不一致 cache；未知后补齐用量、重复 after、第二次调用被预算拦截。 |

新增测试先复现原缺陷，再按正确行为验收；修复后本机 **109 passed，0 skipped**，其中 8 项使用真实 Neo4j。Lint 与依赖检查通过。详细计数及容器结果见 `ROUND3_VALIDATION.json`；远端 CI 可在仓库的 Actions 中按最终提交查询。

## 原始产物与当前计算路径

原五例继续保留原 `5344202` 运行源码、c67e1ac… code hash、request/response/trace 和完整证据，语义评测仍分别保留发布 49/50 与实验基线 50/50。它们不是 planner-v3 的新语义评测。

当前引擎读取五例的完整冻结 JSON，经过真实 Neo4j 查询重新计算，比较 visualization、统计 metadata、provenance 和完整 evidence；没有调用模型或 ClinicalTrials.gov HTTP。五例结果全部相同，并再次通过增强 oracle，共核对 33,824 个原始字段。现有 psoriasis 八桶例子没有受到本次分桶修复的数值/格式影响，因此不重新生成付费产物。重放记录为 `ROUND3_EXAMPLE_REPLAY.json`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --live-neo4j
.\.venv\Scripts\python.exe -m app.verify_examples
# 当前 Neo4j + 当前计算逻辑，对原样例完整重算；零模型/源 HTTP 调用：
.\.venv\Scripts\python.exe -m scripts.replay_frozen_examples --output data\new_frozen_replay
```

既有 238 次 Harness 调用全部有完整用量；用新规则复核后，原估算仍为 $1.106343。没有重跑付费实验，也没有调阈值覆盖负结果。默认服务的两个实验开关继续关闭。

Linux 容器同样 **109 passed，0 skipped**。另用真实主模型做了两轮定向联调：第一轮 scope 冲突返回 needs_clarification，第二轮显式改为 comparison 后返回 ok，保留 melanoma 并统计为 400/396 两组；两次调用与真实 attempt trace 一致，usage_complete 均为 true。它只验证这次纠正路径，不替代原语义评测。记录见 `ROUND3_LIVE_SCOPE.json`。

交付 ZIP 解压后重新安装当前源码，通过 101 项离线测试、五例核验及依赖检查，8 项数据库测试按预期跳过。本次复用之前干净安装且 lock 文件未变的验证环境，不声称重新创建了全新依赖环境。记录见 `ROUND3_PACKAGE_VALIDATION.json`。
