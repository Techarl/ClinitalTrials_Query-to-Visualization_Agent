# Cheiron ClinicalTrials Agent：完整讨论整理与 Codex 实现交接

> 整理日期：2026-10-31（Asia/Shanghai）
>
> 用途：把本次对话中与 Cheiron take-home 相关的要求、讨论、调研、修正和最终方案汇总到一个文件，交给 Codex 继续工作。
>
> 本文件是按主题去重后的完整交接整理，不是逐字聊天导出。历史方案与最终决定已分开；所有设计均不代表已经实现。后续执行以“当前决定”为准。

## 0. Codex 首先阅读：当前任务和已确定的边界

用户当前希望先完成基础服务，两个附加实验只留好接口，不在第一阶段实现或运行。

### 0.1 当前决定

1. 项目是 **ClinicalTrials.gov 自然语言问题到结构化可视化的后端服务**。
2. ClinicalTrials.gov Data API 是权威数据源，必须继续使用。
3. 主模型使用用户自己的 OpenAI API，指定 **gpt-6.1-sol**。
4. 公司提供的“Allowed Models”是其 LLM API 可用模型列表；用户已经明确澄清：对方没有要求必须使用该 API，也没有把这份列表作为项目的模型限制。
5. 用户还有有余额的 OpenRouter 账户。未来决策模型实验优先走 API，大概率不本地部署。
6. 基础版采用 **GPT 语义规划＋LangGraph 编排＋Neo4j 关系查询＋固定代码计算与证据校验**。
7. 数据采用按查询范围获取的完整研究集合、不可变原始 JSON 快照及派生图；不先复制全站。
8. 基础版需要考虑召回/检索、上下文、记忆、prompt、工具调用、恢复、输出契约及可观测性，不能只写一个能运行示例的单 prompt。
9. 基础版支持多种图表、有意义的关系查询、必要澄清、多轮过滤和来源追溯。
10. 两个后续实验是：
    - 专用决策模型对执行/澄清等判断的影响；
    - 保留双层机制的受限 Harness² 移植。
11. 当前只预留模型/决策适配、harness 配置、context builder、轨迹和经验库接口。实验默认关闭，不能自动增加额外付费模型调用。
12. 首先让基础服务符合题目，随后再做实验。实验可以得到负结果，但必须有可信对照、成本记录及失败分析。

### 0.2 实际工作状态

目前完成的是：

- 阅读用户提供的作业截图并整理要求；
- 调研相似 GitHub 项目、官方 API、Harness² 和决策模型；
- 对 ClinicalTrials.gov 做过少量真实、只读 API 请求；
- 形成基础架构和实验设计。

目前没有证据表明已经完成以下工作：

- 创建本项目应用仓库；
- 实现服务或部署 Neo4j；
- 接入用户的 OpenAI / OpenRouter 密钥；
- 完成端到端应用测试；
- 跑过本项目的决策模型或 Harness² 实验。

不能把本文件中的示意 schema、设计目标、第三方 benchmark 或只读 API 验证写成项目已实现的成果。

### 0.3 接手后第一轮应做的事

1. 检查实际工作目录、已有代码和项目约定；本文件不是现有仓库目录的证明。
2. 将本文件中的基础范围转成可实施清单。
3. 先实现数据契约、数据获取、图模型、确定性分析和证据层。
4. 用手写 AnalysisPlan 跑通固定数据到可视化 JSON，再接自然语言 planner。
5. 接上 LangGraph、上下文、多轮会话和有限修复。
6. 完成真实示例、独立核验、README、打包。
7. 只保留实验接口，不擅自扩展到完整 RSI、模型训练或全量镜像。

---

## 1. 原始作业要求

### 1.1 标题与目标

**Take-Home Assignment: ClinicalTrials.gov Query-to-Visualization Agent (Backend)**

- 时间预期：约 24 小时。
- 允许工具：任何编程语言、库、AI 工具和互联网资源。
- 目标：构建后端，把临床研究自然语言问题转换成由 ClinicalTrials.gov API 数据支持的结构化可视化输出。
- 前端不是必需；结构化输出必须让前端能够可靠渲染。

系统需要：

1. 理解用户问题。
2. 检索相关 ClinicalTrials.gov 数据。
3. 判断适合的可视化。
4. 返回回答该问题的 visualization specification。

概述提到判断是否需要可视化；后续要求明确强调回答应通过可视化规格完成。我们的解释是：可回答的分析请求输出可视化；歧义、无数据、数据不完整或不支持时返回明确状态，不能为了交付图而编造数据。

### 1.2 数据源

官方文档：

https://clinicaltrials.gov/data-api/api

可使用所需的 endpoints 和 fields。这里的数据 API 与公司提供的 LLM API 是两个不同服务。

### 1.3 输入

必填：

- query：自然语言问题，字符串。

候选人可自定义可选字段，例如：

- drug_name；
- condition / disease；
- trial_phase；
- sponsor；
- country / location；
- start_year、end_year；
- 其他有价值字段。

必须文档化字段名称、类型、必填/可选和校验规则。

题目示例：

~~~json
{
  "query": "How has the number of trials for this drug changed over time?",
  "drug_name": "Pembrolizumab"
}
~~~

### 1.4 输出

必需的 visualization 内容：

- type：例如 bar_chart、time_series、network_graph；
- title：人类可读标题；
- encoding：字段到坐标、序列、节点/边等视觉通道的映射；
- data：渲染所需数据。

必需的 metadata：

- 单位、排序、时间粒度、分组等渲染信息；
- 可以包含假设、过滤条件、问题解释。

必须文档化 response schema，让前端不需要猜测。

### 1.5 可视化覆盖

鼓励多种类型：

- 柱状图 / 分组柱状图；
- 时间线 / 时间序列；
- 散点图；
- 直方图；
- 网络图，例如药物、申办方、疾病、研究者、地点。

设计目标是用同一套合理方法覆盖不同问题，避免每个例句写一个特殊分支。丰富且有意义的网络图可以加分，但“画了一个网络”本身不等于完成了数据建模。

### 1.6 Deep citations 加分项

每个可视化数据项，例如：

- 一根柱子；
- 一个年份桶；
- 一个节点；
- 一条边及其权重；

都能追溯到贡献该值的研究记录。

每条引用包含：

- nct_id；
- 精确 API 原文摘录，或者支持该数据项的具体字段及值。

三条示例引用只能证明三条记录，不能冒充一个统计值的全部贡献来源。可通过明确的完整证据集合及分页接口控制输出体积。

### 1.7 提交内容

提交 ZIP，至少包括：

1. 运行服务所需全部源代码。
2. README：
   - 安装、配置、启动；
   - 输入/输出 schema；
   - 关键设计与取舍；
   - 局限与后续改进；
   - AI 工具使用情况；
   - 如何验证正确性；
   - 哪些是主动设计，哪些是生成后调整。
3. 3—5 个示例问题及系统实际产生的 JSON 输出。
4. 可选：小 UI、部署 endpoint、演示视频。

### 1.8 评分

| 维度 | 权重 |
| --- | ---: |
| System Design | 35% |
| AI / Agent Design | 20% |
| Code Quality | 20% |
| Query and Visualization Coverage | 15% |
| Input / Output Design | 10% |

额外考虑：deep citations / source traceability。

### 1.9 题目附录中的查询例子

- 某药物自 2015 年以来每年的研究数量。
- 某疾病每年启动的研究数量。
- 某疾病研究在阶段上的分布。
- 最常见的干预类型。
- Drug A 与 Drug B 的阶段分布比较。
- 两种疾病的申办方类别比较。
- 某疾病正在招募的研究涉及哪些国家。
- sponsor ↔ drug 网络。
- 药物在研究中的共现网络。

注意：sponsor–drug 本来就在题目例子里，不能当作独创功能或推断其他申请者都没做过。

---

## 2. 用户意图与讨论如何演进

