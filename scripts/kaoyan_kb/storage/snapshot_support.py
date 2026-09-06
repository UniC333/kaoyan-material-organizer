from __future__ import annotations

import json
from pathlib import Path
from common import preferred_python_executable, run_utf8_subprocess, runtime_subprocess_env


def create_backup(script: Path) -> str:
    completed = run_utf8_subprocess(
        [preferred_python_executable(), str(script), "--format", "json"],
        command_label="python:create_snapshot.py",
        check=True,
        env=runtime_subprocess_env(),
    )
    payload = json.loads(completed.stdout)
    return str(payload.get("snapshot_id", ""))
