#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import INDEX_DIRNAME, default_vault_root_arg, ensure_learning_dirs, load_json

CONTEXT_JSON = "00_批次上下文.json"
AUDIT_JSON = "00_知识归纳状态.json"
REPORT_JSON = "knowledge_batch_audit.json"
REPORT_MD = "11_章节批次巡检总览.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject")
    parser.add_argument("--chapter")
    parser.add_argument("--mode", default="chapter-photo")
    parser.add_argument("--write-report", action="store_true")
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


def chapter_body_ready(batch_dir: Path) -> bool:
    chapter_body = ensure_learning_dirs(batch_dir)["chapter_notes"] / "01_章节整理正文.md"
    return chapter_body.exists() and bool(chapter_body.read_text(encoding="utf-8").strip())


def collect_batch_status(context_path: Path) -> dict[str, object]:
    context = load_json(context_path)
    batch_dir = context_path.parent
    audit_path = batch_dir / AUDIT_JSON
    audit = load_json(audit_path) if audit_path.exists() else {}
    question_index_path = ensure_learning_dirs(batch_dir)["question_index"] / "chapter_knowledge_index.json"
    question_index = load_json(question_index_path) if question_index_path.exists() else {}
    issues: list[str] = []
    if context.get("input_path_warning"):
        issues.append("输入目录与学科不一致")
    if not audit_path.exists():
        issues.append("缺少知识归纳巡检 JSON")
    if audit.get("pending_chunk_count", 0):
        issues.append(f"仍有 {audit['pending_chunk_count']} 个 chunk 未补齐")
    if not audit.get("chapter_index_ready", False):
        issues.append("缺少提问索引")
    if not chapter_body_ready(batch_dir):
        issues.append("章节正文未生成")
    if int(audit.get("card_count", 0)) == 0:
        issues.append("知识点卡片仍为空")
    if int(audit.get("question_prompt_count", 0)) == 0:
        issues.append("后续追问仍为空")
    if not audit.get("overview_ready", False):
        issues.append("章节总述未完成")
    return {
        "subject": context.get("subject", ""),
        "chapter_title": context.get("chapter_title", context.get("scope", "")),
        "batch_id": context.get("batch_id", ""),
        "quality_level": audit.get("quality_level", context.get("quality_level", "")),
        "knowledge_status": audit.get("knowledge_status", context.get("knowledge_status", "")),
        "chunk_total": int(audit.get("chunk_total", 0)),
        "ready_chunk_count": int(audit.get("ready_chunk_count", 0)),
        "pending_chunk_count": int(audit.get("pending_chunk_count", 0)),
        "card_count": int(audit.get("card_count", 0)),
        "question_prompt_count": int(audit.get("question_prompt_count", 0)),
        "learning_path_count": int(audit.get("learning_path_count", 0)),
        "priority_concept_count": int(audit.get("priority_concept_count", 0)),
        "priority_feedback_cards": question_index.get("priority_feedback_cards", []),
        "next_step": audit.get("next_step", context.get("next_step", "")),
        "input_path_warning": context.get("input_path_warning", ""),
        "issues": issues,
        "batch_dir": str(batch_dir),
    }


def summarize_overview(rows: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "total": len(rows),
        "high_quality": 0,
        "learning_ready": 0,
        "with_issues": 0,
        "with_pending_chunks": 0,
    }
    for row in rows:
        if row.get("quality_level") == "高质量成品":
            summary["high_quality"] += 1
        if row.get("quality_level") in {"高质量成品", "学习成品", "可提问"}:
            summary["learning_ready"] += 1
        if row.get("issues"):
            summary["with_issues"] += 1
        if int(row.get("pending_chunk_count", 0)) > 0:
            summary["with_pending_chunks"] += 1
    return summary


def render_text(rows: list[dict[str, object]], summary: dict[str, int]) -> str:
    lines = [
        "# 章节批次巡检总览",
        "",
        f"- 章节批次数：{summary['total']}",
        f"- 高质量成品：{summary['high_quality']}",
        f"- 已进入可学习状态：{summary['learning_ready']}",
        f"- 仍有结构性问题：{summary['with_issues']}",
        "",
    ]
    if not rows:
        lines.append("- 当前没有命中可巡检的章节图片批次。")
        return "\n".join(lines) + "\n"
    for row in rows:
        lines.extend(
            [
                f"## {row['subject']} - {row['chapter_title']}",
                "",
                f"- 质量层级：{row['quality_level'] or '待评估'}",
                f"- 知识归纳状态：{row['knowledge_status'] or '待补充'}",
                f"- chunk 进度：{row['ready_chunk_count']}/{row['chunk_total']}",
                f"- 知识点卡片：{row['card_count']}",
                f"- 可直接追问：{row['question_prompt_count']}",
                f"- 下一步：{row['next_step'] or '待补充'}",
            ]
        )
        if row["issues"]:
            lines.append("- 当前问题：")
            for issue in row["issues"]:
                lines.append(f"  - {issue}")
        else:
            lines.append("- 当前问题：无明显结构性缺口")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report(vault_root: Path, rows: list[dict[str, object]], summary: dict[str, int]) -> None:
    index_root = vault_root / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "batches": rows}
    (index_root / REPORT_JSON).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (index_root / REPORT_MD).write_text(render_text(rows, summary), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    vault_root = Path(args.vault_root)
    rows: list[dict[str, object]] = []
    for context_path in iter_contexts(vault_root):
        payload = load_json(context_path)
        if args.mode and payload.get("mode") != args.mode:
            continue
        if not matches_filter(payload.get("subject", ""), args.subject):
            continue
        if not matches_filter(payload.get("chapter_title", payload.get("scope", "")), args.chapter):
            continue
        rows.append(collect_batch_status(context_path))
    summary = summarize_overview(rows)
    if args.write_report:
        write_report(vault_root, rows, summary)
    if args.format == "json":
        print(json.dumps({"summary": summary, "batches": rows}, ensure_ascii=False, indent=2))
    else:
        print(render_text(rows, summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
