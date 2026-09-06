# 学习收束

仅明确“记录、整理、收束、同步进度、总结学习、今天结束、收工”等学习写入意图触发；普通进度陈述与讲解不触发扫描，也不在对话结束后自行运行。

先执行 SKILL.md 的主控读取与状态优先分流。配置 Vault 的主控协议负责扫描范围、正文读取、回退、写入顺序与报告；本文件仅说明 CLI 衔接，不建立第二套算法。

1. 按主控规则通过正式任务接口发现候选、分页读取正文、冻结扫描上界，将最小必要内容存为临时 `conversation-scan-manifest.v1`。
2. 运行 `learner closure validate --manifest-json <path> --vault-root <vault_root> --format json`，仅按 `conversation-closure-decision.v1` 给出的写入/水位线资格继续。正式列表截断可用同分组的 `probe-local-indexes`、`discover-local` 只读核验；未知 schema、缺表/字段或读取不完整须失败关闭。
3. 读取并去重当前任务、相关锚点与实际日期周记录，按主控顺序写入。只记录用户确认的进度、接受的理解或明确卡点，不把标题、摘要、助手建议、未验证教材事实、施工结果或推测当成学习事实。
4. 仅一个清晰、确认、可复用主概念适合 `learner distill`，命令查其 `--help`。保留原题与补充内容关系、来源类型、掌握状态和下次续接，不新增未接受的自测/任务。触发语已是本次写入授权，验证通过后无需重复确认。
5. 写入成功且覆盖资格成立后，分别判断本地与全局水位线并最后更新；任何缺口不得以推进水位线掩盖。结束删除本流程临时清单。

清单、判定和蒸馏候选分别遵循 `schemas/conversation_scan_manifest.schema.json`、`schemas/conversation_closure_decision.schema.json`、`schemas/conversation_distillation_candidate.schema.json`（均相对技能根目录）。学习反馈可为已讲解、跟随完成、独立写过、待验证；不据此升级教材 evidence/claim 或宣称掌握。

按主控格式报告扫描边界、扫描/纳入/跳过来源、列表截断、本地回退、云端/正文缺口、实际写入及水位线结果。
