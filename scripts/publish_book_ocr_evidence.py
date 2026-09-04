#!/usr/bin/env python3
"""Preview and transactionally publish reviewed photo-book OCR evidence."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import (
    allocate_kb_id,
    build_provenance_record,
    ensure_kb_layout,
    load_json_or_default,
    now_iso,
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


SENSITIVE = {"table", "formula", "equation"}


def reviewed_candidate_text(candidate: dict, overlay_item: dict) -> str:
    """Return the accepted human correction when one exists."""
    corrected_text = str(overlay_item.get("corrected_text", "")).strip()
    if overlay_item.get("review_status") == "accepted" and corrected_text:
        return corrected_text
    return str(candidate.get("text", "")).strip()


def existing_evidence_by_key(evidence_dir: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for evidence_path in evidence_dir.glob("*.json") if evidence_dir.is_dir() else []:
        payload = load_json_or_default(evidence_path, {})
        if payload.get("evidence_key"):
            result[payload["evidence_key"]] = payload
    return result


def publication_is_allowed(*, require_complete: bool, blocked: list[dict]) -> bool:
    return not (require_complete and blocked)


def select_evidence_id(*, old: dict, subject: str) -> str:
    return old.get("evidence_id") or allocate_kb_id("evidence", subject)


def collect_publication_items(
    *,
    runtime,
    root: Path,
    assets: dict,
    status: dict,
    classes: dict,
    locator_by_page: dict,
    chapter_ids: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Preflight every registered page without allocating IDs or writing evidence."""
    status_by_page = {item.get("page_id"): item for item in status.get("items", []) if item.get("page_id")}
    asset_items = assets.get("items", [])
    page_ids = [item.get("page_id") for item in asset_items if item.get("page_id")]
    page_ids.extend(page_id for page_id in status_by_page if page_id not in page_ids)
    publication_items: list[dict] = []
    blocked: list[dict] = []

    for page_id in page_ids:
        item = status_by_page.get(page_id)
        cls = classes.get(page_id, {})
        # 显式章节范围只消费已归类到该章节的页面，避免本次发布扩散到全书。
        if chapter_ids and str(cls.get("chapter_id") or "") not in chapter_ids:
            continue
        if not item or item.get("status") != "completed":
            blocked.append({"page_id": page_id, "reason": "ocr-not-completed"})
            continue
        page = locator_by_page.get(page_id, {})
        if not page or cls.get("classification_status") != "confirmed":
            blocked.append({"page_id": page_id, "reason": "page-not-confirmed"})
            continue
        normalized_path = Path(str(item.get("normalized_path", "")))
        norm = load_json_or_default(normalized_path, {}) if str(item.get("normalized_path", "")).strip() else {}
        request_key = str(item.get("request_key") or norm.get("request_key") or "").strip()
        overlay_path = cache_paths_for_request(runtime.ocr_cache_root, request_key)["overlay"]
        overlay = {
            row.get("block_id"): row
            for row in load_json_or_default(overlay_path, {}).get("items", [])
            if row.get("block_id")
        }
        candidates = norm.get("chunk_candidates", [])
        pending = [
            candidate
            for candidate in candidates
            if candidate.get("block_type") in SENSITIVE
            and overlay.get(candidate.get("block_id"), {}).get("review_status") != "accepted"
        ]
        if pending:
            blocked.append(
                {
                    "page_id": page_id,
                    "reason": "sensitive-block-review-pending",
                    "block_ids": [candidate.get("block_id") for candidate in pending],
                }
            )
            continue
        reviewed_texts = [reviewed_candidate_text(candidate, overlay.get(candidate.get("block_id"), {})) for candidate in candidates]
        text = "\n".join(value for value in reviewed_texts if value)
        if not text:
            blocked.append({"page_id": page_id, "reason": "empty-ocr"})
            continue
        publication_items.append(
            {
                "page_id": page_id,
                "status": item,
                "page": page,
                "classification": cls,
                "candidates": candidates,
                "content": text,
                "normalized": norm,
                "normalized_path": normalized_path,
                "overlay_path": overlay_path,
            }
        )
    return publication_items, blocked


def _book_payload(root: Path) -> dict[str, Any]:
    book_path = root / "book.yaml"
    payload = load_json_or_default(book_path, {})
    if isinstance(payload, dict) and payload:
        return payload
    if not book_path.is_file():
        raise SystemExit(f"book.yaml is missing: {book_path}")
    result: dict[str, Any] = {}
    for line in book_path.read_text(encoding="utf-8").splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("'").strip('"')
    return result


