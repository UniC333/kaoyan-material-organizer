"""Authoritative evidence and claim publication/search eligibility rules.

The knowledge base keeps records that are useful for audit and repair next to
records that may be exposed to normal retrieval.  This module is the single
place where that boundary is decided.  Callers that only need a boolean may
use :func:`is_publishable_source_evidence`; validators and index builders
should consume :func:`evidence_publication_decision` so that the reason and
classification remain visible.
"""
from __future__ import annotations

from typing import Any, Mapping


PUBLICATION_POLICY_VERSION = "evidence-publication.v1"

PUBLISHABLE_EVIDENCE_VERIFICATION_STATUSES = {"source_grounded", "reviewed"}
ACCEPTED_EVIDENCE_REVIEW_STATUSES = {"accepted", "approved", "not-required"}
FORBIDDEN_EVIDENCE_ORIGIN_TYPES = {"profile_hint", "title_inference", "placeholder"}
BLOCKED_EVIDENCE_MAPPING_STATUSES = {"unmapped", "stale", "pending", "rejected", "review"}
ALLOWED_NON_PDF_MAPPING_STATUSES = {"", "accepted", "mapped"}
SOURCE_SPAN_LOCATOR_FIELDS = ("page_start", "page_end", "image_start", "image_end")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and value == 0:
        return False
    return _text(value) != ""