| 阶段 | 用户提出的问题/偏好 | 最终保留的结论 |
| --- | --- | --- |
| 最初理解作业 | 先解释题目，再提出方案，之后交给 Codex | 先形成可信基础设计 |
| 开源借鉴 | GitHub 是否有人做过相同或近似任务 | 有高度相似项目；可借鉴许可允许的代码与设计，披露来源 |
| 图数据库设想 | 实习同事用 Neo4j 连接 study、drug、负责人，再由 agent 意图识别、写查询、LangGraph 编排 | Neo4j 可用于本任务，但关系查询和证据路径必须真实发挥作用 |
| 概念澄清 | sponsor、柱子、API 是什么 | sponsor 为申办方；柱子是柱状图数据项；API 返回记录，不是直接回答问题 |
| 是否“更卷” | 图数据库能否比一般方案更有深度、引起公司注意 | 组件数量不保证加分；正确口径、追溯、可验证取舍更有价值 |
| 模型选择 | 用自己的 OpenAI credit，选择 GPT-6.1 Sol | 已确定，不再反复追问是否必须用公司 API |
| 两个实验 | 决策模型、Harness²/RSI | 基础完成后再独立评测，允许负结果 |
| 数据存放困惑 | 是否把全站搬进 Neo4j，是否存不下 | 全量并非天然不可行；当前采用完整范围快照 |
| 微信文章补充 | 提供 Harness² 文章全文及 10 月模型汇总 | 精确保留 Harness² 双层机制；开放决策候选扩大 |
| 部署偏好更新 | 有 OpenRouter 余额，大概率不用本地模型 | 未来决策实验走 API |
| 当前范围收敛 | 先基础，两个实验只留接口；考虑检索、LangGraph、context、memory、prompt | 本文件基础架构为当前实施依据 |
| 本文件请求 | 整理全部讨论为 Markdown 给 Codex | 只整理交接，不在保存文档过程中启动应用实现或实验 |

### 2.1 公司提供的 LLM API 模型列表：历史背景

用户最初给出的列表：

- gpt-4o-mini
- gpt-4o-2024-08-06
- gpt-4.1-nano
- gpt-4.1-mini
- gpt-4.1
- gpt-5-mini
- gpt-5
- gpt-5.1
- gpt-5.2
- gpt-5.4-nano
- gpt-5.4-mini
- gpt-5.4

后续用户明确说明：这是公司所给 endpoint 支持的型号列表，不是对所有实现的强制模型白名单。当前选择是用户自己的 gpt-6.1-sol。

### 2.2 被后续决定替代的想法

- “必须使用公司 API / 只能使用 GPT-5.4”：用户已澄清，不采用。
- “先本地部署 Laya、joey-1 或 d1-3B”：仅为早期候选讨论；当前优先 API。
- “基础阶段就做两个完整实验”：已收缩为只留接口。
- “把离线 prompt 优化直接叫 Harness²”：不准确。以后若研究 Harness²，保留当前任务 PPC 和跨任务经验库。
- “Neo4j 存不下全站”：没有依据；不全量的主要理由是工程收益和时间范围。
- “所有查询都需要向量召回”：不采用，完整统计和相关性检索有不同职责。
- “强模型和长上下文能保证提升”：没有本任务实测依据。

---

## 3. 领域概念与数据流

### 3.1 Sponsor、Trial、Intervention

- Sponsor：申办方，发起并对研究承担管理/控制责任的组织或个人，可为药企、医院、大学等；不能只理解成出资方。
- Collaborator：提供支持的合作方，与 lead sponsor 是不同字段。
- Trial / Study：本项目分析的一项研究记录，以 NCT ID 标识。
- Intervention：干预措施，范围比口语“药物”更广；药物相关干预可能标为 DRUG 或 BIOLOGICAL。
- “柱子”：柱状图中的一个数据项，柱高可能是某组的不同研究数量。

### 3.2 数据如何进入我们的系统

ClinicalTrials.gov 保存登记记录。API 按条件返回 JSON，一次可以返回一批研究。

我们的程序：

1. 搜索符合条件的记录；
2. 完整获取本次需要的研究集合；
3. 保存原始 JSON 快照；
4. 把字段变为 Neo4j 节点、关系和属性；
5. 使用 Cypher 查询自己的 Neo4j；
6. 将计算结果装配为可视化 JSON。

Neo4j 不会自动把 Cypher 发送到 ClinicalTrials.gov 官方数据库。它是应用维护的派生数据副本。

这里的 cohort 指用于本次分析的“研究集合”，不是患者级队列。

### 3.3 为什么不先全量镜像

全量复制技术上可能可行。空间取决于原始文本、关系展开、索引、向量、历史版本及运行配置，不能只用记录条数推算 Neo4j 显存或内存。

当前选择：

- 请求时获取完整匹配集合并缓存；
- 为演示和评测预先固定几个完整集合；
- 暂不承担全站清洗、同步和增量维护。

### 3.4 前期只读验证记录

以下是讨论时的点时观察，不是永久常量，也不是项目性能测试：

- 2026-10-31 请求 /api/v2/stats/size：totalStudies 为 609551，averageSizeBytes 为 17315。
- 两者相乘约 10.55 GB，仅是原始记录体量粗估，不等于 Neo4j 磁盘/内存占用。
- /api/v2/version 返回 apiVersion 2.0.5、dataTimestamp 2026-10-30T09:00:04。
- 查询 pembrolizumab + melanoma，pageSize=1、countTotal=true，当时 totalCount 为 400。
- 示例记录 NCT02083484：leadSponsor 为 Merck Sharp & Dohme LLC，condition 为 Melanoma，intervention 为 Pembrolizumab，类型为 BIOLOGICAL。
- 这里只取过少量示例，没有下载并验证全部 400 条。
- 曾对官方全部 JSON 下载端点做 HEAD，得到 ZIP 响应，没有下载全站数据。

正式实现和真实示例应重新查询，并保存实际快照、时间与版本。

---

## 4. 基础系统总架构

### 4.1 分工

| 层 | 职责 |
| --- | --- |
| API | 接收请求、校验输入、返回状态与可视化 |
| 语义规划 | 自然语言转结构化 AnalysisPlan |
| 编排 | LangGraph 管理状态、分支、澄清、恢复和预算 |
| 实体解析 | 名称、别名、中文/英文候选及歧义处理 |
| 来源检索 | ClinicalTrials.gov 完整范围获取 |
| 数据 | 不可变原始 JSON＋Neo4j 派生图 |
| 分析 | 参数化 Cypher、确定性聚合和分桶 |
| Context | 按阶段构造必要上下文 |
| Memory | 保存可追溯的会话分析状态 |
| 输出 | 图表规格、数据、元信息、证据 |
| 观测 | 执行轨迹、版本、错误、成本和时间 |
| 实验扩展 | 可替换 policy/harness 等接口，默认关闭 |

这些是模块边界，不是要求部署十几个独立服务。

### 4.2 建议技术栈

- Python；
- FastAPI；
- Pydantic；
- LangGraph；
- Neo4j 官方 Python driver；
- SQLite：本地交付的检查点、会话、运行元信息；
- 原始 JSON 文件及持久化卷；
- OpenAI Responses API：主模型；
- OpenRouter Decisions API：未来实验适配；
- Docker Compose：API＋Neo4j。

正式实现时锁定可工作的依赖版本。不要把讨论中的“current docs”直接当作安装版本。

### 4.3 当前不需要增加的设施

- 独立向量数据库；
- Kafka、Celery、RabbitMQ、Redis；
- 多租户企业平台；
- 常驻多 agent 团队；
- 任意生成 Python/Cypher 的开放执行环境；
- 自动模型训练；
- 全站镜像；
- 自我改写生产代码的后台进程。

后续有实际需求再增加，不要为预留实验搭一套研究平台。

---

## 5. 请求、计划与会话契约

### 5.1 请求示意

以下为设计示意，字段最终由实际 Pydantic schema 定义。

~~~json
{
  "query": "比较这两种药在黑色素瘤研究中的阶段分布",
  "filters": {
    "condition": "melanoma",
    "interventions": ["pembrolizumab", "nivolumab"]
  },
  "session_id": "optional-session-id",
  "parent_run_id": "optional-previous-run-id",
  "language": "zh"
}
~~~

只有 query 必填。

必须明确 interventions 在当前请求中的含义：比较集合、任一命中或同时包含，不能凭数组格式偷偷决定 AND/OR。通过 AnalysisPlan 的 cohorts 和操作定义表达。

### 5.2 条件合并规则

