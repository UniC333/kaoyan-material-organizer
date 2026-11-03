# Kaoyan Material Organizer - 考研资料整理

一个面向 Windows、Obsidian 与本地资料的考研知识库 skill。它把教材、讲义、课件、题解、PDF、照片和学习记录整理为可追溯的知识链，并提供本地检索、问答、复习与个性化学习建议。

```text
原始来源 -> 证据 -> 考纲 -> claim -> canonical card -> query -> learner
```

## 主要能力

### 多教材定位与比较

`kb.py ask --subject math --question "1800 P17第3题为什么和高数基础篇P56类似" --format teaching-json`
保留原话即可，入口按正式教材目录分别绑定各目标。`query --format json`、`ask --format json/teaching-json` 的多目标响应使用 `source_targets_version: source-targets.v1`：`items` 保留原文片段、绑定依据和对应单目标结果，`status` 为 `exact/partial/blocked`，只有全部目标通过证据门禁时 `comparison_allowed` 才为真。单目标与原有王道批量格式不变。

多目标的 `--book-title` 只填补未注明书籍的目标；无法唯一归属的全局页码/题号参数返回冲突。`--save` 拒绝多目标写入。缺失、歧义和不可用状态保持为阻塞结果；默认查询不补做 OCR、发布或索引写入，显式 `--auto-sync` 的索引刷新例外见下文。数学学科支持 `math`、`数学`、`高数`、`高等数学`，统一输出“数学”。

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
git clone https://github.com/UniC333/kaoyan-material-organizer.git
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

### 无 API key 的官方入库路线

优先使用本地 CPU OCR：安装 `python -m pip install -e ".[ocr-local]"`，然后设置
`"ocr_provider": "rapidocr"`、`"ocr_model": "rapidocr-onnxruntime-1.4.4-bundled"`、
`"ocr_allow_remote": false`。也可在现有 `book ocr` / `book ocr-pdf-source` 命令上加
`--provider rapidocr`（未指定 `--model` 时自动选择本地模型）。原有 Mistral 和 fixture 行为不变；fixture 仅供测试，不能识别图片。

