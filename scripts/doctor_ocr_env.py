#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from common import run_utf8_subprocess
from config import load_runtime_config

DEFAULT_GITIGNORE_ENTRIES = (
    ".venv/",
    ".env",
    ".env.*",
    ".kaoyan-kb/ocr/",
    "tmp/ocr/",
    "tmp/smoke/",
    "tmp/signed-urls/",
    "*.signed-url.txt",
)

DEPENDENCY_PATTERNS = (
    "pyproject.toml",
    "requirements*.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "setup.py",
    "setup.cfg",
)


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def api_key_presence(raw: str | None) -> str:
    return "present" if str(raw or "").strip() else "absent"


def collect_dependency_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in DEPENDENCY_PATTERNS:
        files.extend(sorted(root.glob(pattern)))
    return files


def collect_virtual_envs(root: Path) -> list[Path]:
    candidates = [root / ".venv", root / "venv", root / "env"]
    return [path for path in candidates if path.exists()]


def preferred_project_python(root: Path) -> Path:
    candidate = root / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate
    return Path(sys.executable).resolve()


def detect_package_manager(root: Path) -> str:
    files = {path.name for path in collect_dependency_files(root)}
    if "uv.lock" in files:
        return "uv"
    if "poetry.lock" in files:
        return "poetry"
    if "Pipfile" in files or "Pipfile.lock" in files:
        return "pipenv"
    if "pyproject.toml" in files:
        return "pyproject+pip"
    if any(name.startswith("requirements") and name.endswith(".txt") for name in files):
        return "requirements+pip"
    return "pip"


def discover_python_commands() -> list[dict[str, str]]:
    commands = []
    for name in ("py", "python"):
        resolved = shutil.which(name)
        commands.append({"command": name, "available": "yes" if resolved else "no", "path": resolved or ""})
    return commands


def run_python_version(python_executable: Path) -> str:
    completed = run_utf8_subprocess(
        [str(python_executable), "--version"],
        command_label="python:version",
        check=True,
    )
    return (completed.stdout or completed.stderr).strip()


def detect_mistralai(python_executable: Path) -> dict[str, str]:
    completed = run_utf8_subprocess(
        [
            str(python_executable),
            "-c",
            (
                "import json, importlib.metadata as m; "
                "from mistralai.client import Mistral; "
                "print(json.dumps({'importable': True, 'version': m.version('mistralai'), 'symbol': Mistral.__name__, 'import_path': 'mistralai.client'}))"
            ),
        ],
        command_label="python:mistralai-probe",
    )
    if completed.returncode != 0:
        return {"importable": "no", "version": "", "detail": completed.stderr.strip() or completed.stdout.strip()}
    payload = json.loads(completed.stdout.strip())
    return {
        "importable": "yes" if payload.get("importable") else "no",
        "version": str(payload.get("version", "")),
        "detail": f"{payload.get('import_path', '')}:{payload.get('symbol', '')}".strip(":"),
    }


def _ocr_runtime_snapshot(root: Path) -> dict[str, object]:
    runtime = load_runtime_config(default_workspace=str(root))
    return {
        "provider": runtime.ocr_provider,
        "model": runtime.ocr_model,
        "allow_remote_configured": runtime.ocr_allow_remote,
        "cache_root": str(runtime.ocr_cache_root),
        "config_path": str(runtime.config_path) if runtime.config_path else "",
    }


def _ocr_acceptance_report(root: Path, *, mistralai: dict[str, str], api_key_state: str, runtime_snapshot: dict[str, object]) -> dict[str, object]:
    gates = {
        "mistralai_importable": mistralai.get("importable") == "yes",
        "api_key_present": api_key_state == "present",
        "config_allow_remote": bool(runtime_snapshot.get("allow_remote_configured")),
        "cli_allow_remote_required": False,
        "cli_yes_required": False,
    }
    blocking_reasons: list[str] = []
    if not gates["mistralai_importable"]:
        blocking_reasons.append("mistralai package is not importable in the selected project python")
    if not gates["api_key_present"]:
        blocking_reasons.append("MISTRAL_API_KEY is absent from the current process environment")
    if not gates["config_allow_remote"]:
        blocking_reasons.append("KAOYAN_OCR_ALLOW_REMOTE is not enabled in the current runtime config")

    fixture_ocr_ready = gates["mistralai_importable"]
    live_smoke_ready = fixture_ocr_ready and gates["api_key_present"] and gates["config_allow_remote"]
    automation_allowed = live_smoke_ready
    return {
        "fixture_ocr_ready": fixture_ocr_ready,
        "live_smoke_ready": live_smoke_ready,
        "manual_only": not automation_allowed,
        "automation_allowed": automation_allowed,
        "blocking_reasons": blocking_reasons,
        "staging_root_candidates": [
            ".local-api-smoke/408-ch1",
            ".local-api-smoke/math-ch1",
        ],
        "live_smoke_command": ".\\.venv\\Scripts\\python.exe scripts\\kb.py book ocr --book-root <book-root> --format json",
        "gates": gates,
        "notes": [
            "When runtime config enables remote OCR, new book OCR runs default to the Mistral provider.",
            "MISTRAL_API_KEY must remain environment-only and must not be written to logs or artifacts.",
        ],
    }


def build_report(root: Path) -> dict:
    python_executable = preferred_project_python(root)
    mistralai = detect_mistralai(python_executable)
    api_key_state = api_key_presence(os.environ.get("MISTRAL_API_KEY"))
    runtime_snapshot = _ocr_runtime_snapshot(root)
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "shell": os.environ.get("COMSPEC") or os.environ.get("SHELL") or "",
        },
        "python_commands": discover_python_commands(),
        "selected_python": str(python_executable),
        "selected_python_version": run_python_version(python_executable),
        "virtual_envs": [str(path) for path in collect_virtual_envs(root)],
        "package_manager": detect_package_manager(root),
        "dependency_files": [str(path.relative_to(root)) for path in collect_dependency_files(root)],
        "mistralai": mistralai,
        "api_key_presence": api_key_state,
        "ocr_runtime": runtime_snapshot,
        "ocr_acceptance": _ocr_acceptance_report(
            root,
            mistralai=mistralai,
            api_key_state=api_key_state,
            runtime_snapshot=runtime_snapshot,
        ),
        "network_requests": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "text"), default="text")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    report = build_report(skill_root())
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"selected_python: {report['selected_python']}")
    print(f"selected_python_version: {report['selected_python_version']}")
    print(f"package_manager: {report['package_manager']}")
    print(f"mistralai_importable: {report['mistralai']['importable']}")
    print(f"mistralai_version: {report['mistralai']['version']}")
    print(f"api_key_presence: {report['api_key_presence']}")
    print(f"ocr_provider: {report['ocr_runtime']['provider']}")
    print(f"ocr_model: {report['ocr_runtime']['model']}")
    print(f"ocr_allow_remote_configured: {'yes' if report['ocr_runtime']['allow_remote_configured'] else 'no'}")
    print(f"fixture_ocr_ready: {'yes' if report['ocr_acceptance']['fixture_ocr_ready'] else 'no'}")
    print(f"live_smoke_ready: {'yes' if report['ocr_acceptance']['live_smoke_ready'] else 'no'}")
    print("network_requests: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
