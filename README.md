# Kaoyan Material Organizer - 考研资料整理

一个面向 Windows、Obsidian 与本地资料的考研知识库 skill。它把教材、讲义、课件、题解、PDF、照片和学习记录整理为可追溯的知识链，并提供本地检索、问答、复习与个性化学习建议。

```text
原始来源 -> 证据 -> 考纲 -> claim -> canonical card -> query -> learner
```

## 主要能力

### 多教材定位与比较

`kb.py ask --subject math --question "1800 P17第3题为什么和高数基础篇P56类似" --format teaching-json`
保留原话即可，入口按正式教材目录分别绑定各目标。`query --format json`、`ask --format json/teaching-json` 的多目标响应使用 `source_targets_version: source-targets.v1`：`items` 保留原文片段、绑定依据和对应单目标结果，`status` 为 `exact/partial/blocked`，只有全部目标通过证据门禁时 `comparison_allowed` 才为真。单目标与原有王道批量格式不变。

多目标的 `--book-title` 只填补未注明书籍的目标；无法唯一归属的全局页码/题号参数返回冲突。`--save` 拒绝多目标写入。缺失、歧义和不可用状态保持为阻塞结果；查询不补做 OCR、发布或索引写入。数学学科支持 `math`、`数学`、`高数`、`高等数学`，统一输出“数学”。

### 资料与学习工作流

- 注册教材、PDF 与纸质书照片，并保留来源和内容哈希。
- 按 `inspect -> map-pages -> OCR -> review -> classify -> publish -> query/ask` 处理纸质教材与 PDF。
- `book pdf-ocr-approve-mapping-interval` 与 `book pdf-ocr-apply-outline` 默认只预览；人工复核后用同一组参数加 `--yes --plan-fingerprint <预览指纹>` 执行。输入变化会拒绝执行，任一写入失败会恢复本次涉及的映射、分类及审计文件。
- 将证据关联到考纲、知识点和可复用知识卡。
- 在本地知识库中检索和提问，输出带来源的回答。
- 根据学习记录、错题和薄弱点生成复习与学习建议。
- 在迁移或批量处理前创建快照，并支持受控恢复。

## 项目与学习工作区边界

本仓库负责资料接入、页码映射、OCR、复核、证据发布、索引和 `query` / `ask` 验收；`vault_root` 负责学习任务、专题停点、复习与周记录，并消费已经发布的问答能力。普通章节接入只为可枚举的改动建立小范围恢复点，不默认复制整个 Vault 或媒体库。

PDF 物理页码与书上印刷页码必须分别保存并由正式映射确认；映射缺失、冲突或不连续时停止该范围发布。来源或版本专用偏移应保存在本地配置、映射或人工复核 handoff 中，不应写成公共规则。

## 安装

要求 Python 3.10 或更高版本。Windows PowerShell 示例：

```powershell
git clone https://github.com/UniC114514/kaoyan-material-organizer.git
cd kaoyan-material-organizer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

作为 Codex skill 使用时，可将仓库放在 `$CODEX_HOME\skills\kaoyan-material-organizer`。

## 本地配置

复制配置模板：

```powershell
Copy-Item kaoyan.config.example.json kaoyan.config.json
```

至少根据本机情况确认：

```json
{
  "workspace_root": ".",
  "vault_root": "D:\\Study\\KaoyanVault",
  "kb_root": ".kaoyan-kb",
  "backup_root": ".kaoyan-backups",
  "migration_root": "_migration",
  "python_executable": ".venv\\Scripts\\python.exe",
  "ocr_allow_remote": false
}
```

- `vault_root`：Obsidian 考研库或准备作为学习库的目录。
- `kb_root`：机器知识层、索引和审核队列。
- `backup_root`：快照与恢复数据。
- `migration_root`：迁移过程的中间工件。
- `ocr_*`：OCR 模型、缓存、并发、预算和远程调用策略。
- `paper_book_*`：纸质书入口目录和图片质量阈值。

`kaoyan.config.json`、本地知识库、快照、个人教材 profile 和本地考纲定义都已被 Git 忽略。每位使用者需要在自己的机器上配置这些内容。

如需生成某个科目的空白考纲定义：

```powershell
.\.venv\Scripts\python.exe scripts\import_syllabus.py scaffold --subject 数学 --format json
```

远程 OCR 默认关闭。不要把 API key 写入 JSON；需要时只在当前进程环境中提供：

```powershell
$env:MISTRAL_API_KEY = "your-key"
```

## 使用

先检查路径、Python、终端编码和 OCR 状态：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py doctor
```

