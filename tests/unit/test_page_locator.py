from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import page_locator
from kaoyan_kb.domain import exercise_locator
from query_local_knowledge import parse_page_anchor
from query_local_knowledge import apply_exercise_relation, apply_hard_page_route, build_reference_items, exact_evidence_hits_for_locator
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


def test_reviewed_ocr_source_span_matches_exact_page() -> None:
    locator = {"requested_page": 49, "source_image_sha256": "right-sha"}
    evidence = {
        "source_spans": [
            {
                "source_file_sha256": "right-sha",
                "locator": {"page_start": "第49页", "page_end": "第49页"},
            }
        ]
    }

    assert page_locator.evidence_matches_locator(evidence, locator)


def test_pdf_printed_page_must_be_explicit_and_pdf_mapping_is_continuous(tmp_path: Path) -> None:
    layout = {"evidence": tmp_path / "evidence"}
    layout["evidence"].mkdir()
    source = {"source_id": "SRC-PDF", "subject": "408", "source_name": "PDF", "source_path": "book.pdf"}
    for pdf_page, printed_page in ((13, 1), (14, 2)):
        (layout["evidence"] / f"EV-{pdf_page}.json").write_text(
            __import__("json").dumps({
                "evidence_id": f"EV-{pdf_page}", "source_id": "SRC-PDF", "subject": "408", "book_title": "PDF",
                "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True,
                "locator": {"page_start": pdf_page}, "content": f"{printed_page}\ntext",
                "source_spans": [{"source_file_sha256": "pdf-sha"}],
            }), encoding="utf-8"
        )
    reviews = {page: {"printed_page": printed, "page_header_verified": True, "source_file_sha256": "pdf-sha"} for page, printed in ((13, 1), (14, 2))}
    records, review = page_locator._pdf_source_records(source, layout, reviews)
    assert not review
    assert [(item["printed_page"], item["pdf_page"]) for item in records] == [(1, 13), (2, 14)]
    assert page_locator.extract_printed_page_from_ocr("chapter\nnot-a-page") == 0


def test_pdf_printed_page_discontinuity_is_not_published(tmp_path: Path) -> None:
    layout = {"evidence": tmp_path / "evidence"}
    layout["evidence"].mkdir()
    source = {"source_id": "SRC-PDF", "subject": "408", "source_name": "PDF", "source_path": "book.pdf"}
    for pdf_page, printed_page in ((13, 1), (14, 9)):
        (layout["evidence"] / f"EV-{pdf_page}.json").write_text(
            __import__("json").dumps({
                "evidence_id": f"EV-{pdf_page}", "source_id": "SRC-PDF", "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True,
                "locator": {"page_start": pdf_page}, "content": str(printed_page), "source_spans": [{"source_file_sha256": "pdf-sha"}],
            }), encoding="utf-8"
        )
    reviews = {page: {"printed_page": printed, "page_header_verified": True, "source_file_sha256": "pdf-sha"} for page, printed in ((13, 1), (14, 9))}
    records, review = page_locator._pdf_source_records(source, layout, reviews)
    assert records == []
    assert any(item["kind"] == "printed-page-discontinuity" for item in review)


def test_pdf_page_without_header_verification_is_not_mapped(tmp_path: Path) -> None:
    layout = {"evidence": tmp_path / "evidence"}
    layout["evidence"].mkdir()
    (layout["evidence"] / "EV-13.json").write_text(__import__("json").dumps({
        "evidence_id": "EV-13", "source_id": "SRC-PDF", "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True,
        "locator": {"page_start": 13}, "source_spans": [{"source_file_sha256": "pdf-sha"}],
    }), encoding="utf-8")
    records, review = page_locator._pdf_source_records({"source_id": "SRC-PDF"}, layout, {13: {"printed_page": 1}})
    assert records == []
    assert review[0]["kind"] == "page-header-unverified"


