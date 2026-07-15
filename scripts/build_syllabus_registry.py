#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, now_iso, preferred_python_executable, resolve_subject, run_utf8_subprocess, runtime_subprocess_env, save_json


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DEFINITIONS_ROOT = SCRIPT_DIR.parent / "syllabus-definitions"
SUBJECT_FILE_PREFIX = {
    "数学": "math",
    "408": "408",
    "英语": "english",
    "政治": "politics",
}
DEFINITION_PRIORITY = ("manual", "official", "scaffold")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--definitions-root", default=str(DEFAULT_DEFINITIONS_ROOT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def alias_payload(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    for node in nodes:
        values = [node.get("title", ""), *node.get("aliases", []), *node.get("keywords", [])]
        seen: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.append(text)
        payload[str(node["node_id"])] = seen
    return payload


def script_path(name: str) -> Path:
    return SCRIPT_DIR / name


def create_backup() -> str:
    completed = run_utf8_subprocess(
        [preferred_python_executable(), str(script_path("create_snapshot.py")), "--format", "json"],
        command_label="python:create_snapshot.py",
        check=True,
        env=runtime_subprocess_env(),
    )
    payload = json.loads(completed.stdout)
    return str(payload.get("snapshot_id", ""))


def definitions_root_from_arg(raw: str | None) -> Path:
    return Path(raw) if raw else DEFAULT_DEFINITIONS_ROOT


def definition_candidates(subject: str, definitions_root: Path) -> list[Path]:
    prefix = SUBJECT_FILE_PREFIX[subject]
    return [definitions_root / f"{prefix}.{status}.json" for status in DEFINITION_PRIORITY]


def resolve_definition_path(subject: str, definitions_root: Path) -> tuple[Path, str]:
    for status, path in zip(DEFINITION_PRIORITY, definition_candidates(subject, definitions_root)):
        if path.exists():
            return path, status
    raise SystemExit(f"[ERROR] missing syllabus definition for {subject} under {definitions_root}")


def definition_payload(subject: str, definitions_root: Path) -> tuple[dict[str, Any], Path, str]:
    path, status = resolve_definition_path(subject, definitions_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("subject") != subject:
        raise SystemExit(f"[ERROR] syllabus definition subject mismatch for {path}: {payload.get('subject')} != {subject}")
    payload.setdefault("source_status", status)
    return payload, path, status


def tree_payload_from_definition(subject: str, payload: dict[str, Any]) -> dict[str, Any]:
    nodes = list(payload.get("nodes", []))
    return {
        "subject": subject,
        "definition_version": payload.get("definition_version", ""),
        "source_status": payload.get("source_status", "scaffold"),
        "mapping_overrides": list(payload.get("mapping_overrides", [])),
        "updated_at": now_iso(),
        "node_count": len(nodes),
        "nodes": nodes,
    }


def aliases_payload_from_definition(subject: str, payload: dict[str, Any]) -> dict[str, Any]:
    nodes = list(payload.get("nodes", []))
    return {
        "subject": subject,
        "definition_version": payload.get("definition_version", ""),
        "source_status": payload.get("source_status", "scaffold"),
        "mapping_overrides": list(payload.get("mapping_overrides", [])),
        "updated_at": now_iso(),
        "aliases": alias_payload(nodes),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    definitions_root = definitions_root_from_arg(args.definitions_root)
    subjects = args.subject or list(SUBJECT_FILE_PREFIX)
    execute = args.yes or args.force
    written: list[dict[str, Any]] = []

    for raw_subject in subjects:
        subject, _ = resolve_subject(raw_subject)
        payload, definition_path, status = definition_payload(subject, definitions_root)
        nodes = list(payload.get("nodes", []))
        tree_path = layout["syllabus"] / f"{subject}.json"
        aliases_path = layout["syllabus"] / f"{subject}.aliases.json"
        written.append(
            {
                "subject": subject,
                "node_count": len(nodes),
                "definition_version": payload.get("definition_version", ""),
                "source_status": status,
                "definition_path": str(definition_path),
                "tree_path": str(tree_path),
                "aliases_path": str(aliases_path),
            }
        )

    backup_snapshot_id = ""
    if execute and not args.no_backup:
        backup_snapshot_id = create_backup()

    if execute:
        for raw_subject in subjects:
            subject, _ = resolve_subject(raw_subject)
            payload, _, _ = definition_payload(subject, definitions_root)
            save_json(layout["syllabus"] / f"{subject}.json", tree_payload_from_definition(subject, payload))
            save_json(layout["syllabus"] / f"{subject}.aliases.json", aliases_payload_from_definition(subject, payload))

    if args.format == "json":
        print(
            json.dumps(
                {
                    "executed": execute,
                    "mode": "execute" if execute else "dry-run",
                    "backup_snapshot_id": backup_snapshot_id,
                    "count": len(written),
                    "items": written,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