所有稳定功能均从 `scripts\kb.py` 进入：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py --help
.\.venv\Scripts\python.exe scripts\kb.py book --help
.\.venv\Scripts\python.exe scripts\kb.py sync --help
.\.venv\Scripts\python.exe scripts\kb.py query --help
.\.venv\Scripts\python.exe scripts\kb.py ask --help
.\.venv\Scripts\python.exe scripts\kb.py review --help
.\.venv\Scripts\python.exe scripts\kb.py learner --help
.\.venv\Scripts\python.exe scripts\kb.py maintain --help
.\.venv\Scripts\python.exe scripts\kb.py snapshot --help
.\.venv\Scripts\python.exe scripts\kb.py migrate-vault --help
```

从仓库之外的工作目录调用时，使用解释器、入口和配置文件的绝对路径，并把全局 `--config` 放在子命令之前：

```powershell
& "<skill-root>\.venv\Scripts\python.exe" "<skill-root>\scripts\kb.py" --config "<skill-root>\kaoyan.config.json" query --subject 数学 --printed-page 62 --query "定理3.5" --format json
```

配置优先级为 `--config`、`KAOYAN_CONFIG_FILE`、当前目录配置、技能根目录配置、默认值。被选中的配置不存在、不可读或不是有效 JSON 时，命令会失败，不会静默切换到其他知识库。

精确教材定位可同时提供书名与印刷页：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py query --subject 数学 --book-title 李正元数一 --printed-page 49 --query "例2.29 隐函数微分" --format json
```

显式页码是硬约束。只有 `page_anchor.match_status` 为 `exact_evidence` 或 `exact_asset` 才表示原页已经确认；其他状态不会回退到无关页。`unavailable` 表示配置或正式页码索引不可用，与“教材中没有该页”的 `not_found` 严格区分。

“第 94 页起”“94 页附近”等软页码还会与正式小节标题锚点交叉核验。检查 `page_crosscheck`：只有 `confirmed` 才能继续按该小节讲解；`conflict`、`ambiguous` 或 `unverified` 都会阻断结论。页码与习题关系索引使用内容指纹，证据、映射、外部纸书 metadata、人工批准关系发生改写或删除后会返回 `*_index_stale`，用 `kb.py sync --indexes-only` 重建。

教材、讲义、题集、例题或题目照片中的解题请求还必须检查 `answer_grounding`。只有 `status=exact_answer` 且 `can_conclude=true` 才能输出答案判断或 AI 补充推导；题目页的 `exact_evidence` 不能代替原书答案。答案未找到、存在歧义、仅定位原图或链路不可用时，问答会失败关闭且 `ask --save` 保持零写入。

`request_resolution.source_request_kind` 把来源请求分为 `generic`、`page_content` 和 `exercise`。普通概念检索走 `generic`；解释教材某页的定义、代码或段落走 `page_content`，只有同来源、同印刷页且已复核的 `page_content_bundle.status=exact` 才能按原页讲解；有来源题目走 `exercise`，继续要求 `answer_grounding.status=exact_answer`。页内讲解和习题答案是两套独立门禁，不能互相替代。

普通 `generic` 检索同样受 `effective_book_title` 约束：显式书名或当前任务默认教材不会跨书补证据；书名不存在、没有同书可发布证据或主题词未覆盖时，结果为 `answer_mode=unconfirmed`，不回退到其他教材或无关章节。`generic_answer_bundle` 只有在主题相关性、同书证据和引用覆盖同时通过时才为 `exact`；其余为无结论、无引用正文的 `blocked`，`ask --save` 不得写入。

精确页码已定位但自然语言没有题号时，查询只从该页 `relation_status=exact` 且题干可唯一切片的正式习题关系中消歧。单题页直接采用；多题页必须命中唯一的区分性题干词组，答案正文、普通语义检索和选项字母均不参与题目身份判断。结果记录在 `request_resolution.exercise_resolution`：`explicit` 表示显式题号，`inferred_unique` 表示正式题干唯一命中；`ambiguous`、`not_found`、`unavailable` 均失败关闭，其中题目不唯一时只要求补充题号。

