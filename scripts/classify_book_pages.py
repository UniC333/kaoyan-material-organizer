#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import load_json_or_default, now_iso, sanitize_name, save_json
from config import load_runtime_config

MACHINE_GENERATED_BY = "kaoyan-material-organizer"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def _book_paths(book_root: Path, metadata_dirname: str) -> dict[str, Path]:
    metadata_root = book_root / metadata_dirname
    return {
        "root": metadata_root,
        "page_assets": metadata_root / "page_assets.json",
        "page_mappings": metadata_root / "page_mappings.json",
        "page_ocr_status": metadata_root / "page_ocr_status.json",
        "chapter_definitions": metadata_root / "chapter_definitions.json",
        "page_classifications": metadata_root / "page_classifications.json",
        "chapter_views_root": book_root / "views" / "by-chapter",
        "section_views_root": book_root / "views" / "by-section",
        "chapters_yaml": book_root / "chapters.yaml",
    }


def _book_title_from_root(book_root: Path) -> str:
    book_yaml = book_root / "book.yaml"
    if book_yaml.exists():
        for line in book_yaml.read_text(encoding="utf-8", errors="replace").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or ":" not in text:
                continue
            key, value = text.split(":", 1)
            if key.strip() not in {"book_title", "title"}:
                continue
            cleaned = value.strip().strip("'").strip('"')
            if cleaned:
                return cleaned
    return book_root.name


def _build_page_classification_index(
    *,
    runtime,
    book_title: str,
    page_assets_payload: dict[str, Any],
    page_classifications: list[dict[str, Any]],
    chapter_view_paths: list[str],
    section_view_paths: list[str],
) -> tuple[Path, dict[str, Any]]:
    chapter_view_by_id: dict[str, str] = {}
    for path_text in chapter_view_paths:
        path = Path(path_text)
        chapter_id = path.stem.split("_", 1)[0]
        if chapter_id:
            chapter_view_by_id[chapter_id] = str(path)

    section_view_by_id: dict[str, str] = {}
    for path_text in section_view_paths:
        path = Path(path_text)
        section_id = path.stem.split("_", 1)[0]
        if section_id:
            section_view_by_id[section_id] = str(path)

    page_asset_by_id = {
        str(item.get("page_id", "")).strip(): item
        for item in page_assets_payload.get("items", [])
        if item.get("page_id")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    book_title = str(book_title or page_assets_payload.get("book_title", "")).strip()
    for item in page_classifications:
        page_id = str(item.get("page_id", "")).strip()
        asset = page_asset_by_id.get(page_id, {})
        source_sha = str(asset.get("source_image_sha256", "")).strip()
        if not source_sha:
            continue
        grouped.setdefault(source_sha, []).append(
            {
                "book_id": item.get("book_id", ""),
                "book_title": book_title,
                "page_id": page_id,
                "printed_page": item.get("printed_page"),
                "chapter_id": item.get("chapter_id"),
                "chapter_title": item.get("chapter_title"),
                "section_id": item.get("section_id"),
                "section_title": item.get("section_title"),
                "classification_status": item.get("classification_status"),
                "classification_method": item.get("classification_method"),
                "chapter_view_path": chapter_view_by_id.get(str(item.get("chapter_id", "")).strip(), ""),
                "section_view_path": section_view_by_id.get(str(item.get("section_id", "")).strip(), ""),
            }
        )

    payload = {
        "version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "items": [
            {
                "source_file_sha256": source_sha,
                "refs": refs,
            }
            for source_sha, refs in sorted(grouped.items())
        ],
    }
    index_path = runtime.ocr_cache_root / "indexes" / "page_classification_index.json"
    return index_path, payload