- 当前明确条件优先于历史条件。
- 当前结构化字段和自然语言矛盾，返回冲突项供澄清。
- 历史条件只在明确承接追问时继承。
- 新话题不自动继承上次疾病/药物。
- 相对日期转成绝对日期，记录解释所用时间。
- 保留条件来源：显式字段、当前文本、继承状态或系统默认假设。

### 5.3 AnalysisPlan

建议表达：

~~~text
intent
cohorts[]
  filters
  entity_bindings
operation
measure
group_by[]
time_field
granularity
relationship_definition
chart_hint
assumptions[]
unresolved[]
~~~

基本操作覆盖：

- 分类聚合；
- 时间分桶；
- 数值分桶；
- 多集合比较；
- 关系投影；
- 受约束的多跳关系查询。

同一套表达覆盖不同问法，不能用“如果问题包含某一句示例就走一个特殊函数”的方式扩展。

### 5.4 多轮 PlanPatch

“只看 Phase 3”应对上轮已确认计划形成明确修改：

- 保留疾病/药物和时间范围；
- 修改阶段；
- 检查已有快照是否覆盖；
- 重新执行并生成新 run。

不要仅靠把全部聊天发给模型来“回忆”。

---

## 6. LangGraph 执行流程

~~~mermaid
flowchart TD
    A["接收请求与恢复会话"] --> B["构造上下文并生成计划"]
    B --> C["校验计划与实体"]
    C -->|"需要补充"| D["保存澄清状态"]
    D -->|"用户补充"| B
    C -->|"可执行"| E["获取或复用完整数据快照"]
    E --> F["执行查询与聚合"]
    F --> G["检查结果与来源"]
    G -->|"可修复的计划错误"| H["有限计划修复"]
    H --> C
    G -->|"通过"| I["生成可视化并保存会话"]
~~~

### 6.1 哪些节点调用模型

主要调用：

- planner；
- 必要的实体消歧；
- 有明确错误反馈的 plan repair；
- 必要时生成澄清问题。

API 分页、图导入、聚合、完整性检查、证据校验和大部分图表装配是代码。

常见明确请求的设计目标是一次规划调用；不是承诺所有请求都只需一次。

### 6.2 RunState

保存句柄及结构化状态，避免巨大 JSON：

~~~text
run_id
session_id
parent_run_id
input
session_state_version
plan
plan_revision
entity_bindings
dataset_ids
result_ids
coverage_summary
validation_errors
retry_counters
pending_clarification
budget
harness_version
model_config
~~~

### 6.3 分支与失败类型

| 情况 | 行为 |
| --- | --- |
| 缺少关键语义 | needs_clarification |
| 模型输出不合 schema | 有上限的计划修复 |
| API 超时/限流 | 工具层有限重试 |
| 完整集合为空 | no_data |
| 抓取中断或超出范围预算 | incomplete，或要求缩小范围 |
| 当前能力不支持 | unsupported |
| 内部错误 | 明确失败及 run_id，不能回答“0 条研究” |

可回答请求返回可视化；其他状态不能伪造图表。

### 6.4 持久化与恢复

- 用持久化 checkpointer 保存状态。
- 本地交付采用 SQLite，不依赖只存在内存的 MemorySaver。
- 明确 session_id、run_id、LangGraph thread_id 的映射。
- 第一版同一会话串行，或通过 parent_run_id/state_version 检查冲突。
- 澄清中的计划单独保存。
- 成功后再更新 last_successful_plan。
- 中断恢复可能重新执行当前节点，写入与导入需要幂等。
- 不把 LangGraph 检查点误认为外部 API 调用具有 exactly-once 保证。

---

## 7. 检索、实体解析与查询完整性

### 7.1 三种检索

| 类型 | 目的 | 方案 |
| --- | --- | --- |
| 实体检索 | 确定用户指的是哪个实体 | 原文、明确别名、候选匹配 |
| 来源检索 | 确定分析所需全部研究 | 官方 API 条件查询与分页 |
| 图检索 | 关系、路径和分组分析 | 当前数据范围内的 Cypher |

### 7.2 实体解析

- 保留用户原词、查询词、候选及采用依据。
- 中文实体可提出英文候选，但不确定时核验或澄清。
- 本地图没有某实体不代表来源没有，必要时去官方 API 搜索。
- 优先精确匹配及来源记录中的明确别名。
- 模糊匹配用于提供候选，不直接改写事实。
- 机构名先做大小写、空白等保守规范化。
- 不把名称相似的公司自动合并为同一法人/申办方。
- 不让模型凭空扩展一批药物同义词后宣称召回完整。
- 记录采用的是官方搜索命中口径还是本地精确字段过滤；二者不能混为一谈。

### 7.3 暂不接入向量检索

完整统计不使用 top-k 向量结果作为总体。

未来可把向量＋关键词用于：

- 找类似研究；
- 候选实体匹配；
- 检索历史经验；
- 检索字段说明。

若以后做“检索结果中的统计”，必须在输出说明其范围，不能声称全量。

### 7.4 CT.gov API 注意点

前期读过官方 OpenAPI，正式实现仍应核对当前契约：

- /api/v2/studies 支持按 condition、intervention、sponsor/location 等条件查询。
- 可投影需要的 fields。
- 分页使用 nextPageToken → pageToken。
- 不能只因 studies 数组为空就停止；应依据是否还有 nextPageToken。
- 首次可用 countTotal=true 获取匹配数量。
- 讨论时文档规定默认每页 10、上限 1000。
- 后续页保持查询条件一致。
- 对去重后的实际数量、来源总数、翻页完成状态分别记录。
- 源数据在翻页期间变化可能造成快照漂移；记录官方数据版本和获取时间，必要时标注或重试。
- 针对 NCT ID 的查询行为可能不同于一般条件查询，正式实现验证过滤语义。
- query.spons 可能涉及 sponsor/collaborator 搜索，而本地 lead sponsor 分析是另一种明确字段语义。

### 7.5 范围与预算

预检查数量后决定是否继续完整获取。

- 不静默取前 N 条。
- 不靠相关性排序后的样本推算全量分布。
- 允许配置记录数、API 请求次数、总耗时和 token 上限。
- 图上 top-N 展示与来源截断使用不同字段。
- API 故障、数据未覆盖、真实零结果分别处理。

---

## 8. 数据快照与 Neo4j 模型

### 8.1 DatasetSnapshot

~~~text
dataset_id
normalized_source_query
requested_filters
source_data_timestamp
retrieved_at
expected_count
retrieved_unique_count
complete
truncated_reason
raw_snapshot_path
raw_snapshot_hash
schema_version
~~~

快照先处于构建状态；抓取、保存及导入完成并核验后，才成为可分析数据集。

### 8.2 图结构

~~~text
(DatasetSnapshot)-[:CONTAINS]->(TrialRecord)
(Sponsor)-[:LEADS]->(TrialRecord)
(TrialRecord)-[:HAS_INTERVENTION]->(Intervention)
(TrialRecord)-[:HAS_CONDITION]->(Condition)
(TrialRecord)-[:HAS_COUNTRY]->(Country)
~~~

按需求增加 Collaborator 或 Location，不为所有 API 字段都建节点。

TrialRecord 属性包括 NCT ID、研究类型、阶段、状态、日期、入组人数与原始记录引用等。

### 8.3 标识和版本

- TrialRecord 以 dataset_id + nct_id 标识某个快照中的研究版本。
- 一个研究可出现在多个快照，不能跨版本无声覆写。
- 所有统计及多跳查询必须限制所有支持研究属于当前 dataset。
- 可以共享规范化实体节点，但不能因为共享节点而把范围外研究带入统计。
- 原始 JSON 保存完整字段值，图保存分析所需属性与来源引用。
- 只有 MERGE 新边而不清除旧关系，会留下过期事实；当前优先不可变快照，避免复杂增量同步。

### 8.4 缓存语义

- 缓存 key 关联规范化来源查询、字段投影及版本策略。
- 相同条件可以复用有效完整快照。
- 收窄条件可从覆盖充分的快照过滤。
- 放宽条件不能假定已覆盖，需要重新取数或验证包含关系。
- 不把历史多次下载的并集当作本次查询集合。
- 实验和示例固定引用具体 snapshot，不能仅引用“latest”。

---

## 9. 分析、图表和来源校验

### 9.1 参数化执行

模型只输出合法计划；编译器负责：

- 映射允许的字段与操作；
- 构造参数化 Cypher；
- 注入 dataset 范围；
- 执行预算和结果规模限制。

