"""Closed teaching clauses, original character spans, and conservative composition.

This module is pure: it neither resolves textbook identities nor reads any state.
Instruction recognition and permission to remove text are separate from the
final action; unresolved roles always take precedence over a known action.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

TEACHING_ACTION_PHRASES = (
    ("只提示下一步", "hint"), ("先别给答案", "hint"), ("别给答案", "hint"),
    ("只给提示", "hint"), ("提示一下", "hint"),
    ("直接给完整答案", "full"), ("给完整答案", "full"),
    ("直接讲答案", "full"), ("直接给答案", "full"),
)
TEACHING_MODIFIERS = ("特别", "老师", "请", "麻烦", "拜托", "分别")
_NEGATIONS = ("请不要", "不要", "请别", "别", "不用", "不必", "请勿", "切勿", "切莫", "勿", "莫")
_SLOTS = ("", "再", "给我", "啊", "老是", "总是", "一直", "老", "总", "分别", "先")
_LEADS = ("", "我", "请你", "这次", "先", "老师", "请", "麻烦", "拜托", "能不能", "千万")
_MIXED = {
    **{word + "只给提示": (word, "只给提示")
       for word in ("个别", "区别", "临别", "判别", "甄别", "辨别", "要不要")},
    "这两题分别只给提示": ("这两题", "分别只给提示"),
}
_SIGNALS = tuple(p for p, _ in TEACHING_ACTION_PHRASES) + (
    "只提示", "只给", "给答案", "讲答案", "完整讲解", "检查这一步", "核对这一步", "只核对", "闭卷验收",
)
_SEPARATORS = frozenset("，、；：,;:。！？!?\n\r")
_PAIRS = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}"}
_QUOTES = {"“": "”", "‘": "’", "「": "」", "『": "』", "《": "》", '"': '"', "'": "'", "`": "`"}
_WORD = re.compile(r"(?:[A-Za-z\u0370-\u03ff]+|\d+(?:\.\d+)?|∞)")
_COMMAND = re.compile(r"\\[A-Za-z]+")
_ENV = re.compile(r"\\(begin|end)\{([^{}]+)\}")
_OPS = frozenset("+-−*×·/÷^_=≠<>≤≥∈∉→")
_TARGET = re.compile(
    r"(?P<page>第?[0-9零一二三四五六七八九十两]+页|[Pp]\d+)"
    r"|(?P<exercise>第?[0-9零一二三四五六七八九十两]+题|例[0-9零一二三四五六七八九十两]+|这题|另一题)"
    r"|(?P<book>(?:高数|线代|1800)(?:基础篇|强化篇)?)"
)


@dataclass(frozen=True)
class _Protection:
    start: int
    end: int
    complete: bool = True


@dataclass
class _Clause:
    start: int
    end: int
    left: tuple[int, int]
    right: tuple[int, int]
    protected: tuple[_Protection, ...]
    role: str = "problem_description"


def _escaped(raw: str, index: int) -> bool:
    count = 0
    while index > 0 and raw[index - 1] == "\\":
        count += 1
        index -= 1
    return count % 2 == 1


def _prime(raw: str, index: int) -> bool:
    # Derivative apostrophes belong to the preceding variable/function/group.
    before = raw[index - 1:index]
    after = raw[index + 1:index + 2]
    return bool(before and (re.fullmatch(r"[A-Za-z\u0370-\u03ff]", before) or before in ")]}'")
                and (not after or after in "'([" or after in _OPS or after.isspace() or after in _SEPARATORS))


def _delimited(raw: str, start: int, opening: str, closing: str) -> _Protection:
    index = start + len(opening)
    while index < len(raw):
        if raw.startswith(closing, index) and not _escaped(raw, index):
            return _Protection(start, index + len(closing))
        index += 1
    return _Protection(start, len(raw), False)


def _environment(raw: str, start: int, first: re.Match) -> _Protection:
    stack = [first.group(2)]
    for token in _ENV.finditer(raw, first.end()):
        if _escaped(raw, token.start()):
            continue
        if token.group(1) == "begin":
            stack.append(token.group(2))
        elif stack[-1] != token.group(2):
            return _Protection(start, len(raw), False)
        else:
            stack.pop()
            if not stack:
                return _Protection(start, token.end())
    return _Protection(start, len(raw), False)


def _protection(raw: str, start: int) -> _Protection | None:
    if _escaped(raw, start):
        return None
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]"), ("```", "```")):
        if raw.startswith(opening, start):
            return _delimited(raw, start, opening, closing)
    env = _ENV.match(raw, start)
    if env and env.group(1) == "begin":
        return _environment(raw, start, env)
    char = raw[start]
    if char in _QUOTES and not (char == "'" and _prime(raw, start)):
        return _delimited(raw, start, char, _QUOTES[char])
    if char in _PAIRS:
        index = start + 1
        while index < len(raw):
            if raw[index] == _PAIRS[char]:
                return _Protection(start, index + 1)
            if char in "([" and raw[index] in ")]":
                # Only a mathematical interval permits unlike brackets.
                inside = raw[start + 1:index]
                if re.fullmatch(r"[A-Za-z\u0370-\u03ff0-9_+−.∞\-\s]+[,，][A-Za-z\u0370-\u03ff0-9_+−.∞\-\s]+", inside):
                    return _Protection(start, index + 1)
                return _Protection(start, len(raw), False)
            nested = _protection(raw, index)
            if nested:
                if not nested.complete:
                    return _Protection(start, len(raw), False)
                index = nested.end
            elif raw[index] in _PAIRS.values():
                return _Protection(start, len(raw), False)
            else:
                index += 1
        return _Protection(start, len(raw), False)
    return None


def _math_term(raw: str, start: int) -> int:
    word = _WORD.match(raw, start)
    if word:
        return word.end()
    command = _COMMAND.match(raw, start)
    if command and not _ENV.match(raw, start):
        return command.end()
    if start < len(raw) and raw[start] in _PAIRS:
        group = _protection(raw, start)
        if group and group.complete:
            return group.end
    return start


def _math_end(raw: str, start: int) -> int:
    end = _math_term(raw, start)
    if end == start:
        return start
    while end < len(raw):
        if raw[end] == "'" and _prime(raw, end):
            end += 1
            continue
        cursor = end
        while cursor < len(raw) and raw[cursor] in " \t\u3000":
            cursor += 1
        if cursor < len(raw) and (raw[cursor] in _OPS or raw[cursor] in ",;:"):
            cursor += 1
            while cursor < len(raw) and raw[cursor] in " \t\u3000":
                cursor += 1
            # An operator is retained even when followed by Chinese prose;
            # only a readable next term authorizes swallowing punctuation.
            next_end = _math_term(raw, cursor)
        else:
            next_end = _math_term(raw, cursor)
        if next_end <= cursor:
            break
        end = next_end
    return end


def _scan(raw: str) -> tuple[list[_Clause], list[_Protection]]:
    protections, separators = [], []
    index = 0
    while index < len(raw):
        protected = _protection(raw, index)
        if protected:
            protections.append(protected)
            index = protected.end
            continue
        math_end = _math_end(raw, index)
        if math_end > index:
            protections.append(_Protection(index, math_end))
            index = math_end
            continue
        if raw[index] in _SEPARATORS:
            end = index + 1
            while end < len(raw) and raw[end] in _SEPARATORS:
                end += 1
            separators.append((index, end))
            index = end
        else:
            index += 1
    clauses = []
    cursor, left = 0, (0, 0)
    for right in [*separators, (len(raw), len(raw))]:
        if cursor < right[0]:
            clauses.append(_Clause(cursor, right[0], left, right, tuple(
                p for p in protections if cursor <= p.start < right[0])))
        cursor, left = right[1], right
    return clauses, protections


def _negation_heads():
    for lead in _LEADS:
        for negation in _NEGATIONS:
            if lead in ("请", "麻烦", "拜托") and negation.startswith("请"):
                continue
            if lead == "能不能" and negation != "不要":
                continue
            if lead == "千万" and negation != "别":
                continue
            for slot in _SLOTS:
                yield lead, negation, slot


def _closed_forms() -> dict[str, frozenset[tuple[str, str, int]]]:
    # Compile the finite grammar as module constants, not a mutable parse cache.
    forms: dict[str, set[tuple[str, str, int]]] = {}
    for phrase, action in TEACHING_ACTION_PHRASES:
        for prefix in ("", *TEACHING_MODIFIERS):
            forms.setdefault(prefix + phrase, set()).add((phrase, action, len(prefix)))
        if phrase in ("先别给答案", "别给答案"):
            continue
        for lead, negation, slot in _negation_heads():
            prefix = lead + negation + slot
            forms.setdefault(prefix + phrase, set()).add((phrase, "full" if action == "hint" else "hint", len(prefix)))
    return {text: frozenset(choices) for text, choices in forms.items()}


_FORMS = _closed_forms()
_DANGLING = re.compile("(?:" + "|".join(sorted({
    r"\s*".join(re.escape(component) for component in head if component)
    for head in _negation_heads()
})) + ")")


def _interpret(text: str) -> set[tuple[str, str, int]]:
    """Return (phrase, resolved action, phrase offset), only for entire clauses."""
    return set(_FORMS.get(text, ()))


def _visible(raw: str, clause: _Clause, *, hide_math: bool = True) -> str:
    chars = list(raw[clause.start:clause.end])
    for protection in clause.protected:
        # Math variables/numbers also belong to target anchors; only brackets,
        # quotes and LaTeX must hide target/candidate strings.
        if (hide_math or re.search(r"[\u4e00-\u9fff]", raw[protection.start:protection.end])
                or raw[protection.start] in _PAIRS or raw[protection.start] in _QUOTES
                or raw[protection.start] in "$\\"):
            chars[protection.start - clause.start:protection.end - clause.start] = " " * (protection.end - protection.start)
    return "".join(chars)


def _has_signal(text: str) -> bool:
    return any(signal in text for signal in _SIGNALS)


def _dangling(text: str) -> bool:
    return _DANGLING.fullmatch(text) is not None


def _rebuild(raw: str, fragments: list[dict], clauses: list[_Clause]) -> str:
    if not fragments:
        return raw
    merged = []
    for fragment in fragments:
        start, end = fragment["removed_start"], fragment["removed_end"]
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces, cursor = [], 0
    boundaries = sorted(set(c.left for c in clauses) | set(c.right for c in clauses))
    for start, end in merged:
        pieces.append(raw[cursor:start])
        if start > 0 and end < len(raw):
            # Reuse the leftmost original boundary, never invent punctuation.
            connector = next((raw[a:b] for a, b in boundaries
                              if start <= a < b <= end and any(c in _SEPARATORS for c in raw[a:b])), "")
            pieces.append(connector)
        cursor = end
    pieces.append(raw[cursor:])
    return "".join(pieces)


def split_teaching_requirement(text):
    """Return the compatible (action, six-field fragments, conservative problem)."""
    raw = str(text or "")
    clauses, protections = _scan(raw)
    fragments, instructions = [], []
    visible = [_visible(raw, clause) for clause in clauses]
    has_candidate = any(_has_signal(part) for part in visible)
    unresolved = any(not p.complete and _has_signal(raw[p.start:p.end]) for p in protections)
    for index, clause in enumerate(clauses):
        original = raw[clause.start:clause.end]
        text = original.strip()
        trim_start = clause.start + len(original) - len(original.lstrip())
        choices = _interpret(text) if not clause.protected else set()
        mixed = _MIXED.get(text) if not clause.protected else None
        removed_start = clause.left[0]
        if mixed:
            prefix, suffix = mixed
            choices = _interpret(suffix)
            removed_start = trim_start + len(prefix)
            trim_start = removed_start
        if len(choices) == 1:
            phrase, resolved, offset = choices.pop()
            start = trim_start + offset
            fragments.append(dict(phrase=phrase, start=start, end=start + len(phrase),
                                  action=dict(TEACHING_ACTION_PHRASES)[phrase],
                                  removed_start=removed_start, removed_end=clause.right[1]))
            instructions.append((index, text, resolved))
            clause.role = "instruction" if not mixed else "problem_description"
        elif choices or _has_signal(visible[index]) or (has_candidate and _dangling(text)):
            unresolved = True
            clause.role = "unresolved"
    # Distinct content targets with teaching signals cannot receive one global mode.
    target_text = [_visible(raw, clause, hide_math=False) for clause in clauses]
    target_anchors = [list(_TARGET.finditer(part)) for part in target_text]
    targets = {tuple(match.group() for match in anchors) for anchors in target_anchors if anchors}
    # A book, page and label can describe one target; distinct values of the
    # same kind within one clause describe multiple scopes. The approved
    # collective “这两题” contributes just one anchor, “两题”.
    multiple_in_clause = any(
        len({match.group() for match in anchors if match.lastgroup == kind}) > 1
        for anchors in target_anchors for kind in ("book", "page", "exercise")
    )
    if has_candidate and (len(targets) > 1 or multiple_in_clause):
        unresolved = True
    actions = {action for _, _, action in instructions}
    if unresolved:
        action = "clarify"
    elif len(actions) > 1:
        correction = (len(instructions) == 2
                      and instructions[0][1] == "先别给答案"
                      and instructions[1][1] == "不要再只给提示"
                      and instructions[1][0] == instructions[0][0] + 1)
        if correction:
            left, right = (clauses[item[0]] for item in instructions)
            separator = raw[left.right[0]:right.left[1]]
            correction = separator in _SEPARATORS or separator == "\r\n"
        action = "full" if correction else "clarify"
    else:
        action = next(iter(actions), "")
    return action, fragments, _rebuild(raw, fragments, clauses)