def _merge_page_classification_index(
    *,
    index_path: Path,
    incoming_payload: dict[str, Any],
    current_book_id: str,
) -> dict[str, Any]:
    existing_payload = load_json_or_default(index_path, {})
    grouped: dict[str, list[dict[str, Any]]] = {}
    if isinstance(existing_payload, dict):
        for item in existing_payload.get("items", []):
            source_sha = str(item.get("source_file_sha256", "")).strip()
            if not source_sha:
                continue
            for ref in item.get("refs", []):
                if str(ref.get("book_id", "")).strip() == current_book_id:
                    continue
                grouped.setdefault(source_sha, []).append(ref)

    for item in incoming_payload.get("items", []):
        source_sha = str(item.get("source_file_sha256", "")).strip()
        if not source_sha:
            continue
        grouped.setdefault(source_sha, []).extend(list(item.get("refs", []) or []))

    return {
        "version": incoming_payload.get("version", 1),
        "created_at": existing_payload.get("created_at") if isinstance(existing_payload, dict) else incoming_payload.get("created_at"),
        "updated_at": now_iso(),
        "items": [
            {
                "source_file_sha256": source_sha,
                "refs": refs,
            }
            for source_sha, refs in sorted(grouped.items())
            if refs
        ],
    }


def _load_chapters_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit("chapters.yaml is missing")
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"chapters": []}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("chapters.yaml currently must use JSON-compatible content") from exc
    if not isinstance(payload, dict):
        raise SystemExit("chapters.yaml must contain an object payload")
    return payload


def _normalize_text(text: str) -> str:
    return "".join(str(text or "").strip().lower().split())


