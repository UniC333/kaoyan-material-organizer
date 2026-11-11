"""Overflow audit §7: synthetic boundaries and read-only private-data regression."""
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from common import kb_layout
from kaoyan_kb.domain import book_series as bs, exercise_locator as el


TITLE = "考研数学高等数学辅导讲义 基础篇"
BOOK = "TANG2027-M1-CALCULUS-BASIC"
LIVE = Path(__file__).resolve().parents[2] / ".kaoyan-kb"


@pytest.mark.parametrize("line", ["· 23 ·", "23", " 3 "])
def test_t1_footer_positive(line):
    assert el.is_overflow_footer_line(line)


@pytest.mark.parametrize("line", ["x=23", "$23$", "23.", "(2)", "· 23", "第23页"])
def test_t1_footer_negative(line):
    assert not el.is_overflow_footer_line(line)


@pytest.mark.parametrize("line", [
    "第三章 中值定理与一元函数微分学的应用", "考研数学高等数学辅导讲义·基础篇",
    "《考研数学高等数学辅导讲义 基础篇》", "理", "3", "自定义教材",
])
def test_t1_header_positive(line):
    assert el.is_overflow_header_line(line, "自定义教材" if line == "自定义教材" else TITLE)


@pytest.mark.parametrize("line", [
    "第三章 由$x=1$得结论。", "第三章 f(x)=1", "考研数学可以这样计算。",
    "理论", "x", "I", "=", "+", "$", "。", "²", "∞",
])
def test_t1_header_negative(line):
    assert not el.is_overflow_header_line(line, TITLE)


@pytest.mark.parametrize("line", [
    "# 划重点", "## 题型九 不等式证明", "### 三、无理函数的不定积分",
    "情形 2 $1^\\infty$ 型极限", "情形6、7 $0^0$ 型和 $\\infty^0$ 型极限",
    "题型二 分段函数的导数", "题理1 极值问题", "题码二 分段函数的导数",
    "思路分析", "思路分析：", "方法二", "（一）第一类换元积分法",
    "三、无理函数的不定积分", "四、泰勒公式的应用——洛必达法则", "划重点", "本章学习诊断",
])
def test_t1_structure_positive(line):
    assert el.is_overflow_structure_line(line)


@pytest.mark.parametrize("line", [
    "方法二 令$f(t)=t$，由拉格朗日中值定理得结论。", "方法二 因为函数连续。",
    "情形一: 存在两个零点", "情形二：若函数单调", "由零点定理可得。",
    "思路分析 令$f(x)=x$", "(1) 计算积分", "（2）证明结论", "=2.$$",
])
def test_t1_structure_negative(line):
    assert not el.is_overflow_structure_line(line)


def evidence(page, content):
    evidence_id = f"EV-SYN-{page}"
    span = {"source_id": "SRC-SYN", "file_id": evidence_id, "source_file_sha256": "test-sha",
            "locator": {"page_start": page, "page_end": page, "image_start": page, "image_end": page}}
    return {
        "evidence_id": evidence_id, "evidence_key": evidence_id, "source_id": "SRC-SYN",
        "chapter_id": "CH-SYN", "chunk_id": evidence_id, "content": content,
        "origin_type": "paper_book_reviewed_ocr", "review_status": "accepted",
        "verification_status": "source_grounded", "source_grounded": True,
        "source_spans": [span],
        "provenance": {"origin_type": "paper_book_reviewed_ocr", "verification_status": "source_grounded",
                       "source_grounded": True, "source_spans": [span]},
        "page_classification_refs": [{"book_id": "SYN", "book_title": TITLE, "chapter_id": "CH-SYN",
                                      "printed_page": page, "source_image_path": f"P{page}.jpg"}],
    }


@pytest.fixture(params=[(bs.worked_example_pairs_from_evidence, "solution", "pair_status", "exact_pair"),
                       (el.build_worked_example_relations, "answer", "relation_status", "exact")],
                ids=["pairs", "relations"])
def builder(request):
    return request.param


@pytest.mark.parametrize("overlap", [
    "第三章 中值定理与一元函数微分学的应用",  # p66 shape, before a Markdown boundary
    "考研数学高等数学辅导讲义·基础篇",
    "· 23 ·\n3\n考研数学高等数学辅导讲义·基础篇",
    "25\n理\n考研数学高等数学辅导讲义·基础篇",
    "第一章 函数、极限、连续\n情形 4 无穷减无穷型极限",
])
def test_builders_drop_only_empty_overflow_overlap(builder, overlap):
    build, side, status, exact = builder
    rows = build([evidence(10, "例1 求积分。\n解 答案为1。"),
                  evidence(11, overlap + "\n## 题型八 新单元\n例2 下一题。\n解 答案为2。")])
    row = next(x for x in rows if x["exercise_label"] == "例1")
    assert row[status] == exact
    assert row["question"]["evidence_ids"] == ["EV-SYN-10"]
    assert row[side]["evidence_ids"] == ["EV-SYN-10"]
    assert row[side]["printed_pages"] == [10]
    assert row[side]["source_image_paths"] == ["P10.jpg"]
    assert row[side]["source_image_path"] == "P10.jpg"
    assert row[side]["content"] == "解 答案为1。"


