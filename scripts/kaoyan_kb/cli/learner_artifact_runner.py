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
    payload = build(index_root, args)
    markdown = render(payload)
    result = {key: payload[key] for key in result_fields}
    output = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else None
    _write_artifact_pair(index_root / json_filename, index_root / markdown_filename, payload, markdown)
    if output is not None:
        print(output)
    return 0


def _write_artifact_pair(json_path: Path, markdown_path: Path, payload: dict[str, Any], markdown: str) -> None:
    paths = (json_path, markdown_path)
    snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
    missing_dirs: set[Path] = set()
    for path in paths:
        parent = path.parent
        while not parent.exists():
            missing_dirs.add(parent)
            parent = parent.parent
    try:
        save_json(json_path, payload)
        save_text(markdown_path, markdown)
    except BaseException as original:
        errors = []
        for path, previous in snapshots.items():
            try:
                if previous is None:
                    if path.exists():
                        path.unlink()
                elif not path.is_file() or path.read_bytes() != previous:
                    path.write_bytes(previous)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        for directory in sorted(missing_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                if directory.is_dir():
                    directory.rmdir()
            except OSError as exc:
                errors.append(f"{directory}: {exc}")
        if errors:
            raise OSError("artifact pair rollback failed: " + "; ".join(errors)) from original
        raise
