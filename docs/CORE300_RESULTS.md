# Core300 冻结执行评测结果

300 个模板派生 episode、15 个疾病–药物群组；留出集为 200 题、10 个群组。失败保留在分母。详见 [冻结协议](CORE300_PROTOCOL.md)。

| 集合 | 正确 / 预定题数 | 群组数 | 群组 bootstrap 95% |
|---|---:|---:|---|
| 全部 | 295 / 300 | 15 | 96.7%–99.7% |
| calibration | 39 / 40 | 2 | 95.0%–100.0% |
| development | 60 / 60 | 3 | 100.0%–100.0% |
| heldout | 196 / 200 | 10 | 96.0%–100.0% |

只有 15 / 10 个群组，区间为探索性描述；重复模板和小群组数限制统计推断。即使区间退化到 100%，也不能保证新的任务全部正确。

| 任务类别 | 正确 / 预定题数 | 不正确或失败 |
|---|---:|---:|
| boundary | 40 / 40 | 0 |
| compound_or_dependent | 76 / 80 | 4 |
| multi_turn | 60 / 60 | 0 |
| single | 119 / 120 | 1 |

全部失败代码（次数，不是互斥失败题数）：

```json
{
  "status": 5,
  "artifact": 5,
  "dependent_selection_missing": 2,
  "constraint": 3
}
```

原始人口合计 4376 条记录、4349 个去重 NCT ID。新增独立 SQL 数值核验覆盖分类与共享药物网络；时间/散点/直方图仍由已有原始字段核验器负责，同时所有范围由独立父人口成员检查覆盖。成功指标依赖这些自动评分规则，不等于人工临床验证。

| 费用阶段 | 调用尝试 | 已知费用 USD | 未知预留 USD |
|---|---:|---:|---:|
| pilot | 57 | 0.441391 | 0.000000 |
| formal | 2271 | 13.915365 | 0.373053 |

总估算或预留 **$14.729809**，包括失败调用。费用不是提供商最终账单。正式结果不合并 pilot 得分。

[汇总与全部分层](public-evaluation/core300-summary.json) · [逐题指标](public-evaluation/core300-cases.json) · [来源人口](public-evaluation/core300-sources.json) · [费用账本汇总](public-evaluation/core300-accounting.json)

执行状态与成功指标须分开理解：正式保存 420 轮，其中包括按题目要求拒绝不支持因果判断的轮次。不能把全部正确 episode 都描述成成功生成了图表。下列诊断只引用已保存的错误类型，不推断未记录的根因：

| Episode | 轮次状态 | 模型调用错误 | 工具错误 |
|---|---|---|---|
| `ep_4e3a5a15e6eace3c` | partial | APITimeoutError | 未记录 |
| `ep_59294b2ffabede77` | partial | 未记录 | select_entities: ClientError |
| `ep_c63f6ff70355dcd7` | failed | APITimeoutError | 未记录 |
| `ep_e3276466708534eb` | partial | 未记录 | analyze: ClientError, select_entities: ClientError |
| `ep_f9adf337490f6c40` | failed | InternalServerError | 未记录 |

这些失败均保留在原始分母，未选择性重跑。图工具的 ClientError 未保存具体 Neo4j code 或完整 traceback，不能据此确定是超时、竞争或其他数据库原因。后续代码修复不改变本次冻结结果。

另见 [数据依赖关联审计](CORE300_SELECTION_AUDIT.md)：对全部要求依赖选择的轮次核对 anchor、top-N、scope、lineage 和 artifact dependencies；原始分数与审计分数并列，其他评分规则保留。
[冻结协议文件](public-evaluation/frozen-protocols/core300-protocol.json) 与 [执行源码归档](public-evaluation/frozen-protocols/core300-source.zip) 标识本次运行版本。