@pytest.mark.parametrize("continuation", ["故原结论成立。", "=2.$$", "(2) 由零点定理得结论。",
                                          "方法二 令$f(x)=x$，由此得结论。", "x"])
def test_builders_keep_short_and_substantive_continuations(builder, continuation):
    build, side, status, exact = builder
    prefix = "第三章 中值定理与一元函数微分学的应用\n"
    row = build([evidence(10, "例1 求积分。\n解 计算如下"),
                 evidence(11, prefix + continuation + "\n例2 下一题。")])[0]
    assert row[status] == exact
    assert row[side]["evidence_ids"] == ["EV-SYN-10", "EV-SYN-11"]
    assert row[side]["printed_pages"] == [10, 11]
    assert row[side]["content"] == "解 计算如下\n" + prefix + continuation


@pytest.mark.parametrize("numbered", ["2 设函数连续，则结论成立。", "3 证明此新命题。", "4 求积分的值。",
                                     "5 计算积分。", "6 讨论函数。", "7 已知函数连续。", "8 若函数连续。"])
def test_t2_new_knowledge_item_cuts_before_unrelated_proof_and_page(builder, numbered):
    build, side, status, exact = builder
    row = build([evidence(10, "例1 求积分。\n解 I=1/4。\n" + numbered + "\n证明 令 I=另一个积分，则"),
                 evidence(11, "第三章 中值定理与一元函数微分学的应用\n此为新命题的收尾。\n例2 下一题。")])[0]
    assert row[status] == exact  # Explicit p95 ruling: derive status from the truncated segment.
    assert row[side]["content"] == "解 I=1/4。"
    assert row[side]["evidence_ids"] == ["EV-SYN-10"]
    assert row[side]["printed_pages"] == [10]


def test_t2_parenthesized_subquestions_do_not_cut(builder):
    build, side, status, exact = builder
    first = "例1 求积分。\n解 (1) 计算得到1。\n(2) 设函数连续，则"
    last = "（3）由中值定理得到2。"
    row = build([evidence(10, first), evidence(11, last + "\n例2 下一题。")])[0]
    assert row[status] == exact
    assert row[side]["content"] == first.split("\n", 1)[1] + "\n" + last
    assert row[side]["printed_pages"] == [10, 11]


def test_t2_structural_boundary_requires_a_new_unit_introduction(builder):
    build, side, _, _ = builder
    first = "例1 求积分。\n解 已得到结论。\n题型二 积分计算\n思路分析\n这是新单元。"
    row = build([evidence(10, first), evidence(11, "新单元续写。\n例2 下一题。")])[0]
    assert row[side]["content"] == "解 已得到结论。"
    assert row[side]["printed_pages"] == [10]


@pytest.mark.parametrize("overflow", [False, True])
def test_t3_trims_terminal_title_only_with_retained_overflow(builder, overflow):
    build, side, _, _ = builder
    content = "例1 求积分。\n解 (1) 算式未完"
    tail = "(2) 答案为2。\n情形 2 下一种极限"
    evs = ([evidence(10, content), evidence(11, tail + "\n例2 下一题。")]
           if overflow else [evidence(10, content + "\n" + tail + "\n例2 下一题。")])
    row = build(evs)[0]
    expected = "解 (1) 算式未完\n(2) 答案为2。" + ("" if overflow else "\n情形 2 下一种极限")
    assert row[side]["content"] == expected
    assert row[side]["printed_pages"] == ([10, 11] if overflow else [10])


# Audit §3: fixed independent expectations, never inferred from the rebuilt output.
# (question page, label) -> the one blindly included evidence to remove.
REMOVED = {
    (2, "例2"): "EV-MATH-000168", (8, "例（P8页内3）"): "EV-MATH-000172",
    (13, "例9"): "EV-MATH-000148", (22, "例3"): "EV-MATH-000182",
    (23, "例6"): "EV-MATH-000183", (25, "例2"): "EV-MATH-000184",
    (38, "例10"): "EV-MATH-000198", (39, "例3"): "EV-MATH-000199",
    (54, "例2"): "EV-MATH-000205", (63, "例1"): "EV-MATH-000153",
    (67, "例3"): "EV-MATH-000216", (68, "例5"): "EV-MATH-000217",
    (70, "例6"): "EV-MATH-000219", (75, "例2"): "EV-MATH-000157",
    (82, "例3"): "EV-MATH-000562", (83, "例1"): "EV-MATH-000563",
    (95, "例7"): "EV-MATH-000575", (102, "例3"): "EV-MATH-000582",
    (105, "例3"): "EV-MATH-000585", (107, "例5"): "EV-MATH-000587",
    (108, "例8"): "EV-MATH-000158",
}
TRIMMED = {(20, "例1"): r"情形 2 $1^\infty$ 型极限",
           (24, "例10"): r"情形6、7 $0^0$ 型和 $\infty^0$ 型极限",
           (60, "例2"): "情形 2 结论中关于两个中值的项一个简单、一个复杂"}
