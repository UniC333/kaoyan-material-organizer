from __future__ import annotations

import re
from datetime import date
from typing import Any


TEACHING_CONTEXT_CONTRACT_VERSION = "r55.bounded-teaching-context.v1"
MAX_ACCEPTED_ANCHORS = 1
MAX_PREFERRED_ROUTES = 2
MAX_AVOID_FIRST = 1
MAX_SELF_CHECKS = 1
MAX_CONTEXT_CHARS = 500
ALLOWED_TEACHING_SCOPES = {"topic", "chapter", "subject"}
GENERIC_TOPIC_FRAGMENTS = (
    "的最小记忆方法",
    "最小记忆方法",
    "怎么理解",
    "如何理解",
    "怎么推导",
    "如何推导",
    "推导方法",
)


def _normalized(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s，。；：、,.!?！？（）()【】\[\]“”\"'·_-]+", "", text)


def _topic_key(value: Any) -> str:
    text = _normalized(value)
    for fragment in GENERIC_TOPIC_FRAGMENTS:
        text = text.replace(_normalized(fragment), "")
    return text


def _topic_matches(topic: Any, query: Any) -> bool:
    topic_key = _topic_key(topic)
    query_key = _normalized(query)
    if len(topic_key) < 2 or len(query_key) < 2:
        return False
    return topic_key in query_key or query_key in topic_key


def _chapter_matches(expected: Any, actual: Any) -> bool:
    left = _normalized(expected)
    right = _normalized(actual)
    return bool(left and right and (left in right or right in left))


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _unhelpful(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method", "")).strip()
        reason = str(item.get("reason", "")).strip()
        normalized = {"method": method, "reason": reason}
        if method and reason and normalized not in result:
            result.append(normalized)
    return result


def _empty_context(summary: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "contract_version": TEACHING_CONTEXT_CONTRACT_VERSION,
        "scope_match": "none",
        "accepted_anchor": "",
        "preferred_routes": [],
        "avoid_as_first_explanation": [],
        "self_check": "",
        "history_used": [],
        "effect_scope": "presentation_only",
        "fact_write_allowed": False,
        "selection_summary": summary
        or {
            "considered_count": 0,
            "selected_event_count": 0,
            "wrong_subject_count": 0,
            "out_of_scope_count": 0,
            "expired_count": 0,
            "superseded_count": 0,
            "ineligible_count": 0,
            "inactive_count": 0,
            "budget_rejected_count": 0,
        },
    }


def build_bounded_teaching_context(
    events: list[dict[str, Any]],
    *,
    subject: str,
    chapter: str | None,
    query: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    summary = _empty_context()["selection_summary"]
    today = _as_date(as_of) or date.today()
    eligible_events = [
        event
        for event in events
        if event.get("event_type") == "understanding_distilled"
        and bool(dict(event.get("intake_decision") or {}).get("learner_model_eligible", False))
    ]
    superseded_candidates = {
        candidate_id
        for event in eligible_events
        for candidate_id in _strings(dict(event.get("payload") or {}).get("supersedes_candidate_ids", []))
    }
    ordered = sorted(events, key=lambda item: (str(item.get("occurred_at", "")), str(item.get("event_id", ""))), reverse=True)
    accepted_anchor = ""
    preferred_routes: list[str] = []
    avoid_first: list[dict[str, str]] = []
    self_check = ""
    history_used: list[str] = []
    strongest_scope = "none"
    scope_rank = {"none": 0, "subject": 1, "chapter": 2, "exact_topic": 3}
    remaining_chars = MAX_CONTEXT_CHARS

    def reserve(text: str) -> bool:
        nonlocal remaining_chars
        cost = len(text)
        if cost <= 0 or cost > remaining_chars:
            summary["budget_rejected_count"] += 1
            return False
        remaining_chars -= cost
        return True

    for event in ordered:
        summary["considered_count"] += 1
        if event.get("event_type") != "understanding_distilled" or not bool(
            dict(event.get("intake_decision") or {}).get("learner_model_eligible", False)
        ):
            summary["ineligible_count"] += 1
            continue
        if str(event.get("subject", "")).strip() != subject:
            summary["wrong_subject_count"] += 1
            continue
        payload = dict(event.get("payload") or {})
        if str(payload.get("history_status", "active")).strip() != "active":
            summary["inactive_count"] += 1
            continue
        candidate_id = str(payload.get("candidate_id", "")).strip()
        if candidate_id and candidate_id in superseded_candidates:
            summary["superseded_count"] += 1
            continue
        review_after_text = str(payload.get("review_after", "")).strip()
        review_after = _as_date(review_after_text)
        if review_after_text and (review_after is None or today > review_after):
            summary["expired_count"] += 1
            continue

        event_chapter = str(event.get("chapter_title", "")).strip()
        exact_chapter = bool(chapter) and _chapter_matches(chapter, event_chapter)
        exact_topic = exact_chapter and _topic_matches(payload.get("topic", ""), query)
        teaching_scope = str(payload.get("teaching_scope", "chapter")).strip()
        if teaching_scope not in ALLOWED_TEACHING_SCOPES:
            teaching_scope = "chapter"
        event_selected = False
        event_scope = "none"

        if exact_topic:
            event_scope = "exact_topic"
            if not accepted_anchor:
                anchors = _strings(payload.get("accepted_core", []))
                for anchor in anchors:
                    if reserve(anchor):
                        accepted_anchor = anchor
                        event_selected = True
                        break
            for route in _strings(payload.get("derivation_route", [])):
                if len(preferred_routes) >= MAX_PREFERRED_ROUTES:
                    break
                if route not in preferred_routes and reserve(route):
                    preferred_routes.append(route)
                    event_selected = True
            if len(avoid_first) < MAX_AVOID_FIRST:
                routes = _unhelpful(payload.get("unhelpful_routes", []))
                if routes and reserve(routes[0]["method"] + routes[0]["reason"]):
                    avoid_first.append(routes[0])
                    event_selected = True
            if not self_check:
                checks = _strings(payload.get("self_checks", []))
                for check in checks:
                    if reserve(check):
                        self_check = check
                        event_selected = True
                        break

        preference_allowed = (
            (teaching_scope == "topic" and exact_topic)
            or (teaching_scope == "chapter" and exact_chapter)
            or teaching_scope == "subject"
        )
        if preference_allowed:
            preference_scope = "exact_topic" if teaching_scope == "topic" else teaching_scope
            if scope_rank[preference_scope] > scope_rank[event_scope]:
                event_scope = preference_scope
            for preference in _strings(payload.get("teaching_preferences", [])):
                if len(preferred_routes) >= MAX_PREFERRED_ROUTES:
                    break
                if preference not in preferred_routes and reserve(preference):
                    preferred_routes.append(preference)
                    event_selected = True

        if event_selected:
            event_id = str(event.get("event_id", "")).strip()
            if event_id and event_id not in history_used:
                history_used.append(event_id)
            if scope_rank[event_scope] > scope_rank[strongest_scope]:
                strongest_scope = event_scope
        else:
            summary["out_of_scope_count"] += 1

    summary["selected_event_count"] = len(history_used)
    return {
        "contract_version": TEACHING_CONTEXT_CONTRACT_VERSION,
        "scope_match": strongest_scope,
        "accepted_anchor": accepted_anchor,
        "preferred_routes": preferred_routes[:MAX_PREFERRED_ROUTES],
        "avoid_as_first_explanation": avoid_first[:MAX_AVOID_FIRST],
        "self_check": self_check,
        "history_used": history_used,
        "effect_scope": "presentation_only",
        "fact_write_allowed": False,
        "selection_summary": summary,
    }
