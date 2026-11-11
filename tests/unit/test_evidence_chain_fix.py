from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from kaoyan_kb.domain import book_series, exercise_locator, page_locator
from kaoyan_kb.domain.query import permission
from kaoyan_kb.domain.query.request import normalize_text, resolve_request


BOOK = "TANG2027-M1-CALCULUS-BASIC"
TITLE = "考研数学高等数学辅导讲义 基础篇"
CHAPTER = "CH-TANG2027-B-CALC-"


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def chapter_index(tmp_path, monkeypatch):
    layout = {key: tmp_path / key for key in ("indexes", "evidence", "manifests")}
    for module in (exercise_locator, page_locator, permission):
        monkeypatch.setattr(module, "kb_layout", lambda: layout)
    pairs = [{"pair_kind": "same_book_worked_example", "book_id": BOOK, "book_title": TITLE,
              "chapter_id": CHAPTER + "01", "chapter_title": "第一章", "exercise_label": "例1",
              "container_path": ["题型七 极限的存在性问题"], "pair_status": "exact_pair",
              "question": {"book_id": BOOK, "evidence_ids": ["EV-Q"], "printed_pages": [26], "content": "递推数列"},
              "solution": {"evidence_ids": ["EV-A"], "content": "数列有界且单调"}}]
    write(layout["indexes"] / "exercise_pair_index.json", {"items": pairs})
    monkeypatch.setattr(book_series, "load_exercise_pair_index", lambda: {"items": pairs})
    return layout, pairs


def grounding(query):
    return book_series.resolve_answer_grounding(query=query, book_title=TITLE, page_anchor={"book_id": BOOK},
                                               book_route={}, exercise_route={})


def test_fallback_rejects_cross_chapter_and_keeps_conflict_reason(chapter_index):
    result = grounding("第三章题型七例1：函数极值点")
    assert result["status"] == "answer_not_found"
    assert result["problem"] == {}
    assert "请求第三章、唯一候选在第一章" in result["failure_reason"]


def test_content_veto_and_solution_only_match(chapter_index):
    assert "内容证据不足" in grounding("题型七例1：保号性判断极值点")["failure_reason"]
    assert grounding("题型七例1：有界且单调")["status"] == "answer_not_found"
    assert grounding("第一章题型七例1：有界且单调")["status"] == "exact_answer"  # fake solution has literal 有界
    assert grounding("第一章题型七例1")["status"] == "exact_answer"


def test_chapter_mapping_is_book_scoped_and_unknown_conventions_stay_missing(chapter_index):
    assert exercise_locator.requested_chapter_id(chapter_number=3, book_id=BOOK) == CHAPTER + "03"
    assert exercise_locator.requested_chapter_id(chapter_number=3, book_id="other") == ""
    assert exercise_locator.requested_chapter_id(chapter_number=None, book_id=BOOK) == ""


def test_pair_missing_chapter_rejected_when_book_convention_is_known(chapter_index):
    _, pairs = chapter_index
    pairs.append({**deepcopy(pairs[0]), "chapter_id": "", "container_path": ["题型八"]})
    result = grounding("第一章题型八例1")
    assert result["status"] != "exact_answer"
    assert "章节冲突" in result["failure_reason"]


def test_route_preserves_both_chapter_representations(chapter_index):
    _, pairs = chapter_index
    pairs[0].update(series_id="TANG", stage="basic", exercise_type="worked")
    pairs[0]["question"]["printed_pages"] = [26]
    result = book_series.resolve_exercise_route(query="第一章26页例1", book_route={"series_id": "TANG", "match_status": "exact_series"})
    assert result["match_status"] == "exact_exercise"
    result = book_series.resolve_exercise_route(query="第三章26页例1", book_route={"series_id": "TANG", "match_status": "exact_series"})
    assert result["match_status"] == "not_found"
    pairs[:] = [{"series_id": "JIELI", "chapter_number": 1, "exercise_number": 7, "pair_status": "exact_pair"}]
    for number, expected in [(1, "exact_exercise"), (2, "not_found")]:
        result = book_series.resolve_exercise_route(query=f"第{number}章第7题", book_route={"series_id": "JIELI", "match_status": "exact_series"})
        assert result["match_status"] == expected