基础版没有任意 Cypher 或 Python 生成执行入口。此边界不限制自然语言表达方式，允许的字段/操作仍可组合。

### 9.2 口径规则

1. 研究数量使用不同 NCT ID。
2. 多对多展开不能重复计数。
3. 多国家研究可以计入多个国家，分类总和未必等于研究总数。
4. 药物比较集合可能重叠。
5. Phase 1/2 等组合阶段使用明确、固定的分类策略，并在 metadata 说明。
6. 缺失阶段/日期/入组人数不等于零。
7. 预计与实际日期、预计与实际入组应保留类型信息。
8. “开始年份”与“首次登记年份”不能混用。
9. DRUG 不是所有药物相关干预的唯一类型。
10. lead sponsor 与 collaborator 不能混用。
11. 研究整体 recruiting 不证明每个地点都在 recruiting。
12. 研究内药物共现不证明同臂联合用药。
13. Sponsor–drug 关联不等于药物所有权或机构合作关系。
14. 图上省略低权重边不等于这些关系不存在。

### 9.3 第一版图表范围

优先：

- bar_chart；
- grouped_bar；
- time_series；
- histogram；
- network_graph。

地理排名可以用 bar_chart，不必为了地图额外处理地理编码。

散点图在题目允许范围内，但讨论中不是必须抢在上述能力前实现；有余力再补。

### 9.4 输出契约

~~~text
status
visualization
  type
  title
  encoding
  data
meta
  interpreted_query
  applied_filters
  metric_definition
  units
  sorting
  time_granularity
  dataset_ids
  completeness
  missing_data
  assumptions
provenance
run_id
session_id
~~~

网络图需要明确 nodes、edges、权重含义及 encoding。正式 schema 可使用类型区分的结构，不让前端猜数组含义。

无数据、澄清、未完成或不支持时，visualization 可为空，并用状态解释原因。

### 9.5 Deep citations

每个数据项关联完整 contributor 集合，包括：

- NCT ID；
- 官方研究链接；
- 原始 JSON 字段路径；
- 精确字段值；
- snapshot/version；
- 聚合或归组规则。

小集合可内联；大集合通过 evidence_set_id 和分页接口展开，并返回 contributor_count。

图中显示的引用预览与完整证据集合必须区分。导出的示例需要足够完整，不能只有无法恢复的运行时引用。

验证包括：

- 引用是否存在于原始记录；
- 字段值是否一致；
- 柱子或边的贡献研究数是否与数据一致；
- 图边起止节点是否存在；
- 排序、单位、分桶及类别顺序是否明确；
- 未完整获取的集合是否被错误标记为完整。

---

## 10. Context 工程

实现独立 ContextBuilder，不在各节点散落字符串拼接。

### 10.1 按阶段组装

规划上下文包含：

1. 稳定任务与能力边界；
2. 当前问题和显式条件；
3. 必要的会话状态；
4. 精简字段目录；
5. 与当前问题相关的领域说明；
6. 当前阶段工具 schema；
7. 有上限的错误与反馈摘要。

时间趋势问题加载日期语义；关系问题加载路径、实体角色和共现限制。

如果第一次调用还不知道具体意图，可先提供精简能力/字段目录；详细说明按工具请求或识别结果加载，不能为了“动态 context”强制每题增加多个无用调用。

### 10.2 工具返回句柄

~~~text
dataset_id / result_id
coverage: expected_count, fetched_count, complete
available_fields
missingness_summary
small_preview
evidence_handle
~~~

模型看到少量预览是为了理解结构，不用于自行计算总体数量。

原始 JSON、完整 contributor 集合、巨大查询结果保持在后端，按需查看。

### 10.3 压缩规则

优先保留：

- 用户明确条件；
- 实体选择；
- 数据范围；
- 当前计划；
- 未解决歧义；
- 当前错误。

可以压缩或移除：

- 重复工具结果；
- 已过时的错误；
- 大段旧聊天；
- 与当前分析无关的字段说明。

任何压缩都不能丢掉用户约束或把未确认猜测变成事实。

---

## 11. Memory 和 Prompt

### 11.1 四类状态

| 状态 | 存储内容 | 第一版 |
| --- | --- | --- |
| 领域数据 | 研究与实体关系、来源快照 | 实现 |
| 运行状态 | 计划、句柄、错误、重试 | 实现 |
| 会话记忆 | 上次成功分析、追问条件、待澄清事项 | 实现 |
| 改进经验 | 可复用 harness、经验条目和证据 | 留接口关闭 |

Neo4j 中的研究记录不是 agent 的“学习经验”。

### 11.2 会话内容

~~~text
session_id
last_successful_plan
active_dataset_ids
selected_entities
last_result_id
explicit_user_filters
display_preferences
pending_clarification
state_version
updated_at
~~~

成功后提交新的分析状态；半成品计划单独保存。不要自动跨会话保存个人偏好或跨用户共享状态。

### 11.3 Prompt 模板

| 模板 | 职责 |
| --- | --- |
| planner | 转换结构化计划、识别歧义 |
| entity_resolution | 多候选时辅助匹配 |
| plan_repair | 根据明确校验错误修复 |
| clarification | 必要时生成清楚的澄清问题 |

图表标题、坐标名、单位和常见说明优先从计划用模板生成，不默认加一次总结 LLM。

### 11.4 版本化

每次运行记录：

- prompt template version；
- 字段说明版本；
- 工具 schema 版本；
- harness version；
- 实际模型/provider。

原始记录自由文本作为数据，不升级成系统指令。需要记录结构化计划和简短依据，不要求输出隐藏的内部推理过程。

---

## 12. Provider、实验接口与默认行为

### 12.1 生成模型与决策模型分开

- GenerationClient 处理文本/结构化计划/工具调用。
- DecisionClient 处理 state + typed questions → typed decisions/probabilities。
- 不能假定专用决策模型支持普通聊天或让它自由解释。
- OpenAI 主模型按 Responses API 集成。
- OpenRouter 适配在实际接入时核对 model ID、端点、参数、供应商和费用记录。

截至本次讨论，OpenRouter 官方文档列出 Jev Decisions API 和 System One API，并说明可使用 OpenRouter key 计费。不再把“还需要独立 TypeSafe 账户”当作必然要求。

### 12.2 预留接口

~~~text
GenerationClient.generate(...) -> StructuredOutput

DecisionPolicy.evaluate(context) -> Decision

ContextBuilder.build(stage, state, harness) -> ContextPackage

HarnessProvider.load(version) -> HarnessConfig

ExperienceStore.retrieve(...) -> ExperienceEntries

TraceSink.record(event)
~~~

### 12.3 基础默认实现

- DecisionPolicy：使用基础 planner 的判断和固定校验；不额外调用一个模拟决策模型。
- HarnessProvider：只读取固定 baseline-v1。
- HarnessConfig：prompt、工具说明、context policy、允许的 retry policy、skills 引用。
- ExperienceStore：关闭，不在请求间自动学习。
- TraceSink：启用，记录实际操作和版本。

禁止让未来候选配置关闭数据范围、来源核验、输出契约或硬预算。

不要默默 fallback 到别的模型；如果支持 fallback，必须显式配置并记入结果，否则无法公平评测。

---

## 13. 可观测性、测试与交付顺序

### 13.1 运行轨迹

从第一版记录：

- 请求与会话版本；
- 模型、provider、代码、prompt、harness 版本；
- 计划及修订；
- API 查询、快照 ID、完整性；
- 工具参数与结果摘要；
- 错误和恢复；
- 最终输出与来源校验；
- token、耗时、调用数；
- provider 可返回的实际费用，估算费用则明确标注估算。

无需收集隐藏 chain-of-thought。也不能把 API key 写进日志或示例。

### 13.2 测试重点

- 分页 token、空页仍有 token、重复记录、限流、超时和中断；
- 完整集合、未完成集合、真实空集合；
- 多药、多疾病、多国家造成的重复计数；
- 组合阶段、缺失字段、预计/实际日期；
- 数据集范围及跨快照隔离；
- 收窄/放宽追问、条件清除、话题切换及冲突；
- 图边权及 contributor 一致性；
- 引用字段值与原始 JSON 一致；
- 输出 schema 和前端渲染所需信息；
- 计划修复上限、预算结束、错误状态。

