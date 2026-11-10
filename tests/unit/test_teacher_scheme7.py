"""Scheme 7 approved corpus and structural/consumer acceptance checks."""
from copy import deepcopy
import json
import types

import pytest
from kaoyan_kb.domain.query import request, teaching_intent
from test_teacher_minstep import teach, _fake_view, P65, BOOK

# Literal expectations transcribed from evaluation §3 + approved design §4.1.
CORPUS = [('R1-01',
  '高数基础篇65页例1，f(x)=a，只给提示，b>0',
  'hint',
  [{'phrase': '只给提示', 'start': 18, 'end': 22, 'action': 'hint', 'removed_start': 17, 'removed_end': 23}],
  '高数基础篇65页例1，f(x)=a，b>0'),
 ('R1-02',
  '高数基础篇65页例1，请不要只给提示，直接给完整答案',
  'full',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 10, 'removed_end': 19},
   {'phrase': '直接给完整答案', 'start': 19, 'end': 26, 'action': 'full', 'removed_start': 18, 'removed_end': 26}],
  '高数基础篇65页例1'),
 ('R1-03',
  '不要只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R2-01',
  '我不要只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R2-02',
  '请你不要只给提示，直接给完整答案',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 9},
   {'phrase': '直接给完整答案', 'start': 9, 'end': 16, 'action': 'full', 'removed_start': 8, 'removed_end': 16}],
  ''),
 ('R2-03',
  '这次不要只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R2-04',
  '先不要只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R2-05',
  '能不能不要只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 5, 'end': 9, 'action': 'hint', 'removed_start': 0, 'removed_end': 9}],
  ''),
 ('R2-06',
  '千万别只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R2-07',
  '高数基础篇65页例1，我不要只给提示，直接给完整答案',
  'full',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 10, 'removed_end': 19},
   {'phrase': '直接给完整答案', 'start': 19, 'end': 26, 'action': 'full', 'removed_start': 18, 'removed_end': 26}],
  '高数基础篇65页例1'),
 ('R3-01',
  '不要再只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R3-02',
  '别再只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-03',
  '千万别再只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R3-04',
  '不用只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-05',
  '不必只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-06',
  '勿只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 1, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 5}],
  ''),
 ('R3-07',
  '莫只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 1, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 5}],
  ''),
 ('R3-08',
  '请勿只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-09',
  '切勿只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-10',
  '不要给我只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R3-11',
  '请不要给我只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 5, 'end': 9, 'action': 'hint', 'removed_start': 0, 'removed_end': 9}],
  ''),
 ('R3-12',
  '不要啊只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R3-13',
  '先别给答案，不要再只给提示',
  'full',
  [{'phrase': '先别给答案', 'start': 0, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 6},
   {'phrase': '只给提示', 'start': 9, 'end': 13, 'action': 'hint', 'removed_start': 5, 'removed_end': 13}],
  ''),
 ('R3-14',
  '高数基础篇65页例1，不用只给提示，直接给完整答案',
  'full',
  [{'phrase': '只给提示', 'start': 13, 'end': 17, 'action': 'hint', 'removed_start': 10, 'removed_end': 18},
   {'phrase': '直接给完整答案', 'start': 18, 'end': 25, 'action': 'full', 'removed_start': 17, 'removed_end': 25}],
  '高数基础篇65页例1'),
 ('R3-15',
  '分别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R3-16',
  '临别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 2, 'removed_end': 6}],
  '临别'),
 ('R3-17',
  '判别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 2, 'removed_end': 6}],
  '判别'),
 ('R3-18',
  '甄别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 2, 'removed_end': 6}],
  '甄别'),
 ('R3-19',
  '这两题分别只给提示，别给答案',
  'hint',
  [{'phrase': '只给提示', 'start': 5, 'end': 9, 'action': 'hint', 'removed_start': 3, 'removed_end': 10},
   {'phrase': '别给答案', 'start': 10, 'end': 14, 'action': 'hint', 'removed_start': 9, 'removed_end': 14}],
  '这两题'),
 ('R4-01',
  '别老是只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R4-02',
  '不要总是只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R4-03',
  '别一直只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R4-04',
  '不要老只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R4-05',
  '别总只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R4-06',
  '不要总只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R4-07',
  '别老只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R4-08',
  '不要一直只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R4-09', '不要再给我啊只给提示', 'clarify', [], '不要再给我啊只给提示'),
 ('R4-10', '请勿再给我啊只给提示', 'clarify', [], '请勿再给我啊只给提示'),
 ('R4-11',
  '高数基础篇65页例1，别老是只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 10, 'removed_end': 18}],
  '高数基础篇65页例1'),
 ('R5-01',
  '设a等于一，只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 6, 'end': 10, 'action': 'hint', 'removed_start': 5, 'removed_end': 10}],
  '设a等于一'),
 ('R5-02',
  '设a取任意实数，且a不是一，只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 13, 'removed_end': 18}],
  '设a取任意实数，且a不是一'),
 ('R5-03',
  '高数基础篇65页例一，只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 11, 'end': 15, 'action': 'hint', 'removed_start': 10, 'removed_end': 15}],
  '高数基础篇65页例一'),
 ('R5-04',
  '设x等于一，只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 6, 'end': 10, 'action': 'hint', 'removed_start': 5, 'removed_end': 10}],
  '设x等于一'),
 ('R5-05',
  '不要分别只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R5-06',
  '不要分别直接给完整答案，只给提示',
  'hint',
  [{'phrase': '直接给完整答案', 'start': 4, 'end': 11, 'action': 'full', 'removed_start': 0, 'removed_end': 12},
   {'phrase': '只给提示', 'start': 12, 'end': 16, 'action': 'hint', 'removed_start': 11, 'removed_end': 16}],
  ''),
 ('R5-07',
  '高数基础篇65页例1，不要分别直接给完整答案，只给提示',
  'hint',
  [{'phrase': '直接给完整答案', 'start': 15, 'end': 22, 'action': 'full', 'removed_start': 10, 'removed_end': 23},
   {'phrase': '只给提示', 'start': 23, 'end': 27, 'action': 'hint', 'removed_start': 22, 'removed_end': 27}],
  '高数基础篇65页例1'),
 ('R5-08', '别每次只给提示', 'clarify', [], '别每次只给提示'),
 ('R5-09', '不要再给我呀只给提示', 'clarify', [], '不要再给我呀只给提示'),
 ('R5-10',
  '不要先只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R5-11',
  '别先只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('R5-12',
  '请不要先只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 4, 'end': 8, 'action': 'hint', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R5-13', '不要就只给提示', 'clarify', [], '不要就只给提示'),
 ('R5-14', '不要请只给提示', 'clarify', [], '不要请只给提示'),
 ('R5-15', '不要麻烦只给提示', 'clarify', [], '不要麻烦只给提示'),
 ('R5-16', '不要拜托只给提示', 'clarify', [], '不要拜托只给提示'),
 ('R5-17', '不要老师只给提示', 'clarify', [], '不要老师只给提示'),
 ('R5-18', '不要特别只给提示', 'clarify', [], '不要特别只给提示'),
 ('R5-19', '别特别只给提示', 'clarify', [], '别特别只给提示'),
 ('R5-20',
  '不要先直接给完整答案',
  'hint',
  [{'phrase': '直接给完整答案', 'start': 3, 'end': 10, 'action': 'full', 'removed_start': 0, 'removed_end': 10}],
  ''),
 ('R5-21',
  '别先直接给答案',
  'hint',
  [{'phrase': '直接给答案', 'start': 2, 'end': 7, 'action': 'full', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R5-22',
  '不要先给完整答案',
  'hint',
  [{'phrase': '给完整答案', 'start': 3, 'end': 8, 'action': 'full', 'removed_start': 0, 'removed_end': 8}],
  ''),
 ('R5-23',
  '别先直接讲答案',
  'hint',
  [{'phrase': '直接讲答案', 'start': 2, 'end': 7, 'action': 'full', 'removed_start': 0, 'removed_end': 7}],
  ''),
 ('R5-24',
  '高数基础篇65页例1，不要先只给提示，直接给完整答案',
  'full',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 10, 'removed_end': 19},
   {'phrase': '直接给完整答案', 'start': 19, 'end': 26, 'action': 'full', 'removed_start': 18, 'removed_end': 26}],
  '高数基础篇65页例1'),
 ('C-01',
  '只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 0, 'end': 4, 'action': 'hint', 'removed_start': 0, 'removed_end': 4}],
  ''),
 ('C-02',
  '特别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 0, 'removed_end': 6}],
  ''),
 ('C-03',
  '先别给答案',
  'hint',
  [{'phrase': '先别给答案', 'start': 0, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 5}],
  ''),
 ('C-04',
  '个别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 2, 'removed_end': 6}],
  '个别'),
 ('C-05',
  '区别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 2, 'end': 6, 'action': 'hint', 'removed_start': 2, 'removed_end': 6}],
  '区别'),
 ('C-06',
  '要不要只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 3, 'removed_end': 7}],
  '要不要'),
 ('C-07',
  '这题不要用洛必达，只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 9, 'end': 13, 'action': 'hint', 'removed_start': 8, 'removed_end': 13}],
  '这题不要用洛必达'),
 ('C-08',
  '高数基础篇65页例1，f(x)=a，只给提示，b>0',
  'hint',
  [{'phrase': '只给提示', 'start': 18, 'end': 22, 'action': 'hint', 'removed_start': 17, 'removed_end': 23}],
  '高数基础篇65页例1，f(x)=a，b>0'),
 ('C-09',
  '先别给答案，只提示下一步',
  'hint',
  [{'phrase': '先别给答案', 'start': 0, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 6},
   {'phrase': '只提示下一步', 'start': 6, 'end': 12, 'action': 'hint', 'removed_start': 5, 'removed_end': 12}],
  ''),
 ('C-10', '高数基础篇65页例1', '', [], '高数基础篇65页例1'),
 ('X-01',
  '高数基础篇65页例1，别只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 12, 'end': 16, 'action': 'hint', 'removed_start': 10, 'removed_end': 16}],
  '高数基础篇65页例1'),
 ('X-02',
  '高数基础篇65页例1，直接给完整答案',
  'full',
  [{'phrase': '直接给完整答案', 'start': 11, 'end': 18, 'action': 'full', 'removed_start': 10, 'removed_end': 18}],
  '高数基础篇65页例1'),
 ('X-03', '高数基础篇65页例1，珂别只给提示', 'clarify', [], '高数基础篇65页例1，珂别只给提示'),
 ('X-04',
  '高数基础篇65页例1，老师别只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 14, 'end': 18, 'action': 'hint', 'removed_start': 10, 'removed_end': 18}],
  '高数基础篇65页例1'),
 ('X-05',
  '高数基础篇65页例1，特别只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 13, 'end': 17, 'action': 'hint', 'removed_start': 10, 'removed_end': 17}],
  '高数基础篇65页例1'),
 ('X-06',
  '高数基础篇65页例1，老师只给提示',
  'hint',
  [{'phrase': '只给提示', 'start': 13, 'end': 17, 'action': 'hint', 'removed_start': 10, 'removed_end': 17}],
  '高数基础篇65页例1'),
 ('X-07', '高数基础篇65页例1，珂只给提示', 'clarify', [], '高数基础篇65页例1，珂只给提示'),
 ('X-08',
  '莫只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 1, 'end': 5, 'action': 'hint', 'removed_start': 0, 'removed_end': 5}],
  ''),
 ('X-09',
  '不要总只给提示',
  'full',
  [{'phrase': '只给提示', 'start': 3, 'end': 7, 'action': 'hint', 'removed_start': 0, 'removed_end': 7}],
  '')]

