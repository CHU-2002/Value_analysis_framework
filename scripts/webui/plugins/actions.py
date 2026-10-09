"""动作（`REQ-012.2`）：把「按键」升级为「任务」。

三条边界，都是刻意画在这里的：

1. **动作只编排既有按键**，业务实现一行都不搬进来（`REQ-009` 的约束「面板是入口不是副本」）。
   `steps` 里引用的全是 `commands.py::ENTRIES` 里的白名单命令；
2. **参数不手敲**（`AC-2.1` / `AC-3`）：`company` / 期次 / run 这些值由**当前上下文**给，
   动作声明里只写占位符（`{ticker}` / `{company_dir}` / `{run_dir}`）。界面上因此不出现
   `company_dir` / `output_dir` / `run_dir` / `--only-gaps` 这类内部名字；
3. **预检在服务端**（`AC-4`）：缺 token、仓里没有这家公司、上游产物缺失都在这里判成
   「禁用 + 可读理由」，而不是等提交之后报错。

人机交接（`AC-2.4` 的修订条款）：模块 Agent 与 Final Synthesis Agent 只能在
Claude Code / OpenCode 里跑——仓库**没有程序化 LLM 入口**。所以「更新这家公司的分析」
中间的 LLM 段写成一个 `HumanStep`：界面给出可复制的命令与已解析路径，任务停在
`awaiting_agent`，用户回来说「跑完了」后由服务端**校验该步声明的产物**才继续。
"""

from __future__ import annotations

from ..core import envelope
from ..core.errors import WebUIError
from ..core.models import HumanStep, JobTypeSpec, CommandStep
from .companies import companies_dataset, resolve_company

#: `download_report` 不接受本项目形态的 ticker（`600887.SH`），它要 `SH600887`。
#: 这个换算放在**动作的参数解析**里，不放进界面、也不让用户填。
_MARKET_PREFIX = {"SH": "SH", "SZ": "SZ", "HK": "HK", "US": "US"}


def stock_code_of(ticker: str) -> str:
    """`600887.SH` → `SH600887`；已经是 `SH600887` 形态就原样返回。"""
    text = str(ticker or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        code, _, market = text.partition(".")
        prefix = _MARKET_PREFIX.get(market.upper(), market.upper())
        return f"{prefix}{code}"
    return text


# --------------------------------------------------------------- 预检


def _archive_store(ctx):
    """数据层仓的门面（只读用）。仓不可用返回 None——预检据此给「还没有原始仓」。"""
    from datalayer.store import DataStore

    try:
        return DataStore(ctx.config.archive_root)
    except Exception:  # noqa: BLE001（仓不可用是**正常**分支：还没拉过数据）
        return None


def _has_token() -> bool:
    from datalayer.security import resolve_token

    return bool(resolve_token())


def _pull_blockers(ctx, selection: dict) -> dict:
    """「拉取」类动作的前置：有凭据、清单里有启用的标的、没有正在跑的批次。"""
    blockers = []
    if not _has_token():
        blockers.append("没有配置数据源凭据：把 TUSHARE_TOKEN 放进项目 .env 或环境变量后重试。")
    store = _archive_store(ctx)
    if store is None:
        return {"blockers": blockers}
    try:
        from datalayer.universe import Universe

        entries = Universe(store).entries(enabled_only=True)
        if not entries:
            blockers.append("自选股清单是空的：先在数据页把要跟踪的公司加进清单。")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"读自选股清单失败：{type(exc).__name__}")
    else:
        try:
            batches = store.list_batches(limit=5)
        except Exception:  # noqa: BLE001
            batches = []
        running = [item for item in batches if item.get("status") == "running"]
        if running:
            blockers.append(f"已经有一个采集批次在跑（{running[0].get('batch_id', '')}）：等它结束或先恢复它。")
    return {"blockers": blockers}


def action_context(ctx, selection: dict) -> dict:
    """动作的参数上下文：把「当前公司」解析成**绝对路径**与规范标识。

    为什么路径必须在这里解析成绝对值：被编排的命令是**子进程**，它的工作目录是仓库根。
    把 `output/600887_伊利` 这样的相对路径交给它，产物会落到「跟仓库有关」的地方，
    而界面上显示的是另一个路径——用户看到「产物在这」但那里什么都没有。
    这是实测踩到的坑（`sys.argv[1]` 拿到相对路径 → 写到仓库根而不是公司目录）。
    """
    context = {key: value for key, value in (selection or {}).items() if value}
    company = str(selection.get("company") or "").strip()
    if not company:
        return context
    try:
        resolved = resolve_company(ctx, company)
    except WebUIError:
        return context
    context.update({
        "company": resolved["dir"],
        "company_dir": str(resolved["base"]),
        "ticker": resolved["ticker"],
        "display_name": resolved["display_name"],
        "stock_code": stock_code_of(resolved["ticker"]),
    })
    # 期次只在**解析出来**的时候才覆盖：产物里没写期次时不要用空串盖掉调用方给的值
    # （否则「先补一次取数」的提示和调用方传入的期次都会被无声吞掉）。其余键同理。
    for key, value in (("primary_period", resolved["primary_period"]),
                       ("display_name", resolved["display_name"]),
                       ("ticker", resolved["ticker"])):
        if value:
            context[key] = value
        else:
            context.setdefault(key, "")
    return context


