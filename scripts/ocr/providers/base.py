from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class OCRProviderRequest:
    file_path: Path
    pages: str
    model: str
    include_blocks: bool
    confidence_granularity: str
    table_format: str
    extract_header: bool
    extract_footer: bool


class OCRProvider(Protocol):
    name: str

    def run(self, request: OCRProviderRequest) -> dict[str, Any]:
        ...
