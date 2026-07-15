from __future__ import annotations

from typing import Any


LEARNER_MODEL_CONTRACT_VERSION = "r15.learner-model.v1"


def _empty_summary() -> dict[str, Any]:
    return {
        "total_questions": 0,
        "fallback_count": 0,
        "compare_count": 0,
        "diagnose_count": 0,
        "plan_count": 0,
        "citation_count": 0,
    }


def _update_summary(summary: dict[str, Any], *, intent: str, answer_mode: str, citation_count: int) -> None:
    summary["total_questions"] = int(summary.get("total_questions", 0)) + 1
    summary["citation_count"] = int(summary.get("citation_count", 0)) + citation_count
    if answer_mode == "chapter_fallback":
        summary["fallback_count"] = int(summary.get("fallback_count", 0)) + 1
    if intent == "compare":
        summary["compare_count"] = int(summary.get("compare_count", 0)) + 1
    if intent == "diagnose":
        summary["diagnose_count"] = int(summary.get("diagnose_count", 0)) + 1
    if intent == "plan":
        summary["plan_count"] = int(summary.get("plan_count", 0)) + 1


def _score_mastery(
    question_count: int,
    fallback_count: int,
    citation_count: int,
    compare_count: int,
    diagnose_count: int,
    plan_count: int,
) -> float:
    if question_count <= 0:
        return 0.0
    resolved_ratio = max(question_count - fallback_count, 0) / question_count
    citation_ratio = min(citation_count / question_count, 1.0)
    practice_ratio = min(question_count / 5.0, 1.0)
    intent_mix = min((compare_count + diagnose_count + plan_count) / question_count, 1.0)
    score = resolved_ratio * 0.45 + citation_ratio * 0.25 + practice_ratio * 0.20 + intent_mix * 0.10
    return round(min(max(score, 0.0), 1.0), 4)


def _band_for(score: float) -> str:
    if score >= 0.8:
        return "stable"
    if score >= 0.5:
        return "developing"
    return "weak"


