"""一次动作全量拉取与缺口补齐（`REQ-011` 的 `AC-2` / `AC-6` / `AC-7`）。

**升级不是重写**：`REQ-009.4` 的 `ArchiveBatch` 状态机与语义（pending/running/paused/
done/partial/failed、`owner_pid` + lock 文件防并发、逐目标落盘、`KeyboardInterrupt` →
paused）已经过独立验收，本模块把同一套语义搬到仓上，行为逐条保留：

- 未配 token → `NO_TOKEN` 且**零请求**（`AC-4.1`）；
- 预估 + 必须显式确认（`--yes` 可跳过，`AC-4.2` 语义）；
- 已有记录（`ok`/`empty`）跳过并计入「命中存档」，`--force` 才重拉（`AC-6`）；
- 中断后重启同一批次只补未完成目标（`AC-6`）；
- 同批次不允许并发（`AC-6`）；
- 批次结束报告「新增请求 / 命中存档 / 失败 / 无权限」四类计数（`AC-6`）；
- `--only-gaps` 只补缺口（`AC-7`，并入本需求且不得回退）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone

from . import registry
from .errors import BatchRunning, NoToken, QuotaConfirmRequired, UsageError
from .gaps import DONE_KINDS, classify_result, completeness, pending_targets

# 与既有 `webui/archive/quota.py` 相同的市场过滤规则：港股接口只对港股标的，
# 美股接口只对美股标的，`stock_basic` 只对 A 股。
HK_MARKETS = ("HK",)
US_MARKETS = ("US", "NYSE", "NASDAQ")
CN_MARKETS = ("SH", "SZ", "")


def market_of(ticker: str) -> str:
    """默认市场推断：后缀优先；无后缀的字母代码视为美股。"""

    text = str(ticker or "").strip().upper()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return "US" if text.isalpha() else ""


def api_eligible(api: str, market: str) -> bool:
    market = str(market or "").upper()
    if api.startswith("hk_"):
        return market in HK_MARKETS
    if api.startswith("us_"):
        return market in US_MARKETS
    if api == "stock_basic":
        return market in CN_MARKETS
    return True


def params_for(dataset: str, variant: dict, *, today=None) -> dict:
    """拉取某接口要带的**非标的、非期次**入参：字段并集 + 字面量语义参数 + 变体 + 时间窗口。

    全部由代码扫出来（D3）或由注册表声明：拉取参数与调用点的请求对不上，
    离线重建就会静默变成一片「数据缺失」。
    """

    from .endpoints import union_fields, union_params

    params: dict = {}
    fields = union_fields(dataset)
    if fields:
        params["fields"] = fields
    params.update(registry.window_params(dataset, today=today))
    params.update(union_params(dataset))
    params.update(variant)
    return params


def targets_for(tickers, periods, profile: str, *, params_by_api=None,
                markets=None, today=None) -> list[dict]:
    """「清单 × 档位 × 期次范围」→ 目标集合。

    期次规则：能按期次逐期枚举的报告期接口用给定期次列表；其余接口只用 ``latest``。
    `frugal` 档位对报告期接口只取**最新一期**（沿用 `REQ-009.4` 的口径：低配额档案不拉历史）。
    语义变体（如利润表的 `report_type=1/6`）各出一条目标——拉一次就够用，重建时不再缺小节。
    """

    apis = registry.profile_datasets(profile)
    params_by_api = params_by_api or {}
    markets = markets or {}
    selected_periods = tuple(str(period) for period in periods) or ("latest",)
    targets: list[dict] = []
    for ticker in tickers:
        market = markets.get(ticker) or market_of(ticker)
        for api in apis:
            if not api_eligible(api, market):
                continue
            spec = registry.spec_for(api)
            if spec.enumerate_periods:
                api_periods = tuple(selected_periods)
                if profile == registry.FRUGAL:
                    api_periods = (max(selected_periods),)
            else:
                api_periods = ("latest",)
            for variant in registry.variant_combinations(api):
                base = {**params_for(api, variant, today=today), **params_by_api.get(api, {})}
                for period in api_periods:
                    params = dict(base)
                    params.setdefault("ts_code", ticker)
                    if period != "latest":
                        params.setdefault("period", period)
                    # 接口若把 ts_code 钉成字面量（如 yc_cb 的 `1001.CB`），目标的标的
                    # 就是那个值——否则批次去重与冒烟读都会拿错标的去查仓。
                    targets.append({"ticker": str(params.get("ts_code") or ticker),
                                    "dataset": api, "period": period, "params": params})
    return targets


def estimate(targets, profile: str = "", tiers=None) -> dict:
    """调用量预估：纯计算，不联网、不读仓内容（所以「先看预估再决定」零成本）。

    `tiers` 给出「目标键 → 档位」的映射时，额外按档位分组（清单里各条档位不同时，
    `AC-2` 要的「按档位分组的条数」才看得出来）。
    """

    by_dataset = Counter(target["dataset"] for target in targets)
    by_ticker = Counter(target["ticker"] for target in targets)
    report = {
        "profile": profile,
        "total": len(targets),
        "by_dataset": dict(sorted(by_dataset.items())),
        "by_ticker": dict(sorted(by_ticker.items())),
    }
    if tiers:
        by_tier = Counter(tiers.get(_target_key(target), "") for target in targets)
        report["by_tier"] = dict(sorted(by_tier.items()))
    return report


def format_estimate(report: dict) -> str:
    lines = [f"调用量预估：{report['total']} 次请求（{report.get('profile') or '未指定档位'}）"]
    lines.extend(f"  {dataset:<18}{count}" for dataset, count in report["by_dataset"].items())
    return "\n".join(lines)


def plan(universe, *, periods, profile: str | None = None, only_gaps: bool = False,
         store=None, tickers=None, params_by_api=None) -> dict:
    """按清单生成一次拉取的完整计划（目标 + 预估），**不联网**。

    `profile` 为 None 时按清单每条自己的档位分组枚举（`AC-1` 的「所需数据档位」生效）。
    """

    entries = universe.entries(enabled_only=True)
    if tickers:
        wanted = set(tickers)
        entries = [entry for entry in entries if entry["ticker"] in wanted]
        missing = sorted(wanted - {entry["ticker"] for entry in entries})
        if missing:
            raise UsageError(f"清单里没有这些标的：{', '.join(missing)}（先 data-universe add）")
    if not entries:
        raise UsageError("自选股清单为空：先用 data-universe add 登记要维护的公司")

    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(profile or entry["tier"], []).append(entry)

    targets: list[dict] = []
    tier_of: dict[str, str] = {}
    for tier, tier_entries in sorted(grouped.items()):
        tier_targets = targets_for(
            [entry["ticker"] for entry in tier_entries], periods, tier,
            params_by_api=params_by_api,
            markets={entry["ticker"]: entry["market"] for entry in tier_entries},
        )
        for target in tier_targets:
            tier_of[_target_key(target)] = tier
        targets.extend(tier_targets)

    before = len(targets)
    if only_gaps and store is not None:
        # 判据与读取路径同源（`serves_target`）：只看「上次结果 ok」会让窗口更窄的旧记录
        # 永远补不上（独立复核 B5），而离线重建又把它报成缺口。
        targets = pending_targets(targets, store.serves_target)
    report = estimate(targets, profile or "按清单档位", tiers=tier_of)
    report["requested"] = before
    report["skipped_complete"] = before - len(targets)
    return {"targets": targets, "estimate": report, "profile": profile or "按清单档位"}


def _target_key(target: dict) -> str:
    from .store import target_key

    return target_key(target)


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def _target_summary(target: dict) -> dict:
    return {key: target.get(key) for key in ("ticker", "dataset", "period", "params")}


class PullBatch:
    """一个批次：逐目标取数、去重、断点续跑、四类计数。"""

    def __init__(self, store, targets, profile: str, access, *, token: str = "",
                 tier_label: str = "", clock=None):
        self.store = store
        self.targets = [dict(target) for target in targets]
        registry.profile_datasets(profile)  # 未知档位立刻报错
        self.profile = profile
        self.access = access
        self.token = token
        self.tier_label = tier_label
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def create_id() -> str:
        return uuid.uuid4().hex

    def run(self, *, batch_id: str | None = None, confirm: bool = False,
            force: bool = False) -> dict:
        if not self.token:
            raise NoToken("未配置 Tushare token",
                          hint="设置 TUSHARE_TOKEN 或在项目 .env 中配置后重试。")
        if not self.targets:
            raise UsageError("采集目标清单不能为空")
        batch_id = batch_id or self.create_id()
        estimate_size = len(self.targets)
        if not confirm:
            raise QuotaConfirmRequired(
                f"本批（{self.profile}）预计 {estimate_size} 次请求，需要显式确认。",
                hint="复核调用量后使用 --yes 确认。",
            )
        batch = self.store.load_batch(batch_id) or {
            "batch_id": batch_id, "profile": self.profile, "targets": self.targets,
            "status": "pending", "completed": {}, "created_at": self.clock().isoformat(),
            "estimate": estimate_size,
            "progress": {"completed": 0, "total": len(self.targets)},
            "usage": {"new_requests": 0, "archive_hits": 0},
        }
        if batch.get("profile") != self.profile:
            raise UsageError("恢复批次的配额档案与原批次不一致")
        batch.setdefault("progress", {"completed": len(batch.get("completed") or {}),
                                      "total": len(batch["targets"])})
        if batch.get("status") == "running" and _pid_alive(batch.get("owner_pid")):
            raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束，或确认旧进程已退出后再恢复。")

        lock_path = self.store.batches_dir / f"{batch_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = None
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                lock_pid = int(lock_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                lock_pid = None
            if _pid_alive(lock_pid):
                raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束。") from exc
            lock_path.unlink(missing_ok=True)
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as retry_exc:
                raise BatchRunning("该采集批次正在运行", hint="等待当前批次结束。") from retry_exc
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)

        batch.update(status="running", owner_pid=os.getpid(),
                     heartbeat_at=self.clock().isoformat())
        batch.setdefault("usage", {"new_requests": 0, "archive_hits": 0})
        # 档位标签与配额档案要落到**每条记录**上（`AC-3` 的「档位标签」+ `REQ-009.4` 的
        # `AC-4.7` 语义）：独立复核 B4 抓到 `--tier-label` 是个死选项——批次收了它却从不
        # 传给取数门面，于是仓里的 `tier_label` / `quota_profile` 永远是空。
        self.access.tier_label = self.tier_label or self.access.tier_label
        self.access.quota_profile = self.profile
        # 批次 id 同理：记录要能回答「这批数据是哪一次拉的」。
        self.access.batch_id = batch_id
        self.store.append_batch(batch)
        try:
            for target in batch["targets"]:
                key = _target_key(target)
                existing = (batch.get("completed") or {}).get(key)
                if existing and not force:
                    continue
                if not force and self.store.result_of(target) in DONE_KINDS \
                        and self.store.serves_target(target):
                    result = self.store.result_of(target)
                    batch["completed"][key] = {**_target_summary(target), "result": result,
                                               "error_excerpt": None}
                    batch["usage"]["archive_hits"] = batch["usage"].get("archive_hits", 0) + 1
                    batch["progress"]["completed"] = len(batch["completed"])
                    self.store.append_batch(batch)
                    continue
                # 「新增请求」只数**真的发出去的请求**：命中存档的分支有两处
                # （上面按精确键跳过、以及取数门面按「记录服务请求」命中），
                # 早先的实现把后者也计成新增请求——独立复核 B3 在真实仓上实测
                # 7 个「新增请求」里只有 4 次真的出网。
                calls_before = self.access.remote_calls
                try:
                    frame = self.access.call(target["dataset"], **target["params"])
                    result, _ = classify_result(data=frame)
                    excerpt = None
                except Exception as exc:  # noqa: BLE001（逐目标记账，批次继续）
                    result, excerpt = classify_result(error=exc)
                if result in DONE_KINDS and self.access.remote_calls == calls_before:
                    batch["usage"]["archive_hits"] = batch["usage"].get("archive_hits", 0) + 1
                else:
                    batch["usage"]["new_requests"] = batch["usage"].get("new_requests", 0) + 1
                batch["completed"][key] = {**_target_summary(target), "result": result,
                                           "error_excerpt": excerpt}
                batch["progress"]["completed"] = len(batch["completed"])
                batch["heartbeat_at"] = self.clock().isoformat()
                self.store.append_batch(batch)

            summary = completeness(batch["completed"].values())
            batch.update(status="partial" if summary["gaps"] else "done", owner_pid=None,
                         heartbeat_at=self.clock().isoformat(), summary=summary,
                         usage={**batch["usage"],
                                "failures": summary["counts"]["error"]
                                + summary["counts"]["rate_limited"],
                                "no_permission": summary["counts"]["no_permission"]})
            self.store.append_batch(batch)
            return batch
        except KeyboardInterrupt:
            batch.update(status="paused", owner_pid=None,
                         heartbeat_at=self.clock().isoformat(),
                         progress={"completed": len(batch["completed"]),
                                   "total": len(batch["targets"])})
            self.store.append_batch(batch)
            raise
        except Exception:
            batch.update(status="failed", owner_pid=None,
                         heartbeat_at=self.clock().isoformat(),
                         progress={"completed": len(batch["completed"]),
                                   "total": len(batch["targets"])})
            self.store.append_batch(batch)
            raise
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def summary_line(batch: dict) -> str:
        usage = batch.get("usage", {})
        summary = batch.get("summary") or {"counts": {}}
        counts = summary.get("counts", {})
        return (f"批次 {batch['batch_id']}：{batch['status']}；"
                f"新增请求 {usage.get('new_requests', 0)} / "
                f"命中存档 {usage.get('archive_hits', 0)} / "
                f"失败 {counts.get('error', 0) + counts.get('rate_limited', 0)} / "
                f"无权限 {counts.get('no_permission', 0)}")


def batch_json(batch: dict) -> str:
    return json.dumps(batch, ensure_ascii=False, indent=2)
