#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import (
    allocate_kb_id,
    build_provenance_record,
    canonical_book_title,
    ensure_kb_layout,
    is_placeholder,
    load_all_json,
    load_json,
    normalize_context,
    now_iso,
    save_json,
    stable_fingerprint,
    validate_entity_contract,
)

PLAN_JSON = "00_分片计划.json"
FORBIDDEN_ORIGIN_TYPES = {"profile_hint", "title_inference", "placeholder"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-json")
    parser.add_argument("--source-id")
    parser.add_argument("--chapter-id")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def meaningful(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not is_placeholder(text)


def context_from_ids(source_id: str | None, chapter_id: str | None) -> dict[str, Any]:
    layout = ensure_kb_layout()
    matches = []
    for chapter in load_all_json(layout["manifest_chapters"]):
        if source_id and chapter.get("source_id") != source_id:
            continue
        if chapter_id and chapter.get("chapter_id") != chapter_id:
            continue
        matches.append(chapter)
    if not matches:
        raise SystemExit("[ERROR] no chapter manifest matched for evidence extraction")
    context_path = Path(matches[0].get("context_json_path", ""))
    if not context_path.exists():
        raise SystemExit(f"[ERROR] missing context json: {context_path}")
    return normalize_context(load_json(context_path))


def build_text_lines(chunk_payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    seen = set()

    def append_line(text: str) -> None:
        value = str(text or "").strip()
        if not meaningful(value):
            return
        if value in seen:
            return
        seen.add(value)
        lines.append(value)

    summary = str(chunk_payload.get("focus_summary", "")).strip()
    append_line(summary)
    for item in chunk_payload.get("core_concepts", []):
        name = str(item.get("name", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if meaningful(name) or meaningful(summary):
            append_line(f"概念: {name} {summary}".strip())
    for item in chunk_payload.get("rules_or_formulas", []):
        name = str(item.get("name", "")).strip()
        content = str(item.get("content", "")).strip()
        if meaningful(name) or meaningful(content):
            append_line(f"规则: {name} {content}".strip())
    for item in chunk_payload.get("example_types", []):
        name = str(item.get("name", "")).strip()
        pattern = str(item.get("pattern", "")).strip()
        if meaningful(name) or meaningful(pattern):
            append_line(f"题型: {name} {pattern}".strip())
    for item in chunk_payload.get("confusions", []):
        text = str(item or "").strip()
        if meaningful(text):
            append_line(f"易混点: {text}")
    for item in chunk_payload.get("followup_questions", []):
        text = str(item or "").strip()
        if meaningful(text):
            append_line(f"后续可问: {text}")
    for item in build_ocr_overlay_refs(chunk_payload):
        text = str(item.get("text", "")).strip()
        if meaningful(text):
            append_line(f"OCR修订: {text}")
    return lines


def build_formula_list(chunk_payload: dict[str, Any]) -> list[str]:
    formulas: list[str] = []
    for item in chunk_payload.get("rules_or_formulas", []):
        latex = str(item.get("latex", "")).strip()
        if latex:
            formulas.append(latex)
    return formulas


def build_ocr_overlay_refs(chunk_payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in chunk_payload.get("ocr_chunk_candidates", []):
        if not isinstance(item, dict):
            continue
        review_status = str(item.get("review_status", "")).strip()
        corrected_text = str(item.get("corrected_text", "")).strip()
        text_source = str(item.get("text_source", "")).strip()
        if review_status != "accepted":
            continue
        if not corrected_text and text_source != "review_overlay":
            continue
        source_span = item.get("source_span", {}) if isinstance(item.get("source_span", {}), dict) else {}
        locator = source_span.get("locator", {}) if isinstance(source_span.get("locator", {}), dict) else {}
        refs.append(
            {
                "block_id": str(item.get("block_id", "")).strip(),
                "block_type": str(item.get("block_type", "")).strip(),
                "text": str(item.get("text", "")).strip(),
                "raw_text": str(item.get("raw_text", "")).strip(),
                "corrected_text": corrected_text,
                "review_status": review_status,
                "note": str(item.get("note", "")).strip(),
                "confidence": item.get("confidence", 0.0),
                "bbox": list(item.get("bbox", []) or []),
                "locator": {
                    "page_start": locator.get("page_start", ""),
                    "page_end": locator.get("page_end", ""),
                    "image_start": locator.get("image_start", ""),
                    "image_end": locator.get("image_end", ""),
                    "block_ids": list(locator.get("block_ids", []) or []),
                },
            }
        )
    return refs


def source_spans_for(chunk_payload: dict[str, Any]) -> list[dict[str, Any]]:
    spans = chunk_payload.get("source_spans", [])
    if isinstance(spans, list):
        return [span for span in spans if isinstance(span, dict)]
    return []


def origin_type_for(chunk_payload: dict[str, Any]) -> str:
    return str(chunk_payload.get("origin_type", "")).strip()


def verification_status_for(chunk_payload: dict[str, Any]) -> str:
    return str(chunk_payload.get("verification_status", "")).strip()


def has_required_locator(span: dict[str, Any]) -> bool:
    locator = span.get("locator", {})
    return all(meaningful(locator.get(key, "")) for key in ("page_start", "page_end", "image_start", "image_end"))


def should_publish_chunk(chunk_payload: dict[str, Any]) -> bool:
    origin_type = origin_type_for(chunk_payload)
    verification_status = verification_status_for(chunk_payload)
    spans = source_spans_for(chunk_payload)
    if not origin_type or not verification_status or not spans:
        return False
    if origin_type == "placeholder":
        return False
    if verification_status != "source_grounded":
        return False
    if not chunk_payload.get("source_grounded"):
        return False
    if not chunk_payload.get("provenance"):
        return False
    if any(not meaningful(span.get("file_id", "")) for span in spans):
        return False
    if any(not has_required_locator(span) for span in spans):
        return False
    if origin_type in FORBIDDEN_ORIGIN_TYPES and not has_confirmed_page_classification_refs(chunk_payload):
        return False
    return True


def has_confirmed_page_classification_refs(chunk_payload: dict[str, Any]) -> bool:
    refs = chunk_payload.get("page_classification_refs", [])
    if not isinstance(refs, list):
        return False
    for item in refs:
        if not isinstance(item, dict):
            continue
        if str(item.get("classification_status", "")).strip() == "confirmed":
            return True
    return False


def evidence_key_for(context: dict[str, Any], chunk_payload: dict[str, Any], chunk_id: str) -> str:
    return stable_fingerprint(
        {
            "subject": context.get("subject", ""),
            "source_id": context.get("source_id", ""),
            "chapter_id": context.get("chapter_id", ""),
            "chunk_id": chunk_id,
            "origin_type": origin_type_for(chunk_payload),
            "source_spans": source_spans_for(chunk_payload),
        }
    )


def evidence_type_for(chunk_payload: dict[str, Any], plan_chunk: dict[str, Any]) -> str:
    usage_values = [
        str(plan_chunk.get("usage_hint", "")).strip(),
        str(plan_chunk.get("focus_hint", "")).strip(),
        str(chunk_payload.get("section", "")).strip(),
    ]
    joined = " ".join(value for value in usage_values if value)
    if "公式" in joined or "定理" in joined or "规则" in joined:
        return "rule"
    if "例题" in joined or "题型" in joined:
        return "example"
    if "习题" in joined:
        return "exercise"
    if "易混" in joined:
        return "confusion"
    return "concept"


def stale_status_for_replaced_evidence(new_evidence_id: str) -> dict[str, Any]:
    return {
        "verification_status": "stale",
        "mapping_status": "stale",
        "accepted_syllabus_nodes": [],
        "syllabus_candidates": [],
        "source_grounded": False,
        "replaced_by_evidence_id": new_evidence_id,
        "stale_reason": "replaced-by-newer-chunk-origin",
        "updated_at": now_iso(),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    context = normalize_context(load_json(Path(args.context_json))) if args.context_json else context_from_ids(args.source_id, args.chapter_id)
    batch_dir = Path(context["context_json_path"]).parent
    chunk_dir = batch_dir / "30_片段提取"
    plan_path = batch_dir / "10_分片计划" / PLAN_JSON
    if not plan_path.exists():
        raise SystemExit(f"[ERROR] missing chunk plan: {plan_path}")
    plan_payload = load_json(plan_path)
    plan_map = {item.get("chunk_id"): item for item in plan_payload.get("chunks", [])}
    layout = ensure_kb_layout()
    existing_items = load_all_json(layout["evidence"])
    existing = {
        (item.get("chapter_id"), item.get("chunk_id"), item.get("origin_type") or item.get("origin")): item
        for item in existing_items
    }
    created: list[dict[str, Any]] = []
    active_by_chunk: dict[tuple[str, str], str] = {}
    for chunk_path in sorted(chunk_dir.glob("chunk-*.json")):
        chunk_payload = load_json(chunk_path)
        chunk_id = str(chunk_payload.get("chunk_id") or chunk_path.stem).strip()
        plan_chunk = plan_map.get(chunk_id, {})
        if not should_publish_chunk(chunk_payload):
            continue
        lines = build_text_lines(chunk_payload)
        if not lines:
            continue
        origin_type = origin_type_for(chunk_payload)
        current = existing.get((context.get("chapter_id", ""), chunk_id, origin_type), {})
        evidence_id = current.get("evidence_id") or allocate_kb_id("evidence", context["subject"])
        source_spans = source_spans_for(chunk_payload)
        first_span = source_spans[0]
        last_span = source_spans[-1]
        first_locator = first_span.get("locator", {})
        last_locator = last_span.get("locator", {})
        locator = {
            "page_start": first_locator.get("page_start", plan_chunk.get("page_start", "")),
            "page_end": last_locator.get("page_end", plan_chunk.get("page_end", "")),
            "image_start": first_locator.get("image_start", plan_chunk.get("image_start", "")),
            "image_end": last_locator.get("image_end", plan_chunk.get("image_end", "")),
        }
        source_grounded = bool(chunk_payload.get("source_grounded"))
        verification_status = verification_status_for(chunk_payload)
        evidence_key = current.get("evidence_key") or evidence_key_for(context, chunk_payload, chunk_id)
        provenance = chunk_payload.get("provenance") or build_provenance_record(
            origin_type=origin_type,
            verification_status=verification_status,
            source_spans=source_spans,
            source_grounded=source_grounded,
        )
        evidence = {
            "evidence_id": evidence_id,
            "evidence_key": evidence_key,
            "subject": context["subject"],
            "book_title": canonical_book_title(context),
            "source_id": context.get("source_id", ""),
            "chapter_id": context.get("chapter_id", ""),
            "chapter_title": context.get("chapter_title", ""),
            "chunk_id": chunk_id,
            "chunk_kb_id": plan_chunk.get("chunk_kb_id", ""),
            "locator": locator,
            "evidence_type": evidence_type_for(chunk_payload, plan_chunk),
            "title": chunk_payload.get("section") or plan_chunk.get("section_guess") or chunk_id,
            "content": "\n".join(lines),
            "formula_latex": build_formula_list(chunk_payload),
            "origin_type": origin_type,
            "verification_status": verification_status,
            "confidence": 0.92 if source_grounded else 0.72,
            "source_grounded": source_grounded,
            "source_spans": source_spans,
            "ocr_overlay_refs": build_ocr_overlay_refs(chunk_payload),
            "page_classification_refs": list(chunk_payload.get("page_classification_refs", []) or []),
            "provenance": provenance,
            "syllabus_candidates": current.get("syllabus_candidates", []),
            "accepted_syllabus_nodes": current.get("accepted_syllabus_nodes", []),
            "mapping_status": current.get("mapping_status", "unmapped"),
            "context_json_path": context.get("context_json_path", ""),
            "chunk_extract_path": str(chunk_path),
            "updated_at": now_iso(),
        }
        validate_entity_contract("evidence", evidence)
        save_json(layout["evidence"] / f"{evidence_id}.json", evidence)
        created.append(evidence)
        active_by_chunk[(context.get("chapter_id", ""), chunk_id)] = evidence_id
    stale_count = 0
    for item in existing_items:
        evidence_id = str(item.get("evidence_id", "")).strip()
        chapter_id = str(item.get("chapter_id", "")).strip()
        chunk_id = str(item.get("chunk_id", "")).strip()
        replacement_id = active_by_chunk.get((chapter_id, chunk_id), "")
        if not evidence_id or not replacement_id or evidence_id == replacement_id:
            continue
        if item.get("verification_status") == "stale" and item.get("replaced_by_evidence_id") == replacement_id:
            continue
        stale_payload = dict(item)
        stale_payload.update(stale_status_for_replaced_evidence(replacement_id))
        save_json(layout["evidence"] / f"{evidence_id}.json", stale_payload)
        stale_count += 1
    if args.format == "json":
        print(json.dumps({"count": len(created), "stale_count": stale_count, "items": created}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
