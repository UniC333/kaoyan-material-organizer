#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from common import (
    PUBLICATION_POLICY_VERSION,
    claim_publication_decision,
    ensure_kb_layout,
    evidence_publication_decision,
    load_all_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def add_error(
    errors: list[dict[str, Any]],
    *,
    entity: str,
    entity_id: str,
    message: str,
    classification: str = "structural_invalid",
    reason_code: str = "",
    severity: str = "error",
) -> None:
    errors.append(
        {
            "entity": entity,
            "id": entity_id,
            "message": message,
            "classification": classification,
            "reason_code": reason_code,
            "severity": severity,
        }
    )


def locator_has_required_fields(locator: dict[str, Any]) -> list[str]:
    """Compatibility helper for callers that inspect a nested locator."""
    missing: list[str] = []
    for field in ("page_start", "page_end", "image_start", "image_end"):
        if str(locator.get(field, "")).strip() == "":
            missing.append(field)
    return missing


def evidence_publishability_errors(evidence: dict[str, Any]) -> list[str]:
    """Return the authoritative decision messages for one evidence record."""
    return list(evidence_publication_decision(evidence).get("reasons", []))


def _decision_findings(
    *,
    entity: str,
    entity_id: str,
    decision: dict[str, Any],
) -> list[dict[str, Any]]:
    reasons = [str(item) for item in decision.get("reasons", []) or []]
    codes = [str(item) for item in decision.get("reason_codes", []) or []]
    classification = str(decision.get("classification") or "structural_invalid")
    severity = "error" if classification == "structural_invalid" else "warning"
    return [
        {
            "entity": entity,
            "id": entity_id,
            "message": message,
            "classification": classification,
            "reason_code": codes[index] if index < len(codes) else "",
            "severity": severity,
        }
        for index, message in enumerate(reasons)
    ]


def _summary(
    *,
    evidence_decisions: list[tuple[str, dict[str, Any]]],
    claim_decisions: list[tuple[str, dict[str, Any]]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    classification_counts = Counter(str(item.get("classification") or "") for item in findings)
    reason_counts = Counter(str(item.get("reason_code") or "") for item in findings if item.get("reason_code"))

    def entity_summary(decisions: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        counts = Counter(str(decision.get("classification") or "") for _, decision in decisions)
        return {
            "total_count": len(decisions),
            "publishable_count": sum(1 for _, decision in decisions if decision.get("publishable")),
            "audit_only_count": counts.get("audit_only", 0),
            "structural_invalid_count": counts.get("structural_invalid", 0),
            "distinct_entity_count": len({entity_id for entity_id, _ in decisions if entity_id}),
            "classification_counts": dict(sorted(counts.items())),
        }

    distinct_findings = {(str(item.get("entity")), str(item.get("id"))) for item in findings}
    return {
        "evidence": entity_summary(evidence_decisions),
        "claim": entity_summary(claim_decisions),
        "classification_counts": dict(sorted(classification_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "distinct_entity_count": len(distinct_findings),
        "distinct_error_entity_count": len(
            {
                key
                for key in distinct_findings
                if any(
                    str(item.get("entity")) == key[0]
                    and str(item.get("id")) == key[1]
                    and item.get("severity") == "error"
                    for item in findings
                )
            }
        ),
        "audit_only_entity_count": len(
            {
                key
                for key in distinct_findings
                if any(
                    str(item.get("entity")) == key[0]
                    and str(item.get("id")) == key[1]
                    and item.get("classification") == "audit_only"
                    for item in findings
                )
            }
        ),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    layout = ensure_kb_layout()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    evidence_index: dict[str, dict[str, Any]] = {}
    evidence_decisions: list[tuple[str, dict[str, Any]]] = []
    for evidence in load_all_json(layout["evidence"]):
        evidence_id = str(evidence.get("evidence_id", "")).strip()
        if evidence_id:
            evidence_index[evidence_id] = evidence
        decision = evidence_publication_decision(evidence)
        evidence_decisions.append((evidence_id, decision))
        findings = _decision_findings(entity="evidence", entity_id=evidence_id, decision=decision)
        errors.extend(findings)
        warnings.extend(item for item in findings if item["severity"] != "error")

    claim_index: dict[str, dict[str, Any]] = {}
    claim_decisions: list[tuple[str, dict[str, Any]]] = []
    for claim in load_all_json(layout["claims"]):
        claim_id = str(claim.get("claim_id", "")).strip()
        claim_index[claim_id] = claim
        decision = claim_publication_decision(claim, evidence_index)
        claim_decisions.append((claim_id, decision))
        findings = _decision_findings(entity="claim", entity_id=claim_id, decision=decision)
        errors.extend(findings)
        warnings.extend(item for item in findings if item["severity"] != "error")

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

    summary = _summary(
        evidence_decisions=evidence_decisions,
        claim_decisions=claim_decisions,
        findings=errors,
    )
    payload = {
        "ok": not errors,
        "publication_policy_version": PUBLICATION_POLICY_VERSION,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
