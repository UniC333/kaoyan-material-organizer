#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import load_json_or_default, now_iso, save_json
from config import load_runtime_config


def _metadata_paths(book_root: Path, metadata_dirname: str) -> dict[str, Path]:
    metadata_root = book_root / metadata_dirname
    return {
        "root": metadata_root,
        "page_assets": metadata_root / "page_assets.json",
        "page_mappings": metadata_root / "page_mappings.json",
        "overrides": metadata_root / "page_mapping_overrides.json",
    }


def _mapping_compare_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"updated_at"}}


def _resolve_default_mapping(
    *,
    scan_index: int,
    start_scan_index: int,
    start_printed_page: int,
    unmapped_scan_indexes: set[int],
) -> dict[str, Any]:
    if scan_index in unmapped_scan_indexes or scan_index < start_scan_index:
        return {
            "printed_page": None,
            "printed_page_label": None,
            "mapping_status": "unmapped",
            "mapping_method": "front_matter_offset" if scan_index < start_scan_index else "manual_unmapped",
            "manual_override": False,
        }
    return {
        "printed_page": start_printed_page + (scan_index - start_scan_index),
        "printed_page_label": None,
        "mapping_status": "mapped",
        "mapping_method": "front_matter_offset",
        "manual_override": False,
    }


def map_book_pages(*, book_root: Path, dry_run: bool, format_name: str = "json") -> dict[str, Any]:
    runtime = load_runtime_config()
    paths = _metadata_paths(book_root, runtime.paper_book_metadata_dir)
    page_assets_payload = load_json_or_default(paths["page_assets"], {})
    if not page_assets_payload:
        raise SystemExit("page_assets.json is missing; run book inspect first")

    items = list(page_assets_payload.get("items", []))
    items.sort(key=lambda item: (int(item.get("scan_index", 0) or 0), str(item.get("page_id", ""))))

    overrides_payload = load_json_or_default(paths["overrides"], {})
    start_scan_index = int(overrides_payload.get("printed_page_start_scan_index", 1) or 1)
    start_printed_page = int(overrides_payload.get("printed_page_start", 1) or 1)
    unmapped_scan_indexes = {int(value) for value in overrides_payload.get("unmapped_scan_indexes", [])}
    manual_overrides = overrides_payload.get("overrides", {}) if isinstance(overrides_payload.get("overrides", {}), dict) else {}

    existing_mapping_payload = load_json_or_default(paths["page_mappings"], {})
    existing_mapping_items = {
        str(item.get("page_id")): item
        for item in existing_mapping_payload.get("items", [])
        if isinstance(item, dict) and item.get("page_id")
    }

    mapped_items: list[dict[str, Any]] = []
    updated_page_assets: list[dict[str, Any]] = []
    for asset in items:
        scan_index = int(asset.get("scan_index", 0) or 0)
        resolved = _resolve_default_mapping(
            scan_index=scan_index,
            start_scan_index=start_scan_index,
            start_printed_page=start_printed_page,
            unmapped_scan_indexes=unmapped_scan_indexes,
        )
        override = manual_overrides.get(str(scan_index), {})
        if override:
            resolved.update(
                {
                    "printed_page": override.get("printed_page", resolved["printed_page"]),
                    "printed_page_label": override.get("printed_page_label", resolved["printed_page_label"]),
                    "mapping_status": override.get("mapping_status", resolved["mapping_status"]),
                    "mapping_method": "manual_override",
                    "manual_override": bool(override.get("manual_override", True)),
                }
            )

        existing_asset = asset.copy()
        updated_asset = asset.copy()
        updated_asset["printed_page"] = resolved["printed_page"]
        updated_asset["printed_page_label"] = resolved["printed_page_label"]
        updated_asset["mapping_status"] = resolved["mapping_status"]
        updated_asset["mapping_method"] = resolved["mapping_method"]
        updated_asset["manual_override"] = resolved["manual_override"]
        if _mapping_compare_payload(updated_asset) == _mapping_compare_payload(existing_asset):
            updated_asset["asset_updated_at"] = existing_asset.get("asset_updated_at", updated_asset.get("asset_updated_at"))
        else:
            updated_asset["asset_updated_at"] = now_iso()
        updated_page_assets.append(updated_asset)

        existing_mapping = existing_mapping_items.get(str(asset.get("page_id")), {})
        mapping_item = {
            "page_mapping_id": existing_mapping.get("page_mapping_id") or f"MAP-{asset['book_id']}-{scan_index:04d}",
            "page_id": asset["page_id"],
            "book_id": asset["book_id"],
            "scan_index": scan_index,
            "printed_page": resolved["printed_page"],
            "printed_page_label": resolved["printed_page_label"],
            "mapping_status": resolved["mapping_status"],
            "mapping_method": resolved["mapping_method"],
            "manual_override": resolved["manual_override"],
            "current_version_id": asset.get("current_version_id"),
            "previous_version_ids": asset.get("previous_version_ids", []),
            "updated_at": now_iso(),
        }
        if _mapping_compare_payload(mapping_item) == _mapping_compare_payload(existing_mapping):
            mapping_item["updated_at"] = existing_mapping.get("updated_at", mapping_item["updated_at"])
        mapped_items.append(mapping_item)

    page_mapping_payload = {
        "book_id": page_assets_payload.get("book_id"),
        "source_root": page_assets_payload.get("source_root"),
        "created_at": existing_mapping_payload.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "items": mapped_items,
        "summary": {
            "mapped_count": sum(1 for item in mapped_items if item["mapping_status"] == "mapped"),
            "unmapped_count": sum(1 for item in mapped_items if item["mapping_status"] == "unmapped"),
            "needs_review_count": sum(1 for item in mapped_items if item["mapping_status"] == "needs_review"),
        },
    }

    writes = {"page_assets": False, "page_mappings": False}
    if not dry_run:
        paths["root"].mkdir(parents=True, exist_ok=True)
        updated_page_assets_payload = dict(page_assets_payload)
        updated_page_assets_payload["items"] = updated_page_assets
        updated_page_assets_payload["updated_at"] = now_iso()
        writes["page_assets"] = save_json(paths["page_assets"], updated_page_assets_payload)
        writes["page_mappings"] = save_json(paths["page_mappings"], page_mapping_payload)

    return {
        "book_id": page_assets_payload.get("book_id"),
        "book_root": str(book_root),
        "dry_run": dry_run,
        "items": mapped_items,
        "summary": page_mapping_payload["summary"],
        "page_mappings_path": str(paths["page_mappings"]),
        "writes": writes,
        "network_requests": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    payload = map_book_pages(book_root=Path(args.book_root), dry_run=args.dry_run, format_name=args.format)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
