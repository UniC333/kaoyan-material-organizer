from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kaoyan_kb.domain import page_locator
from kaoyan_kb.domain import exercise_locator
from kaoyan_kb.domain.index_freshness import fingerprint_index_inputs
from query_local_knowledge import build_page_crosscheck, parse_page_anchor, resolve_request
from query_local_knowledge import apply_exercise_relation, apply_hard_page_route, build_reference_items, exact_evidence_hits_for_locator
import sync_exam_kb
import create_snapshot


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _freshness_layout(tmp_path: Path) -> dict[str, Path]:
    layout = {
        "manifests": tmp_path / "manifests",
        "evidence": tmp_path / "evidence",
        "review_queues": tmp_path / "review-queues",
        "indexes": tmp_path / "indexes",
    }
    for root in layout.values():
        root.mkdir(parents=True, exist_ok=True)
    return layout


def test_content_fingerprint_detects_same_mtime_rewrite_and_deletion(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text('{"value":"a"}', encoding="utf-8")
    original_stat = source.stat()
    before = fingerprint_index_inputs(files=[("input", source)])

    source.write_text('{"value":"b"}', encoding="utf-8")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    rewritten = fingerprint_index_inputs(files=[("input", source)])
    source.unlink()
    deleted = fingerprint_index_inputs(files=[("input", source)])

    assert rewritten != before
    assert deleted != rewritten


def test_page_fingerprint_covers_external_photo_metadata_and_assets(tmp_path: Path) -> None:
    layout = _freshness_layout(tmp_path)
    photo_root = tmp_path / "photo-source"
    metadata = photo_root / "metadata"
    image = photo_root / "P94.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    _write_json(
        layout["manifests"] / "sources" / "SRC-PHOTO.json",
        {"source_id": "SRC-PHOTO", "status": "active", "material_type": "chapter-photo", "source_path": str(photo_root)},
    )
    book_asset = metadata / "book_asset.json"
    _write_json(book_asset, {"book_id": "a"})
    _write_json(metadata / "page_assets.json", {"items": [{"page_id": "P94", "source_image_path": str(image)}]})
    _write_json(metadata / "page_mappings.json", {"items": []})

    before = page_locator.page_locator_input_fingerprint(layout, metadata_dirname="metadata")
    original_stat = book_asset.stat()
    _write_json(book_asset, {"book_id": "b"})
    os.utime(book_asset, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    metadata_changed = page_locator.page_locator_input_fingerprint(layout, metadata_dirname="metadata")
    image.unlink()
    asset_deleted = page_locator.page_locator_input_fingerprint(layout, metadata_dirname="metadata")

    assert metadata_changed != before
    assert asset_deleted != metadata_changed


def test_exercise_fingerprint_uses_only_approved_queue_projection(tmp_path: Path) -> None:
    layout = _freshness_layout(tmp_path)
    _write_json(layout["manifests"] / "sources" / "SRC.json", {"source_id": "SRC"})
    _write_json(layout["evidence"] / "EV.json", {"evidence_id": "EV"})
    _write_json(layout["indexes"] / "pdf_book_anchors" / "SRC.json", {"anchors": []})
    queue = layout["review_queues"] / "exercise-locator" / "SRC.json"
    approved = [{"relation_id": "R1", "question_evidence_ids": ["EV-Q"], "answer_evidence_ids": ["EV-A"]}]
    _write_json(queue, {"approved_relations": approved, "items": [{"kind": "old"}], "summary": {"open_count": 1}})
    before = exercise_locator.exercise_locator_input_fingerprint(layout)

    _write_json(queue, {"approved_relations": approved, "items": [{"kind": "rewritten"}], "summary": {"open_count": 99}})
    generated_fields_changed = exercise_locator.exercise_locator_input_fingerprint(layout)
    _write_json(queue, {"approved_relations": [*approved, {"relation_id": "R2"}], "items": []})
    approval_changed = exercise_locator.exercise_locator_input_fingerprint(layout)

    assert generated_fields_changed == before
    assert approval_changed != before


def test_exercise_locator_fails_closed_when_fingerprint_is_missing(monkeypatch, tmp_path: Path) -> None:
    layout = _freshness_layout(tmp_path)
    _write_json(layout["indexes"] / exercise_locator.EXERCISE_LOCATOR_INDEX_NAME, {"relations": []})
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: layout)

    loaded = exercise_locator.load_exercise_locator_index()

    assert loaded["_availability"]["available"] is False
    assert loaded["_availability"]["reason"] == "exercise_locator_index_stale"


def test_normalize_exercise_category_preserves_canonical_values() -> None:
    assert exercise_locator.normalize_exercise_category("single-choice") == "single-choice"
    assert exercise_locator.normalize_exercise_category("comprehensive") == "comprehensive"


