"""数据层命令行（`DATA_LAYER_PLAN` §11）。

```
python -m scripts.datalayer universe list|add|enable|disable|remove|import-output
python -m scripts.datalayer pull  [--profile frugal|bulk] [--ticker T]… [--periods …]
                                  [--only-gaps] [--force] [--yes] [--batch-id ID]
python -m scripts.datalayer rebuild [--ticker T]… [--out 路径]
python -m scripts.datalayer gaps   [--ticker T] [--dataset D] [--json]
python -m scripts.datalayer import-legacy [--from 路径] [--dry-run] [--force-import]
python -m scripts.datalayer export [--ticker T]… [--out 目录]
python -m scripts.datalayer check
python -m scripts.datalayer wipe --confirm WIPE
```

`--universe` / `--pull` / … 也接受（`make data-*` 就是这么转发的）。
退出码：`0` 成功 / `2` 用法或前置错误（含 `NO_TOKEN`、未确认）/ `4` 仓不可用 / `130` 中断。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import archive_root as resolve_archive_root
from .config import default_periods
from .errors import DatalayerError, NoToken, QuotaConfirmRequired, StoreUnavailable, UsageError
from .store import DataStore
from .universe import Universe, market_of

# `--universe add …` 与 `universe add …` 两种写法都支持（argparse 子命令）。
ALIASES = {
    "--universe": "universe", "--pull": "pull", "--rebuild": "rebuild", "--gaps": "gaps",
    "--import-legacy": "import-legacy", "--export": "export", "--check": "check", "--wipe": "wipe",
}


def normalize_argv(argv):
    argv = list(argv)
    if argv and argv[0] in ALIASES:
        argv[0] = ALIASES[argv[0]]
    return argv


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", help="原始仓根（默认 ~/turtle_archive，或 TURTLE_ARCHIVE_ROOT）")
    common.add_argument("--json", action="store_true", help="以 JSON 输出（机器可读）")

    parser = argparse.ArgumentParser(
        prog="python -m scripts.datalayer",
        description="统一原始数据仓：自选股清单 / 一次全量拉取 / 缺口补齐 / 离线重建",
    )
    parser.add_argument("--version", action="version", version=f"datalayer {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    universe = subparsers.add_parser("universe", parents=[common], help="自选股清单（AC-1）")
    universe.add_argument("action", choices=("list", "add", "enable", "disable", "remove",
                                             "import-output"))
    universe.add_argument("--ticker")
    universe.add_argument("--name", help="显示名（add 必填）")
    universe.add_argument("--market", choices=("SH", "SZ", "HK", "US"))
    universe.add_argument("--tier", help="所需数据档位（frugal / bulk）")
    universe.add_argument("--note")
    universe.add_argument("--output-root", default="output", help="import-output 的既有产物根")
    universe.add_argument("--write", action="store_true", help="import-output 时真的写入清单")

    pull = subparsers.add_parser("pull", parents=[common], help="一次动作全量拉取（AC-2/6/7）")
    pull.add_argument("--profile", choices=("frugal", "bulk"), help="不填则按清单每条自己的档位")
    pull.add_argument("--ticker", action="append", default=[], help="只拉这些标的（可重复）")
    pull.add_argument("--periods", help="期次，逗号分隔（接口期次 20260630 或项目期次 2026H1）")
    pull.add_argument("--only-gaps", action="store_true", help="只补缺口目标（AC-7）")
    pull.add_argument("--force", action="store_true", help="显式重拉已有记录（AC-6）")
    pull.add_argument("--yes", action="store_true", help="确认调用量预估（AC-2 的显式确认）")
    pull.add_argument("--batch-id", help="恢复已有批次")
    pull.add_argument("--tier-label", default="", help="账号档位标签，不要填写 token")

    rebuild = subparsers.add_parser("rebuild", parents=[common], help="从仓离线重建产物（AC-5）")
    rebuild.add_argument("--ticker", action="append", default=[])
    rebuild.add_argument("--out", help="产物路径（默认 output/<公司>/data_pack_market.md）")
    rebuild.add_argument("--output-root", default="output")

    gaps = subparsers.add_parser("gaps", parents=[common], help="缺口与完备度（AC-7）")
    gaps.add_argument("--ticker")
    gaps.add_argument("--dataset")
    gaps.add_argument("--profile", choices=("frugal", "bulk"),
                      help="配合清单口径的完备度（不填则按清单每条自己的档位）")
    gaps.add_argument("--periods", help="清单口径的期次范围（默认最近 5 个年报期）")

    legacy = subparsers.add_parser("import-legacy", parents=[common],
                                   help="一次性导入旧存档与旧缓存（AC-8）")
    legacy.add_argument("--from", dest="source", help="旧存档根（默认取仓根）")
    legacy.add_argument("--cache-dir", help="旧文件缓存目录（默认 output/.collector_cache）")
    legacy.add_argument("--dry-run", action="store_true")
    legacy.add_argument("--force-import", action="store_true", help="冲突时也覆盖（显式动作）")

    export = subparsers.add_parser("export", parents=[common], help="导出为可读 JSON 树")
    export.add_argument("--ticker", action="append", default=[])
    export.add_argument("--out", default="output/.datalayer_export")

    subparsers.add_parser("check", parents=[common], help="仓的规模、schema 与扫描自检")

    wipe = subparsers.add_parser("wipe", parents=[common], help="删除仓内记录（二次确认）")
    wipe.add_argument("--confirm", help='必须逐字写 WIPE')
    return parser


def _store_from_args(args) -> DataStore:
    return DataStore(args.store or resolve_archive_root())


def normalize_period(value: str) -> str:
    """期次参数接受两种写法：接口期次 ``20260630`` 与项目期次 ``2026H1``。"""

    text = str(value or "").strip()
    if not text:
        raise UsageError("期次不能为空")
    from periods import is_valid_period, period_to_end_date

    if is_valid_period(text):
        return period_to_end_date(text)
    return text


def parse_periods(value) -> list[str]:
    if not value:
        return default_periods()
    return [normalize_period(item) for item in str(value).split(",")]


# ---------------------------------------------------------------------------- 子命令


def cmd_universe(args) -> int:
    store = _store_from_args(args)
    universe = Universe(store)
    action = args.action
    if action == "list":
        entries = universe.entries()
        if args.json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return 0
        if not entries:
            print("清单为空：用 `universe add --ticker 600887.SH --name 伊利股份` 登记要维护的公司")
            return 0
        print(f"{'标的':<12}{'显示名':<16}{'市场':<6}{'启用':<6}{'档位':<8}备注")
        for entry in entries:
            print(f"{entry['ticker']:<12}{entry['display_name']:<16}{entry['market']:<6}"
                  f"{'是' if entry['enabled'] else '否':<6}{entry['tier'] or '':<8}"
                  f"{entry.get('note') or ''}")
        return 0
    if action == "add":
        if not args.ticker or not args.name:
            raise UsageError("add 需要 --ticker 与 --name")
        result = universe.add(args.ticker, args.name, args.market, True,
                              args.tier or "bulk", args.note)
        entry = result["entry"]
        print(f"{'已新增' if result['created'] else '已更新'}：{entry['ticker']} "
              f"{entry['display_name']}（{entry['market']} / {entry['tier']}）")
        return 0
    if action in ("enable", "disable"):
        if not args.ticker:
            raise UsageError(f"{action} 需要 --ticker")
        entry = universe.set_enabled(args.ticker, action == "enable")
        print(f"{entry['ticker']}：{'启用' if entry['enabled'] else '停用'}")
        return 0
    if action == "remove":
        if not args.ticker:
            raise UsageError("remove 需要 --ticker")
        removed = universe.remove(args.ticker)
        print(f"{'已从清单删除' if removed else '清单里没有'}：{args.ticker}")
        return 0
    if action == "import-output":
        suggestions = universe.suggest_from_output(args.output_root)
        if not suggestions:
            print(f"{args.output_root} 下没有可建议的公司目录")
            return 0
        print("从既有产物建议的清单条目（**不会自动写入**，确认后用 universe add 登记）：")
        for item in suggestions:
            print(f"  {item['ticker']:<12}{item['display_name']:<16}{item['market']:<6}"
                  f"（目录 {item['dir']}）")
        if args.write:
            for item in suggestions:
                if universe.get(item["ticker"]) is None:
                    universe.add(item["ticker"], item["display_name"], item["market"])
            print(f"已写入 {len(suggestions)} 条建议条目")
        return 0
    raise UsageError(f"未知的 universe 动作：{action}")


def cmd_pull(args) -> int:
    from .pull import PullBatch, format_estimate, plan
    from .security import resolve_token

    store = _store_from_args(args)
    token = resolve_token()
    if not token:
        raise NoToken("未配置 Tushare token",
                      hint="设置 TUSHARE_TOKEN 或在项目 .env 中配置后重试。")
    universe = Universe(store)
    periods = parse_periods(args.periods)
    planned = plan(universe, periods=periods, profile=args.profile,
                   only_gaps=args.only_gaps, store=store, tickers=args.ticker or None)
    report = planned["estimate"]
    if args.only_gaps:
        print(f"只补缺口：{report['requested']} 个目标中 {report['total']} 个仍需补齐")
    print(format_estimate(report))
    if not report["total"]:
        print("没有需要拉取的目标：清单内目标都已被仓服务（含命中存档与已完备）。")
        return 0
    if not args.yes:
        raise QuotaConfirmRequired(
            f"本批预计 {report['total']} 次请求，需要显式确认。",
            hint="复核调用量后使用 --yes 确认。",
        )
    from tushare_collector import TushareClient

    client = TushareClient(token, store=store, batch_id=args.batch_id)
    batch = PullBatch(store, planned["targets"], args.profile or _dominant_tier(universe),
                      client._access, token=token, tier_label=args.tier_label).run(
        batch_id=args.batch_id, confirm=args.yes, force=args.force)
    print(PullBatch.summary_line(batch))
    if args.json:
        print(json.dumps(batch, ensure_ascii=False, indent=2))
    # 退出码表（§11）里没有「批次部分完成」这一档：`partial` 也是**正常跑完**的一轮
    # （无权限/限频是数据的事实，不是命令的错误）。要用退出码判成功，看 `--json` 的 status。
    return 0


def _dominant_tier(universe) -> str:
    """没给 `--profile` 时，批次档案取清单里出现最多的档位（目标本身已按各自档位枚举）。"""

    tiers = [entry["tier"] for entry in universe.entries(enabled_only=True)]
    if not tiers:
        return "bulk"
    return max(sorted(set(tiers)), key=tiers.count)


def cmd_rebuild(args) -> int:
    from .rebuild import format_report, rebuild

    store = _store_from_args(args)
    tickers = args.ticker or Universe(store).tickers()
    if not tickers:
        raise UsageError("清单为空且没有 --ticker：不知道要重建哪个标的")
    if args.out and len(tickers) > 1:
        raise UsageError("--out 只能配合单个 --ticker 使用")
    for ticker in tickers:
        report = rebuild(store, ticker, out_path=args.out, output_root=args.output_root)
        print(format_report(report))
    # 缺口是**数据的事实**（无权限/限频/还没拉），不是命令的错误：退出码表里没有这一档。
    # 要按缺口分支请用 `gap_targets()` / `--json`，不要靠退出码。
    return 0


def cmd_gaps(args) -> int:
    from .gaps import completeness
    from .pull import plan

    store = _store_from_args(args)
    records = store.records(ticker=args.ticker, dataset=args.dataset)
    report = completeness(records)
    # 「仓内口径」数的是**买回来的记录**，「清单口径」数的是**计划要拉的目标**。
    # 两个数不一样是正常的（独立复核 B3 把这种差异当成了矛盾），所以两个都打印、各自标注定义。
    planned = None
    universe = Universe(store)
    if universe.entries(enabled_only=True):
        # 注意 `gaps --ticker` 是**单个**标的（与 `pull/rebuild` 的 `append` 不同），
        # 所以要包成列表再交给 plan——直接传字符串会被当成可迭代的字符集合，
        # 于是「清单里没有这些标的」把清单口径整段吞掉（独立复核 B3 的现场就是这个）。
        try:
            planned = plan(universe, periods=parse_periods(args.periods), profile=args.profile,
                           only_gaps=True, store=store,
                           tickers=[args.ticker] if args.ticker else None)
        except UsageError as exc:
            print(f"（清单口径跳过：{exc.message}）", file=sys.stderr)
    if args.json:
        print(json.dumps({"ticker": args.ticker, "dataset": args.dataset,
                          "records": report,
                          "targets": planned["estimate"] if planned else None},
                         ensure_ascii=False, indent=2))
        return 0
    counts = report["counts"]
    print(f"仓内口径完备度：{counts['complete']}/{counts['total']} 条记录"
          f"（有效 {counts['ok']} · 空 {counts['empty']} · "
          f"无权限 {counts['no_permission']} · 频率受限 {counts['rate_limited']} · "
          f"其他错误 {counts['error']}）")
    if planned:
        estimate = planned["estimate"]
        print(f"清单口径完备度：{estimate['requested'] - estimate['total']}/{estimate['requested']} "
              f"个目标（清单 × 档位 × 期次范围；还需补齐 {estimate['total']} 个）")
    for gap in report["gaps"]:
        print(f"  缺口 {gap['ticker']} {gap['dataset']} {gap['period']}：{gap['result']}"
              f"{' — ' + (gap['error_excerpt'] or '') if gap.get('error_excerpt') else ''}")
    if report["gaps"]:
        print("补齐：make data-pull ARGS='--only-gaps --yes'")
    return 0


def cmd_import_legacy(args) -> int:
    from .legacy import format_report, import_all

    store = _store_from_args(args)
    report = import_all(store, archive_root=args.source, cache_dir=args.cache_dir,
                        dry_run=args.dry_run, force=args.force_import)
    print(format_report(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    # 冲突按设计「不覆盖、只报告」：报告里逐条列出，命令本身算跑完。
    return 0


def cmd_export(args) -> int:
    store = _store_from_args(args)
    tickers = args.ticker or [None]
    total = 0
    for ticker in tickers:
        total += store.export(args.out, ticker=ticker)
    print(f"已导出 {total} 条记录 → {args.out}")
    return 0


def cmd_check(args) -> int:
    from .endpoints import scan_dynamic_calls, scan_safe_calls
    from .registry import DATASETS, PROFILES

    store = _store_from_args(args)
    payload = {
        "store": str(store.db_path),
        "root": str(store.root),
        "stats": store.stats(),
        "scanned_datasets": sorted(scan_safe_calls()),
        "dynamic_call_sites": scan_dynamic_calls(),
        "undeclared_scanned": sorted(set(scan_safe_calls()) - set(DATASETS)),
        "profile_sizes": {name: len(datasets) for name, datasets in PROFILES.items()},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    stats = payload["stats"]
    print(f"仓：{payload['store']}")
    print(f"  schema {stats['schema_version']} · 记录 {stats['records']} 条 · "
          f"{stats['bytes']} 字节 · 清单 {stats['universe']} 条 · 批次 {stats['batches']} 个")
    print(f"  各结果：{stats['by_result']}")
    print(f"  各口径：{stats['by_period_type']}")
    print(f"扫描到 {len(payload['scanned_datasets'])} 个接口（档位：{payload['profile_sizes']}）；"
          f"未声明：{payload['undeclared_scanned'] or '无'}")
    print(f"动态调用点（第一参数不是字面量）：{payload['dynamic_call_sites']}")
    return 0


def cmd_wipe(args) -> int:
    store = _store_from_args(args)
    entry = store.wipe(args.confirm or "")
    print(f"已删除 {entry['records']} 条记录（这是显式动作，manifest 里留了痕）。")
    return 0


COMMANDS = {
    "universe": cmd_universe,
    "pull": cmd_pull,
    "rebuild": cmd_rebuild,
    "gaps": cmd_gaps,
    "import-legacy": cmd_import_legacy,
    "export": cmd_export,
    "check": cmd_check,
    "wipe": cmd_wipe,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))
    try:
        return COMMANDS[args.command](args)
    except StoreUnavailable as exc:
        print(f"仓不可用 [{exc.code}]：{exc.message}", file=sys.stderr)
        return 4
    except (NoToken, QuotaConfirmRequired, UsageError) as exc:
        print(f"数据层错误 [{exc.code}]：{exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"提示：{exc.hint}", file=sys.stderr)
        return 2
    except DatalayerError as exc:
        print(f"数据层错误 [{exc.code}]：{exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"提示：{exc.hint}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断；用相同 --batch-id 可从未完成目标继续。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
