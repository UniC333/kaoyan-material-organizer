#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common import default_vault_root_arg, ensure_learning_dirs, load_json, preferred_python_executable, run_utf8_subprocess, runtime_subprocess_env, vault_root_from_context_path

CONTEXT_JSON = "00_批次上下文.json"
MANIFEST_MD = "00_章节图片清单.md"
PLAN_JSON = "00_分片计划.json"
AUDIT_JSON = "00_知识归纳状态.json"
CHAPTER_BODY = "01_章节整理正文.md"
INDEX_JSON = "chapter_knowledge_index.json"
SAVED_QA_JSON = "saved_qa_registry.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--context-json", action="append", default=[])
    parser.add_argument("--batch-id", action="append", default=[])
    parser.add_argument("--mode", default="chapter-photo")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--replan-chunks", action="store_true")
    parser.add_argument("--max-images-per-chunk", type=int)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def matches_filter(value: str, expected: str | None) -> bool:
    if not expected:
        return True
    return normalize_text(expected) in normalize_text(value)


def iter_contexts(vault_root: Path) -> list[Path]:
    return sorted(vault_root.rglob(CONTEXT_JSON))


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def newest_mtime(paths: list[Path]) -> float:
    existing = [path.stat().st_mtime for path in paths if path.exists()]
    return max(existing) if existing else 0.0


def change_reasons(context_path: Path) -> list[str]:
    batch_dir = context_path.parent
    dirs = ensure_learning_dirs(batch_dir)
    manifest_path = batch_dir / MANIFEST_MD
    plan_path = dirs["chunk_plan"] / PLAN_JSON
    chunk_extract_paths = sorted(dirs["chunk_extracts"].glob("chunk-*.json"))
    chapter_body_path = dirs["chapter_notes"] / CHAPTER_BODY
    chapter_index_path = dirs["question_index"] / INDEX_JSON
    audit_path = batch_dir / AUDIT_JSON
    reasons: list[str] = []

    if not plan_path.exists():
        reasons.append("missing-chunk-plan")
    elif manifest_path.exists() and manifest_path.stat().st_mtime > plan_path.stat().st_mtime:
        reasons.append("manifest-newer-than-plan")

    if not chunk_extract_paths:
        reasons.append("missing-chunk-extracts")
    elif plan_path.exists() and plan_path.stat().st_mtime > newest_mtime(chunk_extract_paths):
        reasons.append("plan-newer-than-chunk-extracts")

    if not chapter_body_path.exists() or not chapter_body_path.read_text(encoding="utf-8").strip():
        reasons.append("missing-chapter-body")

    if not chapter_index_path.exists():
        reasons.append("missing-chapter-index")
    elif newest_mtime(chunk_extract_paths) > chapter_index_path.stat().st_mtime:
        reasons.append("chunk-extracts-newer-than-chapter-index")

    if not audit_path.exists():
        reasons.append("missing-audit")
    else:
        downstream_latest = newest_mtime([chapter_body_path, chapter_index_path])
        if downstream_latest > audit_path.stat().st_mtime:
            reasons.append("chapter-outputs-newer-than-audit")

    qa_registry_path = vault_root_from_context_path(context_path) / "99_索引与状态" / SAVED_QA_JSON
    if qa_registry_path.exists() and chapter_index_path.exists() and qa_registry_path.stat().st_mtime > chapter_index_path.stat().st_mtime:
        reasons.append("saved-qa-newer-than-chapter-index")

    return reasons


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> str:
    command = [preferred_python_executable(), str(script_path(name)), *args]
    completed = run_utf8_subprocess(command, command_label=f"python:{name}", check=True, env=runtime_subprocess_env())
    return completed.stdout.strip()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    vault_root = Path(args.vault_root)

    explicit_contexts = [Path(item) for item in args.context_json]
    if explicit_contexts:
        selected = unique_paths([path for path in explicit_contexts if path.exists()])
    else:
        selected = []
        batch_ids = set(args.batch_id)
        for context_path in iter_contexts(vault_root):
            payload = load_json(context_path)
            if args.mode and payload.get("mode") != args.mode:
                continue
            if not matches_filter(payload.get("subject", ""), args.subject):
                continue
            if not matches_filter(payload.get("chapter_title", payload.get("scope", "")), args.chapter):
                continue
            if batch_ids and payload.get("batch_id", "") not in batch_ids:
                continue
            if args.changed_only and not change_reasons(context_path):
                continue
            selected.append(context_path)

    results: list[dict[str, str]] = []
    for idx, context_path in enumerate(selected):
        payload = load_json(context_path)
        reasons = change_reasons(context_path) if args.changed_only else []
        sync_args = ["--context-json", str(context_path), "--skip-global-registry"]
        if args.replan_chunks:
            sync_args.append("--replan-chunks")
        if args.max_images_per_chunk:
            sync_args.extend(["--max-images-per-chunk", str(args.max_images_per_chunk)])
        run_script("sync_chapter_knowledge.py", *sync_args)
        context = load_json(context_path)
        results.append(
            {
                "subject": context.get("subject", ""),
                "chapter_title": context.get("chapter_title", context.get("scope", "")),
                "batch_id": context.get("batch_id", ""),
                "quality_level": context.get("quality_level", ""),
                "knowledge_status": context.get("knowledge_status", ""),
                "change_reasons": reasons,
            }
        )

    if selected:
        run_script("build_global_knowledge_registry.py")
        run_script("build_subject_course_index.py")
        run_script("audit_knowledge_batches.py", "--write-report")
        run_script("build_refinement_queue.py", "--format", "json")
        run_script("build_refinement_packs.py")
        run_script("build_master_card_drafts.py")
        run_script("build_learning_dashboard.py")

    if args.format == "json":
        print(json.dumps({"count": len(results), "chapters": results}, ensure_ascii=False, indent=2))
        return 0

    lines = [
        "# 批量章节同步结果",
        "",
        f"- 同步批次数：{len(results)}",
        f"- 筛选模式：{args.mode or '全部'}",
        "",
        f"- 增量模式：{'changed-only' if args.changed_only else 'all-matched'}",
        "",
    ]
    if results:
        for item in results:
            suffix = ""
            if item.get("change_reasons"):
                suffix = f"｜变更原因 {', '.join(item['change_reasons'])}"
            lines.append(
                f"- {item['subject']}｜{item['chapter_title']}｜{item.get('quality_level') or '待评估'}｜{item.get('knowledge_status') or '待补充'}{suffix}"
            )
    else:
        lines.append("- 当前没有命中可同步的章节批次。")
    print("\n".join(lines) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
