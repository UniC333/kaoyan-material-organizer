#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from common import ensure_kb_layout, load_all_json


FORBIDDEN_ORIGIN_TYPES = {"profile_hint", "title_inference", "placeholder"}
PUBLISHABLE_VERIFICATION_STATUSES = {"source_grounded", "reviewed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def add_error(errors: list[dict[str, str]], *, entity: str, entity_id: str, message: str) -> None:
    errors.append({"entity": entity, "id": entity_id, "message": message})


def locator_has_required_fields(locator: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("page_start", "page_end", "image_start", "image_end"):
        if str(locator.get(field, "")).strip() == "":
            missing.append(field)
    return missing


def evidence_publishability_errors(evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    evidence_id = str(evidence.get("evidence_id", "")).strip()
    origin_type = str(evidence.get("origin_type") or evidence.get("origin") or "").strip()
    verification_status = str(evidence.get("verification_status", "")).strip()
    # Stale records are retained for auditability but are intentionally excluded
    # from search and must not be treated as publishable evidence.
    if verification_status == "stale" or str(evidence.get("mapping_status", "")).strip() == "stale":
        return []
    source_spans = evidence.get("source_spans", [])
    provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
    source_grounded = bool(evidence.get("source_grounded"))

    for field in ("source_id", "chapter_id", "chunk_id", "evidence_key"):
        if not str(evidence.get(field, "")).strip():
            issues.append(f"missing {field}")

    if not origin_type:
        issues.append("missing origin_type")
    elif origin_type in FORBIDDEN_ORIGIN_TYPES:
        issues.append(f"forbidden origin_type: {origin_type}")

    if not verification_status:
        issues.append("missing verification_status")
    elif verification_status not in PUBLISHABLE_VERIFICATION_STATUSES and source_grounded:
        issues.append(f"unexpected verification_status for grounded evidence: {verification_status}")

    if not isinstance(source_spans, list) or not source_spans:
        issues.append("missing source_spans")
    else:
        for index, span in enumerate(source_spans, start=1):
            if not isinstance(span, dict):
                issues.append(f"invalid source_spans[{index}]")
                continue
            for field in ("source_id", "file_id"):
                if not str(span.get(field, "")).strip():
                    issues.append(f"missing source_spans[{index}].{field}")
            locator = span.get("locator") if isinstance(span.get("locator"), dict) else {}
            for field in locator_has_required_fields(locator):
                issues.append(f"missing source_spans[{index}].locator.{field}")

    locator = evidence.get("locator") if isinstance(evidence.get("locator"), dict) else {}
    for field in locator_has_required_fields(locator):
        issues.append(f"missing locator.{field}")

    if not provenance:
        issues.append("missing provenance")
    else:
        for field in ("origin_type", "verification_status", "source_spans"):
            value = provenance.get(field)
            if field == "source_spans":
                if not isinstance(value, list) or not value:
                    issues.append("missing provenance.source_spans")
            elif not str(value or "").strip():
                issues.append(f"missing provenance.{field}")
        if provenance.get("origin_type") != origin_type:
            issues.append("provenance.origin_type mismatch")
        if provenance.get("verification_status") != verification_status:
            issues.append("provenance.verification_status mismatch")
        if bool(provenance.get("source_grounded")) != source_grounded:
            issues.append("provenance.source_grounded mismatch")

    if not source_grounded:
        issues.append(f"evidence is not source_grounded: {evidence_id}")

    return issues


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    errors: list[dict[str, str]] = []

    evidence_index: dict[str, dict[str, Any]] = {}
    publishable_evidence_ids: set[str] = set()
    for evidence in load_all_json(layout["evidence"]):
        evidence_id = str(evidence.get("evidence_id", "")).strip()
        if evidence_id:
            evidence_index[evidence_id] = evidence
        issues = evidence_publishability_errors(evidence)
        for issue in issues:
            add_error(errors, entity="evidence", entity_id=evidence_id, message=issue)
        if not issues:
            publishable_evidence_ids.add(evidence_id)

    claim_index: dict[str, dict[str, Any]] = {}
    for claim in load_all_json(layout["claims"]):
        claim_id = str(claim.get("claim_id", "")).strip()
        claim_index[claim_id] = claim
        if not str(claim.get("syllabus_node_id") or claim.get("concept_id") or "").strip():
            add_error(errors, entity="claim", entity_id=claim_id, message="missing syllabus_node_id/concept_id")
        if not str(claim.get("canonical_text") or claim.get("text") or "").strip():
            add_error(errors, entity="claim", entity_id=claim_id, message="missing canonical_text")
        if claim.get("origin") == "placeholder":
            add_error(errors, entity="claim", entity_id=claim_id, message="placeholder claim cannot be published")
        evidence_ids = claim.get("evidence_ids", [])
        if not evidence_ids:
            add_error(errors, entity="claim", entity_id=claim_id, message="missing evidence_ids")
        else:
            for evidence_id in evidence_ids:
                evidence_id = str(evidence_id).strip()
                if evidence_id not in evidence_index:
                    add_error(errors, entity="claim", entity_id=claim_id, message=f"unknown evidence: {evidence_id}")
                    continue
                if evidence_id not in publishable_evidence_ids:
                    add_error(
                        errors,
                        entity="claim",
                        entity_id=claim_id,
                        message=f"claim references non-publishable evidence: {evidence_id}",
                    )

    for conflict in load_all_json(layout["conflicts"]):
        conflict_id = str(conflict.get("conflict_id") or conflict.get("relation_id") or "").strip()
        claim_ids = [str(item).strip() for item in conflict.get("claim_ids", []) if str(item).strip()]
        relation_type = str(conflict.get("relation_type") or conflict.get("conflict_type") or "").strip()
        if not conflict_id:
            add_error(errors, entity="conflict", entity_id="", message="missing conflict_id/relation_id")
        if not relation_type:
            add_error(errors, entity="conflict", entity_id=conflict_id, message="missing relation_type")
        if not claim_ids:
            add_error(errors, entity="conflict", entity_id=conflict_id, message="missing claim_ids")
        for claim_id in claim_ids:
            if claim_id not in claim_index:
                add_error(errors, entity="conflict", entity_id=conflict_id, message=f"unknown claim: {claim_id}")
    payload = {"ok": not errors, "error_count": len(errors), "errors": errors}
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
