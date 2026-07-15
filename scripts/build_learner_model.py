#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from common import learner_file_map, save_json
from kaoyan_kb.domain.learner_model import LEARNER_MODEL_CONTRACT_VERSION, build_learner_model_payload
from learner_events import load_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--format", choices=("json", "quiet"), default="json")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    events = load_events()
    model = build_learner_model_payload(events, subject_filter=args.subject)
    save_json(learner_file_map()["learner_model"], model)
    result = {"subjects_updated": sorted(model.get("subjects", {}).keys()), "updated_at": model.get("updated_at", "")}
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