### 13.3 独立正确性标准

- 数值使用原始记录和独立参考计算核验。
- 自然语言语义与预期计划人工审核。
- 不用同一份有 bug 的 Cypher 同时充当实现和标准答案。
- LLM judge 可辅助评估标题/说明，不作为数值真值。
- 固定快照做回归，少量 live API 做联调。
- 测试通过后不为了堆数量无限重复同类检查。

### 13.4 实施顺序

阶段 A：数据与计算基础。

- 请求/计划/输出 schema；
- 字段目录和统计口径；
- CT.gov client；
- 原始快照；
- Neo4j 导入与范围查询；
- 聚合、关系、来源证据；
- 手写 AnalysisPlan 端到端。

阶段 B：Agent。

- provider adapter；
- planner；
- ContextBuilder；
- LangGraph；
- 实体消歧、澄清、有限修复；
- 结构化会话记忆。

阶段 C：交付。

- 轨迹、回归和 live 联调；
- 3—5 个实际查询及完整 JSON；
- README、schema 文档、设计取舍；
- Docker Compose 和 ZIP；
- 实验接口存在且默认关闭。

### 13.5 可参考的代码模块划分

这是目录建议，不是已经创建的代码：

~~~text
app/
  api/
  schemas/
  agent/
    graph
    planner
    context
    memory
    prompts/
  data/
    ctgov_client
    snapshots
    entity_resolution
  graph/
    importer
    query_compiler
    repository
  analysis/
    aggregations
    networks
    validators
  visualization/
  evidence/
  providers/
  observability/
  extensions/
    decision_policy
    harness_provider
    experience_store
tests/
examples/
docs/
~~~

实现可合并过小模块，避免为目录结构拆出不必要抽象。


---

## 14. 后续实验一：决策模型（延期，仅背景与接口）

### 14.1 研究问题

专用决策模型能否改善“执行、澄清、不支持”的语义判断，并在准确率、覆盖率、延迟及成本之间取得有用取舍？

不能先假定更强，也不能假定加一次调用就能省钱。

### 14.2 建议输入与输出

输入：

- 用户原问题；
- 结构化计划；
- 可用字段和能力摘要；
- 必要的数据覆盖信息。

输出：

- execute；
- clarify；
- unsupported；
- 候选概率（如 provider 提供）；
- 模型版本、耗时、费用。

字段合法性、分页完整性、数据是否存在等可计算检查仍由代码执行。

本地还没下载不是 unsupported；应该触发取数。

### 14.3 对照

- B：基础 planner＋固定检查。
- B＋G：额外一次生成式 GPT 结构化判断。
- B＋D：专用决策模型 API。

这样区分“多检查一次”与“专用模型”的收益。

若另做节省成本路由，需要让决策真正跳过昂贵调用；应单独定义实验，不能与单纯增加可靠性门控混成一个结论。

### 14.4 指标

- 明确问题的最终正确率；
- 本该澄清却直接回答的误执行率；
- 明确问题被无谓澄清的比例；
- 回答覆盖率；
- 成本、总调用数、端到端 p50/p95；
- 有足够样本时评估 Brier、risk–coverage 等概率质量。

仅提高拒答率可能让已回答问题看起来更准，需要同时看覆盖率。

概率阈值在验证集确定，不能看测试结果后反复调阈值。

### 14.5 模型调研如何演变

早期重点考察：

- Jev：TypeSafe 托管决策模型；
- System One Adapter：用生成式 LLM 实现相同接口，便于公平比较；
- Laya：开放实现，但作者明确提示基础零样本表现和校准局限；
- Tev1：另一类决策微调实现，代码许可与权重许可不能混同。

用户提供 10 月汇总文章后，又核实：

- joey-1；
- d1-3B；
- d3-mini；
- Clef-Flash 等。

当前用户选择 API，因此这些本地候选只作背景。正式实验优先检查 OpenRouter 当前可调用的型号，不要求实现本地推理栈。

### 14.6 避免误解

- 不同“决策模型”不一定采用相同架构。
- typed output / schema 有效，不等于语义正确。
- 训练包含 Brier 校准项，不等于已在 clinical 领域校准。
- 高置信度仍可能选错。
- 各家公布的 Decision Index 版本、样本及流程不同，不可直接按宣传分数排优先级。
- 通用 GPT 输出相同 JSON，属于决策层对照，不等于使用了真正的 Jev。
- 以后确实接入哪个 API、哪个模型，就如实记录哪个，不能用“Jev 类”掩盖实际模型。

---

## 15. 后续实验二：受限 Harness²（延期，仅背景与接口）

### 15.1 用户想研究的机制

模型权重冻结，改进周围的运行框架，例如：

- prompt；
- context 组织；
- 工具说明；
- memory；
- skills；
- 脚本；
- 子任务组织；
- hooks / plugins。

论文名称：

**Harness²: Recursive Agent Harnessing for an Open World**

用户的关注点是：当前任务发现流程缺陷，自动修改；再积累“如何修改”的经验，帮助后续任务。

### 15.2 两层机制

第一层：当前任务 Propose–Probe–Compose。

1. 用共同基础 H₀ 进行参考执行。
2. 通用编辑器提出执行流程修改。
3. 领域编辑器提出专业要求相关修改。
4. 两个候选分别在同一任务上试跑。
5. Composer 比较配置差异、轨迹和产物，形成后继 harness。
6. 实际运行组合后的 harness。

第二层：跨任务积累改进经验。

- 保存采用的可执行 harness；
- 保存 EXPERIENCE.md，说明适用条件、观察到的变化和证据；
- 证据不足可以不新增经验；
- 下一个任务仍从共同 H₀ 开始，过去组件和经验按需检索；
- 不直接继承上一题整套高度定制配置。

模型参数、阶段指令和决策规则在该实现中保持固定；第二层不是无限重写改进算法。

### 15.3 单步成本

k=1 至少涉及：

- 参考执行；
- 通用候选执行；
- 领域候选执行；
- 组合后执行。

另有编辑器、Composer、经验整理的调用。不能把“一步”误报成只额外调用一次模型。

k>1 可以增加并行探索宽度或顺序改进深度。第一轮实验只计划有限 k=1，避免先实现全套递归扩展。

### 15.4 ClinicalTrials 中可以改变的内容

- planner 提示词；
- 字段说明的加载；
- 分析 skill；
- 工具顺序、重试和上下文压缩策略；
- 调用固定工具的辅助脚本；
- 在预算内触发检查的 hook。

保持固定：

- 原始研究快照；
- Neo4j 数据；
- 字段映射；
- 去重、聚合、图边权；
- 来源和完整性验证；
- 输出 schema；
- 隐藏评测答案与评分器；
- 硬性预算。

辅助脚本可以整理诊断或构造合法参数，不能绕过正式计算工具直接改统计值。

### 15.5 与原论文的环境区别

原论文强调开放任务缺少标准答案反馈。ClinicalTrials 有不少确定性检查，所以受限移植会使用正常工具反馈，但最终评测真值和分数必须对改进器隐藏。

成熟基础服务＋强主模型可能本来就表现很好，改进空间有限。这是实验问题，不能为了体现收益而故意削弱基础版本。

基础已知的分页/去重错误应直接修复，不作为 RSI 的“发现”。

### 15.6 必要对照

| 组 | 研究目的 |
| --- | --- |
| 固定 H₀，单次执行 | 基础表现 |
| 固定 H₀，多次执行并选择结果 | 额外计算本身的收益 |
| PPC，关闭跨任务库 | 当前任务框架修改的收益 |
| PPC，开启跨任务库 | 经验让下次修改更有效的收益 |

预算允许可加入：

- 固定框架只检索经验文本，区分普通经验 RAG 和组件修改；
- B＋D＋H，检验决策与 harness 的组合。

第一轮先把各项测清楚，不急着增加组合组。

### 15.7 公平性

- 固定执行模型、数据和正式工具。
- 可让执行器和编辑器都用同一个 gpt-6.1-sol，减少编辑模型差异。
- 对照设置相近总 token/费用上限，报告实际消耗。
- 不能只匹配执行器调用数而忽略编辑/选择成本。
- 离线优化费用和线上推理费用分开。
- 多候选选择也不能接触隐藏 oracle，否则会得到不公平结果。
- 正常运行时校验对所有组一致。