纸质习题书可登记书系别名、分册与题解配对。照片书源先注册，再按阶段或章节预览 OCR 范围：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py book register-photo-source --book-root <book-root> --format json
.\.venv\Scripts\python.exe scripts\kb.py book ocr --book-root <book-root> --stage basic --chapter-id <chapter-id> --dry-run --format json
```

`--dry-run` 不访问远程 OCR，也不写 `page_ocr_status.json`。OCR、复核和分类完成后，先预览 `book publish-exercises`；执行时必须追加预览返回的 `--plan-fingerprint`，再使用 `--yes` 才会把审核通过的习题发布到 evidence。预览不分配 evidence ID，也不改写 `id_counters.json`；执行会在事务快照内分配 ID、写 evidence、刷新 page/exercise/search/book-series 四类索引，任何失败都会回滚。问答结果中的 `book_route` 和 `exercise_route` 分别说明书系识别及题目—题解配对状态。

```powershell
.\.venv\Scripts\python.exe scripts\kb.py book publish-exercises --book-root <book-root> --format json
.\.venv\Scripts\python.exe scripts\kb.py book publish-exercises --book-root <book-root> --yes --plan-fingerprint <fingerprint> --format json
```

OCR 证据发布使用独立的零写入预览：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py book ocr-publish --book-root <book-root> --format json
.\.venv\Scripts\python.exe scripts\kb.py book ocr-publish --book-root <book-root> --yes --plan-fingerprint <fingerprint> --format json
```

预览会列出可发布项、阻断原因、输入哈希和稳定 `plan_fingerprint`；只有 `--yes` 且携带未变化的指纹才会写 evidence 并刷新 page/exercise/search/book-series 索引。图片来源哈希、OCR request、复核状态和正式页码映射不一致时 fail-closed；任何索引刷新失败都会回滚本次 evidence 与相关索引。PDF 走同一条边界：`ocr-pdf-source -> pdf-ocr-review-status -> pdf-ocr-review-artifact -> pdf-ocr-publish`，发布只接受显式 `accepted` 的逐页复核，且同时绑定注册 PDF SHA、渲染源图 SHA、request key、`pdf_page` 与 `printed_page`；`not-required` 不能发布。发布后应重放原始 `query` / `ask` 请求验证结果。旧的 `publish_full_pdf_ocr_evidence.py` 仅返回迁移错误，不再提供旁路发布。