@pytest.mark.parametrize("ident,raw,action,fragments,problem", CORPUS, ids=[r[0] for r in CORPUS])
def test_approved_corpus(ident, raw, action, fragments, problem):
    actual = request.split_teaching_requirement(raw)
    assert actual == (action, fragments, problem)
    for f in actual[1]:
        assert raw[f["start"]:f["end"]] == f["phrase"]
        assert f["phrase"] in raw[f["removed_start"]:f["removed_end"]]
    resolved = request.resolve_request(query=raw, book_title=BOOK, chapter=None, printed_page=None, exercise_label=None)
    assert resolved["original_query"] == raw
    assert resolved["teaching_action"] == action
    assert resolved["teaching_fragments"] == fragments
    assert resolved["problem_query"] == problem


@pytest.mark.parametrize("content", [
    "  设a取任意实数，且a不是一\t", "高数基础篇65页例一", "f'(x)=a,b>0",
    "x∈[a,b)，f(x)=x^2", "x∈(a,b]，a≠0", "α_1+β²=−2.5",
    '题干“只给提示，别给答案”仍是引用', "‘不要只给提示’", "「只给提示」", "『别给答案』",
    "《只给提示》", '"只给提示\\\"，别给答案"', "'只给提示'",
    "`只给提示，别给答案`", "```只给提示\n别给答案```",
    "(只给提示，[别给答案])", "（题干【只给提示】）", "{只给提示，别给答案}",
    "$只给提示，别给答案$", "$$只给提示，别给答案$$", r"\(只给提示，别给答案\)",
    r"\[只给提示，别给答案\]", r"\text{只给提示，别给答案}",
    r"\begin{a}只给提示\begin{b}别给答案\end{b}\end{a}",
    r"$x=\text{只给提示}\$，别给答案$", "这题不要用洛必达", "这题特别难",
])
def test_protected_and_content_slices_survive_exactly(content):
    assert request.split_teaching_requirement(content) == ("", [], content)
    raw = content + "，只给提示"
    action, fragments, problem = request.split_teaching_requirement(raw)
    assert action == "hint"
    assert problem == content
    assert len(fragments) == 1
    f = fragments[0]
    assert f == dict(phrase="只给提示", start=len(content)+1, end=len(raw), action="hint",
                     removed_start=len(content), removed_end=len(raw))