def _find_range_match(printed_page: int | None, chapters: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if printed_page is None:
        return None, None
    for chapter in chapters:
        if int(chapter.get("page_start", 0) or 0) <= printed_page <= int(chapter.get("page_end", 0) or 0):
            for section in chapter.get("sections", []):
                if int(section.get("page_start", 0) or 0) <= printed_page <= int(section.get("page_end", 0) or 0):
                    return chapter, section
            return chapter, None
    return None, None


def _ocr_title_candidates_from_payload(payload: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for item in payload.get("chunk_candidates", []):
        if str(item.get("candidate_kind", "")) != "title_candidate":
            continue
        if not bool(item.get("eligible_for_chunk_hint")):
            continue
        text = str(item.get("text", "")).strip()
        if text:
            titles.append(text)
    return titles


def _ocr_title_candidates_for_page(page_status: dict[str, Any], *, source_image_sha256: str) -> list[str]:
    normalized_path_text = str(page_status.get("normalized_path", "")).strip()
    if normalized_path_text:
        normalized_path = Path(normalized_path_text)
        if normalized_path.exists() and normalized_path.is_file():
            payload = load_json_or_default(normalized_path, {})
            return _ocr_title_candidates_from_payload(payload)

    runtime = load_runtime_config()
    normalized_dir = runtime.ocr_cache_root / "normalized"
    if not normalized_dir.exists():
        return []
    for normalized_path in sorted(normalized_dir.glob("*.json")):
        payload = load_json_or_default(normalized_path, {})
        if str(payload.get("source_file_sha256", "")).strip() != str(source_image_sha256 or "").strip():
            continue
        titles = _ocr_title_candidates_from_payload(payload)
        if titles:
            return titles
    return []


def _render_chapter_view(chapter: dict[str, Any], chapter_pages: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        f"generated_by: {MACHINE_GENERATED_BY}",
        f"chapter_id: {chapter['chapter_id']}",
        "---",
        f"# {chapter['chapter_title']}",
        "",
        f"- 页段范围: 第{chapter['page_start']}页 - 第{chapter['page_end']}页",
        "",
        "## 页面列表",
        "",
    ]
    for item in chapter_pages:
        section_title = str(item.get("section_title", "")).strip()
        section_suffix = f" | {section_title}" if section_title else ""
        lines.append(
            f"- 第{item.get('printed_page')}页 | {item.get('page_id')} | {item.get('classification_status')} | {item.get('classification_method')}{section_suffix}"
        )
    if chapter.get("sections"):
        lines.extend(["", "## 小节范围", ""])
        for section in chapter["sections"]:
            lines.append(f"- {section['section_title']}: 第{section['page_start']}页 - 第{section['page_end']}页")
    return "\n".join(lines) + "\n"


def _render_section_view(chapter: dict[str, Any], section: dict[str, Any], section_pages: list[dict[str, Any]]) -> str:
    lines = [
        "---",
        f"generated_by: {MACHINE_GENERATED_BY}",
        f"chapter_id: {chapter['chapter_id']}",
        f"section_id: {section['section_id']}",
        "---",
        f"# {section['section_title']}",
        "",
        f"- 所属章节: {chapter['chapter_title']}",
        f"- 页段范围: 第{section['page_start']}页 - 第{section['page_end']}页",
        "",
        "## 页面列表",
        "",
    ]
    for item in section_pages:
        lines.append(
            f"- 第{item.get('printed_page')}页 | {item.get('page_id')} | {item.get('classification_status')} | {item.get('classification_method')}"
        )
    return "\n".join(lines) + "\n"


def _cleanup_generated_views(views_root: Path) -> None:
    if not views_root.exists():
        return
    for path in views_root.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"generated_by: {MACHINE_GENERATED_BY}" in content:
            path.unlink()


def classify_book_pages(*, book_root: Path, format_name: str = "json") -> dict[str, Any]:
    runtime = load_runtime_config()
    paths = _book_paths(book_root, runtime.paper_book_metadata_dir)
    page_assets_payload = load_json_or_default(paths["page_assets"], {})
    page_mappings_payload = load_json_or_default(paths["page_mappings"], {})
    if not page_assets_payload or not page_mappings_payload:
        raise SystemExit("page_assets.json or page_mappings.json is missing")

    chapters_payload = _load_chapters_config(paths["chapters_yaml"])
    chapters = list(chapters_payload.get("chapters", []))
    page_status_payload = load_json_or_default(paths["page_ocr_status"], {})
    status_by_page_id = {
        str(item.get("page_id", "")).strip(): item
        for item in page_status_payload.get("items", [])
        if item.get("page_id")
    }
    mapping_by_page_id = {
        str(item.get("page_id", "")).strip(): item
        for item in page_mappings_payload.get("items", [])
        if item.get("page_id")
    }
    existing_classifications = {
        str(item.get("page_id", "")).strip(): item
        for item in load_json_or_default(paths["page_classifications"], {}).get("items", [])
        if item.get("page_id")
    }

    chapter_definitions: list[dict[str, Any]] = []
    page_classifications: list[dict[str, Any]] = []
    chapter_pages: dict[str, list[dict[str, Any]]] = {}
    section_pages: dict[str, list[dict[str, Any]]] = {}

    for chapter in chapters:
        chapter_definitions.append(
            {
                "chapter_id": chapter["chapter_id"],
                "book_id": page_assets_payload.get("book_id", ""),
                "chapter_title": chapter["chapter_title"],
                "page_start": int(chapter.get("page_start", 0) or 0),
                "page_end": int(chapter.get("page_end", 0) or 0),
                "definition_status": "confirmed",
                "sections": list(chapter.get("sections", [])),
                "created_at": now_iso(),
                "updated_at": now_iso(),
            }
        )

    for asset in sorted(page_assets_payload.get("items", []), key=lambda item: (int(item.get("scan_index", 0) or 0), str(item.get("page_id", "")))):
        page_id = str(asset.get("page_id", "")).strip()
        mapping = mapping_by_page_id.get(page_id, {})
        printed_page = mapping.get("printed_page")
        chapter_match, section_match = _find_range_match(printed_page, chapters)
        existing = existing_classifications.get(page_id, {})
        classification = {
            "page_classification_id": existing.get("page_classification_id")
            or f"PCLASS-{asset['book_id']}-{int(asset.get('scan_index', 0) or 0):04d}",
            "page_id": page_id,
            "book_id": asset["book_id"],
            "chapter_id": None,
            "chapter_title": None,
            "section_id": None,
            "section_title": None,
            "classification_method": "manual",
            "classification_status": "unassigned",
            "classification_confidence": None,
            "classification_source": "chapters.yaml",
            "confirmed_by": None,
            "confirmed_at": None,
            "updated_at": now_iso(),
            "printed_page": printed_page,
        }
        if chapter_match is not None:
            classification.update(
                {
                    "chapter_id": chapter_match["chapter_id"],
                    "chapter_title": chapter_match["chapter_title"],
                    "section_id": section_match.get("section_id") if section_match else None,
                    "section_title": section_match.get("section_title") if section_match else None,
                    "classification_method": "page_range",
                    "classification_status": "confirmed",
                    "classification_confidence": 1.0,
                    "classification_source": "chapters.yaml:page_range",
                    "confirmed_by": "manual-rule",
                    "confirmed_at": now_iso(),
                }
            )
        else:
            titles = _ocr_title_candidates_for_page(
                status_by_page_id.get(page_id, {}),
                source_image_sha256=str(asset.get("source_image_sha256", "")).strip(),
            )
            normalized_titles = [_normalize_text(item) for item in titles]
            for chapter in chapters:
                chapter_title = str(chapter.get("chapter_title", "")).strip()
                if _normalize_text(chapter_title) and _normalize_text(chapter_title) in normalized_titles:
                    classification.update(
                        {
                            "chapter_id": chapter["chapter_id"],
                            "chapter_title": chapter_title,
                            "classification_method": "ocr_title_candidate",
                            "classification_status": "candidate",
                            "classification_confidence": 0.85,
                            "classification_source": "ocr_title_candidate",
                        }
                    )
                    break
        page_classifications.append(classification)
        if classification["chapter_id"]:
            chapter_pages.setdefault(str(classification["chapter_id"]), []).append(classification)
        if classification["section_id"]:
            section_pages.setdefault(str(classification["section_id"]), []).append(classification)

    chapter_definitions_payload = {
        "book_id": page_assets_payload.get("book_id", ""),
        "created_at": load_json_or_default(paths["chapter_definitions"], {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "items": chapter_definitions,
    }
    page_classifications_payload = {
        "book_id": page_assets_payload.get("book_id", ""),
        "created_at": load_json_or_default(paths["page_classifications"], {}).get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "items": page_classifications,
        "summary": {
            "confirmed_count": sum(1 for item in page_classifications if item["classification_status"] == "confirmed"),
            "candidate_count": sum(1 for item in page_classifications if item["classification_status"] == "candidate"),
            "conflict_count": sum(1 for item in page_classifications if item["classification_status"] == "conflict"),
            "unassigned_count": sum(1 for item in page_classifications if item["classification_status"] == "unassigned"),
        },
    }

    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["chapter_views_root"].mkdir(parents=True, exist_ok=True)
    paths["section_views_root"].mkdir(parents=True, exist_ok=True)
    save_json(paths["chapter_definitions"], chapter_definitions_payload)
    save_json(paths["page_classifications"], page_classifications_payload)
    _cleanup_generated_views(paths["chapter_views_root"])
    _cleanup_generated_views(paths["section_views_root"])

    chapter_view_paths: list[str] = []
    for chapter in chapter_definitions:
        filename = f"{chapter['chapter_id']}_{sanitize_name(chapter['chapter_title'])}.md"
        view_path = paths["chapter_views_root"] / filename
        view_path.write_text(_render_chapter_view(chapter, chapter_pages.get(chapter["chapter_id"], [])), encoding="utf-8")
        chapter_view_paths.append(str(view_path))

    section_view_paths: list[str] = []
    for chapter in chapter_definitions:
        for section in chapter.get("sections", []):
            section_id = str(section.get("section_id", "")).strip()
            if not section_id:
                continue
            filename = f"{section_id}_{sanitize_name(section['section_title'])}.md"
            view_path = paths["section_views_root"] / filename
            view_path.write_text(
                _render_section_view(chapter, section, section_pages.get(section_id, [])),
                encoding="utf-8",
            )
            section_view_paths.append(str(view_path))

    index_path, index_payload = _build_page_classification_index(
        runtime=runtime,
        book_title=_book_title_from_root(book_root),
        page_assets_payload=page_assets_payload,
        page_classifications=page_classifications,
        chapter_view_paths=chapter_view_paths,
        section_view_paths=section_view_paths,
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_payload = _merge_page_classification_index(
        index_path=index_path,
        incoming_payload=index_payload,
        current_book_id=str(page_assets_payload.get("book_id", "")).strip(),
    )
    save_json(index_path, index_payload, ignored_compare_keys=("updated_at", "created_at"))

    return {
        "book_id": page_assets_payload.get("book_id", ""),
        "book_root": str(book_root),
        "chapter_definitions_path": str(paths["chapter_definitions"]),
        "page_classifications_path": str(paths["page_classifications"]),
        "chapter_views_root": str(paths["chapter_views_root"]),
        "section_views_root": str(paths["section_views_root"]),
        "page_classification_index_path": str(index_path),
        "view_paths": chapter_view_paths,
        "section_view_paths": section_view_paths,
        "summary": page_classifications_payload["summary"],
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = classify_book_pages(book_root=Path(args.book_root), format_name=args.format)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
