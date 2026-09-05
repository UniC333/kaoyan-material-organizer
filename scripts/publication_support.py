"""Shared safety primitives for the two maintained OCR publication routes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from common import kb_layout, sha256_for_file, stable_fingerprint


PUBLICATION_PLAN_VERSION = "ocr-publication-plan.v1"


def read_only_layout(kb_root: Path) -> dict[str, Path]:
    """Resolve KB paths without creating directories or updating schema files."""
    return kb_layout(root=kb_root)


def file_input(label: str, path: Path) -> dict[str, Any]:
    """Return a deterministic signature for an expected file, including absence."""
    path = Path(path)
    if path.is_file():
        return {"label": str(label), "path": str(path), "exists": True, "sha256": sha256_for_file(path)}
    return {"label": str(label), "path": str(path), "exists": False, "sha256": ""}


def fingerprint_inputs(records: Iterable[dict[str, Any]]) -> str:
    return stable_fingerprint({"version": PUBLICATION_PLAN_VERSION, "inputs": list(records)})


def publication_plan_fingerprint(payload: dict[str, Any]) -> str:
    """Hash only stable plan data; timestamps and display-only fields are excluded."""
    return stable_fingerprint(
        {
            "version": PUBLICATION_PLAN_VERSION,
            "publication_kind": payload.get("publication_kind", ""),
            "arguments": payload.get("arguments", {}),
            "inputs": payload.get("inputs", []),
            "items": payload.get("items", []),
            "blocked": payload.get("blocked", []),
            "global_blockers": payload.get("global_blockers", []),
            "writes": payload.get("writes", {}),
        }
    )


def publication_transaction_roots(kb_root: Path) -> list[Path]:
    """Return the exact KB state that publication and index refresh may mutate."""
    root = Path(kb_root)
    return [
        root / "evidence",
        root / "indexes" / "page_locator_index.json",
        root / "indexes" / "exercise_locator_index.json",
        root / "indexes" / "search_manifest.json",
        root / "indexes" / "search_documents.json",
        root / "indexes" / "inverted_index.json",
        root / "indexes" / "book_series_index.json",
        root / "indexes" / "exercise_pair_index.json",
        root / "indexes" / "id_counters.json",
        root / "review-queues" / "pdf-page-mapping",
        root / "review-queues" / "exercise-locator",
        root / "schemas",
        root / "schema-version.json",
    ]


class FileTransaction:
    """Snapshot selected files and restore them if any later write fails."""

    def __init__(self, roots: Iterable[Path]) -> None:
        self.roots = [Path(root) for root in roots]
        self._before = self._snapshot()
        self._closed = False

    def _files(self) -> set[Path]:
        files: set[Path] = set()
        for root in self.roots:
            if root.is_file():
                files.add(root)
            elif root.is_dir():
                files.update(path for path in root.rglob("*") if path.is_file())
        return files

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self._files()}

    def restore(self) -> None:
        if self._closed:
            return
        current = self._files()
        for path in sorted(current - set(self._before), key=lambda item: len(item.parts), reverse=True):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for path, content in self._before.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.restore.tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
        self._closed = True

    def commit(self) -> None:
        self._closed = True


def require_matching_plan_fingerprint(expected: str | None, actual: str) -> None:
    if not expected:
        raise SystemExit("[ERROR] --yes requires --plan-fingerprint from a fresh zero-write preview")
    if expected != actual:
        raise SystemExit(
            "[ERROR] publication plan fingerprint changed; inputs changed after preview, regenerate the plan"
        )
