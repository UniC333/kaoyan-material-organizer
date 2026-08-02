from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import page_locator
from query_local_knowledge import parse_page_anchor
import sync_exam_kb


def _entry(book_id: str, book_title: str, page: int, image: str = "page.jpg") -> dict:
    return {
        "subject": "数学",
        "book_id": book_id,
        "book_title": book_title,
        "normalized_book_title": page_locator.normalize_book_title(book_title),
        "source_id": f"SRC-{book_id}",
        "page_id": f"PAGE-{book_id}-{page}",
        "printed_page": page,
        "source_image_path": image,
        "source_image_sha256": f"sha-{book_id}-{page}",
    }


def test_parse_page_anchor_accepts_common_page_and_exercise_forms() -> None:
    assert parse_page_anchor("P49 例2.29") == {
        "requested_page": 49,
        "requested_position": None,
        "requested_exercise_label": "例2.29",
    }
    assert parse_page_anchor("p.49 最下方") ["requested_page"] == 49
    assert parse_page_anchor("第49页") ["requested_page"] == 49
    assert parse_page_anchor("49页") ["requested_page"] == 49


def test_resolver_uses_book_title_and_reports_ambiguity(monkeypatch) -> None:
    entries = [_entry("a", "李正元数一", 49), _entry("b", "另一教材", 49)]
    monkeypatch.setattr(page_locator, "load_page_locator_index", lambda: {"entries": entries, "sources": []})
    ambiguous = page_locator.resolve_page_locator(subject="数学", book_title=None, printed_page=49)
    assert ambiguous["match_status"] == "ambiguous"
    exact = page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=49)
    assert exact["match_status"] == "exact_asset"
    assert exact["book_id"] == "a"


def test_resolver_distinguishes_unmapped_from_unknown(monkeypatch) -> None:
    sources = [
        {
            "subject": "数学",
            "book_id": "a",
            "book_title": "李正元数一",
            "source_id": "SRC-a",
            "mapping_status": "unmapped",
        }
    ]
    monkeypatch.setattr(page_locator, "load_page_locator_index", lambda: {"entries": [], "sources": sources})
    assert page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=49)["match_status"] == "unmapped"
    assert page_locator.resolve_page_locator(subject="数学", book_title="不存在", printed_page=49)["match_status"] == "not_found"


def test_evidence_requires_same_printed_page_and_source_hash() -> None:
    locator = {"requested_page": 49, "source_image_sha256": "right-sha"}
    wrong_page = {"page_classification_refs": [{"printed_page": 21, "source_file_sha256": "right-sha"}]}
    wrong_source = {"page_classification_refs": [{"printed_page": 49, "source_file_sha256": "wrong-sha"}]}
    exact = {"page_classification_refs": [{"printed_page": 49, "source_file_sha256": "right-sha"}]}
    assert not page_locator.evidence_matches_locator(wrong_page, locator)
    assert not page_locator.evidence_matches_locator(wrong_source, locator)
    assert page_locator.evidence_matches_locator(exact, locator)


def test_smoke_and_test_paths_are_not_formal_sources() -> None:
    assert not page_locator._is_formal_source_path(Path("C:/.local-api-smoke/math"))
    assert not page_locator._is_formal_source_path(Path("C:/repo/tests/fixtures/math"))
    assert page_locator._is_formal_source_path(Path("E:/考研笔记/考研/教材图片/李正元数一"))
    assert not page_locator._is_formal_evidence_ref({"book_id": "math-ch2-demo"})
    assert not page_locator._is_formal_evidence_ref({"chapter_view_path": ".local-api-smoke/math/view.md"})
    assert page_locator._is_formal_evidence_ref({"book_id": "li-zhengyuan-math1-ch2"})


def test_sync_rebuilds_page_locator_index(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(sync_exam_kb, "run_script", lambda name, *args: calls.append((name, args)))
    monkeypatch.setattr(sys, "argv", ["sync_exam_kb.py", "--format", "quiet"])
    assert sync_exam_kb.main() == 0
    assert ("build_page_locator_index.py", ("--format", "quiet")) in calls
