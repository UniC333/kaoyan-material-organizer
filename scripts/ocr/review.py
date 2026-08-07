from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from common import load_json_or_default, now_iso, save_json
from config import load_runtime_config
from ocr.cache import cache_paths_for_request

LOW_CONFIDENCE_THRESHOLD = 0.6
REVIEW_TYPES = ("table", "equation", "low-confidence")
REVIEW_STATUSES = ("pending", "accepted", "rejected", "ignored")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue")
    queue.add_argument("--book-root", required=True)
    queue.add_argument("--review-type", choices=REVIEW_TYPES)
    queue.add_argument("--format", choices=("json", "quiet"), default="json")

    apply = subparsers.add_parser("apply")
    apply.add_argument("--request-key", required=True)
    apply.add_argument("--block-id", required=True)
    apply.add_argument("--review-status", choices=REVIEW_STATUSES, required=True)
    apply.add_argument("--corrected-text", default="")
    apply.add_argument("--note", default="")
    apply.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _metadata_paths(book_root: Path, metadata_dirname: str) -> dict[str, Path]:
    metadata_root = book_root / metadata_dirname
    return {
        "root": metadata_root,
        "page_ocr_status": metadata_root / "page_ocr_status.json",
        "page_classifications": metadata_root / "page_classifications.json",
    }


def _load_overlay(cache_root: Path, request_key: str) -> dict[str, Any]:
    path = cache_paths_for_request(cache_root, request_key)["overlay"]
    return load_json_or_default(
        path,
        {
            "request_key": request_key,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "items": [],
        },
    )