`book exercise-coverage` 同时支持 PDF 与照片教材。`--source-id` 是统一参数，旧的 `--pdf-source-id` 继续兼容；追加 `--verify-query-ask` 会逐关系验证检索与教学问答入口，`--expected-relations` 固定预期数量，`--require-complete` 在关系数、答案门禁或来源页码任一不一致时返回非零：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py book exercise-coverage --subject 数学 --book-title "考研数学高等数学辅导讲义 基础篇" --source-id SRC-MATH-0006 --chapter-number 2 --verify-query-ask --expected-relations 26 --require-complete --format json
```

按页查询必须提供 `--subject`；已知时也应提供 `--book-title`。结果中的 `page_verification` 会分别说明页面定位、题号正文核验、命中层及能否按教材正文讲解。例如 `exact_asset + unverified + page_asset` 表示“原页已定位、教材正文未确认”，并不表示该页不存在。

### 有来源题目的原书答案门控

教材、讲义、题集、例题或题目照片中的解题请求还必须检查 `answer_grounding`。题目页的 `exact_evidence` 只证明原题存在，不能代替原书答案；只有 `status=exact_answer` 且 `can_conclude=true` 时，`ask` 才能判断使用者过程、给出数值/选项/证明结论或附加 AI 辅助推导。

```powershell
.\.venv\Scripts\python.exe scripts\kb.py ask --subject 数学 --book-title 李正元数一 --printed-page 64 --question "例3.8 第一问，检查我的过程" --format json
```

`ask` 的规范参数是 `--question`；为兼容历史命令也接受 `--query`，二者互斥并归一到同一问题字段。比较多道例题时，先为每道题分别执行精确 `ask`，全部通过来源门禁后再比较。

给教学模型或自动化消费时可改用 `--format teaching-json`。当前视图版本是 `m6.teaching.v3`，它输出请求解析、页码交叉核验、答案门控、引用覆盖、`generic_answer_bundle`、`teaching_bundle`、`page_content_bundle` 和只读的 `concept_routes`；`--format json` 继续保留完整诊断契约。即使同时使用 `--save`，程序也会先用完整契约完成保存，再把终端输出投影为紧凑视图。

`answer_grounding.status` 可能为 `exact_answer`、`answer_asset_only`、`answer_ambiguous`、`answer_not_found`、`answer_unavailable` 或 `not_applicable`。除 `exact_answer` 外，有来源题目均失败关闭：不输出解题结论、不以 AI 独立推导补位；`ask --save` 还要求原题和原书答案的证据引用同时完整，否则保持零写入。明确说明是自拟题时不启用此门控。

### 会话续接与学习记录

长对话先以“当前任务 + 学科专题锚点”确定真实停点。教材页码、题号、选项或原文请求必须先调用 `kb.py query/ask`，禁止“先回答、后定位”；后续追问必须把本会话结构化结果已经确认的教材、印刷页、章节、容器和题号继续显式带入下一次调用。若使用者在追问中明确改指另一页、定义、定理或原文位置，新指向取代旧例题范围，不能继续用旧例题的答案证据回答新概念。教材回答必须把教材结构化证据、仅原页定位、补充推导和学习者反馈分别标注；补充推导不能沿用原题页码或题号。“可用于本题”“可由某定理推出”“是专门情形”和“两个定理等价”是不同判断，只有教材正文分别支持时才能作相应归因。若一次学习请求触发了 OCR、证据或索引修复，最终验收必须重放最初的自然语言请求，不能只依据索引数量或通用测试宣告完成。

讲解本身不写入学习资料。使用者明确要求“记录”“同步”或收束时，技能先读取本次 `vault_root` 中的 `00_总计划/26_主控与对话规则.md`；该本地文件是跨会话范围、双水位线、正文读取、防溢出回退和状态写入的唯一权威。正式任务接口产生的临时清单必须先经 `kb.py learner closure validate --manifest-json <path> --vault-root <path>` 做时间窗口、覆盖缺口、稳定来源键和推进资格校验；列表截断时可用 `learner closure probe-local-indexes` 与 `discover-local` 只读核验本地索引。技能不在仓库中维护第二份扫描算法；权威文件缺失或判定结果不允许时保持零写入，并分别报告本地和全局覆盖。只有一个明确主概念时才可用 `kb.py learner distill` 生成候选并通过 `apply --yes` 发布；多任务形成多个主题且没有明确主次时不额外蒸馏。

### 章末复盘与自适应复习

数学和408的章末复盘可以在同一道题下按重要知识点分别记录正确性与熟练度：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py learner exercise --subject 数学 --chapter 第三章 --node MATH-INTEGRAL-001 --result right --context chapter_review --review-id chapter-3-review --question-id question-1 --fluency not_fluent --duration-minutes 8 --format json
```

正式章末复盘本身即授权写入 learner 层。整次复盘优先使用 `adaptive-review-batch.v1` JSON；命令默认只预览，全部项目合法后才用 `--yes` 一次性写入，并保留原始复盘时间、来源任务/消息、知识点键、客观结果、熟练度和提示使用情况：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py learner exercise --batch-json <chapter-review.json> --format json
.\.venv\Scripts\python.exe scripts\kb.py learner exercise --batch-json <chapter-review.json> --yes --format json
```

熟练度可选 `very_fluent`、`fairly_fluent`、`not_fluent` 和 `needs_remediation`。首次间隔分别为 14、7、3、1 天；客观错误或部分完成会限制有效熟练度，但不会覆盖使用者的原始自评。

学习开始时可读取当天到期项。默认只选最多 20 分钟、3 个知识点，未选中的逾期内容顺延；查询本身不会记作完成：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py learner review-followups --plan-date 2026-08-12 --time-budget-minutes 20 --max-items 3 --format json
```

### learner 日常入口与阶段产物

日常教学使用 `query/ask`、学习续接、`learner review-followups`、`exercise`、`distill` 与 `closure`。下列阶段链保留为显式 CLI 能力，不要求普通讲题逐层生成或读取；调用前须准备对应输入。生成器会保存派生文件，不能把它们当成普通零写入查询。

