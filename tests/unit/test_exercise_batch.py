from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import exercise_batch, exercise_locator
import query_local_knowledge as query_module


def _published_pdf_evidence(evidence_id: str, pdf_page: int, printed_page: int) -> dict:
    span = {
        "source_id": "SRC",
        "file_id": "FILE-PDF",
        "source_file_sha256": "pdf-sha",
        "locator": {"page_start": pdf_page, "page_end": pdf_page, "image_start": pdf_page, "image_end": pdf_page},
    }
    return {
        "evidence_id": evidence_id,
        "evidence_key": f"{evidence_id}-key",
        "source_id": "SRC",
        "chapter_id": "CH-PDF",
        "chunk_id": f"CHUNK-{evidence_id}",
        "origin_type": "pdf_page_ocr",
        "verification_status": "reviewed",
        "review_status": "accepted",
        "source_grounded": True,
        "mapping_status": "mapped",
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "source_spans": [span],
        "provenance": {"origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True, "source_spans": [span]},
    }


def test_parse_original_wrong_answer_batch_preserves_order_options_and_focus() -> None:
    parsed = exercise_batch.parse_exercise_batch_request(
        "刚刚做完了数据结构133～135页的选择，选错的选项有这些：1B,8D,10A,16A,18A,19C,25A,28B，另外，12和28两个题没理解透题意，着重多一点细节"
    )

    assert parsed["is_batch"] is True
    assert parsed["page_range"] == {"start": 133, "end": 135, "semantics": "question_scope"}
    assert parsed["exercise_category"] == "single-choice"
    assert [item["exercise_label"] for item in parsed["items"]] == ["01", "08", "10", "16", "18", "19", "25", "28", "12"]
    assert {item["exercise_label"]: item["requested_option"] for item in parsed["items"] if item["requested_option"]} == {
        "01": "B", "08": "D", "10": "A", "16": "A", "18": "A", "19": "C", "25": "A", "28": "B"
    }
    assert {item["exercise_label"] for item in parsed["items"] if item["emphasis"]} == {"12", "28"}


def test_parse_section_batch_does_not_treat_section_number_as_exercise() -> None:
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8、28题")

    assert parsed["is_batch"] is True
    assert parsed["page_range"] == {}
    assert [item["exercise_label"] for item in parsed["items"]] == ["01", "08", "28"]


def test_parse_invalid_option_is_retained_as_a_blocked_item() -> None:
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1E、8D题")

    assert [item["exercise_label"] for item in parsed["items"]] == ["08", "01"]
    invalid = next(item for item in parsed["items"] if item["exercise_label"] == "01")
    assert invalid == {"exercise_label": "01", "requested_option": "E", "emphasis": False, "parse_status": "invalid_option"}


def test_page_range_requires_contiguous_formal_pdf_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        exercise_batch,
        "load_page_locator_index",
        lambda: {
            "_availability": {"available": True},
            "entries": [
                {"source_asset_kind": "pdf", "subject": "408", "book_title": "王道数据结构", "source_id": "SRC", "printed_page": 133, "pdf_page": 145},
                {"source_asset_kind": "pdf", "subject": "408", "book_title": "王道数据结构", "source_id": "SRC", "printed_page": 134, "pdf_page": 147},
            ],
        },
    )

    resolved = exercise_batch._resolve_range_source(
        subject="408",
        book_title="王道数据结构",
        page_range={"start": 133, "end": 134},
        allowed_source_ids={"SRC"},
    )

    assert resolved["status"] == "unavailable"
    assert resolved["reason"] == "printed-page-range-not-contiguous"


def test_stale_relation_index_blocks_each_item(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC": {"source_id": "SRC"}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "exact", "section_root": "5.2"})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {"_availability": {"available": False, "reason": "exercise_locator_index_stale"}},
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="5.2 单选第1、8题", parsed=parsed
    )

    assert [item["reason"] for item in resolved["targets"]] == ["exercise_locator_index_stale"] * 2