def _locator_conflicts(index: dict[str, Any]) -> list[dict[str, Any]]:
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in index.get("entries", []) or []:
        if isinstance(entry, dict) and str(entry.get("page_id") or "").strip():
            by_page[str(entry["page_id"]).strip()].append(entry)
    conflicts: list[dict[str, Any]] = []
    for page_id, entries in sorted(by_page.items()):
        if len(entries) <= 1:
            continue
        conflicts.append(
            {
                "reason": "page-locator-conflict",
                "page_id": page_id,
                "entries": [
                    {
                        "source_id": item.get("source_id", ""),
                        "printed_page": item.get("printed_page"),
                        "source_image_sha256": item.get("source_image_sha256", ""),
                    }
                    for item in entries
                ],
            }
        )
    return conflicts


def _locator_by_page(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("page_id")): item
        for item in index.get("entries", []) or []
        if isinstance(item, dict) and item.get("page_id")
    }


def _evidence_key(publication: dict[str, Any]) -> str:
    item = publication["status"]
    return stable_fingerprint(
        {
            "page_id": publication["page_id"],
            "source_sha": item.get("source_image_sha256", ""),
            "request_key": item.get("request_key", ""),
        }
    )


def _strict_image_inputs(
    *,
    runtime,
    assets: dict[str, Any],
    publication_items: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    blocked: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    plan_items: list[dict[str, Any]] = []
    assets_by_id = {
        str(item.get("page_id")): item
        for item in assets.get("items", []) or []
        if isinstance(item, dict) and item.get("page_id")
    }
    for publication in publication_items:
        page_id = str(publication["page_id"])
        status = publication["status"]
        locator = publication["page"]
        asset = assets_by_id.get(page_id, {})
        try:
            printed_page = int(locator.get("printed_page", 0) or 0)
        except (TypeError, ValueError):
            printed_page = 0
        if printed_page < 1:
            blocked.append({"page_id": page_id, "reason": "printed-page-mapping-missing"})
            continue
        image_path = Path(str(locator.get("source_image_path") or asset.get("source_image_path") or ""))
        inputs.append(file_input(f"source-image/{page_id}", image_path))
        if not image_path.is_file():
            blocked.append({"page_id": page_id, "reason": "source-image-missing"})
            continue
        actual_sha = sha256_for_file(image_path)
        declared = {
            str(value).strip()
            for value in (
                status.get("source_image_sha256"),
                locator.get("source_image_sha256"),
                asset.get("source_image_sha256"),
            )
            if str(value or "").strip()
        }
        if not declared or declared != {actual_sha}:
            blocked.append(
                {
                    "page_id": page_id,
                    "reason": "source-image-hash-mismatch",
                    "declared_sha256": sorted(declared),
                    "actual_sha256": actual_sha,
                }
            )
            continue

        normalized_path = Path(str(status.get("normalized_path") or publication.get("normalized_path") or ""))
        inputs.append(file_input(f"normalized/{page_id}", normalized_path))
        normalized = publication.get("normalized", {})
        if not normalized_path.is_file() or not normalized:
            blocked.append({"page_id": page_id, "reason": "normalized-ocr-missing"})
            continue
        if str(normalized.get("source_file_sha256") or "").strip() != actual_sha:
            blocked.append({"page_id": page_id, "reason": "normalized-source-hash-mismatch"})
            continue
        if str(normalized.get("request_key") or "").strip() != str(status.get("request_key") or "").strip():
            blocked.append({"page_id": page_id, "reason": "ocr-request-key-mismatch"})
            continue
        overlay_path = publication["overlay_path"]
        inputs.append(file_input(f"overlay/{page_id}", overlay_path))
        overlay_payload = load_json_or_default(overlay_path, {})
        if overlay_payload.get("request_key") and str(overlay_payload.get("request_key")) != str(status.get("request_key") or ""):
            blocked.append({"page_id": page_id, "reason": "overlay-request-key-mismatch"})
            continue
        overlay_sha = str(overlay_payload.get("source_file_sha256") or "").strip()
        if overlay_sha and overlay_sha != actual_sha:
            blocked.append({"page_id": page_id, "reason": "overlay-source-image-hash-mismatch"})
            continue
        evidence_key = _evidence_key(publication)
        old = existing.get(evidence_key, {})
        if old:
            inputs.append(file_input(f"existing-evidence/{page_id}", Path(str(old.get("_path") or ""))))
        plan_items.append(
            {
                "page_id": page_id,
                "printed_page": printed_page,
                "source_id": locator.get("source_id", ""),
                "source_image_sha256": actual_sha,
                "request_key": status.get("request_key", ""),
                "evidence_key": evidence_key,
                "existing_evidence_id": old.get("evidence_id", ""),
                "chapter_id": publication["classification"].get("chapter_id", ""),
            }
        )
    return plan_items


def _prepare_plan(
    *,
    book_root: Path,
    chapter_ids: set[str] | None = None,
    require_complete: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], Any, dict[str, Path]]:
    runtime = load_runtime_config()
    root = Path(book_root)
    metadata = root / runtime.paper_book_metadata_dir
    assets_path = metadata / "page_assets.json"
    status_path = metadata / "page_ocr_status.json"
    classifications_path = metadata / "page_classifications.json"
    assets = load_json_or_default(assets_path, {})
    status = load_json_or_default(status_path, {})
    classes_payload = load_json_or_default(classifications_path, {})
    if not assets or not status:
        raise SystemExit("page assets or OCR status missing; run inspect and OCR first")
    classes = {
        item.get("page_id"): item
        for item in classes_payload.get("items", [])
        if item.get("page_id")
    }
    layout = read_only_layout(runtime.kb_root)
    locator = load_page_locator_index()
    availability = dict(locator.get("_availability") or {"available": True})
    global_blockers: list[dict[str, Any]] = []
    if not availability.get("available", True):
        global_blockers.append(
            {
                "reason": str(availability.get("reason") or "page_locator_index_unavailable"),
                "detail": str(availability.get("detail") or ""),
            }
        )
    global_blockers.extend(_locator_conflicts(locator))

    selected_chapter_ids = {str(item).strip() for item in chapter_ids or set() if str(item).strip()}
    publication_items, blocked = collect_publication_items(
        runtime=runtime,
        root=root,
        assets=assets,
        status=status,
        classes=classes,
        locator_by_page=_locator_by_page(locator),
        chapter_ids=selected_chapter_ids or None,
    )
    inputs = [
        file_input("book.yaml", root / "book.yaml"),
        file_input("page-assets", assets_path),
        file_input("page-ocr-status", status_path),
        file_input("page-classifications", classifications_path),
        file_input("page-locator-index", layout["indexes"] / "page_locator_index.json"),
        file_input("id-counters", layout["indexes"] / "id_counters.json"),
    ]
    existing = existing_evidence_by_key(layout["evidence"])
    for payload in existing.values():
        payload["_path"] = str(layout["evidence"] / f"{payload.get('evidence_id', '')}.json")
    plan_items = _strict_image_inputs(
        runtime=runtime,
        assets=assets,
        publication_items=publication_items,
        inputs=inputs,
        blocked=blocked,
        existing=existing,
    )
    unique_inputs: dict[tuple[str, str], dict[str, Any]] = {}
    for item in inputs:
        unique_inputs[(str(item.get("label", "")), str(item.get("path", "")))] = item
    inputs = [unique_inputs[key] for key in sorted(unique_inputs)]
    plan_items.sort(key=lambda item: str(item.get("page_id", "")))
    blocked.sort(key=lambda item: (str(item.get("page_id", "")), str(item.get("reason", ""))))
    book = _book_payload(root)
    arguments = {
        "book_root": str(root),
        "chapter_ids": sorted(selected_chapter_ids),
        "require_complete": bool(require_complete),
    }
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
        "publication_kind": "book-ocr",
        "arguments": arguments,
        "inputs": inputs,
        "items": plan_items,
        "blocked": blocked,
        "global_blockers": global_blockers,
        "writes": writes,
    }
    fingerprint = publication_plan_fingerprint(base)
    can_execute = not global_blockers and publication_is_allowed(require_complete=require_complete, blocked=blocked) and bool(plan_items)
    plan = {
        **base,
        "plan_fingerprint": fingerprint,
        "preview_only": True,
        "can_execute": can_execute,
        "publish_status": "ready" if can_execute else "blocked",
        "book_id": assets.get("book_id", ""),
        "chapter_ids": sorted(selected_chapter_ids),
        "published_evidence_ids": [],
        "published_count": 0,
        "blocked_count": len(blocked),
    }
    return plan, publication_items, book, runtime, layout


