from __future__ import annotations

from typing import Any

from common import now_iso, sha256_for_file

NORMALIZER_VERSION = "v1"
HINTABLE_BLOCK_TYPES = {"heading", "title", "paragraph", "list_item"}
NON_HINTABLE_BLOCK_TYPES = {"table", "formula"}
MIN_HINT_CONFIDENCE = 0.6


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_bbox(raw_bbox: Any) -> list[float]:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return []
    return [_as_float(item) for item in raw_bbox]


def _candidate_kind(block_type: str) -> str:
    if block_type in {"heading", "title"}:
        return "title_candidate"
    return "content_candidate"


def _eligible_for_chunk_hint(block_type: str, confidence: float, text: str) -> bool:
    if not text.strip():
        return False
    if block_type in NON_HINTABLE_BLOCK_TYPES:
        return False
    if block_type not in HINTABLE_BLOCK_TYPES:
        return False
    return confidence >= MIN_HINT_CONFIDENCE


def normalize_ocr_payload(raw_payload: dict[str, Any], *, request_key: str, source_file) -> dict[str, Any]:
    pages = raw_payload.get("pages", [])
    source_file_sha256 = sha256_for_file(source_file)
    chunk_candidates: list[dict[str, Any]] = []
    normalized_pages: list[dict[str, Any]] = []

    for page_offset, raw_page in enumerate(pages):
        page_index = int(raw_page.get("page_index", page_offset) or 0)
        blocks = raw_page.get("blocks", [])
        normalized_blocks: list[dict[str, Any]] = []
        for block_offset, raw_block in enumerate(blocks):
            block_id = str(raw_block.get("block_id") or f"OCRBLK-{page_index + 1:03d}-{block_offset + 1:03d}")
            block_type = str(raw_block.get("block_type") or "paragraph").strip().lower() or "paragraph"
            text = str(raw_block.get("text") or "").strip()
            confidence = _as_float(raw_block.get("confidence"), 0.0)
            bbox = _normalize_bbox(raw_block.get("bbox"))
            eligible = _eligible_for_chunk_hint(block_type, confidence, text)
            normalized_block = {
                "block_id": block_id,
                "block_type": block_type,
                "text": text,
                "bbox": bbox,
                "confidence": confidence,
            }
            normalized_blocks.append(normalized_block)
            chunk_candidates.append(
                {
                    "block_id": block_id,
                    "page_index": page_index,
                    "block_type": block_type,
                    "candidate_kind": _candidate_kind(block_type),
                    "text": text,
                    "bbox": bbox,
                    "confidence": confidence,
                    "eligible_for_chunk_hint": eligible,
                    "source_file_sha256": source_file_sha256,
                }
            )

        normalized_pages.append(
            {
                "page_index": page_index,
                "text": str(raw_page.get("text") or "").strip(),
                "blocks": normalized_blocks,
            }
        )

    return {
        "request_key": request_key,
        "provider": raw_payload.get("provider", ""),
        "exact_model": raw_payload.get("exact_model", ""),
        "source_file": str(source_file),
        "source_file_sha256": source_file_sha256,
        "page_count": len(normalized_pages),
        "pages": normalized_pages,
        "chunk_candidates": chunk_candidates,
        "normalizer_version": NORMALIZER_VERSION,
        "normalized_at": now_iso(),
        "published_evidence": False,
    }