@pytest.mark.parametrize("content", [
    '“只给提示', "'别给答案", "`只给提示", "```只给提示", "$只给提示", "$$只给提示",
    r"\(只给提示", r"\[只给提示", "(只给提示", "[只给提示", "{只给提示", "（只给提示",
    r"\begin{a}只给提示\end{b}", "([)]，只给提示",
])
def test_incomplete_structure_preserves_every_character_and_clarifies(content):
    assert request.split_teaching_requirement(content) == ("clarify", [], content)


@pytest.mark.parametrize("raw", [
    "只给提示，直接给完整答案", "别给答案，给完整答案", "不要只给提示，不要直接给答案",
    "不要不只给提示", "不能不要只给提示", "不要别给答案", "不要先别给答案",
    "第一题只给提示，第二题直接给答案", "这题，只给提示，另一题，给完整答案",
    "高数基础篇65页例1，只给提示，高数基础篇60页例2，只给提示",
    "高数基础篇65页例1，只给提示，线代基础篇65页例1，只给提示",
    "请不要 ， 只给提示", "不要 再，给 我，只给提示",
    "先别给答案；；不要再只给提示", "先别给答案，请不要再只给提示",
])
def test_unknown_conflicts_and_cross_targets_never_become_full(raw):
    assert request.split_teaching_requirement(raw)[0] == "clarify"
    if "，" in raw:
        # The same target conflict must not be resolved by a first-match rule.
        assert request.split_teaching_requirement("，".join(reversed(raw.split("，"))))[0] == "clarify"


