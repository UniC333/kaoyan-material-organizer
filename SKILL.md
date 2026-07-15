---
name: kaoyan-material-organizer
description: 将考研教材、课件、讲义、截图、OCR、PDF 和学习记录整理为本地可追溯知识库，并通过统一入口提供检索、问答、复习和学习建议。适用于 Windows、Obsidian 和中文考研资料。
---

# Kaoyan Material Organizer

## 使用目标

帮助使用者把本地考研资料整理进以下稳定主链：

```text
原始来源 -> 证据 -> 考纲 -> claim -> canonical card -> query -> learner
```

所有机器路径、资料目录和运行策略都从使用者自己的 `kaoyan.config.json` 读取。不要假设仓库自带个人资料、教材 profile 或正式考纲定义。

## 首次使用

1. 将 `kaoyan.config.example.json` 复制为 `kaoyan.config.json`。
2. 配置本机的 `vault_root`、`kb_root`、`backup_root` 和 Python 路径。
3. 运行 `scripts/kb.py doctor` 检查环境。
4. 根据实际资料选择 `book`、`sync`、`query`、`ask` 或 `learner` 工作流。

仓库中的 Markdown 和 JSON 使用 UTF-8。在 PowerShell 中读取中文时显式使用 `Get-Content -Encoding utf8`；若显示异常，先运行 `scripts/kb.py doctor` 查看终端编码提示。

## 稳定入口

默认只通过 `scripts/kb.py` 调用功能：

- `doctor`：检查路径、Python、OCR 和终端编码。
- `book`：接入纸质书、PDF、页码映射、OCR、复核与分类。
- `sync`：同步证据、考纲、知识卡和学习层。
- `query`、`ask`：本地检索与带来源问答。
- `review`：处理证据、冲突和 refinement 队列。
- `learner`：生成学习记录、复习卡和辅导上下文。
- `maintain`：执行受控维护。
- `snapshot`、`run`、`migrate-vault`：快照、可恢复运行和资料库迁移。

底层脚本属于实现细节；只有在 `kb.py --help` 无对应入口时，才直接调用 README 明确列出的配置脚本。

## 资料处理边界

纸质教材按以下顺序处理：

```text
inspect -> map-pages -> OCR -> review -> classify
```

- 原图是只读可信源，不通过移动文件表达书籍或章节归属。
- OCR 输出在完成审核和证据门控前，不直接写入正式 evidence 或 claim。
- OCR 置信度不等于章节分类置信度。
- 人工文件、人工审核结果和人工考纲不得自动覆盖。
- 删除、迁移、批量发布等操作必须先预览范围，并保留受控恢复路径。

## 隐私规则

- 默认只处理使用者明确指定的本地资料目录。
- 远程 OCR 默认关闭；未经使用者明确允许，不向外部服务发送资料。
- API key 只从进程环境读取，不写入配置、日志或学习资料。
- 本地配置、知识库、快照、教材 profile 和考纲定义不得提交回公共仓库。
