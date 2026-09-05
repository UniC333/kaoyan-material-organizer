"""Plan and apply small, auditable repairs at the publication boundary."""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from kaoyan_kb.storage.atomic_io import load_json_or_default, save_json

from .evidence_publication import (
    FORBIDDEN_EVIDENCE_ORIGIN_TYPES,
    claim_publication_decision,
    evidence_publication_decision,
)


REPAIR_SCHEMA_VERSION = "evidence-publication-recovery.v1"
REPAIR_QUEUE_NAME = "evidence-publication-repair"

FORMAL_INDEX_FILES = (
    ("page-locator", "page_locator_index.json"),
    ("exercise-locator", "exercise_locator_index.json"),
    ("search-documents", "search_documents.json"),
    ("search-inverted", "inverted_index.json"),
    ("search-manifest", "search_manifest.json"),
    ("book-series", "book_series_index.json"),
    ("exercise-pairs", "exercise_pair_index.json"),
)
FORMAL_INDEX_QUEUE_DIRS = ("pdf-page-mapping", "exercise-locator")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _path(layout: Mapping[str, Path], key: str) -> Path | None:
    value = layout.get(key)
    return Path(value) if value is not None else None


def _load_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    payload = load_json_or_default(path, {})
    return payload if isinstance(payload, dict) else {}


def _source_payload(layout: Mapping[str, Path], source_id: str) -> tuple[dict[str, Any], str]:
    candidates: list[Path] = []
    for key in ("sources", "manifest_sources"):
        root = _path(layout, key)
        if root is not None:
            candidates.append(root / f"{source_id}.json")
    for candidate in candidates:
        payload = _load_object(candidate)
        if payload:
            return payload, str(candidate)
    return {}, ""


def _registered_source_sha(source: Mapping[str, Any]) -> str:
    files = [item for item in source.get("files", []) or [] if isinstance(item, dict)]
    return _text((files[0] if files else {}).get("sha256"))


def _review_items(layout: Mapping[str, Path], source_id: str) -> tuple[dict[str, Any], str]:
    root = _path(layout, "review_queues")
    if root is None:
        return {}, ""
    path = root / "pdf-page-review" / f"{source_id}.json"
    return _load_object(path), str(path)


