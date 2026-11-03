"""Repeat isolated CLI measurements without changing production execution."""
from __future__ import annotations

import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from .baseline_fixture import BOOK_TITLE, EVIDENCE_ID, QUESTION, create_fixture
from .process_probe import LIMITATIONS, METHOD, probe_environment, read_process_tree

ENTRY = Path(__file__).resolve().parents[2] / "kb.py"
COMMANDS = {
    "query": ["query", "--subject", "数学", "--book-title", BOOK_TITLE, "--query", QUESTION, "--format", "json"],
    "ask": ["ask", "--subject", "数学", "--book-title", BOOK_TITLE, "--question", QUESTION, "--format", "json"],
    "sync_indexes_only": ["sync", "--indexes-only", "--format", "json"],
}


def summarize(values: list[float]) -> dict[str, float]:
    """P95 uses nearest rank: sorted(values)[ceil(0.95 * n) - 1]."""
    return {"mean_seconds": statistics.mean(values),
            "median_seconds": statistics.median(values),
            "p95_seconds": sorted(values)[math.ceil(0.95 * len(values)) - 1]}


def run_command(command: list[str], root: Path, env: dict[str, str]) -> tuple[float, dict]:
    started = perf_counter()
    completed = subprocess.run(command, cwd=root, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=120)
    elapsed = perf_counter() - started
    if completed.returncode or "Error in sitecustomize" in completed.stderr:
        raise RuntimeError(f"baseline command failed: {command}\n{completed.stderr}\n{completed.stdout}")
    return elapsed, json.loads(completed.stdout)


def validate_result(name: str, result: dict) -> None:
    if name == "sync_indexes_only":
        if result.get("mode") != "indexes-only" or len(result.get("steps", [])) != 4:
            raise RuntimeError("baseline sync did not complete all four index builders")
        return
    answer = result["answer"] if name == "ask" else result
    ids = {item.get("evidence_id") for item in answer.get("evidence_hits", [])}
    if EVIDENCE_ID not in ids or (name == "ask" and result.get("saved") is not False):
        raise RuntimeError(f"baseline {name} failed to retrieve fixture evidence without saving")


def run_baseline(samples: int = 20, warmups: int = 1, query_threshold: float = 2.0) -> dict:
    if samples < 1 or warmups < 0 or not math.isfinite(query_threshold) or query_threshold <= 0:
        raise ValueError("samples >= 1, warmups >= 0 and a finite positive threshold are required")
    report = {
        "schema_version": "command-baseline.v1",
        "environment": {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
                        "platform": platform.platform(), "python_version": platform.python_version(),
                        "python_executable": sys.executable, "cpu_count": os.cpu_count()},
        "method": {"samples_per_path": samples, "warmups_per_path": warmups,
                   "p95": "nearest-rank ceil(0.95*n)", "timing": "perf_counter around subprocess.run; no probe",
                   "process_count": METHOD, "process_count_limitations": LIMITATIONS,
                   "fixture": "one synthetic reviewed evidence; fresh indexes prepared before sampling",
                   "sync_state": "repeat rebuild of unchanged inputs; not full/cold rebuild"},
        "paths": {},
    }
    # On Linux explicitly use the system /tmp, never the repository's tmp/.
    with tempfile.TemporaryDirectory(prefix="kaoyan-kb-baseline-",
                                     dir="/tmp" if sys.platform.startswith("linux") else None) as temporary:
        root = Path(temporary)
        config, env = create_fixture(root)
        prefix = [sys.executable, str(ENTRY), "--config", str(config)]
        _, prepared = run_command([*prefix, *COMMANDS["sync_indexes_only"]], root, env)
        validate_result("sync_indexes_only", prepared)
        for name, arguments in COMMANDS.items():
            command = [*prefix, *arguments]
            for _ in range(warmups):
                _, result = run_command(command, root, env)
                validate_result(name, result)
            measurements = []
            for index in range(1, samples + 1):
                elapsed, result = run_command(command, root, env)
                validate_result(name, result)
                probe_dir = root / f"probe-{name}-{index}"
                _, probed = run_command(command, root, probe_environment(probe_dir, env))
                validate_result(name, probed)
                measurements.append({"sample": index, "elapsed_seconds": elapsed,
                                     **read_process_tree(probe_dir)})
            counts = [sample["python_subprocesses"] for sample in measurements]
            report["paths"][name] = {
                "arguments": arguments, "samples": measurements,
                "statistics": summarize([sample["elapsed_seconds"] for sample in measurements]),
                "python_subprocesses": {"min": min(counts), "max": max(counts),
                                        "per_sample": counts},
            }
    exceeded = report["paths"]["query"]["statistics"]["p95_seconds"] > query_threshold
    report["query_threshold"] = {
        "seconds": query_threshold, "exceeded": exceeded,
        "conclusion": "建议后续优化调查（仅记录，不创建 issue）" if exceeded else "暂不启动 in-process 重构",
    }
    return report
