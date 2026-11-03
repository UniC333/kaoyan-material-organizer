"""Textbook evidence gates and teaching payloads built from resolved records.

These helpers do not retrieve or persist records. The query entry orchestrates
lookup; publication eligibility remains owned by evidence_publication.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kaoyan_kb.domain.book_series import classify_source_request
from kaoyan_kb.domain.evidence_publication import is_publishable_source_evidence


def build_answer_grounding(
    *,
    query: str,
    book_title: str | None,
    page_anchor: dict[str, Any],
    exercise_anchor: dict[str, Any],
    evidences: list[dict[str, Any]],
    page_crosscheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_kind = classify_source_request(
        query=query,
        book_title=book_title,
        page_anchor=page_anchor,
        exercise_label=str(exercise_anchor.get("exercise_label") or ""),
    )
    required = request_kind == "exercise"
    grounding = {
        "required": required,
        "status": "answer_not_found" if required else "not_applicable",
        "can_conclude": not required,
        "problem": {},
        "solution": {},
        "failure_reason": "尚未定位原书题解。" if required else "",
        "next_action": "请补充教材名、页码或题号后重新定位原书答案。" if required else "",
    }
    if not required:
        return grounding
    crosscheck = dict(page_crosscheck or {})
    if crosscheck.get("required") and crosscheck.get("status") != "confirmed":
        crosscheck_status = str(crosscheck.get("status") or "unverified")
        if crosscheck_status in {"conflict", "ambiguous"}:
            grounding.update(
                status="answer_ambiguous",
                failure_reason=str(crosscheck.get("reason") or "页码线索与小节标题锚点冲突。"),
                next_action="请核对教材版本、小节编号或起始页后重新检索。",
            )
        else:
            grounding.update(
                status="answer_unavailable",
                failure_reason=str(crosscheck.get("reason") or "页码线索尚未完成小节锚点核验。"),
                next_action="请先补齐正式页码映射或小节标题锚点。",
            )
        return grounding
    page_status = str(page_anchor.get("match_status") or "")
    if page_status == "unavailable":
        grounding.update(status="answer_unavailable", failure_reason="本地证据链当前不可用。", next_action="请先修复配置或正式页码索引。")
        return grounding
    if page_status == "exact_asset":
        grounding.update(status="answer_asset_only", failure_reason="只定位到原题图片，原书答案正文未确认。", next_action="如需继续，请明确允许人工核对答案原图，或先发布题解证据。")
        return grounding
    if page_status == "ambiguous":
        grounding.update(status="answer_ambiguous", failure_reason="原题页存在多个教材候选。", next_action="请先确认教材名称。")
        return grounding
    if exercise_anchor.get("status") == "unavailable":
        grounding.update(
            status="answer_unavailable",
            failure_reason=f"习题关系索引当前不可用（{exercise_anchor.get('reason') or 'exercise_locator_index_unavailable'}）。",
            next_action="请先运行 kb.py sync --indexes-only 重建习题关系索引。",
        )
        return grounding
    if exercise_anchor.get("status") == "ambiguous":
        if exercise_anchor.get("reason") == "exercise-label-missing":
            candidates = "、".join(str(item) for item in exercise_anchor.get("candidate_labels", []) if str(item))
            suffix = f"；本页候选题号为 {candidates}" if candidates else ""
            grounding.update(
                status="answer_ambiguous",
                failure_reason=f"教材和页码已确认，但当前描述不足以唯一确定题目{suffix}。",
                next_action="目前只缺题号，请告诉我是第几题。",
            )
        else:
            grounding.update(status="answer_ambiguous", failure_reason="题目与原书答案的关系存在歧义。", next_action="请先审核题目—题解关系。")
        return grounding
    if exercise_anchor.get("status") != "exact_answer_evidence":
        if exercise_anchor.get("reason") == "exercise-label-missing":
            grounding.update(
                failure_reason="教材和页码已确认，但当前描述不足以唯一确定题目。",
                next_action="目前只缺题号，请告诉我是第几题。",
            )
        elif exercise_anchor.get("reason") == "page-exercise-relations-not-found":
            grounding.update(
                failure_reason="教材原页已确认，但该页没有可用于答案配对的正式题目关系。",
                next_action="请补充准确题号；若仍无法定位，再审核该页题目—题解关系。",
            )
        return grounding

    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in evidences}
    question_ids = [str(item) for item in exercise_anchor.get("question_evidence_ids", []) if str(item)]
    answer_ids = [str(item) for item in exercise_anchor.get("answer_evidence_ids", []) if str(item)]
    problem_content = str(exercise_anchor.get("question_content") or "").strip() or "\n".join(str(evidence_by_id[item].get("content") or "") for item in question_ids if item in evidence_by_id).strip()
    answer_content = str(exercise_anchor.get("answer_content") or "").strip() or "\n".join(str(evidence_by_id[item].get("content") or "") for item in answer_ids if item in evidence_by_id).strip()
    grounding["problem"] = {
        "evidence_ids": question_ids,
        "book_title": str(book_title or page_anchor.get("book_title") or ""),
        "exercise_label": str(exercise_anchor.get("exercise_label") or ""),
        "printed_pages": list(exercise_anchor.get("question_printed_pages", []) or []),
        "pdf_pages": list(exercise_anchor.get("question_pdf_pages", []) or []),
        "source_image_paths": list(exercise_anchor.get("question_source_image_paths", []) or []),
        "content": problem_content,
    }
    grounding["solution"] = {
        "evidence_ids": answer_ids,
        "book_title": str(book_title or page_anchor.get("book_title") or ""),
        "exercise_label": str(exercise_anchor.get("exercise_label") or ""),
        "printed_pages": list(exercise_anchor.get("answer_printed_pages", []) or []),
        "pdf_pages": list(exercise_anchor.get("answer_pdf_pages", []) or []),
        "source_image_paths": list(exercise_anchor.get("answer_source_image_paths", []) or []),
        "content": answer_content,
    }
    if question_ids and answer_ids and problem_content and answer_content:
        grounding.update(status="exact_answer", can_conclude=True, failure_reason="", next_action="")
    return grounding


def build_teaching_bundle(grounding: dict[str, Any], request_resolution: dict[str, Any]) -> dict[str, Any]:
    if not grounding.get("required"):
        return {
            "status": "not_applicable",
            "problem_text": "",
            "source_answer_text": "",
            "requested_option": "",
            "exercise_label": "",
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
            "failure_reason": "",
        }
    can_conclude = bool(grounding.get("can_conclude")) and grounding.get("status") == "exact_answer"
    problem = dict(grounding.get("problem") or {})
    solution = dict(grounding.get("solution") or {})
    problem_text = _replace_relative_image_links(
        str(problem.get("content") or ""),
        list(problem.get("source_image_paths", []) or []),
        label="原题图",
    )
    solution_text = _replace_relative_image_links(
        str(solution.get("content") or ""),
        list(solution.get("source_image_paths", []) or []),
        label="原书答案图",
    )
    return {
        "status": "exact" if can_conclude else "blocked",
        "problem_text": problem_text if can_conclude else "",
        "source_answer_text": solution_text if can_conclude else "",
        "requested_option": str(request_resolution.get("requested_option") or ""),
        "exercise_label": str(request_resolution.get("exercise_label") or problem.get("exercise_label") or ""),
        "citations": {
            "problem_evidence_ids": list(problem.get("evidence_ids", []) or []),
            "solution_evidence_ids": list(solution.get("evidence_ids", []) or []),
            # Photo-book evidence has no PDF page number. Keep the physical
            # printed-page order and original image paths in the formal
            # teaching citation so exact answers remain auditable.
            "problem_printed_pages": list(problem.get("printed_pages", []) or []),
            "solution_printed_pages": list(solution.get("printed_pages", []) or []),
            "problem_source_image_paths": list(problem.get("source_image_paths", []) or []),
            "solution_source_image_paths": list(solution.get("source_image_paths", []) or []),
            "problem_pdf_pages": list(problem.get("pdf_pages", []) or []),
            "solution_pdf_pages": list(solution.get("pdf_pages", []) or []),
        },
        "failure_reason": "" if can_conclude else str(grounding.get("failure_reason") or "原题或原书答案未确认。"),
    }


def _replace_relative_image_links(content: str, source_image_paths: list[str], *, label: str) -> str:
    """Replace unusable OCR-local image links with auditable original-page links."""
    pattern = r"!\[[^\]]*\]\((?![A-Za-z][A-Za-z0-9+.-]*:|[/\\])[^)]+\)"
    relative_images = list(re.finditer(pattern, content))
    if not relative_images or not source_image_paths:
        return content
    verified_paths = list(dict.fromkeys(str(item).strip() for item in source_image_paths if str(item).strip()))
    if not verified_paths:
        return content
    if len(relative_images) == 1 and len(verified_paths) == 1:
        target = Path(verified_paths[0]).as_posix()
        return content[:relative_images[0].start()] + f"![{label}（已核验整页原图）](<{target}>)" + content[relative_images[0].end():]

    # Multiple OCR-local image names cannot be paired to page images safely.
    # Remove the broken links and expose every verified full-page source instead.
    cleaned = re.sub(pattern, f"[{label}块：见下列已核验整页原图]", content).rstrip()
    links = "\n".join(
        f"- [{label}整页来源 {index}](<{Path(path).as_posix()}>)"
        for index, path in enumerate(verified_paths, start=1)
    )
    return f"{cleaned}\n\n已核验整页原图：\n{links}"


def _promote_exact_pair_page_anchor(
    page_anchor: dict[str, Any],
    answer_grounding: dict[str, Any],
    *,
    exact_pair: bool,
    series_alias_match: bool,
) -> None:
    """Use a verified problem page only to repair an alias locator miss."""
    if not exact_pair or not series_alias_match or str(page_anchor.get("match_status") or "") != "not_found":
        return
    if page_anchor.get("locator_available") is not True:
        return
    if str(answer_grounding.get("status") or "") != "exact_answer" or not answer_grounding.get("can_conclude"):
        return
    requested_page = page_anchor.get("requested_page")
    problem = dict(answer_grounding.get("problem") or {})
    printed_pages = list(problem.get("printed_pages", []) or [])
    if requested_page is None or requested_page not in printed_pages:
        return
    evidence_ids = list(problem.get("evidence_ids", []) or [])
    if not evidence_ids and problem.get("evidence_id"):
        evidence_ids = [str(problem["evidence_id"])]
    image_paths = list(problem.get("source_image_paths", []) or [])
    if not evidence_ids or not image_paths:
        return
    page_anchor.update(
        match_status="exact_evidence",
        match_basis="exact_exercise_problem_evidence",
        evidence_ids=evidence_ids,
        matched_evidence_id=evidence_ids[0],
        source_image_path=image_paths[0],
    )


def _empty_page_content_bundle(
    *,
    status: str = "not_applicable",
    failure_reason: str = "",
    next_action: str = "",
    page_anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anchor = dict(page_anchor or {})
    printed_page = anchor.get("requested_page") if anchor.get("requested_page") is not None else anchor.get("printed_page")
    pdf_page = anchor.get("pdf_page")
    return {
        "status": status,
        "source_id": str(anchor.get("source_id") or anchor.get("book_id") or ""),
        "book_title": str(anchor.get("book_title") or anchor.get("requested_book_title") or ""),
        "evidence_ids": [],
        "printed_pages": [int(printed_page)] if printed_page is not None else [],
        "pdf_pages": [int(pdf_page)] if pdf_page is not None else [],
        "content": "",
        "failure_reason": failure_reason,
        "next_action": next_action,
    }


def _reviewed_page_evidence(evidence: dict[str, Any]) -> bool:
    return is_publishable_source_evidence(evidence)


def build_page_content_bundle(
    *,
    request_resolution: dict[str, Any],
    page_anchor: dict[str, Any],
    evidences: list[dict[str, Any]],
    page_crosscheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose only reviewed evidence from the exact requested textbook page."""
    if str(request_resolution.get("source_request_kind") or "generic") != "page_content":
        return _empty_page_content_bundle()
    crosscheck = dict(page_crosscheck or {})
    if crosscheck.get("required") and str(crosscheck.get("status") or "") != "confirmed":
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason=str(crosscheck.get("reason") or "页码线索与正式小节锚点尚未完成交叉核验。"),
            next_action="请先核对教材版本、小节编号和起始页。",
            page_anchor=page_anchor,
        )
    page_status = str(page_anchor.get("match_status") or "not_found")
    if page_status == "exact_asset":
        return _empty_page_content_bundle(
            status="asset_only",
            failure_reason="教材原页已定位，但教材正文尚未审核发布。",
            next_action="如需继续，请先人工核对原页或发布审核后的页面证据。",
            page_anchor=page_anchor,
        )
    if page_status != "exact_evidence":
        reasons = {
            "unavailable": "本地证据链当前不可用。",
            "ambiguous": "存在多个教材或页面候选，无法唯一定位页面正文。",
            "unmapped": "该印刷页尚未建立正式页码映射。",
            "not_requested": "尚未给出可唯一定位的教材页码。",
            "not_found": "正式页码索引中没有找到该印刷页。",
        }
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason=reasons.get(page_status, "教材页面正文尚未确认。"),
            next_action="请先补齐唯一教材、印刷页码和审核证据。",
            page_anchor=page_anchor,
        )

    anchor_ids = [str(item) for item in page_anchor.get("evidence_ids", []) if str(item)]
    if page_anchor.get("matched_evidence_id"):
        anchor_ids.insert(0, str(page_anchor["matched_evidence_id"]))
    anchor_ids = list(dict.fromkeys(anchor_ids))
    by_id = {str(item.get("evidence_id") or ""): item for item in evidences}
    source_id = str(page_anchor.get("source_id") or page_anchor.get("book_id") or "")
    printed_page = page_anchor.get("requested_page")
    pdf_page = page_anchor.get("pdf_page")
    reviewed: list[dict[str, Any]] = []
    for evidence_id in anchor_ids:
        evidence = by_id.get(evidence_id)
        if not evidence or not _reviewed_page_evidence(evidence):
            continue
        if source_id and evidence.get("source_id") and str(evidence.get("source_id")) != source_id:
            continue
        if printed_page is not None and evidence.get("printed_page") is not None and int(evidence["printed_page"]) != int(printed_page):
            continue
        if pdf_page is not None and evidence.get("pdf_page") is not None and int(evidence["pdf_page"]) != int(pdf_page):
            continue
        if str(evidence.get("content") or "").strip():
            reviewed.append(evidence)
    if not reviewed:
        return _empty_page_content_bundle(
            status="blocked",
            failure_reason="页面已定位，但没有同源、同页且审核通过的正文证据。",
            next_action="请先审核并发布该页 evidence。",
            page_anchor=page_anchor,
        )

    return {
        "status": "exact",
        "source_id": source_id,
        "book_title": str(page_anchor.get("book_title") or page_anchor.get("requested_book_title") or ""),
        "evidence_ids": [str(item.get("evidence_id") or "") for item in reviewed],
        "printed_pages": [int(printed_page)] if printed_page is not None else [],
        "pdf_pages": [int(pdf_page)] if pdf_page is not None else [],
        "content": "\n\n".join(str(item.get("content") or "").strip() for item in reviewed),
        "failure_reason": "",
        "next_action": "",
    }