def test_snapshot_file_selection_excludes_media_runtime_and_rebuildable_cache(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "evidence.json").write_text("{}", encoding="utf-8")
    (tmp_path / "keep" / "page.pdf").write_bytes(b"pdf")
    (tmp_path / ".tmp").mkdir()
    (tmp_path / ".tmp" / "test.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ocr").mkdir()
    (tmp_path / "ocr" / "cache.json").write_text("{}", encoding="utf-8")

    files = create_snapshot.relative_files(
        tmp_path,
        exclude_dir_names=create_snapshot.WORKSPACE_RUNTIME_DIRS | create_snapshot.KB_REBUILDABLE_DIRS,
        exclude_suffixes=create_snapshot.MEDIA_SUFFIXES,
    )

    assert files == [Path("keep/evidence.json")]


def test_snapshot_id_only_resumes_matching_lightweight_policy(tmp_path: Path, monkeypatch) -> None:
    class FixedDateTime:
        @classmethod
        def now(cls):
            return cls()

        def strftime(self, _format: str) -> str:
            return "20260812"

    monkeypatch.setattr(create_snapshot, "datetime", FixedDateTime)
    old = tmp_path / ".staging" / "SNAP-20260812-001"
    old.mkdir(parents=True)
    (old / create_snapshot.STAGING_MARKER).write_text(
        json.dumps({"machine_owned_snapshot": True, "snapshot_id": old.name}), encoding="utf-8"
    )
    current = tmp_path / ".staging" / "SNAP-20260812-002"
    current.mkdir(parents=True)
    (current / create_snapshot.STAGING_MARKER).write_text(
        json.dumps(
            {
                "machine_owned_snapshot": True,
                "snapshot_id": current.name,
                "snapshot_policy": create_snapshot.SNAPSHOT_POLICY,
            }
        ),
        encoding="utf-8",
    )

    assert create_snapshot.allocate_snapshot_id(tmp_path) == current.name


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


def test_request_resolution_distinguishes_page_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        "query_local_knowledge.resolve_section_anchor",
        lambda **kwargs: {"status": "exact", "requested_section": "3.3.6", "section_root": "3.3", "pdf_page": 106},
    )
    section_start = resolve_request(query="94页起的3.3.6试题第4题C项", book_title="王道数据结构", chapter=None, printed_page=None, exercise_label=None)
    assert section_start["page"] == {"number": 94, "semantics": "section_start", "explicit_cli": False}
    assert section_start["section_root"] == "3.3"
    assert section_start["exercise_label"] == "04"
    assert section_start["exercise_category"] == "single-choice"
    assert section_start["requested_option"] == "C"
    exact = resolve_request(query="讲第94页第4题", book_title="王道数据结构", chapter=None, printed_page=None, exercise_label=None)
    assert exact["page"]["semantics"] == "exact_page"
    approximate = resolve_request(query="94页后面的3.3.6试题第4题的C项", book_title="王道数据结构", chapter=None, printed_page=None, exercise_label=None)
    assert approximate["page"]["semantics"] == "approximate_page"
    assert approximate["requested_option"] == "C"
    assert approximate["exercise_category"] == "single-choice"
    overridden = resolve_request(query="94页起第4题", book_title="王道数据结构", chapter=None, printed_page=94, exercise_label="4")
    assert overridden["page"]["semantics"] == "exact_page"


def test_page_crosscheck_requires_formal_section_anchor_match() -> None:
    request = {
        "page": {"number": 94, "semantics": "section_start", "explicit_cli": False},
        "section_anchor": {"status": "exact", "pdf_page": 106},
    }
    confirmed = build_page_crosscheck(request, {"match_status": "exact_evidence", "pdf_page": 106})
    assert confirmed["status"] == "confirmed"
    assert confirmed["delta_pdf_pages"] == 0

    conflict = build_page_crosscheck(request, {"match_status": "exact_evidence", "pdf_page": 107})
    assert conflict["status"] == "conflict"
    assert conflict["delta_pdf_pages"] == 1

    unmapped = build_page_crosscheck(request, {"match_status": "unmapped", "pdf_page": 0})
    assert unmapped["status"] == "unverified"


def test_approximate_page_crosscheck_has_bounded_tolerance() -> None:
    request = {
        "page": {"number": 94, "semantics": "approximate_page", "explicit_cli": False},
        "section_anchor": {"status": "exact", "pdf_page": 106},
    }
    assert build_page_crosscheck(request, {"match_status": "exact_evidence", "pdf_page": 108})["status"] == "confirmed"
    assert build_page_crosscheck(request, {"match_status": "exact_evidence", "pdf_page": 109})["status"] == "conflict"