def test_scope_relation_checks_chapter_id_and_missing_exception(chapter_index, monkeypatch, caplog):
    layout, _ = chapter_index
    write(layout["manifests"] / "sources/SRC.json", {"source_id": "SRC", "status": "active", "material_type": "book-pdf", "source_name": TITLE})
    relation = {"relation_id": "R", "relation_status": "exact", "source_id": "SRC", "book_id": BOOK,
                "section_root": "3", "chapter_id": CHAPTER + "01", "exercise_label": "01"}
    monkeypatch.setattr(exercise_locator, "load_exercise_locator_index", lambda: {"relations": [relation]})
    args = dict(book_title=TITLE, chapter="第三章", exercise_label="01")
    assert exercise_locator.find_unique_relation_for_scope(**args) == {}
    relation["chapter_id"] = CHAPTER + "03"
    assert exercise_locator.find_unique_relation_for_scope(**args) == relation
    relation.pop("chapter_id")
    assert exercise_locator.find_unique_relation_for_scope(**args) == relation
    assert "has no chapter_id" in caplog.text


def test_chinese_chapter_and_tili_container(chapter_index):
    request = resolve_request(query="题理一例1", book_title=TITLE, chapter="第三章", printed_page=None, exercise_label=None)
    assert request["exercise_scope"]["chapter_number"] == 3
    assert request["section_root"] == "3"
    assert request["container_path"] == ["题理一"]
    assert exercise_locator.normalize_container_label("题理一") == exercise_locator.normalize_container_label("题理 1")
    assert not exercise_locator._container_heading("划重点 1")


@pytest.mark.parametrize("actual,allowed", [(CHAPTER + "01", False), (CHAPTER + "03", True), (None, True)])
def test_permission_reads_evidence_chapter_and_only_vetoes_conflict(chapter_index, actual, allowed):
    layout, _ = chapter_index
    write(layout["evidence"] / "EV-Q.json", {"chapter_id": actual})
    payload = {"request_resolution": {"source_request_kind": "exercise", "exercise_scope": {"chapter_number": 3}, "book_title": TITLE},
               "book_resolution": {"book_id": BOOK},
               "answer_grounding": {"status": "exact_answer", "can_conclude": True, "problem": {"book_id": BOOK, "evidence_ids": ["EV-Q"]}, "solution": {"evidence_ids": ["EV-A"]}},
               "teaching_bundle": {"status": "exact", "problem_text": "题", "source_answer_text": "解", "citations": {"problem_evidence_ids": ["EV-Q"], "solution_evidence_ids": ["EV-A"]}}}
    assert permission.teaching_permission(payload) is allowed
    payload["answer_grounding"]["can_conclude"] = False
    assert not permission.teaching_permission(payload)


@pytest.mark.parametrize("token", sorted(exercise_locator.GENERIC_MATH_CONTENT_STOPLIST))
def test_generic_math_stoplist_document_frequency_requires_review(token):
    pairs = book_series.load_exercise_pair_index()["items"]
    documents = [normalize_text("\n".join(str((item.get(side) or {}).get(key) or "")
                                       for side in ("question", "solution", "answer") for key in ("content", "text")))
                 for item in pairs]
    frequency = sum(token in document for document in documents)
    assert frequency >= 10, f"停用词 {token!r} 的 DF={frequency} < 10，须复核停用表"


def test_v4_4_full_question_rejects_generic_math_match(monkeypatch, capsys):
    import sys
    import ask_local_knowledge as ask_module

    query = "题型七例1：设 f 可导，lim f′(x)/sin³πx=−2，判断 x=1 是否为 f 的极值点"
    result = grounding(query)
    assert result["status"] == "answer_not_found"
    assert "内容证据不足" in result["failure_reason"]
    assert "EV-MATH-000184" not in result["problem"].get("evidence_ids", [])

    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--format", "teaching-json", "--subject", "数学", "--question", query])
    assert ask_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_mode"] == "exercise_unconfirmed"
    assert "内容证据不足" in payload["answer_grounding"]["failure_reason"]
    assert payload["teaching_bundle"]["status"] != "exact"
    assert "EV-MATH-000184" not in payload["answer_grounding"]["problem"].get("evidence_ids", [])


