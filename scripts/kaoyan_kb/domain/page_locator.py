from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, save_json
from config import load_runtime_config


PAGE_LOCATOR_INDEX_NAME = "page_locator_index.json"
PDF_PAGE_MAPPING_QUEUE = "pdf-page-mapping"
EXCLUDED_PATH_PARTS = {".local-api-smoke", ".tmp", "tmp", "tests", "fixtures", "__pycache__"}


def normalize_book_title(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s·・:：_\-—（）()《》]+", "", text)


def parse_exercise_label(query: str) -> str:
    text = str(query or "")
    match = re.search(r"(?:例|题|习题)\s*([0-9]+(?:[.．-][0-9]+)+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return "例" + re.sub(r"[．-]", ".", match.group(1))


def _is_formal_source_path(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    return not bool(lowered.intersection(EXCLUDED_PATH_PARTS))


def _is_formal_evidence_ref(ref: dict[str, Any]) -> bool:
    if "demo" in str(ref.get("book_id") or "").lower():
        return False
    paths = " ".join(str(ref.get(key) or "") for key in ("chapter_view_path", "section_view_path"))
    return ".local-api-smoke" not in paths.lower()


def _printed_page(value: Any) -> int:
    match = re.search(r"([0-9]+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _is_explicit_printed_page(value: Any) -> bool:
    return bool(re.search(r"(?:第\s*\d+\s*页|^\s*\d+\s*页\s*$)", str(value or "")))


def _pdf_page(value: Any) -> int:
    return _printed_page(value)


def extract_printed_page_from_ocr(content: Any) -> int:
    """Read only an explicitly printed page number from the page OCR itself.

    A numeric offset is never inferred: a page without a clear page-number token
    must be reviewed before it can become a formal locator entry.
    """
    lines = [str(line).strip() for line in str(content or "").splitlines() if str(line).strip()]
    candidates: list[int] = []
    for line in lines[:16]:
        match = re.fullmatch(r"(?:第\s*)?(\d{1,4})(?:\s*页)?", line)
        if match:
            candidates.append(int(match.group(1)))
    unique = sorted(set(candidates))
    return unique[0] if len(unique) == 1 else 0


def evidence_page_refs(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Read legacy classification refs and page-bounded OCR source spans."""
    refs = [ref for ref in evidence.get("page_classification_refs", []) or [] if isinstance(ref, dict)]
    for span in evidence.get("source_spans", []) or []:
        if not isinstance(span, dict):
            continue
        locator = span.get("locator", {}) if isinstance(span.get("locator"), dict) else {}
        # OCR spans may describe either a printed page (legacy paper-book OCR)
        # or a PDF render page. Only the former is a page mapping by itself.
        printed_page = _printed_page(locator.get("page_start")) if _is_explicit_printed_page(locator.get("page_start")) else 0
        source_sha = str(span.get("source_file_sha256") or "").strip()
        if printed_page and source_sha:
            refs.append(
                {
                    "source_file_sha256": source_sha,
                    "printed_page": printed_page,
                    "book_id": evidence.get("book_id", ""),
                    "chapter_id": span.get("chapter_id") or evidence.get("chapter_id", ""),
                    "chapter_title": evidence.get("chapter_title", ""),
                }
            )
    return refs


def _metadata_paths(source_root: Path, metadata_dirname: str) -> dict[str, Path]:
    root = source_root / metadata_dirname
    return {
        "book_asset": root / "book_asset.json",
        "page_assets": root / "page_assets.json",
        "page_mappings": root / "page_mappings.json",
    }


def _pdf_source_records(source: dict[str, Any], layout: dict[str, Path], approved_overrides: dict[int, dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return verified PDF page records and fail-closed review items."""
    source_id = str(source.get("source_id") or "").strip()
    approved_overrides = approved_overrides or {}
    evidence_by_pdf_page: dict[int, list[dict[str, Any]]] = {}
    for evidence_path in sorted(layout["evidence"].glob("*.json")):
        evidence = load_json_or_default(evidence_path, {})
        if (
            evidence.get("source_id") != source_id
            or evidence.get("origin_type") != "pdf_page_ocr"
            or evidence.get("verification_status") != "reviewed"
            or not evidence.get("source_grounded")
            or evidence.get("mapping_status") == "stale"
        ):
            continue
        pdf_page = _pdf_page((evidence.get("locator") or {}).get("page_start"))
        if not pdf_page:
            continue
        evidence_by_pdf_page.setdefault(pdf_page, []).append(evidence)

    records: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []
    for pdf_page, evidences in sorted(evidence_by_pdf_page.items()):
        if len(evidences) != 1:
            review_items.append({"kind": "duplicate-pdf-page-evidence", "pdf_page": pdf_page, "evidence_ids": sorted(str(item.get("evidence_id") or "") for item in evidences)})
            continue
        evidence = evidences[0]
        source_spans = [item for item in evidence.get("source_spans", []) or [] if isinstance(item, dict)]
        source_sha = str((source_spans[0] if source_spans else {}).get("source_file_sha256") or "").strip()
        review = approved_overrides.get(pdf_page, {})
        if not review.get("page_header_verified") or not review.get("printed_page") or review.get("source_file_sha256") != source_sha:
            review_items.append({"kind": "page-header-unverified", "pdf_page": pdf_page, "evidence_id": evidence.get("evidence_id", ""), "source_file_sha256": source_sha})
            continue
        printed_page = int(review["printed_page"])
        records.append({
            "subject": str(source.get("subject") or evidence.get("subject") or "").strip(),
            "book_id": str(evidence.get("book_id") or source_id).strip(),
            "book_title": str(evidence.get("book_title") or source.get("source_name") or "").strip(),
            "source_id": source_id,
            "printed_page": printed_page,
            "pdf_page": pdf_page,
            "source_asset_kind": "pdf",
            "source_asset_path": str(((source.get("files") or [{}])[0] or {}).get("absolute_path") or source.get("source_path") or "").strip(),
            "source_file_sha256": source_sha,
            "evidence_ids": [str(evidence.get("evidence_id") or "")],
        })

    seen_printed: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        seen_printed.setdefault(int(record["printed_page"]), []).append(record)
    invalid_pdf_pages: set[int] = set()
    for printed_page, items in seen_printed.items():
        if len(items) > 1:
            invalid_pdf_pages.update(int(item["pdf_page"]) for item in items)
            review_items.append({"kind": "duplicate-printed-page", "printed_page": printed_page, "pdf_pages": sorted(int(item["pdf_page"]) for item in items)})
    ordered = sorted(records, key=lambda item: int(item["pdf_page"]))
    for previous, current in zip(ordered, ordered[1:]):
        if int(current["printed_page"]) - int(previous["printed_page"]) != int(current["pdf_page"]) - int(previous["pdf_page"]):
            invalid_pdf_pages.update({int(previous["pdf_page"]), int(current["pdf_page"])})
            review_items.append({"kind": "printed-page-discontinuity", "previous_pdf_page": previous["pdf_page"], "previous_printed_page": previous["printed_page"], "pdf_page": current["pdf_page"], "printed_page": current["printed_page"]})
    return [item for item in records if int(item["pdf_page"]) not in invalid_pdf_pages], review_items


def build_page_locator_index() -> dict[str, Any]:
    runtime = load_runtime_config()
    layout = ensure_kb_layout()
    entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    pdf_mapping_blockers: list[dict[str, Any]] = []
    manifests_root = layout["manifests"] / "sources"
    for manifest_path in sorted(manifests_root.glob("*.json")):
        source = load_json_or_default(manifest_path, {})
        if source.get("status") != "active":
            continue
        if source.get("material_type") == "book-pdf":
            source_id = str(source.get("source_id") or "").strip()
            queue_path = layout["review_queues"] / PDF_PAGE_MAPPING_QUEUE / f"{source_id}.json"
            existing_queue = load_json_or_default(queue_path, {})
            page_review = load_json_or_default(layout["review_queues"] / "pdf-page-review" / f"{source_id}.json", {})
            approved_overrides = {
                int(item.get("pdf_page", 0) or 0): item
                for item in page_review.get("items", []) or []
                if isinstance(item, dict) and item.get("review_status") == "accepted" and int(item.get("pdf_page", 0) or 0)
            }
            pdf_entries, pdf_review_items = _pdf_source_records(source, layout, approved_overrides)
            title = str(source.get("source_name") or "").strip()
            subject = str(source.get("subject") or "").strip()
            entries.extend(pdf_entries)
            sources.append({
                "source_id": source_id,
                "subject": subject,
                "book_id": source_id,
                "book_title": title,
                "normalized_book_title": normalize_book_title(title),
                "source_root": str(source.get("source_path") or ""),
                "material_type": "book-pdf",
                "mapping_status": "mapped" if pdf_entries and not pdf_review_items else "unmapped",
                "review_item_count": len(pdf_review_items),
            })
            save_json(queue_path, {"queue_type": PDF_PAGE_MAPPING_QUEUE, "source_id": source_id, "approved_overrides": list(existing_queue.get("approved_overrides", []) or []), "items": pdf_review_items, "summary": {"open_count": len(pdf_review_items), "mapped_count": len(pdf_entries)}})
            pdf_mapping_blockers.extend({"source_id": source_id, **item} for item in pdf_review_items)
            continue
        if source.get("material_type") != "chapter-photo":
            continue
        source_root = Path(str(source.get("source_path", "")).strip())
        if not source_root.is_dir() or not _is_formal_source_path(source_root):
            continue
        paths = _metadata_paths(source_root, runtime.paper_book_metadata_dir)
        book_asset = load_json_or_default(paths["book_asset"], {})
        page_assets = load_json_or_default(paths["page_assets"], {})
        page_mappings = load_json_or_default(paths["page_mappings"], {})
        book_id = str(book_asset.get("book_id") or page_assets.get("book_id") or "").strip()
        book_title = str(book_asset.get("book_title") or source.get("source_name") or "").strip()
        subject = str(book_asset.get("subject") or source.get("subject") or "").strip()
        source_id = str(source.get("source_id") or "").strip()
        mapping_items = [item for item in page_mappings.get("items", []) if isinstance(item, dict)]
        sources.append(
            {
                "source_id": source_id,
                "subject": subject,
                "book_id": book_id,
                "book_title": book_title,
                "normalized_book_title": normalize_book_title(book_title),
                "source_root": str(source_root),
                "mapping_status": "mapped" if mapping_items else "unmapped",
            }
        )
        assets_by_id = {
            str(item.get("page_id")): item
            for item in page_assets.get("items", [])
            if isinstance(item, dict) and item.get("page_id")
        }
        for mapping in mapping_items:
            if mapping.get("mapping_status") != "mapped" or mapping.get("printed_page") is None:
                continue
            page_id = str(mapping.get("page_id") or "")
            asset = assets_by_id.get(page_id, {})
            source_image_path = str(asset.get("source_image_path") or "").strip()
            if not source_image_path or not Path(source_image_path).is_file():
                continue
            entries.append(
                {
                    "subject": subject,
                    "book_id": book_id,
                    "book_title": book_title,
                    "normalized_book_title": normalize_book_title(book_title),
                    "source_id": source_id,
                    "page_id": page_id,
                    "printed_page": int(mapping["printed_page"]),
                    "scan_index": int(mapping.get("scan_index", 0) or 0),
                    "source_image_path": source_image_path,
                    "source_image_sha256": str(asset.get("source_image_sha256") or ""),
                    "mapping_method": str(mapping.get("mapping_method") or ""),
                }
            )
    evidence_ids_by_page: dict[tuple[str, int], list[str]] = {}
    for evidence_path in sorted(layout["evidence"].glob("*.json")):
        evidence = load_json_or_default(evidence_path, {})
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        if (
            not evidence_id
            or evidence.get("verification_status") == "stale"
            or evidence.get("mapping_status") == "stale"
            # 多页章节提取仍可用于宽泛检索；但正式印刷页定位只能指向
            # 经审核的单页 OCR，避免旧提取遮蔽当前页级证据并使按页回答歧义。
            or evidence.get("origin_type") == "chunk_extract"
        ):
            continue
        for ref in evidence_page_refs(evidence):
            if not isinstance(ref, dict) or not _is_formal_evidence_ref(ref):
                continue
            source_sha = str(ref.get("source_file_sha256") or "").strip()
            printed_page = int(ref.get("printed_page", 0) or 0)
            if source_sha and printed_page:
                evidence_ids_by_page.setdefault((source_sha, printed_page), []).append(evidence_id)
    for entry in entries:
        if entry.get("source_asset_kind") == "pdf":
            entry["normalized_book_title"] = normalize_book_title(entry.get("book_title"))
            continue
        key = (str(entry.get("source_image_sha256") or ""), int(entry.get("printed_page", 0) or 0))
        entry["evidence_ids"] = sorted(set(evidence_ids_by_page.get(key, [])))
    entries.sort(key=lambda item: (item["subject"], item["normalized_book_title"], item["printed_page"], item["source_id"]))
    sources.sort(key=lambda item: (item["subject"], item["normalized_book_title"], item["source_id"]))
    payload = {
        "schema_version": "page-locator.v2",
        "generated_by": "kaoyan-material-organizer",
        "entries": entries,
        "sources": sources,
        "summary": {
            "entry_count": len(entries),
            "source_count": len(sources),
            "mapped_source_count": sum(1 for item in sources if item["mapping_status"] == "mapped"),
            "unmapped_source_count": sum(1 for item in sources if item["mapping_status"] == "unmapped"),
        },
    }
    save_json(layout["indexes"] / PAGE_LOCATOR_INDEX_NAME, payload)
    if pdf_mapping_blockers:
        details = ", ".join(f"{item['source_id']}:PDF{item.get('pdf_page', '?')}:{item['kind']}" for item in pdf_mapping_blockers)
        raise SystemExit(f"[ERROR] PDF page mapping review is incomplete: {details}")
    return payload


def load_page_locator_index() -> dict[str, Any]:
    layout = ensure_kb_layout()
    return load_json_or_default(layout["indexes"] / PAGE_LOCATOR_INDEX_NAME, {"entries": [], "sources": []})


def _book_title_matches(requested: str, candidate: str) -> bool:
    needle = normalize_book_title(requested)
    haystack = normalize_book_title(candidate)
    return bool(needle and haystack and (needle == haystack or needle in haystack or haystack in needle))


def resolve_page_locator(*, subject: str, book_title: str | None, printed_page: int, exercise_label: str = "") -> dict[str, Any]:
    index = load_page_locator_index()
    subject_entries = [
        item for item in index.get("entries", [])
        if item.get("subject") == subject and int(item.get("printed_page", 0) or 0) == printed_page
    ]
    if book_title:
        candidates = [item for item in subject_entries if _book_title_matches(book_title, str(item.get("book_title", "")))]
    else:
        candidates = subject_entries
    distinct_books = {(item.get("book_id"), item.get("book_title")) for item in candidates}
    source_catalog = [item for item in index.get("sources", []) if item.get("subject") == subject]
    if book_title:
        source_catalog = [item for item in source_catalog if _book_title_matches(book_title, str(item.get("book_title", "")))]

    base = {
        "requested_page": printed_page,
        "requested_position": None,
        "requested_book_title": str(book_title or ""),
        "requested_exercise_label": exercise_label,
        "match_status": "not_found",
        "exercise_match_status": "unverified" if exercise_label else "not_requested",
        "book_id": "",
        "book_title": "",
        "source_id": "",
        "page_id": "",
        "source_image_path": "",
        "source_image_sha256": "",
        "source_asset_kind": "",
        "source_asset_path": "",
        "pdf_page": 0,
        "evidence_ids": [],
        "match_basis": "formal_page_locator_index",
        "candidates": [],
        "matched_evidence_id": "",
        "matched_chunk_id": "",
        "snippets": [],
    }
    if len(distinct_books) > 1 and not book_title:
        base["match_status"] = "ambiguous"
        base["candidates"] = [
            {"book_id": book_id, "book_title": title}
            for book_id, title in sorted(distinct_books, key=lambda item: (str(item[1]), str(item[0])))
        ]
        return base
    if len(candidates) != 1:
        if source_catalog:
            base["match_status"] = "unmapped"
            base["candidates"] = [
                {"book_id": item.get("book_id", ""), "book_title": item.get("book_title", ""), "source_id": item.get("source_id", "")}
                for item in source_catalog
            ]
        return base
    match = candidates[0]
    base.update(
        {
            "match_status": "exact_asset",
            "book_id": match.get("book_id", ""),
            "book_title": match.get("book_title", ""),
            "source_id": match.get("source_id", ""),
            "page_id": match.get("page_id", ""),
            "source_image_path": match.get("source_image_path", ""),
            "source_image_sha256": match.get("source_image_sha256", ""),
            "source_asset_kind": match.get("source_asset_kind", "image"),
            "source_asset_path": match.get("source_asset_path", match.get("source_image_path", "")),
            "pdf_page": int(match.get("pdf_page", 0) or 0),
            "evidence_ids": list(match.get("evidence_ids", []) or []),
        }
    )
    return base


def evidence_matches_locator(evidence: dict[str, Any], locator: dict[str, Any]) -> bool:
    evidence_id = str(evidence.get("evidence_id") or "")
    if evidence_id and evidence_id in set(locator.get("evidence_ids", []) or []):
        requested_pdf_page = int(locator.get("pdf_page", 0) or 0)
        actual_pdf_page = _pdf_page((evidence.get("locator") or {}).get("page_start"))
        return not requested_pdf_page or actual_pdf_page == requested_pdf_page
    requested_page = locator.get("requested_page")
    source_sha = str(locator.get("source_image_sha256") or "")
    if not requested_page or not source_sha:
        return False
    for ref in evidence_page_refs(evidence):
        if not isinstance(ref, dict):
            continue
        if int(ref.get("printed_page", 0) or 0) != int(requested_page):
            continue
        if str(ref.get("source_file_sha256") or "") == source_sha:
            return True
    return False