@pytest.mark.parametrize("separator", list("，、；：,;:。！？!?\n\r") + ["\r\n"])
def test_hard_boundaries_preserve_original_connector(separator):
    # ASCII punctuation between two math terms is protected, not a boundary;
    # Chinese content on both sides makes each of these an actual boundary.
    raw = "题干甲" + separator + "只给提示" + separator + "题干乙"
    action, _, problem = request.split_teaching_requirement(raw)
    assert action == "hint"
    assert problem == "题干甲" + separator + "题干乙"


def test_math_lists_intervals_and_decimal_are_not_clause_boundaries():
    raw = "x=a,b>0;c<2:d=1.25，f'(x)=a，只给提示，b>0"
    clauses, _ = teaching_intent._scan(raw)
    assert raw[clauses[0].start:clauses[0].end] == "x=a,b>0;c<2:d=1.25"
    assert request.split_teaching_requirement(raw)[2] == "x=a,b>0;c<2:d=1.25，f'(x)=a，b>0"


@pytest.mark.parametrize("raw,action,problem", [
    ("先别给答案，不要再只给提示", "full", ""),
    (P65 + "， 先别给答案 ， 不要再只给提示 ", "full", P65),
    ("不要再只给提示，先别给答案", "clarify", ""),
    ("先别给答案，题干，不要再只给提示", "clarify", "题干"),
    ("题干，珂只给提示，只给提示", "clarify", "题干，珂只给提示"),
    ("题干，珂别只给提示，直接给答案", "clarify", "题干，珂别只给提示"),
    ("总结关键思路", "", "总结关键思路"),
    ("  无教学要求 \t", "", "  无教学要求 \t"),
    ("第8题只给提示", "clarify", "第8题只给提示"),
])
def test_composition_and_conservative_problem(raw, action, problem):
    result = request.split_teaching_requirement(raw)
    assert result[0] == action
    assert result[2] == problem


