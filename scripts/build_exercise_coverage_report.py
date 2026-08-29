#!/usr/bin/env python3
"""Build a PDF/photo exercise coverage report and optionally verify query/ask gates."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from answer_local_question import TEACHING_VIEW_VERSION, build_answer_contract, build_teaching_answer_view
from common import (
    default_vault_root_arg,
    ensure_kb_layout,
    load_all_json,
    load_json_or_default,
    resolve_subject,
    sanitize_name,
    save_json,
)
from query_local_knowledge import query_knowledge


REPORT_VERSION = "exercise-coverage.v2"
CATEGORY_LABELS = {
    "single-choice": "单项选择题",
    "multiple-choice": "多项选择题",
    "comprehensive": "综合应用题",
    "true-false": "判断题",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--book-title", required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-id", default="")
    source.add_argument("--pdf-source-id", default="", help="compatibility alias for --source-id")
    parser.add_argument("--chapter-number", type=int)
    parser.add_argument("--verify-query-ask", action="store_true")
    parser.add_argument("--expected-relations", type=int)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _ints(value: Any) -> list[int]:
    result: list[int] = []
    for item in value if isinstance(value, list) else ([] if value is None else [value]):
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def relation_source_id(relation: dict[str, Any]) -> str:
    question = dict(relation.get("question") or {})
    answer = dict(relation.get("answer") or {})
    return str(relation.get("source_id") or question.get("source_id") or answer.get("source_id") or "").strip()


def relation_chapter_number(relation: dict[str, Any]) -> int | None:
    section_root = str(relation.get("section_root") or "").strip()
    if section_root:
        try:
            return int(section_root.split(".", 1)[0])
        except ValueError:
            pass
    chapter_id = str(relation.get("chapter_id") or "").strip()
    match = re.search(r"(?:^|[-_])(\d{1,2})$", chapter_id)
    if match:
        return int(match.group(1))
    chapter_title = str(relation.get("chapter_title") or "").strip()
    match = re.search(r"第\s*(\d+)\s*章", chapter_title)
    if match:
        return int(match.group(1))
    return None


def relation_expectation(relation: dict[str, Any]) -> dict[str, Any]:
    question = dict(relation.get("question") or {})
    answer = dict(relation.get("answer") or {})
    nested = bool(question or answer)
    return {
        "source_id": relation_source_id(relation),
        "question_evidence_ids": _strings(
            question.get("evidence_ids") if nested else relation.get("question_evidence_ids")
        ),
        "answer_evidence_ids": _strings(
            answer.get("evidence_ids") if nested else relation.get("answer_evidence_ids")
        ),
        "question_printed_pages": _ints(question.get("printed_pages")) if nested else [],
        "answer_printed_pages": _ints(answer.get("printed_pages")) if nested else [],
        "question_pdf_pages": _ints(question.get("pdf_pages")) if nested else _ints(relation.get("question_pdf_pages")),
        "answer_pdf_pages": _ints(answer.get("pdf_pages")) if nested else _ints(relation.get("answer_pdf_pages")),
    }


def _source_title(source: dict[str, Any]) -> str:
    return str(source.get("source_name") or source.get("book_title") or source.get("title") or "").strip()


def resolve_source(
    *,
    sources: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    subject: str,
    book_title: str,
    explicit_source_id: str,
) -> dict[str, Any]:
    if explicit_source_id:
        matches = [item for item in sources if str(item.get("source_id") or "") == explicit_source_id]
        if len(matches) != 1:
            raise ValueError(f"source id is not registered uniquely: {explicit_source_id}")
        source = matches[0]
        if str(source.get("subject") or "").strip() != subject:
            raise ValueError("source id does not belong to the requested subject")
        return source

    matches = [
        item
        for item in sources
        if str(item.get("subject") or "").strip() == subject and _source_title(item) == book_title
    ]
    if len(matches) == 1:
        return matches[0]

    relation_ids = {
        relation_source_id(item)
        for item in relations
        if str(item.get("book_title") or "").strip() == book_title and relation_source_id(item)
    }
    relation_matches = [item for item in sources if str(item.get("source_id") or "") in relation_ids]
    if len(relation_matches) == 1:
        return relation_matches[0]
    raise ValueError("book source is not unique; pass --source-id")


def _relation_query(relation: dict[str, Any]) -> tuple[str, int | None, str]:
    expectation = relation_expectation(relation)
    label = str(relation.get("exercise_label") or "").strip()
    printed_pages = expectation["question_printed_pages"]
    if printed_pages:
        container = " ".join(_strings(relation.get("container_path")))
        middle = f" {container}" if container else ""
        return f"P{printed_pages[0]}{middle} {label} 的原书答案", printed_pages[0], label
    section = str(relation.get("section_root") or "").strip()
    category = CATEGORY_LABELS.get(str(relation.get("category") or "").strip(), str(relation.get("category") or ""))
    display_label = str(int(label)) if label.isdigit() else label
    return f"第{section}节{category}第{display_label}题的原书答案", None, label


def _side_matches(actual: dict[str, Any], expectation: dict[str, Any], prefix: str) -> bool:
    checks: list[bool] = []
    expected_ids = set(expectation[f"{prefix}_evidence_ids"])
    if expected_ids:
        checks.append(set(_strings(actual.get("evidence_ids"))) == expected_ids)
    expected_printed = set(expectation[f"{prefix}_printed_pages"])
    if expected_printed:
        checks.append(set(_ints(actual.get("printed_pages"))) == expected_printed)
    expected_pdf = set(expectation[f"{prefix}_pdf_pages"])
    if expected_pdf:
        checks.append(set(_ints(actual.get("pdf_pages"))) == expected_pdf)
    return bool(checks) and all(checks)


def verify_relation(
    *,
    relation: dict[str, Any],
    subject: str,
    book_title: str,
    source_id: str,
    vault_root: Path,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    expectation = relation_expectation(relation)
    query, printed_page, exercise_label = _relation_query(relation)
    try:
        query_result = query_knowledge(
            vault_root,
            subject,
            None,
            query,
            3,
            printed_page,
            book_title,
            exercise_label,
        )
        contract = build_answer_contract(query_result)
        teaching_view = build_teaching_answer_view(contract)
    except Exception as exc:  # retain a per-relation failure instead of aborting the batch
        return {
            "query": query,
            "query_ok": False,
            "ask_ok": False,
            "page_source_consistent": False,
            "answer_gate_ok": False,
            "verification_status": "failed",
            "failure_reasons": [f"verification_exception:{type(exc).__name__}:{exc}"],
        }

    request = dict(query_result.get("request_resolution") or {})
    grounding = dict(query_result.get("answer_grounding") or {})
    teaching_bundle = dict(query_result.get("teaching_bundle") or {})
    problem = dict(grounding.get("problem") or {})
    solution = dict(grounding.get("solution") or {})
    answer_gate_ok = grounding.get("status") == "exact_answer" and bool(grounding.get("can_conclude"))
    problem_matches = _side_matches(problem, expectation, "question")
    solution_matches = _side_matches(solution, expectation, "answer")
    if not problem_matches:
        reasons.append("problem_relation_mismatch")
    if not solution_matches:
        reasons.append("solution_relation_mismatch")
    if not answer_gate_ok:
        reasons.append("answer_gate_not_exact")

    relation_sources = {
        value
        for value in (
            relation_source_id(relation),
            str((relation.get("question") or {}).get("source_id") or "").strip(),
            str((relation.get("answer") or {}).get("source_id") or "").strip(),
        )
        if value
    }
    expected_evidence_ids = expectation["question_evidence_ids"] + expectation["answer_evidence_ids"]
    evidence_source_ok = bool(expected_evidence_ids) and all(
        str((evidence_by_id.get(evidence_id) or {}).get("source_id") or "").strip() == source_id
        for evidence_id in expected_evidence_ids
    )
    relation_source_ok = relation_sources == {source_id}
    page_anchor = dict(query_result.get("page_anchor") or {})
    anchor_ok = True
    if expectation["question_printed_pages"]:
        observed_printed_page = int(page_anchor.get("printed_page") or page_anchor.get("requested_page") or 0)
        anchor_ok = (
            str(page_anchor.get("source_id") or "").strip() == source_id
            and observed_printed_page == expectation["question_printed_pages"][0]
        )
    page_source_consistent = (
        relation_source_ok and evidence_source_ok and anchor_ok and problem_matches and solution_matches
    )
    if not relation_source_ok:
        reasons.append("relation_source_mismatch")
    if not evidence_source_ok:
        reasons.append("evidence_source_mismatch")
    if not anchor_ok:
        reasons.append("page_anchor_mismatch")

    query_ok = (
        request.get("source_request_kind") == "exercise"
        and answer_gate_ok
        and problem_matches
        and solution_matches
    )
    ask_grounding = dict(teaching_view.get("answer_grounding") or {})
    ask_ok = (
        teaching_view.get("teaching_view_version") == TEACHING_VIEW_VERSION
        and ask_grounding.get("status") == "exact_answer"
        and bool(ask_grounding.get("can_conclude"))
        and teaching_bundle.get("status") == "exact"
        and bool(teaching_view.get("citation_coverage_ok"))
    )
    if not query_ok:
        reasons.append("query_gate_failed")
    if not ask_ok:
        reasons.append("ask_gate_failed")
    return {
        "query": query,
        "query_ok": query_ok,
        "ask_ok": ask_ok,
        "page_source_consistent": page_source_consistent,
        "answer_gate_ok": answer_gate_ok,
        "verification_status": "passed" if query_ok and ask_ok and page_source_consistent and answer_gate_ok else "failed",
        "failure_reasons": list(dict.fromkeys(reasons)),
        "observed": {
            "request_kind": request.get("source_request_kind", ""),
            "page_anchor": {
                "source_id": page_anchor.get("source_id", ""),
                "printed_page": page_anchor.get("printed_page") or page_anchor.get("requested_page"),
                "pdf_page": page_anchor.get("pdf_page"),
            },
            "answer_grounding_status": grounding.get("status", ""),
            "teaching_bundle_status": teaching_bundle.get("status", ""),
            "teaching_view_version": teaching_view.get("teaching_view_version", ""),
        },
    }


def build_pdf_pages(
    *,
    layout: dict[str, Path],
    subject: str,
    book_title: str,
    source_id: str,
    chapter_number: int | None,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    report = load_json_or_default(
        layout["indexes"] / "pdf_ocr_runs" / f"{subject.lower()}-{sanitize_name(book_title)}.json",
        {},
    )
    review = {
        int(item.get("pdf_page", 0) or 0): item
        for item in load_json_or_default(
            layout["review_queues"] / "pdf-page-review" / f"{source_id}.json", {}
        ).get("items", [])
        if isinstance(item, dict)
    }
    evidence_by_page = {
        int((item.get("locator") or {}).get("page_start", 0) or 0): item
        for item in evidence
        if item.get("source_id") == source_id and item.get("origin_type") == "pdf_page_ocr"
    }
    pages: list[dict[str, Any]] = []
    for item in report.get("pages", report.get("chapters", [])):
        number = int(item.get("pdf_page", item.get("page_start", 0)) or 0)
        chapter = int(item.get("chapter_number", 0) or 0)
        if chapter_number is not None and chapter != chapter_number:
            continue
        decision = review.get(number, {})
        published = evidence_by_page.get(number, {})
        status = (
            "published"
            if published
            else ("reviewed" if decision.get("review_status") == "accepted" else decision.get("review_status") or "ocr_pending_review")
        )
        pages.append(
            {
                "pdf_page": number,
                "chapter_number": chapter,
                "chapter_title": item.get("chapter_title", ""),
                "status": status,
                "review_note": decision.get("note", ""),
                "evidence_id": published.get("evidence_id", ""),
            }
        )
    return sorted(pages, key=lambda item: int(item.get("pdf_page", 0)))


def build_photo_pages(
    *,
    relations: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for relation in relations:
        expectation = relation_expectation(relation)
        pairs = (
            [(page, evidence_id) for page in expectation["question_printed_pages"] for evidence_id in expectation["question_evidence_ids"]]
            + [(page, evidence_id) for page in expectation["answer_printed_pages"] for evidence_id in expectation["answer_evidence_ids"]]
        )
        for page, evidence_id in pairs:
            item = grouped.setdefault(
                page,
                {
                    "printed_page": page,
                    "chapter_number": relation_chapter_number(relation),
                    "chapter_title": relation.get("chapter_title", ""),
                    "status": "published",
                    "review_note": "",
                    "evidence_ids": [],
                },
            )
            if evidence_id not in item["evidence_ids"]:
                item["evidence_ids"].append(evidence_id)
            evidence = evidence_by_id.get(evidence_id, {})
            if not evidence or not evidence.get("source_grounded"):
                item["status"] = "ocr_pending_review"
    return [grouped[key] for key in sorted(grouped)]


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    layout = ensure_kb_layout()
    subject, _ = resolve_subject(args.subject)
    all_relations = list(load_json_or_default(layout["indexes"] / "exercise_locator_index.json", {}).get("relations", []))
    sources = load_all_json(layout["sources"])
    source = resolve_source(
        sources=sources,
        relations=all_relations,
        subject=subject,
        book_title=args.book_title,
        explicit_source_id=str(args.source_id or args.pdf_source_id or "").strip(),
    )
    source_id = str(source["source_id"])
    relations = [item for item in all_relations if relation_source_id(item) == source_id]
    if args.chapter_number is not None:
        relations = [item for item in relations if relation_chapter_number(item) == args.chapter_number]
    relations.sort(key=lambda item: str(item.get("relation_id") or item.get("location_key") or ""))

    evidence = load_all_json(layout["evidence"])
    evidence_by_id = {str(item.get("evidence_id") or ""): item for item in evidence if item.get("evidence_id")}
    material_type = str(source.get("material_type") or "").strip()
    pages = (
        build_pdf_pages(
            layout=layout,
            subject=subject,
            book_title=args.book_title,
            source_id=source_id,
            chapter_number=args.chapter_number,
            evidence=evidence,
        )
        if "pdf" in material_type
        else build_photo_pages(relations=relations, evidence_by_id=evidence_by_id)
    )

    verify = bool(args.verify_query_ask or args.require_complete)
    relation_results: list[dict[str, Any]] = []
    vault_root = Path(default_vault_root_arg())
    for relation in relations:
        item = dict(relation)
        if verify:
            item.update(
                verify_relation(
                    relation=relation,
                    subject=subject,
                    book_title=args.book_title,
                    source_id=source_id,
                    vault_root=vault_root,
                    evidence_by_id=evidence_by_id,
                )
            )
        else:
            item.update(
                {
                    "query": "",
                    "query_ok": None,
                    "ask_ok": None,
                    "page_source_consistent": None,
                    "answer_gate_ok": None,
                    "verification_status": "not_requested",
                    "failure_reasons": [],
                }
            )
        relation_results.append(item)

    expected_matches = args.expected_relations is None or len(relations) == args.expected_relations
    exact_relations = all(str(item.get("relation_status") or "") == "exact" for item in relations)
    verified_complete = verify and bool(relations) and all(
        item["query_ok"] and item["ask_ok"] and item["page_source_consistent"] and item["answer_gate_ok"]
        for item in relation_results
    )
    complete = expected_matches and exact_relations and verified_complete
    summary = {
        "page_count": len(pages),
        "published_count": sum(item.get("status") == "published" for item in pages),
        "reviewed_count": sum(item.get("status") == "reviewed" for item in pages),
        "pending_count": sum(item.get("status") == "ocr_pending_review" for item in pages),
        "rejected_count": sum(item.get("status") == "rejected" for item in pages),
        "relation_count": len(relations),
        "expected_relations": args.expected_relations,
        "relation_count_matches": expected_matches,
        "verification_enabled": verify,
        "query_ok_count": sum(item.get("query_ok") is True for item in relation_results),
        "ask_ok_count": sum(item.get("ask_ok") is True for item in relation_results),
        "page_source_consistent_count": sum(item.get("page_source_consistent") is True for item in relation_results),
        "answer_gate_ok_count": sum(item.get("answer_gate_ok") is True for item in relation_results),
        "failure_count": sum(bool(item.get("failure_reasons")) for item in relation_results),
        "complete": complete if verify else None,
    }
    payload = {
        "schema_version": REPORT_VERSION,
        "subject": subject,
        "book_title": args.book_title,
        "source_id": source_id,
        "source_material_type": material_type,
        "chapter_number": args.chapter_number,
        "pages": pages,
        "relations": relation_results,
        "summary": summary,
    }
    stem = f"{subject.lower()}-{sanitize_name(args.book_title)}" + (
        f"-ch{args.chapter_number}" if args.chapter_number is not None else ""
    )
    path = layout["indexes"] / "exercise_coverage" / f"{stem}.json"
    save_json(path, payload, ignored_compare_keys=())
    payload["report_path"] = str(path)
    return payload, complete


def main() -> int:
    args = parse_args()
    if args.expected_relations is not None and args.expected_relations < 0:
        raise SystemExit("[ERROR] --expected-relations must be zero or greater")
    try:
        payload, complete = build_report(args)
    except ValueError as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.require_complete and not complete:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
