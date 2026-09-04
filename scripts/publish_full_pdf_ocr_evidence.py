#!/usr/bin/env python3
"""Removed compatibility entrypoint for the former full-PDF publisher.

The old script published PDF pages through an unbounded side route.  It is kept
as a failing shim so callers receive a deterministic migration message instead
of silently bypassing the maintained review and hash gates.
"""
from __future__ import annotations

import sys


MESSAGE = (
    "[ERROR] publish_full_pdf_ocr_evidence.py is no longer supported; use "
    "kb.py book pdf-ocr-publish (preview first, then --yes with --plan-fingerprint)."
)


def main() -> int:
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
