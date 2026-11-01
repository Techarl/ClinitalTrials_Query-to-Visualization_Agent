# Cheiron 下一轮实现规格

日期：2026-11-01（北京时间）

目标仓库：https://github.com/Techarl/ClinitalTrials_Query-to-Visualization_Agent

本轮确认的目标：继续开发。补齐前三个相近公开项目的主要能力，并保留当前完整统计、来源追溯和持久化状态的优势；同时按修订方案完成决策模型与 Harness 第二阶段实验。以下内容是实施要求，不是已经完成的功能说明。

编写时 main 为 dab19217d0dc7330c1adf359c6ad74ad6fe47e93。执行者必须先检查当前 HEAD 和未提交工作，在当前修复之上增量实现；不要覆盖正在进行的修改。旧版 handoff 中“本期只做基础服务、实验只留接口”和 review 中“修完即冻结”的范围限制，已被用户本轮新要求取代。

## 1. 本轮完整范围

用户指定三个参考项目：

- [yipjunkai/takehome-surgical](https://github.com/yipjunkai/takehome-surgical)，比较版本 5ec3d405099922d3fb4ae54f09260a3376e4b716。
- [bibekyess/Query-to-Visualization](https://github.com/bibekyess/Query-to-Visualization)，比较版本 ca2832830988c07ea62bb3248be4df8acec1542a。
- [JoelKShaju/qtov](https://github.com/JoelKShaju/qtov)，比较版本 b035ca6dfe025b465b043daf0a82f62104919378。

以下能力全部进入交付范围。阶段划分表示实现依赖，不表示删减后面阶段：

| 编号 | 能力 | 实际交付及验收重点 |
| --- | --- | --- |
| A01 | 结构化规划与查询编译 | typed TaskSpec/Action/Result；复用固定计算、范围校验；无任意执行代码 |
| A02 | 药物身份与别名 | 有来源、版本和置信状态的候选；品牌/通用名/代号；识别同药比较；歧义可澄清 |
| A03 | 查询范围扩展 | 完整分页及可审计分区；有限维度官方精确计数模式；精确数与证据覆盖分开 |
| A04 | 图表有效性提示 | 单类别、过短趋势、空系列、集中度、缺失过高、稀疏网络等规则及可解释提示 |
| A05 | 真实工具调用循环 | 根据一次工具观察选择下一步工具；支持继续查询、修复、澄清、完成及有理由停止 |
| A06 | 多轮会话及执行恢复 | 追问、修改/清除条件、历史分支；run 内工具循环；断线/重启不会重复计算或误记费用 |
| A07 | 聊天与流式步骤 | 真实 SSE 事件与恢复；呈现操作、发现与依据；不以假动画替代真实进度 |
| A08 | 图表覆盖 | 原有柱/分组柱/时间序列/直方/网络；新增散点，地理条形及适用时的国家分布地图 |
| A09 | 日期与数值分析 | 明确日期字段，年/月粒度，开始/完成/首次发布；入组人数、试验持续时间与缺失/精度 |
| A10 | 多阶段分析 | 排名或关系结果决定后续 cohorts；一次任务可返回有关联的多个图表 |
| A11 | React + TypeScript + ECharts | 聊天、筛选、图表、网络、解释、来源抽屉、错误/空数据/等待状态 |
| A12 | 点击引用与替代图 | 点柱/散点/边定位贡献记录；语义等价图切换复用同一结果；改变口径则新分析 |
| A13 | 历史、分享、导出 | 保存只读结果快照、分享路径、JSON/证据导出；不把可写会话凭据放在分享链接 |
| A14 | PubMed | 实际检索与文献元数据/摘要来源；聊天可选择文献工具；与 CTgov 统计证据分开 |
| A15 | MCP | 基于共享工具注册表暴露查询、分析、文献及证据读取，实际客户端调用通过 |
| A16 | 评测与可观测性 | 端到端挑战任务、真实工具回放、trace、用量、时延、完整性与前端验收 |
| B01 | 决策实验第二阶段 | 原子判断、系统修复与用户澄清分流、正确/错误计划辨别；进一步评估模型级联 |
| B02 | Harness 第二阶段 | 真实执行反馈、有效 skill/context 修改、具体经验、去重和跨任务复用对照 |

参照其他项目的设计，不默认复制没有明确许可证的代码。复用时记录许可、来源和改动；独立实现同类能力不要求复制其实现。

## 2. 保留的基础与新的架构边界

继续使用 FastAPI、LangGraph、Neo4j 和 SQLite。前端使用 React/TypeScript/ECharts。不为了实现以上范围强制引入新的队列、数据库或第二套 Agent 框架。

当前固定分析服务仍作为可测试的计算内核。把单体执行路径拆成工具后，API、Agent、CLI、MCP 和实验都调用同一实现，避免统计口径出现多份副本。

必须保留：

- 原始来源快照、内容哈希、请求参数和获取时间。
- dataset-scoped 的试验与关系，NCT 去重及明确贡献集合。
- 完整字段引用和完整 evidence 导出。
- 原子 session/run 完成、历史分支、调用前 attempt 与结果账本。
- 来源错误不等于零研究，缺失字段不等于零，展示裁剪不等于来源完整。
- 原始研究实验不可被新版输出覆盖；每次新实验使用新的版本和报告。
- 旧 API 的现有调用可继续使用，或有明确的版本适配。不要无声改变旧 start_year/end_year 的含义。

ClinicalTrials.gov 继续作为试验统计的权威数据来源。PubMed 提供文献背景和直接文献引用，不代替试验注册统计，不将两种来源的数量混合。

## 3. 三种多轮分别实现

1. 对话多轮：用户后续修改条件，继承或清除原有范围，必要时从历史 parent 派生新分支。
2. 单任务工具多轮：用户只问一次，Agent 读取工具观察，多次查询/分析后完成。
3. 流式传输：前端持续看到该 run 的真实事件；重连不意味着重新发起任务。

当前已有规划、验证、固定执行与有限修复。新增重点是“工具结果 → 下一步决策”的环，而不是仅把节点增加或把一次回答拆成多条消息。

### 3.1 LangGraph 节点和路由

建议状态转换：

~~~mermaid
flowchart TD
    A["接收任务与恢复约束"] --> B["选择下一步行动"]
    B --> C["校验参数与预算"]
    C --> D["执行类型化工具"]
    D --> E["检查结果与覆盖"]
    E -->|"需要继续或修复"| B
    E -->|"缺用户信息"| F["保存澄清状态"]
    F -->|"用户答复"| B
    E -->|"满足任务"| G["组装并验证交付"]
    E -->|"无进展或预算耗尽"| H["返回有依据的限制"]
~~~

执行观测只包括可解释的状态、数据摘要、错误、待解决问题和下一步简短理由。界面不展示模型私有思维链。

建议 Action union：

- call_tool
- repair_plan
- clarify_user
- finalize
- stop_with_limit

语义审查和确定性检查分开。计划错误由系统修复；用户确实缺少必要信息才澄清。固定 verifier 对范围、统计与证据的判定不能被模型一句“足够了”覆盖。

### 3.2 状态契约

状态至少包含：

- run_id、session_id、parent_run_id、schema/model/prompt/tool 版本。
- 原始请求、当前显式约束、确认后的实体/范围、待澄清字段。
- TaskSpec：任务目标、需要交付的图表/解释、时间口径、来源模式、完成条件。
- 已执行步骤及依赖、当前动作、结构化 observations、未满足的子目标。
- dataset/result/evidence/literature 句柄，避免全量原始 JSON 进入模型上下文。
- 每一步来源范围、获取版本、输入哈希、产物哈希、错误与恢复状态。
- deadline、剩余模型/工具/API 次数、费用及未知用量预留。
- 数据集版本、已尝试 query 签名和进展签名。
- event sequence、已完成 tool-call journal、最终响应。

显式约束是不可变事实的版本化集合。任何用户更改都形成新版本；后续工具动作必须证明与对应版本兼容。语义等价的别名展开可记录后执行；统计集合的放宽必须有用户已有授权或新的明确选择。

### 3.3 工具层

接口名可随代码调整，职责必须存在：

| 工具职责 | 典型输入/输出 |
| --- | --- |
| resolve_entity_candidates | 原始名称 → 带来源的候选、匹配类型、歧义状态；不自动认定相似即等价 |
| inspect_query_scope | 查询条件 → 官方匹配量、字段能力、预计获取路径与限制 |
| search_trials | 合法范围 → 完整/部分/失败状态、dataset_id、来源清单 |
| fetch_trial_details | NCT ID → 具体试验和字段证据 |
| profile_dataset | dataset_id → 日期/数值缺失、字段分布、覆盖、可分析维度 |
| derive_cohort / select_entities | 已核验结果 → 有 lineage 的集合选择与筛选，保留根总体 |
| aggregate / compare / histogram / scatter | 数据句柄、操作参数 → 固定代码计算的 result_id |
| build_network / query_relation | 有界关系模式 → 节点、边、贡献路径与 result_id |
| search_pubmed / fetch_articles | 文献问题/PMID → 原始元数据、摘要及 citation_id |
| inspect_evidence | 结果项 → 分页完整贡献证据及原始字段 |
| validate_result | 结果 → 范围/集合/字段/数值/渲染合同检查 |
| finalize_analysis | 结果句柄和解释引用 → 版本化响应与可渲染 artifact |

模型可以查看数据摘要、候选名称、少量有来源的文本和检查结果；最终统计值从 result store 装配。模型不能直接改写一个 count 或生成不存在的 evidence ID。

简单问题可以直接完成；不为显示“多步”强制遍历所有工具或每步增加一次通用反思调用。单次任务的行动序列必须来自当前目标及已发生的工具观察。

API、CLI、MCP 共用工具注册表、输入输出 schema、预算与来源逻辑。工具之间使用句柄，不让客户端自报任意 dataset_id 获得别的会话数据。

### 3.4 继续查询与终止规则

可以继续的原因必须具体，例如：

- 某个用户子问题尚无结果；
- 必要实体未解析，需要候选检索；
- 首次匹配为空，需要检验拼写/已验证别名；
- 排名结果决定第二步分析对象；
- 来源分页/合法分区尚未完成；
- 数据不支持所选字段，需要解释缺失或确认替代方案；
- 工具错误可在预算内修复。

“没有结果”不自动允许删除 phase/date/country。重复相同查询且来源未变、不增加新信息时，检测为无进展。对重复、修复、扩展和总时长分别有上限；预算可以配置，但不能无限循环。

若因预算/来源限制无法完成，说明已完成和未完成的具体内容，不能把结果包标记成完整回答。

### 3.5 恢复、幂等与事件

checkpoint、tool journal、attempt ledger 的职责分开。查询开始前记录动作；完成后保存结果句柄；重启先对账。

相同已完成动作可以复用版本匹配的产物；执行中崩溃导致结果未知时保留 unknown 状态，不能假定没有费用。外部 API 不提供幂等语义时，不承诺 exactly-once 网络请求；保证不重复导入、不重复汇总，重试行为可见。

同一 session 冲突使用现有 CAS/原子事务处理。SSE 断开不创建新 run；重连按事件序号取回遗漏。取消明确设状态并在下一安全边界停止。已完成的共享结果为不可变快照。

多个查询合并时按 NCT ID 去重；交集仅在父集合覆盖充分时计算。相同 NCT 的不同版本不能按最后写入随意覆盖，需明确版本选择或冲突状态。外部网络请求期间不长期持有 SQLite 写事务。

## 4. Context、memory 与 prompt

### 4.1 ContextBuilder

每次模型决策包含：

1. 原始目标与不可丢的显式约束。
2. 当前确认的实体、范围、时间口径及 TaskSpec。
3. 最近相关工具观察、失败点和待完成子目标。
4. 必要的数据/证据句柄和来源摘要。
5. 按需加载的工具说明与领域 skill。
6. 有来源且适用的经验候选（默认产品策略与研究策略分开）。

实现实际 context packing：优先保留约束和当前决策必需的观察，历史工具全文保存在 trace，通过句柄可再读取。超长时可生成有字段来源的摘要，但摘要不能覆盖约束事实。不能把“长度限制更小”叫作上下文优化。

Prompt 拆成稳定行为约束、字段/统计目录、工具规则、当前任务四层，并记录版本。默认不将检索到的论文/试验文本作为执行指令。

### 4.2 Memory

- 会话记忆：确认条件、已分析范围、历史结果、澄清与分支。
- 运行记忆：当前计划、观察、尝试和预算；由 checkpoint/journal 支撑。
- 研究经验：具体失败模式、组件、行为证据和适用范围；与用户会话事实分开。

用户下一轮换疾病时，不把上一疾病条件或实验经验中的具体实体当成新任务事实。

## 5. 实体、查询范围与来源完整性

### 5.1 Entity resolution

保留 raw_name、canonical_id/name、aliases、source、source_version、match_kind、resolution_status。

先实现有来源的小型词表和 CTgov 记录中的 otherNames 候选，支持 exact/alias/ambiguous/unresolved；医学实体等价需要对应证据。大小写和标点归一不能代替药物身份判断；不能把成分、剂型、组合干预无条件合并。

品牌名与通用名属于同一可确认实体时，比较界面提示同实体，而不是生成两份看似不同的 cohorts。模糊检索只用于候选生成；BM25/dense/RRF 返回的 top-k 不得当作总体统计数据。

验收包含已知别名、未知名、拼写近似、同药比较、组合制剂、重名和用户否认候选后继续澄清。

### 5.2 宽范围查询

支持两个明确模式：

- full_evidence：完整取得声明范围的试验后统计，所有结果项可追溯全部贡献记录；作为默认严格模式。
- aggregate_overview：只在可正确表达的有限分桶上请求官方聚合计数，明确记录完整计数和有限引用证据。该模式需要请求/UI 的明确选项或既有偏好，不能静默替代严格模式。

独立报告：

- statistics_basis / count_exactness；
- population_coverage / fetched_count / matched_count；
- evidence_coverage；
- scope、来源查询和更新时间；
- presentation_clipping；
- 类别互斥性、多值 membership 与缺失类别处理。

严格模式可按合法、不重叠分区获取；分区 union 必须等于原始范围，不能漏掉缺日期记录或因分区改变搜索语义。不同获取时间出现来源更新时要检查并重试或声明无法形成一致版本。

overview 的每个桶保存原始 count 响应、查询、时间与状态。任何桶计数失败必须标记该桶失败或整体不完整，不能静默保留样本数量。

从样本中发现几个 sponsor/country，再补这些类别的 count，不能证明它们是全局 top-N；要么完整枚举候选，要么返回明确的 sampled-candidate ranking，不称全局排行。

组合 phase、多个 country 等可能重叠，不能把桶之和当去重总体。需要完整记录的网络、共现、持续时间和细粒度散点，不允许用 aggregate-only 计数伪造。

## 6. 图表、日期、网络与解释

### 6.1 日期合同

增加明确 date_range.field，区分 start、primary_completion、completion、first_posted；图表轴 time_field 单独指定。用户要求按完成日期筛选时，不能沿用 start_year。

保留日期原值、精度和 ACTUAL/ESTIMATED 类型。年级日期不能编造某月某日；月统计对年级数据按声明策略排除并报告。持续时间需要两个兼容日期，负值、缺失及不精确日期按合同处理。

### 6.2 散点与地图

散点至少支持 enrollment 与 trial_duration、start/completion year 的合法组合，每个点可追到试验字段和计算规则；显示有效点、排除点、缺失原因以及实际/预计口径。

网络保留 sponsor-drug、study-level drug co-occurrence、sponsor-shared-drug。共现不等于同 arm 联用，共享药物不等于合作。边的数值含义、支持路径和记录数量单独展示。

地理可视化支持国家条形和适用的 choropleth。使用来源明确的地理资产，无法映射的国家值进入可见的未映射列表，不能静默丢弃记录。

### 6.3 Chart advisories 与替代图

建议规则包括单一类别、只有一个时间点、90% 以上集中于单类、有效散点过少、缺失率高、网络太密/太稀疏、比较组为空。阈值和版本可见，提示不修改数据。

替代图在同一 result_id 与同一口径下切换渲染；如果改变分母、分组、聚合或取样，则发起新分析并建立 lineage。地图与条形可共享国家计数，散点不能仅换 renderer 变成含新统计意义的 histogram。

解释面板说明解析出的条件、数据范围、指标和关系定义、排除情况、图表选择和限制。涉及数字的解释绑定 result/metric ID；涉及文献的句子绑定 literature citation。不能只因存在一个链接就称内容已经被支持。

## 7. React/ECharts 与 API

### 7.1 界面

- 左侧会话与历史、中央聊天/图表、右侧解释/证据抽屉；具体布局可简化但功能齐全。
- 问题输入、结构化筛选、scope 模式、实体候选与具体澄清回答。
- 实际工具进度：如“确认名称”“获取记录”“发现缺失字段”“生成网络”，带简短公开理由。
- 多图结果包、图表选择、缩放/筛选、点选柱/点/节点/边查看准确 evidence_set。
- 引用分页、原始字段/值、NCT/PMID 跳转、完整引用导出。
- 空结果、限制、失败、恢复、取消均有明确状态。
- JSON/CSV 和结果图导出；CSV 导出与屏幕统计口径一致。
- 响应式、键盘可操作、可读字号；信息密集部分不依赖 hover 才能阅读。

### 7.2 建议 API 合同

可保留旧 /analyze，并新增版本化异步入口：

- POST /v2/runs：创建 run，返回 run_id。
- GET /v2/runs/{id}：状态与结果包。
- GET /v2/runs/{id}/events：SSE，支持事件 ID 重放。
- POST /v2/runs/{id}/resume：提交澄清答案或恢复所需信息。
- POST /v2/runs/{id}/cancel：请求取消。
- GET /v2/results/{id}/evidence：分页证据。
- POST /v2/results/{id}/share：创建只读分享快照。
- GET /s/{token}：只读结果及允许暴露的证据。
- GET /capabilities：工具、图类型、来源和范围能力。

API 名称按现有约定调整。不要为了 SSE 重写所有现有服务。可以先使用单实例执行调度和持久 run 状态，明确断线、重启的恢复语义；不要求现在引入分布式 worker。

v2 响应使用 artifacts[]，每项绑定 artifact_id/result_id、source_type、scope_id、metric、encoding、evidence_mode、warnings 和依赖产物。顶层区分目标完成、部分完成和失败，兼容旧单 visualization 的适配层。一个图完成但用户要求的另一个图失败，不能把整个任务标成完全成功。

分享 token 与 session/run 写权限分离。默认本地或自托管 URL，提供导出；公网部署另按用户部署意图处理。分享界面说明可访问内容，支持撤销；不包含密钥、内部prompt、私有会话或模型完整原始响应。

SSE 事件建议：run_started、action_selected、tool_started、tool_finished、observation、clarification_required、artifact_ready、run_completed、run_failed、usage_updated。事件有 sequence/run_id/timestamp，客户端按 ID 去重；final 响应仍是事实来源。

## 8. PubMed 与 MCP

PubMed：通过官方 NCBI E-utilities 做 search 与 metadata/abstract fetch，缓存原始记录；配置限流、重试、tool/email 和可选 API key。获取摘要与未获取全文区分，不能声称读过不存在的全文。论文撤稿/更正信息在来源提供时保留。

literature citation 与 trial contribution 使用不同类型。试验到文献关联优先使用显式 NCT/PMID/reference 字段；仅文本相似的候选标成候选。研究数量继续来自 CTgov。PubMed 工具失败不应抹掉已经正确完成的图表，可返回分部分状态并说明未完成的子任务。

MCP：复用共享工具与 artifact/evidence 查询服务，提供 stdio 与本地 Streamable HTTP 配置、实际 tools/list/tools/call 验证及使用示例。使用当前官方 SDK 和协议版本；不得只放一个 server 文件就算完成。tool annotations/输入输出 schema/错误/取消/分页及大结果读取一致；MCP 不绕过预算、数据范围或访问约束。

## 9. 新评测先于大规模实验

先实现 replay environment，固定 CTgov/PubMed 响应、数据快照、工具实现版本和模型配置。评分器独立于规划与执行组件。

若工具请求没有冻结数据覆盖，返回 replay_coverage_missing，不能伪装成 no_data。每个候选有独立会话、运行和派生结果，原始快照只读共享。episode ID 使用不透露答案的标识。建议 evaluation/contracts.py、replay.py、scorers.py、splits.py 与独立 decision_v2/harness_v2 入口；具体目录可按仓库组织调整。

建议起步约 60–100 个场景，含单轮和多轮 episodes，按场景家族/实体/原始问题拆 development、calibration、heldout；规模是实施起点，不是统计代表性承诺。初期先用少量 smoke episodes 打通，完整交付仍需覆盖能力矩阵。旧 50 轮保持历史/回归，不能反复用作新方法的独立测试。

必含任务：

- 直接统计与多组比较。
- 别名、同药比较与真正歧义。
- 查询空结果/字段缺失后的合理动作。
- 排名后派生 cohort、关系查询后时间趋势。
- 明确开始/完成日期与月粒度。
- 有效澄清、条件清除、话题切换、历史分支。
- 重复查询无进展、超预算、来源失败。
- 文献解释加试验图表的组合任务。
- SSE 断开重连、重启恢复和只读分享。

指标包括完整任务成功、统计数值与贡献集合、语义条件正确、澄清必要性/可回答性/回答后完成率、无效调用、延迟、费用、未知用量。不能只看 query_type 或 plan JSON 合法。

确定性 oracle 验证数字与来源；解释质量需要证据对齐检查及小样本人审。真实错误与人为合成错误分别报告。多轮 runtime 只能看到当前及过去用户话语，不可看到未来答复。

## 10. 决策实验第二阶段

历史观察：归档强 baseline 50/50，Jev raw action 46/50，固定 gate 最终25/50；23个正确计划被复合 plan-preservation 门槛额外阻断。离线取消该 veto 为48/50，仅作为诊断，不是新独立测试结果。

### 10.1 判断与动作分离

先保持强 planner，检验 gate 是否有辨别力。判断聚焦实体变化、遗漏条件、AND/OR/比较变化、时间口径变化、错误历史继承等；固定代码已经能检查的内容由代码检查。

动作必须区分 execute、repair_plan、clarify_user、unsupported。清晰请求被模型写错，最多进行有界重新规划；用户确实缺必要信息，提出可被系统消费的具体问题。

同一 request 的判断只带当前操作有效字段、明确的历史约束及必要原文。删除无关默认字段是否有收益，需要消融证明，不能先写成结论。

比较：

1. 原复合判断及原 context。
2. 拆分判断及原 context。
3. 拆分判断及精简 context。
4. 相同输入的通用生成模型 judge。
5. 无 gate 的固定校验基线。

数据含正确计划、自然规划错误、schema合法且固定校验未挡住的标记合成语义错误、真实歧义。按原始问题/对话分组切分，避免变体泄漏。

在 calibration 上选择阈值与组合策略，然后冻结。不要假定各判断概率独立，不机械相乘。报告错误检出、误放、误拦、修复成功、无谓澄清与额外成本。

### 10.2 成本级联

若判断有辨别力，实现便宜 planner → 固定校验 → gate → 必要时强模型重规划。强模型始终作为完整质量和费用对照；同时比较便宜 planner+固定校验、便宜 planner+通用 judge。

所有路径计入 planner、judge、重规划、再次检查、失败及未知费用，报告质量—成本—时延关系。没有收益时保留负结果；不能以大量拒答降低错误率而宣称提升。

## 11. Harness 第二阶段

历史 pilot 只执行 planning-only stub，修改主要是追加 prompt；context cap 从未被触发。有效 memory 组 11 条经验文本完全相同，22 个编辑提案20个为空，未采用组件。保留这些结果，不能覆盖成第二阶段成功。

### 11.1 环境与有效编辑空间

H0 与候选在相同冻结数据上实际执行工具、Neo4j、聚合和证据链。编辑器可看公开工具输出、固定运行检查、错误、产物摘要及费用时延；隐藏答案只在评分阶段使用。

开放有限的版本化组件：

- 分析 skill / tool-use procedure，例如先确定范围、先检查字段再选分析。
- 实际 context selection/packing policy，影响选入哪些相关观察和技能。
- 必要的有界执行策略，如允许的修复/检查顺序，在固定预算和工具权限内运行。

不可修改统计内核、证据生成、固定验证器、隐藏 oracle、模型或预算。组件不保存当前任务答案、药名、特定测试 ID 等用于记忆答案的补丁。

每个任务从共同 H0 开始，通过 general/domain 提案、实际 probe、composer、组合后执行完成 PPC。更改被采用需要可观察对比依据；“输出变了”不等于改善。

### 11.2 经验

保存适用条件、观察到的问题、候选改动、行为差异、限制、轨迹引用及可复用组件。无变化写入实验日志，不生成多份相同可检索经验；具体负经验可以去重后保存。

经验检索有适用性过滤与阈值；候选返回不意味着组件被采用。记录 retrieved/applied/adopted 和后续效果。允许最终答案相同但工具调用减少的改进，但需确认质量/证据检查没有回归。

### 11.3 对照

- 强 H0。
- 同预算重试/多采样。
- PPC 无经验。
- PPC 有经验。
- 人工编写固定 skill 对照（实现成本可控时加入，用于区分固定工程改进与自动改进）。

多个任务顺序，每个独立 stream 从空经验库开始。测试时若允许在线学习，预先声明协议，不能跨 stream 泄漏经验。多轮任务逐 turn 执行，隐藏未来用户输入。

先少量诊断任务定位失败，扩大到冻结测试前确保确有可观察改进空间。基线天花板高时考察成本、澄清负担、工具效率，或扩大自然任务覆盖；不故意削弱 baseline。

## 12. 实施顺序与验收门槛

| 阶段 | 内容 | 交付 |
| --- | --- | --- |
| P0 | 对齐最新修复、冻结基线、定义 v2 contracts 与 parity matrix | 基线回归、版本化 schemas、能力矩阵 |
| P1 | 工具注册表、状态/journal、LangGraph 观察闭环、SSE、replay | 直接问题和依赖前一步结果的任务均可端到端执行 |
| P2 | 实体、宽范围、日期、散点、图表提示、多图组合 | 领域合同和独立数字/证据验收 |
| P3 | React/ECharts、证据交互、替代图、历史/分享/导出 | 实际浏览器完成示例、断线重连与引用核对 |
| P4 | PubMed 与 MCP 接到相同工具层 | 组合任务、外部 MCP 客户端调用及来源隔离验证 |
| P5 | 扩展挑战集、决策与 Harness 第二阶段 | 独立实验版本、完整轨迹和质量/成本对照 |
| P6 | 集成回归、演示与文档 | 能力矩阵逐项证据链接、可运行交付包 |

前端可在 v2 contract 冻结后并行开发，mock 仅用于开发；最终演示必须使用真实后端。PubMed/MCP 不因阶段靠后而自动变成 future work。

每阶段提交可复现代码、相关测试和实际输出。不要只更新 README 宣称有能力；不能把多节点数量、依赖名称、空 adapter 或 mock UI 当作验收。

模型/API 实验沿用会话已授权的配置及预算；使用显式实验命令、每次费用预检和持久账本。普通 CI 不做付费调用。实际缺少配置时先完成所有独立离线工作，再准确报告阻塞点，不虚构运行结果。

## 13. 演示验收任务

1. 基础统计后，用户要求只看 Phase 3 并改日期轴；确认未丢其他条件。
2. 品牌名/通用名比较，系统展示实体解析依据并识别同实体。
3. 含糊药名，提出可回答的具体问题，用户回答后成功继续。
4. 完整范围内选共享药物最多的前三家 sponsor，再生成其年度趋势与关系证据。
5. 散点展示入组人数与持续时间，逐点可追源，缺失/日期精度被准确说明。
6. 宽范围精确计数与有限引用模式，界面不把样本引用标为完整贡献集合。
7. 先查询得到空结果，Agent 查证别名/条件后继续，未擅自删除约束。
8. 用户问药物背景并要求试验图表，PubMed 与 CTgov 各自提供相应证据。
9. 执行中刷新/断线，重连看到同一 run；重启后对账且不重复导入/统计。
10. 点击网络边查看全部支持记录；生成只读链接，另一个页面能查看相同快照。
11. MCP 客户端能调用查询、分析、文献和证据工具，结果与 HTTP 路径口径一致。
12. 无进展/预算耗尽时明确停止，已完成与未完成部分可区分。

## 14. 可直接交给 Codex 的任务说明

> 继续在当前仓库实现本规格，不再按“修完就冻结”或“实验只留接口”的旧范围收尾。用户已明确选择补齐前三个相似项目的主要能力，并继续决策模型和 Harness 实验。
>
> 先检查最新 HEAD、未提交工作及上一轮修复，保留现有统计、证据、数据集隔离和状态恢复。按 P0–P6 增量实现，完成整张 A01–A16/B01–B02 矩阵。不要省略 React/ECharts、真实工具循环、PubMed、MCP、散点/日期、宽查询、别名、图表提示、引用交互及分享。
>
> 把 Agent 改为能观察工具结果并选择下一步的有界 LangGraph 流程，区分对话多轮、单次任务工具多轮和 SSE。继续查询必须有可检查的原因和范围；系统计划错误由系统修复，用户信息缺失才澄清。
>
> 使用共享 typed tools 和数据句柄。API/CLI/MCP/实验共用统计与证据内核；模型不得生成最终数字或替代确定性 verifier。宽查询的精确聚合与完整记录证据分别建模。
>
> 实验先修环境和任务设计，再运行：决策拆分具体判断并支持 repair_plan；Harness 在冻结数据上真正执行工具，开放有效 skill/context policy，经验具体、去重、可追溯。保留历史负结果和强基线，不在已见测试上反复调出好看的分数。
>
> 每阶段给出实际运行与验收证据，最终更新 README、schemas、示例、部署与研究报告。新功能只有通过真实后端/工具路径才标完成，未完成项明确列出。遇到不影响其余工作的阻塞继续推进独立部分。

## 15. 官方与实现参考

- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)：固定流程与工具观察驱动的 Agent。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：checkpointer 与 store 的边界；具体 API 以锁定依赖版本为准。
- [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api)：新增字段、过滤、计数与分页必须核对官方定义。
- [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/) 与 [NLM 限流说明](https://support.nlm.nih.gov/kbArticle/?pn=KA-05510)：PubMed 工具集成依据。
- [MCP server concepts](https://modelcontextprotocol.io/docs/learn/server-concepts)：tools/resources 与共享服务契约。
- [TypeSafe Noul](https://docs.typesafe.ai/primitives/noul)：聚焦单项判断。
- [OpenRouter Jev cascade](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-verified-cascade)：级联实验思路，不继承其示例收益。
- [Harness²](https://github.com/google-research/harness2)：PPC 与跨任务经验；本项目需明确移植范围。

