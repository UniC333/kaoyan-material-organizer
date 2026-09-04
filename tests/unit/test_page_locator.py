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
from kaoyan_kb.domain import book_series
from kaoyan_kb.domain.index_freshness import fingerprint_index_inputs
from query_local_knowledge import build_answer_grounding, build_page_crosscheck, infer_exercise_from_exact_page, parse_page_anchor, resolve_current_task_book, resolve_request
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
    assert parse_page_anchor("第25页例1")["requested_exercise_label"] == "例1"


def test_request_resolution_preserves_worked_example_prefix() -> None:
    resolved = resolve_request(
        query="高数13页例8怎么做",
        book_title="高等数学辅导讲义基础篇",
        chapter=None,
        printed_page=13,
        exercise_label=None,
    )

    assert resolved["exercise_label"] == "例8"
    assert resolved["exercise_resolution"] == {
        "status": "explicit",
        "source": "query",
        "exercise_label": "例8",
        "requested_option": "",
        "candidate_labels": [],
        "matched_terms": [],
        "container_path": [],
    }

    page_local = resolve_request(
        query="第8页第一个例题",
        book_title="高等数学辅导讲义基础篇",
        chapter=None,
        printed_page=8,
        exercise_label="例（P8页内1）",
    )
    assert page_local["exercise_label"] == "例（P8页内1）"

    structured = resolve_request(
        query="高数基础篇 P18 题型四例1怎么做",
        book_title="高等数学辅导讲义基础篇",
        chapter=None,
        printed_page=None,
        exercise_label=None,
    )
    assert structured["container_path"] == ["题型四"]
    assert structured["exercise_resolution"]["container_path"] == ["题型四"]
    assert resolve_request(query="P18 题型四的例1", book_title="高等数学辅导讲义基础篇", chapter=None, printed_page=None, exercise_label=None)["container_path"] == ["题型四"]
    assert resolve_request(query="P18 题型四中例1", book_title="高等数学辅导讲义基础篇", chapter=None, printed_page=None, exercise_label=None)["container_path"] == ["题型四"]
    assert resolve_request(query="P18 题型四 无穷小的比较例1", book_title="高等数学辅导讲义基础篇", chapter=None, printed_page=None, exercise_label=None)["container_path"] == ["题型四 无穷小的比较"]
    assert resolve_request(query="P18 题型4 的例2", book_title="高等数学辅导讲义基础篇", chapter=None, printed_page=None, exercise_label=None)["container_path"] == ["题型4"]
    assert exercise_locator.normalize_container_label("题型4 无穷小的比较") == exercise_locator.normalize_container_label("题型四 无穷小的比较")


def test_current_task_default_book_requires_one_explicit_math_title(tmp_path: Path) -> None:
    task_path = tmp_path / "01_任务" / "当前任务.md"
    task_path.parent.mkdir(parents=True)
    task_path.write_text("- 数学当前只跟汤家凤《考研数学高等数学辅导讲义 基础篇》\n", encoding="utf-8")

    inferred = resolve_current_task_book(vault_root=tmp_path, subject="数学", explicit_book_title=None)
    explicit = resolve_current_task_book(vault_root=tmp_path, subject="数学", explicit_book_title="李正元数一")
    non_math = resolve_current_task_book(vault_root=tmp_path, subject="408", explicit_book_title=None)

    assert inferred["status"] == "exact"
    assert inferred["source"] == "current_task_default"
    assert inferred["book_title"] == "考研数学高等数学辅导讲义 基础篇"
    assert explicit["source"] == "explicit"
    assert explicit["book_title"] == "李正元数一"
    assert non_math["status"] == "not_applicable"


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


def test_request_resolution_parses_option_without_exercise_label() -> None:
    resolved = resolve_request(
        query="讲下数据结构94页C选项，我的问题是读取*后，读到C，C会进入操作数栈吗？",
        book_title="数据结构",
        chapter=None,
        printed_page=None,
        exercise_label=None,
    )
    assert resolved["exercise_label"] == ""
    assert resolved["requested_option"] == "C"
    assert resolved["exercise_resolution"]["status"] == "not_requested"