### 15.8 测试流评测

先用开发集确定实现、预算和评测规范，然后锁定测试任务流。

测试阶段：

- 可使用前面任务的无标签执行经验，这是方法的一部分；
- 不读取测试正确答案、评分或未来问题；
- 每条独立任务流从空经验库开始；
- 方法之间使用独立经验库；
- 尽量采用三种相同的任务顺序重复；
- 报告任务顺序对表现的影响；
- 保留新增经验与后续检索使用记录。

另一个可选迁移实验是：开发任务积累经验库，冻结后测试新疾病/新问题。这个结果单独报告，不和在线持续积累混算。

### 15.9 离线优化与 Harness² 的区别

早期建议参考 Meta-Harness/GEPA：看开发集轨迹，优化 prompt/config，再冻结测试。这是可行的离线优化。

用户补充文章后，最终结论修正为：

- 若目标是普通工程调优，可以做离线优化；
- 若目标是检验这篇 Harness²，则保留 PPC＋跨任务经验库；
- 简单重试、一次反思、一次 prompt 搜索，不应声称复现完整 Harness²；
- 即使保留双层，也应称“ClinicalTrials 场景受限移植”，而非完整八组件专业任务复现。

---

## 16. 共用评测原则与负结果价值

### 16.1 数据与题目

此前讨论过一个可行的 pilot 规模：

- 15 个开发问题；
- 15 个验证问题；
- 30 个测试问题。

这只是建议规模，不是硬性要求，也不足以轻易证明小幅改善有统计意义。

覆盖：

- 时间趋势；
- 阶段分布；
- 两集合比较；
- 地理；
- 关系网络；
- 多轮过滤；
- 空结果；
- 歧义；
- 数据不足；
- 超出能力范围。

按实体、cohort 和问题结构划分，避免同一道题简单换个词就跨集合。

### 16.2 真值

- 固定原始快照。
- 手动审核问题的预期解释。
- 数字及关系由独立参考程序核验。
- 隐藏答案不进入 planner、editor、Composer、selector 或经验库。
- 正式评测前固定指标和阈值。
- live API 演示与冻结数据评测分开。

### 16.3 共同指标

- cohort/NCT 集合正确性；
- 聚合数字、时间桶、图边正确性；
- 图类型与问题匹配；
- 引用覆盖及字段真实性；
- schema 有效性；
- 不完整数据标注；
- 澄清质量；
- 回答覆盖；
- 工具次数、token、费用及延迟。

“JSON 能解析”不是端到端正确。

### 16.4 如何呈现负结果

有价值的负结果应说明：

- 比较了什么；
- 模型/数据/预算是否一致；
- 实现是否确实接入该机制；
- 哪些任务改善、哪些退化；
- 成本变化；
- 失败集中在哪里；
- 是否建议部署。

例如可以观察到“增加门控减少模糊题误答，但增加明确题无谓澄清”，或者“经验库改善开发任务，留出任务无稳定提升”。这些只是说明结果类型的假设例子，不是已经测出的项目结论。

小样本未观察到提升，不证明该方法普遍无效。

---

## 17. 相似 GitHub 项目与借鉴方式

以下是对话中的调研线索。仓库状态、许可和实现应在实际复用前按提交版本重新检查。调研没有证明这些项目是 Cheiron 的正式提交，也没有证明通过评审。

### 17.1 yipjunkai/takehome-surgical

链接：

https://github.com/yipjunkai/takehome-surgical

曾用地址 ctgov-query-viz-agent 会重定向到该仓库。

调研时确认 MIT 许可，仓库已归档。与作业非常相似，采用结构化 QueryPlan、确定性执行、API 查询、聚合与可视化。

值得参考：

- planner 与计算分离；
- schema；
- 查询编译；
- 示例和验证组织。

需检查：

- 某些快速路径使用来源分桶计数和示例引用，不能当成完整 contributor 追溯；
- README 对正确性的强表述不等于语义计划不会错；
- 复用时保留许可及来源。

### 17.2 bibekyess/Query-to-Visualization

链接：

https://github.com/bibekyess/Query-to-Visualization

采用工具调用 agent，通过 dataset_id/result_id 共享服务端数据，覆盖聚合、比较、散点和网络等。

值得参考：

- 句柄式工具输出；
- 模型不直接写数值；
- 工具与最终规格装配分工。

讨论中未确认可自由复用的许可证。审阅时还观察到示例引用数量受限、网络语义和分页边界等需独立检查，不能照搬后当作可靠基线。

### 17.3 JoelKShaju/qtov

链接：

https://github.com/JoelKShaju/qtov

调研看到 Pydantic AI、FastAPI、React/ECharts、来源查看、缓存及评测相关设计。可参考交互与契约组织。

未确认自由复用许可，未验证部署质量或评审结果。

### 17.4 lohith-bhogadula/clinicaltrials_visualizer

链接：

https://github.com/lohith-bhogadula/clinicaltrials_visualizer

较小的起步实现。调研时看到获取范围和自然语言条件解析存在局限，例如仅使用首批数据的路径，不能作为全量统计正确性的依据。

未确认自由复用许可。

### 17.5 借鉴原则

- 公开可读不等于许可允许复制。
- 依照具体 LICENSE 使用代码。
- README 说明复用了哪些部分、主动设计了什么。
- 不把他人仓库当作最终答案或评测真值。
- 代码生成与开源复用可以使用，但提交者需要理解并验证行为。
- 用户希望展示工程判断，不是堆叠所有现成框架。

---

## 18. 方法与模型参考项目

### 18.1 Harness²

- 论文：https://arxiv.org/abs/2610.14224
- 正文：https://arxiv.org/html/2610.14224v1
- 仓库：https://github.com/google-research/harness2
- 项目页：https://harness2.github.io

调研时仓库标 Apache-2.0。方法重点是无 gold 的当前任务对照探测，以及跨任务组件/经验库。现成执行器与 benchmark 需要适配到本项目，不能理解成直接装一个依赖就完成实验。

### 18.2 Meta-Harness

- 仓库：https://github.com/stanford-iris-lab/meta-harness
- 论文：https://arxiv.org/abs/2603.28052

调研时 MIT。参考其固定模型外围的 harness 搜索与轨迹驱动优化方式。不是 Harness² 的同义词。

### 18.3 GEPA

- 仓库：https://github.com/gepa-ai/gepa
- 论文：https://arxiv.org/abs/2507.19457
- 优化接口说明：https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything/

调研时 MIT。可参考基于执行反馈优化 prompt/config/code 的方法；如果以后选用，应明确具体优化对象与评测预算。

### 18.4 RRSI

- 论文：https://arxiv.org/abs/2609.24972
- 仓库：https://github.com/google-research/rrsi
- 项目页：https://regularized-rsi.com/

重点参考防止 harness 记住开发题、限制候选修改、剔除特定 benchmark 补丁的思想。论文不同修订版本的数字可能不同，本项目没有复制其结果。

### 18.5 Jev 与生成模型对照

- 官方介绍：https://docs.typesafe.ai/introduction
- 官方概率说明：https://docs.typesafe.ai/confidence
- 官方发布介绍：https://typesafe.ai/blog/introducing-system-one-models-and-jev
- System One Adapter：https://github.com/typesafe-ai/system-one-adapter-python
- OpenRouter Jev：https://openrouter.ai/docs/guides/community/jev

System One Adapter 是生成模型比较接口，不是 Jev 权重。Jev 的固定输出类型不能保证判断永远正确。

### 18.6 本地候选的历史调研

当前不要求部署，但保留讨论结论：