def test_extract_exercise_block_is_exact_and_unique() -> None:
    content = "# 03. D\n第三题\n# 04. B\n第四题答案\n技巧\n# 05. A\n第五题答案"
    assert exercise_locator.extract_exercise_block(content, "4") == "# 04. B\n第四题答案\n技巧"
    assert exercise_locator.extract_exercise_block(content + "\n# 04. C\n重复", "4") == ""


def test_extract_exercise_block_uses_category_heading_to_avoid_same_number_collision() -> None:
    content = "# 1. 章节说明\n# 二、综合应用题\n01. 综合题\n# 一、单项选择题\n01. B\n单选解析\n02. C"
    assert exercise_locator.extract_exercise_block(content, "1", category="single-choice") == "01. B\n单选解析"


def test_extract_exercise_block_accepts_continued_category_until_next_heading() -> None:
    content = "35. C\n解析\n36. D\n# 二、综合应用题\n01. 综合题"
    assert exercise_locator.extract_exercise_block(content, "35", category="single-choice") == "35. C\n解析"


def test_resolver_uses_book_title_and_reports_ambiguity(monkeypatch) -> None:
    entries = [_entry("a", "李正元数一", 49), _entry("b", "另一教材", 49)]
    monkeypatch.setattr(page_locator, "load_page_locator_index", lambda: {"entries": entries, "sources": []})
    ambiguous = page_locator.resolve_page_locator(subject="数学", book_title=None, printed_page=49)
    assert ambiguous["match_status"] == "ambiguous"
    exact = page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=49)
    assert exact["match_status"] == "exact_asset"
    assert exact["book_id"] == "a"


def test_resolver_deduplicates_parallel_assets_and_prefers_evidence(monkeypatch) -> None:
    photo = _entry("photo", "王道数据结构", 94, "P94.jpg")
    photo["logical_book_id"] = "408:王道数据结构"
    pdf = {
        **_entry("pdf", "王道数据结构", 94, ""),
        "logical_book_id": "408:王道数据结构",
        "source_asset_kind": "pdf",
        "source_asset_path": "book.pdf",
        "pdf_page": 106,
        "evidence_ids": ["EV-Q"],
    }
    monkeypatch.setattr(page_locator, "load_page_locator_index", lambda: {"entries": [photo, pdf], "sources": []})
    result = page_locator.resolve_page_locator(subject="数学", book_title="王道数据结构", printed_page=94)
    assert result["match_status"] == "exact_asset"
    assert result["source_id"] == pdf["source_id"]
    assert result["evidence_ids"] == ["EV-Q"]
    assert result["alternate_assets"][0]["source_id"] == photo["source_id"]


def test_load_page_locator_fails_closed_when_index_is_stale(monkeypatch, tmp_path: Path) -> None:
    indexes = tmp_path / "indexes"
    indexes.mkdir()
    (indexes / page_locator.PAGE_LOCATOR_INDEX_NAME).write_text(
        json.dumps({"entries": [], "sources": [], "input_fingerprint": "old"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        page_locator,
        "load_runtime_config",
        lambda: SimpleNamespace(configured=True, kb_root=tmp_path, paper_book_metadata_dir="metadata"),
    )
    monkeypatch.setattr(page_locator, "page_locator_input_fingerprint", lambda layout, metadata_dirname: "new")
    monkeypatch.setattr(page_locator, "ensure_kb_layout", lambda: {})
    loaded = page_locator.load_page_locator_index()
    assert loaded["_availability"]["available"] is False
    assert loaded["_availability"]["reason"] == "page_locator_index_stale"


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


def test_resolver_reports_missing_page_as_not_found_for_mapped_source(monkeypatch) -> None:
    sources = [
        {
            "subject": "数学",
            "book_id": "a",
            "book_title": "李正元数一",
            "source_id": "SRC-a",
            "mapping_status": "mapped",
        }
    ]
    monkeypatch.setattr(page_locator, "load_page_locator_index", lambda: {"entries": [], "sources": sources})

    result = page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=9999)

    assert result["match_status"] == "not_found"
    assert result["locator_available"] is True


def test_resolver_reports_unavailable_locator_separately(monkeypatch) -> None:
    monkeypatch.setattr(
        page_locator,
        "load_page_locator_index",
        lambda: {
            "entries": [],
            "sources": [],
            "_availability": {
                "available": False,
                "path": "C:/kb/indexes/page_locator_index.json",
                "reason": "page_locator_index_missing",
                "detail": "missing",
            },
        },
    )

    result = page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=49)

    assert result["match_status"] == "unavailable"
    assert result["locator_available"] is False
    assert result["unavailable_reason"] == "page_locator_index_missing"


