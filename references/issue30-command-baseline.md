# issue #30：query / ask / sync 性能基线

本次只增加测量工具、测试和报告，没有修改生产命令编排或进行 in-process 重构。
原始逐次数据及进程启动记录见 [JSON 报告](issue30-command-baseline-linux.json)。

> 适用范围说明：本报告正文与 JSON 原始记录（含其中的 `--recall-mode` 参数）采自 2026-10-31 的工作目录测量环境，该环境含当时本地的召回模式参数；**本仓库交付树不含 `--recall-mode`，上述参数不代表本次交付状态**。历史原始记录予以保留、不改写；清理后交付状态的重测结果见文末“交付状态重测”一节。

## 环境与复现

- 采样时间：2026-10-31 05:32:33 UTC 开始。
- 本机 Linux VM，`Linux-7.0.0-39-generic-x86_64-with-glibc2.39`，可见逻辑 CPU 4 个。
- Python 3.12.3，解释器 `/tmp/issue-test-venv/bin/python`。
- 每条路径预热 1 次，然后计时 20 次；另做 20 次进程计数运行。
- 不与 pytest 并行运行基线；没有清空操作系统文件缓存或控制 VM 宿主机负载。

在仓库根目录运行（结果输出到 stdout，可另存 JSON）：

```bash
/tmp/issue-test-venv/bin/python -B scripts/benchmark_kb.py --samples 20 --warmups 1
```

工具使用集成测试的“临时工作区 + 显式配置 + 清除继承的 `KAOYAN_*`”方式，
在系统临时目录创建并自动清理一份合成知识库。Linux 固定使用系统 `/tmp`，不使用仓库的 `tmp/`。
所有存储根目录均明确指向夹具，Python 使用当前解释器，远程 OCR 关闭；
不发现、复制或读取真实 `.kaoyan-kb`，也不读取仓库的本机运行配置。

夹具只含 1 条已审核 PDF OCR 定义证据（`EV-BASELINE-1`），教材名“基线高等数学”，
PDF 第 2 页、印刷第 1 页；无学习事件、保存问答、实际教材文件或远程服务。
先通过一次 `sync --indexes-only` 准备索引，该准备步骤不计入样本。
query 和 ask 均指定该教材、学科“数学”和问题“什么是导数”，使用默认规则召回、JSON 输出，
不启用 `--auto-sync`；ask 不传 `--save`。每次运行均校验命中合成证据，ask 还校验未保存。
sync 校验 `indexes-only` 模式及四个构建步骤完成。

每次命令启动独立解释器；计时覆盖 `kb.py` 入口启动、导入、子进程及输出捕获，
不含夹具创建、预热、结果 JSON 解析与校验。`perf_counter` 计时，单位秒；
P95 使用最近秩法 `sorted(samples)[ceil(0.95*n)-1]`，20 次采样取第 19 小值。
工具参数支持修改采样数、预热次数及 query 阈值；参数不合法或命令失败即失败退出。

## 数据与阈值

以下子进程数均不计 `kb.py` 入口进程，也不计基线工具本身。

| 路径 | 计时样本数 | 均值（s） | 中位数（s） | P95（s） | 实测 Python 子进程数 |
| --- | ---: | ---: | ---: | ---: | --- |
| query | 20 | 0.280913 | 0.278315 | 0.302906 | 每次 1（20/20） |
| ask（不保存） | 20 | 0.291705 | 0.288922 | 0.313481 | 每次 1（20/20） |
| sync --indexes-only | 20 | 0.643830 | 0.630032 | 0.747490 | 每次 5（20/20） |

query P95 = **0.302906s < 2s**，没有触发本次建议阈值：**暂不启动 in-process 重构**。
该判定仅覆盖本机 Linux 最小夹具，不能据此判定真实大库或 Windows 性能。
后续如在目标环境、代表性数据规模下复测 query P95 > 2s，应记录后续优化建议，
先分析启动、导入和检索耗时，再决定是否需要 in-process 调用。本次没有创建 GitHub issue。

## 进程计数方法与原文纠正

计数不是对源码调用次数的静态推断：每次计时后，使用同一命令额外运行一次计数采样，
通过临时 `PYTHONPATH/sitecustomize.py` 在解释器启动时记录 PID、PPID、argv 和解释器路径。
读取独立的 PID 记录文件，核对以 `kb.py` 为根的父子树，再排除入口进程。
完整启动记录和每次计数保存在 JSON；计数插桩及文件写入开销没有混入计时样本。
计时和计数是相邻的两次执行，不能将某次计数说成对对应计时运行的操作系统追踪。

本次实际观测到：

- query：`kb.py → query_local_knowledge.py`，1 个子进程。
- ask（不保存）：`kb.py → ask_local_knowledge.py`，1 个子进程；该子进程内直接调用查询与答案函数，
  没有再启动 query / answer 脚本。
- sync：`kb.py → sync_exam_kb.py →` 四个构建器，合计 **5 个而非几十个**。
  构建器依次是 `build_page_locator_index.py`、`build_exercise_locator_index.py`、
  `build_search_index.py`、`build_book_series_indexes.py`。

计数范围是“正常加载 site、继承插桩环境的 Python 解释器启动”；它不是系统级进程跟踪器。
使用 `-S` / `-I`、替换环境变量或绕开 sitecustomize 的解释器可能漏计；
原生非 Python 进程、仅 fork 而不启动解释器的子进程也不在范围内。
当前三条指定路径使用普通 Python 启动并转交环境，符合本次方法的适用条件。
工具会拒绝缺失入口、没有子进程记录或父子关系断裂的结果，但不能保证发现所有绕过插桩的情况。

对 issue 原文的限定与纠正：

