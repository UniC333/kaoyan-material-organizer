#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import allocate_kb_id, ensure_kb_layout, load_all_json, load_json_or_default, now_iso, save_json, stable_fingerprint
from config import load_runtime_config
from kaoyan_kb.domain.book_series import exercise_key, load_book_series_index
from ocr.cache import cache_paths_for_request
from ocr.review import LOW_CONFIDENCE_THRESHOLD


TYPE_PATTERNS = (
    ("choice", re.compile(r"选择题")),
    ("fill_blank", re.compile(r"填空题")),
    ("worked", re.compile(r"(?:解答题|计算题|证明题)")),
)
QUESTION_PATTERN = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*(.*)$")
CHAPTER_PATTERN = re.compile(r"-(?:B|SB|A|SA)-(CALC|LA|PROB)-(\d+)$")


def _source_for_root(book_root: Path) -> dict[str, Any]:
    layout = ensure_kb_layout()
    target = str(book_root.resolve()).casefold()
    matches = [item for item in load_all_json(layout["manifest_sources"]) if str(Path(str(item.get("source_path") or "")).resolve()).casefold() == target and item.get("material_type") == "chapter-photo" and item.get("status") == "active"]
    if len(matches) != 1:
        raise SystemExit("exactly one active chapter-photo source is required; run book register-photo-source first")
    return matches[0]