@pytest.mark.parametrize("query,state,exact", [
    ("第一章题型七例1", "no_content", True),
    ("题型七例1：求极限，判断单调性", "generic_only", False),
    ("第一章题型七例1：求极限", "generic_only", True),
    ("题型七例1：有界且单调", "has_anchor", False),
    ("第一章题型七例1：有界且单调", "has_anchor", False),
    ("题型七例1：单调递减", "generic_only", False),
])
def test_r2_states_grounding_and_ask(query, state, exact, monkeypatch, capsys):
    import sys
    import ask_local_knowledge as ask_module
    item = next(item for item in book_series.load_exercise_pair_index()["items"]
                if item.get("book_id") == BOOK and item.get("exercise_label") == "例1"
                and (item.get("container_path") or [""])[0].startswith("题型七"))
    analysis = exercise_locator.analyze_exercise_content(query, item, book_title=TITLE)
    assert analysis["state"] == state
    assert analysis["has_content_description"] == (state != "no_content")
    assert all({"token", "category"} <= entry.keys() for entry in analysis["tokens_before_filter"])
    assert all(entry["category"] == "anchor" for entry in analysis["tokens_after_filter"])
    result = grounding(query)
    assert (result["status"] == "exact_answer") == exact
    assert result["content_analysis"][0]["state"] == state
    if not exact:
        assert result["failure_code"] == "content_evidence_insufficient"
        assert "内容证据不足" in result["failure_reason"]
        assert "无法确认" in result["failure_reason"]
        assert "内容不符" not in result["failure_reason"]
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--format", "teaching-json",
                                     "--subject", "数学", "--question", query])
    assert ask_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_mode"] == ("exercise_pair" if exact else "exercise_unconfirmed")
    assert (payload["teaching_bundle"]["status"] == "exact") == exact


@pytest.mark.parametrize("token", sorted(exercise_locator.GENERIC_MATH_CONTENT_STOPLIST))
def test_r2_generic_signal_cannot_supply_support(chapter_index, token):
    _, pairs = chapter_index
    pairs[0]["question"]["content"] = token
    query = f"题型七例1：{token}"
    analysis = exercise_locator.analyze_exercise_content(query, pairs[0])
    assert analysis["state"] == "generic_only"
    assert not analysis["tokens_after_filter"]
    assert {"token": token, "category": "generic_math", "kind": "term"} in analysis["tokens_before_filter"]
    assert not exercise_locator.exercise_content_matches(query, pairs[0])
    assert grounding(query)["status"] != "exact_answer"


@pytest.mark.parametrize("query", ["第一章例1：极限", "题型七例1：极限", "第一章题型七：极限"])
def test_r2_generic_exception_requires_all_three_locators(chapter_index, query):
    assert grounding(query)["status"] != "exact_answer"


def test_r2_generic_exception_requires_unique_after_chapter_filter(chapter_index):
    _, pairs = chapter_index
    pairs.append(deepcopy(pairs[0]))
    result = grounding("第一章题型七例1：极限")
    assert result["status"] != "exact_answer"
    assert not any(entry["location_evidence"]["triple_unique"] for entry in result["content_analysis"])


@pytest.mark.parametrize("prefix,allowed", [("", False), ("第一章", False), ("题型七", False), ("第一章题型七", True), ("第三章题型七", False)])
def test_r2_solution_only_requires_matching_chapter_and_container(chapter_index, prefix, allowed):
    _, pairs = chapter_index
    pairs[0]["question"]["content"] = "递推数列"
    pairs[0]["solution"]["content"] = "使用泰勒公式"
    query = prefix + "例1：泰勒"
    analysis = exercise_locator.analyze_exercise_content(query, pairs[0])
    assert analysis["hit_location"] == "solution"
    assert not analysis["question_hit"]
    assert (grounding(query)["status"] == "exact_answer") == allowed


