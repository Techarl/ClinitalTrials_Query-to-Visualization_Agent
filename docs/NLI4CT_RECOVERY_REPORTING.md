# NLI4CT 中断恢复与最终报告规则

这是提交之后个人项目阶段的报告层补充，不修改已提交 ZIP、冻结实验源码、原结果或运行中的 producer。最终质量报告只能在 500 题 × 5 组全部终止、同一 200 题回答子集齐全且当前恢复进程成功关闭后生成。

## 已锁定的恢复事实

- 冻结身份：`476bb042ff5273e16975f4e638aef8ff0c85e62877ca08a6bea540c038ea86ed`，70 个源码文件。
- 桌面进程中断前的备份包含 14,983 个文件；ZIP SHA256 为 `c9d37015e4becdc20161f4a37ee2de52feb45d3fe7f2492d50158f289587dc66`。
- 仅根据备份文件名，394 题存在活动，391 题的五组结果已落盘；共 1,964 份 arm 结果。这些是执行进度，不能解释为正确率。
- 首次恢复因依赖导入失败退出；启动日志确认发生在付费调用前。有效的第二次恢复使用原实验依赖环境，并重新通过冻结身份校验。正式关闭记录是 `nli-resume-exit-2.json`；不得用第一次失败的 `nli-resume-exit.json` 代替。
- 恢复审计记录的 10 次未知用量预留为 $0.0799175，仍占原 $20 上限。报告准备阶段没有读取活动账本；最终将从备份账本与完整正式账本重新核对。

## 延迟排除规则

规则和具体 case/arm 清单在没有读取片段评分、答案或标准答案的情况下保存于 [恢复延迟计划](public-evaluation/nli-recovery-latency-plan.json)。读取范围只有恢复清单、ZIP 目录和冻结 protocol 元数据；没有打开 case 结果文件。

对恢复前已存在文件的题目，如果某个 arm 的最终结果尚未落盘，则保守排除其 `ranking_seconds`；若已有失败标记，也排除。该规则可能涵盖当时尚未开始的后续 arm，不声称全部都是已调用但中断的工作。

本次固定清单包含三道题的 Luna 与 full，共六个 arm。两道题已有 12/8 个 Luna 片段评分缓存，第三道没有落盘的 Luna 评分。full 没有片段缓存，但按统一保守规则处理。不得根据最终成绩或延迟重新选择排除对象。

质量指标、失败、置信区间、主分母 500 和回答分母 200 保持不变。原始计时保留在脱敏逐题 JSON，并另列为诊断统计；筛除后的延迟显示保留和排除样本数。另提供五组均完成且均没有中断标记的共同题目延迟摘要，避免各组时间样本来自不同题目集合。共同题目摘要只用于时间，不产生新的质量主分析。

排序时间仍不包括共享 embedding 预热、初召回、停机时间和全部重试成本。Jev/Luna 的连接实现不对称也仍存在，因此这些时间不能宣称是模型服务速度或端到端任务延迟。独立开发集的 [公平时延控制](JEV_LATENCY_CONTROL.md) 单列。

## 费用与恢复证据

最终报告将确认：备份 ZIP 未改变，成员清单和 protocol 一致；备份账本的所有事件是最终账本的**逐项相同前缀**。任何原尝试被删除、未知预留被清零或事件被改写都会阻止发布。

备份账本只提供恢复前费用小计和未知预留说明，绝不能作为第四份独立账本加到费用总数。`formal_v2` 已包含这些事件以及恢复后的新尝试；pilot、aborted_v1 继续单独列出。快照、关闭记录和预先保存的排除计划均保留哈希；不导出原始材料或答案。

## 最终运行

在当前仓库目录执行下面命令。`$nliOriginal` 仅指定原冻结研究数据的位置，不改变 producer 或环境。只有 `report.json` 完整且第二次恢复的退出记录为 `completed` 时才可运行；不能用进度日志推断结束。

```powershell
$nliOriginal = 'C:/path/to/frozen-study-workspace'
$nliRecovery = "$nliOriginal/data/review-followup/crash-recovery-20261101-0950"
python -m scripts.report_public_nli `
  --study "$nliOriginal/data/nli4ct-official-test-v2" `
  --inputs "$nliOriginal/data/external-benchmarks/nli4ct-test-inputs.json" `
  --pilot "$nliOriginal/data/public-pilot-nli4ct-v1/ledger.json" `
  --aborted "$nliOriginal/data/nli4ct-official-test-v1/ledger.json" `
  --recovery-manifest "$nliRecovery/nli-recovery.json" `
  --recovery-completion "$nliRecovery/nli-resume-exit-2.json" `
  --recovery-plan docs/public-evaluation/nli-recovery-latency-plan.json
```

输出仍为 `docs/NLI4CT_RESULTS.md` 及 summary/cases/accounting JSON，新增 `nli4ct-recovery.json`。已知的中断协议若缺少恢复参数会直接拒绝发布；关闭标记失败时，在读取最终成绩前退出。
