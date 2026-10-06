from __future__ import annotations

from collections.abc import Callable
from common import validate_entity_contract
from kaoyan_kb.domain.exercise_batch import (
    BATCH_CONTRACT_VERSION, parse_exercise_batch_request,
)
from pathlib import Path
from typing import Any


def _batch_failure_text(reason: str) -> tuple[str, str]:
    messages = {
        "invalid-requested-option": ("用户选项不是 A、B、C、D。", "请更正该题所选选项后重试。"),
        "book-title-missing": ("未能唯一确定王道教材。", "请补充教材名称。"),
        "active-book-pdf-source-not-found": ("当前教材没有已启用的正式 PDF 来源。", "请先完成该教材资料接入与发布。"),
        "printed-page-range-not-uniquely-mapped": ("题目页范围没有形成唯一正式页码映射。", "请先核对教材版本及页码映射。"),
        "printed-page-range-not-contiguous": ("题目页范围的正式 PDF 映射不连续。", "请先复核该页码区间的正式映射。"),
        "printed-page-range-crosses-sources": ("题目页范围跨越了多个教材来源。", "请缩小范围或明确教材版本。"),
        "section-anchor-ambiguous": ("小节定位存在多个正式候选。", "请补充准确小节编号。"),
        "section-start-anchor-not-found": ("起始页未定位到正式习题标题锚点。", "请确认起始页或补充准确小节编号。"),
        "section-start-anchor-conflict": ("起始页与指定小节的正式锚点冲突。", "请核对小节编号与起始页。"),
        "exercise-outside-requested-range": ("该题号不在用户指定的练习范围内。", "请核对题号或练习范围。"),
        "exercise_locator_index_missing": ("正式习题关系索引不存在。", "请先运行 kb.py sync --indexes-only。"),
        "exercise_locator_index_stale": ("正式习题关系索引已过期。", "请先运行 kb.py sync --indexes-only。"),
        "exercise_locator_index_version_mismatch": ("正式习题关系索引版本过旧。", "请先运行 kb.py sync --indexes-only。"),
        "exercise-relation-ambiguous": ("该题存在多个正式题目—答案关系。", "请补充小节、题型或页码范围。"),
        "exercise-relation-needs-review": ("该题的题目—答案关系仍需人工复核。", "请先复核并发布该题关系。"),
        "exercise-relation-not-found": ("未找到该题的正式题目—答案关系。", "请先确认题号或发布对应关系。"),
        "book-source-ambiguous": ("同名教材命中了多个来源或版本。", "请明确教材版本或页码范围。"),
    }
    return messages.get(reason, ("该题当前无法唯一定位原题和原书答案。", "请补充教材、小节、题型或页码信息。"))


def _blocked_batch_item(target: dict[str, Any], *, book_title: str) -> dict[str, Any]:
    reason = str(target.get("reason") or "exercise-relation-not-found")
    failure_reason, next_action = _batch_failure_text(reason)
    label = str(target.get("exercise_label") or "")
    grounding = {
        "required": True,
        "status": "answer_unavailable" if "index" in reason or "page" in reason else "answer_ambiguous" if "ambiguous" in reason else "answer_not_found",
        "can_conclude": False,
        "problem": {},
        "solution": {},
        "failure_reason": failure_reason,
        "next_action": next_action,
    }
    return {
        "exercise_label": label,
        "requested_option": str(target.get("requested_option") or ""),
        "emphasis": bool(target.get("emphasis")),
        "status": "blocked",
        "relation_id": str((target.get("relation") or {}).get("relation_id") or ""),
        "textbook_location": {
            "status": "blocked",
            "exercise_label": label,
            "printed_pages": [],
            "question_printed_pages": [],
            "question_pdf_pages": [],
            "answer_printed_pages": [],
            "answer_pdf_pages": [],
        },
        "page_verification": {},
        "answer_grounding": grounding,
        "citation_coverage_ok": False,
        "teaching_bundle": {
            "status": "blocked",
            "problem_text": "",
            "source_answer_text": "",
            "requested_option": str(target.get("requested_option") or ""),
            "exercise_label": label,
            "citations": {
                "problem_evidence_ids": [],
                "solution_evidence_ids": [],
                "problem_printed_pages": [],
                "solution_printed_pages": [],
                "problem_source_image_paths": [],
                "solution_source_image_paths": [],
                "problem_pdf_pages": [],
                "solution_pdf_pages": [],
            },
            "failure_reason": failure_reason,
        },
        "book_title": book_title,
    }


