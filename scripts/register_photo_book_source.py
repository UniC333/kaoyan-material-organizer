#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import register_source_material
from config import load_runtime_config
from paper_book_assets import _parse_simple_yaml


def register_photo_source(book_root: Path) -> dict:
    book_root = book_root.resolve()
    if not book_root.is_dir():
        raise SystemExit(f"book root not found: {book_root}")
    config = _parse_simple_yaml(book_root / "book.yaml")
    metadata = book_root / load_runtime_config().paper_book_metadata_dir / "page_assets.json"
    if not metadata.is_file():
        raise SystemExit("page_assets.json is missing; run book inspect first")
    page_assets = json.loads(metadata.read_text(encoding="utf-8"))
    include_paths = [Path(item["source_image_path"]) for item in page_assets.get("items", []) if Path(str(item.get("source_image_path") or "")).is_file()]
    source = register_source_material(
        subject=str(config["subject"]),
        source_name=str(config["book_title"]),
        material_type="chapter-photo",
        material_path=book_root,
        include_paths=include_paths,
    )
    return {"source_id": source["source_id"], "book_id": config["book_id"], "book_title": config["book_title"], "source_root": str(book_root), "file_count": source["file_count"], "image_count": source["image_count"], "status": source["status"]}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--book-root", required=True)
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    args = parser.parse_args()
    payload = register_photo_source(Path(args.book_root))
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