def test_request_resolution_separates_page_explanation_from_exercise_answer() -> None:
    page_content = resolve_request(
        query="现在我能理解117页，但 else nextval[i]=nextval[j] 是怎么来的？",
        book_title="王道数据结构",
        chapter=None,
        printed_page=117,
        exercise_label=None,
    )
    exercise = resolve_request(
        query="P117 第4题怎么做",
        book_title="王道数据结构",
        chapter=None,
        printed_page=117,
        exercise_label=None,
    )
    generic = resolve_request(
        query="KMP 的 j 回退和并查集 find 有什么联系？",
        book_title=None,
        chapter=None,
        printed_page=None,
        exercise_label=None,
    )

    assert page_content["source_request_kind"] == "page_content"
    assert exercise["source_request_kind"] == "exercise"
    assert generic["source_request_kind"] == "generic"
    assert generic["page"]["semantics"] == "none"


def test_understanding_and_explanation_do_not_trigger_either_answer_grounding_path(monkeypatch) -> None:
    query = "解释 P117 的 nextval 代码，我还没理解这里"
    anchor = {"requested_page": 117, "book_id": "SRC-408-0004", "match_status": "exact_evidence"}
    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": []})

    local_grounding = build_answer_grounding(
        query=query,
        book_title="王道数据结构",
        page_anchor=anchor,
        exercise_anchor={},
        evidences=[],
    )
    series_grounding = book_series.resolve_answer_grounding(
        query=query,
        book_title="王道数据结构",
        page_anchor=anchor,
        book_route={},
        exercise_route={},
    )

    assert local_grounding["status"] == "not_applicable"
    assert series_grounding["status"] == "not_applicable"


def test_explicit_exercise_still_requires_exact_source_answer(monkeypatch) -> None:
    anchor = {"requested_page": 117, "book_id": "SRC-408-0004", "match_status": "exact_evidence"}
    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": []})

    grounding = book_series.resolve_answer_grounding(
        query="P117 第4题 C 选项怎么判断？",
        book_title="王道数据结构",
        page_anchor=anchor,
        book_route={},
        exercise_route={},
    )

    assert grounding["required"] is True
    assert grounding["status"] == "answer_not_found"
    assert grounding["can_conclude"] is False


def test_request_resolution_keeps_multiple_direct_options_ambiguous() -> None:
    resolved = resolve_request(
        query="第94页的B选项和C选项分别怎么判断？",
        book_title="数据结构",
        chapter=None,
        printed_page=None,
        exercise_label=None,
    )
    assert resolved["requested_option"] == ""


def test_page_content_inference_uses_unique_distinctive_terms(monkeypatch) -> None:
    monkeypatch.setattr(
        "query_local_knowledge.list_exact_relations_for_question_page",
        lambda **kwargs: {
            "status": "exact",
            "relations": [
                {"exercise_label": "02", "question_content": "表达式 a*(b+c)-d 的后缀表达式是（ ）。"},
                {"exercise_label": "04", "question_content": "利用栈求表达式的值时，设立运算数栈 OPEN。"},
                {"exercise_label": "05", "question_content": "执行下列递归语句段后，i 的值为（ ）。"},
            ],
        },
    )
    result = infer_exercise_from_exact_page(
        locator={"source_id": "SRC-408", "pdf_page": 106},
        query="读到 C 时会进入操作数栈吗？",
        category="single-choice",
        requested_option="C",
    )
    assert result["status"] == "inferred_unique"
    assert result["exercise_label"] == "04"
    assert "运算数栈" in result["matched_terms"]


def test_page_content_inference_fails_closed_on_weak_shared_terms(monkeypatch) -> None:
    monkeypatch.setattr(
        "query_local_knowledge.list_exact_relations_for_question_page",
        lambda **kwargs: {
            "status": "exact",
            "relations": [
                {"exercise_label": "02", "question_content": "下面哪个表达式含有乘法运算？"},
                {"exercise_label": "04", "question_content": "利用栈计算下面的表达式。"},
            ],
        },
    )
    result = infer_exercise_from_exact_page(
        locator={"source_id": "SRC-408", "pdf_page": 106},
        query="这个表达式里的 C 选项怎么判断？",
        category="single-choice",
        requested_option="C",
    )
    assert result["status"] == "ambiguous"
    assert result["exercise_label"] == ""