def test_duplicate_label_across_sections_is_ambiguous_without_section(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC": {"source_id": "SRC"}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "not_found", "section_root": ""})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {
            "_availability": {"available": True},
            "relations": [
                {"source_id": "SRC", "section_root": section, "category": "single-choice", "exercise_label": label, "relation_status": "exact", "question_printed_pages": [page]}
                for section, label, page in [("5.2", "01", 133), ("5.3", "01", 139), ("5.2", "08", 133)]
            ],
        },
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="王道数据结构单选第1、8题", parsed=parsed
    )

    assert resolved["targets"][0]["reason"] == "exercise-relation-ambiguous"
    assert resolved["targets"][1]["status"] == "exact"


def test_same_title_source_conflict_blocks_cross_source_batch(monkeypatch) -> None:
    monkeypatch.setattr(exercise_batch, "_active_pdf_sources", lambda _: {"SRC-A": {}, "SRC-B": {}})
    monkeypatch.setattr(exercise_batch, "resolve_section_anchor", lambda **_: {"status": "exact", "section_root": "5.2"})
    monkeypatch.setattr(
        exercise_batch,
        "load_exercise_locator_index",
        lambda: {
            "_availability": {"available": True},
            "relations": [
                {"source_id": "SRC-A", "section_root": "5.2", "category": "single-choice", "exercise_label": "01", "relation_status": "exact", "question_printed_pages": [133]},
                {"source_id": "SRC-B", "section_root": "5.2", "category": "single-choice", "exercise_label": "08", "relation_status": "exact", "question_printed_pages": [133]},
            ],
        },
    )
    parsed = exercise_batch.parse_exercise_batch_request("王道数据结构 5.2 单选第1、8题")

    resolved = exercise_batch.resolve_exercise_batch_targets(
        subject="408", book_title="王道数据结构", chapter=None, query="王道数据结构 5.2 单选第1、8题", parsed=parsed
    )

    assert [item["reason"] for item in resolved["targets"]] == ["book-source-ambiguous"] * 2


def test_relation_enrichment_requires_independent_printed_pages() -> None:
    relation = {
        "relation_id": "EXR-SRC-5.2-single-choice-01",
        "relation_status": "exact",
        "source_id": "SRC",
        "question_evidence_ids": ["EV-Q"],
        "question_pdf_pages": [145],
        "answer_evidence_ids": ["EV-A"],
        "answer_pdf_pages": [147],
    }
    evidences = {
        "EV-Q": _published_pdf_evidence("EV-Q", 145, 133),
        "EV-A": _published_pdf_evidence("EV-A", 147, 135),
    }

    enriched = exercise_locator._with_relation_printed_pages(relation, evidences)
    assert enriched["relation_status"] == "exact"
    assert enriched["question_printed_pages"] == [133]
    assert enriched["answer_printed_pages"] == [135]

    del evidences["EV-A"]["printed_page"]
    blocked = exercise_locator._with_relation_printed_pages(relation, evidences)
    assert blocked["relation_status"] == "needs_review"
    assert blocked["mapping_failure_reason"] == "formal-question-or-answer-page-mapping-missing"


