from __future__ import annotations

import argparse
from collections.abc import Callable


def add_learner_commands(subparsers: argparse._SubParsersAction, *, formatter_class: type[argparse.HelpFormatter] | None = None) -> None:
    opts = {} if formatter_class is None else {"formatter_class": formatter_class}
    learner = subparsers.add_parser("learner", help="build learner-layer artifacts and tutoring packets", description="Build learner-layer artifacts and tutoring packets.", **opts)
    commands = learner.add_subparsers(dest="learner_command", required=True)

    def artifact(name: str, *, date: bool = True, vault: bool = True, extra: tuple[tuple[str, dict], ...] = ()) -> argparse.ArgumentParser:
        item = commands.add_parser(name)
        if vault:
            item.add_argument("--vault-root")
        if date:
            item.add_argument("--plan-date", required=True)
        for flag, kwargs in extra:
            item.add_argument(flag, **kwargs)
        item.add_argument("--format", choices=("json", "quiet"), default="json")
        return item

    artifact("orchestration-context", date=False, extra=(("--as-of", {}), ("--freshness-days", {"type": int, "default": 14})))
    artifact("daily-card")
    artifact("review-followups")
    artifact("weekly-orchestration", extra=(("--override-json", {}),))
    artifact("teacher-loop-artifact")
    artifact("adaptive-coaching-context", extra=(("--stale-signal-days", {"type": int, "default": 21}),))
    artifact("adaptive-coaching-packet")
    artifact("coaching-feedback-loop")
    artifact("closed-loop-operations", extra=(("--override-json", {}),))
    artifact("adaptive-coaching-artifact")
    artifact("longitudinal-tutoring-context", extra=(("--stale-cycle-days", {"type": int, "default": 14}),))
    artifact("tutoring-strategy-packet")
    artifact("tutoring-feedback-loop")
    artifact("long-horizon-operations", extra=(("--override-json", {}),))
    artifact("longitudinal-tutoring-artifact")
    artifact("autonomous-trigger-contract")
    artifact("autonomous-action-plan")
    artifact("autonomous-governance-ledger", extra=(("--governance-json", {}),))
    artifact("autonomous-tutoring-artifact")

    distill = commands.add_parser("distill")
    distill_commands = distill.add_subparsers(dest="distill_command", required=True)
    inspect = distill_commands.add_parser("inspect")
    inspect.add_argument("--session-id", required=True)
    inspect.add_argument("--codex-home")
    inspect.add_argument("--format", choices=("json", "quiet"), default="json")
    propose = distill_commands.add_parser("propose")
    propose.add_argument("--session-id", required=True)
    propose.add_argument("--codex-home")
    propose.add_argument("--candidate-json", required=True)
    propose.add_argument("--vault-root")
    propose.add_argument("--format", choices=("json", "quiet"), default="json")
    apply = distill_commands.add_parser("apply")
    apply.add_argument("--candidate-id", required=True)
    apply.add_argument("--vault-root")
    apply.add_argument("--yes", action="store_true")
    apply.add_argument("--format", choices=("json", "quiet"), default="json")
    queue = distill_commands.add_parser("queue")
    queue.add_argument("--format", choices=("json", "quiet"), default="json")

    exercise = commands.add_parser("exercise")
    exercise.add_argument("--subject")
    exercise.add_argument("--chapter")
    exercise.add_argument("--node", required=True)
    exercise.add_argument("--result", choices=("right", "wrong", "partial"), required=True)
    exercise.add_argument("--tag", action="append", default=[])
    exercise.add_argument("--note", default="")
    exercise.add_argument("--format", choices=("json", "quiet"), default="json")


def _artifact_forwarded(args: argparse.Namespace, *extra: str) -> list[str]:
    forwarded = [*extra, "--format", args.format]
    if args.vault_root:
        forwarded.extend(["--vault-root", args.vault_root])
    return forwarded


def dispatch_learner(args: argparse.Namespace, run_script: Callable[..., str], exercise_handler: Callable[[argparse.Namespace], str]) -> str | None:
    if args.command != "learner":
        return None
    command = args.learner_command
    if command == "exercise":
        return exercise_handler(args)
    if command == "distill":
        forwarded = [args.distill_command, "--format", args.format]
        if args.distill_command in {"inspect", "propose"}:
            forwarded.extend(["--session-id", args.session_id])
            if args.codex_home:
                forwarded.extend(["--codex-home", args.codex_home])
        if args.distill_command == "propose":
            forwarded.extend(["--candidate-json", args.candidate_json])
        if args.distill_command == "apply":
            forwarded.extend(["--candidate-id", args.candidate_id])
            if args.yes:
                forwarded.append("--yes")
        if args.distill_command in {"propose", "apply"} and args.vault_root:
            forwarded.extend(["--vault-root", args.vault_root])
        return run_script("conversation_distillation.py", *forwarded)
    if command == "orchestration-context":
        forwarded = ["--freshness-days", str(args.freshness_days), "--format", args.format]
        if args.vault_root:
            forwarded.extend(["--vault-root", args.vault_root])
        if args.as_of:
            forwarded.extend(["--as-of", args.as_of])
        return run_script("build_study_orchestration_context.py", *forwarded)

    scripts = {
        "daily-card": "build_daily_study_card.py",
        "review-followups": "build_review_followups.py",
        "weekly-orchestration": "build_weekly_orchestration.py",
        "teacher-loop-artifact": "build_r17_teacher_loop_artifact.py",
        "adaptive-coaching-context": "build_adaptive_coaching_context.py",
        "adaptive-coaching-packet": "build_adaptive_coaching_packet.py",
        "coaching-feedback-loop": "build_coaching_feedback_loop.py",
        "closed-loop-operations": "build_closed_loop_operations.py",
        "adaptive-coaching-artifact": "build_r18_adaptive_coaching_artifact.py",
        "longitudinal-tutoring-context": "build_longitudinal_tutoring_context.py",
        "tutoring-strategy-packet": "build_tutoring_strategy_packet.py",
        "tutoring-feedback-loop": "build_tutoring_feedback_loop.py",
        "long-horizon-operations": "build_long_horizon_operations.py",
        "longitudinal-tutoring-artifact": "build_r19_longitudinal_tutoring_artifact.py",
        "autonomous-trigger-contract": "build_autonomous_trigger_contract.py",
        "autonomous-action-plan": "build_autonomous_action_plan.py",
        "autonomous-governance-ledger": "build_autonomous_governance_ledger.py",
        "autonomous-tutoring-artifact": "build_r20_autonomous_tutoring_artifact.py",
    }
    script = scripts.get(command)
    if script is None:
        return None
    extra = ["--plan-date", args.plan_date]
    if command == "adaptive-coaching-context":
        extra.extend(["--stale-signal-days", str(args.stale_signal_days)])
    elif command == "longitudinal-tutoring-context":
        extra.extend(["--stale-cycle-days", str(args.stale_cycle_days)])
    forwarded = _artifact_forwarded(args, *extra)
    if command in {"weekly-orchestration", "closed-loop-operations", "long-horizon-operations"} and args.override_json:
        forwarded.extend(["--override-json", args.override_json])
    if command == "autonomous-governance-ledger" and args.governance_json:
        forwarded.extend(["--governance-json", args.governance_json])
    return run_script(script, *forwarded)
