#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import allocate_run_id, ensure_kb_layout, load_json, load_json_or_default, now_iso, save_json, stable_fingerprint

RUN_SCHEMA_VERSION = "1.0"
RUN_INDEX_JSON = "resume_index.json"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RESUMABLE_STATUSES = {"running", "paused", "failed"}
STEP_STATUSES = {"pending", "running", "completed", "failed", "skipped"}
FINAL_STATUSES = TERMINAL_STATUSES | {"running", "paused"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--run-type", required=True)
    start.add_argument("--resume-key", required=True)
    start.add_argument("--subject")
    start.add_argument("--metadata-json")
    start.add_argument("--format", choices=("json", "quiet"), default="json")

    step = subparsers.add_parser("step")
    step.add_argument("--run-id", required=True)
    step.add_argument("--step", required=True)
    step.add_argument("--status", choices=sorted(STEP_STATUSES), required=True)
    step.add_argument("--checkpoint-json")
    step.add_argument("--message", default="")
    step.add_argument("--format", choices=("json", "quiet"), default="json")

    finish = subparsers.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", choices=sorted(FINAL_STATUSES), required=True)
    finish.add_argument("--summary-json")
    finish.add_argument("--format", choices=("json", "quiet"), default="json")

    show = subparsers.add_parser("show")
    show.add_argument("--run-id", required=True)
    show.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _resume_lookup_key(run_type: str, resume_key: str, subject: str) -> str:
    return stable_fingerprint(
        {
            "run_type": str(run_type or "").strip(),
            "resume_key": str(resume_key or "").strip(),
            "subject": str(subject or "").strip(),
        }
    )


def _resume_index_path() -> Path:
    return ensure_kb_layout()["runs"] / RUN_INDEX_JSON


def _load_resume_index() -> dict[str, str]:
    return load_json_or_default(_resume_index_path(), {})


def _save_resume_index(payload: dict[str, str]) -> None:
    save_json(_resume_index_path(), payload)


def manifest_path(run_id: str) -> Path:
    return ensure_kb_layout()["runs"] / f"{run_id}.json"


def load_manifest(run_id: str) -> dict[str, Any]:
    path = manifest_path(run_id)
    if not path.exists():
        raise SystemExit(f"run not found: {run_id}")
    return load_json(path)


def save_manifest(payload: dict[str, Any]) -> None:
    save_json(manifest_path(str(payload["run_id"])), payload)


def start_run(*, run_type: str, resume_key: str, subject: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    lookup_key = _resume_lookup_key(run_type, resume_key, subject)
    resume_index = _load_resume_index()
    existing_run_id = str(resume_index.get(lookup_key, "")).strip()
    if existing_run_id:
        existing_manifest = load_manifest(existing_run_id)
        if str(existing_manifest.get("status", "")).strip() in RESUMABLE_STATUSES:
            existing_manifest["updated_at"] = now_iso()
            existing_manifest["resume_count"] = int(existing_manifest.get("resume_count", 0)) + 1
            save_manifest(existing_manifest)
            return {"created": False, "resumed": True, "run_id": existing_run_id, "manifest": existing_manifest}

    run_id = allocate_run_id()
    started_at = now_iso()
    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id,
        "run_type": str(run_type).strip(),
        "resume_key": str(resume_key).strip(),
        "resume_lookup_key": lookup_key,
        "subject": str(subject or "").strip(),
        "status": "running",
        "created_at": started_at,
        "updated_at": started_at,
        "started_at": started_at,
        "finished_at": "",
        "resume_count": 0,
        "metadata": dict(metadata or {}),
        "summary": {},
        "steps": {},
    }
    save_json(layout["runs"] / f"{run_id}.json", manifest)
    resume_index[lookup_key] = run_id
    _save_resume_index(resume_index)
    return {"created": True, "resumed": False, "run_id": run_id, "manifest": manifest}


def update_run_step(*, run_id: str, step_name: str, status: str, checkpoint: dict[str, Any] | None = None, message: str = "") -> dict[str, Any]:
    manifest = load_manifest(run_id)
    updated_at = now_iso()
    steps = dict(manifest.get("steps", {}))
    step_payload = dict(steps.get(step_name, {}))
    step_payload.update(
        {
            "step": step_name,
            "status": status,
            "updated_at": updated_at,
        }
    )
    if not step_payload.get("started_at"):
        step_payload["started_at"] = updated_at
    if status in {"completed", "failed", "skipped"}:
        step_payload["finished_at"] = updated_at
    if checkpoint is not None:
        step_payload["checkpoint"] = checkpoint
    if message:
        step_payload["message"] = message
    steps[step_name] = step_payload
    manifest["steps"] = steps
    manifest["updated_at"] = updated_at
    save_manifest(manifest)
    return {"updated": True, "run_id": run_id, "manifest": manifest}


def finish_run(*, run_id: str, status: str, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = load_manifest(run_id)
    updated_at = now_iso()
    manifest["status"] = status
    manifest["updated_at"] = updated_at
    manifest["finished_at"] = updated_at if status in TERMINAL_STATUSES else ""
    if summary is not None:
        manifest["summary"] = dict(summary)
    save_manifest(manifest)
    return {"updated": True, "run_id": run_id, "manifest": manifest}


def _print_payload(payload: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.command == "start":
        payload = start_run(
            run_type=args.run_type,
            resume_key=args.resume_key,
            subject=args.subject or "",
            metadata=json.loads(args.metadata_json) if args.metadata_json else {},
        )
        _print_payload(payload, args.format)
        return 0
    if args.command == "step":
        payload = update_run_step(
            run_id=args.run_id,
            step_name=args.step,
            status=args.status,
            checkpoint=json.loads(args.checkpoint_json) if args.checkpoint_json else None,
            message=args.message,
        )
        _print_payload(payload, args.format)
        return 0
    if args.command == "finish":
        payload = finish_run(
            run_id=args.run_id,
            status=args.status,
            summary=json.loads(args.summary_json) if args.summary_json else None,
        )
        _print_payload(payload, args.format)
        return 0
    if args.command == "show":
        _print_payload(load_manifest(args.run_id), args.format)
        return 0
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
