from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish_book_ocr_evidence
from publish_book_ocr_evidence import collect_publication_items, publication_is_allowed, reviewed_candidate_text, select_evidence_id


def test_accepted_correction_replaces_raw_ocr_text() -> None:
    candidate = {"text": "raw OCR", "block_id": "OCRBLK-001-001"}
    overlay = {"review_status": "accepted", "corrected_text": r"$x^{2}$"}

    assert reviewed_candidate_text(candidate, overlay) == r"$x^{2}$"


def test_empty_or_unaccepted_correction_does_not_replace_raw_text() -> None:
    candidate = {"text": " raw OCR ", "block_id": "OCRBLK-001-001"}

    assert reviewed_candidate_text(candidate, {}) == "raw OCR"
    assert reviewed_candidate_text(
        candidate,
        {"review_status": "pending", "corrected_text": "unreviewed correction"},
    ) == "raw OCR"
    assert reviewed_candidate_text(
        candidate,
        {"review_status": "accepted", "corrected_text": "   "},
    ) == "raw OCR"


def test_require_complete_blocks_all_publication_when_any_page_is_blocked() -> None:
    blocked = [{"page_id": "PAGE-2", "reason": "sensitive-block-review-pending"}]

    assert not publication_is_allowed(require_complete=True, blocked=blocked)
    assert publication_is_allowed(require_complete=False, blocked=blocked)
    assert publication_is_allowed(require_complete=True, blocked=[])


def test_rerun_reuses_existing_evidence_id(monkeypatch) -> None:
    monkeypatch.setattr(publish_book_ocr_evidence, "allocate_kb_id", lambda *_: "EV-MATH-NEW")

    assert select_evidence_id(old={"evidence_id": "EV-MATH-OLD"}, subject="数学") == "EV-MATH-OLD"
    assert select_evidence_id(old={}, subject="数学") == "EV-MATH-NEW"


def test_chapter_scope_only_collects_requested_chapter(monkeypatch, tmp_path: Path) -> None:
    def fake_load(path: Path, default: object) -> dict:
        if str(path).endswith("normalized.json"):
            return {"chunk_candidates": [{"block_id": "TEXT-1", "block_type": "text", "text": "正文"}]}
        return {"items": []}

    monkeypatch.setattr(publish_book_ocr_evidence, "load_json_or_default", fake_load)
    monkeypatch.setattr(
        publish_book_ocr_evidence,
        "cache_paths_for_request",
        lambda *_: {"overlay": tmp_path / "overlay.json"},
    )
    status = {
        "items": [
            {"page_id": "PAGE-CH3", "status": "completed", "normalized_path": tmp_path / "normalized.json", "request_key": "ch3"},
            {"page_id": "PAGE-CH4", "status": "completed", "normalized_path": tmp_path / "normalized.json", "request_key": "ch4"},
        ]
    }
    assets = {"items": [{"page_id": "PAGE-CH3"}, {"page_id": "PAGE-CH4"}]}
    classes = {
        "PAGE-CH3": {"chapter_id": "CH-3", "classification_status": "confirmed"},
        "PAGE-CH4": {"chapter_id": "CH-4", "classification_status": "confirmed"},
    }
    locator = {"PAGE-CH3": {"printed_page": 42}, "PAGE-CH4": {"printed_page": 73}}

    items, blocked = collect_publication_items(
        runtime=SimpleNamespace(ocr_cache_root=tmp_path),
        root=tmp_path,
        assets=assets,
        status=status,
        classes=classes,
        locator_by_page=locator,
        chapter_ids={"CH-3"},
    )

    assert [item["page_id"] for item in items] == ["PAGE-CH3"]
    assert blocked == []