@pytest.mark.parametrize("mode", ["hint", "clarify"])
def test_independent_answer_sentinels_do_not_enter_packet_or_prompt(mode):
    view = _fake_view(mode)
    sentinels = ["SOURCE_ANSWER_SECRET", "GENERIC_CONCLUSION_SECRET", "GENERIC_EXPLANATION_SECRET",
                 "GENERIC_CITATION_SECRET", "PAGE_BODY_SECRET"]
    view["teaching_bundle"]["source_answer_text"] = sentinels[0]
    view["generic_answer_bundle"].update(conclusion=sentinels[1], explanation=sentinels[2], citations=[sentinels[3]])
    view["page_content_bundle"]["content"] = sentinels[4]
    packet = teach.build_evidence_packet(view, mode)
    args = types.SimpleNamespace(subject="数学", question=P65, progress_context=None)
    prompt = teach.build_prompt(args, packet, mode=mode, question=P65)
    for sentinel in sentinels:
        assert sentinel not in packet
        assert sentinel not in prompt
    # Ensure the sentinels are meaningful: full really does expose the answers.
    full = teach.build_evidence_packet(view, "full")
    assert all(s in full for s in sentinels)


@pytest.mark.parametrize("mode", ["hint", "clarify"])
@pytest.mark.parametrize("missing", [False, True])
def test_empty_problem_packet_never_refills_original_question(mode, missing):
    view = _fake_view(mode)
    view["question"] = "ORIGINAL_REQUEST_NOT_A_PROBLEM"
    if missing:
        view["request_resolution"].pop("problem_query")
    else:
        view["request_resolution"]["problem_query"] = ""
    packet = teach.build_evidence_packet(view, mode)
    assert packet.splitlines()[0] == "问题："
    assert "ORIGINAL_REQUEST_NOT_A_PROBLEM" not in packet


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("show_evidence", [False, True])
@pytest.mark.parametrize("action,problem,expected", [
    ("clarify", P65 + "，珂只给提示", teach.CLARIFY_TEXT),
    ("hint", "", teach.MISSING_PROBLEM_TEXT),
    ("full", None, teach.MISSING_PROBLEM_TEXT),
    ("clarify", "", teach.MISSING_PROBLEM_TEXT),
])
def test_main_fixed_clarification_zero_gpt_before_profile_packet_prompt(
        monkeypatch, capsys, dry_run, show_evidence, action, problem, expected):
    view = _fake_view(action)
    if problem is None:
        view["request_resolution"].pop("problem_query")
    else:
        view["request_resolution"]["problem_query"] = problem
    args = types.SimpleNamespace(question="ORIGINAL_REQUEST", dry_run=dry_run,
                                 show_evidence=show_evidence, no_profile=False, require_profile=True)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("unexpected")
        raise AssertionError("clarify must return before profile/packet/prompt/GPT")
    monkeypatch.setattr(teach, "parse_args", lambda: args)
    monkeypatch.setattr(teach, "run_local_ask", lambda _: view)
    for name in ("build_evidence_packet", "build_prompt", "call_gpt"):
        monkeypatch.setattr(teach, name, forbidden)
    monkeypatch.setattr(teach.context, "find_project_root", forbidden)
    assert teach.main() == 0
    assert calls == []
    out = capsys.readouterr().out
    assert out == (("【固定澄清输出；无模型 prompt】\n" if dry_run else "") + expected + "\n")


