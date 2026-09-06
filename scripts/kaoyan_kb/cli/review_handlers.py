from __future__ import annotations

from typing import Any
from common import learner_file_map, load_json_or_default, now_iso, save_json


def _review_refinement_decide(refinement_id: str, status: str, note: str) -> dict[str, Any]:
    files = learner_file_map()
    payload = load_json_or_default(files["refinement_queue"], {"updated_at": "", "items": []})
    updated = False
    review_at = now_iso()
    allowed_transitions = {
        "open": {"accepted", "rejected"},
        "accepted": {"implemented", "rejected"},
        "implemented": {"verified", "rejected"},
        "verified": {"verified"},
        "rejected": {"rejected"},
    }
    for item in payload.get("items", []):
        if item.get("refinement_id") != refinement_id:
            continue
        current_status = str(item.get("status", "open")).strip() or "open"
        if status != current_status and status not in allowed_transitions.get(current_status, set()):
            raise SystemExit(f"invalid refinement lifecycle transition: {current_status} -> {status}")
        history = list(item.get("review_history", []))
        history.append({"status": status, "note": note, "at": review_at})
        item["status"] = status
        item["updated_at"] = review_at
        item["review_history"] = history
        updated = True
        break
    if not updated:
        raise SystemExit(f"refinement not found: {refinement_id}")
    payload["updated_at"] = review_at
    save_json(files["refinement_queue"], payload)
    return {
        "updated": True,
        "refinement_id": refinement_id,
        "status": status,
        "reviewed_at": review_at,
        "cli_write_scope": "refinement_queue",
        "learner_layer_only": True,
    }