def _positive_integer(value: Any) -> int | None:
    """Return only a JSON-style positive integer, never a guessed conversion."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _reason(code: str, message: str, *, kind: str) -> dict[str, str]:
    return {"code": code, "message": message, "kind": kind}


def _decision_payload(
    *,
    entity_id: str,
    structural: list[dict[str, str]],
    blocked: list[dict[str, str]],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reasons = [*structural, *blocked]
    if structural:
        classification = "structural_invalid"
    elif blocked:
        classification = "audit_only"
    else:
        classification = "publishable"
    return {
        "policy_version": PUBLICATION_POLICY_VERSION,
        "entity_id": entity_id,
        "publishable": not reasons,
        "classification": classification,
        "reason_codes": [item["code"] for item in reasons],
        "reasons": [item["message"] for item in reasons],
        "structural_reason_codes": [item["code"] for item in structural],
        "audit_reason_codes": [item["code"] for item in blocked],
        "structural_reasons": [item["message"] for item in structural],
        "audit_reasons": [item["message"] for item in blocked],
        **dict(metadata or {}),
    }


def _origin_type(evidence: Mapping[str, Any]) -> tuple[str, bool]:
    primary = _text(evidence.get("origin_type"))
    legacy = _text(evidence.get("origin"))
    if primary and legacy and primary != legacy:
        return primary, True
    return primary or legacy, False


def _source_span_decision(
    evidence: Mapping[str, Any],
    source_spans: Any,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    structural: list[dict[str, str]] = []
    locator_complete = True
    span_count = len(source_spans) if isinstance(source_spans, list) else 0
    if not isinstance(source_spans, list) or not source_spans:
        return [
            _reason("missing-source-spans", "missing source_spans", kind="structural")
        ], {"source_span_count": 0, "source_span_locator_complete": False}

    source_id = _text(evidence.get("source_id"))
    for index, span in enumerate(source_spans, start=1):
        prefix = f"source_spans[{index}]"
        if not isinstance(span, dict):
            structural.append(_reason("invalid-source-span", f"invalid {prefix}", kind="structural"))
            locator_complete = False
            continue
        span_source_id = _text(span.get("source_id"))
        if not span_source_id:
            structural.append(_reason("missing-source-span-source-id", f"missing {prefix}.source_id", kind="structural"))
        elif source_id and span_source_id != source_id:
            structural.append(
                _reason(
                    "source-span-source-id-mismatch",
                    f"{prefix}.source_id does not match source_id",
                    kind="structural",
                )
            )
        if not _text(span.get("file_id")):
            structural.append(_reason("missing-source-span-file-id", f"missing {prefix}.file_id", kind="structural"))
        locator = span.get("locator") if isinstance(span.get("locator"), dict) else None
        if locator is None:
            structural.append(_reason("missing-source-span-locator", f"missing {prefix}.locator", kind="structural"))
            locator_complete = False
            continue
        for field in SOURCE_SPAN_LOCATOR_FIELDS:
            if not _present(locator.get(field)):
                structural.append(
                    _reason(
                        "missing-source-span-locator-field",
                        f"missing {prefix}.locator.{field}",
                        kind="structural",
                    )
                )
                locator_complete = False
    return structural, {
        "source_span_count": span_count,
        "source_span_locator_complete": locator_complete and not structural,
    }


def evidence_publication_decision(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Assess whether one evidence record may enter normal search/retrieval.

    ``locator`` at the evidence root is intentionally not inspected.  It is a
    compatibility mirror; the authoritative location is carried by every
    ``source_spans[].locator`` entry.  A malformed mirror therefore cannot make
    a record publishable, but a missing mirror cannot invalidate a complete
    nested locator.
    """
    payload: Mapping[str, Any] = evidence if isinstance(evidence, Mapping) else {}
    evidence_id = _text(payload.get("evidence_id"))
    structural: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []

    if not evidence_id:
        structural.append(_reason("missing-evidence-id", "missing evidence_id", kind="structural"))
    for field in ("source_id", "chapter_id", "chunk_id", "evidence_key"):
        if not _text(payload.get(field)):
            structural.append(_reason(f"missing-{field}", f"missing {field}", kind="structural"))

    origin_type, origin_mismatch = _origin_type(payload)
    if origin_mismatch:
        structural.append(_reason("origin-type-mismatch", "origin_type and origin mismatch", kind="structural"))
    if not origin_type:
        structural.append(_reason("missing-origin-type", "missing origin_type", kind="structural"))
    elif origin_type in FORBIDDEN_EVIDENCE_ORIGIN_TYPES:
        blocked.append(_reason("forbidden-origin-type", f"forbidden origin_type: {origin_type}", kind="audit"))

    verification_status = _text(payload.get("verification_status"))
    if not verification_status:
        structural.append(_reason("missing-verification-status", "missing verification_status", kind="structural"))
    elif verification_status not in PUBLISHABLE_EVIDENCE_VERIFICATION_STATUSES:
        blocked.append(
            _reason(
                "verification-status-not-publishable",
                f"verification_status is not publishable: {verification_status}",
                kind="audit",
            )
        )

    source_spans = payload.get("source_spans")
    span_structural, span_metadata = _source_span_decision(payload, source_spans)
    structural.extend(span_structural)

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        structural.append(_reason("missing-provenance", "missing provenance", kind="structural"))
    else:
        for field in ("origin_type", "verification_status", "source_spans"):
            value = provenance.get(field)
            if field == "source_spans":
                if not isinstance(value, list) or not value:
                    structural.append(_reason("missing-provenance-source-spans", "missing provenance.source_spans", kind="structural"))
            elif not _text(value):
                structural.append(_reason(f"missing-provenance-{field}", f"missing provenance.{field}", kind="structural"))
        if _text(provenance.get("origin_type")) != origin_type:
            structural.append(_reason("provenance-origin-type-mismatch", "provenance.origin_type mismatch", kind="structural"))
        if _text(provenance.get("verification_status")) != verification_status:
            structural.append(_reason("provenance-verification-status-mismatch", "provenance.verification_status mismatch", kind="structural"))
        if provenance.get("source_grounded") != payload.get("source_grounded"):
            structural.append(_reason("provenance-source-grounded-mismatch", "provenance.source_grounded mismatch", kind="structural"))

    if "source_grounded" not in payload:
        structural.append(_reason("missing-source-grounded", "missing source_grounded", kind="structural"))
    elif not isinstance(payload.get("source_grounded"), bool):
        structural.append(_reason("invalid-source-grounded", "source_grounded must be boolean", kind="structural"))
    elif not payload.get("source_grounded"):
        blocked.append(_reason("not-source-grounded", f"evidence is not source_grounded: {evidence_id}", kind="audit"))

    review_status = _text(payload.get("review_status"))
    if review_status and review_status not in ACCEPTED_EVIDENCE_REVIEW_STATUSES:
        blocked.append(
            _reason(
                "review-status-not-publishable",
                f"review_status is not publishable: {review_status}",
                kind="audit",
            )
        )

    mapping_status = _text(payload.get("mapping_status"))
    if mapping_status in BLOCKED_EVIDENCE_MAPPING_STATUSES:
        blocked.append(_reason("mapping-status-not-publishable", f"mapping_status is not publishable: {mapping_status}", kind="audit"))
    elif mapping_status and mapping_status not in ALLOWED_NON_PDF_MAPPING_STATUSES:
        blocked.append(_reason("mapping-status-unknown", f"mapping_status is not publishable: {mapping_status}", kind="audit"))

    pdf_page = _positive_integer(payload.get("pdf_page"))
    printed_page = _positive_integer(payload.get("printed_page"))
    pdf_page_gate: dict[str, Any] = {"required": origin_type == "pdf_page_ocr"}
    if origin_type == "pdf_page_ocr":
        if pdf_page is None:
            blocked.append(_reason("pdf-page-missing-or-invalid", "pdf_page must be a positive integer for pdf_page_ocr", kind="audit"))
        if printed_page is None:
            blocked.append(_reason("printed-page-missing-or-invalid", "printed_page must be a positive integer for pdf_page_ocr", kind="audit"))
        if mapping_status != "mapped":
            blocked.append(_reason("pdf-mapping-not-mapped", "pdf_page_ocr requires mapping_status=mapped", kind="audit"))
        pdf_page_gate.update({"pdf_page": pdf_page, "printed_page": printed_page, "mapping_status": mapping_status})

    publication_status = _text(payload.get("publication_status"))
    if publication_status and publication_status != "published":
        blocked.append(
            _reason(
                "publication-status-not-publishable",
                f"publication_status is not publishable: {publication_status}",
                kind="audit",
            )
        )

    return _decision_payload(
        entity_id=evidence_id,
        structural=structural,
        blocked=blocked,
        metadata={
            "origin_type": origin_type,
            "verification_status": verification_status,
            "review_status": review_status,
            "mapping_status": mapping_status,
            "pdf_page_gate": pdf_page_gate,
            "top_level_locator_compatibility": "ignored_when_source_spans_locator_is_complete",
            **span_metadata,
        },
    )