@pytest.mark.parametrize("mode", ["hint", "full"])
def test_normal_main_keeps_generation_path(monkeypatch, capsys, mode):
    args = types.SimpleNamespace(question="原话", dry_run=False, show_evidence=False, no_profile=True)
    view = _fake_view(mode)
    calls = []
    monkeypatch.setattr(teach, "parse_args", lambda: args)
    monkeypatch.setattr(teach, "run_local_ask", lambda _: view)
    def prompt(*args, **kwargs):
        calls.append(("prompt", kwargs["mode"], kwargs["question"]))
        return "PROMPT"
    def gpt(prompt, args):
        calls.append(("gpt", prompt))
        return "GENERATED"
    monkeypatch.setattr(teach, "build_prompt", prompt)
    monkeypatch.setattr(teach, "call_gpt", gpt)
    assert teach.main() == 0
    assert calls == [("prompt", mode, P65 if mode == "hint" else "原话"), ("gpt", "PROMPT")]
    assert capsys.readouterr().out == "GENERATED\n"


@pytest.mark.parametrize("disagreement", [None, "teaching_action", "problem_query", "teaching_fragments"])
def test_book_resolution_uses_preliminary_problem_and_checks_all_three_fields(monkeypatch, tmp_path, disagreement):
    import query_local_knowledge as entry
    raw = P65 + "，只给提示"
    first = request.resolve_request(query=raw, book_title="", chapter=None, printed_page=None, exercise_label=None)
    final = deepcopy(first)
    final["book_title"] = "RESOLVED_BOOK"
    if disagreement == "teaching_action":
        final[disagreement] = "full"
    elif disagreement == "problem_query":
        final[disagreement] = "CHANGED_CONTENT"
    elif disagreement == "teaching_fragments":
        final[disagreement] = []
    sequence = []
    def resolve(**kw):
        sequence.append(("request", kw))
        return deepcopy(first if len([s for s in sequence if s[0] == "request"]) == 1 else final)
    def book(**kw):
        sequence.append(("book", kw))
        return {"book_title": "RESOLVED_BOOK"}
    def intent(text):
        sequence.append(("intent", text))
        return "define"
    class Stop(Exception):
        pass
    def route(**kw):
        sequence.append(("route", kw))
        raise Stop
    monkeypatch.setattr(entry, "resolve_request", resolve)
    monkeypatch.setattr(entry, "resolve_current_task_book", book)
    monkeypatch.setattr(entry, "detect_intent", intent)
    monkeypatch.setattr(entry, "resolve_book_route", route)
    with pytest.raises(Stop):
        entry._query_single(tmp_path, "数学", None, raw, 3)
    assert [s[0] for s in sequence] == ["request", "book", "request", "intent", "route"]
    assert sequence[0][1]["query"] == sequence[2][1]["query"] == raw
    assert sequence[1][1]["query"] == sequence[3][1] == sequence[4][1]["query"] == P65
    # Observe the selected first-resolution object on disagreement at the next
    # consumer, rather than merely comparing local variables in the implementation.
    selected = []
    monkeypatch.setattr(entry, "resolve_book_route", lambda **_: {})
    monkeypatch.setattr(entry, "resolve_exercise_route", lambda **_: {})
    monkeypatch.setattr(entry, "catalogue", lambda: [])
    def crosscheck(*args, **kwargs):
        selected.append(args[0])
        raise Stop
    monkeypatch.setattr(entry, "apply_scoped_exercise_relation", lambda **_: ({"status": "blocked"}, []))
    monkeypatch.setattr(entry, "apply_hard_page_route", lambda **_: ({}, [], [], []))
    monkeypatch.setattr(entry, "build_page_crosscheck", crosscheck)
    sequence.clear()
    with pytest.raises(Stop):
        entry._query_single(tmp_path, "数学", None, raw, 3)
    chosen = selected[0]
    assert chosen["problem_query"] == P65
    assert chosen["teaching_fragments"] == first["teaching_fragments"]
    assert chosen["teaching_action"] == ("clarify" if disagreement else "hint")
    assert chosen["book_title"] == (first["book_title"] if disagreement else "RESOLVED_BOOK")


