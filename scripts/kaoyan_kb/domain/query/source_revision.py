"""Bind saved answers to the source records used during answer assembly."""
import hashlib
import json
from common import kb_layout
from .read_session import read_json


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def dependencies(contract):
    ids = {str(item.get("evidence_id") or "") for item in contract.get("citations", [])}
    ids.update(str(item.get("evidence_id") or "") for item in contract.get("references", []))
    result = [("evidence", value) for value in sorted(ids) if value]
    result.extend(("claims", str(item["claim_id"])) for item in contract.get("claim_hits", []) if item.get("claim_id"))
    return sorted(set(result))


def source_path(kind, identifier):
    if not identifier or any(char in identifier for char in '/\\:') or identifier in {".", ".."}:
        raise ValueError("invalid source identifier")
    return kb_layout()[kind] / (identifier + ".json")


def capture(contract):
    revisions = {}
    for kind, identifier in dependencies(contract):
        try:
            revisions[kind + "/" + identifier] = digest(read_json(source_path(kind, identifier)))
        except (OSError, ValueError):
            revisions[kind + "/" + identifier] = ""
    return revisions


def verify(contract):
    expected = contract.get("source_revisions") or {}
    required = dependencies(contract)
    if not required:
        raise ValueError("缺少可复核的来源，不能保存。")
    for kind, identifier in required:
        key = kind + "/" + identifier
        try:
            # Deliberately bypass the query cache before any learning-state write.
            actual = digest(json.loads(source_path(kind, identifier).read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise ValueError("来源已缺失或不可读，请重新查询后保存：" + identifier) from exc
        if not expected.get(key) or expected[key] != actual:
            raise ValueError("来源版本未确认或已变化，请重新查询后保存：" + identifier)
