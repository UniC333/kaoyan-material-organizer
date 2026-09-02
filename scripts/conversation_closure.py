#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import default_vault_root_arg
from kaoyan_kb.domain.conversation_closure import (
    ClosureValidationError,
    discover_local_tasks,
    load_manifest,
    probe_local_indexes,
    validate_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate bounded cross-conversation closure inputs.")
    commands = parser.add_subparsers(dest="action", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--manifest-json", required=True)
    validate.add_argument("--vault-root", default=default_vault_root_arg())
    validate.add_argument("--codex-home")
    validate.add_argument("--format", choices=("json", "quiet"), default="json")

    probe = commands.add_parser("probe-local-indexes")
    probe.add_argument("--codex-home")
    probe.add_argument("--format", choices=("json", "quiet"), default="json")

    discover = commands.add_parser("discover-local")
    discover.add_argument("--after", required=True)
    discover.add_argument("--through", required=True)
    discover.add_argument("--codex-home")
    discover.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    args = parse_args()
    try:
        if args.action == "validate":
            manifest = load_manifest(Path(args.manifest_json))
            fallback = dict(dict(manifest.get("discovery") or {}).get("local_fallback") or {})
            local_probe = (
                probe_local_indexes(Path(args.codex_home) if args.codex_home else None)
                if fallback.get("schema_status") == "supported"
                else None
            )
            result = validate_manifest(
                manifest,
                vault_root=Path(args.vault_root),
                local_index_probe=local_probe,
            )
        elif args.action == "probe-local-indexes":
            result = probe_local_indexes(Path(args.codex_home) if args.codex_home else None)
        else:
            result = discover_local_tasks(
                after=args.after,
                through=args.through,
                codex_home=Path(args.codex_home) if args.codex_home else None,
            )
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ClosureValidationError as exc:
        print(f"[ERROR] {exc}; no learning state was written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