def build_publication_plan(*, book_root: Path, chapter_ids: set[str] | None = None, require_complete: bool = False) -> dict[str, Any]:
    return _prepare_plan(book_root=book_root, chapter_ids=chapter_ids, require_complete=require_complete)[0]


def _build_evidence(*, publication: dict[str, Any], book: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    item = publication["status"]
    page = publication["page"]
    cls = publication["classification"]
    page_id = publication["page_id"]
    candidates = publication["candidates"]
    source_id = str(page.get("source_id", ""))
    printed = page.get("printed_page")
    source_sha = str(item.get("source_image_sha256", ""))
    key = _evidence_key(publication)
    evidence_id = select_evidence_id(old=old, subject=str(book.get("subject", "数学")))
    span = {
        "source_id": source_id,
        "file_id": page_id,
        "source_file_sha256": source_sha,
        "chapter_id": cls.get("chapter_id", ""),
        "chunk_id": page_id,
        "origin_type": "reviewed_ocr",
        "verification_status": "source_grounded",
        "review_status": "accepted",
        "locator": {
            "page_start": f"第{printed}页",
            "page_end": f"第{printed}页",
            "image_start": item.get("scan_index"),
            "image_end": item.get("scan_index"),
            "block_ids": [candidate.get("block_id") for candidate in candidates],
            "bbox": [],
        },
        "notes": "All table/formula/equation blocks accepted before publication.",
    }
    evidence = {
        "evidence_id": evidence_id,
        "evidence_key": key,
        "subject": book.get("subject", "数学"),
        "book_title": book.get("book_title", ""),
        "source_id": source_id,
        "chapter_id": cls.get("chapter_id", ""),
        "chapter_title": cls.get("chapter_title", ""),
        "chunk_id": page_id,
        "title": f"第{printed}页 OCR",
        "content": publication["content"],
        "origin_type": "reviewed_ocr",
        "verification_status": "source_grounded",
        "review_status": "accepted",
        "source_grounded": True,
        "ocr_request_key": item.get("request_key", ""),
        "ocr_source_image_sha256": source_sha,
        "locator": span["locator"],
        "source_spans": [span],
        "page_classification_refs": [
            {
                "book_id": book.get("book_id", ""),
                "book_title": book.get("book_title", ""),
                "source_id": source_id,
                "page_id": page_id,
                "printed_page": printed,
                "source_file_sha256": source_sha,
                "source_image_sha256": source_sha,
                "request_key": item.get("request_key", ""),
                "chapter_id": cls.get("chapter_id", ""),
                "chapter_title": cls.get("chapter_title", ""),
                "source_image_path": page.get("source_image_path", ""),
            }
        ],
        "provenance": build_provenance_record(
            origin_type="reviewed_ocr",
            verification_status="source_grounded",
            source_spans=[span],
            source_grounded=True,
        ),
        "updated_at": now_iso(),
    }
    validate_entity_contract("evidence", evidence)
    return evidence


def publish(
    *,
    book_root: Path,
    chapter_ids: set[str] | None = None,
    require_complete: bool = False,
    yes: bool = False,
    expected_plan_fingerprint: str | None = None,
) -> dict[str, Any]:
    plan, publication_items, book, runtime, layout = _prepare_plan(
        book_root=book_root,
        chapter_ids=chapter_ids,
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
        existing = existing_evidence_by_key(layout["evidence"])
        for publication in publication_items:
            key = _evidence_key(publication)
            evidence = _build_evidence(publication=publication, book=book, old=existing.get(key, {}))
            save_json(layout["evidence"] / f"{evidence['evidence_id']}.json", evidence)
            existing[key] = evidence
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or publish reviewed photo-book OCR. Workflow: inspect -> map-pages -> OCR -> "
            "review -> classify -> publish -> query/ask. Default is a zero-write plan; --yes executes "
            "the same plan only when --plan-fingerprint still matches."
        )
    )
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--chapter-id", action="append", default=[])
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--yes", action="store_true", help="execute the reviewed plan and refresh all retrieval indexes")
    parser.add_argument("--plan-fingerprint", help="fingerprint printed by the preview; reject execution if inputs drifted")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    payload = publish(
        book_root=Path(args.book_root),
        chapter_ids={str(item).strip() for item in args.chapter_id if str(item).strip()},
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
