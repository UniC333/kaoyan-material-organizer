# 教材问答

## 定位与调用

保留用户原话及已知 `subject`、`book_title`、`printed_page`、章节/节、`container_path`、`exercise_label`、`requested_option`，交由稳定入口解析，不新建路由状态。按页请求必须传学科；常规传原话与已知教材，仅用户明确指定严格页码时附加 `--printed-page`，不把范围压成单页。

字段来源优先级：本轮显式信息 > 会话中结构化确认的上下文 > 当前任务唯一明确的默认教材。默认须由 `book_resolution.source=current_task_default` 可审计，无法映射或多候选不能选书。追问携带已确认上下文；改页、改定理或改题立即替换旧定位，并重新调用，不复用旧答案。

核对 `request_resolution`、`book_resolution`、`textbook_location`、`page_anchor`；以正式 `container_path` 和 `location_key` 保持教材身份，口语数字/空格变体只用于匹配。缺题号仅接受 `request_resolution.exercise_resolution.status=inferred_unique`；歧义保留候选并问一个必要信息，不能按顺序、语义或选项字母猜题。

## 返回判定

先按入口检查运行配置，再看 `request_resolution.source_request_kind`、`page_verification` 与以下状态：

| 状态 | 可做什么 |
| --- | --- |
| `page_anchor.match_status=exact_evidence` | 页面有正式证据，仍须检查正文/答案 bundle |
| `exact_asset` | 原图已定位、正文未确认，不说成找不到页面 |
| `ambiguous` / `unmapped` / `not_found` | 报告未确认，不以邻页或同章命中替代 |
| `unavailable` | 报告配置/索引原因并停止教材讲解，询问是否修复 |
| 需要 `page_crosscheck` 但未 `confirmed` | 不输出教材结论，报告待核验原因 |

索引正常但未命中时，可检查正式映射与配置登记的原图目录；现有附件仍可用时先检查，不重复索取。原图定位不授予人工阅图或 OCR 的自动授权，遵循入口边界。

- `exercise`：执行入口的三项答案门禁；先给已确认原书答案，再按需要补充解释。保留书中变量、代码顺序和结构条件，不混合不同解法；推导与原书冲突时停止判断并请求核对答案原图。
- `page_content`：仅 exact 正文可归因到该页。`asset_only` / `blocked` 不输出或继承原文；在证据链可用且用户仍需帮助时，可作明确标注的独立概念补充，不继承原页、题号、变量或原文身份，不用于解被阻塞的来源题。
- `generic`：来源绑定的检索使用 `effective_book_title` 同书过滤；书名不明、无可发布证据或主题不匹配时保持 `answer_mode=unconfirmed`。可保存结论须 `generic_answer_bundle.status=exact`，且主题相关、同书和引用覆盖均为真；blocked bundle 不生成结论、解释正文或引用。无教材来源信号的独立概念问答不强加此定位流程，也不冒充教材或保存为正式事实。

比较定理或例题须分别核验相关正文/答案。区分“本题可用”“可推出”“专门情形”与陈述等价，不能从同题可用推出等价。

## 批量与可视化

多个王道 408 练习使用一次批量 `ask`，不在对话层手写逐题循环。页码范围只约束题目页，答案可在范围外；逐项检查 `items[]` 的答案门禁，已确认项可讲，阻塞项单独报告。其他多例题比较仍逐题核验。

按页回答简要说明定位与证据状态，其余结构随卡点选择。状态/指针变化优先用可用交互可视化技能，静态教材插图可用图像生成技能。交互演示核验初始、中间、最终状态；算法显示代码、关键变量/指针、结构、不变量及时间/空间复杂度，空结构或单元素改变行为时检查边界。

可视化默认临时；明确保存时走 `ask --save` 或 learner，保留来源、页码及解法标识。若问题经修复或发布才能回答，最终重放用户原始自然语言请求核验，不能以覆盖率替代。