def _volume(book_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for series in load_book_series_index().get("series", []):
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
        requires_review = block_type in {"table", "formula"} or confidence < LOW_CONFIDENCE_THRESHOLD
        decision = overlay.get(block_id, {})
        if requires_review and decision.get("review_status") not in {"accepted", "ignored"}:
            blockers.append(block_id)
            continue
        if decision.get("review_status") == "rejected":
            blockers.append(block_id)
            continue
        corrected = str(decision.get("corrected_text") or "").strip()
        raw = str(candidate.get("text") or "").strip()
        text = corrected if decision.get("review_status") == "accepted" and corrected else raw
        if text:
            lines.append(text)
    return "\n".join(lines), blockers


def _existing_evidence_by_key() -> dict[str, dict[str, Any]]:
    layout = ensure_kb_layout()
    return {str(item.get("evidence_key") or ""): item for item in load_all_json(layout["evidence"]) if item.get("evidence_key")}


def publish_exercises(*, book_root: Path, write: bool) -> dict[str, Any]:
    runtime = load_runtime_config()
    metadata = book_root / runtime.paper_book_metadata_dir
    statuses = load_json_or_default(metadata / "page_ocr_status.json", {})
    classifications = load_json_or_default(metadata / "page_classifications.json", {})
    assets = load_json_or_default(metadata / "page_assets.json", {})
    if not statuses or not classifications or not assets:
        raise SystemExit("page OCR status, page classifications, and page assets are required")
    book_id = str(assets.get("book_id") or "")
    series, volume = _volume(book_id)
    source = _source_for_root(book_root)
    files_by_path = {str(Path(str(item.get("absolute_path") or "")).resolve()).casefold(): item for item in source.get("files", [])}
    class_by_page = {str(item.get("page_id") or ""): item for item in classifications.get("items", []) if isinstance(item, dict)}
    status_by_page = {str(item.get("page_id") or ""): item for item in statuses.get("items", []) if isinstance(item, dict)}
    pages_by_chapter: dict[str, list[dict[str, Any]]] = {}
    blocked_pages: list[dict[str, Any]] = []
    blocked_chapters: set[str] = set()
    for page_id, classification in class_by_page.items():
        chapter_id = str(classification.get("chapter_id") or "")
        if classification.get("classification_status") != "confirmed" or not chapter_id:
            continue
        if status_by_page.get(page_id, {}).get("status") != "completed":
            blocked_pages.append({"page_id": page_id, "printed_page": classification.get("printed_page"), "chapter_id": chapter_id, "reason": "ocr_not_completed"})
            blocked_chapters.add(chapter_id)
    for status in statuses.get("items", []):
        if status.get("status") != "completed":
            continue
        classification = class_by_page.get(str(status.get("page_id") or ""), {})
        if classification.get("classification_status") != "confirmed" or not classification.get("chapter_id"):
            blocked_pages.append({"page_id": status.get("page_id"), "reason": "classification_not_confirmed"})
            continue
        chapter_id = str(classification["chapter_id"])
        normalized = load_json_or_default(Path(str(status.get("normalized_path") or "")), {})
        overlay = _overlay_by_block(runtime.ocr_cache_root, str(status.get("request_key") or ""))
        text, blockers = _effective_text(normalized, overlay)
        if blockers:
            blocked_pages.append({"page_id": status.get("page_id"), "printed_page": status.get("printed_page"), "chapter_id": chapter_id, "reason": "review_pending", "block_ids": blockers})
            blocked_chapters.add(chapter_id)
            continue
        file_item = files_by_path.get(str(Path(str(status.get("source_image_path") or "")).resolve()).casefold(), {})
        if not file_item:
            blocked_pages.append({"page_id": status.get("page_id"), "chapter_id": chapter_id, "reason": "source_file_not_registered"})
            blocked_chapters.add(chapter_id)
            continue
        pages_by_chapter.setdefault(chapter_id, []).append({"status": status, "classification": classification, "text": text, "file": file_item})

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
        pages.sort(key=lambda item: int(item["status"].get("printed_page", 0) or 0))
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
                        parse_issues.append({"chapter_id": chapter_id, "exercise_type": current_type, "exercise_number": number, "reason": "duplicate_exercise_key"})
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

    existing = _existing_evidence_by_key()
    layout = ensure_kb_layout()
    written = 0
    preview: list[dict[str, Any]] = []
    for candidate in candidates:
        key = exercise_key(series["series_id"], candidate["stage"], candidate["part"], candidate["chapter_number"], candidate["exercise_type"], candidate["exercise_number"])
        content = "\n".join(line for line in candidate["lines"] if line).strip()
        evidence_key = stable_fingerprint({"exercise_key": key, "book_id": book_id, "content": content, "source_sha256": [page["status"].get("source_image_sha256") for page in candidate["pages"].values()]})
        prior = existing.get(evidence_key, {})
        evidence_id = str(prior.get("evidence_id") or allocate_kb_id("evidence", str(series.get("subject") or "数学")))
        refs = []
        spans = []
        for printed_page, page in sorted(candidate["pages"].items()):
            classification = page["classification"]
            status = page["status"]
            refs.append({"page_classification_id": classification.get("page_classification_id"), "page_id": status.get("page_id"), "book_id": book_id, "book_title": volume.get("title", ""), "chapter_id": candidate["chapter_id"], "chapter_title": classification.get("chapter_title", ""), "printed_page": printed_page, "source_file_sha256": status.get("source_image_sha256", ""), "classification_status": "confirmed"})
            spans.append({"source_id": source["source_id"], "file_id": page["file"].get("file_id", ""), "source_file_sha256": status.get("source_image_sha256", ""), "locator": {"page_start": printed_page, "page_end": printed_page, "image_start": status.get("source_image_path", ""), "image_end": status.get("source_image_path", "")}})
        locator = {"page_start": min(candidate["pages"]), "page_end": max(candidate["pages"]), "image_start": spans[0]["locator"]["image_start"], "image_end": spans[-1]["locator"]["image_end"]}
        evidence = {
            "evidence_id": evidence_id,
            "evidence_key": evidence_key,
            "subject": series.get("subject", "数学"),
            "source_id": source["source_id"],
            "chapter_id": candidate["chapter_id"],
            "chunk_id": key,
            "title": f"{volume.get('title', '')} {candidate['exercise_type']} {candidate['exercise_number']}",
            "content": content,
            "origin_type": "paper_book_reviewed_ocr",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "source_spans": spans,
            "locator": locator,
            "page_classification_refs": refs,
            "provenance": {"publisher": "publish_book_exercises.py", "review_gate": "all_flagged_blocks_resolved", "published_at": now_iso(), "origin_type": "paper_book_reviewed_ocr", "verification_status": "source_grounded", "source_grounded": True, "source_spans": spans},
            "book_id": book_id,
            "book_title": volume.get("title", ""),
            "exercise_key": key,
            "exercise_stage": candidate["stage"],
            "exercise_part": candidate["part"],
            "exercise_chapter_number": candidate["chapter_number"],
            "exercise_type": candidate["exercise_type"],
            "exercise_number": candidate["exercise_number"],
            "exercise_role": volume.get("role", ""),
        }
        preview.append({"exercise_key": key, "evidence_id": evidence_id, "printed_pages": sorted(candidate["pages"]), "content_length": len(content)})
        if write:
            written += int(save_json(layout["evidence"] / f"{evidence_id}.json", evidence))
    return {"book_id": book_id, "book_root": str(book_root), "write_authorized": write, "candidate_count": len(candidates), "written_count": written, "blocked_pages": blocked_pages, "parse_issues": parse_issues, "items": preview}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    payload = publish_exercises(book_root=Path(args.book_root).resolve(), write=args.yes)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
