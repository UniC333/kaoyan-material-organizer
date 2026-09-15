#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
import subprocess

from answer_local_question import ANSWER_CONTRACT_VERSION, build_answer_contract, direct_conclusion, intuitive_explanation, next_steps, personalized_reminder
from common import default_vault_root_arg, normalize_context, preferred_python_executable, resolve_subject, run_utf8_subprocess, runtime_subprocess_env, sanitize_name
from config import load_runtime_config
from query_local_knowledge import query_knowledge
from kaoyan_kb.domain.query.source_revision import verify as verify_source_revisions
from kaoyan_kb.domain.query.read_session import read_scope


SAVEABLE_ANSWER_MODES = {"canonical_claim", "accepted_evidence", "exercise_pair"}
LEARNER_FEEDBACK_FILENAMES = (
    "learner_events.jsonl",
    "learner_model.json",
    "question_history.json",
    "error_log.json",
    "review_history.json",
    "review_schedule.json",
    "refinement_queue.json",
    "distillation_candidates.json",
)


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    contents: bytes | None


def learner_feedback_paths() -> tuple[Path, ...]:
    """Return only the learner files written by the two save feedback steps."""
    learner_root = Path(load_runtime_config().kb_root) / "learner"
    return tuple(learner_root / filename for filename in LEARNER_FEEDBACK_FILENAMES)


def transaction_paths(*paths: Path) -> tuple[Path, ...]:
    """Deduplicate the exact files that this save operation is allowed to restore."""
    return tuple(dict.fromkeys(Path(path) for path in paths))


def snapshot_files(paths: tuple[Path, ...]) -> tuple[FileSnapshot, ...]:
    """Read the enumerated files before any note, index, or feedback write."""
    snapshots: list[FileSnapshot] = []
    for path in paths:
        if path.exists():
            if not path.is_file():
                raise OSError(f"保存目标不是文件，无法建立回滚点: {path}")
            snapshots.append(FileSnapshot(path=path, contents=path.read_bytes()))
        else:
            snapshots.append(FileSnapshot(path=path, contents=None))
    return tuple(snapshots)


