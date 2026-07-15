from __future__ import annotations
import argparse
import json
from collections.abc import Callable

def add_run_maintain_commands(subparsers: argparse._SubParsersAction, *, formatter_class: type[argparse.HelpFormatter] | None = None) -> None:
    opts={} if formatter_class is None else {'formatter_class':formatter_class}
    maintain=subparsers.add_parser('maintain',help='run wrapped maintenance flows such as weekly upkeep',description='Run wrapped maintenance flows such as weekly upkeep.',**opts)
    weekly=maintain.add_subparsers(dest='maintain_command',required=True).add_parser('weekly')
    for name in ('--vault-root','--subject','--chapter'): weekly.add_argument(name)
    weekly.add_argument('--max-images-per-chunk',type=int); weekly.add_argument('--replan-chunks',action='store_true'); weekly.add_argument('--force-full-sync',action='store_true'); weekly.add_argument('--topn',type=int,default=5); weekly.add_argument('--snapshot',action='store_true'); weekly.add_argument('--format',choices=('json','text'),default='json')
    run=subparsers.add_parser('run',help='manage resumable run manifests, checkpoints, and summaries',description='Manage resumable run manifests, checkpoints, and summaries.',**opts)
    rs=run.add_subparsers(dest='run_command',required=True)
    start=rs.add_parser('start'); start.add_argument('--run-type',required=True); start.add_argument('--resume-key',required=True); start.add_argument('--subject'); start.add_argument('--metadata-json'); start.add_argument('--format',choices=('json','quiet'),default='json')
    step=rs.add_parser('step'); step.add_argument('--run-id',required=True); step.add_argument('--step',required=True); step.add_argument('--status',required=True); step.add_argument('--checkpoint-json'); step.add_argument('--message',default=''); step.add_argument('--format',choices=('json','quiet'),default='json')
    finish=rs.add_parser('finish'); finish.add_argument('--run-id',required=True); finish.add_argument('--status',required=True); finish.add_argument('--summary-json'); finish.add_argument('--format',choices=('json','quiet'),default='json')
    show=rs.add_parser('show'); show.add_argument('--run-id',required=True); show.add_argument('--format',choices=('json','quiet'),default='json')

def dispatch_run_maintain(
    args: argparse.Namespace,
    run_script: Callable[..., str],
    weekly_refresh_fallback: Callable[[argparse.Namespace, str], dict] | None = None,
) -> str | None:
    if args.command == "maintain":
        if args.maintain_command != "weekly":
            return None
        snapshot_payload: dict | None = None
        if args.snapshot:
            snapshot_payload = {
                "created": True,
                **json.loads(run_script("create_snapshot.py", "--format", "json")),
            }
        forwarded: list[str] = ["--topn", str(args.topn), "--format", "json"]
        if args.vault_root:
            forwarded.extend(["--vault-root", args.vault_root])
        if args.subject:
            forwarded.extend(["--subject", args.subject])
        if args.chapter:
            forwarded.extend(["--chapter", args.chapter])
        if args.max_images_per_chunk is not None:
            forwarded.extend(["--max-images-per-chunk", str(args.max_images_per_chunk)])
        if args.replan_chunks:
            forwarded.append("--replan-chunks")
        if args.force_full_sync:
            forwarded.append("--force-full-sync")
        try:
            maintenance_payload = json.loads(run_script("maintain_knowledge_engine.py", *forwarded))
        except SystemExit as exc:
            if args.force_full_sync or weekly_refresh_fallback is None:
                raise
            maintenance_payload = weekly_refresh_fallback(args, str(exc))
        maintenance_payload["operation_layers"] = [
            "fact_sync_layer",
            "learner_rebuild_layer",
            "dashboard_render_layer",
        ]
        if args.format == "json":
            return json.dumps(
                {"snapshot": snapshot_payload, "maintenance": maintenance_payload},
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        text = json.dumps(maintenance_payload, ensure_ascii=False, indent=2)
        snapshot_text = ""
        if snapshot_payload:
            snapshot_text = f"# snapshot\n- id: {snapshot_payload.get('snapshot_id', '')}\n\n"
        return snapshot_text + text
    if args.command!='run': return None
    f=[args.run_command]
    if args.run_command=='start':
        f += ['--run-type',args.run_type,'--resume-key',args.resume_key]
        if args.subject:f+=['--subject',args.subject]
        if args.metadata_json:f+=['--metadata-json',args.metadata_json]
    elif args.run_command=='step':
        f+=['--run-id',args.run_id,'--step',args.step,'--status',args.status]
        if args.checkpoint_json:f+=['--checkpoint-json',args.checkpoint_json]
        if args.message:f+=['--message',args.message]
    elif args.run_command=='finish':
        f+=['--run-id',args.run_id,'--status',args.status]
        if args.summary_json:f+=['--summary-json',args.summary_json]
    else: f+=['--run-id',args.run_id]
    f+=['--format',args.format]
    return run_script('run_manager.py',*f)