@pytest.mark.parametrize("content", ["f(只给提示)", r"\text{只给提示}", "f(x)='别给答案'", "f(第1题，别给答案)"])
def test_mathematical_group_cannot_expose_instruction_or_target(content):
    assert request.split_teaching_requirement(content) == ("", [], content)
    assert request.split_teaching_requirement(content + "，只给提示")[0::2] == ("hint", content)


@pytest.mark.parametrize("problem", ["极限的定义", ""])
def test_main_topic_formal_and_concept_consumers_get_only_preserved_problem(monkeypatch, tmp_path, problem):
    # Port of the two local enhancement cases: main computes topic/formal
    # requirements inline in _query_single, then passes them to retrieval.
    # Its independent concept route must consume the same preserved field.
    import query_local_knowledge as entry
    raw = "ORIGINAL_UNRESOLVED_REQUEST"
    resolved = request.resolve_request(query=problem, book_title="", chapter=None,
                                       printed_page=None, exercise_label=None)
    calls = []
    class Stop(Exception):
        pass
    def terms(text, *args, **kw):
        calls.append(("terms", text))
        return ["极限"] if text else []
    def formal(text, *args, **kw):
        calls.append(("formal", text))
        return bool(text)
    def retrieval(*args, **kw):
        calls.append(("retrieval", args[2], kw))
        raise Stop
    monkeypatch.setattr(entry, "resolve_request", lambda **_: deepcopy(resolved))
    monkeypatch.setattr(entry, "resolve_current_task_book", lambda **_: {"book_title": ""})
    monkeypatch.setattr(entry, "detect_intent", lambda _: "define")
    monkeypatch.setattr(entry, "resolve_book_route", lambda **_: {})
    monkeypatch.setattr(entry, "resolve_exercise_route", lambda **_: {})
    monkeypatch.setattr(entry, "generic_topic_terms", terms)
    monkeypatch.setattr(entry, "is_definition_request", formal)
    monkeypatch.setattr(entry, "_resolve_query_hits", retrieval)
    with pytest.raises(Stop):
        entry._query_single(tmp_path, "数学", None, raw, 3)
    assert calls[:2] == [("terms", problem), ("formal", problem)]
    assert calls[2][0:2] == ("retrieval", raw)
    assert calls[2][2]["topic_terms"] == (["极限"] if problem else [])
    assert calls[2][2]["formal_only"] == bool(problem)

    seen = []
    def concepts(text):
        seen.append(text)
        return []
    monkeypatch.setattr(entry, "extract_explicit_concepts", concepts)
    baseline = {"query": raw, "subject": "数学",
                "book_title": entry.PRIMARY_CALCULUS_BOOK_TITLE,
                "answer_grounding": {"status": "exact_answer", "can_conclude": True},
                "request_resolution": {"source_request_kind": "exercise", "problem_query": problem}}
    assert entry.build_concept_routes(baseline, topk=3) == []
    assert seen == [problem]