def test_r2_question_hit_needs_no_solution_only_exception(chapter_index):
    _, pairs = chapter_index
    pairs[0]["question"]["content"] = "递推数列"
    assert grounding("题型七例1：递推")["status"] == "exact_answer"


@pytest.mark.parametrize("phrase", list(exercise_locator.DIRECTION_ATTRIBUTES))
def test_r2_direction_is_retained_without_strong_anchor(phrase):
    analysis = exercise_locator.analyze_exercise_content(phrase, {"solution": {"content": phrase}})
    assert analysis["state"] == "generic_only"
    assert analysis["direction_matches"] == [phrase]
    assert {"token": phrase, "category": "diagnostic", "kind": "direction"} in analysis["tokens_before_filter"]
    assert not analysis["anchor_hits"]


@pytest.mark.parametrize("formula", ["f′(x)/sin³(πx) → −2", "ｆʼ（ｘ）/sin3(πx)→–2", "f'(x)/sin3(πx)→-2"])
def test_r2_complete_formula_relation_is_strong_anchor(formula, chapter_index):
    _, pairs = chapter_index
    pairs[0]["question"]["content"] = "f'(x)/sin3(πx)→-2"
    pairs[0]["solution"]["content"] = "已核对"
    query = "题型七例1：" + formula
    analysis = exercise_locator.analyze_exercise_content(query, pairs[0])
    assert any(hit["kind"] == "formula_relation" and hit["locations"] == ["question"] for hit in analysis["anchor_hits"])
    assert grounding(query)["status"] == "exact_answer"
    pairs[0]["question"]["content"] = "sin 极限 f'(x)/sin3(πx)→-3"
    assert not any(hit["kind"] == "formula_relation" for hit in exercise_locator.analyze_exercise_content(query, pairs[0])["anchor_hits"])


@pytest.mark.parametrize("query_attr,expected", [("有界", "bounded"), ("有上界", "upper_bounded"), ("有下界", "lower_bounded")])
def test_r2_boundedness_weak_association_never_replaces_literal_support(query_attr, expected):
    analysis = exercise_locator.analyze_exercise_content(query_attr, {"solution": {"content": "单调递减且有下界"}})
    assert analysis["requested_attributes"] == [expected]
    assert analysis["candidate_attributes"]["solution"] == ["lower_bounded", "decreasing"]
    assert analysis["weak_associations"] == ["boundedness"]
    if query_attr == "有界":
        assert not analysis["anchor_hits"]


@pytest.mark.parametrize("token,df,chapters", [("收敛", 12, 3), ("发散", 4, 2)])
def test_r2_df_does_not_justify_adding_convergence_terms(token, df, chapters):
    pairs = book_series.load_exercise_pair_index()["items"]
    hits = [item for item in pairs if token in normalize_text("\n".join(
        str((item.get(side) or {}).get(key) or "") for side in ("question", "solution", "answer") for key in ("content", "text")))]
    assert len(hits) == df
    assert len({item.get("chapter_id") or (item.get("book_id"), item.get("chapter_number")) for item in hits}) == chapters
    assert df < 20 or chapters < 4  # Reviewed admission threshold, not met.
    assert token not in exercise_locator.CONTENT_EVIDENCE_USE["generic_math"]


@pytest.mark.parametrize("suffix", ["保号性", "单调 保号性 极限", "极限 保号性"])
def test_r2_adding_generic_words_preserves_real_strong_anchor(suffix, monkeypatch, capsys):
    import sys
    import ask_local_knowledge as ask_module
    query = "65页例1：设f可导，lim f′(x)/sin³πx=−2，判断x=1是否为f的极值点；" + suffix
    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--format", "teaching-json", "--subject", "数学",
                                     "--book-title", TITLE, "--printed-page", "65", "--question", query])
    assert ask_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_grounding"]["status"] == "exact_answer"
    assert payload["answer_grounding"]["problem"]["evidence_ids"] == ["EV-MATH-000154"]
    assert payload["teaching_bundle"]["status"] == "exact"


