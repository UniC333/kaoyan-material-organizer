from __future__ import annotations

from pathlib import Path
from typing import Any
from common import load_all_json


def resolve_pdf_source_id(subject: str, book_title: str, layout: dict[str, Path], explicit: str) -> str:
    if explicit:
        return explicit
    candidates = []
    for payload in load_all_json(layout["sources"]):
        if payload.get("subject") != subject:
            continue
        if payload.get("material_type") != "book-pdf":
            continue
        if payload.get("source_name") != book_title:
            continue
        candidates.append(payload)
    if not candidates:
        raise SystemExit(f"[ERROR] no registered book-pdf source found for {subject} / {book_title}")
    candidates.sort(key=lambda item: str(item.get("updated_at") or ""))
    return str(candidates[-1]["source_id"])