@pytest.mark.parametrize("raw", [
    "高数基础篇65页例1，只给提示，高数基础篇60页例2，直接给答案",
    "高数基础篇65页例1，只给提示，高数基础篇60页例2，只给提示",
])
def test_multi_teaching_view_cannot_aggregate_full_over_child_hint(monkeypatch, capsys, raw):
    view = {"source_targets_version": "source-targets.v1", "original_query": raw,
            "items": [{"result": _fake_view("hint")}, {"result": _fake_view("full")}]}
    assert teach.decide_teaching_action(view)[0] == "clarify"
    args = types.SimpleNamespace(dry_run=False, question=raw)
    monkeypatch.setattr(teach, "parse_args", lambda: args)
    monkeypatch.setattr(teach, "run_local_ask", lambda _: view)
    def forbidden(*args, **kwargs):
        pytest.fail("multi-target clarify must not construct generation inputs")
    monkeypatch.setattr(teach, "call_gpt", forbidden)
    monkeypatch.setattr(teach, "build_prompt", forbidden)
    assert teach.main() == 0
    assert capsys.readouterr().out == teach.CLARIFY_TEXT + "\n"


@pytest.mark.parametrize("separator", list("，、；：,;:。！？!?\n\r") + ["\r\n"])
def test_one_original_hard_separator_allows_only_listed_correction(separator):
    assert request.split_teaching_requirement("先别给答案" + separator + "不要再只给提示")[0::2] == ("full", "")


@pytest.mark.parametrize("content,suffix,expected", [
    ("第一题和第二题", "只给提示", "clarify"),
    ("这题与另一题", "直接给完整答案", "clarify"),
    ("高数基础篇65页例1和例2", "只给提示", "clarify"),
    ("高数基础篇65页与66页例1", "只给提示", "clarify"),
    ("高数基础篇和线代基础篇65页例1", "只给提示", "clarify"),
    ("这两题", "分别只给提示", "hint"),
    (P65, "不要分别只给提示", "full"),
    (P65, "别先直接给答案", "hint"),
    ('题干“第一题和第二题”', "只给提示", "hint"),
    ("题干（这题与另一题）", "只给提示", "hint"),
    ("第一题和第二题", "", ""),
])
def test_target_scope_within_clause_preserves_content_and_legal_offsets(content, suffix, expected):
    raw = content + ("，" + suffix if suffix else "")
    action, fragments, problem = request.split_teaching_requirement(raw)
    assert action == expected
    assert problem == content
    resolved = request.resolve_request(query=raw, book_title=BOOK, chapter=None,
                                       printed_page=None, exercise_label=None)
    assert resolved["original_query"] == raw
    assert resolved["problem_query"] == content
    assert resolved["teaching_fragments"] == fragments
    if suffix:
        phrase, meaning = ("直接给完整答案", "full") if suffix == "直接给完整答案" else (
            ("直接给答案", "full") if suffix == "别先直接给答案" else ("只给提示", "hint"))
        start = raw.index(phrase)
        assert fragments == [dict(phrase=phrase, start=start, end=start + len(phrase),
                                  action=meaning, removed_start=len(content), removed_end=len(raw))]
    else:
        assert fragments == []


@pytest.mark.parametrize("raw", ["第一题和第二题，只给提示", "这题与另一题，直接给完整答案"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("show_evidence", [False, True])
def test_same_clause_multiple_targets_main_fixed_before_all_generation(
        monkeypatch, capsys, raw, dry_run, show_evidence):
    view = _fake_view("")
    view["request_resolution"] = request.resolve_request(
        query=raw, book_title=BOOK, chapter=None, printed_page=None, exercise_label=None)
    args = types.SimpleNamespace(question=raw, dry_run=dry_run, show_evidence=show_evidence,
                                 no_profile=False, require_profile=True)
    monkeypatch.setattr(teach, "parse_args", lambda: args)
    monkeypatch.setattr(teach, "run_local_ask", lambda _: view)
    def forbidden(*args, **kwargs):
        pytest.fail("fixed clarification must precede profile/packet/prompt/GPT")
    for name in ("build_evidence_packet", "build_prompt", "call_gpt"):
        monkeypatch.setattr(teach, name, forbidden)
    monkeypatch.setattr(teach.context, "find_project_root", forbidden)
    assert teach.main() == 0
    assert capsys.readouterr().out == (("【固定澄清输出；无模型 prompt】\n" if dry_run else "")
                                      + teach.CLARIFY_TEXT + "\n")