def is_publishable_source_evidence(evidence: Mapping[str, Any] | None) -> bool:
    """Boolean compatibility API backed by the authoritative decision."""
    return bool(evidence_publication_decision(evidence).get("publishable"))


def claim_publication_decision(
    claim: Mapping[str, Any] | None,
    evidence_by_id: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Assess an accepted claim using the same evidence decisions as indexing.

    A claim is publishable only when it is accepted and *every* referenced
    evidence record is publishable.  Missing support is fail-closed.
    """
    payload: Mapping[str, Any] = claim if isinstance(claim, Mapping) else {}
    claim_id = _text(payload.get("claim_id"))
    structural: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    if not claim_id:
        structural.append(_reason("missing-claim-id", "missing claim_id", kind="structural"))
    for field in ("syllabus_node_id",):
        if not _text(payload.get(field) or payload.get("concept_id")):
            structural.append(_reason("missing-syllabus-node-id", "missing syllabus_node_id/concept_id", kind="structural"))
    if not _text(payload.get("canonical_text") or payload.get("text")):
        structural.append(_reason("missing-canonical-text", "missing canonical_text", kind="structural"))

    status = _text(payload.get("status"))
    if not status:
        structural.append(_reason("missing-claim-status", "missing claim status", kind="structural"))
    elif status != "accepted":
        blocked.append(_reason("claim-status-not-accepted", f"claim status is not accepted: {status or 'missing'}", kind="audit"))
    if _text(payload.get("origin")) == "placeholder":
        blocked.append(_reason("placeholder-claim", "placeholder claim cannot be published", kind="audit"))

    raw_evidence_ids = payload.get("evidence_ids")
    if not isinstance(raw_evidence_ids, list) or not raw_evidence_ids:
        structural.append(_reason("missing-claim-evidence-ids", "missing evidence_ids", kind="structural"))
        evidence_ids: list[str] = []
    else:
        evidence_ids = [_text(item) for item in raw_evidence_ids if _text(item)]
        if len(evidence_ids) != len(raw_evidence_ids):
            structural.append(_reason("invalid-claim-evidence-id", "claim evidence_ids must contain non-empty ids", kind="structural"))

    support: list[dict[str, Any]] = []
    valid_support_count = 0
    known_support_count = 0
    evidence_lookup = evidence_by_id or {}
    for evidence_id in evidence_ids:
        evidence = evidence_lookup.get(evidence_id)
        if not isinstance(evidence, Mapping):
            structural.append(_reason("unknown-claim-evidence", f"unknown evidence: {evidence_id}", kind="structural"))
            support.append({"evidence_id": evidence_id, "publishable": False, "classification": "structural_invalid", "reason_codes": ["unknown-claim-evidence"]})
            continue
        known_support_count += 1
        decision = evidence_publication_decision(evidence)
        support.append({
            "evidence_id": evidence_id,
            "publishable": bool(decision["publishable"]),
            "classification": decision["classification"],
            "reason_codes": list(decision["reason_codes"]),
        })
        if decision["publishable"]:
            valid_support_count += 1
        else:
            blocked.append(
                _reason(
                    "non-publishable-claim-evidence",
                    f"claim references non-publishable evidence: {evidence_id}",
                    kind="audit",
                )
            )

    return _decision_payload(
        entity_id=claim_id,
        structural=structural,
        blocked=blocked,
        metadata={
            "status": status,
            "evidence_ids": evidence_ids,
            "support": support,
            "support_count": len(evidence_ids),
            "known_support_count": known_support_count,
            "valid_support_count": valid_support_count,
        },
    )


def is_publishable_claim(
    claim: Mapping[str, Any] | None,
    evidence_by_id: Mapping[str, Mapping[str, Any]] | None,
) -> bool:
    return bool(claim_publication_decision(claim, evidence_by_id).get("publishable"))
