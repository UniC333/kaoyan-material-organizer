#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import load_runtime_config


@dataclass(frozen=True)
class MigrationPaths:
    workspace_root: Path
    vault_root: Path
    migration_root: Path
    reports_root: Path
    snapshots_root: Path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing required migration file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid migration json: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"migration json must be an object: {path}")
    return payload


def _paths(args: argparse.Namespace) -> MigrationPaths:
    runtime = load_runtime_config()
    workspace_root = Path(args.workspace_root).expanduser() if args.workspace_root else runtime.workspace_root
    vault_root = Path(args.vault_root).expanduser() if args.vault_root else runtime.vault_root
    migration_root = Path(args.migration_root).expanduser() if args.migration_root else runtime.migration_root
    reports_root = migration_root / "reports"
    snapshots_root = migration_root / "snapshots"
    return MigrationPaths(
        workspace_root=workspace_root,
        vault_root=vault_root,
        migration_root=migration_root,
        reports_root=reports_root,
        snapshots_root=snapshots_root,
    )


def _classification_summary(plan: dict[str, Any]) -> dict[str, int]:
    entries = plan.get("entries", [])
    if not isinstance(entries, list):
        return {}
    return dict(sorted(Counter(str(item.get("classification", "")) for item in entries).items()))


def plan_payload(paths: MigrationPaths) -> dict[str, Any]:
    plan_path = paths.reports_root / "migration-plan.json"
    plan = _load_json(plan_path)
    return {
        "command": "plan",
        "executed": False,
        "mode": "dry-run",
        "workspace_root": str(paths.workspace_root),
        "vault_root": str(paths.vault_root),
        "migration_root": str(paths.migration_root),
        "plan_path": str(plan_path),
        "classification_summary": _classification_summary(plan),
        "conflict_count": int(plan.get("conflict_count", 0)),
        "next_required_step": "review plan, then run migrate-vault verify",
    }


def verify_payload(paths: MigrationPaths) -> dict[str, Any]:
    required_reports = [
        paths.reports_root / "windows-vault-inventory.json",
        paths.reports_root / "wsl-legacy-inventory.json",
        paths.reports_root / "skill-inventory.json",
        paths.reports_root / "path-reference-report.json",
        paths.reports_root / "migration-plan.json",
        paths.reports_root / "migration-plan.md",
        paths.reports_root / "conflict-index.md",
        paths.reports_root / "kb-migration-plan.md",
        paths.reports_root / "skill-relocation-plan.md",
    ]
    required_snapshots = [
        paths.snapshots_root / "windows-vault-snapshot.zip",
        paths.snapshots_root / "windows-vault-manifest.json",
        paths.snapshots_root / "windows-vault-snapshot.zip.sha256",
        paths.snapshots_root / "windows-vault-manifest.sha256",
        paths.snapshots_root / "wsl-legacy-snapshot.zip",
        paths.snapshots_root / "wsl-legacy-manifest.json",
        paths.snapshots_root / "wsl-legacy-snapshot.zip.sha256",
        paths.snapshots_root / "wsl-legacy-manifest.sha256",
        paths.snapshots_root / "skill-snapshot.zip",
        paths.snapshots_root / "skill-manifest.json",
        paths.snapshots_root / "skill-snapshot.zip.sha256",
        paths.snapshots_root / "skill-manifest.sha256",
    ]
    missing_reports = [str(path) for path in required_reports if not path.exists()]
    missing_snapshots = [str(path) for path in required_snapshots if not path.exists()]
    plan = _load_json(paths.reports_root / "migration-plan.json") if not missing_reports else {}
    ok = not missing_reports and not missing_snapshots and int(plan.get("conflict_count", 0)) == 0
    return {
        "command": "verify",
        "executed": False,
        "mode": "dry-run",
        "workspace_root": str(paths.workspace_root),
        "vault_root": str(paths.vault_root),
        "migration_root": str(paths.migration_root),
        "ok": ok,
        "missing_reports": missing_reports,
        "missing_snapshots": missing_snapshots,
        "conflict_count": int(plan.get("conflict_count", 0)) if plan else None,
        "classification_summary": _classification_summary(plan) if plan else {},
    }


def apply_payload(paths: MigrationPaths, *, yes: bool) -> dict[str, Any]:
    verification = verify_payload(paths)
    if not yes:
        return {
            "command": "apply",
            "executed": False,
            "mode": "dry-run",
            "ok": False,
            "reason": "apply requires explicit --yes",
            "verification": verification,
        }
    if not verification["ok"]:
        return {
            "command": "apply",
            "executed": False,
            "mode": "blocked",
            "ok": False,
            "reason": "verification failed; no changes applied",
            "verification": verification,
        }
    return {
        "command": "apply",
        "executed": False,
        "mode": "guarded-noop",
        "ok": True,
        "reason": "MIG-T04 implements the apply guard only; real apply is reserved for later approved migration tasks",
        "verification": verification,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root")
    parser.add_argument("--vault-root")
    parser.add_argument("--migration-root")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--format", choices=("json", "quiet"), default=argparse.SUPPRESS)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--format", choices=("json", "quiet"), default=argparse.SUPPRESS)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--yes", action="store_true")
    apply_parser.add_argument("--format", choices=("json", "quiet"), default=argparse.SUPPRESS)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args()
    paths = _paths(args)
    if args.command == "plan":
        payload = plan_payload(paths)
    elif args.command == "verify":
        payload = verify_payload(paths)
    elif args.command == "apply":
        payload = apply_payload(paths, yes=args.yes)
    else:
        parser.error("unsupported command")
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) is not False or args.command in {"plan", "apply"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