| 原文说法 | 本次采用的事实 |
| --- | --- |
| 每个命令 spawn N 个子进程 | `kb.py.run_script` 每调用启动一个子进程；总数取决于命令路径。普通 query 和不保存 ask 各转发一次。 |
| sync 可能几十个进程 | 本次范围是成功完成的 `sync --indexes-only`，实测为 1 个 sync + 4 个构建器，共 5 个；未测完整 sync，不能外推。 |
| ask 会继续套 query / answer 子进程 | 不保存 ask 在子进程内直接调用函数；`ask --save` 有章节上下文时另有反馈和 refinement 脚本，数量依路径变化，本次未测保存。 |
| 没有任何性能数据 | 已有单次小样本报告，query 中也有 `route['elapsed_ms']` 统计。本次补齐的是三条指定路径、重复采样、逐次进程数及可复现夹具；没有读取仓库 tmp/ 中的历史报告。 |

## 局限与验证

- Linux VM 数据**无法验证 Windows 进程 spawn 开销**；本报告不提供 Windows 性能结论。
- 单证据最小夹具主要提供固定路径开销参考，不代表实际资料规模、磁盘、OCR、网络或完整教学工作流。
  没有做分阶段瓶颈归因，也不能把端到端耗时全部归因于 spawn。
- sync 数据是索引已存在、输入不变时的重复刷新，搜索构建器可走增量路径；不是冷启动完整构建数据。
- 每次 Python 进程是新的，但文件缓存处于预热状态；小样本 P95 仍有噪声，应跨环境重复测量。
- 原始 JSON 的临时配置路径是采样时的审计线索；夹具已自动删除，不要求复用这些路径。

新增冒烟测试用小采样实际跑通三条命令，校验输出结构、检索成功、逐次进程数及统计一致性，
另测 P95 最近秩算法、参数拒绝和缺失进程记录时失败。
相关 query / ask / sync 测试在隔离源码副本运行，不复制本机配置、真实知识库或仓库 `tmp/`。
执行目录为 `/tmp/issue30-test-source-rrbyderv`（隔离副本），清除继承的 `KAOYAN_*`、
`PYTHONPATH`、`PYTHONHOME`，设置 `PYTHONDONTWRITEBYTECODE=1` 和 `PYTHONUTF8=1`：

```bash
/tmp/issue-test-venv/bin/python -m pytest -p no:cacheprovider \
  --basetemp /tmp/issue30-related-tests -q \
  tests/integration/test_command_baseline.py \
  tests/integration/test_cross_cwd_cli.py \
  tests/integration/test_exact_page_query.py \
  tests/unit/test_query_components.py \
  tests/unit/test_query_readonly_and_artifact_rollback.py \
  tests/unit/test_answer_chain.py \
  tests/unit/test_page_locator.py \
  tests/unit/test_search_index_status.py
```

结果：**213 passed, 4 failed，15.63s**；其中新增基线测试 **6 passed, 0 failed**。
四项失败为：

| 测试（位于上述两个 query 单元测试文件） | 失败现象 |
| --- | --- |
| `test_teaching_bundle_replaces_unique_relative_figure_with_verified_full_page` | Linux 下 Windows 路径保留反斜杠，与预期正斜杠不符。 |
| `test_teaching_bundle_lists_all_pages_when_image_to_page_mapping_is_ambiguous` | 同上，涉及多页图片链接。 |
| `test_ask_shares_query_and_citation_reads_but_save_reads_fresh` | 旧 mock 仅接收位置参数，无法接收现有 `recall_policy` 关键字参数。 |
| `test_retrieval_reuses_records_and_keeps_output` | 读取次数实际为 2，旧断言要求 4。 |

为核实归属，在 `/tmp/issue30-before-source-22v4ny9t` 的另一个副本中移除本次新增诊断包、
CLI 和测试文件，使用同一解释器运行上述命令的原有七个测试文件
（省去 `test_command_baseline.py`，`--basetemp /tmp/issue30-before-tests`）。
结果 **207 passed, 4 failed，8.88s**，失败的测试与现象完全一致。
本次没有修复这些已有失败、改动对应断言或跳过失败测试。

最终在仓库根目录单独复测新增工具：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/issue-test-venv/bin/python -m pytest \
  -p no:cacheprovider tests/integration/test_command_baseline.py \
  --basetemp /tmp/issue30-final-smoke-tests -q
```

结果 **6 passed, 0 failed，5.91s**。

## 交付状态重测（分支 issue-queue-passed，#22 已撤出）

本次重测在清理后的交付副本上进行（只含基线收录文件与审定待交付文件，不含本机配置、缓存与真实知识库；清除继承的 `KAOYAN_*` 与 `PYTHONHOME`；解释器为已验证的 rapidocr venv，`PYTHONPATH=scripts`）。原始逐次数据见交付副本外的 `delivery-benchmark.json`（不在本次提交内）。

- 采样：2026-11-03T13:01:10Z 起，Linux-7.0.0-39-generic-x86_64-with-glibc2.39，Python 3.12.3，CPU 4 个；每条路径预热 1 次、计时 20 次。
- query：均值 0.243s，P95 0.263s，每次 1 个子进程。
- ask：均值 0.229s，P95 0.236s，每次 1 个子进程。
- sync --indexes-only：均值 0.583s，P95 0.610s，每次 5 个子进程。
- query 阈值 2.0s 未超标，结论仍为“暂不启动 in-process 重构”。
- 与 2026-10-31 原始报告的差异说明：原始数据采自工作目录测量环境，其子进程 argv 含 `--recall-mode rules`；交付树无 `--recall-mode` 参数，本次重测的 argv 不含该参数。原始记录予以保留、不改写；两组数字适用的源码状态不同，不宜直接等同，但量级与结论一致。
