#!/usr/bin/env python3
"""REQ-016: save an offline full-market plan, then explicitly execute that plan.

Directory refresh is a separate datalayer command. This command never refreshes
it. Plans pin the directory version and are checked again before submission.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from datalayer.store import DataStore
from webui.config import Config
from webui.core.errors import BadRequest
from webui.plugins.research_data import CollectionRunner, explain_batch, make_plan


def build_parser():
    parser = argparse.ArgumentParser(description="全A股采集：离线规划，再明确确认执行")
    parser.add_argument("action", choices=("plan", "run", "resume"))
    parser.add_argument("--store", required=True, help="隔离原始仓根")
    parser.add_argument("--plan", required=True, help="固定计划JSON，plan时写入，run时核对")
    parser.add_argument("--scope", choices=("all_a", "watchlist"), default="all_a")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--tier", choices=("frugal", "bulk"), default="frugal")
    parser.add_argument("--periods", default="20260630")
    parser.add_argument("--yes", action="store_true", help="已复核此计划规模，确认联网及配额影响")
    parser.add_argument("--batch-id", help="resume需要原批次标识")
    return parser


def context(store):
    config = Config(archive_root=Path(store).resolve())
    return SimpleNamespace(config=config, registry=SimpleNamespace(), query={}, log=None)


def summarize(plan):
    return {key: plan[key] for key in ("digest", "inputs", "directory", "company_count", "total",
                                       "requests_estimate", "reuse_estimate", "groups", "unknowns", "blockers")}


def execute(args):
    ctx = context(args.store)
    path = Path(args.plan)
    if args.action == "plan":
        inputs = {"scope": args.scope, "tier": args.tier, "periods": args.periods}
        if args.snapshot_id:
            inputs["snapshot_id"] = args.snapshot_id
        plan = make_plan(ctx, inputs)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summarize(plan), ensure_ascii=False, indent=2))
        return 0
    if not args.yes:
        raise BadRequest("请先生成并复核固定计划；确认规模后加 --yes 执行")
    saved = json.loads(path.read_text(encoding="utf-8"))
    store = DataStore(args.store)
    batch_id = None
    if args.action == "resume":
        if not args.batch_id:
            raise BadRequest("恢复需要原批次 --batch-id")
        batch = store.load_batch(args.batch_id)
        if not batch:
            raise BadRequest("原批次不存在")
        batch_id = args.batch_id
        planned = {"inputs": {"tier": batch["profile"]}, "targets": batch["targets"]}
    else:
        planned = make_plan(ctx, saved["inputs"])
        if planned["digest"] != saved["digest"]:
            raise BadRequest("范围或参数变化，请重新生成计划并确认")
        if planned["blockers"]:
            raise BadRequest("；".join(planned["blockers"]))
    runner = CollectionRunner(ctx)
    result = runner.start(planned, batch_id=batch_id)
    batch_id = result["batch_id"]
    print(json.dumps({"batch_id": batch_id, "status": result["status"], "total": result["total"]}, ensure_ascii=False), flush=True)
    thread, pause = runner.active[batch_id]
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        pause.set()
        while thread.is_alive():
            thread.join(timeout=1)
        print(json.dumps({"batch_id": batch_id, "status": "paused"}, ensure_ascii=False), flush=True)
        return 130
    result = explain_batch(store, store.load_batch(batch_id))
    print(json.dumps({key: result[key] for key in ("batch_id", "status", "total", "actual_requests", "reused", "counts", "directory")}, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "failed" else 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return execute(args)
    except Exception as exc:
        # Do not emit arbitrary provider exceptions (they may contain credentials).
        from datalayer.errors import DatalayerError
        from webui.core.errors import WebUIError
        print(str(exc) if isinstance(exc, (DatalayerError, WebUIError)) else type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
