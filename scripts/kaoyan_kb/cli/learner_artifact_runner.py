"""Shared output lifecycle for explicitly invoked learner artifact builders."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from collections.abc import Callable

from common import INDEX_DIRNAME, save_json, save_text


def run_artifact(
    parse_args: Callable[[], argparse.Namespace],
    build: Callable[[Path, argparse.Namespace], dict[str, Any]],
    render: Callable[[dict[str, Any]], str],
    json_filename: str,
    markdown_filename: str,
    result_fields: tuple[str, ...],
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    index_root = Path(args.vault_root) / INDEX_DIRNAME
    index_root.mkdir(parents=True, exist_ok=True)
    payload = build(index_root, args)
    save_json(index_root / json_filename, payload)
    save_text(index_root / markdown_filename, render(payload))
    result = {key: payload[key] for key in result_fields}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