def test_r2_chapter_conflict_preserves_original_text_and_code(chapter_index):
    result = grounding("第三章题型七例1：保号性")
    assert result["failure_reason"] == "章节冲突：请求第三章、唯一候选在第一章；不确立原题—题解精确配对。"
    assert result["failure_code"] == "content_conflict"


@pytest.mark.parametrize("query,exact", [
    ("26页题型七例1：极限", False),
    ("26页题型七例1：泰勒", True),
    ("第一章26页题型七例1：泰勒", True),
])
def test_r2_exercise_route_uses_same_content_gate(chapter_index, query, exact):
    _, pairs = chapter_index
    pairs[0].update(series_id="TANG", stage="basic", exercise_type="worked")
    pairs[0]["question"]["content"] = "递推数列"
    pairs[0]["solution"]["content"] = "泰勒公式"
    route = book_series.resolve_exercise_route(query=query, book_route={"series_id": "TANG", "match_status": "exact_series"})
    assert (route["match_status"] == "exact_exercise") == exact
    assert route["content_analysis"]
    if not exact:
        assert route["failure_code"] == "content_evidence_insufficient"


def test_r2fix_original_request_grounding_and_ask(monkeypatch, capsys):
    import sys
    import ask_local_knowledge as ask_module

    query = "高数基础篇65页的例1，是不是直接分别写 x 趋于 1- 和 1+ 也可以？一定要写保号性吗？"
    anchor = page_locator.resolve_page_locator(subject="数学", book_title=TITLE, printed_page=65, exercise_label="例1")
    anchor = page_locator.resolve_page_from_accepted_evidence(subject="数学", book_title=TITLE, printed_page=65, locator=anchor)
    assert anchor["match_status"] == "exact_evidence"
    result = book_series.resolve_answer_grounding(query=query, book_title=TITLE, page_anchor=anchor,
                                                 book_route={}, exercise_route={})
    assert result["status"] == "exact_answer"
    assert result["can_conclude"]
    analysis = result["content_analysis"][0]
    assert analysis["state"] == "has_anchor"
    assert not analysis["question_hit"] and analysis["solution_hit"]
    assert analysis["location_evidence"]["page_match"]
    assert not analysis["location_evidence"]["chapter_match"]
    assert not analysis["location_evidence"]["container_match"]

    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--format", "teaching-json",
                                     "--subject", "数学", "--question", query])
    assert ask_module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_mode"] == "exercise_pair"
    assert payload["answer_grounding"]["status"] == "exact_answer"
    assert payload["answer_grounding"]["can_conclude"]
    assert payload["teaching_bundle"]["status"] == "exact"
    assert payload["page_anchor"]["match_status"] == "exact_evidence"
    assert payload["page_anchor"]["source_id"] == "SRC-MATH-0006"
    for answer in (result, payload["answer_grounding"]):
        for side in ("problem", "solution"):
            assert answer[side]["evidence_ids"] == ["EV-MATH-000154"]
            assert answer[side]["printed_pages"] == [65]


@pytest.mark.parametrize("prefix,allowed", [("26页", True), ("27页", False), ("", False)])
def test_r2fix_solution_only_page_anchor(chapter_index, prefix, allowed):
    _, pairs = chapter_index
    pairs[0]["question"]["content"] = "递推数列"
    pairs[0]["solution"]["content"] = "使用泰勒公式"
    result = grounding(prefix + "例1：泰勒")
    analysis = result["content_analysis"][0]
    assert analysis["state"] == "has_anchor"
    assert not analysis["question_hit"] and analysis["solution_hit"]
    assert analysis["location_evidence"]["page_match"] == allowed
    assert (result["status"] == "exact_answer") == allowed
    if not allowed:
        assert "仅题解命中而缺少章节及容器定位或页码定位" in result["failure_reason"]


