#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any

from common import canonicalize_claim_text

HIGH_RISK_RELATIONS = {"missing_condition", "true_conflict"}
CONDITION_MARKERS = ("若", "如果", "当", "仅当", "前提", "条件")
SCOPE_MARKERS = ("渐进", "一般", "通常", "只在", "仅在", "范围", "适用", "闭区间", "开区间")
NEGATION_MARKERS = ("不是", "并非", "不等于", "不能", "无关")


def normalize_relation_text(text: str) -> str:
    value = canonicalize_claim_text(text)
    value = re.sub(r"[a-z0-9]+", lambda m: m.group(0).lower(), value)
    return value


def token_set(text: str) -> set[str]:
    normalized = normalize_relation_text(text)
    ascii_tokens = set(re.findall(r"[a-z0-9]+", normalized))
    compact = re.sub(r"\s+", "", normalized)
    grams = {compact[index : index + 2] for index in range(max(len(compact) - 1, 0))}
    grams.update({compact[index : index + 3] for index in range(max(len(compact) - 2, 0))})
    return {token for token in ascii_tokens.union(grams) if token}


def overlap_ratio(left: str, right: str) -> float:
    left_tokens = token_set(left)
    right_tokens = token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens.intersection(right_tokens)
    return len(shared) / max(min(len(left_tokens), len(right_tokens)), 1)


def shared_token_count(left: str, right: str) -> int:
    return len(token_set(left).intersection(token_set(right)))


def has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in str(text or "") for marker in markers)


def marker_count(text: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in str(text or ""))


def classify_claim_relation(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_text = str(left.get("canonical_text") or left.get("text") or "").strip()
    right_text = str(right.get("canonical_text") or right.get("text") or "").strip()
    left_norm = normalize_relation_text(left_text)
    right_norm = normalize_relation_text(right_text)
    ratio = overlap_ratio(left_text, right_text)
    shared_count = shared_token_count(left_text, right_text)

    relation_type = "uncertain"
    reason = "low semantic overlap"

    if left_norm and right_norm and left_norm == right_norm:
        relation_type = "equivalent"
        reason = "normalized texts match"
    else:
        left_has_condition = has_any(left_text, CONDITION_MARKERS)
        right_has_condition = has_any(right_text, CONDITION_MARKERS)
        left_has_scope = has_any(left_text, SCOPE_MARKERS)
        right_has_scope = has_any(right_text, SCOPE_MARKERS)
        left_scope_count = marker_count(left_text, SCOPE_MARKERS)
        right_scope_count = marker_count(right_text, SCOPE_MARKERS)
        left_negated = has_any(left_text, NEGATION_MARKERS)
        right_negated = has_any(right_text, NEGATION_MARKERS)

        if ratio >= 0.45 and left_has_condition != right_has_condition:
            relation_type = "missing_condition"
            reason = "one side adds an explicit condition"
        elif shared_count >= 2 and (left_has_scope != right_has_scope or left_scope_count != right_scope_count):
            relation_type = "scope"
            reason = "one side narrows the scope of the other"
        elif left_negated != right_negated and ratio >= 0.2:
            relation_type = "true_conflict"
            reason = "negation mismatch on overlapping content"
        elif shared_count >= 1 and ("答案" in left_text or "答案" in right_text) and ("步骤" in left_text or "步骤" in right_text):
            relation_type = "true_conflict"
            reason = "core predicate mismatch"
        elif shared_count >= 1 and left.get("claim_type") == right.get("claim_type"):
            relation_type = "complementary"
            reason = "claims overlap and add different useful details"

    risk_level = "high" if relation_type in HIGH_RISK_RELATIONS else "low"
    return {
        "relation_type": relation_type,
        "risk_level": risk_level,
        "overlap_ratio": round(ratio, 3),
        "reason": reason,
    }
