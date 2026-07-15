#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageFile

from common import load_json_or_default, now_iso, sanitize_name, save_json, sha256_for_file, stable_fingerprint
from config import RuntimeConfig


ImageFile.LOAD_TRUNCATED_IMAGES = True

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
IGNORED_DIRS = {"metadata", "ocr", "review", "views", "__pycache__"}


def _parse_simple_yaml(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    if not path.exists():
        return payload
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        payload[key.strip()] = value.strip().strip("'\"")
    return payload


def _book_metadata_paths(book_root: Path, runtime: RuntimeConfig) -> dict[str, Path]:
    metadata_root = book_root / runtime.paper_book_metadata_dir
    return {
        "root": metadata_root,
        "book_asset": metadata_root / "book_asset.json",
        "page_assets": metadata_root / "page_assets.json",
    }


def _derive_book_id(book_root: Path, metadata_path: Path) -> str:
    existing = load_json_or_default(metadata_path, {})
    if isinstance(existing, dict) and existing.get("book_id"):
        return str(existing["book_id"])
    yaml_payload = _parse_simple_yaml(book_root / "book.yaml")
    if yaml_payload.get("book_id"):
        return yaml_payload["book_id"]
    slug = sanitize_name(book_root.name).upper()
    return f"BOOK-{slug}"


def _discover_candidate_images(book_root: Path, runtime: RuntimeConfig) -> list[Path]:
    incoming_root = book_root / runtime.paper_book_incoming_dir
    search_root = incoming_root if incoming_root.exists() else book_root
    images: list[Path] = []
    for path in sorted(search_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
            continue
        if any(part.lower() in IGNORED_DIRS for part in path.parts):
            continue
        images.append(path)
    return images


def _read_capture_time(path: Path, image: Image.Image) -> tuple[str, str]:
    exif = image.getexif() if hasattr(image, "getexif") else None
    for key in (36867, 306):
        raw_value = exif.get(key) if exif else None
        if not raw_value:
            continue
        try:
            stamp = datetime.strptime(str(raw_value), "%Y:%m:%d %H:%M:%S").isoformat()
            return stamp, "exif"
        except ValueError:
            continue
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(), "file_mtime"


def _blur_score(image: Image.Image) -> float:
    grayscale = image.convert("L").resize((64, 64))
    pixels = list(grayscale.getdata())
    width, height = grayscale.size
    values: list[float] = []
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            center = pixels[y * width + x]
            laplacian = (
                4 * center
                - pixels[y * width + (x - 1)]
                - pixels[y * width + (x + 1)]
                - pixels[(y - 1) * width + x]
                - pixels[(y + 1) * width + x]
            )
            values.append(float(laplacian))
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return round(variance, 3)


def _average_hash(image: Image.Image) -> str:
    grayscale = image.convert("L").resize((8, 8))
    pixels = list(grayscale.getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
    return f"{int(bits, 2):016x}"


def _hamming_distance(left: str, right: str) -> int:
    return sum(ch1 != ch2 for ch1, ch2 in zip(f"{int(left, 16):064b}", f"{int(right, 16):064b}", strict=True))


def _asset_compare_payload(asset: dict[str, Any]) -> dict[str, Any]:
    ignored = {"asset_created_at", "asset_updated_at", "capture_time", "suspected_duplicate_page_ids"}
    return {key: value for key, value in asset.items() if key not in ignored}


def _next_page_id(book_id: str, existing_items: list[dict[str, Any]]) -> str:
    prefix = f"PAGE-{sanitize_name(book_id)}-"
    numbers: list[int] = []
    for item in existing_items:
        page_id = str(item.get("page_id") or "")
        if not page_id.startswith(prefix):
            continue
        suffix = page_id.removeprefix(prefix)
        if suffix.isdigit():
            numbers.append(int(suffix))
    next_number = max(numbers, default=0) + 1
    return f"{prefix}{next_number:04d}"


def _version_id(page_id: str, sha_value: str) -> str:
    return f"PV-{page_id}-{sha_value[:12]}"


def _build_book_asset(book_root: Path, book_id: str, runtime: RuntimeConfig, asset_count: int) -> dict[str, Any]:
    existing = load_json_or_default(_book_metadata_paths(book_root, runtime)["book_asset"], {})
    created_at = existing.get("created_at") or now_iso()
    yaml_payload = _parse_simple_yaml(book_root / "book.yaml")
    return {
        "book_id": book_id,
        "book_title": yaml_payload.get("book_title") or book_root.name,
        "subject": yaml_payload.get("subject") or "",
        "source_root": str(book_root),
        "incoming_root": str(book_root / runtime.paper_book_incoming_dir),
        "metadata_root": str(book_root / runtime.paper_book_metadata_dir),
        "created_at": created_at,
        "updated_at": now_iso(),
        "status": "active",
        "asset_count": asset_count,
    }


def inspect_book_images(
    *,
    book_root: Path,
    runtime: RuntimeConfig,
    dry_run: bool,
    min_width: int | None = None,
    min_height: int | None = None,
    blur_threshold: float | None = None,
    phash_distance: int | None = None,
) -> dict[str, Any]:
    min_width = int(min_width if min_width is not None else runtime.paper_book_min_width)
    min_height = int(min_height if min_height is not None else runtime.paper_book_min_height)
    blur_threshold = float(blur_threshold if blur_threshold is not None else runtime.paper_book_blur_threshold)
    phash_distance = int(phash_distance if phash_distance is not None else runtime.paper_book_phash_distance)

    metadata_paths = _book_metadata_paths(book_root, runtime)
    existing_payload = load_json_or_default(metadata_paths["page_assets"], {})
    existing_items = existing_payload.get("items", []) if isinstance(existing_payload, dict) else []
    existing_by_sha = {
        item.get("source_image_sha256"): item
        for item in existing_items
        if isinstance(item, dict) and item.get("source_image_sha256")
    }
    existing_by_path = {
        str(item.get("source_image_path")): item
        for item in existing_items
        if isinstance(item, dict) and item.get("source_image_path")
    }

    book_id = _derive_book_id(book_root, metadata_paths["book_asset"])
    images = _discover_candidate_images(book_root, runtime)

    candidates: list[dict[str, Any]] = []
    for path in images:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            capture_time, capture_source = _read_capture_time(path, image)
            blur_score = _blur_score(image)
            perceptual_hash = _average_hash(image)
        sha_value = sha256_for_file(path)
        candidates.append(
            {
                "path": path,
                "sha256": sha_value,
                "capture_time": capture_time,
                "capture_source": capture_source,
                "width": width,
                "height": height,
                "blur_score": blur_score,
                "perceptual_hash": perceptual_hash,
                "sort_key": (capture_time, path.relative_to(book_root).as_posix().lower()),
            }
        )
    candidates.sort(key=lambda item: item["sort_key"])

    grouped_by_sha: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped_by_sha.setdefault(candidate["sha256"], []).append(candidate)

    next_scan_index = max((int(item.get("scan_index", 0) or 0) for item in existing_items), default=0) + 1
    items: list[dict[str, Any]] = []
    exact_duplicate_file_count = 0
    for sha_value, group in grouped_by_sha.items():
        canonical = group[0]
        existing = existing_by_sha.get(sha_value) or existing_by_path.get(str(canonical["path"])) or {}
        duplicate_paths = [str(item["path"]) for item in group[1:]]
        exact_duplicate_file_count += len(duplicate_paths)

        quality_reasons: list[str] = []
        if canonical["width"] < min_width or canonical["height"] < min_height:
            quality_reasons.append("low-resolution")
        if canonical["blur_score"] < blur_threshold:
            quality_reasons.append("blurry")
        quality_status = "accepted" if not quality_reasons else "needs_review"

        page_id = str(existing.get("page_id") or _next_page_id(book_id, existing_items + items))
        created_at = existing.get("asset_created_at") or now_iso()
        current_version_id = _version_id(page_id, sha_value)
        previous_version_ids = list(existing.get("previous_version_ids", []))
        existing_current_version = existing.get("current_version_id")
        if existing_current_version and existing_current_version != current_version_id and existing_current_version not in previous_version_ids:
            previous_version_ids.append(existing_current_version)
        asset = {
            "page_id": page_id,
            "book_id": book_id,
            "source_image_path": str(canonical["path"]),
            "source_image_sha256": sha_value,
            "scan_index": int(existing.get("scan_index") or next_scan_index),
            "capture_time": canonical["capture_time"],
            "capture_time_source": canonical["capture_source"],
            "quality_status": quality_status,
            "quality_reasons": quality_reasons,
            "duplicate_source_paths": duplicate_paths,
            "printed_page": existing.get("printed_page"),
            "chapter_id": existing.get("chapter_id"),
            "section_id": existing.get("section_id"),
            "classification_status": existing.get("classification_status") or "unassigned",
            "asset_created_at": created_at,
            "asset_updated_at": now_iso(),
            "current_version_id": current_version_id,
            "previous_version_ids": previous_version_ids,
            "resolution": {"width": canonical["width"], "height": canonical["height"]},
            "blur_score": canonical["blur_score"],
            "perceptual_hash": canonical["perceptual_hash"],
        }
        if not existing:
            next_scan_index += 1
        elif _asset_compare_payload(asset) == _asset_compare_payload(existing):
            asset["asset_updated_at"] = existing.get("asset_updated_at", asset["asset_updated_at"])
            asset["capture_time"] = existing.get("capture_time", asset["capture_time"])
        items.append(asset)

    items.sort(key=lambda item: (int(item["scan_index"]), item["page_id"]))
    for index, item in enumerate(items, start=1):
        if not item.get("scan_index"):
            item["scan_index"] = index

    for index, item in enumerate(items):
        suspected: list[dict[str, Any]] = []
        for other_index, other in enumerate(items):
            if index == other_index or item["source_image_sha256"] == other["source_image_sha256"]:
                continue
            distance = _hamming_distance(item["perceptual_hash"], other["perceptual_hash"])
            if distance <= phash_distance:
                suspected.append(
                    {
                        "page_id": other["page_id"],
                        "distance": distance,
                    }
                )
        item["suspected_duplicate_page_ids"] = sorted(suspected, key=lambda candidate: (candidate["distance"], candidate["page_id"]))

    page_assets_payload = {
        "book_id": book_id,
        "source_root": str(book_root),
        "generated_by": "kaoyan-material-organizer",
        "schema_hint": "page-assets-v1",
        "created_at": existing_payload.get("created_at") if isinstance(existing_payload, dict) and existing_payload.get("created_at") else now_iso(),
        "updated_at": now_iso(),
        "items": items,
        "summary": {
            "image_count": len(images),
            "asset_count": len(items),
            "exact_duplicate_file_count": exact_duplicate_file_count,
            "rejected_count": sum(1 for item in items if item["quality_status"] == "rejected"),
            "needs_review_count": sum(1 for item in items if item["quality_status"] == "needs_review"),
        },
    }
    book_asset_payload = _build_book_asset(book_root, book_id, runtime, len(items))

    writes = {
        "book_asset": False,
        "page_assets": False,
    }
    if not dry_run:
        metadata_paths["root"].mkdir(parents=True, exist_ok=True)
        writes["book_asset"] = save_json(metadata_paths["book_asset"], book_asset_payload)
        writes["page_assets"] = save_json(metadata_paths["page_assets"], page_assets_payload)

    payload = {
        "book_id": book_id,
        "dry_run": dry_run,
        "book_root": str(book_root),
        "incoming_root": str(book_root / runtime.paper_book_incoming_dir),
        "image_count": len(images),
        "asset_count": len(items),
        "items": items,
        "summary": page_assets_payload["summary"],
        "page_assets_path": str(metadata_paths["page_assets"]),
        "book_asset_path": str(metadata_paths["book_asset"]),
        "writes": writes,
        "network_requests": 0,
        "inspection_fingerprint": stable_fingerprint(
            {
                "book_id": book_id,
                "images": [
                    {
                        "page_id": item["page_id"],
                        "sha256": item["source_image_sha256"],
                        "scan_index": item["scan_index"],
                        "quality_status": item["quality_status"],
                    }
                    for item in items
                ],
            }
        ),
    }
    return payload