def formal_pdf_mapping_decision(
    layout: Mapping[str, Path],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove an existing PDF/printed pair from current formal metadata.

    No page number is calculated here.  The evidence must already carry both
    positive page values, and one current accepted review-queue item must
    prove the exact same pair and source.  Generated locator indexes are not
    used as repair evidence because they may be stale under the damaged
    admission policy.
    """
    source_id = _text(evidence.get("source_id"))
    pdf_page = _positive_integer(evidence.get("pdf_page"))
    printed_page = _positive_integer(evidence.get("printed_page"))
    base = {
        "source_id": source_id,
        "pdf_page": pdf_page,
        "printed_page": printed_page,
        "proof_source": "",
    }
    if not source_id:
        return {**base, "status": "unresolved", "reason": "missing-source-id"}
    if pdf_page is None or printed_page is None:
        return {**base, "status": "unresolved", "reason": "missing-or-invalid-independent-page-fields"}

    source, source_path = _source_payload(layout, source_id)
    if not source:
        return {**base, "status": "unresolved", "reason": "registered-source-missing"}
    if _text(source.get("source_id")) != source_id:
        return {**base, "status": "unresolved", "reason": "source-id-conflict", "source_path": source_path}
    registered_sha = _registered_source_sha(source)
    if not registered_sha:
        return {**base, "status": "unresolved", "reason": "registered-source-sha-missing", "source_path": source_path}
    span_values = [
        _text(span.get("source_file_sha256"))
        for span in evidence.get("source_spans", []) or []
        if isinstance(span, dict)
    ]
    if not span_values or any(not value for value in span_values):
        return {**base, "status": "unresolved", "reason": "evidence-span-sha-missing", "source_path": source_path}
    span_shas = set(span_values)
    if span_shas != {registered_sha}:
        return {**base, "status": "unresolved", "reason": "evidence-source-sha-conflict", "source_path": source_path}

    review_payload, review_path = _review_items(layout, source_id)
    review_source_id = _text(review_payload.get("source_id"))
    if not review_payload:
        return {**base, "status": "unresolved", "reason": "formal-review-queue-missing", "review_path": review_path}
    if review_source_id != source_id:
        return {**base, "status": "unresolved", "reason": "formal-review-source-id-conflict", "review_path": review_path}
    all_page_items = [
        item
        for item in review_payload.get("items", []) or []
        if isinstance(item, dict) and _positive_integer(item.get("pdf_page")) == pdf_page
    ]
    if len(all_page_items) != 1:
        reason = "formal-review-mapping-duplicate-or-conflict" if len(all_page_items) > 1 else "formal-mapping-not-found"
        return {**base, "status": "unresolved", "reason": reason, "review_path": review_path}
    item = all_page_items[0]
    if _text(item.get("review_status")) != "accepted":
        return {**base, "status": "unresolved", "reason": "formal-review-not-accepted", "review_path": review_path}
    if not item.get("page_header_verified"):
        return {**base, "status": "unresolved", "reason": "formal-review-header-unverified", "review_path": review_path}
    item_sha = _text(item.get("source_file_sha256"))
    if not item_sha:
        return {**base, "status": "unresolved", "reason": "formal-review-source-sha-missing", "review_path": review_path}
    if item_sha != registered_sha or item_sha not in span_shas:
        return {**base, "status": "unresolved", "reason": "formal-review-source-sha-conflict", "review_path": review_path}
    if _positive_integer(item.get("printed_page")) != printed_page:
        return {**base, "status": "unresolved", "reason": "formal-review-printed-page-conflict", "review_path": review_path}
    return {
        **base,
        "status": "mapped",
        "reason": "formal-mapping-confirmed",
        "proof_source": review_path,
        "source_path": source_path,
    }


def _append_history(
    payload: dict[str, Any],
    *,
    action: str,
    reason_codes: list[str],
    note: str,
) -> dict[str, Any]:
    history = list(payload.get("review_history", [])) if isinstance(payload.get("review_history"), list) else []
    history.append(
        {
            "decision": "publication-repair",
            "action": action,
            "reason_codes": list(reason_codes),
            "note": note,
            "previous_verification_status": payload.get("verification_status", ""),
            "reviewed_at": _now_iso(),
        }
    )
    payload["review_history"] = history
    payload["review_decision"] = "publication-repair"
    payload["review_note"] = note
    payload["reviewed_at"] = history[-1]["reviewed_at"]
    return payload


def _now_iso() -> str:
    from common import now_iso

    return now_iso()


def _profile_hint_repair(evidence: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], str]:
    reason_codes = ["forbidden-origin-type"]
    note = "profile_hint evidence is retained for audit only and removed from normal publication/search."
    current_provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
    if (
        _text(evidence.get("publication_status")) == "audit_only"
        and _text(evidence.get("verification_status")) == "needs_review"
        and evidence.get("source_grounded") is False
        and _text(evidence.get("review_status")) == "acknowledged"
        and _text(current_provenance.get("verification_status")) == "needs_review"
        and current_provenance.get("source_grounded") is False
    ):
        return dict(evidence), [], note
    after = copy.deepcopy(dict(evidence))
    after["publication_status"] = "audit_only"
    after["verification_status"] = "needs_review"
    after["source_grounded"] = False
    after["review_status"] = "acknowledged"
    after = _append_history(after, action="audit-only", reason_codes=reason_codes, note=note)
    provenance = dict(after.get("provenance") or {})
    provenance["verification_status"] = "needs_review"
    provenance["source_grounded"] = False
    after["provenance"] = provenance
    return after, reason_codes, note


def _pdf_repair(
    layout: Mapping[str, Path],
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[str], str, dict[str, Any]]:
    mapping = formal_pdf_mapping_decision(layout, evidence)
    if mapping.get("status") == "mapped":
        if _text(evidence.get("mapping_status")) == "mapped":
            return None, [], "", mapping
        after = copy.deepcopy(dict(evidence))
        after["mapping_status"] = "mapped"
        return after, ["mapping-status-repaired"], "formal PDF/printed-page mapping confirmed; mapping_status set to mapped.", mapping

    reason_codes = ["formal-mapping-unresolved", str(mapping.get("reason") or "formal-mapping-not-found")]
    note = "formal PDF/printed-page mapping is missing or conflicting; evidence remains audit-only until reviewed."
    current_provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
    if (
        _text(evidence.get("mapping_status")) == "stale"
        and _text(evidence.get("verification_status")) == "needs_review"
        and evidence.get("source_grounded") is False
        and _text(evidence.get("publication_status")) == "audit_only"
        and _text(evidence.get("review_status")) == "acknowledged"
        and _text(current_provenance.get("verification_status")) == "needs_review"
        and current_provenance.get("source_grounded") is False
    ):
        return None, [], "", mapping
    after = copy.deepcopy(dict(evidence))
    after["mapping_status"] = "stale"
    after["verification_status"] = "needs_review"
    after["source_grounded"] = False
    after["publication_status"] = "audit_only"
    after["review_status"] = "acknowledged"
    after = _append_history(after, action="needs-review", reason_codes=reason_codes, note=note)
    provenance = dict(after.get("provenance") or {})
    provenance["verification_status"] = "needs_review"
    provenance["source_grounded"] = False
    after["provenance"] = provenance
    return after, reason_codes, note, mapping


def _entity_files(layout: Mapping[str, Path], key: str) -> list[Path]:
    root = _path(layout, key)
    return sorted(root.glob("*.json")) if root is not None and root.is_dir() else []


def _selected(
    payload: Mapping[str, Any],
    *,
    subjects: set[str],
    evidence_ids: set[str],
) -> bool:
    entity_id = _text(payload.get("evidence_id") or payload.get("claim_id"))
    if evidence_ids and entity_id not in evidence_ids:
        return False
    if subjects and _text(payload.get("subject")) not in subjects:
        return False
    return True


def _update_metadata(
    *,
    path: Path,
    payload: Mapping[str, Any],
    after: Mapping[str, Any],
    reason_codes: list[str],
    note: str,
    mapping: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "entity_id": _text(payload.get("evidence_id") or payload.get("claim_id")),
        "path": str(path),
        "action": "update",
        "reason_codes": list(reason_codes),
        "note": note,
        "classification_before": evidence_publication_decision(payload).get("classification")
        if payload.get("evidence_id")
        else claim_publication_decision(payload, {}).get("classification"),
        "classification_after": evidence_publication_decision(after).get("classification")
        if after.get("evidence_id")
        else "needs_review",
        "mapping": dict(mapping or {}),
    }


def plan_publication_repairs(
    *,
    layout: Mapping[str, Path],
    subjects: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create a zero-write repair plan for selected local records."""
    subject_set = {_text(item) for item in subjects or [] if _text(item)}
    id_set = {_text(item) for item in evidence_ids or [] if _text(item)}
    input_fingerprint = repair_input_fingerprint(layout=layout, subjects=subjects, evidence_ids=evidence_ids)
    evidence_items: list[dict[str, Any]] = []
    all_evidence_by_id: dict[str, dict[str, Any]] = {}
    selected_evidence_by_id: dict[str, dict[str, Any]] = {}
    for path in _entity_files(layout, "evidence"):
        payload = _load_object(path)
        if not payload:
            continue
        entity_id = _text(payload.get("evidence_id"))
        if entity_id:
            all_evidence_by_id[entity_id] = payload
        if not _selected(payload, subjects=subject_set, evidence_ids=id_set):
            continue
        if entity_id:
            selected_evidence_by_id[entity_id] = payload
        if _text(payload.get("origin_type")) in FORBIDDEN_EVIDENCE_ORIGIN_TYPES:
            after, reasons, note = _profile_hint_repair(payload)
            if not reasons:
                continue
            evidence_items.append(
                {
                    "path": path,
                    "before": payload,
                    "after": after,
                    "metadata": _update_metadata(path=path, payload=payload, after=after, reason_codes=reasons, note=note),
                }
            )
        elif _text(payload.get("origin_type")) == "pdf_page_ocr":
            after, reasons, note, mapping = _pdf_repair(layout, payload)
            if after is not None:
                evidence_items.append(
                    {
                        "path": path,
                        "before": payload,
                        "after": after,
                        "metadata": _update_metadata(path=path, payload=payload, after=after, reason_codes=reasons, note=note, mapping=mapping),
                    }
                )

    updated_evidence = dict(all_evidence_by_id)
    for item in evidence_items:
        entity_id = _text(item["after"].get("evidence_id"))
        if entity_id:
            updated_evidence[entity_id] = item["after"]

    claim_items: list[dict[str, Any]] = []
    for path in _entity_files(layout, "claims"):
        payload = _load_object(path)
        if not payload:
            continue
        claim_id = _text(payload.get("claim_id"))
        claim_matches_selection = not id_set or claim_id in id_set or bool(
            id_set.intersection({_text(item) for item in payload.get("evidence_ids", []) if _text(item)})
        )
        if not claim_matches_selection or (subject_set and _text(payload.get("subject")) not in subject_set):
            continue
        if _text(payload.get("status")) != "accepted":
            continue
        decision = claim_publication_decision(payload, updated_evidence)
        support_count = int(decision.get("support_count", 0) or 0)
        if decision.get("publishable") or support_count <= 0:
            continue
        if (
            _text(payload.get("status")) == "needs_review"
            and _text(payload.get("review_status")) == "needs_review"
            and _text(payload.get("publication_status")) == "audit_only"
        ):
            continue
        after = copy.deepcopy(payload)
        reason_codes = ["accepted-claim-support-needs-review", *list(decision.get("reason_codes", []) or [])]
        note = "accepted claim has non-publishable supporting evidence; status set to needs_review."
        after["status"] = "needs_review"
        after["review_status"] = "needs_review"
        after["publication_status"] = "audit_only"
        after = _append_history(after, action="needs-review", reason_codes=reason_codes, note=note)
        claim_items.append(
            {
                "path": path,
                "before": payload,
                "after": after,
                "metadata": _update_metadata(path=path, payload=payload, after=after, reason_codes=reason_codes, note=note),
            }
        )

    all_items = [*evidence_items, *claim_items]
    summary = {
        "selected_evidence_count": len(selected_evidence_by_id),
        "evidence_update_count": len(evidence_items),
        "claim_update_count": len(claim_items),
        "write_count": len(all_items),
        "by_action": {
            "audit-only": sum(1 for item in evidence_items if "forbidden-origin-type" in item["metadata"]["reason_codes"]),
            "mapping-repaired": sum(1 for item in evidence_items if "mapping-status-repaired" in item["metadata"]["reason_codes"]),
            "needs-review": sum(1 for item in all_items if "needs-review" in item["metadata"]["reason_codes"] or "accepted-claim-support-needs-review" in item["metadata"]["reason_codes"]),
        },
    }
    return {
        "schema_version": REPAIR_SCHEMA_VERSION,
        "mode": "preview",
        "input_fingerprint": input_fingerprint,
        "subjects": sorted(subject_set),
        "evidence_ids": sorted(id_set),
        "summary": summary,
        "evidence_updates": [item["metadata"] for item in evidence_items],
        "claim_updates": [item["metadata"] for item in claim_items],
        "_write_items": all_items,
    }


def _sha256_bytes(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write a byte-for-byte snapshot without going through JSON normalization."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.repair-tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _write_recovery_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    """Persist audit metadata independently from entity-write fault injection."""
    _atomic_write_bytes(
        path,
        json.dumps(dict(payload), ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _known_source_ids(layout: Mapping[str, Path]) -> set[str]:
    source_ids: set[str] = set()
    for key in ("sources", "manifest_sources", "evidence"):
        for path in _entity_files(layout, key):
            payload = _load_object(path)
            source_id = _text(payload.get("source_id"))
            if source_id:
                source_ids.add(source_id)
    return source_ids


def _formal_index_paths(layout: Mapping[str, Path]) -> list[tuple[Path, str]]:
    """Return every file a formal index refresh is allowed to create or rewrite.

    Index builders call ``ensure_kb_layout`` before building.  Its schema
    bootstrap is therefore part of this transaction too, including newly
    created schema files and the root schema-version record.
    """
    root = _path(layout, "root") or Path(".")
    index_root = _path(layout, "indexes") or (root / "indexes")
    paths: list[tuple[Path, str]] = [
        (index_root / filename, group)
        for group, filename in FORMAL_INDEX_FILES
    ]
    schema_root = _path(layout, "schemas") or (root / "schemas")
    paths.append((root / "schema-version.json", "runtime-schema"))
    from common import load_core_schema_templates

    paths.extend(
        (schema_root / name, "runtime-schema")
        for name in sorted(load_core_schema_templates())
    )

    review_root = _path(layout, "review_queues") or (root / "review-queues")
    source_ids = _known_source_ids(layout)
    for queue_name in FORMAL_INDEX_QUEUE_DIRS:
        queue_root = review_root / queue_name
        existing = sorted(queue_root.glob("*.json")) if queue_root.is_dir() else []
        paths.extend((path, f"review-queue:{queue_name}") for path in existing)
        paths.extend(
            (queue_root / f"{source_id}.json", f"review-queue:{queue_name}")
            for source_id in sorted(source_ids)
        )

    deduplicated: dict[Path, str] = {}
    for path, group in paths:
        deduplicated.setdefault(path.resolve(), group)
    return [(path, deduplicated[path]) for path in sorted(deduplicated)]


def _target_snapshot(
    *,
    root: Path,
    path: Path,
    target_type: str,
    target_group: str,
    entity_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = _safe_root_child(root, path)
    if target.exists() and not target.is_file():
        raise ValueError(f"repair target is not a regular file: {target}")
    exists = target.is_file()
    raw = target.read_bytes() if exists else b""
    root_abs = root.resolve()
    snapshot: dict[str, Any] = {
        "target_type": target_type,
        "target_group": target_group,
        "path": str(target.relative_to(root_abs)) if target.is_relative_to(root_abs) else str(target),
        "before_exists": exists,
        "before_sha256": hashlib.sha256(raw).hexdigest() if exists else "",
        "before_bytes_base64": base64.b64encode(raw).decode("ascii") if exists else "",
    }
    if target_type == "entity":
        payload = dict(entity_payload or {})
        snapshot.update(
            {
                "entity_type": target_group,
                "entity_id": _text(payload.get("evidence_id") or payload.get("claim_id")),
                "before": copy.deepcopy(payload),
            }
        )
    return snapshot


def _target_state(root: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    target = _safe_root_child(root, root / str(snapshot.get("path") or ""))
    if target.exists() and not target.is_file():
        raise ValueError(f"repair target is not a regular file: {target}")
    exists = target.is_file()
    raw = target.read_bytes() if exists else b""
    return {
        "exists": exists,
        "sha256": hashlib.sha256(raw).hexdigest() if exists else "",
    }


def _restore_target_snapshot(root: Path, snapshot: Mapping[str, Any]) -> str:
    target = _safe_root_child(root, root / str(snapshot.get("path") or ""))
    before_exists = snapshot.get("before_exists")
    if before_exists is None:
        before_exists = bool(snapshot.get("before_sha256")) or snapshot.get("before") is not None
    if not before_exists:
        if target.exists() or target.is_symlink():
            if not target.is_file() and not target.is_symlink():
                raise ValueError(f"cannot remove non-file repair target: {target}")
            target.unlink()
        return "removed"

    encoded = snapshot.get("before_bytes_base64")
    if encoded is not None:
        try:
            raw = base64.b64decode(str(encoded), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid recovery byte image for {target}") from exc
    elif snapshot.get("before") is not None:
        raw = json.dumps(snapshot["before"], ensure_ascii=False, indent=2).encode("utf-8")
    else:
        raise ValueError(f"recovery manifest has no before-image for {target}")
    _atomic_write_bytes(target, raw)
    return "restored"


def _rollback_target_snapshots(root: Path, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    restored = 0
    removed = 0
    errors: list[dict[str, str]] = []
    for snapshot in reversed(snapshots):
        try:
            action = _restore_target_snapshot(root, snapshot)
            if action == "removed":
                removed += 1
            else:
                restored += 1
        except BaseException as exc:
            errors.append(
                {
                    "path": str(snapshot.get("path") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "ok": not errors,
        "restored_count": restored,
        "removed_count": removed,
        "errors": errors,
    }


def repair_input_fingerprint(
    *,
    layout: Mapping[str, Path],
    subjects: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> str:
    """Hash the records and formal mapping inputs used by a plan."""
    subject_set = {_text(item) for item in subjects or [] if _text(item)}
    id_set = {_text(item) for item in evidence_ids or [] if _text(item)}
    rows: list[dict[str, str]] = []
    selected_claim_support_ids: set[str] = set()
    claims_root = _path(layout, "claims")
    for path in sorted(claims_root.glob("*.json")) if claims_root is not None and claims_root.is_dir() else []:
        payload = _load_object(path)
        if not payload:
            continue
        claim_id = _text(payload.get("claim_id"))
        supported_ids = {_text(item) for item in payload.get("evidence_ids", []) if _text(item)}
        selected = (not id_set or claim_id in id_set or bool(id_set.intersection(supported_ids))) and (
            not subject_set or _text(payload.get("subject")) in subject_set
        )
        if selected:
            selected_claim_support_ids.update(supported_ids)
    for key in ("evidence", "claims"):
        for path in _entity_files(layout, key):
            payload = _load_object(path)
            if not payload:
                continue
            if key == "evidence":
                selected = _selected(payload, subjects=subject_set, evidence_ids=id_set) or (
                    _text(payload.get("evidence_id")) in selected_claim_support_ids
                )
            else:
                claim_id = _text(payload.get("claim_id"))
                supported_ids = {_text(item) for item in payload.get("evidence_ids", []) if _text(item)}
                selected = (not id_set or claim_id in id_set or bool(id_set.intersection(supported_ids))) and (
                    not subject_set or _text(payload.get("subject")) in subject_set
                )
            if selected:
                rows.append({"kind": key, "name": path.name, "sha256": _sha256_bytes(path)})
    source_ids = set()
    for path in _entity_files(layout, "evidence"):
        payload = _load_object(path)
        if payload and (
            _selected(payload, subjects=subject_set, evidence_ids=id_set)
            or _text(payload.get("evidence_id")) in selected_claim_support_ids
        ) and _text(payload.get("origin_type")) == "pdf_page_ocr":
            source_id = _text(payload.get("source_id"))
            if source_id:
                source_ids.add(source_id)
    for key in ("sources", "manifest_sources"):
        root = _path(layout, key)
        if root is None:
            continue
        for source_id in sorted(source_ids):
            path = root / f"{source_id}.json"
            if path.is_file():
                rows.append({"kind": key, "name": path.name, "sha256": _sha256_bytes(path)})
    queue_root = _path(layout, "review_queues")
    if queue_root is not None:
        for source_id in sorted(source_ids):
            path = queue_root / "pdf-page-review" / f"{source_id}.json"
            if path.is_file():
                rows.append({"kind": "pdf-page-review", "name": path.name, "sha256": _sha256_bytes(path)})
    encoded = json.dumps(
        sorted(rows, key=lambda item: (item["kind"], item["name"])),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _recovery_manifest_path(layout: Mapping[str, Path]) -> Path:
    root = _path(layout, "review_queues") or Path("review-queues")
    recovery_dir = root / REPAIR_QUEUE_NAME
    recovery_dir.mkdir(parents=True, exist_ok=True)
    return recovery_dir / f"repair-{_now_iso().replace(':', '').replace('+', '-')}-{uuid4().hex[:8]}.json"


def _safe_root_child(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"repair target escapes KB root: {candidate}")
    return resolved_candidate


def refresh_formal_indexes(layout: Mapping[str, Path]) -> dict[str, Any]:
    """Refresh every index consumed by page-grounded retrieval.

    The builders are intentionally run as their normal commands so their
    existing input fingerprints and output contracts remain authoritative.
    A failed page-locator step does not prevent the later search/index steps
    from running; the caller receives the complete failure list.
    """
    from common import preferred_python_executable, run_utf8_subprocess, runtime_subprocess_env

    root = _path(layout, "root")
    if root is None:
        evidence_root = _path(layout, "evidence")
        root = evidence_root.parent if evidence_root is not None else Path(".")
    scripts_root = Path(__file__).resolve().parents[2]
    steps = [
        "build_page_locator_index.py",
        "build_exercise_locator_index.py",
        "build_search_index.py",
        "build_book_series_indexes.py",
    ]
    completed: list[str] = []
    failed: list[dict[str, str]] = []
    env = runtime_subprocess_env(kb_root_override=root)
    for name in steps:
        try:
            run_utf8_subprocess(
                [preferred_python_executable(), str(scripts_root / name), "--format", "quiet"],
                command_label=f"python:{name}",
                check=True,
                env=env,
            )
            completed.append(name.removesuffix(".py"))
        except (Exception, SystemExit) as exc:
            failed.append({"step": name.removesuffix(".py"), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ok": not failed,
        "steps": [name.removesuffix(".py") for name in steps],
        "completed_steps": completed,
        "failed_steps": failed,
    }


def apply_publication_repairs(
    *,
    layout: Mapping[str, Path],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Apply one plan as a transaction over entities and formal index outputs."""
    expected_fingerprint = _text(plan.get("input_fingerprint"))
    if not expected_fingerprint:
        raise ValueError("repair plan input fingerprint is required before --yes")
    current_fingerprint = repair_input_fingerprint(
        layout=layout,
        subjects=list(plan.get("subjects") or []),
        evidence_ids=list(plan.get("evidence_ids") or []),
    )
    if current_fingerprint != expected_fingerprint:
        raise ValueError("repair plan input fingerprint changed; rerun preview before --yes")
    items = list(plan.get("_write_items", []) or [])
    root = _path(layout, "root") or Path(".")
    snapshots: list[dict[str, Any]] = []
    seen_targets: set[Path] = set()
    for item in items:
        path = _safe_root_child(root, Path(item["path"]))
        if path in seen_targets:
            raise ValueError(f"duplicate repair target: {path}")
        seen_targets.add(path)
        before = item.get("before") if isinstance(item.get("before"), dict) else {}
        target_group = "evidence" if before.get("evidence_id") else "claim"
        snapshots.append(
            _target_snapshot(
                root=root,
                path=path,
                target_type="entity",
                target_group=target_group,
                entity_payload=before,
            )
        )
    for path, target_group in _formal_index_paths(layout):
        safe_path = _safe_root_child(root, path)
        if safe_path in seen_targets:
            continue
        seen_targets.add(safe_path)
        snapshots.append(
            _target_snapshot(
                root=root,
                path=safe_path,
                target_type="formal-index",
                target_group=target_group,
            )
        )

    recovery_path = _recovery_manifest_path(layout)
    recovery: dict[str, Any] = {
        "schema_version": REPAIR_SCHEMA_VERSION,
        "status": "prepared",
        "transaction": "entities-and-formal-indexes",
        "created_at": _now_iso(),
        "input_fingerprint": expected_fingerprint,
        "recoverable": True,
        "item_count": len(snapshots),
        "entity_item_count": sum(item["target_type"] == "entity" for item in snapshots),
        "formal_index_item_count": sum(item["target_type"] == "formal-index" for item in snapshots),
        "items": snapshots,
    }
    _write_recovery_manifest(recovery_path, recovery)

    def failure_result(
        *,
        stage: str,
        error: str,
        index_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rollback = _rollback_target_snapshots(root, snapshots)
        recovery["status"] = "rolled_back" if rollback["ok"] else "rollback_failed"
        recovery["rolled_back_at"] = _now_iso()
        recovery["failure"] = {"stage": stage, "error": error}
        recovery["rollback"] = rollback
        if index_result is not None:
            recovery["index_refresh"] = index_result
        try:
            _write_recovery_manifest(recovery_path, recovery)
        except BaseException as manifest_exc:
            recovery["manifest_update_error"] = f"{type(manifest_exc).__name__}: {manifest_exc}"
        return {
            "schema_version": REPAIR_SCHEMA_VERSION,
            "mode": "apply",
            "ok": False,
            "status": recovery["status"],
            "error": f"publication repair rolled back after {stage} failure: {error}",
            "failure": dict(recovery["failure"]),
            "rollback": rollback,
            "summary": dict(plan.get("summary") or {}),
            "write_count": len(items),
            "recovery_manifest": str(recovery_path),
            "index_refresh": index_result,
        }

    for item in items:
        try:
            save_json(_safe_root_child(root, Path(item["path"])), item["after"], ignored_compare_keys=())
        except BaseException as exc:
            return failure_result(
                stage="entity-write",
                error=f"{type(exc).__name__}: {exc}",
            )

    try:
        index_result = refresh_formal_indexes(layout)
    except BaseException as exc:
        index_result = {
            "ok": False,
            "steps": [],
            "completed_steps": [],
            "failed_steps": [
                {"step": "refresh_formal_indexes", "error": f"{type(exc).__name__}: {exc}"}
            ],
        }
    if not isinstance(index_result, dict) or not index_result.get("ok", False):
        detail = "formal index refresh returned failure"
        if isinstance(index_result, dict) and index_result.get("failed_steps"):
            detail = json.dumps(index_result["failed_steps"], ensure_ascii=False, sort_keys=True)
        return failure_result(stage="index-refresh", error=detail, index_result=index_result if isinstance(index_result, dict) else None)

    try:
        for snapshot in snapshots:
            after_state = _target_state(root, snapshot)
            snapshot.update({"after_exists": after_state["exists"], "after_sha256": after_state["sha256"]})
        recovery["status"] = "applied"
        recovery["applied_at"] = _now_iso()
        recovery["index_refresh"] = index_result
        _write_recovery_manifest(recovery_path, recovery)
    except BaseException as exc:
        return failure_result(
            stage="recovery-finalize",
            error=f"{type(exc).__name__}: {exc}",
            index_result=index_result,
        )

    return {
        "schema_version": REPAIR_SCHEMA_VERSION,
        "mode": "apply",
        "ok": True,
        "status": "applied",
        "summary": dict(plan.get("summary") or {}),
        "write_count": len(items),
        "recovery_manifest": str(recovery_path),
        "index_refresh": index_result,
    }


def restore_publication_repair(
    *,
    layout: Mapping[str, Path],
    recovery_manifest: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    """Restore a repair from its before-images, with an after-image guard."""
    path = Path(recovery_manifest)
    payload = _load_object(path)
    items = [item for item in payload.get("items", []) or [] if isinstance(item, dict)]
    root = _path(layout, "root") or Path(".")
    if not apply:
        return {"mode": "restore-preview", "write_count": len(items), "recovery_manifest": str(path)}
    for item in items:
        target = _safe_root_child(root, root / str(item.get("path") or ""))
        state = _target_state(root, item)
        expected_after_exists = item.get("after_exists")
        expected_after = _text(item.get("after_sha256"))
        if expected_after_exists is not None and state["exists"] != bool(expected_after_exists):
            raise ValueError(f"recovery target changed after repair: {target}")
        if expected_after and state["sha256"] != expected_after:
            raise ValueError(f"recovery target changed after repair: {target}")
    for item in items:
        _restore_target_snapshot(root, item)
    index_result = refresh_formal_indexes(layout)
    payload["status"] = "restored"
    payload["restored_at"] = _now_iso()
    payload["index_refresh_after_restore"] = index_result
    _write_recovery_manifest(path, payload)
    return {
        "mode": "restore",
        "write_count": len(items),
        "recovery_manifest": str(path),
        "index_refresh": index_result,
    }


def public_repair_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove internal before/after payloads before CLI serialization."""
    result = {key: value for key, value in payload.items() if key != "_write_items"}
    return result


def repair_publication(
    *,
    layout: Mapping[str, Path],
    subjects: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    apply: bool = False,
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    if apply and not _text(expected_fingerprint):
        raise ValueError("expected plan fingerprint is required before applying publication repairs")
    plan = plan_publication_repairs(layout=layout, subjects=subjects, evidence_ids=evidence_ids)
    if expected_fingerprint and expected_fingerprint != plan.get("input_fingerprint"):
        raise ValueError("requested plan fingerprint does not match current preview")
    if not apply:
        return public_repair_result(plan)
    return apply_publication_repairs(layout=layout, plan=plan)
