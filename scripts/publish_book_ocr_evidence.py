#!/usr/bin/env python3
"""Publish only fully-reviewed image OCR as page-bounded Q&A evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import (
    allocate_kb_id,
    build_provenance_record,
    ensure_kb_layout,
    load_json_or_default,
    now_iso,
    save_json,
    stable_fingerprint,
    validate_entity_contract,
)
from config import load_runtime_config
from ocr.cache import cache_paths_for_request
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
    for evidence_path in evidence_dir.glob("*.json"):
        payload = load_json_or_default(evidence_path, {})
        if payload.get("evidence_key"):
            result[payload["evidence_key"]] = payload
    return result


def publication_is_allowed(*, require_complete: bool, blocked: list[dict]) -> bool:
    return not (require_complete and blocked)


def select_evidence_id(*, old: dict, subject: str) -> str:
    return old.get("evidence_id") or allocate_kb_id("evidence", subject)


def collect_publication_items(*, runtime, root: Path, assets: dict, status: dict, classes: dict, locator_by_page: dict) -> tuple[list[dict], list[dict]]:
    """Preflight every registered page without allocating IDs or writing evidence."""
    status_by_page = {item.get("page_id"): item for item in status.get("items", []) if item.get("page_id")}
    asset_items = assets.get("items", [])
    page_ids = [item.get("page_id") for item in asset_items if item.get("page_id")]
    page_ids.extend(page_id for page_id in status_by_page if page_id not in page_ids)
    publication_items: list[dict] = []
    blocked: list[dict] = []

    for page_id in page_ids:
        item = status_by_page.get(page_id)
        if not item or item.get("status") != "completed":
            blocked.append({"page_id": page_id, "reason": "ocr-not-completed"})
            continue
        page = locator_by_page.get(page_id, {})
        cls = classes.get(page_id, {})
        if not page or cls.get("classification_status") != "confirmed":
            blocked.append({"page_id": page_id, "reason": "page-not-confirmed"})
            continue
        norm = load_json_or_default(Path(item.get("normalized_path", "")), {})
        overlay_path = cache_paths_for_request(runtime.ocr_cache_root, item["request_key"])["overlay"]
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
        publication_items.append({"page_id": page_id, "status": item, "page": page, "classification": cls, "candidates": candidates, "content": text})
    return publication_items, blocked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--no-refresh-indexes", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()

    runtime = load_runtime_config()
    root = Path(args.book_root)
    meta = root / runtime.paper_book_metadata_dir
    assets = load_json_or_default(meta / "page_assets.json", {})
    status = load_json_or_default(meta / "page_ocr_status.json", {})
    classes = {
        item.get("page_id"): item
        for item in load_json_or_default(meta / "page_classifications.json", {}).get("items", [])
        if item.get("page_id")
    }
    if not assets or not status:
        raise SystemExit("page assets or OCR status missing; run inspect and OCR first")
    layout = ensure_kb_layout()
    locator = load_json_or_default(layout["indexes"] / "page_locator_index.json", {})
    locator_by_page = {item.get("page_id"): item for item in locator.get("entries", []) if item.get("page_id")}
    book = load_json_or_default(root / "book.yaml", {})
    if not book:
        text = (root / "book.yaml").read_text(encoding="utf-8")
        book = {key: value.strip() for key, value in (line.split(":", 1) for line in text.splitlines() if ":" in line)}

    publication_items, blocked = collect_publication_items(
        runtime=runtime,
        root=root,
        assets=assets,
        status=status,
        classes=classes,
        locator_by_page=locator_by_page,
    )
    if not publication_is_allowed(require_complete=args.require_complete, blocked=blocked):
        payload = {
            "book_id": assets.get("book_id"),
            "publish_status": "blocked",
            "published_evidence_ids": [],
            "blocked": blocked,
            "published_count": 0,
            "blocked_count": len(blocked),
            "index_refresh_status": "not_run",
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    evidence_by_key = existing_evidence_by_key(layout["evidence"])
    published: list[str] = []
    for publication in publication_items:
        item = publication["status"]
        page = publication["page"]
        cls = publication["classification"]
        page_id = publication["page_id"]
        candidates = publication["candidates"]
        source_id = page.get("source_id", "")
        printed = page.get("printed_page")
        key = stable_fingerprint(
            {"page_id": page_id, "source_sha": item.get("source_image_sha256"), "request_key": item.get("request_key")}
        )
        old = evidence_by_key.get(key, {})
        evidence_id = select_evidence_id(old=old, subject=str(book.get("subject", "数学")))
        span = {
            "source_id": source_id,
            "file_id": page_id,
            "source_file_sha256": item.get("source_image_sha256", ""),
            "chapter_id": cls.get("chapter_id", ""),
            "chunk_id": page_id,
            "origin_type": "reviewed_ocr",
            "verification_status": "source_grounded",
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
            "source_grounded": True,
            "source_spans": [span],
            "page_classification_refs": [
                {
                    "book_id": assets.get("book_id", ""),
                    "book_title": book.get("book_title", ""),
                    "source_id": source_id,
                    "page_id": page_id,
                    "printed_page": printed,
                    "source_file_sha256": item.get("source_image_sha256", ""),
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
        save_json(layout["evidence"] / f"{evidence_id}.json", evidence)
        published.append(evidence_id)
        evidence_by_key[key] = evidence

    index_refresh_status = "skipped"
    index_refresh_steps: list[str] = []
    index_refresh_error = ""
    return_code = 0
    if not args.no_refresh_indexes:
        try:
            index_refresh_steps = refresh_query_indexes()
            index_refresh_status = "completed"
        except Exception as exc:
            index_refresh_status = "failed"
            index_refresh_error = str(exc)
            return_code = 1

    payload = {
        "book_id": assets.get("book_id"),
        "publish_status": "completed" if return_code == 0 else "index-refresh-failed",
        "published_evidence_ids": published,
        "blocked": blocked,
        "published_count": len(published),
        "blocked_count": len(blocked),
        "index_refresh_status": index_refresh_status,
        "index_refresh_steps": index_refresh_steps,
    }
    if index_refresh_error:
        payload["index_refresh_error"] = index_refresh_error
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
