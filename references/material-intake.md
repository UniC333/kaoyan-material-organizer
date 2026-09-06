# 资料接入与发布

通过技能根目录的稳定 CLI 工作，参数查 `kb.py --help` 及子命令帮助；环境故障用 `doctor`，安装配置见 [README](../README.md)。

## 来源与恢复

顺序为 `inspect -> map-pages -> OCR -> review -> classify -> publish -> query/ask`。原图只读，不以移动文件表达分类；OCR 置信度不能代替章节分类置信度或人工审核。人工文件、复核结果与考纲不自动覆盖，未审核 OCR 不进入正式 evidence/claim。

`pdf_page`（物理页序）与 `printed_page`（印刷页）独立保存，即使相等也须正式映射确认。映射必须逐页可审计，或由已核验端点约束连续区间，严格限定来源与版本；下游仅消费正式映射或人工复核 handoff。缺失、重复、冲突或不连续时保持 pending/unmapped 并停止范围发布，禁止从文件名、邻页或顺序猜测偏移。

预览并枚举写入的 OCR 状态、证据、清单与索引，建立小范围可审计恢复点。普通章节接入复用已有缓存与清单，不复制整个 Vault/知识库。迁移、批量删除/覆盖、无法枚举范围或用户明确要求时才扩大恢复范围。快照默认覆盖轻量可变状态，不复制 PDF、媒体、压缩包或可重建 OCR 缓存；全量媒体备份须用户明确要求。

## 事务与验收

- PDF 连续映射审批、目录分类及 `book publish-exercises`、`book ocr-publish`、`book pdf-ocr-publish` 默认零写预览；执行须同一计划的 `--yes --plan-fingerprint <fingerprint>`。预览不等于人工复核，习题预览不分配 evidence ID 或改写计数器。
- 执行重算输入指纹，输入漂移、页码索引缺失/过期/冲突或审核未通过即停止。事务内分配 ID、写 evidence 并刷新 page/exercise/search/book-series 索引；任何目标失败全部回滚。映射/分类也须对本次目标全体回滚。
- PDF 发布绑定注册 PDF SHA、当前 OCR 源图 SHA、request key、明确 `accepted` 的页复核及独立两套页码；`not-required` 不具发布资格。旧 `publish_full_pdf_ocr_evidence.py` 是失败兼容入口，不能绕行。
- 验收重放最初自然语言 `query/ask`，并用目标区间首尾印刷页确认返回物理页、印刷页和来源一致；不以索引计数或通用测试宣称问答完成。

已完成整本 OCR 但敏感公式待复核时，可在获授权的接入/修复中按需激活：唯一定位本题页、答案页及必要续页，对照原图复核修正后发布并重新 query/ask。不得批量接受未查看的公式块；回答仍须满足 [教材问答](textbook-qa.md) 门禁，不能因普通讲题自动启动本流程。

## 隐私与发布

远程 OCR 默认关闭，须明确允许外传；API key 仅进程环境读取。持久开启远程 OCR 且月预算为 0 时，doctor 必须提示无用量/费用上限。本地配置、知识库、快照、个人教材 profile 与考纲不得入公共仓库。

若任务包含 GitHub 推送，先获取远端最新默认分支并同步，在最终代码上重跑相关检查，确认提交范围和上游后再推送。