| 候选 | 链接 | 当时核实/注意事项 |
| --- | --- | --- |
| joey-1 | https://huggingface.co/baselabs/joey-1 | 官方标称 2B，MIT；专用运行环境；训练含 Brier 项；不代表 clinical 已校准 |
| d1-3B | https://huggingface.co/LiquidAI/d1-3B | 约 3.12B，文本和图像，专用 system_one 接口；LFM 自定义许可 |
| d3-mini | https://huggingface.co/vllm-sr/d3-mini | 小型开放决策候选；运行方式和联合问题实现需要按模型卡核验 |
| Laya | https://github.com/NandhaKishorM/laya | 调研时 Apache-2.0；作者承认基础零样本与过度自信局限 |
| Tev1 | https://github.com/togethercomputer/tev1 | 生成式决策微调路径；代码许可不自动覆盖权重 |
| Tev1 模型卡 | https://huggingface.co/togethercomputer/Tev1-4B-experimental | 调研时有权重许可仍在最终确定的说明 |
| Clef-Flash | https://huggingface.co/Cloudflare/clef-flash | 比小型候选更重，专用服务栈需核验 |
| Perplexity Decider | https://huggingface.co/perplexity-ai/pplx-decider-v1.2 | 模型卡可读不等于账户一定有权重访问权限 |
| Xing Intent | https://huggingface.co/XingChen-AGI/Xing4.0-29B-Intent | 意图和参数抽取路线，不应简单当成 Jev 式零生成 token 输出 |

用户后来选择 API，这些条目不是基础项目依赖清单。

---

## 19. 两篇微信文章的内容整理

用户先提供链接，网页获取未成功；后来在对话中粘贴了两篇全文。本节依据用户提供的文字整理，图片和缺失公式不补写为原文。

### 19.1 第一篇：Google Harness²——让 Agent 自己改流程，还能记住下次该怎么改

原链接：

https://mp.weixin.qq.com/s/JQ-kJ96vQHKsHcf2c9JxIw

#### 背景

Agent 在复杂任务中可能会：

- 合并数据时匹配错误字段；
- 工具调用或流程顺序不合理；
- 生成文件后没有重新打开验证；
- 上下文被无关内容占据；
- 把一次任务的特殊假设带到下一题。

开发者通常手动补规则、脚本、技能。文章介绍的研究试图让系统自己积累这种工程经验。

#### 相关工作脉络

文章提到：

- Self-Refine：改当前输出；
- Reflexion：保留反思；
- ReasoningBank：从轨迹提炼策略并检索；
- DSPy / GEPA：提示词及相关优化；
- ADAS / AFlow：设计或工作流搜索；
- Meta-Harness / AHE：可执行 harness 搜索；
- Harness Scaling / TTHE：测试时或任务流中的框架演化；
- STOP / Darwin Gödel Machine / Hyperagents 等：改进程序或元层演化。

这些只是文章的背景脉络，不是本项目需要全部接入的工具。

#### 机制

- 固定模型权重；
- 八类可修改框架组件；
- 通用与领域两个编辑方向；
- 同题参考执行与候选试跑；
- 根据差异和行为证据组合；
- 并行或顺序增加预算；
- 保存可执行组件和 EXPERIENCE；
- 每个新任务重置到 H₀，按需取用过去经验。

第二层当前通过库更新实现，阶段指令和决策规则保持固定。

#### 文章报告的实验背景

覆盖：

- WorkBuddy-Bench：代码、网页、办公；
- JobBench：职业任务；
- LAB：法律任务。

文章描述主实验使用 Gemini 3.5 Flash、GLM-5，搭配 OpenCode/Codex，并由另一模型承担编辑选择；也提到同模型兼任角色的补充实验。

文章称任务流打乱顺序并重复，经验库从空开始；运行时不提供标准答案、评分细则或成绩，最终由 benchmark evaluator 评价。

#### 文章给出的单步结果示例

下面仅转录用户所贴文章中的“原始框架 → 单步改进”数字，用于保留讨论背景，不是本项目结果，也不应作为本项目收益承诺。

| 执行器与初始框架 | WorkBuddy 平均分 | JobBench 分数 | LAB Partial |
| --- | --- | --- | --- |
| Gemini 3.5 Flash＋OpenCode | 72.4 → 77.5 | 31.5 → 36.0 | 79.9% → 81.9% |
| Gemini 3.5 Flash＋Codex | 74.5 → 77.5 | 33.9 → 35.3 | 71.6% → 75.6% |
| GLM-5＋OpenCode | 64.2 → 75.7 | 20.6 → 24.2 | 59.6% → 71.4% |
| GLM-5＋Codex | 64.4 → 75.3 | 23.6 → 24.8 | 58.1% → 65.9% |

原粘贴中部分数学变量、图表参数因排版丢失，本文不猜补这些符号。

#### 消融、成本和失败

文章强调：

- 删除经验库、通用/领域分工或组合机制会影响结果；
- 有效技能和脚本是重要可执行资产；
- 更多候选意味着更多执行与编辑费用；
- 只统计执行器步骤不能代表整个系统成本；
- 可能选错候选；
- 多个候选可能共享同一错误假设；
- 模型自己写的检查器也可能重复错误假设；
- 经验库可能积累重复或过时内容。

这些问题直接影响我们对预算、独立评测和不可修改计算层的设计。

### 19.2 第二篇：10 月开源模型总结，决策模型成为新的热点

原链接：

https://mp.weixin.qq.com/s/1GRrg1Bp2A8RUrHiLsvGcw

文章作者提供的归档：

https://github.com/liucongg/awesome-open-llms

文章核心观点：

- 当月出现多个决策模型；
- Jev 类模型评价分化；
- 后训练和领域适配仍有机会；
- 月度汇总帮助发现容易被信息流忽略的模型。

这是一篇模型目录文章，不是统一条件下的决策模型比较实验。以下清单保留用户提供的线索，不能因为进入清单就视为每条规格、日期和权重都已独立核实。

#### 用户提供文章中的模型清单

| 日期 | 文章列出的模型 | 文章归类/方向 |
| --- | --- | --- |
| 9/29 补充 | EmpirioLabs Aplomb 1 | 多模态决策 |
| 9/29 补充 | Xiaomi-OCR-0 | 文档理解/OCR |
| 10/1 | Cloudflare Clef、Clef-flash | 决策 |
| 10/2 | Amazon ALoDLM-8B | 自适应扩散语言模型 |
| 10/3 | Aleph Alpha Kolibri | 双语、RAG、代码、企业工具 |
| 10/7 | Liquid AI d1-3B、d1-omni-600M | 决策 |
| 10/8 | JetBrains Mellum2.1 | 代码 agent |
| 10/8 | Hugging Face Bio Carbon-A | DNA |
| 10/8 | LightOnOCR-3 | 文档/OCR |
| 10/9 | Qwen--2.1-Turbo | 图像生成与编辑；名称按用户文章保留 |
| 10/9 | Cloudflare Clef-omni | 多模态决策 |
| 10/9 | Youtu-Parsing-Omni | 全模态解析 |
| 10/10 | vLLM Semantic Router d3 | 决策 |
| 10/12 | MiniCPM-V 4.7 | 端侧视觉、OCR、多图/视频 |
| 10/13 | Fish Audio Drama 3 | 多角色 TTS |
| 10/14 | GameScaling | 游戏动作模型 |
| 10/15 | Matrix-Game 4.0 | 可交互视频世界模型 |
| 10/15 | Nemotron 3.5 Super VL | 文档、视频、多图推理 |
| 10/15 | Xing4.0-29B-Intent | 意图与参数抽取 |
| 10/15 | FLUX.3 Image Klein | 图像生成与编辑 |
| 10/15 | Mistral Timestral | 时间序列 |
| 10/15 | Granite Switch 4.1 | 企业文本、代码、工具 |
| 10/16 | Hy Memory 2.0 | 长期记忆写入 |
| 10/16 | Beam-1.0-501B-A23B | 通用大模型 |
| 10/16 | MOSS-Image | 图像生成 |
| 10/19 | Ming-3.0-audio-flash | 音频理解与生成 |
| 10/19 | Ling-3.0-flash-Sante | 医学研究推理与工具 |
| 10/19 | pplx-decider-v1.2 | 决策 |
| 10/20 | SoMark OXR | 文档解析 |
| 10/21 | Sage-A1.5 | 科研 agent |
| 10/21 | Spark-X2.6-4B | 轻量通用模型与工具调用 |
| 10/21 | Recursive Flint | 编码 agent |
| 10/21 | Granite Speech 5.0 2B | 语音识别 |
| 10/21 | Base Labs mammoth-1 | 决策 |
| 10/21 | pplx-embed-v2-late-0.6b、9b | 多模态 late-interaction 检索 |
| 10/22 | Carbon-X | DNA 与自然语言 |
| 10/23 | MiniMax M3.1 Flash | 原生多模态 |
| 10/23 | Nemotron-3.5-Bolt-E4B-4GB | 小显存模型 |
| 10/24 | Nanbeige4.3-33B | 长上下文 |
| 10/25 | Ling-3.1-flash-base | 混合注意力基座 |
| 10/26 | Hy-SimulMT | 同声传译 |
| 10/27 | Ling-3.1-flash-1019 | 后训练模型 |
| 10/27 | d3-ultra | 多模态决策 |
| 10/27 | Unlimited-ORC-2 | 文档/视觉解析；名称按用户文章保留 |
| 10/28 | Nemotron 3.5 Transcribe | 语音识别 |
| 10/28 | SenseNova 6.8 Code2Video | 代码驱动视频制作 |
| 10/28 | Clover-35B-A3B-Preview | 扩散模型 |
| 10/29 | Mistral Large 4 | 多模态 MoE |
| 10/29 | InclusionAI ZwZ2 | 视觉模型 |
| 10/29 | Base Labs joey-1 | 小型决策模型 |
| 10/30 | SenseNova-Loop-9B-Preview | 图文、代码和工具长流程 |

