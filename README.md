# Kaoyan Material Organizer - 考研资料整理

一个面向 Windows、Obsidian 与本地资料的考研知识库 skill。它把教材、讲义、课件、题解、PDF、照片和学习记录整理为可追溯的知识链，并提供本地检索、问答、复习与个性化学习建议。

```text
原始来源 -> 证据 -> 考纲 -> claim -> canonical card -> query -> learner
```

## 主要能力

- 注册教材、PDF 与纸质书照片，并保留来源和内容哈希。
- 按 `inspect -> map-pages -> OCR -> review -> classify` 处理纸质教材。
- 将证据关联到考纲、知识点和可复用知识卡。
- 在本地知识库中检索和提问，输出带来源的回答。
- 根据学习记录、错题和薄弱点生成复习与学习建议。
- 在迁移或批量处理前创建快照，并支持受控恢复。

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

## 数据与隐私

- 原始资料保留在使用者自己的目录中，不通过移动原文件表达章节归属。
- `.kaoyan-kb/`、`.kaoyan-backups/`、本机配置和临时目录不会进入 Git。
- OCR 结果必须经过审核与证据门控，不能直接成为正式 claim。
- 远程 OCR 默认关闭；启用前请确认资料授权、隐私边界和费用预算。
- API key 只放在进程环境或系统密钥管理工具中，不写入配置、日志或 Markdown。

当前主要支持 Windows、本地 Obsidian vault 和中文考研资料。
