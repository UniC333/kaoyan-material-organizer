#!/usr/bin/env python3
"""Preview and transactionally publish reviewed PDF OCR evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import (
    allocate_kb_id,
    build_provenance_record,
    build_source_span,
    ensure_kb_layout,
    load_json_or_default,
    now_iso,
    sanitize_name,
    save_json,
    sha256_for_file,
    stable_fingerprint,
    validate_entity_contract,
)
from config import load_runtime_config
from kaoyan_kb.domain.page_locator import load_page_locator_index
from ocr.cache import cache_paths_for_request
from publication_support import (
    FileTransaction,
    file_input,
    publication_plan_fingerprint,
    publication_transaction_roots,
    read_only_layout,
    require_matching_plan_fingerprint,
)
from sync_exam_kb import refresh_query_indexes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or publish reviewed PDF OCR. Workflow: inspect -> map-pages -> OCR -> "
            "review -> classify -> publish -> query/ask. Default is a zero-write plan; --yes "
            "requires the unchanged plan fingerprint and executes the publication transaction."
        )
    )
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    parser.add_argument("--pdf-source-id", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--review-artifact-path", required=True)
    parser.add_argument("--chapter-number", type=int)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--yes", action="store_true", help="execute only with --plan-fingerprint from a fresh preview")
    parser.add_argument("--plan-fingerprint", help="fingerprint from preview; required by --yes and rejects input drift")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _resolved_path(raw: Any, *, anchor: Path | None = None) -> Path:
    candidate = Path(str(raw or "").strip()).expanduser()
    if not candidate.is_absolute() and anchor is not None:
        candidate = anchor.parent / candidate
    return candidate.resolve(strict=False)


def _source_file(source: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in source.get("files", []) if isinstance(item, dict)]
    if not files:
        raise ValueError(f"source has no registered file: {source.get('source_id', '')}")
    return files[0]


def _existing_by_key(layout: dict[str, Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for evidence_path in sorted(layout["evidence"].glob("*.json")) if layout["evidence"].is_dir() else []:
        payload = load_json_or_default(evidence_path, {})
        if payload.get("evidence_key"):
            payload = dict(payload)
            payload["_path"] = str(evidence_path)
            result[str(payload["evidence_key"])] = payload
    return result


def _reviewed_page_text(normalized: dict[str, Any], overlay: dict[str, dict[str, Any]]) -> str:
    """Build effective OCR text while preserving accepted human corrections."""
    values: list[str] = []
    for candidate in normalized.get("chunk_candidates", []):
        if not isinstance(candidate, dict):
            continue
        decision = overlay.get(str(candidate.get("block_id", "")), {})
        corrected = str(decision.get("corrected_text", "")).strip()
        if decision.get("review_status") == "accepted" and corrected:
            values.append(corrected)
        else:
            text = str(candidate.get("text", "")).strip()
            if text:
                values.append(text)
    if values:
        return "\n".join(values)
    return "\n".join(
        str(item.get("text", "")).strip()
        for item in normalized.get("pages", [])
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    )


def _resolved_printed_page(chapter: dict[str, Any], handoff_item: dict[str, Any], pdf_page: int) -> int:
    value = handoff_item.get("printed_page")
    try:
        printed_page = int(value)
        if printed_page < 1:
            raise ValueError
        return printed_page
    except (TypeError, ValueError):
        raise SystemExit(f"[ERROR] PDF page {pdf_page} has no formally reviewed printed-page mapping")


def _report_pages(report: dict[str, Any], chapter_number: int | None) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for item in report.get("pages", report.get("chapters", [])) or []:
        if not isinstance(item, dict):
            continue
        number = int(item.get("chapter_number", 0) or 0)
        if chapter_number is not None and number != chapter_number:
            continue
        pdf_page = int(item.get("pdf_page", item.get("page_start", 0)) or 0)
        if pdf_page > 0:
            pages.append(item)
    pages.sort(
        key=lambda item: (
            int(item.get("pdf_page", item.get("page_start", 0)) or 0),
            int(item.get("chapter_number", 0) or 0),
        )
    )
    return pages


def _unique_page_map(items: Any, *, key: str) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    mapped: dict[int, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            value = int(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value < 1:
            continue
        if value in mapped:
            conflicts.append({"reason": f"duplicate-{key}", key: value})
        mapped[value] = item
    return mapped, conflicts


def _formal_pdf_locator(
    locator_index: dict[str, Any],
    *,
    source_id: str,
    pdf_page: int,
    printed_page: int,
    source_sha: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for item in locator_index.get("entries", []) or []:
        if not isinstance(item, dict) or str(item.get("source_id") or "") != source_id:
            continue
        try:
            item_pdf_page = int(item.get("pdf_page", 0) or 0)
        except (TypeError, ValueError):
            item_pdf_page = 0
        if item_pdf_page == pdf_page:
            candidates.append(item)
    exact = []
    for item in candidates:
        try:
            item_printed_page = int(item.get("printed_page", 0) or 0)
        except (TypeError, ValueError):
            item_printed_page = 0
        if item_printed_page == printed_page and str(item.get("source_file_sha256") or "") == source_sha:
            exact.append(item)
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, [{"reason": "formal-pdf-locator-conflict", "pdf_page": pdf_page, "printed_page": printed_page}]
    return None, [
        {
            "reason": "formal-pdf-locator-missing-or-mismatch",
            "pdf_page": pdf_page,
            "printed_page": printed_page,
            "candidate_count": len(candidates),
        }
    ]


def _formal_locator_conflicts(locator_index: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    by_pdf_page: dict[int, list[dict[str, Any]]] = {}
    by_printed_page: dict[int, list[dict[str, Any]]] = {}
    for item in locator_index.get("entries", []) or []:
        if not isinstance(item, dict) or str(item.get("source_id") or "") != source_id:
            continue
        try:
            pdf_page = int(item.get("pdf_page", 0) or 0)
            printed_page = int(item.get("printed_page", 0) or 0)
        except (TypeError, ValueError):
            continue
        if pdf_page > 0:
            by_pdf_page.setdefault(pdf_page, []).append(item)
        if printed_page > 0:
            by_printed_page.setdefault(printed_page, []).append(item)
    conflicts: list[dict[str, Any]] = []
    for pdf_page, items in sorted(by_pdf_page.items()):
        if len(items) > 1:
            conflicts.append({"reason": "formal-pdf-locator-conflict", "pdf_page": pdf_page, "entry_count": len(items)})
    for printed_page, items in sorted(by_printed_page.items()):
        if len(items) > 1:
            conflicts.append({"reason": "formal-printed-page-locator-conflict", "printed_page": printed_page, "entry_count": len(items)})
    return conflicts


def _find_existing(
    existing: dict[str, dict[str, Any]],
    *,
    evidence_key: str,
    source_id: str,
    pdf_page: int,
    printed_page: int,
    request_key: str,
    source_image_sha256: str,
) -> dict[str, Any]:
    current = existing.get(evidence_key)
    if current:
        return current
    for payload in existing.values():
        try:
            payload_pdf_page = int(payload.get("pdf_page", 0) or 0)
            payload_printed_page = int(payload.get("printed_page", 0) or 0)
        except (TypeError, ValueError):
            continue
        if (
            str(payload.get("source_id") or "") == source_id
            and payload_pdf_page == pdf_page
            and payload_printed_page == printed_page
            and (
                (
                    str(payload.get("pdf_ocr_request_key") or "") == request_key
                    and str(payload.get("pdf_ocr_source_image_sha256") or "") == source_image_sha256
                )
                or payload.get("origin_type") == "pdf_page_ocr"
            )
        ):
            return payload
    return {}


def _page_evidence_key(
    *,
    source_id: str,
    chapter_id: str,
    pdf_page: int,
    printed_page: int,
    request_key: str,
    source_sha: str,
    image_sha: str,
) -> str:
    return stable_fingerprint(
        {
            "source_id": source_id,
            "chapter_id": chapter_id,
            "pdf_page": pdf_page,
            "printed_page": printed_page,
            "request_key": request_key,
            "source_file_sha256": source_sha,
            "source_image_sha256": image_sha,
        }
    )


def _validate_pdf_page(
    *,
    page: dict[str, Any],
    handoff: dict[str, Any],
    decision: dict[str, Any],
    source: dict[str, Any],
    source_file: dict[str, Any],
    source_sha: str,
    report_path: Path,
    artifact_path: Path,
    overlay: dict[str, dict[str, Any]],
    overlay_path: Path,
    locator_index: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    del artifact_path
    pdf_page = int(page.get("pdf_page", page.get("page_start", 0)) or 0)
    page_id = f"PDFPAGE-{str(source.get('source_id') or '')}-{pdf_page:04d}"
    blocked: list[dict[str, Any]] = []
    normalized_path = _resolved_path(page.get("normalized_path"), anchor=report_path)
    inputs = [file_input(f"normalized/{page_id}", normalized_path)]
    normalized = load_json_or_default(normalized_path, {})
    ocr_source_path = _resolved_path(
        page.get("ocr_source_image_path") or page.get("rendered_image_path"),
        anchor=report_path,
    )
    inputs.append(file_input(f"ocr-source-image/{page_id}", ocr_source_path))
    if not normalized_path.is_file() or not normalized:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "normalized-ocr-missing"})
        return None, blocked, inputs
    if not ocr_source_path.is_file():
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "ocr-source-image-missing"})
        return None, blocked, inputs

    actual_image_sha = sha256_for_file(ocr_source_path)
    normalized_image_sha = str(normalized.get("source_file_sha256") or "").strip()
    report_image_sha = str(page.get("source_image_sha256") or "").strip()
    if not normalized_image_sha or normalized_image_sha != actual_image_sha or report_image_sha != actual_image_sha:
        blocked.append(
            {
                "pdf_page": pdf_page,
                "page_id": page_id,
                "reason": "source-image-hash-mismatch",
                "normalized_sha256": normalized_image_sha,
                "report_sha256": report_image_sha,
                "actual_sha256": actual_image_sha,
            }
        )
        return None, blocked, inputs
    overlay_payload = load_json_or_default(overlay_path, {})
    overlay_request_key = str(overlay_payload.get("request_key") or "").strip()
    overlay_image_sha = str(overlay_payload.get("source_file_sha256") or "").strip()
    if overlay_request_key and overlay_request_key != str(normalized.get("request_key") or "").strip():
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "overlay-request-key-mismatch"})
    if overlay_image_sha and overlay_image_sha != actual_image_sha:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "overlay-source-image-hash-mismatch"})

    request_key = str(page.get("request_key") or "").strip()
    normalized_request_key = str(normalized.get("request_key") or "").strip()
    decision_request_key = str(decision.get("request_key") or "").strip()
    handoff_request_key = str(handoff.get("request_key") or "").strip()
    if not request_key or not normalized_request_key or request_key != normalized_request_key:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "ocr-request-key-mismatch"})
    if decision_request_key != request_key or handoff_request_key != request_key:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "review-request-key-mismatch"})

    try:
        printed_page = int(handoff.get("printed_page", 0) or 0)
    except (TypeError, ValueError):
        printed_page = 0
    if str(handoff.get("page_id") or "") != page_id:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "review-page-id-mismatch"})
    try:
        if int(handoff.get("pdf_page", pdf_page) or 0) != pdf_page:
            blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "review-pdf-page-mismatch"})
    except (TypeError, ValueError):
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "review-pdf-page-mismatch"})
    if handoff.get("review_status") != "accepted":
        blocked.append(
            {
                "pdf_page": pdf_page,
                "page_id": page_id,
                "reason": "page-review-not-explicitly-accepted",
                "review_status": handoff.get("review_status", ""),
            }
        )
    if decision.get("review_status") != "accepted":
        blocked.append(
            {
                "pdf_page": pdf_page,
                "page_id": page_id,
                "reason": "page-review-decision-not-accepted",
                "review_status": decision.get("review_status", ""),
            }
        )
    if decision.get("source_file_sha256") != source_sha or handoff.get("source_file_sha256") != source_sha:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "page-review-pdf-hash-mismatch"})
    if decision.get("source_image_sha256") != actual_image_sha or handoff.get("source_image_sha256") != actual_image_sha:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "page-review-image-hash-mismatch"})
    if not decision.get("page_header_verified"):
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "page-header-not-verified"})
    if printed_page < 1:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "printed-page-mapping-missing"})
    if str(handoff.get("classification_status") or "") != "confirmed":
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "classification-not-confirmed"})
    chapter_id = str(handoff.get("chapter_id") or "").strip()
    chapter_title = str(handoff.get("chapter_title") or "").strip()
    if not chapter_id or not chapter_title:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "classification-chapter-missing"})
    report_page_sha = str(page.get("source_file_sha256") or "").strip()
    if report_page_sha != source_sha:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "report-pdf-hash-mismatch"})
    if str(source_file.get("sha256") or "") != source_sha:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "registered-pdf-hash-missing"})

    locator, locator_blockers = _formal_pdf_locator(
        locator_index,
        source_id=str(source.get("source_id") or ""),
        pdf_page=pdf_page,
        printed_page=printed_page,
        source_sha=source_sha,
    )
    blocked.extend({**item, "page_id": page_id} for item in locator_blockers)

    sensitive = {
        str(candidate.get("block_type") or "").lower()
        for candidate in normalized.get("chunk_candidates", []) or []
        if isinstance(candidate, dict)
        and str(candidate.get("block_type") or "").lower() in {"table", "formula", "equation"}
        and overlay.get(str(candidate.get("block_id") or ""), {}).get("review_status") != "accepted"
    }
    if sensitive:
        blocked.append(
            {
                "pdf_page": pdf_page,
                "page_id": page_id,
                "reason": "sensitive-block-review-pending",
                "block_types": sorted(sensitive),
            }
        )
    text = _reviewed_page_text(normalized, overlay)
    if not text:
        blocked.append({"pdf_page": pdf_page, "page_id": page_id, "reason": "empty-ocr"})

    if blocked:
        return None, blocked, inputs

    evidence_key = _page_evidence_key(
        source_id=str(source.get("source_id") or ""),
        chapter_id=chapter_id,
        pdf_page=pdf_page,
        printed_page=printed_page,
        request_key=request_key,
        source_sha=source_sha,
        image_sha=actual_image_sha,
    )
    prepared = {
        "page": page,
        "page_id": page_id,
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "normalized": normalized,
        "normalized_path": normalized_path,
        "ocr_source_path": ocr_source_path,
        "overlay": overlay,
        "overlay_path": overlay_path,
        "source": source,
        "source_file": source_file,
        "source_file_sha256": source_sha,
        "source_image_sha256": actual_image_sha,
        "request_key": request_key,
        "handoff": handoff,
        "decision": decision,
        "locator": locator or {},
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "section_id": handoff.get("section_id"),
        "section_title": handoff.get("section_title"),
        "content": text,
        "evidence_key": evidence_key,
    }
    return prepared, blocked, inputs


def _prepare_plan(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str,
    report_path: Path,
    review_artifact_path: Path,
    chapter_number: int | None = None,
    require_complete: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], Any, dict[str, Path]]:
    runtime = load_runtime_config()
    layout = read_only_layout(runtime.kb_root)
    report_path = _resolved_path(report_path)
    review_artifact_path = _resolved_path(review_artifact_path)
    source_path = layout["sources"] / f"{pdf_source_id}.json"
    page_review_path = layout["review_queues"] / "pdf-page-review" / f"{pdf_source_id}.json"
    inputs = [
        file_input("source-manifest", source_path),
        file_input("report", report_path),
        file_input("review-artifact", review_artifact_path),
        file_input("page-review-queue", page_review_path),
        file_input("page-locator-index", layout["indexes"] / "page_locator_index.json"),
    ]
    global_blockers: list[dict[str, Any]] = []
    source = load_json_or_default(source_path, {})
    report = load_json_or_default(report_path, {})
    artifact = load_json_or_default(review_artifact_path, {})
    source_file: dict[str, Any] = {}
    source_sha = ""
    current_pdf_path: Path | None = None
    report_sha256 = sha256_for_file(report_path) if report_path.is_file() else ""
    page_review_sha256 = sha256_for_file(page_review_path) if page_review_path.is_file() else ""
    if not source:
        global_blockers.append({"reason": "source-manifest-missing", "path": str(source_path)})
    else:
        if str(source.get("source_id") or "") != pdf_source_id:
            global_blockers.append({"reason": "source-manifest-source-id-mismatch"})
        if source.get("status") not in {"", "active"}:
            global_blockers.append({"reason": "source-manifest-not-active"})
        if str(source.get("material_type") or "") != "book-pdf":
            global_blockers.append({"reason": "source-manifest-not-pdf"})
        if str(source.get("subject") or "") not in {"", subject}:
            global_blockers.append({"reason": "source-manifest-subject-mismatch"})
        if str(source.get("source_name") or "") not in {"", book_title}:
            global_blockers.append({"reason": "source-manifest-book-scope-mismatch"})
        try:
            source_file = _source_file(source)
            source_sha = str(source_file.get("sha256") or "").strip()
            current_pdf_path = _resolved_path(source_file.get("absolute_path"))
            inputs.append(file_input("current-pdf", current_pdf_path))
            if not current_pdf_path.is_file():
                global_blockers.append({"reason": "current-pdf-missing", "path": str(current_pdf_path)})
            elif not source_sha:
                global_blockers.append({"reason": "registered-pdf-hash-missing"})
            else:
                actual_sha = sha256_for_file(current_pdf_path)
                if actual_sha != source_sha:
                    global_blockers.append(
                        {
                            "reason": "current-pdf-hash-mismatch",
                            "path": str(current_pdf_path),
                            "registered_sha256": source_sha,
                            "actual_sha256": actual_sha,
                        }
                    )
        except ValueError as exc:
            global_blockers.append({"reason": "source-manifest-invalid", "detail": str(exc)})
    if not report:
        global_blockers.append({"reason": "pdf-ocr-report-missing", "path": str(report_path)})
    if not artifact:
        global_blockers.append({"reason": "pdf-ocr-review-artifact-missing", "path": str(review_artifact_path)})
    if report:
        if str(report.get("pdf_source_id") or "") != pdf_source_id:
            global_blockers.append({"reason": "report-source-id-mismatch"})
        if str(report.get("subject") or "") != subject or str(report.get("book_title") or "") != book_title:
            global_blockers.append({"reason": "report-book-scope-mismatch"})
        if str(report.get("source_file_sha256") or "") != source_sha:
            global_blockers.append({"reason": "report-pdf-hash-mismatch"})
        report_pdf_path = _resolved_path(report.get("pdf_path"), anchor=report_path)
        inputs.append(file_input("report-pdf", report_pdf_path))
        if current_pdf_path is not None and report_pdf_path != current_pdf_path:
            global_blockers.append({"reason": "report-pdf-path-mismatch"})
    if artifact:
        if str(artifact.get("pdf_source_id") or "") != pdf_source_id:
            global_blockers.append({"reason": "artifact-source-id-mismatch"})
        if str(artifact.get("subject") or "") != subject or str(artifact.get("book_title") or "") != book_title:
            global_blockers.append({"reason": "artifact-book-scope-mismatch"})
        if str(artifact.get("source_file_sha256") or "") != source_sha:
            global_blockers.append({"reason": "artifact-pdf-hash-mismatch"})
        artifact_report_path = _resolved_path(artifact.get("pdf_ocr_report_path"), anchor=review_artifact_path)
        if str(artifact.get("pdf_ocr_report_path") or "") != str(report_path) or artifact_report_path != report_path:
            global_blockers.append({"reason": "artifact-report-path-mismatch"})
        if str(artifact.get("pdf_ocr_report_sha256") or "") != report_sha256:
            global_blockers.append({"reason": "artifact-report-hash-mismatch"})
        artifact_queue_path = _resolved_path(artifact.get("page_review_queue_path"), anchor=review_artifact_path)
        if str(artifact.get("page_review_queue_path") or "") != str(page_review_path) or artifact_queue_path != page_review_path:
            global_blockers.append({"reason": "artifact-page-review-queue-mismatch"})
        if str(artifact.get("page_review_sha256") or "") != page_review_sha256:
            global_blockers.append({"reason": "artifact-page-review-hash-mismatch"})
        if artifact.get("bridge_report_path"):
            inputs.append(file_input("bridge-report", _resolved_path(artifact.get("bridge_report_path"), anchor=review_artifact_path)))
    locator_index = load_page_locator_index()
    availability = dict(locator_index.get("_availability") or {"available": True})
    if not availability.get("available", True):
        global_blockers.append(
            {
                "reason": str(availability.get("reason") or "page-locator-unavailable"),
                "detail": str(availability.get("detail") or ""),
            }
        )
    global_blockers.extend(_formal_locator_conflicts(locator_index, pdf_source_id))
    locator_path = layout["indexes"] / "page_locator_index.json"
    if locator_path.is_file():
        inputs.append(file_input("page-locator-index-current", locator_path))
    page_review_payload = load_json_or_default(page_review_path, {})
    if str(page_review_payload.get("source_id") or pdf_source_id) != pdf_source_id:
        global_blockers.append({"reason": "page-review-source-id-mismatch"})
    if str(page_review_payload.get("source_file_sha256") or source_sha) not in {"", source_sha}:
        global_blockers.append({"reason": "page-review-queue-pdf-hash-mismatch"})
    decisions, decision_conflicts = _unique_page_map(page_review_payload.get("items", []), key="pdf_page")
    global_blockers.extend(decision_conflicts)
    ledger, ledger_conflicts = _unique_page_map(artifact.get("classify_handoff_ledger", []), key="pdf_page")
    global_blockers.extend(ledger_conflicts)
    accepted_printed_pages: dict[int, list[int]] = {}
    for decision in page_review_payload.get("items", []) or []:
        if not isinstance(decision, dict) or decision.get("review_status") != "accepted":
            continue
        try:
            printed_page = int(decision.get("printed_page", 0) or 0)
            pdf_page = int(decision.get("pdf_page", 0) or 0)
        except (TypeError, ValueError):
            continue
        if printed_page > 0 and pdf_page > 0:
            accepted_printed_pages.setdefault(printed_page, []).append(pdf_page)
    for printed_page, pdf_pages in sorted(accepted_printed_pages.items()):
        if len(pdf_pages) > 1:
            global_blockers.append(
                {
                    "reason": "duplicate-accepted-printed-page",
                    "printed_page": printed_page,
                    "pdf_pages": sorted(pdf_pages),
                }
            )

    existing = _existing_by_key(layout)
    prepared_items: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    pages = _report_pages(report, chapter_number)
    if not pages:
        global_blockers.append({"reason": "no-report-pages-selected"})
    for page in pages:
        pdf_page = int(page.get("pdf_page", page.get("page_start", 0)) or 0)
        page_id = f"PDFPAGE-{pdf_source_id}-{pdf_page:04d}"
        handoff = ledger.get(pdf_page, {})
        decision = decisions.get(pdf_page, {})
        request_key = str(page.get("request_key") or "").strip()
        overlay_path = cache_paths_for_request(runtime.ocr_cache_root, request_key)["overlay"] if request_key else Path()
        overlay = (
            {
                str(item.get("block_id") or ""): item
                for item in load_json_or_default(overlay_path, {}).get("items", [])
                if isinstance(item, dict) and item.get("block_id")
            }
            if request_key
            else {}
        )
        if request_key:
            inputs.append(file_input(f"overlay/{page_id}", overlay_path))
        prepared, page_blocked, page_inputs = _validate_pdf_page(
            page=page,
            handoff=handoff,
            decision=decision,
            source=source,
            source_file=source_file,
            source_sha=source_sha,
            report_path=report_path,
            artifact_path=review_artifact_path,
            overlay=overlay,
            overlay_path=overlay_path,
            locator_index=locator_index,
        )
        inputs.extend(page_inputs)
        if page_blocked:
            blocked.extend(page_blocked)
        elif prepared is not None:
            old = _find_existing(
                existing,
                evidence_key=prepared["evidence_key"],
                source_id=pdf_source_id,
                pdf_page=prepared["pdf_page"],
                printed_page=prepared["printed_page"],
                request_key=prepared["request_key"],
                source_image_sha256=prepared["source_image_sha256"],
            )
            if old:
                inputs.append(file_input(f"existing-evidence/{page_id}", Path(str(old.get("_path") or ""))))
            prepared["existing_evidence"] = old
            prepared_items.append(prepared)

    unique_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    for item in inputs:
        unique_inputs[(str(item.get("label", "")), str(item.get("path", "")))] = item
    inputs = [unique_inputs[key] for key in sorted(unique_inputs)]
    base = {
        "plan_contract_version": "ocr-publication-plan.v1",
        "publication_kind": "pdf-ocr",
        "arguments": {
            "subject": subject,
            "book_title": book_title,
            "pdf_source_id": pdf_source_id,
            "report_path": str(report_path),
            "review_artifact_path": str(review_artifact_path),
            "chapter_number": chapter_number,
            "require_complete": bool(require_complete),
        },
        "inputs": inputs,
        "items": [
            {
                "page_id": item["page_id"],
                "pdf_page": item["pdf_page"],
                "printed_page": item["printed_page"],
                "chapter_id": item["chapter_id"],
                "request_key": item["request_key"],
                "source_file_sha256": item["source_file_sha256"],
                "source_image_sha256": item["source_image_sha256"],
                "evidence_key": item["evidence_key"],
                "existing_evidence_id": str(item.get("existing_evidence", {}).get("evidence_id") or ""),
            }
            for item in prepared_items
        ],
        "blocked": blocked,
        "global_blockers": global_blockers,
        "writes": {
            "evidence_count": len(prepared_items),
            "index_refresh_steps": [
                "build_page_locator_index",
                "build_exercise_locator_index",
                "build_search_index",
                "build_book_series_indexes",
            ],
        },
    }
    fingerprint = publication_plan_fingerprint(base)
    can_execute = not global_blockers and (not require_complete or not blocked) and bool(prepared_items)
    plan = {
        **base,
        "plan_fingerprint": fingerprint,
        "preview_only": True,
        "can_execute": can_execute,
        "publish_status": "ready" if can_execute else "blocked",
        "published_evidence_ids": [],
        "published_count": 0,
        "blocked_count": len(blocked) + len(global_blockers),
    }
    return plan, prepared_items, source, runtime, layout


def build_publication_plan(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str,
    report_path: Path,
    review_artifact_path: Path,
    chapter_number: int | None = None,
    require_complete: bool = False,
) -> dict[str, Any]:
    return _prepare_plan(
        subject=subject,
        book_title=book_title,
        pdf_source_id=pdf_source_id,
        report_path=report_path,
        review_artifact_path=review_artifact_path,
        chapter_number=chapter_number,
        require_complete=require_complete,
    )[0]


def _build_evidence(*, item: dict[str, Any], subject: str, book_title: str) -> dict[str, Any]:
    source = item["source"]
    source_file = item["source_file"]
    source_id = str(source.get("source_id") or "")
    source_sha = item["source_file_sha256"]
    pdf_page = int(item["pdf_page"])
    printed_page = int(item["printed_page"])
    source_image_sha = item["source_image_sha256"]
    chapter_id = item["chapter_id"]
    chapter_title = item["chapter_title"]
    chunk_id = f"PDFOCR-{sanitize_name(chapter_id)}-{pdf_page:04d}"
    span = build_source_span(
        source_id=source_id,
        file_id=str(source_file.get("file_id") or ""),
        source_file_sha256=source_sha,
        chapter_id=chapter_id,
        chunk_id=chunk_id,
        page_start=pdf_page,
        page_end=pdf_page,
        image_start=str(item["ocr_source_path"]),
        image_end=str(item["ocr_source_path"]),
        origin_type="pdf_page_ocr",
        verification_status="reviewed",
        block_ids=[
            str(candidate.get("block_id") or "")
            for candidate in item["normalized"].get("chunk_candidates", [])
            if isinstance(candidate, dict) and candidate.get("block_id")
        ],
        notes="Only an explicitly accepted PDF page review and formal PDF-to-printed-page mapping can be published.",
    )
    span["locator"].update(
        {
            "pdf_page": pdf_page,
            "printed_page": printed_page,
            "source_image_sha256": source_image_sha,
            "request_key": item["request_key"],
            "mapping_status": "formally_reviewed",
        }
    )
    old = item.get("existing_evidence") or {}
    evidence_id = str(old.get("evidence_id") or allocate_kb_id("evidence", subject))
    evidence = {
        "evidence_id": evidence_id,
        "evidence_key": item["evidence_key"],
        "subject": subject,
        "book_id": str(item["handoff"].get("book_id") or f"PDFOCR-{source_id}"),
        "book_title": book_title,
        "source_id": source_id,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "chunk_id": chunk_id,
        "title": str(item["handoff"].get("section_title") or chapter_title or f"PDF第{pdf_page}页").strip(),
        "content": item["content"],
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "evidence_type": "concept",
        "origin_type": "pdf_page_ocr",
        "verification_status": "reviewed",
        "review_status": "accepted",
        "review_decision": "explicit-page-review",
        "review_note": str(item["decision"].get("note") or ""),
        "reviewed_at": str(item["decision"].get("reviewed_at") or now_iso()),
        "confidence": 0.85,
        "source_grounded": True,
        "source_spans": [span],
        "page_classification_refs": [
            {
                "book_id": str(item["handoff"].get("book_id") or f"PDFOCR-{source_id}"),
                "book_title": book_title,
                "source_id": source_id,
                "page_id": item["page_id"],
                "source_file_sha256": source_sha,
                "source_image_sha256": source_image_sha,
                "request_key": item["request_key"],
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "section_id": item["section_id"],
                "section_title": item["section_title"],
                "printed_page": printed_page,
                "pdf_page": pdf_page,
                "classification_status": "confirmed",
                "source_image_path": str(item["ocr_source_path"]),
            }
        ],
        "locator": dict(span["locator"]),
        "provenance": build_provenance_record(
            origin_type="pdf_page_ocr",
            verification_status="reviewed",
            source_spans=[span],
            source_grounded=True,
        ),
        "syllabus_candidates": item.get("existing_evidence", {}).get("syllabus_candidates", []),
        "accepted_syllabus_nodes": item.get("existing_evidence", {}).get("accepted_syllabus_nodes", []),
        "mapping_status": "mapped",
        "pdf_ocr_request_key": item["request_key"],
        "pdf_ocr_normalized_path": str(item["normalized_path"]),
        "pdf_ocr_source_file_sha256": source_sha,
        "pdf_ocr_source_image_sha256": source_image_sha,
        "pdf_ocr_source_image_path": str(item["ocr_source_path"]),
        "coverage_note": "One explicitly reviewed and formally mapped rendered PDF page; no neighboring-page inference.",
        "updated_at": now_iso(),
    }
    validate_entity_contract("evidence", evidence)
    return evidence


def publish(
    *,
    subject: str,
    book_title: str,
    pdf_source_id: str,
    report_path: Path,
    review_artifact_path: Path,
    chapter_number: int | None = None,
    require_complete: bool = False,
    yes: bool = False,
    expected_plan_fingerprint: str | None = None,
) -> dict[str, Any]:
    plan, prepared_items, _source, runtime, layout = _prepare_plan(
        subject=subject,
        book_title=book_title,
        pdf_source_id=pdf_source_id,
        report_path=report_path,
        review_artifact_path=review_artifact_path,
        chapter_number=chapter_number,
        require_complete=require_complete,
    )
    if yes and not expected_plan_fingerprint:
        raise SystemExit("[ERROR] --yes requires --plan-fingerprint from a fresh zero-write preview")
    require_matching_plan_fingerprint(expected_plan_fingerprint, plan["plan_fingerprint"])
    if not yes:
        return plan
    if not plan["can_execute"]:
        return {**plan, "preview_only": False, "publish_status": "blocked"}

    transaction = FileTransaction(publication_transaction_roots(runtime.kb_root))
    published: list[str] = []
    try:
        ensure_kb_layout()
        existing = _existing_by_key(layout)
        for item in prepared_items:
            old = item.get("existing_evidence") or existing.get(item["evidence_key"], {})
            if old and not old.get("evidence_id"):
                old = {}
            item["existing_evidence"] = old
            evidence = _build_evidence(item=item, subject=subject, book_title=book_title)
            save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
            existing[evidence["evidence_key"]] = evidence
            published.append(evidence["evidence_id"])
        refresh_steps = refresh_query_indexes()
        transaction.commit()
    except Exception as exc:
        transaction.restore()
        return {
            **plan,
            "preview_only": False,
            "publish_status": "failed",
            "published_evidence_ids": [],
            "published_count": 0,
            "index_refresh_status": "failed",
            "rolled_back": True,
            "error": str(exc),
        }
    return {
        **plan,
        "preview_only": False,
        "publish_status": "completed",
        "published_evidence_ids": published,
        "published_count": len(published),
        "index_refresh_status": "completed",
        "index_refresh_steps": refresh_steps,
        "rolled_back": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = publish(
        subject=args.subject,
        book_title=args.book_title,
        pdf_source_id=args.pdf_source_id,
        report_path=Path(args.report_path),
        review_artifact_path=Path(args.review_artifact_path),
        chapter_number=args.chapter_number,
        require_complete=bool(args.require_complete),
        yes=bool(args.yes),
        expected_plan_fingerprint=args.plan_fingerprint,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("publish_status") in {"blocked", "failed"} and args.yes:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