def _project_batch_item(target: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    grounding = dict(result.get("answer_grounding") or {})
    teaching = dict(result.get("teaching_bundle") or {})
    from .permission import teaching_permission
    exact = teaching_permission(result)
    problem_ids = set((grounding.get("problem") or {}).get("evidence_ids", []) or [])
    solution_ids = set((grounding.get("solution") or {}).get("evidence_ids", []) or [])
    cited_ids = {str(item.get("evidence_id") or "") for item in result.get("references", []) or []}
    relation = dict(target.get("relation") or {})
    textbook_location = dict(result.get("textbook_location") or {})
    textbook_location.update(
        {
            "question_printed_pages": list(relation.get("question_printed_pages") or []),
            "question_pdf_pages": list(relation.get("question_pdf_pages") or []),
            "answer_printed_pages": list(relation.get("answer_printed_pages") or []),
            "answer_pdf_pages": list(relation.get("answer_pdf_pages") or []),
        }
    )
    return {
        "exercise_label": str(target.get("exercise_label") or ""),
        "requested_option": str(target.get("requested_option") or ""),
        "emphasis": bool(target.get("emphasis")),
        "status": "exact" if exact else "blocked",
        "relation_id": str(relation.get("relation_id") or (result.get("exercise_anchor") or {}).get("relation_id") or ""),
        "textbook_location": textbook_location,
        "page_verification": dict(result.get("page_verification") or {}),
        "answer_grounding": grounding,
        "citation_coverage_ok": bool(exact and problem_ids and solution_ids and problem_ids <= cited_ids and solution_ids <= cited_ids),
        "teaching_bundle": teaching,
        "book_title": str(result.get("book_title") or ""),
    }


def query_exercise_batch(
    vault_root: Path,
    subject: str,
    chapter: str | None,
    query: str,
    topk: int,
    book_title: str | None = None,
    printed_page: int | None = None,
    *,
    view: str = "query",
    query_one: Callable[..., dict[str, Any]],
    resolve_book: Callable[..., dict[str, str]],
    runtime_context: Callable[..., dict[str, Any]],
    resolve_targets: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    parsed = parse_exercise_batch_request(query)
    if not parsed.get("is_batch"):
        return None
    if printed_page is not None and not parsed.get("page_range"):
        parsed["page_range"] = {"start": int(printed_page), "end": int(printed_page), "semantics": "question_scope"}
    book_resolution = resolve_book(vault_root=vault_root, subject=subject, explicit_book_title=book_title, query=query)
    effective_book_title = str(book_resolution.get("book_title") or "")
    resolved = resolve_targets(
        subject=subject,
        book_title=effective_book_title,
        chapter=chapter,
        query=query,
        parsed=parsed,
    )
    section_root = str(resolved.get("section_root") or chapter or "")
    category = str(parsed.get("exercise_category") or "")
    category_label = "单项选择题" if category == "single-choice" else "综合应用题" if category == "comprehensive" else "练习题"
    items: list[dict[str, Any]] = []
    for target in resolved.get("targets", []) or []:
        if target.get("status") != "exact":
            items.append(_blocked_batch_item(target, book_title=effective_book_title))
            continue
        label = str(target.get("exercise_label") or "")
        requested_option = str(target.get("requested_option") or "")
        item_question = f"{effective_book_title} {section_root} {category_label} 第{int(label)}题"
        if requested_option:
            item_question += f" {requested_option}项"
        item_result = query_one(
            vault_root,
            subject,
            section_root or chapter,
            item_question,
            topk,
            int(target.get("printed_page")) if parsed.get("page_range") else None,
            effective_book_title,
            label,
        )
        items.append(_project_batch_item(target, item_result))
    exact_count = sum(item.get("status") == "exact" for item in items)
    blocked_count = len(items) - exact_count
    batch_status = "exact" if items and blocked_count == 0 else "partial" if exact_count else "blocked"
    payload = {
        "batch_contract_version": BATCH_CONTRACT_VERSION,
        "view": view,
        "subject": subject,
        "book_resolution": book_resolution,
        "runtime_context": runtime_context(vault_root_override=vault_root),
        "request_resolution": {
            "original_query": query,
            "source_request_kind": "exercise_batch",
            "book_title": effective_book_title,
            "chapter_input": str(chapter or ""),
            "section_root": section_root,
            "exercise_category": category,
            "page_range": dict(parsed.get("page_range") or {}),
            "page_start": dict(parsed.get("page_start") or {}),
            "page_start_resolution": dict(resolved.get("page_start_resolution") or {}),
            "exercise_range": dict(parsed.get("exercise_range") or {}),
            "source_id": str(resolved.get("source_id") or ""),
            "exercise_resolution": {
                "status": "explicit",
                "source": "query",
                "exercise_labels": [str(item.get("exercise_label") or "") for item in parsed.get("items", []) or []],
            },
        },
        "batch_status": batch_status,
        "summary": {"requested_count": len(items), "exact_count": exact_count, "blocked_count": blocked_count},
        "items": items,
    }
    validate_entity_contract("exercise_batch_view", payload)
    return payload
