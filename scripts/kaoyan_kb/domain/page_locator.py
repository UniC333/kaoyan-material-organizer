from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common import ensure_kb_layout, load_json_or_default, save_json
from config import load_runtime_config


PAGE_LOCATOR_INDEX_NAME = "page_locator_index.json"
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


def _metadata_paths(source_root: Path, metadata_dirname: str) -> dict[str, Path]:
    root = source_root / metadata_dirname
    return {
        "book_asset": root / "book_asset.json",
        "page_assets": root / "page_assets.json",
        "page_mappings": root / "page_mappings.json",
    }


def build_page_locator_index() -> dict[str, Any]:
    runtime = load_runtime_config()
    layout = ensure_kb_layout()
    entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    manifests_root = layout["manifests"] / "sources"
    for manifest_path in sorted(manifests_root.glob("*.json")):
        source = load_json_or_default(manifest_path, {})
        if source.get("status") != "active" or source.get("material_type") != "chapter-photo":
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
        if not evidence_id:
            continue
        for ref in evidence.get("page_classification_refs", []) or []:
            if not isinstance(ref, dict) or not _is_formal_evidence_ref(ref):
                continue
            source_sha = str(ref.get("source_file_sha256") or "").strip()
            printed_page = int(ref.get("printed_page", 0) or 0)
            if source_sha and printed_page:
                evidence_ids_by_page.setdefault((source_sha, printed_page), []).append(evidence_id)
    for entry in entries:
        key = (str(entry.get("source_image_sha256") or ""), int(entry.get("printed_page", 0) or 0))
        entry["evidence_ids"] = sorted(set(evidence_ids_by_page.get(key, [])))
    entries.sort(key=lambda item: (item["subject"], item["normalized_book_title"], item["printed_page"], item["source_id"]))
    sources.sort(key=lambda item: (item["subject"], item["normalized_book_title"], item["source_id"]))
    payload = {
        "schema_version": "page-locator.v1",
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
            "evidence_ids": list(match.get("evidence_ids", []) or []),
        }
    )
    return base


def evidence_matches_locator(evidence: dict[str, Any], locator: dict[str, Any]) -> bool:
    requested_page = locator.get("requested_page")
    source_sha = str(locator.get("source_image_sha256") or "")
    if not requested_page or not source_sha:
        return False
    for ref in evidence.get("page_classification_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        if int(ref.get("printed_page", 0) or 0) != int(requested_page):
            continue
        if str(ref.get("source_file_sha256") or "") == source_sha:
            return True
    return False