def test_page_content_inference_accepts_one_formal_candidate(monkeypatch) -> None:
    monkeypatch.setattr(
        "query_local_knowledge.list_exact_relations_for_question_page",
        lambda **kwargs: {"status": "exact", "relations": [{"exercise_label": "07", "question_content": "唯一题目"}]},
    )
    result = infer_exercise_from_exact_page(locator={"source_id": "SRC", "pdf_page": 1}, query="讲这道题")
    assert result["status"] == "inferred_unique"
    assert result["exercise_label"] == "07"


def test_page_content_inference_propagates_unavailable_index(monkeypatch) -> None:
    availability = {"available": False, "reason": "exercise_locator_index_stale"}
    monkeypatch.setattr(
        "query_local_knowledge.list_exact_relations_for_question_page",
        lambda **kwargs: {
            "status": "unavailable",
            "unavailable_reason": "exercise_locator_index_stale",
            "unavailable_detail": "inputs changed",
            "_availability": availability,
        },
    )
    result = infer_exercise_from_exact_page(locator={"source_id": "SRC", "pdf_page": 1}, query="讲这道题")
    assert result["status"] == "unavailable"
    assert result["unavailable_reason"] == "exercise_locator_index_stale"


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


def test_extract_exercise_block_stops_at_following_numbered_textbook_section() -> None:
    content = "# 二、综合应用题\n# 01.【解答】\n综合题答案\n# 5.2 二叉树的概念\n# 1. 二叉树的定义"
    assert exercise_locator.extract_exercise_block(content, "01", category="comprehensive") == "# 01.【解答】\n综合题答案"


def test_extract_exercise_block_distinguishes_question_continuation_from_answer_heading() -> None:
    content = "04. 第四题题干\n# 5.1.5 答案与解析\n# 一、单项选择题\n04. A\n第四题答案"
    assert exercise_locator.extract_exercise_block(content, "04", category="single-choice", phase="question") == "04. 第四题题干"
    assert exercise_locator.extract_exercise_block(content, "04", category="single-choice", phase="answer") == "04. A\n第四题答案"


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


def test_english_example_translation_is_an_exact_worked_example_relation() -> None:
    evidences = [{
        "evidence_id": "EV-ENG-44",
        "verification_status": "reviewed",
        "source_grounded": True,
        "content": "例：A long sentence.\n译：一个长句。\n语法说明：本句含一个谓语。",
        "page_classification_refs": [{"book_id": "PDFOCR-SRC-ENG-0001", "book_title": "句句真研", "chapter_id": "ENG-1", "printed_page": 44, "source_image_path": "P44.png"}],
    }]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert len(relations) == 1
    assert relations[0]["relation_status"] == "exact"
    assert relations[0]["question"]["content"] == "例：A long sentence."
    assert "译：一个长句。" in relations[0]["answer"]["content"]


def test_plain_worked_example_relations_accept_integer_labels_and_restart_by_page() -> None:
    evidences = [
        {
            "evidence_id": f"EV-TANG-{page}",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 求极限。\n解 书中解答。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH1",
                    "printed_page": page,
                    "source_image_path": f"P{page}.jpg",
                }
            ],
        }
        for page in (25, 30)
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert len(relations) == 2
    assert {item["relation_id"] for item in relations} == {
        "EXW-tang-math1-CH1-p25-例1",
        "EXW-tang-math1-CH1-p30-例1",
    }
    assert all(item["relation_status"] == "exact" for item in relations)