def test_r2fix_page_anchor_requires_unique_after_chapter_filter(chapter_index):
    _, pairs = chapter_index
    pairs[0]["solution"]["content"] = "使用泰勒公式"
    pairs.append(deepcopy(pairs[0]))
    result = grounding("第一章26页例1：泰勒")
    assert result["status"] == "answer_not_found"
    assert len(result["content_analysis"]) == 2
    assert all(entry["location_evidence"]["chapter_match"] for entry in result["content_analysis"])
    assert not any(entry["location_evidence"]["page_match"] for entry in result["content_analysis"])


@pytest.mark.parametrize("label", [None, "例2"])
def test_r2fix_page_anchor_requires_matching_label(chapter_index, label):
    _, pairs = chapter_index
    pairs[0]["solution"]["content"] = "使用泰勒公式"
    diagnostics = []
    matched, reason, code = book_series._filter_exercise_candidates(
        pairs, request={"printed_page": 26, "exercise_label": label}, query="26页：泰勒",
        book_title=TITLE, diagnostics=diagnostics)
    assert not matched
    assert "内容证据不足" in reason
    assert not diagnostics[0]["location_evidence"]["page_match"]


def test_r2fix_page_anchor_does_not_exempt_generic_only(chapter_index):
    result = grounding("26页例1：极限")
    assert result["status"] == "answer_not_found"
    analysis = result["content_analysis"][0]
    assert analysis["state"] == "generic_only"
    assert analysis["location_evidence"]["page_match"]
    assert not analysis["location_evidence"]["triple_unique"]


def c5_ask(query, monkeypatch, capsys):
    import sys
    import ask_local_knowledge as ask_module

    monkeypatch.setattr(sys, "argv", ["ask_local_knowledge.py", "--format", "teaching-json",
                                     "--subject", "数学", "--book-title", TITLE, "--question", query])
    assert ask_module.main() == 0
    return json.loads(capsys.readouterr().out)


def c5_grounding(query):
    request = book_series.parse_exercise_request(query)
    return book_series.resolve_answer_grounding(
        query=query, book_title=TITLE, page_anchor={"book_id": BOOK, "requested_page": request["printed_page"]},
        book_route={}, exercise_route={}, request=request)


@pytest.mark.parametrize("query", ["66页例6：零点定理", "66页例6：中值定理", "60页例2：罗尔定理"])
def test_c5_false_theorem_matches_rejected_grounding_and_ask(query, monkeypatch, capsys):
    direct = c5_grounding(query)
    payload = c5_ask(query, monkeypatch, capsys)
    assert payload["answer_mode"] == "exercise_unconfirmed"
    assert payload["teaching_bundle"]["status"] != "exact"
    for result in (direct, payload["answer_grounding"]):
        assert result["status"] != "exact_answer"
        assert not result["can_conclude"]
        assert result["failure_code"] == "content_evidence_insufficient"
        assert "内容证据不足" in result["failure_reason"]
        assert not result["problem"].get("evidence_ids")
        assert not result["solution"].get("evidence_ids")
    assert not any(a["anchor_hits"] for a in direct["content_analysis"])


def test_c5_zero_theorem_control_keeps_real_continuation(monkeypatch, capsys):
    query = "60页例2：零点定理"
    payload = c5_ask(query, monkeypatch, capsys)
    assert payload["answer_mode"] == "exercise_pair"
    assert payload["teaching_bundle"]["status"] == "exact"
    direct = c5_grounding(query)
    for result in (direct, payload["answer_grounding"]):
        assert result["status"] == "exact_answer"
        assert result["problem"]["evidence_ids"] == ["EV-MATH-000210"]
        assert result["problem"]["printed_pages"] == [60]
        assert result["solution"]["evidence_ids"] == ["EV-MATH-000210", "EV-MATH-000211"]
        assert result["solution"]["printed_pages"] == [60, 61]
    assert any(h["token"] == "零点定理" for a in direct["content_analysis"] for h in a["anchor_hits"])