def _overlay_index(overlay_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in overlay_payload.get("items", []):
        if isinstance(item, dict) and item.get("block_id"):
            index[str(item["block_id"])] = item
    return index


def _review_reasons(candidate: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    block_type = str(candidate.get("block_type", "")).strip().lower()
    confidence = float(candidate.get("confidence", 0.0) or 0.0)
    if block_type == "table":
        reasons.append("table")
    if block_type in {"formula", "equation"}:
        reasons.append("equation")
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("low-confidence")
    return reasons


def queue_review_items(*, book_root: Path, review_type: str | None, format_name: str = "json") -> dict[str, Any]:
    runtime = load_runtime_config()
    paths = _metadata_paths(book_root, runtime.paper_book_metadata_dir)
    status_payload = load_json_or_default(paths["page_ocr_status"], {})
    if not status_payload:
        raise SystemExit("page_ocr_status.json is missing; run book ocr first")
    classifications_by_page_id = {
        str(item.get("page_id", "")).strip(): item
        for item in load_json_or_default(paths["page_classifications"], {}).get("items", [])
        if isinstance(item, dict) and item.get("page_id")
    }

    items: list[dict[str, Any]] = []
    for page_item in status_payload.get("items", []):
        if str(page_item.get("status", "")).strip() != "completed":
            continue
        request_key = str(page_item.get("request_key", "")).strip()
        normalized_path = Path(str(page_item.get("normalized_path", "")).strip())
        if not request_key or not normalized_path.exists():
            continue
        normalized_payload = load_json_or_default(normalized_path, {})
        overlay_payload = _load_overlay(runtime.ocr_cache_root, request_key)
        overlay_by_block = _overlay_index(overlay_payload)
        for candidate in normalized_payload.get("chunk_candidates", []):
            reasons = _review_reasons(candidate)
            if not reasons:
                continue
            if review_type and review_type not in reasons:
                continue
            overlay_item = overlay_by_block.get(str(candidate.get("block_id", "")).strip(), {})
            classification = classifications_by_page_id.get(str(page_item.get("page_id", "")).strip(), {})
            items.append(
                {
                    "book_id": page_item.get("book_id", ""),
                    "page_id": page_item.get("page_id", ""),
                    "printed_page": page_item.get("printed_page"),
                    "printed_page_label": page_item.get("printed_page_label", ""),
                    "chapter_id": classification.get("chapter_id"),
                    "chapter_title": classification.get("chapter_title"),
                    "section_id": classification.get("section_id"),
                    "section_title": classification.get("section_title"),
                    "request_key": request_key,
                    "source_file_sha256": normalized_payload.get("source_file_sha256", ""),
                    "normalized_path": str(normalized_path),
                    "block_id": candidate.get("block_id", ""),
                    "block_type": candidate.get("block_type", ""),
                    "bbox": list(candidate.get("bbox", []) or []),
                    "confidence": candidate.get("confidence", 0.0),
                    "text": candidate.get("text", ""),
                    "review_reasons": reasons,
                    "review_status": overlay_item.get("review_status", "pending"),
                    "corrected_text": overlay_item.get("corrected_text", ""),
                    "note": overlay_item.get("note", ""),
                }
            )

    summary = {
        "total_count": len(items),
        "pending_count": sum(1 for item in items if item["review_status"] == "pending"),
        "accepted_count": sum(1 for item in items if item["review_status"] == "accepted"),
        "rejected_count": sum(1 for item in items if item["review_status"] == "rejected"),
        "ignored_count": sum(1 for item in items if item["review_status"] == "ignored"),
    }
    return {
        "book_root": str(book_root),
        "review_type": review_type or "",
        "items": items,
        "summary": summary,
    }


def apply_overlay(
    *,
    request_key: str,
    block_id: str,
    review_status: str,
    corrected_text: str,
    note: str,
    format_name: str = "json",
) -> dict[str, Any]:
    runtime = load_runtime_config()
    paths = cache_paths_for_request(runtime.ocr_cache_root, request_key)
    normalized_payload = load_json_or_default(paths["normalized"], {})
    if not normalized_payload:
        raise SystemExit(f"normalized OCR payload not found for request_key={request_key}")
    candidate_by_block = {
        str(item.get("block_id", "")).strip(): item
        for item in normalized_payload.get("chunk_candidates", [])
        if item.get("block_id")
    }
    if block_id not in candidate_by_block:
        raise SystemExit(f"unknown block_id for request_key={request_key}: {block_id}")

    overlay_payload = _load_overlay(runtime.ocr_cache_root, request_key)
    items = [item for item in overlay_payload.get("items", []) if isinstance(item, dict)]
    existing_index = {
        str(item.get("block_id", "")).strip(): idx
        for idx, item in enumerate(items)
        if item.get("block_id")
    }
    created_at = overlay_payload.get("created_at") or now_iso()
    overlay_item = {
        "block_id": block_id,
        "review_status": review_status,
        "corrected_text": corrected_text,
        "note": note,
        "updated_at": now_iso(),
    }
    if block_id in existing_index:
        previous = items[existing_index[block_id]]
        if (
            str(previous.get("review_status", "")) == review_status
            and str(previous.get("corrected_text", "")) == corrected_text
            and str(previous.get("note", "")) == note
        ):
            overlay_item["updated_at"] = previous.get("updated_at", overlay_item["updated_at"])
        items[existing_index[block_id]] = overlay_item
    else:
        items.append(overlay_item)

    save_json(
        paths["overlay"],
        {
            "request_key": request_key,
            "source_file_sha256": normalized_payload.get("source_file_sha256", ""),
            "created_at": created_at,
            "updated_at": now_iso(),
            "items": items,
        },
        ignored_compare_keys=(),
    )
    return {
        "saved": True,
        "request_key": request_key,
        "overlay_path": str(paths["overlay"]),
        "block_id": block_id,
        "review_status": review_status,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    if args.command == "queue":
        payload = queue_review_items(
            book_root=Path(args.book_root),
            review_type=args.review_type,
            format_name=args.format,
        )
    else:
        payload = apply_overlay(
            request_key=args.request_key,
            block_id=args.block_id,
            review_status=args.review_status,
            corrected_text=args.corrected_text,
            note=args.note,
            format_name=args.format,
        )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
