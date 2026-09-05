from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import approve_pdf_page_mapping_interval as approval
import build_pdf_page_mapping_candidates as candidates


def test_footer_candidates_only_accept_a_single_numeric_footer() -> None:
    assert candidates._candidate_from_normalized({"chunk_candidates": [{"block_type": "footer", "text": "004"}]}) == (4, "exact-footer-token")
    assert candidates._candidate_from_normalized({"chunk_candidates": [{"block_type": "footer", "text": "4"}, {"block_type": "footer", "text": "5"}]}) == (None, "ambiguous-footer-token")
    assert candidates._candidate_from_normalized({"chunk_candidates": [{"block_type": "paragraph", "text": "004"}]}) == (None, "missing-footer-token")


def test_segments_break_on_missing_or_noncontinuous_candidates() -> None:
    items = [
        {"pdf_page": 7, "printed_page_candidate": 1},
        {"pdf_page": 8, "printed_page_candidate": 2},
        {"pdf_page": 9, "printed_page_candidate": None},
        {"pdf_page": 10, "printed_page_candidate": 4},
        {"pdf_page": 11, "printed_page_candidate": 8},
    ]
    assert candidates._continuous_segments(items) == [
        {"pdf_page_start": 7, "pdf_page_end": 8, "printed_page_start_candidate": 1, "printed_page_end_candidate": 2, "page_count": 2, "candidate_status": "continuous-footer-candidates-only"},
        {"pdf_page_start": 10, "pdf_page_end": 10, "printed_page_start_candidate": 4, "printed_page_end_candidate": 4, "page_count": 1, "candidate_status": "continuous-footer-candidates-only"},
        {"pdf_page_start": 11, "pdf_page_end": 11, "printed_page_start_candidate": 8, "printed_page_end_candidate": 8, "page_count": 1, "candidate_status": "continuous-footer-candidates-only"},
    ]


def test_interval_approval_requires_all_candidates_and_matching_source_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = {"sources": tmp_path / "sources", "indexes": tmp_path / "indexes", "review_queues": tmp_path / "review"}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    (layout["sources"] / "SRC.json").write_text('{"files":[{"sha256":"source-sha"}]}', encoding="utf-8")
    candidate_path = layout["indexes"] / "pdf_page_mapping_candidates" / "SRC.json"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text('{"source_file_sha256":"source-sha","items":[{"pdf_page":7,"printed_page_candidate":1},{"pdf_page":8,"printed_page_candidate":2}]}', encoding="utf-8")
    monkeypatch.setattr(approval, "kb_layout", lambda: layout)

    result = approval.approve_interval(pdf_source_id="SRC", pdf_start=7, pdf_end=8, printed_start=1, printed_end=2, note="visual endpoints")
    result = approval.approve_interval(pdf_source_id="SRC", pdf_start=7, pdf_end=8, printed_start=1, printed_end=2, note="visual endpoints", yes=True, expected_plan_fingerprint=result["plan_fingerprint"])

    assert result["approved_page_count"] == 2
    reviews = (layout["review_queues"] / "pdf-page-review" / "SRC.json").read_text(encoding="utf-8")
    assert '"mapping_basis": "continuous_endpoint_verified"' in reviews
    with pytest.raises(SystemExit, match="candidate mismatch"):
        approval.approve_interval(pdf_source_id="SRC", pdf_start=7, pdf_end=8, printed_start=2, printed_end=3, note="bad")