def _append_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def build_learner_model_payload(
    events: list[dict[str, Any]], *, subject_filter: str | None = None
) -> dict[str, Any]:
    model: dict[str, Any] = {
        "derivation_contract_version": LEARNER_MODEL_CONTRACT_VERSION,
        "updated_at": "",
        "derived_from_event_count": 0,
        "derived_from_event_ids": [],
        "learner_state_readiness": {"status": "insufficient_events", "subject_count": 0},
        "subjects": {},
    }
    latest_seen = ""
    derived_event_ids: list[str] = []
    for event in events:
        event_type = str(event.get("event_type", "")).strip()
        if event_type not in {"question_saved", "understanding_distilled"}:
            continue
        intake_decision = dict(event.get("intake_decision") or {})
        if intake_decision and not bool(intake_decision.get("learner_model_eligible", False)):
            continue
        event_id = str(event.get("event_id", "")).strip()
        subject = str(event.get("subject", "")).strip()
        if subject_filter and subject != subject_filter:
            continue
        chapter_title = str(event.get("chapter_title", "")).strip()
        payload = dict(event.get("payload") or {})
        occurred_at = str(event.get("occurred_at", "")).strip()
        latest_seen = max(latest_seen, occurred_at)
        intent = str(payload.get("intent", "")).strip()
        answer_mode = str(payload.get("answer_mode", "")).strip()
        references = list(payload.get("references", []))
        citation_count = len(references)
        route_nodes = [dict(item) for item in payload.get("syllabus_route", []) if item.get("node_id")]
        preferred_nodes = [item["node_id"] for item in route_nodes]

        subject_model = model["subjects"].setdefault(
            subject,
            {
                "question_count": 0,
                "derived_from_event_count": 0,
                "derived_from_event_ids": [],
                "chapters": {},
                "node_mastery": {},
                "mastery_summary": _empty_summary(),
                "accepted_understanding": [],
                "teaching_preferences": [],
                "unhelpful_routes": [],
                "updated_at": "",
            },
        )
        subject_model.setdefault("accepted_understanding", [])
        subject_model.setdefault("teaching_preferences", [])
        subject_model.setdefault("unhelpful_routes", [])
        if event_id and event_id not in derived_event_ids:
            derived_event_ids.append(event_id)
        if event_id and event_id not in subject_model["derived_from_event_ids"]:
            subject_model["derived_from_event_ids"].append(event_id)
        subject_model["derived_from_event_count"] = int(subject_model.get("derived_from_event_count", 0)) + 1
        subject_model["updated_at"] = occurred_at

        if event_type == "understanding_distilled":
            chapter_model = subject_model["chapters"].setdefault(
                chapter_title,
                {
                    "question_count": 0,
                    "last_question_at": "",
                    "last_note": "",
                    "preferred_nodes": [],
                    "mastery_summary": _empty_summary(),
                    "accepted_understanding": [],
                    "teaching_preferences": [],
                    "unhelpful_routes": [],
                },
            )
            chapter_model.setdefault("accepted_understanding", [])
            chapter_model.setdefault("teaching_preferences", [])
            chapter_model.setdefault("unhelpful_routes", [])
            accepted = [str(item).strip() for item in payload.get("accepted_core", []) if str(item).strip()]
            preferences = [str(item).strip() for item in payload.get("teaching_preferences", []) if str(item).strip()]
            unhelpful = [dict(item) for item in payload.get("unhelpful_routes", []) if isinstance(item, dict)]
            _append_unique(subject_model["accepted_understanding"], accepted)
            _append_unique(subject_model["teaching_preferences"], preferences)
            _append_unique(subject_model["unhelpful_routes"], unhelpful)
            _append_unique(chapter_model["accepted_understanding"], accepted)
            _append_unique(chapter_model["teaching_preferences"], preferences)
            _append_unique(chapter_model["unhelpful_routes"], unhelpful)
            chapter_model["last_note"] = payload.get("saved_note", "")
            continue

        subject_model["question_count"] = int(subject_model.get("question_count", 0)) + 1
        _update_summary(subject_model["mastery_summary"], intent=intent, answer_mode=answer_mode, citation_count=citation_count)

        chapter_model = subject_model["chapters"].setdefault(
            chapter_title,
            {
                "question_count": 0,
                "last_question_at": "",
                "last_note": "",
                "preferred_nodes": [],
                "mastery_summary": _empty_summary(),
                "accepted_understanding": [],
                "teaching_preferences": [],
                "unhelpful_routes": [],
            },
        )
        chapter_model.setdefault("accepted_understanding", [])
        chapter_model.setdefault("teaching_preferences", [])
        chapter_model.setdefault("unhelpful_routes", [])
        chapter_model["question_count"] = int(chapter_model.get("question_count", 0)) + 1
        chapter_model["last_question_at"] = occurred_at
        chapter_model["last_note"] = payload.get("saved_note", "")
        if preferred_nodes:
            chapter_model["preferred_nodes"] = preferred_nodes
        _update_summary(chapter_model["mastery_summary"], intent=intent, answer_mode=answer_mode, citation_count=citation_count)

        for node in route_nodes:
            node_id = str(node.get("node_id", "")).strip()
            node_model = subject_model["node_mastery"].setdefault(
                node_id,
                {
                    "title": node.get("title", node_id),
                    "question_count": 0,
                    "compare_count": 0,
                    "diagnose_count": 0,
                    "plan_count": 0,
                    "fallback_count": 0,
                    "citation_count": 0,
                    "last_question_at": "",
                    "mastery_score": 0.0,
                    "mastery_band": "weak",
                },
            )
            node_model["title"] = node.get("title", node_model.get("title", node_id))
            node_model["question_count"] = int(node_model.get("question_count", 0)) + 1
            node_model["citation_count"] = int(node_model.get("citation_count", 0)) + citation_count
            node_model["last_question_at"] = occurred_at
            for intent_name in ("compare", "diagnose", "plan"):
                if intent == intent_name:
                    key = f"{intent_name}_count"
                    node_model[key] = int(node_model.get(key, 0)) + 1
            if answer_mode == "chapter_fallback":
                node_model["fallback_count"] = int(node_model.get("fallback_count", 0)) + 1

    for subject_model in model["subjects"].values():
        for node_model in subject_model["node_mastery"].values():
            score = _score_mastery(
                question_count=int(node_model["question_count"]),
                fallback_count=int(node_model["fallback_count"]),
                citation_count=int(node_model["citation_count"]),
                compare_count=int(node_model["compare_count"]),
                diagnose_count=int(node_model["diagnose_count"]),
                plan_count=int(node_model["plan_count"]),
            )
            node_model["mastery_score"] = score
            node_model["mastery_band"] = _band_for(score)
    model["derived_from_event_ids"] = derived_event_ids
    model["derived_from_event_count"] = len(derived_event_ids)
    model["updated_at"] = latest_seen
    model["learner_state_readiness"] = {
        "status": "ready_for_cli" if model["subjects"] else "insufficient_events",
        "subject_count": len(model["subjects"]),
    }
    return model