TRUE_CONTINUATIONS = {
    (6, "例1"), (17, "例2"), (20, "例1"), (21, "例2"), (24, "例10"), (26, "例2"), (27, "例5"),
    (36, "例1"), (37, "例6"), (40, "例3"), (47, "例4"), (60, "例2"), (61, "例4"), (62, "例4"),
    (64, "例3"), (69, "例3"), (71, "例3"), (77, "例4"), (81, "例2"), (84, "例3"),
    (99, "例1"), (104, "例6"),
}


@pytest.fixture(scope="module")
def live_rebuild():
    if not (LIVE / "indexes/exercise_pair_index.json").exists():
        pytest.skip("private .kaoyan-kb is not distributed with the repository")
    # Both public build entrypoints run, with every persistence entrypoint suppressed.
    with ExitStack() as stack:
        stack.enter_context(patch.dict("os.environ", {"KAOYAN_KB_ROOT": str(LIVE)}))
        for module in (bs, el):
            stack.enter_context(patch.object(module, "ensure_kb_layout", lambda: kb_layout(root=LIVE)))
            stack.enter_context(patch.object(module, "save_json", lambda *_args: None))
        return bs.build_exercise_pair_index(), el.build_exercise_locator_index()


@pytest.mark.parametrize("index,collection,key,side,status,exact,count,which", [
    ("exercise_pair_index", "items", "exercise_key", "solution", "pair_status", "exact_pair", 539, 0),
    ("exercise_locator_index", "relations", "relation_id", "answer", "relation_status", "exact", 702, 1),
])
def test_real_data_exact_change_set_and_p95_status_ruling(live_rebuild, index, collection, key, side,
                                                        status, exact, count, which):
    old = json.loads((LIVE / f"indexes/{index}.json").read_text(encoding="utf-8"))[collection]
    new = live_rebuild[which][collection]
    before, after = ({x[key]: x for x in rows} for rows in (old, new))
    assert len(old) == len(new) == count
    assert before.keys() == after.keys()
    changed = {k for k in before if before[k] != after[k]}
    targets = {k: x for k, x in before.items() if x.get("book_id") == BOOK}
    assert len(targets) == 182
    coordinates = {k: (min(x["question"]["printed_pages"]), x["exercise_label"]) for k, x in targets.items()}
    expected_keys = {k for k, coord in coordinates.items() if coord in REMOVED or coord in TRIMMED}
    assert len(REMOVED) == 21 and len(TRIMMED) == 3
    assert len(expected_keys) == 24
    assert changed == expected_keys
    for k in changed:
        a, b, coord = before[k], after[k], coordinates[k]
        assert a["question"] == b["question"]
        allowed = {side} | ({status} if coord == (95, "例7") else set())
        assert {field for field in a.keys() | b.keys() if a.get(field) != b.get(field)} == allowed
        if coord in TRIMMED:
            assert a[side]["content"] == b[side]["content"] + "\n" + TRIMMED[coord]
            assert {**a[side], "content": b[side]["content"]} == b[side]
        else:
            removed = REMOVED[coord]
            assert [v for v in a[side]["evidence_ids"] if v not in b[side]["evidence_ids"]] == [removed]
            assert b[side]["evidence_ids"] == [v for v in a[side]["evidence_ids"] if v != removed]
            assert b[side]["printed_pages"] == [coord[0]]
            assert b[side]["source_image_paths"] == a[side]["source_image_paths"][:1]
            assert b[side]["source_image_path"] == a[side]["source_image_path"]
            assert {field for field in a[side].keys() | b[side].keys() if a[side].get(field) != b[side].get(field)} == {
                "content", "evidence_ids", "printed_pages", "source_image_paths"}
            assert a[side]["content"].startswith(b[side]["content"])
        if coord == (95, "例7"):
            assert a[status] == "needs_review" and b[status] == exact
            for value in (b["question"], b[side]):
                assert value["evidence_ids"] == ["EV-MATH-000574"]
                assert value["printed_pages"] == [95]
            assert "2 设函数" not in b[side]["content"]
    # p26 contains two different 例2 containers; the audit selects the cross-page one.
    true_keys = {k for k, coord in coordinates.items() if coord in TRUE_CONTINUATIONS
                 and max(before[k][side].get("printed_pages") or [0])
                 > max(before[k]["question"]["printed_pages"])}
    assert len(true_keys) == 22
    assert {coordinates[k] for k in true_keys} == TRUE_CONTINUATIONS
    for k in true_keys:
        assert after[k][side]["evidence_ids"] == before[k][side]["evidence_ids"]
        assert after[k][side]["printed_pages"] == before[k][side]["printed_pages"]
        if coordinates[k] not in TRIMMED:
            assert after[k] == before[k]  # All other 19, including p26 formula fragment.
    p66 = [k for k, coord in coordinates.items() if coord == (66, "例6")]
    assert len(p66) == 1
    assert after[p66[0]] == before[p66[0]]
    assert after[p66[0]][side]["evidence_ids"] == ["EV-MATH-000214"]
    assert after[p66[0]][side]["printed_pages"] == [66]
    assert all(after[k] == v for k, v in before.items() if v.get("book_id") != BOOK)
