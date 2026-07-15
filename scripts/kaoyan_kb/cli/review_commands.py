from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any


def add_review_commands(
    subparsers: argparse._SubParsersAction,
    *,
    formatter_class: type[argparse.HelpFormatter] | None = None,
) -> None:
    parser_options: dict[str, object] = {}
    if formatter_class is not None:
        parser_options["formatter_class"] = formatter_class
    review = subparsers.add_parser(
        "review",
        help="work with evidence, conflict, and refinement review queues",
        description="Work with evidence, conflict, and refinement review queues.",
        **parser_options,
    )
    review_sub = review.add_subparsers(dest="review_command", required=True)
    evidence = review_sub.add_parser("evidence")
    evidence_sub = evidence.add_subparsers(dest="review_evidence_command", required=True)
    evidence_queue = evidence_sub.add_parser("queue")
    evidence_queue.add_argument("--subject")
    evidence_queue.add_argument("--chapter-id")
    evidence_queue.add_argument("--format", choices=("json", "quiet"), default="json")
    evidence_decide = evidence_sub.add_parser("decide")
    evidence_decide.add_argument("--evidence-id", required=True)
    evidence_decide.add_argument("--decision", choices=("accept", "reject", "acknowledge-stale"), required=True)
    evidence_decide.add_argument("--note", default="")
    evidence_decide.add_argument("--format", choices=("json", "quiet"), default="json")

    conflicts = review_sub.add_parser("conflicts")
    conflicts_sub = conflicts.add_subparsers(dest="review_conflicts_command", required=True)
    conflicts_queue = conflicts_sub.add_parser("queue")
    conflicts_queue.add_argument("--subject")
    conflicts_queue.add_argument("--chapter-id")
    conflicts_queue.add_argument("--format", choices=("json", "quiet"), default="json")
    conflicts_decide = conflicts_sub.add_parser("decide")
    conflicts_decide.add_argument("--relation-id", required=True)
    conflicts_decide.add_argument("--decision", choices=("keep-both", "prefer-left", "prefer-right", "mark-uncertain"), required=True)
    conflicts_decide.add_argument("--note", default="")
    conflicts_decide.add_argument("--format", choices=("json", "quiet"), default="json")

    refinement = review_sub.add_parser("refinement")
    refinement_sub = refinement.add_subparsers(dest="review_refinement_command", required=True)
    refinement_queue = refinement_sub.add_parser("queue")
    refinement_queue.add_argument("--subject")
    refinement_queue.add_argument("--chapter")
    refinement_queue.add_argument("--topn", type=int, default=20)
    refinement_queue.add_argument("--format", choices=("json", "quiet"), default="json")
    refinement_decide = refinement_sub.add_parser("decide")
    refinement_decide.add_argument("--refinement-id", required=True)
    refinement_decide.add_argument("--status", choices=("accepted", "implemented", "verified", "rejected"), required=True)
    refinement_decide.add_argument("--note", default="")
    refinement_decide.add_argument("--format", choices=("json", "quiet"), default="json")


def dispatch_review(
    args: argparse.Namespace,
    run_script: Callable[..., str],
    refinement_decide: Callable[[str, str, str], dict[str, Any]],
) -> str | None:
    if args.command != "review":
        return None
    if args.review_command == "evidence":
        if args.review_evidence_command == "queue":
            forwarded = ["queue", "--format", args.format]
            if args.subject:
                forwarded.extend(["--subject", args.subject])
            if args.chapter_id:
                forwarded.extend(["--chapter-id", args.chapter_id])
            return run_script("review_evidence.py", *forwarded)
        return run_script("review_evidence.py", "decide", "--evidence-id", args.evidence_id, "--decision", args.decision, "--note", args.note, "--format", args.format)
    if args.review_command == "conflicts":
        if args.review_conflicts_command == "queue":
            forwarded = ["queue", "--format", args.format]
            if args.subject:
                forwarded.extend(["--subject", args.subject])
            if args.chapter_id:
                forwarded.extend(["--chapter-id", args.chapter_id])
            return run_script("review_conflicts.py", *forwarded)
        return run_script("review_conflicts.py", "decide", "--relation-id", args.relation_id, "--decision", args.decision, "--note", args.note, "--format", args.format)
    if args.review_command == "refinement":
        if args.review_refinement_command == "queue":
            forwarded = ["--topn", str(args.topn), "--format", args.format]
            if args.subject:
                forwarded.extend(["--subject", args.subject])
            if args.chapter:
                forwarded.extend(["--chapter", args.chapter])
            return run_script("build_refinement_queue.py", *forwarded)
        payload = refinement_decide(args.refinement_id, args.status, args.note)
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else ""
    return None