def test_shared_relation_assembler_returns_both_page_systems(monkeypatch, tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    (evidence_root / "EV-Q.json").write_text(json.dumps({"evidence_id": "EV-Q", "content": "# 一、单项选择题\n01. 题干\n02. 下一题"}), encoding="utf-8")
    (evidence_root / "EV-A.json").write_text(json.dumps({"evidence_id": "EV-A", "content": "# 一、单项选择题\n01. C\n解析\n02. D"}), encoding="utf-8")
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: {"evidence": evidence_root})
    relation = {
        "relation_id": "EXR-SRC-5.2-single-choice-01",
        "relation_status": "exact",
        "source_id": "SRC",
        "section_root": "5.2",
        "category": "single-choice",
        "exercise_label": "01",
        "question_evidence_ids": ["EV-Q"],
        "question_pdf_pages": [145],
        "question_printed_pages": [133],
        "answer_evidence_ids": ["EV-A"],
        "answer_pdf_pages": [147],
        "answer_printed_pages": [135],
    }

    anchor, evidences = exercise_locator.assemble_exact_relation(relation)

    assert anchor["status"] == "exact_answer_evidence"
    assert anchor["question_printed_pages"] == [133]
    assert anchor["question_pdf_pages"] == [145]
    assert anchor["answer_printed_pages"] == [135]
    assert anchor["answer_pdf_pages"] == [147]
    assert anchor["question_content"] == "01. 题干"
    assert anchor["answer_content"] == "01. C\n解析"
    assert {item["evidence_id"] for item in evidences} == {"EV-Q", "EV-A"}


def _exact_query_result(label: str) -> dict:
    grounding = {
        "required": True,
        "status": "exact_answer",
        "can_conclude": True,
        "problem": {"evidence_ids": [f"EV-Q-{label}"], "printed_pages": [133], "pdf_pages": [145], "content": "题干"},
        "solution": {"evidence_ids": [f"EV-A-{label}"], "printed_pages": [135], "pdf_pages": [147], "content": "答案"},
        "failure_reason": "",
        "next_action": "",
    }
    return {
        "book_title": "王道数据结构",
        "answer_grounding": grounding,
        "teaching_bundle": {
            "status": "exact", "problem_text": "题干", "source_answer_text": "答案", "requested_option": "", "exercise_label": label,
            "citations": {"problem_evidence_ids": [f"EV-Q-{label}"], "solution_evidence_ids": [f"EV-A-{label}"], "problem_pdf_pages": [145], "solution_pdf_pages": [147]},
            "failure_reason": "",
        },
        "references": [{"evidence_id": f"EV-Q-{label}"}, {"evidence_id": f"EV-A-{label}"}],
        "textbook_location": {"status": "exact", "exercise_label": label, "printed_pages": [133]},
        "page_verification": {},
    }


def test_batch_query_keeps_exact_items_when_one_relation_is_blocked(monkeypatch, tmp_path: Path) -> None:
    relation = {"relation_id": "EXR-01", "question_printed_pages": [160]}
    monkeypatch.setattr(
        query_module,
        "resolve_exercise_batch_targets",
        lambda **_: {
            "section_root": "5.3", "source_id": "SRC", "targets": [
                {"exercise_label": "01", "requested_option": "", "emphasis": False, "status": "exact", "printed_page": 160, "relation": relation},
                {"exercise_label": "11", "requested_option": "", "emphasis": False, "status": "blocked", "reason": "exercise-relation-needs-review", "relation": {}},
            ],
        },
    )
    monkeypatch.setattr(query_module, "query_knowledge", lambda *args, **kwargs: _exact_query_result("01"))
    monkeypatch.setattr(query_module, "runtime_context_payload", lambda **_: {})

    payload = query_module.query_exercise_batch(
        tmp_path,
        "408",
        "5.3",
        "王道数据结构 5.3 综合题第1、11题",
        3,
        "王道数据结构",
        view="teaching",
    )

    assert payload is not None
    assert payload["batch_contract_version"] == "m6.exercise-batch.v1"
    assert payload["batch_status"] == "partial"
    assert payload["summary"] == {"requested_count": 2, "exact_count": 1, "blocked_count": 1}
    assert payload["items"][0]["teaching_bundle"]["status"] == "exact"
    assert payload["items"][0]["textbook_location"]["question_printed_pages"] == [160]
    assert payload["items"][1]["teaching_bundle"]["status"] == "blocked"
    assert payload["items"][1]["textbook_location"]["question_pdf_pages"] == []
    assert payload["items"][1]["teaching_bundle"]["problem_text"] == ""
    assert payload["items"][1]["teaching_bundle"]["source_answer_text"] == ""