def test_worked_example_relations_do_not_pair_across_missing_printed_pages() -> None:
    evidences = [
        {
            "evidence_id": "EV-TANG-54",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 求不定积分。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH3",
                    "printed_page": 54,
                    "source_image_path": "P54.jpg",
                }
            ],
        },
        {
            "evidence_id": "EV-TANG-64",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "解 这是另一连续页段中的解答。\n例2 求定积分。\n解 第二题答案。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH3",
                    "printed_page": 64,
                    "source_image_path": "P64.jpg",
                }
            ],
        },
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    first = next(item for item in relations if item["exercise_label"] == "例1")
    second = next(item for item in relations if item["exercise_label"] == "例2")
    assert first["relation_status"] == "question_only"
    assert first["answer"] == {}
    assert second["relation_status"] == "exact"
    assert second["question"]["printed_pages"] == [64]
    assert second["answer"]["printed_pages"] == [64]


def test_worked_example_answer_stops_before_following_theorem_proof() -> None:
    evidences = [
        {
            "evidence_id": "EV-TANG-44",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 证明一个结论。\n证明 例题证明。\n定理2 拉格朗日中值定理。\n证明 定理证明。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH3",
                    "printed_page": 44,
                    "source_image_path": "P44.jpg",
                }
            ],
        }
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert len(relations) == 1
    assert relations[0]["relation_status"] == "exact"
    assert relations[0]["answer"]["content"] == "证明 例题证明。"


def test_worked_example_answer_stops_before_numbered_theory_paragraph() -> None:
    evidences = [
        {
            "evidence_id": "EV-TANG-30",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例2 求常数。\n解 常数为2。\n(3) 若函数可导，则函数连续。\n证明 理论证明。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH2",
                    "printed_page": 30,
                    "source_image_path": "P30.jpg",
                }
            ],
        }
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert len(relations) == 1
    assert relations[0]["relation_status"] == "exact"
    assert relations[0]["answer"]["content"] == "解 常数为2。"


def test_unnumbered_worked_examples_receive_page_local_labels() -> None:
    evidences = [
        {
            "evidence_id": "EV-TANG-8",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "[例] 讨论分段函数的极限。\n解 第一题答案。\n[例] 讨论指数函数的极限。\n解 第二题答案。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH1",
                    "printed_page": 8,
                    "source_image_path": "P8.jpg",
                }
            ],
        }
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert [item["exercise_label"] for item in relations] == ["例（P8页内1）", "例（P8页内2）"]
    assert all(item["relation_status"] == "exact" for item in relations)


def test_same_page_restarted_labels_are_exact_and_page_local() -> None:
    evidences = [
        {
            "evidence_id": "EV-TANG-18",
            "verification_status": "source_grounded",
            "source_grounded": True,
            "content": "例1 求数列极限。\n解 第一题答案。\n# 题型四 无穷小的比较\n例1 比较无穷小。\n解 第二题答案。",
            "page_classification_refs": [
                {
                    "book_id": "tang-math1",
                    "book_title": "汤家凤高数基础篇",
                    "chapter_id": "CH1",
                    "printed_page": 18,
                    "source_image_path": "P18.jpg",
                }
            ],
        }
    ]

    relations = exercise_locator.build_worked_example_relations(evidences)

    assert [item["exercise_label"] for item in relations] == ["例1", "例1"]
    assert [item["container_path"] for item in relations] == [[], ["题型四 无穷小的比较"]]
    assert len({item["location_key"] for item in relations}) == 2
    assert all(item["relation_status"] == "exact" for item in relations)
    assert "题型四" not in relations[0]["answer"]["content"]


