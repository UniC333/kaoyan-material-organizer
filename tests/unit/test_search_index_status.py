from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_search_index import build_doc_from_payload
from query_local_knowledge import retrieval_hit_matches_chapter


def test_stale_evidence_is_excluded_from_search_index() -> None:
    assert build_doc_from_payload(
        "evidence",
        {
            "evidence_id": "EV-MATH-STALE",
            "verification_status": "stale",
            "content": "旧概括",
        },
    ) is None


def test_retrieval_candidate_is_filtered_by_requested_chapter(monkeypatch, tmp_path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "EV-MATH-3.json").write_text(
        '{"evidence_id":"EV-MATH-3","chapter_title":"第三章 一元函数积分"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "query_local_knowledge.kb_layout",
        lambda: {"evidence": evidence_dir, "claims": tmp_path / "claims"},
    )

    hit = {"doc_type": "evidence", "entity_id": "EV-MATH-3"}
    assert not retrieval_hit_matches_chapter(hit, "第二章 一元函数求导")
    assert retrieval_hit_matches_chapter(hit, "第三章 一元函数积分")