| 入口组（`learner` 下） | 主要输入 | 输出 | 仓库内下游消费者 |
| --- | --- | --- | --- |
| `orchestration-context`、`daily-card`、`review-followups`、`weekly-orchestration`、`teacher-loop-artifact`（R17） | 学习上下文、复习事件及日/周安排 | 日/周派生视图、`30_r17_teacher_loop_acceptance_artifact.json` | R18 coaching context 与阶段验收 |
| `adaptive-coaching-context`、`adaptive-coaching-packet`、`coaching-feedback-loop`、`closed-loop-operations`、`adaptive-coaching-artifact`（R18） | R17 产物及 coaching 反馈 | context/packet/feedback/operations、`35_r18_adaptive_coaching_acceptance_artifact.json` | R19 longitudinal context 与阶段验收 |
| `longitudinal-tutoring-context`、`tutoring-strategy-packet`、`tutoring-feedback-loop`、`long-horizon-operations`、`longitudinal-tutoring-artifact`（R19） | R18 产物与持续辅导反馈 | context/strategy/feedback/operations、`40_r19_longitudinal_tutoring_acceptance_artifact.json` | R20 trigger 与阶段验收 |
| `autonomous-trigger-contract`、`autonomous-action-plan`、`autonomous-governance-ledger`、`autonomous-tutoring-artifact`（R20） | R19 产物及治理输入 | trigger/action/governance、`44_r20_autonomous_tutoring_acceptance_artifact.json` | 显式 CLI 调用者；未据此推断外部使用情况 |

这些阶段产物及其 Markdown 视图保留既有名称与契约，不替代当前任务、专题锚点或学习记录，也不自动授予行动或写入权限。

### 实现与兼容边界

`query_local_knowledge.py` 保留原 CLI 和 Python 调用入口，负责检索与来源证据编排；请求解析、主题及同书门禁、批量问答、文本呈现分别位于 `scripts/kaoyan_kb/domain/query/` 的 `request.py`、`topics.py`、`batch.py`、`views.py`。批量模块显式接收单题查询与定位依赖，不反向导入入口，不另建门禁或状态。

R17–R20 的业务判定、公开命令和既有产物保持兼容。`learner_artifact_files.py` 是产物 JSON 文件名的唯一声明；生成器仍导出原 `ARTIFACT_JSON` 常量，但读取上游文件名不再加载上游构建实现。18 个生成器共用 `learner_artifact_runner.py` 的输出流程，四个阶段验收共用 Markdown 呈现；输入缺失、事实回写与人工回滚权限仍由原业务判定负责。共用输出流程先完成渲染和结果校验，再写 JSON 与 Markdown；写入异常时恢复这一对文件及本次新建的空目录，不自动构建上游产物。此恢复范围不包括进程强制终止或并发写入。

普通 `query/ask` 使用只读路径，不初始化学习库；同一次查询（含批量子题）复用 JSON 读取结果，`ask` 的查询与引用组装共享读取范围，保存时重新校验。书名到教材 ID 的映射每次请求只构建一次，结束后清空缓存。教材匹配使用规范化的完整名称或正式书系索引中能唯一定位的别名，未登记的截断书名、跨版本名称及多卷歧义不会通过同书门禁。

回归测试使用 `python -m pip install -e ".[test]"` 安装项目与测试依赖，再运行 `python -m pytest -q`。测试应使用临时夹具；交付前还应在仅包含 Git 收录文件和本次待交付文件的副本中运行，排除被忽略的本机配置、索引和教材数据对结果的影响。

答案契约的 `source_revisions` 记录引用证据与命中结论的 JSON 内容指纹。保存前直接读取这些正式记录并比对；记录缺失、损坏、变化或旧契约缺少指纹时拒绝保存，需重新查询。JSON 排版变化不影响指纹。此检查发生在写入前，不提供跨进程锁，也不保证检查后发生的并发修改能被阻止。

## 数据与隐私

- 原始资料保留在使用者自己的目录中，不通过移动原文件表达章节归属。
- `.kaoyan-kb/`、`.kaoyan-backups/`、本机配置和临时目录不会进入 Git。
- OCR 结果必须经过审核与证据门控，不能直接成为正式 claim。
- 远程 OCR 默认关闭；启用前请确认资料授权、隐私边界和费用预算。配置持久开启远程且月预算为 `0`（无限制）时，`doctor` 会明确警告用量与费用无上限。
- API key 只放在进程环境或系统密钥管理工具中，不写入配置、日志或 Markdown。

当前主要支持 Windows、本地 Obsidian vault 和中文考研资料。

## 许可证

本项目采用 [MIT License](LICENSE)。你可以使用、复制、修改和分发本项目，但需保留版权与许可声明。
