#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from build_syllabus_registry import DEFAULT_DEFINITIONS_ROOT, SUBJECT_FILE_PREFIX, definition_candidates, definitions_root_from_arg
from common import now_iso, resolve_subject, save_json, validate_entity_contract


SCAFFOLD_TEMPLATES = {
    "数学": {
        "node_id": "SYL-MATH-SCAFFOLD-001",
        "title": "数学考纲脚手架节点",
        "aliases": ["数学脚手架"],
        "keywords": ["数学", "脚手架"],
    },
    "408": {
        "node_id": "SYL-408-SCAFFOLD-001",
        "title": "408考纲脚手架节点",
        "aliases": ["408脚手架"],
        "keywords": ["408", "脚手架"],
    },
    "英语": {
        "node_id": "SYL-ENG-SCAFFOLD-001",
        "title": "英语考纲脚手架节点",
        "aliases": ["英语脚手架"],
        "keywords": ["英语", "脚手架"],
    },
    "政治": {
        "node_id": "SYL-POL-SCAFFOLD-001",
        "title": "政治考纲脚手架节点",
        "aliases": ["政治脚手架"],
        "keywords": ["政治", "脚手架"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--format", choices=("json", "quiet"), default="json")

    scaffold = subparsers.add_parser("scaffold")
    scaffold.add_argument("--subject", required=True)
    scaffold.add_argument("--definitions-root", default=str(DEFAULT_DEFINITIONS_ROOT))
    scaffold.add_argument("--force", action="store_true")
    scaffold.add_argument("--format", choices=("json", "quiet"), default="json")

    import_cmd = subparsers.add_parser("import")
    import_cmd.add_argument("--input", required=True)
    import_cmd.add_argument("--definitions-root", default=str(DEFAULT_DEFINITIONS_ROOT))
    import_cmd.add_argument("--force", action="store_true")
    import_cmd.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_definition(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    subject = str(payload.get("subject", "")).strip()
    if not subject:
        errors.append("missing subject")
    else:
        try:
            resolve_subject(subject)
        except SystemExit:
            errors.append(f"unsupported subject: {subject}")
    if not str(payload.get("definition_version", "")).strip():
        errors.append("missing definition_version")
    if str(payload.get("source_status", "")).strip() not in {"scaffold", "manual", "official"}:
        errors.append("source_status must be one of scaffold/manual/official")
    if "mapping_overrides" not in payload or not isinstance(payload.get("mapping_overrides"), list):
        errors.append("mapping_overrides must be a list")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
    else:
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                errors.append(f"node[{index}] must be an object")
                continue
            for key in ("node_id", "title"):
                if not str(node.get(key, "")).strip():
                    errors.append(f"node[{index}] missing {key}")
            for key in ("aliases", "keywords", "children"):
                if not isinstance(node.get(key, []), list):
                    errors.append(f"node[{index}] {key} must be a list")
    return errors


def target_path(subject: str, source_status: str, definitions_root: Path) -> Path:
    prefix = SUBJECT_FILE_PREFIX[subject]
    return definitions_root / f"{prefix}.{source_status}.json"


def scaffold_definition(subject: str) -> dict[str, Any]:
    template = SCAFFOLD_TEMPLATES[subject]
    return {
        "subject": subject,
        "definition_version": f"{now_iso()[:10]}-scaffold-v1",
        "source_status": "scaffold",
        "mapping_overrides": [],
        "nodes": [
            {
                "node_id": template["node_id"],
                "title": template["title"],
                "aliases": list(template["aliases"]),
                "keywords": list(template["keywords"]),
                "children": [],
            }
        ],
    }


def emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    if args.command == "validate":
        input_path = Path(args.input)
        payload = load_json(input_path)
        errors = validate_definition(payload)
        emit(
            {
                "valid": not errors,
                "input": str(input_path),
                "subject": payload.get("subject", ""),
                "source_status": payload.get("source_status", ""),
                "node_count": len(payload.get("nodes", [])) if isinstance(payload.get("nodes", []), list) else 0,
                "errors": errors,
            },
            args.format,
        )
        return 0 if not errors else 1

    if args.command == "scaffold":
        subject, _ = resolve_subject(args.subject)
        definitions_root = definitions_root_from_arg(args.definitions_root)
        path = target_path(subject, "scaffold", definitions_root)
        created = False
        if not path.exists() or args.force:
            save_json(path, scaffold_definition(subject), ignored_compare_keys=())
            created = True
        emit(
            {
                "created": created,
                "path": str(path),
                "subject": subject,
                "source_status": "scaffold",
            },
            args.format,
        )
        return 0

    input_path = Path(args.input)
    payload = load_json(input_path)
    errors = validate_definition(payload)
    if errors:
        emit(
            {
                "imported": False,
                "reason": "validation_failed",
                "errors": errors,
                "path": str(input_path),
            },
            args.format,
        )
        return 1
    try:
        validate_entity_contract("syllabus_definition", payload)
    except ValueError as exc:
        emit(
            {"imported": False, "reason": "schema_validation_failed", "errors": [str(exc)], "path": str(input_path)},
            args.format,
        )
        return 1

    subject, _ = resolve_subject(str(payload["subject"]))
    definitions_root = definitions_root_from_arg(args.definitions_root)
    source_status = str(payload["source_status"]).strip()
    destination = target_path(subject, source_status, definitions_root)
    if destination.exists() and not args.force:
        emit(
            {
                "imported": False,
                "reason": "target_exists",
                "path": str(destination),
                "subject": subject,
                "source_status": source_status,
            },
            args.format,
        )
        return 0

    save_json(destination, payload, ignored_compare_keys=())
    emit(
        {
            "imported": True,
            "path": str(destination),
            "subject": subject,
            "source_status": source_status,
            "active_candidates": [str(path) for path in definition_candidates(subject, definitions_root)],
        },
        args.format,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
