from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.diagnostics.command_baseline import run_baseline, summarize
from kaoyan_kb.diagnostics.process_probe import read_process_tree


def test_isolated_command_baseline_smoke(tmp_path: Path) -> None:
    # Inherited real-looking roots must never win over the synthetic fixture.
    forbidden = tmp_path / "must-not-be-created"
    env = {**os.environ, "KAOYAN_KB_ROOT": str(forbidden),
           "KAOYAN_CONFIG_FILE": str(forbidden / "missing.json"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "benchmark_kb.py"), "--samples", "2", "--warmups", "0"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["schema_version"] == "command-baseline.v1"
    assert report["method"]["samples_per_path"] == 2
    assert set(report["paths"]) == {"query", "ask", "sync_indexes_only"}
    for name, count in [("query", 1), ("ask", 1), ("sync_indexes_only", 5)]:
        path = report["paths"][name]
        assert path["python_subprocesses"] == {"min": count, "max": count, "per_sample": [count, count]}
        assert len(path["samples"]) == 2
        elapsed = [sample["elapsed_seconds"] for sample in path["samples"]]
        assert all(value > 0 for value in elapsed)
        assert path["statistics"] == summarize(elapsed)
        for sample in path["samples"]:
            assert len(sample["records"]) == count + 1
            assert sample["entry_pid"] in {record["pid"] for record in sample["records"]}
    query_p95 = report["paths"]["query"]["statistics"]["p95_seconds"]
    assert report["query_threshold"]["exceeded"] == (query_p95 > 2.0)
    if query_p95 <= 2.0:
        assert report["query_threshold"]["conclusion"] == "暂不启动 in-process 重构"
    assert not forbidden.exists()


def test_p95_uses_nearest_rank() -> None:
    assert summarize(list(range(1, 21))) == {
        "mean_seconds": 10.5, "median_seconds": 10.5, "p95_seconds": 19,
    }


@pytest.mark.parametrize("settings", [
    {"samples": 0}, {"warmups": -1}, {"query_threshold": float("nan")},
])
def test_invalid_settings_fail_before_creating_fixture(settings: dict) -> None:
    with pytest.raises(ValueError):
        run_baseline(**settings)


def test_missing_probe_records_are_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="exactly one kb.py entry"):
        read_process_tree(tmp_path)