def test_missing_locator_file_is_not_reported_as_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        page_locator,
        "load_runtime_config",
        lambda: SimpleNamespace(kb_root=tmp_path / ".kaoyan-kb", configured=True),
    )

    result = page_locator.resolve_page_locator(subject="数学", book_title="李正元数一", printed_page=62)

    assert result["match_status"] == "unavailable"
    assert result["unavailable_reason"] == "page_locator_index_missing"


def test_unconfigured_runtime_is_not_reported_as_not_found(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        page_locator,
        "load_runtime_config",
        lambda: SimpleNamespace(kb_root=tmp_path / ".kaoyan-kb", configured=False),
    )

    result = page_locator.resolve_page_locator(subject="数学", book_title=None, printed_page=62)

    assert result["match_status"] == "unavailable"
    assert result["unavailable_reason"] == "runtime_config_missing"


def test_invalid_locator_file_is_reported_as_unavailable(monkeypatch, tmp_path: Path) -> None:
    kb_root = tmp_path / ".kaoyan-kb"
    index_path = kb_root / "indexes" / "page_locator_index.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(
        page_locator,
        "load_runtime_config",
        lambda: SimpleNamespace(kb_root=kb_root, configured=True),
    )

    result = page_locator.resolve_page_locator(subject="数学", book_title=None, printed_page=62)

    assert result["match_status"] == "unavailable"
    assert result["unavailable_reason"] == "page_locator_index_invalid"


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


def test_worked_example_relation_links_p64_question_to_p65_solution() -> None:
    evidences = [
        {
            "evidence_id": "EV-MATH-000097",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "【例 3.8】计算下列定积分：（I）原题；（II）另一问。",
            "page_classification_refs": [{"book_id": "li-math1", "book_title": "李正元数一", "chapter_id": "CH3", "printed_page": 64, "source_image_path": "P64.jpg"}],
        },
        {
            "evidence_id": "EV-MATH-000098",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "【解】（I）2\\left(\\frac12\\cdot\\frac{\\pi}{3}+\\left.\\sin x\\right|_{\\pi/3}^{\\pi/2}\\right)=\\frac{\\pi}{3}+2-\\sqrt3。\n【例 3.9】下一题。",
            "page_classification_refs": [{"book_id": "li-math1", "book_title": "李正元数一", "chapter_id": "CH3", "printed_page": 65, "source_image_path": "P65.jpg"}],
        },
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    relation = next(item for item in relations if item["exercise_label"] == "例3.8")
    assert relation["relation_status"] == "exact"
    assert relation["question"]["evidence_ids"] == ["EV-MATH-000097"]
    assert relation["question"]["printed_pages"] == [64]
    assert relation["answer"]["evidence_ids"] == ["EV-MATH-000098"]
    assert relation["answer"]["printed_pages"] == [65]
    assert "\\frac{\\pi}{3}+2-\\sqrt3" in relation["answer"]["content"]


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
    layout = {"manifests": tmp_path / "manifests", "indexes": tmp_path / "indexes"}
    (layout["manifests"] / "sources").mkdir(parents=True)
    (layout["indexes"] / "pdf_book_anchors").mkdir(parents=True)
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

    (layout["indexes"] / "pdf_book_anchors" / "SRC-PDF.json").write_text(
        __import__("json").dumps({"anchors": [{"title": "3.3.6 本节试题精选"}]}), encoding="utf-8"
    )
    relations["relations"].append({"relation_status": "exact", "source_id": "SRC-PDF", "section_root": "3.3", "category": "single-choice", "exercise_label": "04"})
    resolved = exercise_locator.find_unique_relation_for_scope(book_title="王道数据结构", chapter="3.3.6", exercise_label="4", category="single-choice")
    assert resolved["section_root"] == "3.3"


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
        ("build_book_series_indexes.py", ("--format", "quiet")),
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


def test_example_question_page_does_not_substitute_for_source_answer(monkeypatch) -> None:
    locator = {
        "match_status": "exact_evidence",
        "requested_exercise_label": "例3.14",
        "exercise_match_status": "matched",
        "matched_evidence_id": "EV-MATH-000104",
    }

    monkeypatch.setattr("query_local_knowledge.find_exact_worked_example_relation", lambda **kwargs: {})
    anchor, evidences = apply_exercise_relation(locator, [{"evidence_id": "EV-MATH-000104"}])

    assert anchor["status"] == "unverified"
    assert anchor["exercise_label"] == "例3.14"
    assert anchor["reason"] == "source-answer-not-found"
    assert evidences == [{"evidence_id": "EV-MATH-000104"}]