def test_sanitized_p67_q01_route_fixture_contract() -> None:
    fixture = json.loads((SCRIPTS.parent / "tests" / "fixtures" / "pdf_page_route_p67_q01.json").read_text(encoding="utf-8"))
    relation = fixture["relations"][0]
    assert fixture["request"] == {"chapter": "第3.1节", "printed_page": 67, "exercise_label": "1"}
    assert relation["question_pdf_pages"] == [fixture["expected"]["question_pdf_page"]]
    assert relation["answer_pdf_pages"] == [fixture["expected"]["answer_pdf_page"]]
    assert fixture["expected"]["answer_mode"] == "accepted_evidence"


def test_exercise_locator_links_question_to_multpage_answer(monkeypatch, tmp_path: Path) -> None:
    layout = {"manifests": tmp_path / "manifests", "evidence": tmp_path / "evidence", "indexes": tmp_path / "indexes", "review_queues": tmp_path / "review-queues"}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    (layout["manifests"] / "sources").mkdir()
    (layout["manifests"] / "sources" / "SRC-PDF.json").write_text(__import__("json").dumps({"source_id": "SRC-PDF", "status": "active", "material_type": "book-pdf"}), encoding="utf-8")
    fixtures = {
        13: "1\n# 2.2.3 本节试题精选\n# 二、综合应用题\n01. 题目",
        14: "2\n# 2.2.4 答案与解析\n# 二、综合应用题",
        15: "3\n# 01.【解答】\n答案开始",
        16: "4\n答案续页",
    }
    for page, content in fixtures.items():
        (layout["evidence"] / f"EV-{page}.json").write_text(__import__("json").dumps({"evidence_id": f"EV-{page}", "source_id": "SRC-PDF", "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True, "locator": {"page_start": page}, "content": content}), encoding="utf-8")
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: layout)
    payload = exercise_locator.build_exercise_locator_index()
    assert payload["summary"]["relation_count"] == 1
    relation = payload["relations"][0]
    assert relation["question_pdf_pages"] == [13]
    assert relation["answer_pdf_pages"] == [15, 16]


def test_exercise_locator_does_not_treat_summary_number_as_answer(monkeypatch, tmp_path: Path) -> None:
    layout = {"manifests": tmp_path / "manifests", "evidence": tmp_path / "evidence", "indexes": tmp_path / "indexes", "review_queues": tmp_path / "review-queues"}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    (layout["manifests"] / "sources").mkdir()
    (layout["manifests"] / "sources" / "SRC-PDF.json").write_text(__import__("json").dumps({"source_id": "SRC-PDF", "status": "active", "material_type": "book-pdf"}), encoding="utf-8")
    fixtures = {
        13: "# 1.2.3 本节试题精选\n# 二、综合应用题\n01. 题目",
        14: "# 1.2.4 答案与解析\n# 二、综合应用题\n# 01.【解答】\n答案开始\n# 归纳总结\n# 2. 循环主体中的变量与循环条件无关",
    }
    for page, content in fixtures.items():
        (layout["evidence"] / f"EV-{page}.json").write_text(__import__("json").dumps({"evidence_id": f"EV-{page}", "source_id": "SRC-PDF", "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True, "locator": {"page_start": page}, "content": content}), encoding="utf-8")
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: layout)
    payload = exercise_locator.build_exercise_locator_index()
    assert payload["summary"]["relation_count"] == 1
    assert payload["summary"]["review_count"] == 0


def test_scoped_exercise_relation_uses_section_before_chapter(monkeypatch, tmp_path: Path) -> None:
    layout = {"manifests": tmp_path / "manifests"}
    (layout["manifests"] / "sources").mkdir(parents=True)
    (layout["manifests"] / "sources" / "SRC-PDF.json").write_text(
        __import__("json").dumps({"source_id": "SRC-PDF", "source_name": "王道数据结构", "status": "active", "material_type": "book-pdf"}),
        encoding="utf-8",
    )
    relations = {"relations": [
        {"relation_status": "exact", "source_id": "SRC-PDF", "section_root": "3.1", "exercise_label": "17"},
        {"relation_status": "exact", "source_id": "SRC-PDF", "section_root": "3.2", "exercise_label": "17"},
    ]}
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: layout)
    monkeypatch.setattr(exercise_locator, "load_exercise_locator_index", lambda: relations)

    assert exercise_locator.find_unique_relation_for_scope(book_title="王道数据结构", chapter="第3.1节", exercise_label="17")["section_root"] == "3.1"
    assert exercise_locator.find_unique_relation_for_scope(book_title="王道数据结构", chapter="第3章", exercise_label="17") == {}


def test_exact_page_route_sets_matched_evidence_for_reviewed_ocr(monkeypatch) -> None:
    locator = {
        "match_status": "exact_asset",
        "requested_page": 49,
        "source_image_sha256": "right-sha",
        "evidence_ids": ["EV-MATH-49"],
        "requested_exercise_label": "",
    }
    evidence = {"evidence_id": "EV-MATH-49", "chunk_id": "PAGE-49", "content": "page text"}
    monkeypatch.setattr("query_local_knowledge.resolve_page_locator", lambda **_: dict(locator))
    monkeypatch.setattr("query_local_knowledge.exact_evidence_hits_for_locator", lambda *args: [evidence])

    result, _, _, hits = apply_hard_page_route(
        subject="数学",
        chapter=None,
        book_title="李正元数一",
        request={"requested_page": 49, "requested_exercise_label": "", "requested_position": None},
        retrieval_hits=[],
        claims=[],
    )

    assert result["match_status"] == "exact_evidence"
    assert result["matched_evidence_id"] == "EV-MATH-49"
    assert result["matched_chunk_id"] == "PAGE-49"
    assert hits == [evidence]


def test_exact_page_evidence_is_not_rejected_by_subsection_name(monkeypatch, tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "EV-PDF-67.json").write_text(
        __import__("json").dumps({
            "evidence_id": "EV-PDF-67", "subject": "408", "verification_status": "reviewed",
            "locator": {"page_start": 79}, "chapter_title": "第3章 栈、队列和数组",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("query_local_knowledge.ensure_kb_layout", lambda: {"evidence": evidence_dir})
    locator = {"evidence_ids": ["EV-PDF-67"], "pdf_page": 79}

    matches = exact_evidence_hits_for_locator("408", "第3.1节", locator)

    assert [item["evidence_id"] for item in matches] == ["EV-PDF-67"]


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
    assert ("build_search_index.py", ("--format", "quiet")) in calls


def test_indexes_only_runs_no_full_sync_steps(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(sync_exam_kb, "run_script", lambda name, *args: calls.append((name, args)))
    monkeypatch.setattr(sys, "argv", ["sync_exam_kb.py", "--indexes-only", "--format", "quiet"])

    assert sync_exam_kb.main() == 0
    assert calls == [
        ("build_page_locator_index.py", ("--format", "quiet")),
        ("build_exercise_locator_index.py", ("--format", "quiet")),
        ("build_search_index.py", ("--format", "quiet")),
    ]


def test_image_page_reference_does_not_parse_printed_page_as_pdf_page() -> None:
    evidence = {
        "evidence_id": "EV-IMAGE-001",
        "origin_type": "reviewed_ocr",
        "locator": {"page_start": "第1页", "page_end": "第1页"},
        "page_classification_refs": [{"printed_page": 1}],
    }

    reference = build_reference_items([evidence])[0]

    assert reference["printed_page"] == 1
    assert reference["pdf_page"] == 0


def test_example_label_uses_same_page_evidence_without_number_collapse() -> None:
    locator = {
        "match_status": "exact_evidence",
        "requested_exercise_label": "例3.14",
        "exercise_match_status": "matched",
        "matched_evidence_id": "EV-MATH-000104",
    }

    anchor, evidences = apply_exercise_relation(locator, [{"evidence_id": "EV-MATH-000104"}])

    assert anchor["status"] == "same_page_evidence"
    assert anchor["exercise_label"] == "例3.14"
    assert anchor["question_evidence_ids"] == ["EV-MATH-000104"]
    assert evidences == [{"evidence_id": "EV-MATH-000104"}]