def missing_parent_dirs(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Remember only missing parents so a failed save can remove its empty directories."""
    missing: list[Path] = []
    for path in paths:
        parent = path.parent
        while not parent.exists():
            if parent not in missing:
                missing.append(parent)
            parent = parent.parent
    return tuple(missing)


def rollback_files(snapshots: tuple[FileSnapshot, ...], created_dirs: tuple[Path, ...]) -> None:
    """Restore exact bytes/existence and remove only directories created by this save."""
    errors: list[str] = []
    for snapshot in snapshots:
        try:
            if snapshot.contents is None:
                if snapshot.path.exists():
                    if not snapshot.path.is_file():
                        raise OSError("当前路径不是可删除的文件")
                    snapshot.path.unlink()
            else:
                snapshot.path.parent.mkdir(parents=True, exist_ok=True)
                snapshot.path.write_bytes(snapshot.contents)
        except OSError as exc:
            errors.append(f"{snapshot.path}: {exc}")

    for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
        try:
            if directory.exists() and directory.is_dir():
                directory.rmdir()
        except OSError as exc:
            errors.append(f"{directory}: {exc}")

    if errors:
        raise OSError("；".join(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", default=default_vault_root_arg())
    parser.add_argument("--subject", required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--book-title")
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


def save_eligibility(contract: dict) -> tuple[bool, str]:
    """Return before any write whether this answer may enter learner history."""
    from kaoyan_kb.domain.query.source_targets import is_multi
    from kaoyan_kb.domain.query.permission import teaching_permission
    if is_multi(contract):
        return False, "多目标结果不能保存为单条学习问答。"
    answer_mode = str(contract.get("answer_mode", ""))
    assessment = dict(contract.get("evidence_assessment") or {})
    grounding = dict(contract.get("answer_grounding") or {})
    request_kind = str((contract.get("request_resolution") or {}).get("source_request_kind") or "")
    if not request_kind and grounding.get("required"):
        request_kind = "exercise"
    elif not request_kind and "generic_answer_bundle" in contract:
        request_kind = "generic"
    if request_kind == "generic":
        generic = dict(contract.get("generic_answer_bundle") or {})
        if (
            str(generic.get("status") or "") != "exact"
            or not bool(generic.get("relevance_ok"))
            or not bool(generic.get("same_book_ok"))
            or not bool(generic.get("citation_coverage_ok"))
            or not generic.get("evidence_ids")
            or not generic.get("citations")
        ):
            return False, "通用问答未通过主题相关性、同书证据和引用覆盖门禁，不能保存。"
        if not bool(contract.get("citation_coverage_ok")):
            return False, "正式事实回答缺少完整引用，不能保存。"
    if grounding.get("required"):
        if grounding.get("status") != "exact_answer" or not bool(grounding.get("can_conclude")):
            return False, "原书答案尚未形成唯一可核验锚点，不能保存为学习问答。"
        if not bool(contract.get("citation_coverage_ok")):
            return False, "有来源题目必须同时引用原题和原书答案，当前引用不完整。"
        if not teaching_permission(contract):
            return False, "原题、答案、页码和引用尚未通过统一讲解门禁。"
    if answer_mode not in SAVEABLE_ANSWER_MODES:
        return False, "当前回答没有可保存的结构化证据；请先补齐教材映射或 OCR 后再保存。"
    if answer_mode in {"canonical_claim", "accepted_evidence"} and not bool(contract.get("citation_coverage_ok")):
        return False, "正式事实回答缺少完整引用，不能保存。"
    if assessment.get("level") in {
        "page_asset_only",
        "page_ambiguous",
        "page_unmapped",
        "page_not_found",
        "page_unavailable",
        "structured_unconfirmed",
        "chapter_summary",
        "unconfirmed",
    }:
        return False, "当前只能确认资料定位或检索边界，不能保存为学习问答。"
    return True, ""


def render_note(contract: dict) -> str:
    result = dict(contract["query_result"])
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
        f"- 依据级别：{contract['evidence_assessment']['level']}",
        f"- 原书答案门控：{(contract.get('answer_grounding') or {}).get('status', 'not_applicable')}",
        "",
        "## 内容来源",
        "",
    ]
    for item in contract.get("content_provenance", []):
        source_label = str(item.get("source_label", "")).strip()
        if not source_label:
            continue
        identity: list[str] = []
        if item.get("printed_page") is not None:
            identity.append(f"印刷页 {item['printed_page']}")
        if item.get("exercise_label"):
            identity.append(f"题号 {item['exercise_label']}")
        relation = f"；关联 {item['related_to']}" if item.get("related_to") else ""
        suffix = f"（{'，'.join(identity)}）" if identity else ""
        lines.append(f"- {source_label}{suffix}{relation}")
    lines.extend(["", "## 考纲定位", ""])
    if result["syllabus_route"]:
        for item in result["syllabus_route"]:
            lines.append(f"- {item['title']} (`{item['node_id']}`)")
    else:
        lines.append("- 当前没有稳定命中考纲节点。")
    lines.extend(
        [
            "",
            "## 证据边界",
            "",
            f"- 能确认：{contract['evidence_assessment']['can_confirm']}",
            f"- 不能确认：{contract['evidence_assessment']['cannot_confirm']}",
            f"- 下一步：{contract['evidence_assessment']['next_action']}",
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


def save_answer_contract(
    *,
    contract: dict,
    vault_root: Path,
    subject: str,
    chapter: str | None,
    question: str,
    saved_at: str | None,
) -> Path:
    allowed, reason = save_eligibility(contract)
    if not allowed:
        raise ValueError(reason)
    verify_source_revisions(contract)

    _, config = resolve_subject(subject)
    result = dict(contract["query_result"])
    subject_root = vault_root / config["dir"]
    qa_root = subject_root / "00_课程入口" / "10_问答沉淀"
    chapter_slug = sanitize_name(chapter or result["chapter"] or "未分章")
    chapter_dir = qa_root / chapter_slug
    note_path = chapter_dir / f"{saved_at_label(saved_at)}_{trim_question(question)}.md"

    chapter_index = chapter_dir / "00_本章问答入口.md"
    subject_index = qa_root / "00_知识问答入口.md"

    context_path = None
    if result["references"]:
        layout_hint = result["evidence_hits"][0] if result.get("evidence_hits") else None
        if layout_hint:
            candidate = Path(layout_hint.get("context_json_path", ""))
            if candidate.exists():
                context_path = candidate
    if context_path is None and chapter:
        for candidate in vault_root.rglob("00_批次上下文.json"):
            payload = normalize_context(json.loads(candidate.read_text(encoding="utf-8")))
            chapter_title = str(payload.get("chapter_title", ""))
            if payload.get("subject") == subject and (
                norm(chapter_title) == norm(chapter)
                or norm(chapter) in norm(chapter_title)
                or norm(chapter_title) in norm(chapter)
            ):
                context_path = candidate
                break

    feedback_paths = learner_feedback_paths() if context_path is not None else ()
    exact_paths = transaction_paths(note_path, chapter_index, subject_index, *feedback_paths)
    snapshots = snapshot_files(exact_paths)
    created_dirs = missing_parent_dirs(exact_paths)

    try:
        # All eligibility checks and the exact rollback manifest are complete before this point.
        chapter_dir.mkdir(parents=True, exist_ok=True)
        note_path.write_text(render_note(contract), encoding="utf-8")
        write_index(
            chapter_index,
            f"{chapter or result['chapter'] or '本章'}问答入口",
            sorted(path for path in chapter_dir.glob("*.md") if path.name != chapter_index.name),
            vault_root,
        )
        write_index(
            subject_index,
            f"{subject}知识问答入口",
            sorted(qa_root.rglob("00_本章问答入口.md")),
            vault_root,
        )

        if context_path is not None:
            metadata = json.dumps(
                {
                    "source_kind": "learner_safe_query_answer",
                    "answer_contract_version": contract["answer_contract_version"],
                    "intent": contract["intent"],
                    "answer_mode": contract["answer_mode"],
                    "citation_coverage_ok": contract["citation_coverage_ok"],
                    "syllabus_route": contract["syllabus_route"],
                    "references": contract["references"],
                },
                ensure_ascii=False,
            )
            run_script(
                "apply_saved_qa_feedback.py",
                "--context-json",
                str(context_path),
                "--question",
                question,
                "--answer-metadata",
                metadata,
                "--saved-note",
                str(note_path),
                "--format",
                "quiet",
            )
            run_script("review_refinement_candidates.py", "--format", "quiet")
    except Exception:
        try:
            rollback_files(snapshots, created_dirs)
        except Exception as rollback_error:
            raise RuntimeError("问答保存失败且回滚未完成") from rollback_error
        raise
    return subject_index


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root)
    subject, _ = resolve_subject(args.subject)
    with read_scope():
        result = query_knowledge(vault_root, subject, args.chapter, args.question, args.topk, args.printed_page, args.book_title)
        contract = build_answer_contract(result)
    try:
        subject_index = save_answer_contract(
            contract=contract,
            vault_root=vault_root,
            subject=subject,
            chapter=args.chapter,
            question=args.question,
            saved_at=args.saved_at,
        )
    except ValueError as exc:
        raise SystemExit(f"[ERROR] no saved-QA write was made: {exc}") from exc
    print(str(subject_index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