# --------------------------------------------------------------- 预估（`AC-4`）


def _estimate_pull(ctx, selection: dict) -> dict:
    """「拉取全部数据」的调用量预估：直接问数据层要（`plan` + `estimate`），不另算一套。"""
    from .data_page import pull_estimate

    report = pull_estimate(ctx)
    if not report:
        return {}
    requests = int(report.get("requests") or 0)
    payload = {"requests": requests}
    if report.get("by_dataset"):
        payload["detail"] = "，".join(
            f"{name} {count}" for name, count in sorted(report["by_dataset"].items())
        )
    if report.get("by_tier"):
        payload["detail"] = (payload.get("detail", "") + "；按档位 " + "、".join(
            f"{tier} {count}" for tier, count in sorted(report["by_tier"].items())
        )).strip("；")
    return payload


def _estimate_fill_gaps(ctx, selection: dict) -> dict:
    """「只补缺口」的预估：只数缺口目标（这才是它省配额的理由，`AC-4.3`）。"""
    from .data_page import pull_estimate

    report = pull_estimate(ctx, only_gaps=True)
    if not report:
        return {}
    return {"requests": int(report.get("requests") or 0),
            "detail": f"只对缺口目标发起请求（计划里共 {int(report.get('total') or 0)} 个目标）"}


def _estimate_rebuild(ctx, selection: dict) -> dict:
    """「从仓重建」的预估：要读多少条记录（离线，不花钱——文案里已经说了）。"""
    store = _archive_store(ctx)
    if store is None:
        return {}
    ticker = ""
    company = str(selection.get("company") or "").strip()
    if company:
        try:
            ticker = str(resolve_company(ctx, company)["ticker"] or "")
        except WebUIError:
            ticker = ""
    try:
        records = store.records(ticker=ticker or None, include_rows=False)
    except Exception:  # noqa: BLE001
        return {}
    if not records:
        return {}
    return {"records": len(records),
            "detail": f"要读 {len(records)} 条仓内记录（离线，不联网）"}


def _company_blockers(ctx, selection: dict) -> dict:
    """公司级动作的前置：选了公司、这家公司在产物里有目录、有数据包。"""
    blockers = []
    company = selection.get("company") or ""
    if not company:
        return {"blockers": blockers}     # requires 已经给了「还没选公司」的理由
    try:
        resolved = resolve_company(ctx, company)
    except WebUIError as exc:
        blockers.append(f"{exc.message}：{exc.hint}" if exc.hint else exc.message)
        return {"blockers": blockers}
    if not (resolved["base"] / "data_pack_market.md").is_file():
        blockers.append("这家公司还没有数据包：先在数据页拉取它的数据，或从原始仓离线重建。")
    return {"blockers": blockers}


def _rebuild_blockers(ctx, selection: dict) -> dict:
    """「从仓重建」的前置：原始仓里有这家公司的记录。离线、不花钱。"""
    blockers = []
    company = selection.get("company") or ""
    if not company:
        return {"blockers": blockers}
    store = _archive_store(ctx)
    if store is None:
        return {"blockers": ["还没有原始仓：先在数据页拉取数据。"]}
    try:
        resolved = resolve_company(ctx, company)
    except WebUIError as exc:
        blockers.append(f"{exc.message}：{exc.hint}" if exc.hint else exc.message)
        return {"blockers": blockers}
    ticker = resolved["ticker"]
    if not ticker:
        blockers.append("这家公司在产物里没有登记标的代码：先补一次取数，把标的写进记录。")
        return {"blockers": blockers}
    try:
        records = store.records(ticker=ticker, include_rows=False, limit=1)
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"读原始仓失败：{type(exc).__name__}")
        return {"blockers": blockers}
    if not records:
        blockers.append("原始仓里还没有这家公司的数据：先拉取一次（联网），之后重建都能离线做。")
    return {"blockers": blockers}


# --------------------------------------------------------------- 动作声明


def _steps_pull_all() -> tuple:
    return (
        CommandStep(
            "datalayer_pull",
            title="拉取全部数据（联网）",
            bind={"yes": True},
            writes=("output/",),
        ),
    )


def _steps_fill_gaps() -> tuple:
    return (
        CommandStep(
            "datalayer_pull",
            title="只补缺口（联网）",
            bind={"only_gaps": True, "yes": True},
        ),
    )


