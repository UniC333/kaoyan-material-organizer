# 资料接入与发布

通过技能根目录的稳定 CLI 工作，参数查 `kb.py --help` 及子命令帮助；环境故障用 `doctor`，安装配置见 [README](../README.md)。

## 来源与恢复

顺序为 `inspect -> map-pages -> OCR -> review -> classify -> publish -> query/ask`。原图只读，不以移动文件表达分类；OCR 置信度不能代替章节分类置信度或人工审核。人工文件、复核结果与考纲不自动覆盖，未审核 OCR 不进入正式 evidence/claim。

`pdf_page`（物理页序）与 `printed_page`（印刷页）独立保存，即使相等也须正式映射确认。映射必须逐页可审计，或由已核验端点约束连续区间，严格限定来源与版本；下游仅消费正式映射或人工复核 handoff。缺失、重复、冲突或不连续时保持 pending/unmapped 并停止范围发布，禁止从文件名、邻页或顺序猜测偏移。

预览并枚举写入的 OCR 状态、证据、清单与索引，建立小范围可审计恢复点。普通章节接入复用已有缓存与清单，不复制整个 Vault/知识库。迁移、批量删除/覆盖、无法枚举范围或用户明确要求时才扩大恢复范围。快照默认覆盖轻量可变状态，不复制 PDF、媒体、压缩包或可重建 OCR 缓存；全量媒体备份须用户明确要求。

## 无 API key 的接入

官方首选 `rapidocr` 本地 CPU provider：`python -m pip install -e ".[ocr-local]"` 安装引擎及随包 ONNX 模型，配置
`ocr_provider=rapidocr`、`ocr_model=rapidocr-onnxruntime-1.4.4-bundled`、`ocr_allow_remote=false`，
或在 `book ocr` / `book ocr-pdf-source` 中使用 `--provider rapidocr`。
推理不需要任何 API key，不外传原图。行文字、坐标和行置信度进入现有 raw/normalized 缓存；页码映射、复核、分类、发布和问答验收全部保留。
本地引擎不专门识别公式、表格结构或页眉页脚；这些内容必须人工逐项核对。fixture 只返回预制数据或文件名，不能作为 OCR 替代品。

若本地引擎不可用，可采用正式等价的 **AI 视觉直读转录** 路径。等价范围是“从原图得到待复核转录并进入同一发布链路”，并不意味着 AI 输出已经通过审核。
此路线仅适用于用户已经授权的资料接入；普通问答中临时阅图仍按 SKILL.md 的受限辅助规则执行，不触发入库。
视觉模型可能运行于远程服务；不需要本工具的 API key 不代表数据不外传，使用前仍需遵守明确授权外传的规则。`vision` provider 本身只读取本地转录，不发起网络请求。

1. 完成 inspect 与正式页码映射。逐页实际查看原图，忠实转录；保留公式 LaTeX、代码换行和表格内容，无法辨认处明确标记并在复核时解决。不得根据文件名或预期答案补全文字。
2. 在原图旁新建 `<完整图片文件名>.vision.json`（如 `page.png.vision.json`），不修改原图。格式如下；`source_file_sha256` 用 `common.sha256_for_file` 或 SHA-256 工具计算，`exact_model` 填实际阅图模型标识，`text` 填阅图得到的正文：

   ```json
   {
     "provider": "vision",
     "exact_model": "<实际视觉模型标识>",
     "source_file_sha256": "<该张原图的 SHA-256>",
     "text": "<逐页阅图转录正文>"
   }
   ```

3. 保持 `ocr_include_blocks=true`，执行 `book ocr --book-root <book-root> --provider vision`。单图诊断可用 README 列出的 `ocr_document.py --file <image> --provider vision`，但单图诊断不会注册教材或替代 book 状态。PDF 经 `book ocr-pdf-source` 渲染：先获得实际渲染页图，在它旁边生成转录，再以相同 DPI、范围和 `--provider vision` 重跑；哈希不匹配立即停止。
4. 转录导入现有 raw/normalized/run 缓存，并记录 `provider=vision`、实际模型名、原图哈希及转录哈希。转录哈希参与 request key；修改转录后必须重跑并重新复核，旧缓存与旧审核不能复用。导入时所有块置信度为未知（0），自动进入 low-confidence 复核队列；转录文件中的审核标记不生效。
5. PDF 先执行 `book pdf-ocr-review-status` 生成审核桥接，并使用其返回的 `book_root` 作为后续块审核的 `--book-root`；照片使用原教材的 `book_root`。然后通过 `book ocr-review queue --book-root <book-root>` 枚举审核块。人工逐页对照原图后，使用 `book ocr-review apply --request-key <key> --block-id OCRBLK-001-001 --review-status accepted --corrected-text <核对后正文>` 记录复核。照片转录每块须明确 accepted；PDF 还必须执行 `book pdf-ocr-page-review`，独立确认 `pdf_page`、`printed_page`、request key 和源图哈希。分类及正式映射仍走原有入口，禁止相等回退或猜测。
6. 照片用 `book ocr-publish`，PDF 在上述桥接、块审核及页审核完成后，用 `book pdf-ocr-review-artifact -> pdf-ocr-publish`。先预览，再以同一指纹执行；缺映射、缺审核或输入变化仍会阻断。最后重放原始 `query/ask`，交叉核验目标区间首尾的两套页码及来源。

`.vision.json` 是人工/AI 转录输入，不是第二份学习状态或索引；消费者为 vision provider 与发布前的一致性校验。每张图片一份，最大 1 MiB，不自动覆盖、不自动清理；按资料输入保留，审核/发布记录引用期间不得移除。转录或原图变化以哈希失效，重建须重新阅图、导入和复核；raw/normalized/overlay 继续使用原有 OCR 缓存。转录及相关私有产物不得进入公共仓库。

## 事务与验收

- PDF 连续映射审批、目录分类及 `book publish-exercises`、`book ocr-publish`、`book pdf-ocr-publish` 默认零写预览；执行须同一计划的 `--yes --plan-fingerprint <fingerprint>`。预览不等于人工复核，习题预览不分配 evidence ID 或改写计数器。
- 执行重算输入指纹，输入漂移、页码索引缺失/过期/冲突或审核未通过即停止。事务内分配 ID、写 evidence 并刷新 page/exercise/search/book-series 索引；任何目标失败全部回滚。映射/分类也须对本次目标全体回滚。
- PDF 发布绑定注册 PDF SHA、当前 OCR 源图 SHA、request key、明确 `accepted` 的页复核及独立两套页码；`not-required` 不具发布资格。旧 `publish_full_pdf_ocr_evidence.py` 是失败兼容入口，不能绕行。
- 验收重放最初自然语言 `query/ask`，并用目标区间首尾印刷页确认返回物理页、印刷页和来源一致；不以索引计数或通用测试宣称问答完成。

已完成整本 OCR 但敏感公式待复核时，可在获授权的接入/修复中按需激活：唯一定位本题页、答案页及必要续页，对照原图复核修正后发布并重新 query/ask。不得批量接受未查看的公式块；回答仍须满足 [教材问答](textbook-qa.md) 门禁，不能因普通讲题自动启动本流程。

## 隐私与发布

远程 OCR 默认关闭，须明确允许外传；API key 仅进程环境读取。持久开启远程 OCR 且月预算为 0 时，doctor 必须提示无用量/费用上限。本地配置、知识库、快照、个人教材 profile 与考纲不得入公共仓库。

若任务包含 GitHub 推送，先获取远端最新默认分支并同步，在最终代码上重跑相关检查，确认提交范围和上游后再推送。
