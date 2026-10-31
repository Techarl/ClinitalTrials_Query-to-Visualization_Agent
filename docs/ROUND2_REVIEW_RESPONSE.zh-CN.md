# 第一轮 review 的处理记录

审查基准为 `2c64cff`，开始修复时 main 为 `2120068`；后者增加 Docker 实测及说明，应用核心未变。保留原架构。以下是工程修复与验证记录，不是医学或统计代表性评测。

| 项目 | 修复 | 对应验证 |
| --- | --- | --- |
| R1 ALL 被拆组 | 明确 population/comparison scope；population 中每组必须保留完整显式药物集合，ALL 不得拆组 | AND、OR、compare 及共同疾病约束回归 |
| R2 连续澄清 | 保存原始请求、累计回复、confirmed_filters 和未解决问题 | 两次澄清、重启、故意错误疾病计划拦截；真实三轮会话 |
| R3 finalize 窗口 | session CAS、run response、branch state 在同一 SQLite 事务提交；重复 finalize 返回已完成结果 | run 写入 SQL trigger 失败回滚、提交前后进程崩溃、恢复不重规划 |
| R4 解释过期 | patch 清除旧模型 assumptions；按 resolved plan 生成 analysis_scope、assumptions、不同图型 metric_definition | operation/phase/year patch 回归；重新生成示例 |
| R5 历史 parent | 每次完成保存完整分支状态，历史澄清与成功父均恢复对应分支；失败父在 planner 前拒绝 | 澄清→新话题→选择旧澄清父；跨 session 父；历史成功父 |
| R6 缓存损坏 | 完成并 fsync 原始数据后，以 manifest 原子替换作为发布标记；损坏候选跳过并保留日志 | 无关半写 manifest、有损 raw、有效缓存并存 |
| R7 verifier 假阳性 | 独立 raw JSON oracle 生成完整 rows/edges/nodes，检查 top-N、排序、零桶、角色贡献、证据与精确字段 | 26 种符合 schema 的故意破坏必须失败；五例完整验证 |
| R8 全缺失 enrollment | 在分箱前独立统计缺失数，不填零 | 全部 enrollment 缺失时 no_data、排除数与 cohort missingness 一致 |
| R9 总 deadline | ContextVar 传递单次运行预算；逐 query 检查，事务 timeout 取剩余预算和 30 秒较小值；不自动重试事务 | 慢首批后不再导入后续批、不发布 ready；已过期时不访问 driver |

图数据库预算是协作式截止：不启动超时后的后续工作，服务端事务 timeout 受数据库检查时机影响，连接和取消也可能有少量延迟；不能保证任何外部调用在指定毫秒瞬间停止。初始化 schema 发生在 run 创建前。

额外补充 PlanPatch 的 top_n/histogram_bins/cohort_label；未改变统计全集的官方搜索语义。BM25/embedding 可以提供术语或经验候选，不能作为统计全集。

真实语义评测与离线数值核验分开：`python -m app.semantic_eval --live` 才会收费；不加 --live 只校验 case。48 个 case / 50 轮、36 development 与 12 heldout，在首次模型运行前冻结。人工编写预期，允许明列的搜索名等价，不独立医学标注；一遍通过率没有总体准确率或稳定性保证。标准答案只进 scorer，不进 planner。

第一遍结果 49/50 符合预期；执行计划 37/37、应澄清 9/9；错误执行 0/50、过度澄清 0/37。差异为全站请求返回明确说明“不支持”的澄清状态，预期 unsupported，保留失败。124,827 input / 11,199 output tokens，50 次主模型调用；供应商没有返回实际美元金额。此轮开发运行期间调整了与规划无关的元数据代码；发布版本会单独重跑并记录冻结代码版本。

新增测试与容器实测的最终结果、源码版本和示例见最终验证报告；两个实验只有独立显式命令可推进，基础 API 默认关闭。

第一轮冻结验证：核心源码 `ea65a0a`，五组示例、50 轮 release 评测和 Docker 使用相同 code hash。Release 评测 50/50；前一遍 49/50 也保留，说明并非零方差。五组实际示例现覆盖 melanoma、asthma、psoriasis、rheumatoid arthritis；独立 oracle 检查 33,824 个原始字段，并双向检查完整数据集合。基础回归 68 passed；加入隔离研究边界后本机和 Linux 都为 75 passed。GitHub Actions 的 `f3cc093`、`69bd5ea` 两次检查成功。

另外使用全新 Compose 数据卷完成取数、重启追问和证据检查；ZIP 解压至全新目录，按 lock 安装成功，68 项离线测试通过、7 项真实数据库测试按预期跳过，五例离线核验通过，实际 factory 入口启动成功。验证报告分别为 `VALIDATION.json`、`CLEAN_DOCKER_VALIDATION.json`、`ZIP_VALIDATION.json`。

最终补强：源码 `5344202` 进一步核验 encoding 字段/类型、完整性与缺失值元数据，以及 no_data 中多余证据；新增 5 个回归先失败后通过。总计 81 tests passed（本机与 Linux），26 种符合 schema 的破坏产物全部被拒绝。五组真实示例和最终 50 轮语义报告使用一致的最终 code hash。最终语义为 49/50，执行计划 37/37、应澄清 9/9；全站请求再次返回有明确不支持说明的澄清状态，保留失败记录。实验仍使用事先冻结的 ea65a0a baseline，不以新的运行覆盖既有对照。

最终 ZIP 候选也重新解压至全新目录并安装：74 passed、7 个 live Neo4j fixture 按预期跳过；五组离线 oracle 与实际 factory 启动通过，model_calls=0。完整依赖和运行内容会与最终打包文件逐一核对。
