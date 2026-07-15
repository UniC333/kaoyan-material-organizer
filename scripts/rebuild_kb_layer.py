#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import (
    clear_kb_business_data,
    current_vault_root,
    ensure_kb_layout,
    iter_context_jsons,
    load_json,
    load_json_or_default,
    normalize_context,
    preferred_python_executable,
    register_chapter_manifest,
    register_source_material,
    resolve_subject,
    run_utf8_subprocess,
    runtime_subprocess_env,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> None:
    run_utf8_subprocess(
        [preferred_python_executable(), str(script_path(name)), *args],
        command_label=f"python:{name}",
        check=True,
        env=runtime_subprocess_env(),
    )


def create_backup() -> str:
    completed = run_utf8_subprocess(
        [preferred_python_executable(), str(script_path("create_snapshot.py")), "--format", "json"],
        command_label="python:create_snapshot.py",
        check=True,
        env=runtime_subprocess_env(),
    )
    payload = json.loads(completed.stdout)
    return str(payload.get("snapshot_id", ""))


def cleanup_subject_data(subjects: list[str], *, execute: bool) -> dict[str, int]:
    layout = ensure_kb_layout()
    if not subjects:
        return clear_kb_business_data(preserve_syllabus=False, preserve_learner=True) if execute else {"files": 0, "dirs": 0}
    wanted = {resolve_subject(item)[0] for item in subjects}
    removed = {"files": 0, "dirs": 0}
    for directory in (
        layout["manifest_sources"],
        layout["manifest_files"],
        layout["manifest_chapters"],
        layout["manifest_chunks"],
        layout["sources"],
        layout["evidence"],
        layout["claims"],
        layout["conflicts"],
    ):
        for path in sorted(directory.glob("*.json")):
            payload = load_json_or_default(path, {})
            if payload.get("subject") in wanted:
                removed["files"] += 1
                if execute:
                    path.unlink(missing_ok=True)
    for subject in wanted:
        for path in (
            layout["review_syllabus_mapping"] / f"{subject}.json",
            layout["syllabus"] / f"{subject}.json",
            layout["syllabus"] / f"{subject}.aliases.json",
        ):
            if path.exists():
                removed["files"] += 1
                if execute:
                    path.unlink(missing_ok=True)
    for path in sorted(layout["indexes"].glob("*.json")):
        removed["files"] += 1
        if execute:
            path.unlink(missing_ok=True)
    return removed


def manifest_image_paths(context: dict, batch_dir: Path) -> list[Path]:
    manifest_path = batch_dir / "00_章节图片清单.md"
    if not manifest_path.exists():
        return []
    material_root = Path(context["material_path"])
    paths: list[Path] = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 3 or parts[0] in {"序号", ""}:
            continue
        candidate = material_root / parts[2]
        if candidate.exists():
            paths.append(candidate)
    return paths


def discover_context_jsons(subjects: list[str] | None) -> list[Path]:
    return iter_context_jsons(subjects=subjects or None)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    execute = args.yes or args.force
    subjects = [] if args.all else args.subject
    ensure_kb_layout()
    contexts = discover_context_jsons(subjects or None)
    if not contexts:
        raise SystemExit("[ERROR] no context json found for rebuild")
    chapter_photo_contexts: list[Path] = []
    for path in contexts:
        payload = normalize_context(load_json(path))
        if payload.get("mode") != "chapter-photo":
            continue
        if not (path.parent / "00_章节图片清单.md").exists():
            continue
        chapter_photo_contexts.append(path)
    if not chapter_photo_contexts:
        raise SystemExit("[ERROR] no chapter-photo contexts with manifest found for rebuild")

    cleared = cleanup_subject_data(subjects, execute=False)
    preview = [
        {
            "subject": normalize_context(load_json(path)).get("subject", ""),
            "chapter_title": normalize_context(load_json(path)).get("chapter_title", ""),
            "context_json": str(path),
        }
        for path in chapter_photo_contexts
    ]
    backup_snapshot_id = ""
    if execute and not args.no_backup:
        backup_snapshot_id = create_backup()
    if execute:
        cleanup_subject_data(subjects, execute=True)
        registry_subject_args: list[str] = []
        for path in chapter_photo_contexts:
            registry_subject_args.extend(["--subject", normalize_context(load_json(path)).get("subject", "")])
        run_script("build_syllabus_registry.py", *registry_subject_args, "--yes", "--no-backup", "--format", "quiet")
        rebuilt: list[dict] = []
        for context_path in chapter_photo_contexts:
            context = normalize_context(load_json(context_path))
            batch_dir = context_path.parent
            include_paths = manifest_image_paths(context, batch_dir)
            source_payload = register_source_material(
                subject=context["subject"],
                source_name=context.get("source_name", ""),
                material_type=context.get("mode", "chapter-photo"),
                material_path=Path(context["material_path"]),
                include_paths=include_paths or None,
            )
            context["source_id"] = source_payload["source_id"]
            chapter_payload = register_chapter_manifest(context, source_payload=source_payload)
            context["chapter_id"] = chapter_payload["chapter_id"]
            run_script("plan_chapter_chunks.py", "--context-json", str(context_path))
            run_script("extract_source_evidence.py", "--context-json", str(context_path), "--format", "quiet")
            run_script("map_evidence_to_syllabus.py", "--subject", context["subject"], "--context-json", str(context_path), "--format", "quiet")
            rebuilt.append(
                {
                    "subject": context["subject"],
                    "chapter_title": context.get("chapter_title", ""),
                    "context_json": str(context_path),
                    "source_id": context["source_id"],
                    "chapter_id": context["chapter_id"],
                }
            )
    else:
        rebuilt = []
    if args.format == "json":
        print(
            json.dumps(
                {
                    "executed": execute,
                    "mode": "execute" if execute else "dry-run",
                    "backup_snapshot_id": backup_snapshot_id,
                    "cleared": cleared,
                    "count": len(rebuilt) if execute else len(preview),
                    "items": rebuilt if execute else preview,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