对本项目最相关的是决策模型、检索及记忆思路。第一阶段不把上述目录变成安装清单。

文章中的 d1-3B 被概括为文本决策，后续官方资料核实它也支持图片；本项目只使用文本即可。

---

## 20. 官方文档与来源索引

以下链接用于实现和核验；动态信息以正式接入时的实际文档与端点为准。这里不使用聊天内部 citation ID，确保文件发给 Codex 后仍可访问来源。

### ClinicalTrials.gov

- API 文档：https://clinicaltrials.gov/data-api/api
- OpenAPI：https://clinicaltrials.gov/api/oas/v2
- 研究 endpoint：https://clinicaltrials.gov/api/v2/studies
- 数据版本：https://clinicaltrials.gov/api/v2/version
- 数据规模：https://clinicaltrials.gov/api/v2/stats/size
- 协议字段定义：https://clinicaltrials.gov/policy/protocol-definitions
- 研究链接模式：https://clinicaltrials.gov/study/NCT02083484
- NLM API 培训材料：https://www.nlm.nih.gov/oet/ed/api/2024_drive-thru-data-api_transcript.pdf
- 全量 JSON 下载线索：https://clinicaltrials.gov/api/v2/studies/download?format=json.zip

全量下载链接仅为参考，不是当前执行指令。

### OpenAI

- 指定模型：https://developers.openai.com/api/docs/models/gpt-6.1-sol
- Function calling：https://developers.openai.com/api/docs/guides/function-calling
- API 部署说明：https://developers.openai.com/api/docs/guides/deployment-checklist

讨论时官方页面说明 GPT-6.1 Sol 的工具调用需要 Responses API。没有因为上下文长就要求把整站记录传给模型。

### OpenRouter

- Jev：https://openrouter.ai/docs/guides/community/jev
- Structured outputs：https://openrouter.ai/docs/guides/features/structured-outputs
- Provider selection：https://openrouter.ai/docs/guides/routing/provider-selection

讨论时官方文档列出：

- Decisions API：POST https://openrouter.ai/api/alpha/decisions
- System One API：POST https://openrouter.ai/api/v1/systemone

这些是接口参考，不包含任何账户密钥或付费调用记录。未来实验要锁定确切型号，避免 latest 别名改变比较对象。

结构化输出支持可能因供应商 endpoint 而不同，仍需应用侧校验。

### LangGraph

- Persistence：https://docs.langchain.com/oss/python/langgraph/persistence
- Interrupts：https://docs.langchain.com/oss/python/langgraph/interrupts
- Thinking in LangGraph：https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph

参考状态与恢复概念时，实际 Python API 以所安装版本为准。

### Neo4j

- Import：https://neo4j.com/docs/operations-manual/current/import/
- 官方 GraphRAG 包：https://github.com/neo4j/neo4j-graphrag-python
- RAG guide：https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_rag.html
- API：https://neo4j.com/docs/neo4j-graphrag-python/current/api.html

使用 Neo4j 不代表必须使用该包的生成式回答层或向量检索层。基础方案可以直接使用官方 driver 和自己的受约束查询编译器。

---

## 21. 基础服务验收清单

### 21.1 功能

- [ ] 接受仅含 query 的请求。
- [ ] 可选结构化条件有清晰 schema。
- [ ] 至少覆盖多种统计图和有意义的网络图。
- [ ] 多个自然语言问法可映射同一种分析操作。
- [ ] 支持必要澄清。
- [ ] 支持“只看 Phase 3”“再按年份”等追问。
- [ ] 新话题不继承旧条件。
- [ ] 图查询真实使用 Neo4j 的范围与关系。
- [ ] 返回前端无需猜测的 visualization specification。

### 21.2 数据与证据

- [ ] 全部数据来自指定 API。
- [ ] 有不可变原始快照。
- [ ] 跟随分页直到 token 消失。
- [ ] 去重后的数量与完整性分开核验。
- [ ] 不把局部缓存当全量。
- [ ] 不把 top-k 相似召回当统计总体。
- [ ] 多对多计数不膨胀。
- [ ] 正确处理 BIOLOGICAL 等干预类型。
- [ ] 组合阶段、缺失值、日期/入组类型有口径。
- [ ] 每个数据项有可获取的完整贡献证据。
- [ ] 展示裁剪与来源截断分别标注。
- [ ] no_data 与取数失败/未覆盖不同。

### 21.3 Agent 工程

- [ ] AnalysisPlan 有类型和语义校验。
- [ ] 工具操作与字段有边界。
- [ ] ContextBuilder 独立且有预算。
- [ ] 会话状态结构化保存。
- [ ] 运行状态可追溯、必要时恢复。
- [ ] 重试与修复有上限。
- [ ] 模型/provider/prompt/harness 版本可见。
- [ ] API key 不进入代码、日志和示例。
- [ ] 决策与 harness 实验接口存在但关闭。
- [ ] 不要求每个 LangGraph 节点都调用模型。

### 21.4 验证和提交

- [ ] 独立参考计算核验关键结果。
- [ ] 固定快照回归通过。
- [ ] 少量真实 API 联调通过。
- [ ] 3—5 个实际输入与真实 JSON 输出已保存。
- [ ] 启动步骤可按 README 重现。
- [ ] 输入/输出 schema 文档完整。
- [ ] 设计取舍、限制、AI 使用及开源来源披露。
- [ ] 可以打包提交，且不含密钥。

---

## 22. 给 Codex 的简短交接指令

下面这段是对当前需求的操作性重述，不是新的扩张范围：

> 请先阅读本文件，尤其是第 0、1、4—13、21 节。当前只实现符合 take-home 要求的基础服务。主模型用用户指定的 gpt-6.1-sol 和用户自己的 OpenAI API；后续决策模型走 API，OpenRouter 已有账户，但本期不跑实验。采用 Neo4j 和 LangGraph，同时把完整研究集合、不可变来源快照、范围约束、确定性聚合、完整证据、结构化多轮状态与上下文工程做好。给 DecisionPolicy、HarnessProvider、ContextBuilder、ExperienceStore 和 TraceSink 留好替换点，默认不启用自我改进或额外决策调用。先让手写计划到图表的计算链路正确，再接自然语言 planner。不要把早期设想、第三方 benchmark、示意 JSON 或本文件的设计目标写成已经完成的结果。结合实际仓库情况制定执行步骤，并推进基础服务、验证和文档交付。

## 23. 待实现时确定的常规配置

以下细节尚未锁定，属于实现时根据环境和小规模联调确定的参数，不应重新推翻已确定的架构：

- Python、LangGraph、Neo4j 与 SDK 的具体版本；
- 本机还是托管 Neo4j；
- 快照 TTL；
- 最大记录数、API 分页大小、超时、重试次数；
- 单请求模型调用/token/时间预算；
- 证据内联与分页阈值；
- 网络展示节点/边上限；
- 组合阶段的具体编码与排序；
- API 响应状态对应的 HTTP code；
- 同步请求的时间边界及是否需要后续异步扩展；
- 最终图表 schema 的字段命名；
- 启动配置与密钥注入方式；
- 实际部署是否需要额外鉴权。

模型选择、数据源、先基础后实验、API 优先的决策部署方向和完整性原则已经确定，不应把这些当作待用户重新确认的问题。


