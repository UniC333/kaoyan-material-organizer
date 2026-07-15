#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from common import (
    default_vault_root_arg,
    ensure_learning_dirs,
    load_json,
    normalize_context,
    parse_manifest_table,
    preferred_python_executable,
    register_chapter_manifest,
    register_source_material,
    runtime_subprocess_env,
    save_json,
)

PLAN_JSON = "00_分片计划.json"
PLAN_MD = "01_分片总览.md"
ENTRY_PREFIX = "01_"
ENTRY_SUFFIX = "整理入口.md"
MANIFEST_MD = "00_章节图片清单.md"
STATUS_MD = "00_章节状态总览.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--chunk-plan-json")
    parser.add_argument("--max-images-per-chunk", type=int)
    parser.add_argument("--replan-chunks", action="store_true")
    parser.add_argument("--skip-global-registry", action="store_true")
    return parser.parse_args()


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> None:
    command = [preferred_python_executable(), str(script_path(name)), *args]
    subprocess.run(command, check=True, env=runtime_subprocess_env())


def build_entry_stem(chapter_title: str) -> str:
    match = re.match(r"^(第\s*[0-9一二三四五六七八九十百零两]+章?)", chapter_title.strip())
    if match:
        return match.group(1).replace(" ", "")
    head = chapter_title.strip().split()[0] if chapter_title.strip() else "章节"
    return re.sub(r'[\\/:*?"<>|]+', "", head) or "章节"


def entry_file_path(batch_dir: Path, chapter_title: str) -> Path:
    return batch_dir / f"{ENTRY_PREFIX}{build_entry_stem(chapter_title)}{ENTRY_SUFFIX}"


def manifest_image_paths(context: dict, batch_dir: Path) -> list[Path]:
    manifest_path = batch_dir / MANIFEST_MD
    material_root = Path(context["material_path"])
    if not manifest_path.exists():
        return []
    paths: list[Path] = []
    for row in parse_manifest_table(manifest_path):
        candidate = material_root / row["relative_path"]
        if candidate.exists():
            paths.append(candidate)
    return paths


def render_entry(context: dict, chunk_count: int, chapter_ready: bool) -> str:
    lines = [
        f"# {build_entry_stem(context['chapter_title'])}整理入口",
        "",
        f"- 先看：[00_章节图片清单.md](./{MANIFEST_MD})",
        f"- 再看：[10_分片计划/01_分片总览.md](./10_分片计划/{PLAN_MD})",
        f"- 当前状态：[00_章节状态总览.md](./{STATUS_MD})",
    ]
    if chapter_ready:
        lines.extend(
            [
                "- 章节正文：[01_章节整理正文.md](./20_章节整理/01_章节整理正文.md)",
                "- 提问入口：[03_知识点问答入口.md](./50_提问索引/03_知识点问答入口.md)",
            ]
        )
    lines.extend(
        [
            "",
            "## 当前阶段",
            "",
            f"- 当前已形成 {chunk_count} 个可继续补录的 chunk，后续直接维护 `30_片段提取/chunk-xxx.json`。",
        ]
    )
    if context.get("page_sequence_mode") == "ordered":
        start_page = context.get("start_page_number") or 1
        lines.append(f"- 这批图片已按文件顺序映射到真实书页，首张从第 {start_page} 页起顺延。")
    if context.get("input_path_warning"):
        lines.append(f"- 输入路径提示：{context['input_path_warning']}")
    if chapter_ready:
        lines.append("- 章节正文、知识点卡片和提问索引已生成，可继续回看或直接发起本地提问。")
    else:
        lines.append("- 当前还未形成完整章节整理，下一步先补片段提取内容，再刷新正文与索引。")
    lines.append("- 如果清单页码、小节或用途有改动，先重跑分片，再同步整章知识。")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    context_path = Path(args.context_json)
    context = normalize_context(load_json(context_path))
    batch_dir = context_path.parent
    dirs = ensure_learning_dirs(batch_dir)

    source_payload = register_source_material(
        subject=context["subject"],
        source_name=context.get("source_name", ""),
        material_type=context.get("mode", "chapter-photo"),
        material_path=Path(context["material_path"]),
        include_paths=manifest_image_paths(context, batch_dir) or None,
    )
    context["source_id"] = source_payload["source_id"]
    chapter_payload = register_chapter_manifest(context, source_payload=source_payload)
    context["chapter_id"] = chapter_payload["chapter_id"]
    save_json(context_path, context)

    chunk_plan_path = Path(args.chunk_plan_json) if args.chunk_plan_json else dirs["chunk_plan"] / PLAN_JSON
    manifest_path = batch_dir / MANIFEST_MD
    chapter_photo_mode = context.get("mode") == "chapter-photo"
    if chapter_photo_mode:
        run_script("autofill_manifest_hints.py", "--context-json", str(context_path))
        context = normalize_context(load_json(context_path))
    should_replan = args.replan_chunks or not chunk_plan_path.exists() or chapter_photo_mode
    if manifest_path.exists() and chunk_plan_path.exists():
        should_replan = should_replan or manifest_path.stat().st_mtime > chunk_plan_path.stat().st_mtime
    if should_replan:
        plan_args = ["--context-json", str(context_path)]
        if args.max_images_per_chunk:
            plan_args.extend(["--max-images-per-chunk", str(args.max_images_per_chunk)])
        run_script("plan_chapter_chunks.py", *plan_args)

    run_script("build_chapter_knowledge.py", "--context-json", str(context_path), "--chunk-plan-json", str(chunk_plan_path))
    run_script("refresh_chapter_index.py", "--context-json", str(context_path))
    run_script("rebuild_learning_cards.py", "--context-json", str(context_path), "--clean-generated")
    run_script("apply_saved_qa_feedback.py", "--context-json", str(context_path))
    run_script("audit_chapter_knowledge.py", "--context-json", str(context_path))
    run_script("extract_source_evidence.py", "--context-json", str(context_path))
    run_script("build_syllabus_registry.py", "--subject", context["subject"], "--yes")
    run_script("map_evidence_to_syllabus.py", "--subject", context["subject"], "--context-json", str(context_path))
    run_script("build_syllabus_coverage_report.py", "--subject", context["subject"])
    if not args.skip_global_registry:
        run_script("build_global_knowledge_registry.py")
        run_script("build_subject_course_index.py")
        run_script("audit_knowledge_batches.py", "--write-report")
        run_script("build_refinement_queue.py", "--format", "json")
        run_script("build_refinement_packs.py")
        run_script("build_master_card_drafts.py")
        run_script("build_learning_dashboard.py")

    plan_payload = load_json(chunk_plan_path)
    chapter_body = dirs["chapter_notes"] / "01_章节整理正文.md"
    chapter_ready = chapter_body.exists() and bool(chapter_body.read_text(encoding="utf-8").strip())
    entry_path = entry_file_path(batch_dir, context["chapter_title"])
    entry_path.write_text(render_entry(context, int(plan_payload.get("chunk_count", 0)), chapter_ready), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
