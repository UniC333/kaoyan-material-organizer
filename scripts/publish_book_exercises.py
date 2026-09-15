#!/usr/bin/env python3
"""Preview and transactionally publish reviewed photo-book exercise evidence."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import (
    allocate_kb_id,
    build_provenance_record,
    ensure_kb_layout,
    load_all_json,
    load_json_or_default,
    now_iso,
    save_json,
    sha256_for_file,
    stable_fingerprint,
    validate_entity_contract,
)
from config import load_runtime_config
from kaoyan_kb.domain.book_series import BOOK_SERIES_INDEX_NAME, exercise_key, load_book_series_index
from ocr.cache import cache_paths_for_request
from ocr.review import LOW_CONFIDENCE_THRESHOLD
from publication_support import (
    FileTransaction,
    file_input,
    publication_plan_fingerprint,
    publication_transaction_roots,
    read_only_layout,
    require_matching_plan_fingerprint,
)
from sync_exam_kb import refresh_query_indexes


TYPE_PATTERNS = (
    ("choice", re.compile(r"选择题")),
    ("fill_blank", re.compile(r"填空题")),
    ("worked", re.compile(r"(?:解答题|计算题|证明题)")),
)
QUESTION_PATTERN = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*(.*)$")
CHAPTER_PATTERN = re.compile(r"-(?:B|SB|A|SA)-(CALC|LA|PROB)-(\d+)$")
SENSITIVE_BLOCK_TYPES = {"table", "formula", "equation"}


def _source_for_root(book_root: Path, layout: dict[str, Path] | None = None) -> dict[str, Any]:
    layout = layout or read_only_layout(load_runtime_config().kb_root)
    target = str(book_root.resolve()).casefold()
    matches = [
        item
        for item in load_all_json(layout["manifest_sources"])
        if str(Path(str(item.get("source_path") or "")).resolve()).casefold() == target
        and item.get("material_type") == "chapter-photo"
        and item.get("status") == "active"
    ]
    if len(matches) != 1:
        raise SystemExit("exactly one active chapter-photo source is required; run book register-photo-source first")
    return matches[0]


def _volume(book_id: str, series_index: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    series_index = series_index if series_index is not None else load_book_series_index()
    for series in series_index.get("series", []):
        for volume in series.get("volumes", []):
            if volume.get("book_id") == book_id:
                return series, volume
    raise SystemExit(f"book is not registered in book_series_index: {book_id}")


def _overlay_by_block(cache_root: Path, request_key: str) -> dict[str, dict[str, Any]]:
    overlay = load_json_or_default(cache_paths_for_request(cache_root, request_key)["overlay"], {})
    return {str(item.get("block_id") or ""): item for item in overlay.get("items", []) if isinstance(item, dict)}


def _effective_text(normalized: dict[str, Any], overlay: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    lines: list[str] = []
    blockers: list[str] = []
    for candidate in normalized.get("chunk_candidates", []):
        block_id = str(candidate.get("block_id") or "")
        block_type = str(candidate.get("block_type") or "")
        confidence = float(candidate.get("confidence", 0.0) or 0.0)
        requires_review = block_type in SENSITIVE_BLOCK_TYPES or confidence < LOW_CONFIDENCE_THRESHOLD
        decision = overlay.get(block_id, {})
        review_status = str(decision.get("review_status") or "").strip()
        if review_status in {"pending", "rejected"}:
            blockers.append(block_id)
            continue
        if review_status == "ignored":
            continue
        if requires_review and review_status != "accepted":
            blockers.append(block_id)
            continue
        corrected = str(decision.get("corrected_text") or "").strip()
        raw = str(candidate.get("text") or "").strip()
        text = corrected if review_status == "accepted" and corrected else raw
        if text:
            lines.append(text)
    return "\n".join(lines), blockers


def _existing_evidence_by_key(layout: dict[str, Path] | None = None) -> dict[str, dict[str, Any]]:
    layout = layout or read_only_layout(load_runtime_config().kb_root)
    result: dict[str, dict[str, Any]] = {}
    paths = sorted(layout["evidence"].glob("*.json")) if layout["evidence"].is_dir() else []
    for path in paths:
        payload = load_json_or_default(path, {})
        if payload.get("evidence_key"):
            payload["_path"] = str(path)
            result[str(payload["evidence_key"])] = payload
    return result


def _path_or_none(value: Any) -> Path | None:
    raw = str(value or "").strip()
    return Path(raw) if raw else None


def _append_input(inputs: list[dict[str, Any]], label: str, path: Path) -> None:
    inputs.append(file_input(label, path))


def _page_source_sha(*, status: dict[str, Any], asset: dict[str, Any], file_item: dict[str, Any], image_path: Path) -> tuple[str, str]:
    if not image_path.is_file():
        return "", "source-image-missing"
    actual_sha = sha256_for_file(image_path)
    declared = {
        str(value).strip()
        for value in (
            status.get("source_image_sha256"),
            asset.get("source_image_sha256"),
            file_item.get("sha256"),
        )
        if str(value or "").strip()
    }
    if not declared:
        return actual_sha, "source-image-hash-missing"
    if declared != {actual_sha}:
        return actual_sha, "source-image-hash-mismatch"
    return actual_sha, ""


def _collect_pages(
    *,
    runtime,
    source: dict[str, Any],
    statuses: dict[str, Any],
    classifications: dict[str, Any],
    assets: dict[str, Any],
    inputs: list[dict[str, Any]],
    chapter_ids: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], set[str], list[dict[str, Any]]]:
    files_by_path = {
        str(Path(str(item.get("absolute_path") or "")).resolve()).casefold(): item
        for item in source.get("files", [])
        if isinstance(item, dict) and str(item.get("absolute_path") or "").strip()
    }
    asset_by_page = {
        str(item.get("page_id") or ""): item
        for item in assets.get("items", [])
        if isinstance(item, dict) and item.get("page_id")
    }
    class_by_page = {
        str(item.get("page_id") or ""): item
        for item in classifications.get("items", [])
        if isinstance(item, dict) and item.get("page_id")
    }
    status_items = [item for item in statuses.get("items", []) if isinstance(item, dict) and item.get("page_id")]
    status_by_page = {str(item["page_id"]): item for item in status_items}
    pages_by_chapter: dict[str, list[dict[str, Any]]] = {}
    blocked: list[dict[str, Any]] = []
    blocked_chapters: set[str] = set()

    for page_id, classification in class_by_page.items():
        chapter_id = str(classification.get("chapter_id") or "")
        if chapter_ids is not None and chapter_id not in chapter_ids:
            continue
        if classification.get("classification_status") != "confirmed" or not chapter_id:
            continue
        if status_by_page.get(page_id, {}).get("status") != "completed":
            blocked.append(
                {
                    "page_id": page_id,
                    "printed_page": classification.get("printed_page"),
                    "chapter_id": chapter_id,
                    "reason": "ocr_not_completed",
                }
            )
            blocked_chapters.add(chapter_id)

    for status in status_items:
        page_id = str(status.get("page_id") or "")
        if status.get("status") != "completed":
            continue
        classification = class_by_page.get(page_id, {})
        chapter_id = str(classification.get("chapter_id") or "")
        if chapter_ids is not None and chapter_id not in chapter_ids:
            continue
        if classification.get("classification_status") != "confirmed" or not chapter_id:
            blocked.append({"page_id": page_id, "reason": "classification_not_confirmed"})
            continue

        asset = asset_by_page.get(page_id, {})
        normalized_path = _path_or_none(status.get("normalized_path"))
        if normalized_path is None or not normalized_path.is_file():
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "normalized_ocr_missing"})
            blocked_chapters.add(chapter_id)
            continue
        _append_input(inputs, f"normalized/{page_id}", normalized_path)
        normalized = load_json_or_default(normalized_path, {})
        request_key = str(status.get("request_key") or normalized.get("request_key") or "").strip()
        if not request_key:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "ocr_request_key_missing"})
            blocked_chapters.add(chapter_id)
            continue
        if normalized.get("request_key") and str(normalized.get("request_key")) != request_key:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "ocr_request_key_mismatch"})
            blocked_chapters.add(chapter_id)
            continue

        overlay_path = cache_paths_for_request(runtime.ocr_cache_root, request_key)["overlay"]
        _append_input(inputs, f"overlay/{page_id}", overlay_path)
        overlay = _overlay_by_block(runtime.ocr_cache_root, request_key)
        text, blockers = _effective_text(normalized, overlay)
        if blockers:
            blocked.append(
                {
                    "page_id": page_id,
                    "printed_page": status.get("printed_page"),
                    "chapter_id": chapter_id,
                    "reason": "review_pending",
                    "block_ids": blockers,
                }
            )
            blocked_chapters.add(chapter_id)
            continue
        if not text:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "empty_ocr"})
            blocked_chapters.add(chapter_id)
            continue

        image_path = _path_or_none(status.get("source_image_path") or asset.get("source_image_path"))
        file_item = files_by_path.get(str(image_path.resolve()).casefold()) if image_path else None
        if image_path is None or not file_item:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "source_file_not_registered"})
            blocked_chapters.add(chapter_id)
            continue
        _append_input(inputs, f"source-image/{page_id}", image_path)
        source_sha, sha_reason = _page_source_sha(
            status=status,
            asset=asset,
            file_item=file_item,
            image_path=image_path,
        )
        if sha_reason:
            blocked.append(
                {
                    "page_id": page_id,
                    "chapter_id": chapter_id,
                    "reason": sha_reason,
                    "actual_sha256": source_sha,
                }
            )
            blocked_chapters.add(chapter_id)
            continue
        normalized_sha = str(normalized.get("source_file_sha256") or normalized.get("source_image_sha256") or "").strip()
        if normalized_sha and normalized_sha != source_sha:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "normalized-source-image-hash-mismatch"})
            blocked_chapters.add(chapter_id)
            continue
        overlay_payload = load_json_or_default(overlay_path, {})
        overlay_sha = str(overlay_payload.get("source_file_sha256") or overlay_payload.get("source_image_sha256") or "").strip()
        if overlay_sha and overlay_sha != source_sha:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "overlay-source-image-hash-mismatch"})
            blocked_chapters.add(chapter_id)
            continue

        try:
            printed_page = int(status.get("printed_page", 0) or 0)
            classified_printed_page = int(classification.get("printed_page", 0) or 0)
        except (TypeError, ValueError):
            printed_page = 0
            classified_printed_page = 0
        if printed_page < 1 or classified_printed_page < 1:
            blocked.append({"page_id": page_id, "chapter_id": chapter_id, "reason": "printed_page_mapping_missing"})
            blocked_chapters.add(chapter_id)
            continue
        if printed_page != classified_printed_page:
            blocked.append(
                {
                    "page_id": page_id,
                    "chapter_id": chapter_id,
                    "reason": "printed_page_mapping_mismatch",
                    "status_printed_page": printed_page,
                    "classification_printed_page": classified_printed_page,
                }
            )
            blocked_chapters.add(chapter_id)
            continue

        pages_by_chapter.setdefault(chapter_id, []).append(
            {
                "status": status,
                "classification": classification,
                "text": text,
                "file": file_item,
                "source_image_path": image_path,
                "source_image_sha256": source_sha,
                "normalized_path": normalized_path,
                "overlay_path": overlay_path,
            }
        )
    return pages_by_chapter, blocked_chapters, blocked


def _parse_candidates(
    pages_by_chapter: dict[str, list[dict[str, Any]]], blocked_chapters: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    parse_issues: list[dict[str, Any]] = []
    for chapter_id, pages in sorted(pages_by_chapter.items()):
        if chapter_id in blocked_chapters:
            parse_issues.append({"chapter_id": chapter_id, "reason": "chapter_incomplete"})
            continue
        match = CHAPTER_PATTERN.search(chapter_id)
        if not match:
            parse_issues.append({"chapter_id": chapter_id, "reason": "unsupported_chapter_id"})
            continue
        part, chapter_token = match.groups()
        chapter_number = int(chapter_token)
        stage = "advanced" if re.search(r"-(?:A|SA)-", chapter_id) else "basic"
        pages = sorted(pages, key=lambda item: int(item["status"].get("printed_page", 0) or 0))
        if len({int(item["status"].get("printed_page", 0) or 0) for item in pages}) != len(pages):
            parse_issues.append({"chapter_id": chapter_id, "reason": "duplicate_printed_page"})
            continue
        current_type = ""
        current: dict[str, Any] | None = None
        seen: set[tuple[str, int]] = set()
        for page in pages:
            page_number = int(page["status"].get("printed_page", 0) or 0)
            for line in page["text"].splitlines():
                for type_name, pattern in TYPE_PATTERNS:
                    if pattern.search(line):
                        current_type = type_name
                        current = None
                        break
                match_question = QUESTION_PATTERN.match(line)
                if match_question and current_type:
                    number = int(match_question.group(1))
                    identity = (current_type, number)
                    if identity in seen:
                        parse_issues.append(
                            {
                                "chapter_id": chapter_id,
                                "exercise_type": current_type,
                                "exercise_number": number,
                                "reason": "duplicate_exercise_key",
                            }
                        )
                        current = None
                        continue
                    seen.add(identity)
                    current = {
                        "stage": stage,
                        "part": part,
                        "chapter_number": chapter_number,
                        "chapter_id": chapter_id,
                        "exercise_type": current_type,
                        "exercise_number": number,
                        "lines": [match_question.group(2).strip()],
                        "pages": {page_number: page},
                    }
                    candidates.append(current)
                elif current is not None and line.strip():
                    current["lines"].append(line.strip())
                    current["pages"][page_number] = page
    return candidates, parse_issues


def _exercise_key(candidate: dict[str, Any], series_id: str) -> str:
    return exercise_key(
        series_id,
        candidate["stage"],
        candidate["part"],
        candidate["chapter_number"],
        candidate["exercise_type"],
        candidate["exercise_number"],
    )


def _candidate_content(candidate: dict[str, Any]) -> str:
    return "\n".join(line for line in candidate["lines"] if line).strip()


def _evidence_key(candidate: dict[str, Any], *, series_id: str, book_id: str) -> str:
    key = _exercise_key(candidate, series_id)
    return stable_fingerprint(
        {
            "exercise_key": key,
            "book_id": book_id,
            "content": _candidate_content(candidate),
            "source_sha256": [
                page["source_image_sha256"]
                for _, page in sorted(candidate["pages"].items())
            ],
        }
    )


def _prepare_plan(
    *, book_root: Path, chapter_ids: set[str] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], Any, dict[str, Path]]:
    runtime = load_runtime_config()
    root = Path(book_root).resolve()
    metadata = root / runtime.paper_book_metadata_dir
    status_path = metadata / "page_ocr_status.json"
    classifications_path = metadata / "page_classifications.json"
    assets_path = metadata / "page_assets.json"
    statuses = load_json_or_default(status_path, {})
    classifications = load_json_or_default(classifications_path, {})
    assets = load_json_or_default(assets_path, {})
    if not statuses or not classifications or not assets:
        raise SystemExit("page OCR status, page classifications, and page assets are required")

    layout = read_only_layout(runtime.kb_root)
    book_id = str(assets.get("book_id") or "").strip()
    if not book_id:
        raise SystemExit("page_assets.json must contain book_id")
    series_index = load_json_or_default(layout["indexes"] / BOOK_SERIES_INDEX_NAME, {"series": []})
    series, volume = _volume(book_id, series_index)
    source = _source_for_root(root, layout)
    inputs = [
        file_input("book-yaml", root / "book.yaml"),
        file_input("page-ocr-status", status_path),
        file_input("page-classifications", classifications_path),
        file_input("page-assets", assets_path),
        file_input("book-series-index", layout["indexes"] / BOOK_SERIES_INDEX_NAME),
        file_input("id-counters", layout["indexes"] / "id_counters.json"),
        file_input(
            f"source-manifest/{source.get('source_id', '')}",
            layout["manifest_sources"] / f"{source.get('source_id', '')}.json",
        ),
        file_input(f"source/{source.get('source_id', '')}", layout["sources"] / f"{source.get('source_id', '')}.json"),
    ]
    pages_by_chapter, blocked_chapters, blocked = _collect_pages(
        runtime=runtime,
        source=source,
        statuses=statuses,
        classifications=classifications,
        assets=assets,
        inputs=inputs,
        chapter_ids=chapter_ids,
    )
    candidates, parse_issues = _parse_candidates(pages_by_chapter, blocked_chapters)
    existing = _existing_evidence_by_key(layout)
    for payload in existing.values():
        if payload.get("_path"):
            inputs.append(file_input(f"existing-evidence/{payload.get('evidence_key', '')}", Path(str(payload["_path"]))))

    plan_items: list[dict[str, Any]] = []
    publication_items: list[dict[str, Any]] = []
    for candidate in candidates:
        exercise_id = _exercise_key(candidate, series["series_id"])
        content = _candidate_content(candidate)
        evidence_key = _evidence_key(candidate, series_id=series["series_id"], book_id=book_id)
        old = existing.get(evidence_key, {})
        page_numbers = sorted(candidate["pages"])
        item = {
            "exercise_key": exercise_id,
            "evidence_key": evidence_key,
            "existing_evidence_id": str(old.get("evidence_id") or ""),
            "chapter_id": candidate["chapter_id"],
            "exercise_type": candidate["exercise_type"],
            "exercise_number": candidate["exercise_number"],
            "printed_pages": page_numbers,
            "source_image_sha256": [candidate["pages"][page]["source_image_sha256"] for page in page_numbers],
            "content_length": len(content),
        }
        plan_items.append(item)
        publication_items.append({"candidate": candidate, "evidence_key": evidence_key, "existing": old})

    unique_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    for item in inputs:
        unique_inputs[(str(item.get("label", "")), str(item.get("path", "")))] = item
    inputs = [unique_inputs[key] for key in sorted(unique_inputs)]
    plan_items.sort(key=lambda item: (str(item.get("exercise_key", "")), str(item.get("evidence_key", ""))))
    blocked.sort(key=lambda item: (str(item.get("page_id", "")), str(item.get("reason", ""))))
    parse_issues.sort(key=lambda item: (str(item.get("chapter_id", "")), str(item.get("reason", ""))))
    global_blockers = [{"reason": "parse_issue", **item} for item in parse_issues]
    if not plan_items:
        global_blockers.append({"reason": "no_publishable_exercises"})
    writes = {
        "evidence_count": len(plan_items),
        "index_refresh_steps": [
            "build_page_locator_index",
            "build_exercise_locator_index",
            "build_search_index",
            "build_book_series_indexes",
        ],
    }
    base = {
        "plan_contract_version": "ocr-publication-plan.v1",
        "publication_kind": "book-exercises",
        "arguments": {
            "book_root": str(root),
            "chapter_ids": sorted(chapter_ids) if chapter_ids is not None else None,
        },
        "inputs": inputs,
        "items": plan_items,
        "blocked": blocked,
        "global_blockers": global_blockers,
        "writes": writes,
    }
    fingerprint = publication_plan_fingerprint(base)
    can_execute = not blocked and not global_blockers and bool(plan_items)
    plan = {
        **base,
        "plan_fingerprint": fingerprint,
        "preview_only": True,
        "can_execute": can_execute,
        "publish_status": "ready" if can_execute else "blocked",
        "book_id": book_id,
        "series_id": series.get("series_id", ""),
        "candidate_count": len(plan_items),
        "blocked_count": len(blocked),
        "parse_issue_count": len(parse_issues),
        "parse_issues": parse_issues,
        "write_authorized": False,
        "written_count": 0,
        "published_evidence_ids": [],
        "published_count": 0,
    }
    context = {"source": source, "series": series, "volume": volume, "book_id": book_id}
    return plan, publication_items, context, runtime, layout


def build_publication_plan(*, book_root: Path, chapter_ids: set[str] | None = None) -> dict[str, Any]:
    return _prepare_plan(book_root=book_root, chapter_ids=chapter_ids)[0]


def _build_evidence(*, publication: dict[str, Any], context: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    candidate = publication["candidate"]
    source = context["source"]
    series = context["series"]
    volume = context["volume"]
    book_id = context["book_id"]
    exercise_id = _exercise_key(candidate, series["series_id"])
    evidence_key = publication["evidence_key"]
    evidence_id = str(old.get("evidence_id") or allocate_kb_id("evidence", str(series.get("subject") or "数学")))
    refs: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for printed_page, page in sorted(candidate["pages"].items()):
        status = page["status"]
        classification = page["classification"]
        source_sha = page["source_image_sha256"]
        image_path = str(page["source_image_path"])
        refs.append(
            {
                "page_classification_id": classification.get("page_classification_id"),
                "page_id": status.get("page_id"),
                "book_id": book_id,
                "book_title": volume.get("title", ""),
                "chapter_id": candidate["chapter_id"],
                "chapter_title": classification.get("chapter_title", ""),
                "printed_page": printed_page,
                "source_file_sha256": source_sha,
                "source_image_sha256": source_sha,
                "source_image_path": image_path,
                "classification_status": "confirmed",
            }
        )
        spans.append(
            {
                "source_id": source["source_id"],
                "file_id": page["file"].get("file_id", ""),
                "source_file_sha256": source_sha,
                "source_image_sha256": source_sha,
                "locator": {
                    "page_start": printed_page,
                    "page_end": printed_page,
                    "image_start": image_path,
                    "image_end": image_path,
                },
            }
        )
    locator = {
        "page_start": min(candidate["pages"]),
        "page_end": max(candidate["pages"]),
        "image_start": spans[0]["locator"]["image_start"],
        "image_end": spans[-1]["locator"]["image_end"],
    }
    evidence = {
        "evidence_id": evidence_id,
        "evidence_key": evidence_key,
        "subject": series.get("subject", "数学"),
        "source_id": source["source_id"],
        "chapter_id": candidate["chapter_id"],
        "chunk_id": exercise_id,
        "title": f"{volume.get('title', '')} {candidate['exercise_type']} {candidate['exercise_number']}",
        "content": _candidate_content(candidate),
        "origin_type": "paper_book_reviewed_ocr",
        "verification_status": "source_grounded",
        "review_status": "accepted",
        "source_grounded": True,
        "source_spans": spans,
        "locator": locator,
        "page_classification_refs": refs,
        "provenance": build_provenance_record(
            origin_type="paper_book_reviewed_ocr",
            verification_status="source_grounded",
            source_spans=spans,
            source_grounded=True,
        ),
        "book_id": book_id,
        "book_title": volume.get("title", ""),
        "exercise_key": exercise_id,
        "exercise_stage": candidate["stage"],
        "exercise_part": candidate["part"],
        "exercise_chapter_number": candidate["chapter_number"],
        "exercise_type": candidate["exercise_type"],
        "exercise_number": candidate["exercise_number"],
        "exercise_role": volume.get("role", ""),
        "updated_at": now_iso(),
    }
    validate_entity_contract("evidence", evidence)
    return evidence


def publish_exercises(
    *,
    book_root: Path,
    write: bool | None = None,
    yes: bool | None = None,
    expected_plan_fingerprint: str | None = None,
    chapter_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Preview by default; execute only with --yes and the preview fingerprint."""
    if yes is None:
        yes = bool(write)
    plan, publication_items, context, runtime, layout = _prepare_plan(
        book_root=book_root, chapter_ids=chapter_ids
    )
    if yes and not expected_plan_fingerprint:
        raise SystemExit("[ERROR] --yes requires --plan-fingerprint from a fresh zero-write preview")
    if expected_plan_fingerprint:
        require_matching_plan_fingerprint(expected_plan_fingerprint, plan["plan_fingerprint"])
    if not yes:
        return plan
    if not plan["can_execute"]:
        return {**plan, "preview_only": False, "publish_status": "blocked"}

    transaction = FileTransaction(publication_transaction_roots(runtime.kb_root))
    published: list[str] = []
    try:
        ensure_kb_layout()
        existing = _existing_evidence_by_key(layout)
        for publication in publication_items:
            old = existing.get(publication["evidence_key"], {})
            evidence = _build_evidence(publication=publication, context=context, old=old)
            save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
            existing[publication["evidence_key"]] = evidence
            published.append(evidence["evidence_id"])
        refresh_steps = refresh_query_indexes()
        transaction.commit()
    except (Exception, SystemExit) as exc:
        transaction.restore()
        return {
            **plan,
            "preview_only": False,
            "publish_status": "failed",
            "published_evidence_ids": [],
            "published_count": 0,
            "write_authorized": True,
            "written_count": 0,
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
        "write_authorized": True,
        "written_count": len(published),
        "index_refresh_status": "completed",
        "index_refresh_steps": refresh_steps,
        "rolled_back": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or publish reviewed photo-book exercises. Workflow: inspect -> map-pages -> OCR -> "
            "review -> classify -> publish -> query/ask. Default is a zero-write plan; --yes executes "
            "the same plan only when --plan-fingerprint still matches."
        )
    )
    parser.add_argument("--book-root", required=True)
    parser.add_argument(
        "--chapter-id",
        action="append",
        default=[],
        help="limit publication to the listed confirmed chapter IDs; repeat for multiple chapters",
    )
    parser.add_argument("--yes", action="store_true", help="execute the reviewed plan and refresh all retrieval indexes")
    parser.add_argument("--plan-fingerprint", help="fingerprint printed by the preview; reject execution if inputs drifted")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = publish_exercises(
        book_root=Path(args.book_root).resolve(),
        yes=args.yes,
        expected_plan_fingerprint=args.plan_fingerprint,
        chapter_ids=set(getattr(args, "chapter_id", []) or []) or None,
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get("publish_status") == "failed" or (args.yes and payload.get("publish_status") == "blocked"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
