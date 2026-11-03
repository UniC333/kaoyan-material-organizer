"""Defaults and validation for the adaptive review policy."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any


DEFAULT_REVIEW_SUBJECTS = ("数学", "408")
# The first three groups disambiguate legacy mixed labels (e.g. 英语/数学).
# They are ordinary selectable groups now, rather than unconditional exclusions.
REVIEW_SUBJECT_TOKENS = {
    "英语": ("英语", "english"),
    "洛谷": ("洛谷",),
    "算法竞赛": ("算法竞赛",),
    "数学": ("数学",),
    "408": ("408", "数据结构", "计算机组成", "操作系统", "计算机网络"),
    "政治": ("政治",),
}
DEFAULT_ADAPTIVE_REVIEW = {
    "intervals": {
        "very_fluent": {"initial_days": 14, "multiplier": 2, "max_days": 45},
        "fairly_fluent": {"initial_days": 7, "multiplier": 1.5, "max_days": 21},
        "not_fluent": {"initial_days": 3, "multiplier": 0, "max_days": 3},
        "needs_remediation": {"initial_days": 1, "multiplier": 0, "max_days": 1},
    },
    "time_budget_minutes": 20,
    "max_items": 3,
    "max_full_math_items": 1,
    "math_default_minutes": 10,
    "default_minutes": 5,
    "min_estimated_minutes": 3,
    "max_estimated_minutes": 12,
    "min_interval_days": 1,
}


def default_adaptive_review() -> dict[str, Any]:
    return deepcopy(DEFAULT_ADAPTIVE_REVIEW)


def _number(value: Any, path: str, *, integer: bool = True) -> None:
    valid_type = type(value) is int if integer else type(value) in (int, float)
    if not valid_type or value < 0 or (type(value) is float and not math.isfinite(value)):
        kind = "integer" if integer else "number"
        raise ValueError(f"{path} must be a finite non-negative {kind}")


def parse_review_config(payload: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    raw_subjects = payload.get("review_subjects", list(DEFAULT_REVIEW_SUBJECTS))
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise ValueError("review_subjects must be a non-empty array of known subject names")
    subjects: list[str] = []
    for value in raw_subjects:
        if not isinstance(value, str) or value not in REVIEW_SUBJECT_TOKENS:
            raise ValueError(
                f"review_subjects contains unknown subject {value!r}; "
                f"supported: {', '.join(REVIEW_SUBJECT_TOKENS)}"
            )
        if value in subjects:
            raise ValueError(f"review_subjects contains duplicate subject: {value}")
        subjects.append(value)

    raw = payload.get("adaptive_review", {})
    if not isinstance(raw, dict):
        raise ValueError("adaptive_review must be an object")
    settings = default_adaptive_review()
    for key, value in raw.items():
        path = f"adaptive_review.{key}"
        if key not in settings:
            raise ValueError(f"unknown review config item: {path}")
        if key == "intervals":
            if not isinstance(value, dict):
                raise ValueError(f"{path} must be an object")
            for level, rule in value.items():
                rule_path = f"{path}.{level}"
                if level not in settings[key]:
                    raise ValueError(f"unknown fluency in {rule_path}")
                if not isinstance(rule, dict):
                    raise ValueError(f"{rule_path} must be an object")
                for field, number in rule.items():
                    if field not in settings[key][level]:
                        raise ValueError(f"unknown review config item: {rule_path}.{field}")
                    _number(number, f"{rule_path}.{field}", integer=field != "multiplier")
                    settings[key][level][field] = number
        else:
            _number(value, path)
            settings[key] = value
    if settings["min_estimated_minutes"] > settings["max_estimated_minutes"]:
        raise ValueError("adaptive_review.min_estimated_minutes must not exceed max_estimated_minutes")
    for level, rule in settings["intervals"].items():
        if rule["multiplier"] and rule["max_days"] < settings["min_interval_days"]:
            raise ValueError(f"adaptive_review.intervals.{level}.max_days must be >= min_interval_days")
    return tuple(subjects), settings
