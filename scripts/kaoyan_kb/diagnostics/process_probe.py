"""Count observed Python startups in a separate, instrumented command run."""
from __future__ import annotations

import json
import os
from pathlib import Path

METHOD = "sitecustomize startup records, PID/PPID tree; exclude kb.py entry"
LIMITATIONS = (
    "Counts Python interpreters that inherit PYTHONPATH and load sitecustomize; "
    "-S/-I, replaced environments, native processes and fork-only children are not covered. "
    "This is startup instrumentation, not an OS-wide process tracer. "
    "Counting runs are separate from uninstrumented timing runs."
)

# One file per PID avoids concurrent append races. Missing entry/child records
# or disconnected trees fail validation; bypassed hooks can still undercount
# descendants, as described in LIMITATIONS.
HOOK = '''import json, os, sys
from pathlib import Path
record = {"pid": os.getpid(), "ppid": os.getppid(),
          "argv": sys.argv, "executable": sys.executable}
destination = Path(os.environ["KB_BASELINE_PROCESS_LOG"]) / (str(os.getpid()) + ".json")
with destination.open("x", encoding="utf-8") as stream:
    json.dump(record, stream, ensure_ascii=False)
'''


def probe_environment(directory: Path, env: dict[str, str]) -> dict[str, str]:
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
    return {**env, "PYTHONPATH": str(directory), "KB_BASELINE_PROCESS_LOG": str(directory)}


def read_process_tree(directory: Path) -> dict:
    records = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted(directory.glob("*.json"))]
    entries = [record for record in records if record["ppid"] == os.getpid()
               and record["argv"] and Path(record["argv"][0]).name == "kb.py"]
    if len(entries) != 1:
        raise RuntimeError("process probe did not observe exactly one kb.py entry")
    entry_pid = entries[0]["pid"]
    observed = {entry_pid}
    while True:
        expanded = observed | {record["pid"] for record in records if record["ppid"] in observed}
        if expanded == observed:
            break
        observed = expanded
    if len(observed) != len(records) or len(records) < 2:
        raise RuntimeError("process probe found missing or disconnected Python startup records")
    return {"python_subprocesses": len(records) - 1, "entry_pid": entry_pid,
            "records": records}