def _steps_rebuild() -> tuple:
    return (
        CommandStep(
            "datalayer_rebuild",
            title="从原始仓重建数据包（离线）",
            bind={"ticker": "{ticker}", "output_root": "output"},
            capture={"data_pack": r"^离线重建\s+\S+\s+→\s+(.+)$"},
            writes=("{company_dir}/data_pack_market.md",),
        ),
    )


def _steps_update_analysis() -> tuple:
    """「更新这家公司的分析」：确定性前段 →（人机交接）→ 确定性后段。

    刻意**不**在界面里跑 `prepare`：`prepare` 需要 `--output-dir`，而那个值由 agent 段
    用 `runs new` 定下来（`/update-analysis` 提示词里就是这么做的）。GUI 能做的正好是
    **两端的确定性步骤** + 校验交接产物——这也是 `AC-2.4` 修订后的语义。
    """
    return (
        HumanStep(
            title="在 Claude Code / OpenCode 里跑这次分析",
            slash="/update-analysis {ticker}",
            hint="模块 Agent 与 Final Synthesis Agent 需要 LLM，只能在 agent CLI 里跑；"
                 "跑完回到这个页面点「我跑完了，继续」，服务端会校验产物再继续后续步骤。",
            expects=(
                "{company_dir}/latest.json",
                "{company_dir}/runs/*/record.json",
            ),
            expect_kind="glob",
        ),
        CommandStep(
            "runs_resolve",
            title="确认这次 run 的目录",
            bind={"company_dir": "{company_dir}"},
            capture={"run_dir": r"^(\S+)$"},
            writes=("{run_dir}",),
        ),
        CommandStep(
            "runs_finish",
            title="把这次 run 记进迭代台账",
            bind={
                "company_dir": "{company_dir}",
                "run_dir": "{run_dir}",
                "primary_period": "{primary_period}",
                "status": "{enum:complete|partial}",
            },
            writes=("{company_dir}/history.jsonl",),
        ),
        CommandStep(
            "analysis_status",
            title="更新「是否需要增量」的判定",
            bind={"company_dir": "{company_dir}"},
        ),
    )


def contribute(registry):
    registry.job_type(JobTypeSpec(
        id="data.pull_all",
        title="拉取全部数据",
        description="按自选股清单完整拉取一次原始数据（联网，会消耗调用配额）。",
        group="数据",
        order=10,
        steps=_steps_pull_all(),
        effects={"network": True, "quota": True, "writes": ("原始仓记录",), "confirm": True},
        danger=True,
        confirm={
            "title": "确认要联网拉取全部数据？",
            "body": "这一步会访问数据源、消耗调用配额，并把新记录写进原始仓。"
                    "已存在的记录默认不重拉；取消不会产生任何请求。",
            "confirm_label": "开始拉取",
        },
        preflight=_pull_blockers,
        estimate=_estimate_pull,
    ))
    registry.job_type(JobTypeSpec(
        id="data.fill_gaps",
        title="只补缺口",
        description="只拉「仓里还没有或上次失败」的目标，不重拉已有的数据（联网，消耗较少配额）。",
        group="数据",
        order=20,
        steps=_steps_fill_gaps(),
        effects={"network": True, "quota": True, "writes": ("原始仓记录",), "confirm": True},
        danger=True,
        confirm={
            "title": "确认只补缺口？",
            "body": "只对缺口目标发起请求；已完成的目标不会被重拉。取消不会产生任何请求。",
            "confirm_label": "开始补缺口",
        },
        preflight=_pull_blockers,
        estimate=_estimate_fill_gaps,
    ))
    registry.job_type(JobTypeSpec(
        id="data.rebuild",
        title="从原始仓重建数据包",
        description="完全离线：用原始仓里已有的数据重新生成数据包，不联网、不花配额。",
        group="数据",
        order=30,
        steps=_steps_rebuild(),
        effects={"network": False, "quota": False, "writes": ("数据包",)},
        requires=("selection.company",),
        preflight=_rebuild_blockers,
        # 离线重建的「成本」是仓里有多少条记录要读——也照实说（`AC-4` 的预计项）。
        estimate=lambda ctx, selection: _estimate_rebuild(ctx, selection),
    ))
    registry.job_type(JobTypeSpec(
        id="company.update_analysis",
        title="更新这家公司的分析",
        description="按最新财报期更新这家公司的分析：准备好一切 → 交接给 agent → 校验产物 → 收口。",
        group="公司",
        order=5,
        requires=("selection.company",),
        steps=_steps_update_analysis(),
        effects={"network": False, "quota": False,
                 "writes": ("分析 run", "迭代台账", "变化报告")},
        preflight=_company_blockers,
    ))


def actions_for(ctx) -> dict:
    """动作可用性快照（与 `GET /api/v1/actions` 同一份实现，供测试与面板复用）。"""
    from .commands import list_actions

    return list_actions(ctx)


__all__ = ["contribute", "stock_code_of", "actions_for"]
