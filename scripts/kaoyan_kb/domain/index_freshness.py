from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from common import sha256_for_file, stable_fingerprint


INDEX_INPUT_FINGERPRINT_VERSION = "index-inputs.v1"


def fingerprint_index_inputs(
    *,
    files: Iterable[tuple[str, Path]],
    projections: Iterable[tuple[str, Any]] = (),
    metadata: dict[str, Any] | None = None,
) -> str:
    """Fingerprint an index's complete logical inputs, including missing expected files."""
    file_records: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for raw_label, raw_path in sorted(files, key=lambda item: item[0]):
        label = str(raw_label)
        if label in seen_labels:
            raise ValueError(f"duplicate index input label: {label}")
        seen_labels.add(label)
        path = Path(raw_path)
        file_records.append(
            {
                "label": label,
                "exists": path.is_file(),
                "sha256": sha256_for_file(path) if path.is_file() else "",
            }
        )
    projection_records = [
        {"label": str(label), "payload": payload}
        for label, payload in sorted(projections, key=lambda item: item[0])
    ]
    return stable_fingerprint(
        {
            "version": INDEX_INPUT_FINGERPRINT_VERSION,
            "files": file_records,
            "projections": projection_records,
            "metadata": dict(metadata or {}),
        }
    )


def directory_json_inputs(label: str, root: Path) -> list[tuple[str, Path]]:
    return [
        (f"{label}/{path.name}", path)
        for path in sorted(root.glob("*.json"))
    ] if root.is_dir() else []
