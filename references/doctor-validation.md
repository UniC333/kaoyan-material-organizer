# doctor 校验与维护

## 临时文件清理

```text
python scripts/kb.py --config <kaoyan.config.json> doctor --prune-tmp --format json
python scripts/kb.py --config <kaoyan.config.json> doctor --prune-tmp --format text
```

采用显式 `--prune-tmp` 策略；不带开关的 doctor 保留原诊断行为，sync 启动不清理，以保持原同步行为。清理模式直接使用选定配置，不运行普通 doctor 的 schema 初始化和 OCR 诊断，不刷新索引。不能与只读 validate 合用（组合时退出 2，零清理）。

扫描所选 `kb_root` 的直属文件，以及 `common.kb_layout()` 定义的目录（manifests、sources、evidence、syllabus、claims、conflicts、indexes、learner、runs、review-queues、schemas，含子目录）。配置的 `ocr_cache_root` 仅在 KB 内时额外扫描，含 raw/normalized/indexes/overlays；与下文 JSON 校验的缓存排除规则不同。其他目录（如 backups、snapshots、history）、整个 Vault、外部考纲/缓存和媒体不在扫描范围。目录链接不跟随，候选文件、目标或锁文件为链接时保留，防止越界。

代码显式枚举两个命名模式，不盲删任意 `*.tmp`：

- `atomic_io._atomic_write_text`：`.<目标文件名>.<PID>.<UUID>.tmp`，PID 为正整数，UUID 为 32 位小写十六进制字符。
- `publication_support.FileTransaction.restore`：`.<目标文件名>.restore.tmp`。

使用 #26 `file_lock(target, timeout=0)` 非阻塞取得 `<target>.lock` 的操作系统锁，并在检查和删除期间一直持有；锁被占用则以 `target-lock-held` 报告跳过。不能用锁文件存在、mtime、临时文件年龄或 PID 单独证明安全。锁释放后仍保留稳定 sidecar，不删除、不替换它；若原来没有该文件，会按既有协议创建每目标一个空锁文件，后续写入和清理长期复用。

原子写入并非全部采用目标锁，所以还必须由操作系统确认创建者 PID 已退出；PID 存活（包括复用）或查询失败均跳过，避免删除没有采用锁的活动写入。Windows 使用只读进程句柄检查退出状态，不发送终止信号。固定 `.restore.tmp` 没有 PID，且恢复写入未采用目标锁，无法证明孤儿状态；即使锁空闲也以 `restore-writer-uncoordinated; orphan-status-unknown` 保留并报告，人工清理时，先确认所有相关写入进程均已退出，再逐项核验文件确为恢复残留而非正在进行的写入，确认后逐项删除 `.restore.tmp` 文件；保留正式目标文件和稳定的 `.lock` 文件。这里不改变发布、恢复或证据逻辑。

JSON 报告含 `kb_root`、`deleted_count`、`skipped_count`、`items`；每项有 `path`、`status`（`deleted`/`skipped`）、`reason`。文本同样逐项输出。删除成功后才计入 `deleted_count`；未知命名、持锁、进程存活/无法确认、恢复文件、链接、扫描/删除失败均报告跳过，目录跳过也计入 `skipped_count`。没有候选时也输出零计数。清理本身不保存报告或另建持久化状态。

人工验收步骤（用测试 KB，或确认配置确实指向预期 KB）：

1. 将下面的 `<kb_root>` 替换为该配置的知识库目录，用 Python 执行，手动造一个创建者已退出的孤儿文件：

   ```python
   import subprocess
   import sys
   from pathlib import Path

   creator = subprocess.Popen([sys.executable, "-c", "pass"])
   creator.wait()
   directory = Path(r"<kb_root>") / "indexes"
   directory.mkdir(parents=True, exist_ok=True)
   temporary = directory / f".issue32-check.json.{creator.pid}.{'a' * 32}.tmp"
   temporary.write_text("orphan test", encoding="utf-8")
   print(temporary)
   ```

2. 执行上面的 `doctor --prune-tmp` 命令。核对样例路径为 `deleted`，原因为 `target-lock-acquired; creator-pid-exited`，文件实际消失，`deleted_count` 等于实际删除数（库内另有孤儿时一并计数）。PID 恰被复用时会保守跳过，可重新造样例。
3. 再执行一次；无新增残留时 `deleted_count=0`。正式目标不被创建/改写，空 `.lock` 保留。再放一个 `notes.tmp` 验证 `unrecognized-temp-name` 跳过；由另一进程用 `file_lock(target)` 持锁时，对应候选应以 `target-lock-held` 跳过，内容不变。