`ocr-local` 固定 [rapidocr-onnxruntime 1.4.4](https://pypi.org/project/rapidocr-onnxruntime/1.4.4/)，
安装包携带默认 ONNX 模型，安装时需下载依赖和模型；安装完成后推理全程在本机运行，无需 API key、GPU 或远程授权。
其返回行文本、矩形坐标和行置信度，不提供专用公式识别、表格结构、页眉页脚分离或逐词置信度。
公式、代码和复杂排版仍须对照原图复核，OCR 结果不直接成为正式证据。

```powershell
.\.venv\Scripts\python.exe scripts\kb.py book ocr --book-root <book-root> --provider rapidocr --dry-run --format json
.\.venv\Scripts\python.exe scripts\kb.py book ocr --book-root <book-root> --provider rapidocr --format json
```

单张图片诊断可直接调用 `scripts/ocr_document.py --file <image.png> --provider rapidocr --format json`。
此底层入口从 `KAOYAN_CONFIG_FILE` 选择配置，并写入所选知识库的 OCR 缓存；输出的 `normalized_path` 保存识别文字，`remote_calls` 应为 0，`published_evidence` 应为 false。
它只处理单帧图片和 `--pages 0-0`；PDF 请走 `book ocr-pdf-source` 渲染链路。
缺少可选依赖会报出上述安装命令，不会降级成 fixture。

无法安装或运行本地引擎时，**AI 视觉直读是正式等价的转录接入路径**：实际查看原图后生成绑定原图哈希的 `.vision.json`，用 `--provider vision` 导入同一 OCR 缓存、复核和发布链路。它不执行 OCR 或调用视觉 API，导入成功不代表复核通过；操作和产物格式见[资料接入参考](references/material-intake.md#无-api-key-的接入)。
普通讲题中临时查看原图仍是受限辅助路径，不自动写入或发布。

运行 `tests/unit/test_local_ocr_provider.py` 可用 Pillow 生成英数及中文图片做真实识别验证；缺引擎或模型初始化失败会明确 skip 并报告原因，不能把转录导入测试算作本地 OCR 成功。

## 使用

先检查路径、Python、终端编码和 OCR 状态：

```powershell
.\.venv\Scripts\python.exe scripts\kb.py doctor
```

### 清理强杀残留的临时文件

显式执行 `python scripts/kb.py --config <kaoyan.config.json> doctor --prune-tmp --format json`（也支持 `text`）。普通 doctor、只读 `doctor validate` 和 sync 均不自动清理；清理模式独立运行，不初始化 schema 或刷新索引，也不能与 validate 合用。

仅扫描所选 KB 根目录直属文件、`kb_layout` 管理目录及位于 KB 内的配置 OCR 缓存，不跟随目录链接、不清理库外文件。仅识别 `.<目标文件名>.<PID>.<32位小写十六进制UUID>.tmp` 与 `.<目标文件名>.restore.tmp`。删除前必须取得 #26 的对应目标锁并持锁至删除结束，还须确认 PID 已退出；持锁、进程存活/状态不明、未知命名及链接均保留。固定 `.restore.tmp` 没有创建者 PID，恢复写入也未采用目标锁，因此只报告跳过，人工清理时，先确认所有相关写入进程均已退出，再逐项核验文件确为恢复残留而非正在进行的写入，确认后逐项删除 `.restore.tmp` 文件；保留正式目标文件和稳定的 `.lock` 文件。每项删除/跳过均输出路径和原因，`deleted_count` 只统计实际删除；保留稳定的 `.lock` 文件。

人工验收：在 KB 的 `indexes/` 手动放置符合上述 PID+UUID 命名、PID 确已退出的测试文件；运行清理命令，核对该路径的 `deleted` 记录、删除计数和实际消失，再运行一次应无新增删除。可直接复制的造样例步骤见 [doctor 说明](references/doctor-validation.md#临时文件清理)。

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

也可在本次 `query` 或 `ask` 显式添加 `--auto-sync`，例如 `kb.py ask --subject 数学 --question "导数的定义" --auto-sync --format json`（`query` 对应使用 `--query`）。开关在查询前检查页码与习题关系索引，检测到 stale、缺失或习题索引版本不匹配时，调用与 `sync --indexes-only` 相同的增量同步入口，刷新后重新检查，再执行单目标、多目标或批量查询；索引已新鲜时不写入。同步或锁等待失败、同步后仍不可用时非零退出，不查询、不输出旧答案、不保存；配置缺失、索引损坏等非上述原因也直接报错。

`--auto-sync` 是对本次索引写入的显式授权，不执行 OCR、发布、学习状态刷新或完整 sync；实际更新范围与 `sync --indexes-only` 一致。未加开关时保持原有只读语义、stale 提示与阻断，不会每日首次问答自动刷新，也不记录每日同步时间戳。纯只读调用不要添加此开关。

`ask --save --auto-sync` 依次执行必要的索引同步、查询、答案契约校验和原有保存流程；同步失败不会保存，答案未通过原有保存门禁也不会保存。保存失败不回滚已成功的索引刷新；批量/多目标及 `--confirmed-topic` 与 `--save` 的原有限制不变，非法组合在同步前拒绝。带开关的命令按写命令串行执行；手动和自动索引刷新共用 `indexes/query-indexes.lock`，使用现有 `file_lock` 协议（默认等待 30 秒），锁覆盖全部索引构建子进程；该锁不提供全库事务，也不协调其他发布/保存写命令。锁文件按下文规则保留。


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

配置启用的科目（默认数学和 408）的章末复盘可以在同一道题下按重要知识点分别记录正确性与熟练度：

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

在 `kaoyan.config.json` 中可配置复习科目、间隔与每日上限。例如加入英语：

```json
{
  "review_subjects": ["数学", "408", "英语"],
  "adaptive_review": {
    "intervals": {"very_fluent": {"initial_days": 10, "multiplier": 2, "max_days": 40}},
    "time_budget_minutes": 25,
    "max_items": 4
  }
}
```

优先级为显式 `--time-budget-minutes` / `--max-items` > 配置 > 默认值。缺省仍是数学/408、首次间隔 14/7/3/1 天、每天 20 分钟/3 项/一道完整数学计算题；后续默认非常熟练 ×2（上限 45 天）、比较熟练 ×1.5（上限 21 天），其余重置。新增科目共用相同复习流程，移除科目立即排除其队列与完成统计，不删除历史；间隔修改只影响新记录。

科目采用已知规范名校验：`数学`、`408`、`英语`、`政治`、`洛谷`、`算法竞赛`；空列表、未知名称、错误类型或负数明确报错。完整默认值见 [配置示例](kaoyan.config.example.json)，倍率、间隔上限、完整数学题上限和耗时估计等参数及校验规则见 [自适应复习配置](references/adaptive-review.md#配置与优先级)。

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

`query_local_knowledge.py` 保留原 CLI 和 Python 调用入口，负责检索与来源证据编排；请求解析、主题及同书门禁、批量问答、文本呈现分别位于 `scripts/kaoyan_kb/domain/query/` 的 `request.py`、`topics.py`、`batch.py`、`views.py`；教材证据门禁与讲解载荷位于 `grounding.py`。批量模块显式接收单题查询与定位依赖，不反向导入入口，不另建门禁或状态。

R17–R20 的业务判定、公开命令和既有产物保持兼容。`learner_artifact_files.py` 是产物 JSON 文件名的唯一声明；生成器仍导出原 `ARTIFACT_JSON` 常量，但读取上游文件名不再加载上游构建实现。18 个生成器共用 `learner_artifact_runner.py` 的输出流程，四个阶段验收共用 Markdown 呈现；输入缺失、事实回写与人工回滚权限仍由原业务判定负责。共用输出流程先完成渲染和结果校验，再写 JSON 与 Markdown；写入异常时恢复这一对文件及本次新建的空目录，不自动构建上游产物。此恢复范围不包括进程强制终止或并发写入。

未加 `--auto-sync` 的普通 `query/ask` 使用只读路径，不初始化学习库；同一次查询（含批量子题）复用 JSON 读取结果，`ask` 的查询与引用组装共享读取范围，保存时重新校验。书名到教材 ID 的映射每次请求只构建一次，结束后清空缓存。教材匹配使用规范化的完整名称或正式书系索引中能唯一定位的别名，未登记的截断书名、跨版本名称及多卷歧义不会通过同书门禁。

回归测试使用 `python -m pip install -e ".[test]"` 安装项目与测试依赖，再运行 `python -m pytest -q`。测试应使用临时夹具；交付前还应在仅包含 Git 收录文件和本次待交付文件的副本中运行，排除被忽略的本机配置、索引和教材数据对结果的影响。

写路径的 `file_lock(target, timeout=30)` 使用标准库 POSIX `flock` / Windows `msvcrt` 跨进程互斥锁，覆盖读取、修改到原子替换的全过程；超时抛出包含目标和锁文件路径的 `FileLockTimeout`。持有进程退出或被强制终止后，操作系统自动释放锁，无需按时间或 PID 猜测并删除陈旧锁。每个目标旁仅保留一个空的 `<target>.lock` 文件，供后续写入者复用；它不记录业务事实，随目标长期保留、空间上界为每目标一个空文件，丢失时可在所有相关进程停止后重建。运行期间禁止删除或替换锁文件。锁不可重入，持锁时不要 fork 子进程。

同一知识库的所有写命令必须串行执行，包括 `ask --save`、`sync`、发布、蒸馏及回滚；必须等待前一条写命令结束后再启动下一条。局部文件锁不代表整个命令已支持并发。

Windows 分支目前仅通过模拟测试检查文件定位及 `msvcrt.locking` 调用参数，未验证真实互斥；本机无 Windows，尚未完成真实 Windows 双进程验证。Windows 上仍须串行执行写命令。

目前保护 ID/运行编号计数器、schema 版本初始化、learner 事件追加及视图重建、refinement 队列重建与审核、OCR 按文件缓存索引和复核 overlay。原子文本写入使用进程号与随机标识组成的独立临时文件，以排他创建、flush、fsync、replace 写回，并仅清理本写入者创建的临时文件。读者仍通过正式文件读取，无需取锁。

普通异常会执行 `finally` 清理自身临时文件（清理失败时也可能残留），但强杀或断电不会执行 `finally`。PID+UUID 命名使每次写入使用独立文件，异常终止后的残留文件会随此类终止逐次累积，没有固定数量或空间上界。使用上文 `doctor --prune-tmp` 显式发现并安全清理；固定 `.restore.tmp` 只报告，停止全部写命令后人工核验。保留正式目标文件与 `<target>.lock` 锁文件，不按文件年龄判断孤儿状态。

写路径审计同时发现来源/章节注册、页码映射、发布索引合并、蒸馏候选与多文件回滚等读改写流程；这些流程涉及跨文件状态、调用链或回滚，需要统一事务锁协议，本次未扩展。上述锁仅协调采用同一协议的写入者，不保证全库或多文件事务并发安全；现有保存前来源指纹检查的边界如下。

答案契约的 `source_revisions` 记录引用证据与命中结论的 JSON 内容指纹。保存前直接读取这些正式记录并比对；记录缺失、损坏、变化或旧契约缺少指纹时拒绝保存，需重新查询。JSON 排版变化不影响指纹。此检查发生在写入前，不提供跨进程锁，也不保证检查后发生的并发修改能被阻止。

## 开发分层与增量收敛

本节是新增能力放置规则的权威位置；SKILL.md 只链接到这里。issue #19 沿已有职责抽取逐步收敛，不将现有包视为另一套完整实现，也不一次改写所有脚本。

- `scripts/*.py` 的目标是每个文件少于 200 行，仅保留 `parse_args`、参数转交、输出及退出码适配；新增业务实现直接放入 `kaoyan_kb/`。现存超限脚本保留兼容入口，按关注点逐块迁移，不借入口兼容继续堆积业务。
- `kaoyan_kb/cli/` 负责命令注册、参数到业务调用的适配和用例编排；不得复制领域判定或自行实现文件存储。
- `kaoyan_kb/domain/` 负责请求解析、教材/主题/证据门禁、检索规则、答案契约、批量领域流程及呈现载荷；按内聚关注点分模块，显式传入读取、检索或子查询依赖，不新增对顶层脚本的反向导入。
- `kaoyan_kb/storage/` 负责 JSON/文本读写、原子替换、锁与快照等存储机制，不包含学科、证据或教学决策。调用已有权威 helper，避免建立第二套存储规则。
- `common.py` 不再新增无关函数；新能力直接进入对应包模块。迁移公共函数时保留兼容导出，先核实调用者和职责，再删除旧实现；本次该文件字节不变。
- 大文件按职责拆：query 继续拆检索召回、证据组装和查询编排；answer 拆答案契约、教学视图与文本呈现；common 按配置/路径、实体契约、存储等职责分批迁出。包内模块也应按关注点拆分，不能把整个大脚本原样搬进一个同名大模块。

### issue #19 第一步盘点（2026-10-31）

范围：只统计源码物理行（含空行、注释），顶层为 `scripts/*.py`，递归为 `scripts/**/*.py`；不读取真实知识库或工作区临时目录，不使用 git。此前核查递归数为 171，本地改前实测为 176；此处如实使用本地数，不据此推断差异来源。

| 指标 | 改前 | 改后 |
| --- | ---: | ---: |
| 顶层 Python 文件 | 115 | 115 |
| 顶层含 `argparse` 的文件（文本匹配） | 105 | 105 |
| 顶层 <200 行 | 50 | 50 |
| 顶层 200–499 行 | 52 | 52 |
| 顶层 500–999 行 | 9 | 9 |
| 顶层 ≥1000 行 | 4 | 4 |
| 顶层源码总行数 | 33666 | 33336 |
| 顶层 ≥200 行的文件/总行数 | 65 / 27317 | 65 / 26987 |
| scripts 递归 Python 文件 | 176 | 177 |
| kaoyan_kb 包内 Python 文件（含 `__init__.py`） | 48 | 49 |

已有包的职责：`cli/` 改前 12 个 Python 文件（命令组、handler、查询同步及产物 runner）；`domain/` 改前 30 个（教材/页码/习题定位、证据发布、学习与查询领域模块）；`storage/` 4 个（原子 I/O、文件锁、快照支持）。另有包根 `__init__.py` 和 `validation.py`。query 已复用 request/topics/batch/views 及召回、权限等模块，common 已复用 atomic_io；它们是现有职责抽取，不是两套完整重复业务。

调用核查：`kb.py` 经 `cli/core_commands.py` 将 ask 转发到 `ask_local_knowledge.py`；后者仍导入 `answer_local_question.py` 的答案契约、教学视图和文本呈现。`build_exercise_coverage_report.py`、`save_local_answer.py` 和包内 `query/source_targets.py` 也调用 answer。因此本次保留 answer，不将其当作废弃入口删除或改写。

本次只抽取“教材证据门禁与讲解载荷”：7 个函数迁入 `domain/query/grounding.py`，包含答案确认、讲解引用与图片链接组装、精确题目对的页面锚点修补、审核页面正文筛选。新模块直接复用包内书系请求分类和证据发布判定，不读写知识库、不反向导入 query 入口；旧入口保留同名导入供原有调用者使用。迁移的 7 个函数逐一 AST 对比与原实现相同，参数和返回结构不变。

| 改动/核对文件 | 改前行数 | 改后行数 | 说明 |
| --- | ---: | ---: | --- |
| `scripts/query_local_knowledge.py` | 2666 | 2336 | 移除 7 个函数实现，保留兼容导出与原有查询编排。 |
| `scripts/kaoyan_kb/domain/query/grounding.py` | — | 349 | 新增内聚的教材证据门禁与讲解载荷模块。 |
| `tests/unit/test_query_grounding.py` | — | 98 | 验证兼容导出、证据缺失阻断、独立页码和交叉核验门禁。 |
| `README.md` | 350 | 491 | 集中记录分层规则、盘点、迁移顺序与验证结果。 |
| `SKILL.md` | 56 | 58 | 仅添加到本节的开发规则链接。 |
| `scripts/common.py`（未改） | 1272 | 1272 | 字节不变，未新增函数。 |

### 运行产物模块命名（issue #27 历史映射）

五个模块统一改为通用名称，第五个 run-manifest 是同一运行产物依赖链的起点，一并重命名；旧 Python 文件已删除。

| 旧名 → 新名（均位于 `scripts/`） |
| --- |
| `build_r16_changed_only_artifact.py` → `build_changed_only_artifact.py` |
| `build_r16_formal_usable_artifact.py` → `build_formal_usable_artifact.py` |
| `build_r16_gray_release_artifact.py` → `build_gray_release_artifact.py` |
| `build_r16_recovery_artifact.py` → `build_recovery_artifact.py` |
| `build_r16_run_manifest_artifact.py` → `build_run_manifest_artifact.py` |

审计覆盖全仓库源码、动态导入、CLI 注册、测试、文档、配置和 `agents/`：编排模块只直接导入 changed-only 与 formal-usable，其余通过该家族间接依赖；没有旧模块的动态导入或 CLI 注册。原有文档引用仅在本 README 的脚本盘点表，已更新。

兼容边界：`ARTIFACT_JSON`、其他公共常量（含 `R16_*`、`POST_R16_*`）及函数签名保持不变。旧产物路径、JSON 键（含 `post_r16_*`）、ID、契约版本、readiness 状态和 `input_contract_refs[].name` 是已有生产者/消费者的外部契约，不能随模块改名迁移；其中的 `r16` 值原样保留。`scripts/runtime_artifact_contracts.json` 是这些序列化字符串的静态兼容表，由相邻 Python 模块加载，供五个构建器和编排消费者共用；生成的 Markdown、报告、发布文档和 CLI 输出也保持不变。该表须随源码分发，缺失或损坏会直接报错；它不是运行时缓存或学习状态，不自动写入、追加或过期，随契约版本维护，固定为一个小型源码资源，无运行时空间增长。

定向测试以改名前捕获的公共 API 与产物 SHA-256 为基准，验证缺少输入和输入就绪两条路径（仅临时根路径归一化）、新导入、旧路径不可导入及 `scripts/**/*.py` 无小写 `r16`。测试夹具和本历史映射保留旧契约用于追溯。

### 仍未收敛的顶层脚本

下表是全部 65 个 ≥200 行文件（恰好 200 行也不满足 `<200`），按改前行数降序排列；前 10 行即改前最大的 10 个文件。本步后前 10 名文件及顺序不变。行数达标的 50 个文件尚未逐一证明“只做转发”，不能以行数代替职责验收。

| 排名 | scripts/ 下文件 | 改前 | 改后 |
| ---: | --- | ---: | ---: |
| 1 | `query_local_knowledge.py` | 2666 | 2336 |
| 2 | `answer_local_question.py` | 1417 | 1417 |
| 3 | `common.py` | 1272 | 1272 |
| 4 | `build_chapter_knowledge.py` | 1039 | 1039 |
| 5 | `publish_pdf_ocr_evidence.py` | 954 | 954 |
| 6 | `deepseek_retrieval.py` | 901 | 901 |
| 7 | `publish_book_exercises.py` | 734 | 734 |
| 8 | `build_global_knowledge_registry.py` | 694 | 694 |
| 9 | `publish_book_ocr_evidence.py` | 600 | 600 |
| 10 | `conversation_distillation.py` | 589 | 589 |
| 11 | `build_exercise_coverage_report.py` | 585 | 585 |
| 12 | `build_search_index.py` | 563 | 563 |
| 13 | `refresh_chapter_index.py` | 512 | 512 |
| 14 | `classify_book_pages.py` | 489 | 489 |
| 15 | `ocr_book_pages.py` | 464 | 464 |
| 16 | `promote_master_cards.py` | 433 | 433 |
| 17 | `save_local_answer.py` | 414 | 414 |
| 18 | `build_subject_course_index.py` | 389 | 389 |
| 19 | `build_study_orchestration_context.py` | 381 | 381 |
| 20 | `build_learning_dashboard.py` | 379 | 379 |
| 21 | `retrieve_knowledge.py` | 360 | 360 |
| 22 | `extract_source_evidence.py` | 356 | 356 |
| 23 | `publish_canonical_cards.py` | 356 | 356 |
| 24 | `paper_book_assets.py` | 346 | 346 |
| 25 | `audit_chapter_knowledge.py` | 333 | 333 |
| 26 | `build_pdf_ocr_review_artifact.py` | 332 | 332 |
| 27 | `learner_events.py` | 319 | 319 |
| 28 | `build_gray_release_artifact.py` | 312 | 312 |
| 29 | `ocr_pdf_book_source.py` | 311 | 311 |
| 30 | `reconcile_exam_knowledge.py` | 307 | 307 |
| 31 | `build_adaptive_coaching_context.py` | 304 | 304 |
| 32 | `plan_chapter_chunks.py` | 301 | 301 |
| 33 | `deepseek_adaptive.py` | 300 | 300 |
| 34 | `build_learner_acceptance_artifact.py` | 298 | 298 |
| 35 | `build_pdf_ocr_review_status.py` | 287 | 287 |
| 36 | `build_formal_usable_artifact.py` | 284 | 284 |
| 37 | `repair_parallel_book_provenance.py` | 280 | 280 |
| 38 | `build_autonomous_governance_ledger.py` | 273 | 273 |
| 39 | `build_longitudinal_tutoring_context.py` | 271 | 271 |
| 40 | `config.py` | 265 | 265 |
| 41 | `build_autonomous_trigger_contract.py` | 253 | 253 |
| 42 | `maintain_knowledge_engine.py` | 253 | 253 |
| 43 | `map_evidence_to_syllabus.py` | 250 | 250 |
| 44 | `create_snapshot.py` | 249 | 249 |
| 45 | `kb.py` | 245 | 245 |
| 46 | `link_parallel_book_sources.py` | 245 | 245 |
| 47 | `doctor_ocr_env.py` | 243 | 243 |
| 48 | `build_closed_loop_operations.py` | 242 | 242 |
| 49 | `build_long_horizon_operations.py` | 237 | 237 |
| 50 | `build_weekly_orchestration.py` | 233 | 233 |
| 51 | `build_refinement_queue.py` | 228 | 228 |
| 52 | `import_syllabus.py` | 226 | 226 |
| 53 | `rebuild_learning_cards.py` | 226 | 226 |
| 54 | `build_review_followups.py` | 220 | 220 |
| 55 | `build_daily_study_card.py` | 218 | 218 |
| 56 | `build_autonomous_action_plan.py` | 216 | 216 |
| 57 | `build_run_manifest_artifact.py` | 216 | 216 |
| 58 | `build_tutoring_feedback_loop.py` | 216 | 216 |
| 59 | `run_manager.py` | 208 | 208 |
| 60 | `build_tutoring_strategy_packet.py` | 206 | 206 |
| 61 | `rebuild_kb_layer.py` | 205 | 205 |
| 62 | `sync_all_chapter_knowledge.py` | 205 | 205 |
| 63 | `build_coaching_feedback_loop.py` | 204 | 204 |
| 64 | `lint_kb_entities.py` | 203 | 203 |
| 65 | `review_evidence.py` | 200 | 200 |

后续建议顺序：

1. 继续 query 的检索命中/回退、来源引用组装和召回编排；每块复用现有包模块，保留 Python API 与门禁测试。
2. answer 的契约与教学呈现（仍有正式调用者），再迁 common 的配置/路径、实体契约等职责；兼容导出随调用者迁移逐步收紧。
3. 章节构建、PDF/照片证据发布、习题发布与检索客户端等 500+ 行文件，优先沿已有 domain/storage 实现抽取，逐条核验来源、页码与发布行为。
4. 其余 200–499 行入口按命令组收敛到 cli + domain；同步审计已不足 200 行的脚本是否仅做转发。最后再验收所有顶层文件 `<200` 且仅转发。

### 本步验证与验收边界

测试在 `/tmp/issue19-review` 的隔离源码副本运行，仅复制 scripts/tests/schemas/references/profiles 与项目文档；不复制本机配置、真实 `.kaoyan-kb` 或工作区 `tmp/`。执行前清除继承的 `KAOYAN_*` 环境变量，临时夹具写入 `/tmp/issue19-*-tests`。不使用 git 来枚举或构造副本。

```bash
/tmp/issue-test-venv/bin/python -m pytest -q -p no:cacheprovider --basetemp=/tmp/issue19-after-tests tests/unit/test_{query_components,query_grounding,answer_chain,page_locator,exercise_batch,page_query_preflight,query_auto_sync,query_readonly_and_artifact_rollback,source_targets,search_index_status,refactor_helpers,concurrent_storage,runtime_config}.py
```

改前同组旧测试：**323 passed, 4 failed**；改后含新增模块测试：**336 passed, 4 failed**。新增 13 个用例全部通过，失败项与改前完全相同，没有新增回归；本环境依赖可用，但本组测试仍未全绿。未修改这些既有测试或无关行为来掩盖失败。

| 既有失败（测试名） | 改前/改后原因 |
| --- | --- |
| `test_query_components.py::test_teaching_bundle_replaces_unique_relative_figure_with_verified_full_page` | Linux `Path.as_posix()` 保留 Windows 路径中的反斜线，断言要求正斜线。 |
| `test_query_components.py::test_teaching_bundle_lists_all_pages_when_image_to_page_mapping_is_ambiguous` | 同上，多图来源链接格式不符。 |
| `test_query_readonly_and_artifact_rollback.py::test_ask_shares_query_and_citation_reads_but_save_reads_fresh` | 测试的 `query_knowledge` 替身只接收位置参数，未接收现有 `recall_policy` 关键字。 |
| `test_query_readonly_and_artifact_rollback.py::test_retrieval_reuses_records_and_keeps_output` | 读取次数预期为 4，实测为 2；输出一致性断言已通过。 |

**原文“scripts/ 每个文件 <200 行且只做转发”的验收本次未达成。** 这是第一步：仍有上表 65 个文件、合计 26987 行达到或超过 200 行，query/answer/common 等仍有待迁出的实现；其余 50 个文件也未完成纯转发职责审计。common 未增长；未以重构为由修改门禁或放宽测试断言。

## 数据与隐私

- 原始资料保留在使用者自己的目录中，不通过移动原文件表达章节归属。
- `.kaoyan-kb/`、`.kaoyan-backups/`、本机配置和临时目录不会进入 Git。
- OCR 结果必须经过审核与证据门控，不能直接成为正式 claim。
- 远程 OCR 默认关闭；启用前请确认资料授权、隐私边界和费用预算。配置持久开启远程且月预算为 `0`（无限制）时，`doctor` 会明确警告用量与费用无上限。
- API key 只放在进程环境或系统密钥管理工具中，不写入配置、日志或 Markdown。

当前主要支持 Windows、本地 Obsidian vault 和中文考研资料。

## 许可证

本项目采用 [MIT License](LICENSE)。你可以使用、复制、修改和分发本项目，但需保留版权与许可声明。