def test_c5_endpoint66_both_indexes_and_ask(monkeypatch, capsys):
    pair = next(p for p in book_series.load_exercise_pair_index()["items"]
                if p.get("book_id") == BOOK and p.get("exercise_label") == "例6"
                and p["question"]["printed_pages"] == [66])
    index = exercise_locator.load_exercise_locator_index()
    relation = next(r for r in index["relations"] if r.get("book_id") == BOOK
                    and r.get("exercise_label") == "例6" and r["question"]["printed_pages"] == [66])
    payload = c5_ask("66页例6", monkeypatch, capsys)
    assert payload["answer_mode"] == "exercise_pair"
    assert payload["answer_grounding"]["status"] == "exact_answer"
    for side in (pair["solution"], relation["answer"], payload["answer_grounding"]["solution"]):
        assert side["evidence_ids"] == ["EV-MATH-000214"]
        assert side["printed_pages"] == [66]
    assert pair["solution"]["content"] == relation["answer"]["content"]
    assert pair["solution"]["content"].endswith("· 66 ·")
    assert pair["solution"]["source_image_paths"] == [pair["solution"]["source_image_path"]]


@pytest.mark.parametrize("query", [
    "高数基础篇65页的例1，是不是直接分别写 x 趋于 1- 和 1+ 也可以？一定要写保号性吗？",
    "65页的例1：设f可导，lim f′(x)/sin³πx=−2，判断x=1是否为f的极值点",
])
def test_c5_original_and_v1_keep_exact(query, monkeypatch, capsys):
    payload = c5_ask(query, monkeypatch, capsys)
    assert payload["answer_mode"] == "exercise_pair"
    assert payload["teaching_bundle"]["status"] == "exact"
    for result in (c5_grounding(query), payload["answer_grounding"]):
        assert result["status"] == "exact_answer"
        for side in ("problem", "solution"):
            assert result[side]["evidence_ids"] == ["EV-MATH-000154"]
            assert result[side]["printed_pages"] == [65]


@pytest.mark.parametrize("phrase,fragment", [
    ("零点定理", "定理"), ("归纳方法", "方法"), ("严格定义", "定义"),
    ("推理结论", "结论"), ("充分条件", "条件"),
])
def test_c5_chinese_fragment_cannot_independently_support_identity(phrase, fragment):
    analysis = exercise_locator.analyze_exercise_content("例1：" + phrase, {"solution": {"content": fragment}})
    assert analysis["state"] == "has_anchor"
    assert [a["token"] for a in analysis["tokens_after_filter"]] == [phrase]
    assert not analysis["anchor_hits"]
    supported = exercise_locator.analyze_exercise_content("例1：" + phrase, {"solution": {"content": phrase}})
    assert [h["token"] for h in supported["anchor_hits"]] == [phrase]


def test_c5_long_phrase_stays_whole_and_search_tokenizer_keeps_default():
    from build_search_index import tokenize

    phrase = "拉格朗日中值定理"
    analysis = exercise_locator.analyze_exercise_content(phrase, {"solution": {"content": "中值定理"}})
    assert [a["token"] for a in analysis["tokens_after_filter"]] == [phrase]
    assert not analysis["anchor_hits"]
    assert "定理" in tokenize(phrase)
    assert phrase not in tokenize(phrase)
    assert tokenize("lim f", chinese_ngrams=False) == tokenize("lim f") == ["lim", "f"]


@pytest.mark.parametrize("route", ["grounding", "exercise_route"])
def test_c5_failure_code_survives_conflict_wording_change(chapter_index, monkeypatch, route):
    monkeypatch.setattr(book_series, "_filter_exercise_candidates",
                        lambda *args, **kwargs: ([], "请求与证据所属章不同（替换文案）", "content_conflict"))
    if route == "grounding":
        result = grounding("第一章题型七例1")
    else:
        result = book_series.resolve_exercise_route(query="第一章26页题型七例1", book_route={"series_id": "TANG"})
    assert result["failure_code"] == "content_conflict"
    assert result["failure_reason"] == "请求与证据所属章不同（替换文案）"


@pytest.mark.parametrize("container,match", [("题型七", True), ("题型八", False)])
def test_c5_container_match_compares_normalized_candidate(chapter_index, container, match):
    result = grounding(f"第一章{container}例1：求极限")
    assert (result["status"] == "exact_answer") == match
    if match:
        assert result["content_analysis"][0]["location_evidence"]["container_match"]
