#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import subprocess

from answer_local_question import ANSWER_CONTRACT_VERSION, direct_conclusion, intuitive_explanation, next_steps, personalized_reminder
from common import default_vault_root_arg, normalize_context, preferred_python_executable, resolve_subject, run_utf8_subprocess, runtime_subprocess_env, sanitize_name
from query_local_knowledge import query_knowledge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--question", required=True)
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--printed-page", type=int)
    parser.add_argument("--saved-at")
    return parser.parse_args()


def saved_at_label(raw: str | None) -> str:
    return raw or datetime.now().strftime("%Y-%m-%d")


def script_path(name: str) -> Path:
    return Path(__file__).with_name(name)


def run_script(name: str, *args: str) -> None:
    run_utf8_subprocess(
        [preferred_python_executable(), str(script_path(name)), *args],
        command_label=f"python:{name}",
        check=True,
        env=runtime_subprocess_env(),
    )


def trim_question(question: str, limit: int = 20) -> str:
    return sanitize_name(question.strip().replace("/", " ").replace("\\", " ")[:limit]) or "问答"


def write_index(path: Path, title: str, notes: list[Path], vault_root: Path) -> None:
    lines = [f"# {title}", "", "## 已保存问答", ""]
    if notes:
        for note in notes:
            rel = note.relative_to(vault_root).with_suffix("")
            lines.append(f"- [[{rel.as_posix()}]]")
    else:
        lines.append("- 暂无记录。")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def norm(text: str) -> str:
    return "".join(str(text or "").strip().split()).lower()


def page_anchor_metadata(result: dict) -> dict[str, str]:
    anchor = result.get("page_anchor", {}) or {}
    requested_page = anchor.get("requested_page")
    evidence_id = str(anchor.get("matched_evidence_id", ""))
    if requested_page is None or not evidence_id:
        return {}
    reference = next((item for item in result.get("references", []) if item.get("evidence_id") == evidence_id), {})
    return {
        "printed_page": str(requested_page),
        "evidence_id": evidence_id,
        "image_span": str(reference.get("image_span", "")),
        "chunk_id": str(anchor.get("matched_chunk_id", "") or reference.get("chunk_id", "")),
    }


def render_note(result: dict) -> str:
    anchor = page_anchor_metadata(result)
    lines = [
        f"# {result['query']}",
        "",
        "## 保存定位",
        "",
        f"- 印刷页：{anchor.get('printed_page', '未指定')}",
        f"- 证据 ID：{anchor.get('evidence_id', '未指定')}",
        f"- 图片范围：{anchor.get('image_span', '未指定')}",
        f"- 分片 ID：{anchor.get('chunk_id', '未指定')}",
        "",
        "## 考纲定位",
        "",
    ]
    if result["syllabus_route"]:
        for item in result["syllabus_route"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
    else:
        lines.append("- 当前没有稳定命中考纲节点。")
    lines.extend(
        [
            "",
            "## 直接结论",
            "",
            direct_conclusion(result),
            "",
            "## 直观解释",
            "",
            intuitive_explanation(result),
            "",
            "## 个性化提醒",
            "",
            personalized_reminder(result),
            "",
            "## 下一步建议",
            "",
        ]
    )
    for line in next_steps(result):
        lines.append(f"- {line}")
    lines.extend(["", "## 来源引用", ""])
    if result["references"]:
        for ref in result["references"]:
            lines.append(f"- {ref['title']} | 页段 {ref['page_span']} | 图片 {ref['image_span']} | chunk {ref['chunk_id']}")
    else:
        lines.append("- 当前没有稳定证据引用。")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    subject, config = resolve_subject(args.subject)
    result = query_knowledge(vault_root, subject, args.chapter, args.question, args.topk, args.printed_page)
    if args.printed_page is not None:
        anchor = page_anchor_metadata(result)
        if not anchor:
            raise SystemExit(f"[ERROR] printed page {args.printed_page} was not uniquely resolved; no saved-QA write was made")
    subject_root = vault_root / config["dir"]
    qa_root = subject_root / "00_课程入口" / "10_问答沉淀"
    chapter_slug = sanitize_name(args.chapter or result["chapter"] or "未分章")
    chapter_dir = qa_root / chapter_slug
    chapter_dir.mkdir(parents=True, exist_ok=True)
    note_path = chapter_dir / f"{saved_at_label(args.saved_at)}_{trim_question(args.question)}.md"
    note_path.write_text(render_note(result), encoding="utf-8")

    chapter_index = chapter_dir / "00_本章问答入口.md"
    write_index(chapter_index, f"{args.chapter or result['chapter'] or '本章'}问答入口", sorted(path for path in chapter_dir.glob("*.md") if path.name != chapter_index.name), vault_root)
    subject_index = qa_root / "00_知识问答入口.md"
    write_index(subject_index, f"{subject}知识问答入口", sorted(path for path in qa_root.rglob("00_本章问答入口.md")), vault_root)

    context_path = None
    if result["references"]:
        layout_hint = result["evidence_hits"][0] if result.get("evidence_hits") else None
        if layout_hint:
            candidate = Path(layout_hint.get("context_json_path", ""))
            if candidate.exists():
                context_path = candidate
    if context_path is None and args.chapter:
        for candidate in vault_root.rglob("00_批次上下文.json"):
            payload = normalize_context(json.loads(candidate.read_text(encoding="utf-8")))
            chapter_title = str(payload.get("chapter_title", ""))
            if payload.get("subject") == subject and (
                norm(chapter_title) == norm(args.chapter)
                or norm(args.chapter) in norm(chapter_title)
                or norm(chapter_title) in norm(args.chapter)
            ):
                context_path = candidate
                break
    if context_path is not None:
        metadata = json.dumps(
            {
                "source_kind": "learner_safe_query_answer",
                "answer_contract_version": ANSWER_CONTRACT_VERSION,
                "intent": result["intent"],
                "answer_mode": result["answer_mode"],
                "citation_coverage_ok": result["answer_mode"] == "chapter_fallback" or bool(result["references"]),
                "syllabus_route": result["syllabus_route"],
                "references": result["references"],
            },
            ensure_ascii=False,
        )
        run_script(
            "apply_saved_qa_feedback.py",
            "--context-json",
            str(context_path),
            "--question",
            args.question,
            "--answer-metadata",
            metadata,
            "--saved-note",
            str(note_path),
            "--format",
            "quiet",
        )
        run_script("review_refinement_candidates.py", "--format", "quiet")
    print(str(subject_index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
