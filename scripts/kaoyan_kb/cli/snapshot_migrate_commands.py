from __future__ import annotations

import argparse
from collections.abc import Callable


def add_snapshot_migrate_commands(
    subparsers: argparse._SubParsersAction,
    *,
    formatter_class: type[argparse.HelpFormatter] | None = None,
) -> None:
    parser_options: dict[str, object] = {}
    if formatter_class is not None:
        parser_options["formatter_class"] = formatter_class

    snapshot = subparsers.add_parser(
        "snapshot",
        help="create, list, restore, and prune bounded snapshots",
        description="Create, list, restore, and prune bounded snapshots.",
        **parser_options,
    )
    snapshot_sub = snapshot.add_subparsers(dest="snapshot_command", required=True)
    snap_create = snapshot_sub.add_parser("create")
    snap_create.add_argument("--format", choices=("json", "quiet"), default="json")
    snap_list = snapshot_sub.add_parser("list")
    snap_list.add_argument("--format", choices=("json", "quiet"), default="json")
    snap_restore = snapshot_sub.add_parser("restore")
    snap_restore.add_argument("snapshot_id")
    snap_restore.add_argument("--format", choices=("json", "quiet"), default="json")
    snap_prune = snapshot_sub.add_parser("prune")
    snap_prune.add_argument("--keep-last", type=int, default=3)
    snap_prune.add_argument("--keep-daily", type=int, default=0)
    snap_prune.add_argument("--yes", action="store_true")
    snap_prune.add_argument("--format", choices=("json", "quiet"), default="json")

    migrate = subparsers.add_parser(
        "migrate-vault",
        help="plan, verify, and apply bounded vault migration steps",
        description="Plan, verify, and apply bounded vault migration steps.",
        **parser_options,
    )
    migrate.add_argument("--workspace-root")
    migrate.add_argument("--vault-root")
    migrate.add_argument("--migration-root")
    migrate.add_argument("--format", choices=("json", "quiet"), default="json")
    migrate_sub = migrate.add_subparsers(dest="migrate_command", required=True)
    for command in ("plan", "verify"):
        migrate_sub.add_parser(command).add_argument("--format", choices=("json", "quiet"), default=argparse.SUPPRESS)
    migrate_apply = migrate_sub.add_parser("apply")
    migrate_apply.add_argument("--yes", action="store_true")
    migrate_apply.add_argument("--format", choices=("json", "quiet"), default=argparse.SUPPRESS)


def dispatch_snapshot_migrate(args: argparse.Namespace, run_script: Callable[..., str]) -> str | None:
    if args.command == "snapshot":
        if args.snapshot_command == "create":
            return run_script("create_snapshot.py", "--format", args.format)
        if args.snapshot_command == "list":
            return run_script("list_snapshots.py", "--format", args.format)
        if args.snapshot_command == "restore":
            return run_script("restore_snapshot.py", "--snapshot-id", args.snapshot_id, "--format", args.format)
        if args.snapshot_command == "prune":
            forwarded = ["--keep-last", str(args.keep_last), "--keep-daily", str(args.keep_daily), "--format", args.format]
            if args.yes:
                forwarded.append("--yes")
            return run_script("prune_snapshots.py", *forwarded)

    if args.command == "migrate-vault":
        forwarded = ["--format", args.format]
        for key in ("workspace_root", "vault_root", "migration_root"):
            value = getattr(args, key)
            if value:
                forwarded.extend([f"--{key.replace('_', '-')}", str(value)])
        forwarded.append(args.migrate_command)
        if args.migrate_command == "apply" and args.yes:
            forwarded.append("--yes")
        return run_script("migrate_vault.py", *forwarded)

    return None