def test_worked_example_container_path_resolves_same_page_restarted_label(monkeypatch, tmp_path: Path) -> None:
    layout = {"indexes": tmp_path / "indexes", "manifests": tmp_path / "manifests", "evidence": tmp_path / "evidence", "review_queues": tmp_path / "queues"}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    evidences = [{
        "evidence_id": "EV-TANG-18", "verification_status": "source_grounded", "source_grounded": True,
        "content": "例1 第一题。\n解 第一解。\n# 题型四 无穷小的比较\n例1 第二题。\n解 第二解。",
        "page_classification_refs": [{"book_id": "tang-math1", "book_title": "汤家凤高数基础篇", "chapter_id": "CH1", "printed_page": 18, "source_image_path": "P18.jpg"}],
    }]
    relations = exercise_locator.build_worked_example_relations(evidences)
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: layout)
    monkeypatch.setattr(exercise_locator, "exercise_locator_input_fingerprint", lambda _layout: "fresh")
    _write_json(layout["indexes"] / exercise_locator.EXERCISE_LOCATOR_INDEX_NAME, {"schema_version": "exercise-locator.v3", "input_fingerprint": "fresh", "relations": relations})

    ambiguous = exercise_locator.find_exact_worked_example_relation(book_id="tang-math1", printed_page=18, exercise_label="例1")
    exact = exercise_locator.find_exact_worked_example_relation(book_id="tang-math1", printed_page=18, exercise_label="例1", container_path=["题型四"])
    arabic_ordinal = exercise_locator.find_exact_worked_example_relation(book_id="tang-math1", printed_page=18, exercise_label="例1", container_path=["题型4"])

    assert ambiguous["relation_status"] == "needs_review"
    assert ambiguous["candidate_locations"][1]["container_path"] == ["题型四 无穷小的比较"]
    assert exact["relation_status"] == "exact"
    assert exact["container_path"] == ["题型四 无穷小的比较"]
    assert arabic_ordinal["relation_status"] == "exact"
    assert arabic_ordinal["location_key"] == exact["location_key"]


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
        (layout["evidence"] / f"EV-{page}.json").write_text(__import__("json").dumps({"evidence_id": f"EV-{page}", "source_id": "SRC-PDF", "origin_type": "pdf_page_ocr", "verification_status": "reviewed", "source_grounded": True, "pdf_page": page, "printed_page": page - 2, "locator": {"page_start": page}, "content": content}), encoding="utf-8")
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


def test_page_relation_candidates_exclude_unusable_question_slices(monkeypatch, tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    _write_json(evidence_root / "EV-Q1.json", {"content": "# 一、单项选择题\n01. 可唯一切片的题目。\n\n02. 下一题。"})
    _write_json(evidence_root / "EV-Q2.json", {"content": "该证据没有正式题号标记。"})
    monkeypatch.setattr(exercise_locator, "ensure_kb_layout", lambda: {"evidence": evidence_root})
    monkeypatch.setattr(
        exercise_locator,
        "load_exercise_locator_index",
        lambda: {
            "relations": [
                {"relation_status": "exact", "source_id": "SRC", "category": "single-choice", "exercise_label": "01", "question_pdf_pages": [10], "question_evidence_ids": ["EV-Q1"]},
                {"relation_status": "exact", "source_id": "SRC", "category": "single-choice", "exercise_label": "02", "question_pdf_pages": [10], "question_evidence_ids": ["EV-Q2"]},
                {"relation_status": "needs_review", "source_id": "SRC", "category": "single-choice", "exercise_label": "03", "question_pdf_pages": [10], "question_evidence_ids": ["EV-Q1"]},
            ]
        },
    )
    result = exercise_locator.list_exact_relations_for_question_page(source_id="SRC", question_pdf_page=10, category="single-choice")
    assert result["status"] == "exact"
    assert [item["exercise_label"] for item in result["relations"]] == ["01"]


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
            "evidence_id": "EV-PDF-67", "evidence_key": "EV-PDF-67-key", "subject": "408",
            "source_id": "SRC-PDF", "chapter_id": "CH-3", "chunk_id": "CHUNK-79",
            "title": "page 67", "content": "page content", "origin_type": "paper_book_reviewed_ocr",
            "source_grounded": True, "verification_status": "reviewed", "review_status": "accepted",
            "locator": {"page_start": 79}, "chapter_title": "第3章 栈、队列和数组",
            "source_spans": [{"source_id": "SRC-PDF", "file_id": "FILE-79", "locator": {"page_start": 67, "page_end": 67, "image_start": 79, "image_end": 79}}],
            "provenance": {"origin_type": "paper_book_reviewed_ocr", "verification_status": "reviewed", "source_grounded": True, "source_spans": [{"source_id": "SRC-PDF", "file_id": "FILE-79", "locator": {"page_start": 67, "page_end": 67, "image_start": 79, "image_end": 79}}]},
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
