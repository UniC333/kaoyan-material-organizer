"""最小第一步"只给一级提示"的测试。

覆盖两个开放点的最终定义（定义落在测试里，不只写在注释里）：
- "一级提示"的精确边界：只给方向/切入点，不给可直接得出结论的中间结果。
  定义文本在 teach.py 的 hint 规则 3 里，本文件逐字断言其存在，且 hint
  模式 prompt 不含 full 模式的"先给出已确认的原书答案"。
- 白名单每条短语的触发边界：五条短语逐项核对；"只提示"单独出现仅触发澄清；
  题干内"提示："不触发；长短语优先于其子串短语。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from kaoyan_kb.domain.query import request

BOOK = "考研数学高等数学辅导讲义 基础篇"
P65 = "高数基础篇65页例1"


def _resolve(query, **kwargs):
    return request.resolve_request(
        query=query,
        book_title=kwargs.get("book_title", BOOK),
        chapter=kwargs.get("chapter"),
        printed_page=kwargs.get("printed_page", 65),
        exercise_label=kwargs.get("exercise_label"),
    )


# ---------------------------------------------------------------- 白名单剥离

@pytest.mark.parametrize("phrase", [
    "只提示下一步",
    "先别给答案",
    "别给答案",
    "只给提示",
    "提示一下",
])
def test_each_whitelisted_phrase_triggers_hint(phrase):
    action, fragments, problem = request.split_teaching_requirement(P65 + "，" + phrase)
    assert action == "hint"
    assert [f["phrase"] for f in fragments] == [phrase]
    assert problem == P65


def test_two_phrases_combined_strip_both():
    action, fragments, problem = request.split_teaching_requirement(
        P65 + "，先别给答案，只提示下一步")
    assert action == "hint"
    assert [f["phrase"] for f in fragments] == ["先别给答案", "只提示下一步"]
    assert problem == P65


def test_longer_phrase_wins_over_its_substring():
    # "先别给答案"包含"别给答案"：按长度降序匹配，只能留一条长片段。
    _, fragments, _ = request.split_teaching_requirement(P65 + "，先别给答案")
    assert [f["phrase"] for f in fragments] == ["先别给答案"]


@pytest.mark.parametrize("query", [
    P65 + "，只提示",          # "只提示"单独出现不在白名单内（可能为题干内容）
    P65 + "，提示：先化简",    # 题干内"提示："不触发
    P65,                     # 无教学要求
])
def test_non_whitelisted_forms_do_not_trigger(query):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == ("clarify" if "只提示" in query else "")
    assert fragments == []
    assert problem == query


def test_math_conditions_survive_stripping():
    action, _, problem = request.split_teaching_requirement(
        P65 + "，lim f'(x)/sin^3πx=-2，先别给答案")
    assert action == "hint"
    assert problem == P65 + "，lim f'(x)/sin^3πx=-2"


def test_fragment_offsets_point_at_original_text():
    query = P65 + "，先别给答案，只提示下一步"
    _, fragments, _ = request.split_teaching_requirement(query)
    for fragment in fragments:
        assert query[fragment["start"]:fragment["end"]] == fragment["phrase"]
        assert query[fragment["removed_start"]:fragment["removed_end"]].startswith(
            fragment["phrase"]) or fragment["phrase"] in query[
                fragment["removed_start"]:fragment["removed_end"]]


# ---------------------------------------------------------------- 请求解析接线

def test_resolve_request_hint_keeps_exercise_identity():
    resolved = _resolve(P65 + "，先别给答案，只提示下一步")
    assert resolved["teaching_action"] == "hint"
    assert resolved["problem_query"] == P65
    assert resolved["original_query"] == P65 + "，先别给答案，只提示下一步"
    assert resolved["source_request_kind"] == "exercise"
    assert resolved["page"]["number"] == 65
    assert resolved["exercise_scope"]["exercise_label"] == "例1"
    assert resolved["exercise_scope"]["printed_page"] == 65


def test_resolve_request_plain_has_no_teaching_action():
    resolved = _resolve(P65)
    assert resolved["teaching_action"] == ""
    assert resolved["teaching_fragments"] == []
    assert resolved["problem_query"] == P65


def test_resolve_request_guessed_container_still_hint_but_problem_kept():
    resolved = _resolve("第三章题型七例1，先别给答案，只提示下一步")
    assert resolved["teaching_action"] == "hint"
    assert resolved["problem_query"] == "第三章题型七例1"


# ---------------------------------------------------------------- teach.py 纯函数

def _load_teach():
    teach_dir = Path(__file__).resolve().parents[2] / "gpt-teach"
    sys.path.insert(0, str(teach_dir))
    spec = importlib.util.spec_from_file_location("teach_minstep_under_test", teach_dir / "teach.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


teach = _load_teach()


def _fake_view(teaching_action="hint"):
    return {
        "question": P65 + "，先别给答案，只提示下一步",
        "subject": "数学",
        "book_resolution": {"book_title": BOOK, "status": "explicit"},
        "answer_mode": "exercise_pair",
        "page_verification": {"textbook_explanation_allowed": True, "summary": "已核验"},
        "answer_grounding": {"status": "exact_answer"},
        "generic_answer_bundle": {
            "status": "available",
            "conclusion": "极值点结论：x=1 是极小值点",
            "explanation": "因为导数变号",
            "citations": [{"evidence_id": "EV-MATH-000154"}],
            "failure_reason": "",
            "next_action": "",
        },
        "teaching_bundle": {
            "status": "exact",
            "problem_text": "例1 设函数 f(x) 可导……判断 x=1 是否是极值点？",
            "source_answer_text": "x=1 是 f(x) 的极小值点。",
            "citations": {"problem_printed_pages": [65], "solution_printed_pages": [65]},
        },
        "page_content_bundle": {"status": "available", "content": "页面正文含完整题解"},
        "request_resolution": {
            "teaching_action": teaching_action,
            "problem_query": P65,
            "original_query": P65 + "，先别给答案，只提示下一步",
        },
    }


def test_decide_teaching_action_hint_and_full():
    assert teach.decide_teaching_action(_fake_view("hint")) == ("hint", "")
    assert teach.decide_teaching_action(_fake_view("")) == ("full", "")


def test_verify_placeholder_is_internal_only_natural_signal_clarifies(capsys):
    action, reason = teach.decide_teaching_action(_fake_view("verify"))
    assert action == "full"
    assert "尚未实现" in reason
    assert "verify" in capsys.readouterr().err
    assert request.split_teaching_requirement("检查这一步")[0] == "clarify"


def test_evidence_packet_hint_hides_answer_and_generic_conclusion():
    packet = teach.build_evidence_packet(_fake_view("hint"), mode="hint")
    assert "问题：" + P65 in packet
    assert "教学模式：hint（一级提示）" in packet
    assert "题干（教材原文）：例1 设函数 f(x) 可导" in packet
    assert "原书答案" not in packet
    assert "x=1 是 f(x) 的极小值点" not in packet
    assert "本地结论" not in packet
    assert "本地解释" not in packet
    assert "页面正文" not in packet


def test_evidence_packet_full_keeps_answer_and_generic_conclusion():
    packet = teach.build_evidence_packet(_fake_view(""), mode="full")
    assert "教学模式：full（完整讲解）" in packet
    assert "原书答案（教材原文）：x=1 是 f(x) 的极小值点" in packet
    assert "本地结论：极值点结论" in packet


HINT_BOUNDARY = (
    "一级提示的边界：只给方向或切入点（例如应使用的方法或定理、应先处理的部分），"
    "不给可直接得出结论的中间结果（如代入后可直接算出的表达式、化简后的关键等式）。"
)


def _args():
    args = types.SimpleNamespace(
        subject="数学",
        question=P65 + "，先别给答案，只提示下一步",
        # 带 progress 时 prompt_context 会列出"本次题目"，用它验证 display_question。
        progress_context={"verified": [], "note": "讲义 ch3 已看完"},
    )
    return args


def test_prompt_hint_has_boundary_and_no_full_answer_rule():
    prompt = teach.build_prompt(_args(), "PACKET", None, mode="hint", question=P65)
    assert "只给一级提示：指出下一步的方向或关键切入点" in prompt
    assert HINT_BOUNDARY in prompt
    assert "先给提示，再请学生尝试" in prompt
    assert "先给出已确认的原书答案" not in prompt
    assert "先给结论，再讲关键思路" not in prompt
    assert "问题：" + P65 in prompt


def test_prompt_full_keeps_original_rules():
    prompt = teach.build_prompt(_args(), "PACKET", None, mode="full")
    assert "先给出已确认的原书答案/结论，再补充思路讲解" in prompt
    assert "先给结论，再讲关键思路" in prompt
    assert "只给一级提示" not in prompt
    assert HINT_BOUNDARY not in prompt
    # B2/G4（2026-11-10，a1）：原有 full 顺序保留，且 full rule3 新增来源角色要求。
    assert "补充讲解按来源角色分段" in prompt
    assert "通用讲解（非教材原文）" in prompt
    assert "本题推导" in prompt
    assert prompt.index("先给出已确认的原书答案/结论") < prompt.index("先给结论，再讲关键思路")


@pytest.mark.parametrize("query, expected", [
    (P65 + "，f(x)=a，只给提示，b>0", P65 + "，f(x)=a，b>0"),
    (P65 + "，f(x)=a，先别给答案，b>0，只提示下一步", P65 + "，f(x)=a，b>0"),
    (P65 + "，只给提示，f(x)=a，b>0", P65 + "，f(x)=a，b>0"),
])
def test_middle_teaching_fragments_preserve_math_separation(query, expected):
    action, _, problem = request.split_teaching_requirement(query)
    assert action == "hint"
    assert problem == expected


@pytest.mark.parametrize("query", [
    P65 + "，请不要只给提示，直接给完整答案",
    P65 + "，不要只给提示",
    P65 + "，别只给提示，直接讲答案",
    "不要只给提示",
    P65 + "，请别只给提示",
    "我不要只给提示",
    "请你不要只给提示，直接给完整答案",
    "这次不要只给提示",
    "先不要只给提示",
    "能不能不要只给提示",
    "千万别只给提示",
    P65 + "，我不要只给提示，直接给完整答案",
])
def test_negated_teaching_requirement_strips_fragments_and_requests_full(query):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "full"
    assert fragments
    assert problem == (P65 if query.startswith(P65) else "")


def test_resolve_request_negated_hint_strips_fragments_and_requests_full():
    query = P65 + "，请不要只给提示，直接给完整答案"
    resolved = _resolve(query, book_title=BOOK, printed_page=65)
    assert resolved["teaching_action"] == "full"
    assert resolved["teaching_fragments"]
    assert resolved["problem_query"] == P65
    assert resolved["original_query"] == query


def test_resolve_request_negated_hint_after_ordinary_word_strips_fragments_and_requests_full():
    query = P65 + "，我不要只给提示，直接给完整答案"
    resolved = _resolve(query, book_title=BOOK, printed_page=65)
    assert resolved["teaching_action"] == "full"
    assert resolved["problem_query"] == P65
    assert resolved["original_query"] == query


@pytest.mark.parametrize("query, expected", [
    (P65 + "，个别只给提示", P65 + "，个别"),
    (P65 + "，区别只给提示", P65 + "，区别"),
    ("要不要只给提示", "要不要"),
])
def test_compound_negation_suffix_still_triggers_hint(query, expected):
    action, _, problem = request.split_teaching_requirement(query)
    assert action == "hint"
    assert problem == expected


def test_internal_bie_in_whitelisted_phrase_still_triggers_hint():
    action, fragments, problem = request.split_teaching_requirement(
        P65 + "，先别给答案，只提示下一步")
    assert action == "hint"
    assert [fragment["phrase"] for fragment in fragments] == ["先别给答案", "只提示下一步"]
    assert problem == P65


def test_bie_inside_another_word_is_not_a_negation_prefix():
    action, _, problem = request.split_teaching_requirement(P65 + "，特别只给提示")
    assert action == "hint"
    assert problem == P65


@pytest.mark.parametrize("placeholder", ["accept", "locate"])
def test_internal_placeholders_do_not_claim_natural_acceptance_support(placeholder, capsys):
    action, reason = teach.decide_teaching_action(_fake_view(placeholder))
    assert action == "full"
    assert "尚未实现" in reason
    assert placeholder in capsys.readouterr().err
    assert request.split_teaching_requirement("闭卷验收")[0] == "clarify"


@pytest.mark.parametrize("query, expected", [
    ("A；只给提示，B", "A；B"),
    ("A 只给提示：B", "A 只给提示：B"),
    ("A只给提示B", "A只给提示B"),
    ("A，只给提示，先别给答案；B", "A，B"),
    ("只给提示，" + P65, P65),
])
def test_teaching_fragment_separator_selection_and_leading_cleanup(query, expected):
    action, _, problem = request.split_teaching_requirement(query)
    assert action == ("clarify" if query in ("A 只给提示：B", "A只给提示B") else "hint")
    assert problem == expected


def test_fragment_removed_offsets_match_exact_original_slices():
    query = P65 + "，先别给答案，只提示下一步"
    _, fragments, _ = request.split_teaching_requirement(query)
    # Preserve the original overlapping separator spans confirmed before editing.
    assert [query[fragment["removed_start"]:fragment["removed_end"]]
            for fragment in fragments] == ["，先别给答案，", "，只提示下一步"]


@pytest.mark.parametrize("query", [
    "不要再只给提示",
    "别再只给提示",
    "千万别再只给提示",
    "不用只给提示",
    "不必只给提示",
    "勿只给提示",
    "莫只给提示",
    "请勿只给提示",
    "切勿只给提示",
    "切莫只给提示",
    "不要给我只给提示",
    "请不要给我只给提示",
    "不要啊只给提示",
    P65 + "，不用只给提示，直接给完整答案",
    "先别给答案，不要再只给提示",
])
def test_window_negated_teaching_requirement_strips_fragments_and_requests_full(query):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "full"
    assert fragments
    assert problem == (P65 if query.startswith(P65) else "")


def test_resolve_request_unused_hint_preserves_original_query():
    query = P65 + "，不用只给提示，直接给完整答案"
    resolved = _resolve(query)
    assert resolved["teaching_action"] == "full"
    assert resolved["teaching_fragments"]
    assert resolved["problem_query"] == P65
    assert resolved["original_query"] == query


@pytest.mark.parametrize("query, expected", [
    ("分别只给提示", ""),
    ("临别只给提示", "临别"),
    ("判别只给提示", "判别"),
    ("甄别只给提示", "甄别"),
    ("辨别只给提示", "辨别"),
    ("这两题分别只给提示，别给答案", "这两题"),
])
def test_window_compound_negation_suffix_still_triggers_hint(query, expected):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "hint"
    assert fragments
    assert problem == expected


@pytest.mark.parametrize("query, expected", [
    ("这题不要用洛必达，只给提示", "这题不要用洛必达"),
])
def test_negation_outside_narrow_window_still_triggers_hint(query, expected):
    action, _, problem = request.split_teaching_requirement(query)
    assert action == "hint"
    assert problem == expected


@pytest.mark.parametrize("query", [
    "不要再给我啊只给提示",
    "请勿再给我啊只给提示",
])
def test_overlong_filler_run_requires_clarification(query):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "clarify"
    assert fragments == []
    assert problem == query


CLARIFY_QUERY = P65 + "，珂别只给提示"


def test_ambiguous_negation_requires_clarification_and_preserves_clause():
    query = CLARIFY_QUERY
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "clarify"
    assert problem == query
    assert fragments == []


def test_resolve_request_ambiguous_negation_preserves_original_query():
    resolved = _resolve(CLARIFY_QUERY)
    assert resolved["teaching_action"] == "clarify"
    assert resolved["teaching_fragments"] == []
    assert resolved["problem_query"] == CLARIFY_QUERY
    assert resolved["original_query"] == CLARIFY_QUERY


@pytest.mark.parametrize("query", [
    "珂别只给提示，不要再只给提示",
    "不要再只给提示，珂别只给提示",
])
def test_unknown_clause_takes_priority_over_known_negation(query):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "clarify"
    assert len(fragments) == 1
    assert problem == "珂别只给提示"


def test_ambiguous_negation_takes_priority_over_positive_matches():
    query = "先别给答案，珂别只给提示"
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "clarify"
    assert len(fragments) == 1
    assert problem == "珂别只给提示"


def test_decide_teaching_action_clarify_logs_reason(capsys):
    action, reason = teach.decide_teaching_action(_fake_view("clarify"))
    assert action == "clarify"
    assert reason
    stderr = capsys.readouterr().err
    assert "[INFO]" in stderr
    assert reason in stderr


def test_evidence_packet_clarify_hides_all_answer_projections():
    view = _fake_view("clarify")
    view["question"] = CLARIFY_QUERY
    view["request_resolution"]["problem_query"] = P65
    view["request_resolution"]["original_query"] = CLARIFY_QUERY
    packet = teach.build_evidence_packet(view, mode="clarify")
    assert "问题：" + P65 in packet
    assert "教学模式：clarify（待澄清）" in packet
    assert "题干（教材原文）：例1 设函数 f(x) 可导" in packet
    assert "印刷页码：[65, 65]" in packet
    for hidden in ("原书答案", "x=1 是 f(x) 的极小值点", "本地结论", "本地解释",
                   "因为导数变号", "EV-MATH-000154", "页面正文"):
        assert hidden not in packet


def test_prompt_clarify_asks_before_revealing_answer():
    prompt = teach.build_prompt(_args(), "PACKET", None, mode="clarify", question=CLARIFY_QUERY)
    assert prompt == teach.CLARIFY_TEXT
    for forbidden in ("PACKET", "教材原文证据暂不可用", "照片", "证据来源", "讲解规则"):
        assert forbidden not in prompt


@pytest.mark.parametrize("query, expected", [
    ("别老是只给提示", ""),
    ("不要总是只给提示", ""),
    ("别一直只给提示", ""),
    ("不要老只给提示", ""),
    ("别总只给提示", ""),
    ("不要总只给提示", ""),
    ("别老只给提示", ""),
    ("不要一直只给提示", ""),
    (P65 + "，别老是只给提示", P65),
])
def test_adverb_filler_negation_strips_fragments_and_requests_full(query, expected):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "full"
    assert fragments
    assert problem == expected


@pytest.mark.parametrize("phrase", [
    "直接给完整答案", "给完整答案", "直接讲答案", "直接给答案",
])
def test_each_full_phrase_strips_and_requests_full(phrase):
    action, fragments, problem = request.split_teaching_requirement(P65 + "，" + phrase)
    assert action == "full"
    assert [fragment["phrase"] for fragment in fragments] == [phrase]
    assert problem == P65


def test_longer_full_phrase_wins_over_its_substring():
    _, fragments, _ = request.split_teaching_requirement(P65 + "，直接给完整答案")
    assert [fragment["phrase"] for fragment in fragments] == ["直接给完整答案"]


def test_short_answer_phrase_does_not_trigger():
    query = P65 + "，给答案"
    assert request.split_teaching_requirement(query) == ("clarify", [], query)


@pytest.mark.parametrize("query, expected", [
    (P65 + "，不要直接给完整答案，只给提示", P65),
    ("不要直接给完整答案", ""),
    (P65 + "，别直接讲答案，只给提示", P65),
])
def test_negated_full_phrase_requests_hint(query, expected):
    action, fragments, problem = request.split_teaching_requirement(query)
    assert action == "hint"
    assert fragments
    assert problem == expected


@pytest.mark.parametrize("modifier", ["特别", "老师", "请", "麻烦", "拜托", "分别"])
def test_teaching_modifier_strips_with_hint(modifier):
    action, fragments, problem = request.split_teaching_requirement(P65 + "，" + modifier + "只给提示")
    assert action == "hint"
    assert [fragment["phrase"] for fragment in fragments] == ["只给提示"]
    assert problem == P65


@pytest.mark.parametrize("query, expected", [
    ("不要分别只给提示", "full"),
    ("不要分别直接给完整答案，只给提示", "hint"),
])
def test_separately_negated_actions_have_correct_polarity(query, expected):
    assert request.split_teaching_requirement(query)[0::2] == (expected, "")


@pytest.mark.parametrize("query, problem", [
    (P65 + "，请不要 ， 只给提示", P65 + "，请不要 "),
    (P65 + "，先别给答案，请不要只给提示", P65),
    ("不要再给我只给提示", "不要再给我只给提示"),
    ("不要 再，给 我，只给提示", "不要 再，给 我"),
])
def test_unapproved_combinations_clarify_without_deleting_unknown_content(query, problem):
    action, _, actual = request.split_teaching_requirement(query)
    assert action == "clarify"
    assert actual == problem


# ---------------------------------------------------------------- B2/G4 full 来源角色标注（2026-11-10）
# 唯一推荐 a1：只强化 build_prompt() 的 full 分支 rule3。以下断言只针对
# full 分支字符串与 hint 隔离；不能证明 GPT 实际正确分类，真实输出逐句表
# 另见 b2g4-fix/ 验收产物。

FULL_GENERIC_MARK = "通用讲解（非教材原文）"


def _full_prompt():
    return teach.build_prompt(_args(), "PACKET", None, mode="full")


def test_full_prompt_requires_source_roles_even_when_evidence_is_exact():
    prompt = _full_prompt()
    assert "先给出已确认的原书答案/结论，再补充思路讲解" in prompt
    assert FULL_GENERIC_MARK in prompt
    assert "每项实质内容" in prompt
    assert "明确且唯一的来源标记作用域" in prompt
    assert "开头摘要、列表项、独立公式、证明单元、末尾总结都算" in prompt
    # 角色要求不以证据不可用为前提：规则 2 的 blocked 分支之外，full rule3 自带。
    assert "无可用教材证据时优先遵守规则 2" in prompt


def test_full_prompt_separates_generic_and_problem_derivation():
    prompt = _full_prompt()
    assert "基于题干的独立推导用“本题推导”明确标出" in prompt
    assert "通用内容用完整标记“通用讲解（非教材原文）”标出" in prompt
    assert "一个段落、列表项或证明单元只归属一种来源角色，混合角色的内容必须拆开写" in prompt
    assert "不得把通用定理陈述藏进本题推导，也不得把本题推导全部标为通用" in prompt
    assert "补充思路不得笼统归为通用，也不得继承前面教材内容的身份" in prompt


def test_full_prompt_limits_textbook_citation_scope():
    prompt = _full_prompt()
    assert "不得附教材页码、题号或原文引用" in prompt
    assert "最后的来源列表只支持明确标识的教材内容，不覆盖通用段或生成的独立推导" in prompt
    assert "已确认的教材内容只写证据包中明确标识的内容" in prompt
    assert "exact 状态不等于整份输出都是教材内容" in prompt


def test_full_prompt_preserves_unavailable_evidence_rules():
    prompt = _full_prompt()
    # 规则 2（证据不可用先明说、不得编造）仍在 full prompt 内，新 rule3 不覆盖它。
    assert "教材原文证据暂不可用" in prompt
    assert "不得为填充教材小节而编造教材内容" in prompt
    assert "没有某类内容时不强制填充该类小节" in prompt
    # full 仍先给已确认答案、再补充思路（顺序保留）。
    assert prompt.index("先给出已确认的原书答案/结论") < prompt.index("补充讲解按来源角色分段")


# 修前冻结的 hint prompt（2026-11-10 修前实测，与修后逐字节比较）：
# 输入为 _args() + PACKET + mode="hint" + question=P65；修前模块与修后模块
# 生成结果一致，另见 b2g4-fix/pre-change-dryrun/ 两份 hint 干跑逐字节一致。
HINT_PROMPT_BEFORE_B2G4_FIX = '你是考研指导老师，学科：数学。下面是本地考研知识库对用户问题的检索结果（含证据门禁状态），请基于它为用户讲解。\n\n【本次题目与显式教材上下文】\n问题：高数基础篇65页例1\n学科：数学\n\n【学生进度背景】\n以下是学习状态记录，不是教材原文，不得作为引用、页码或教材结论的依据。\n{"verified": [], "note": "讲义 ch3 已看完"}\n仅用于调整讲解起点和待验证步骤，不改变证据门禁；即使记录准确或标为已验证，也不能据此生成教材原文或页码引用。\n\n【本地证据包】\nPACKET\n\n【讲解规则】\n1. 证据门禁优先：只有证据包中明确标记为已确认（exact/已确认）且带引用的内容，才能当作教材原文来讲；讲解时保留书中变量名、符号顺序和结构条件，不要混入其他解法。\n2. 如果证据包显示 blocked / asset_only / unconfirmed（证据不可用），必须先明确告诉用户“教材原文证据暂不可用”并给出建议下一步；之后可以给一份明确标注为“通用讲解（非教材原文）”的独立讲解，不得把通用讲解冒充成教材内容，不得编造页码、题号或原文引用。\n3. 只给一级提示：指出下一步的方向或关键切入点，不给最终结论、数值与完整证明链；提示后请学生先自己尝试。推导与原书冲突时停止判断，请用户核对答案原图。\n一级提示的边界：只给方向或切入点（例如应使用的方法或定理、应先处理的部分），不给可直接得出结论的中间结果（如代入后可直接算出的表达式、化简后的关键等式）。\n4. 数学公式排版规范（必须严格遵守，否则公式无法渲染）：\n- 行内公式用 $…$，复杂公式用 $$…$$ 独立成行；\n- 可用 \\begin{cases} 等环境，但 cases 内换行只用普通 \\\\，禁用 \\\\[4pt] 这类带间距的换行；\n- 指数一律写成 2^{1/x} 这样的行内除法形式，禁用嵌套 \\frac、\\dfrac；\n- 禁用 \\large \\Large \\huge 等一切字号命令，保持默认字号；\n- 不使用 HTML 公式卡片。\n5. 用中文回答，结构清晰：先给提示，再请学生尝试；最后列出证据来源（书名、页码、题号）。\n6. 直接输出讲解正文。不要读写任何文件，不要执行任何命令，不要提及工具或实现细节。\n'


def test_hint_prompt_is_unchanged_by_full_source_rules():
    prompt = teach.build_prompt(_args(), "PACKET", None, mode="hint", question=P65)
    assert prompt == HINT_PROMPT_BEFORE_B2G4_FIX
    assert "本题推导" not in prompt
    assert "补充讲解按来源角色分段" not in prompt
    assert "只归属一种来源角色" not in prompt
