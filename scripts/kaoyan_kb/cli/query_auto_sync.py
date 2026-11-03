"""Explicit index refresh before query/ask opens its read-only request scope."""
from __future__ import annotations

import subprocess

from kaoyan_kb.domain.page_locator import load_page_locator_index
from kaoyan_kb.domain.exercise_locator import load_exercise_locator_index
from sync_exam_kb import refresh_query_indexes


REPAIRABLE_REASONS = {
    "page_locator_index_missing",
    "page_locator_index_stale",
    "exercise_locator_index_missing",
    "exercise_locator_index_stale",
    "exercise_locator_index_version_mismatch",
}


def _index_problems() -> list[str]:
    problems = []
    for load_index in (load_page_locator_index, load_exercise_locator_index):
        availability = load_index()["_availability"]
        if not availability["available"]:
            problems.append(availability["reason"])
    return problems


def ensure_query_indexes() -> None:
    """Refresh at most once using sync --indexes-only's locked implementation.

    Call only after CLI validation and before any query, answer or save work.
    Fresh indexes cause no writes. Invalid configuration/indexes fail closed.
    """
    problems = _index_problems()
    if not problems:
        return
    if any(reason not in REPAIRABLE_REASONS for reason in problems):
        raise SystemExit(f"[ERROR] --auto-sync cannot refresh indexes: {', '.join(problems)}")
    try:
        refresh_query_indexes()
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise SystemExit(f"[ERROR] --auto-sync failed; query/answer/save aborted: {detail}") from exc
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"[ERROR] --auto-sync failed; query/answer/save aborted: {exc}") from exc
    # Re-read outside the query cache; a successful child exit is not enough.
    remaining = _index_problems()
    if remaining:
        raise SystemExit(f"[ERROR] --auto-sync left indexes unavailable; query/answer/save aborted: {', '.join(remaining)}")
