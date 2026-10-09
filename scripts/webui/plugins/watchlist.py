"""REQ-014：清单动作声明、输入与服务端绑定；不接收使用者传入的路径。"""
from dataclasses import dataclass
from pathlib import Path
import sys

from ..config import REPO_ROOT
from ..core.errors import BadRequest, WebUIError
from ..core.models import CommandSpec, CommandStep, JobTypeSpec
from .commands import scan_cli_params


def _entries(ctx):
    from datalayer.store import DataStore
    from datalayer.universe import Universe
    from datalayer.errors import StoreUnavailable
    try:
        return Universe(DataStore(ctx.config.archive_root)).entries()
    except StoreUnavailable as exc:
        raise WebUIError("自选股清单存储暂不可用", hint="检查存储的读写权限后刷新页面。") from exc


@dataclass(frozen=True)
class WatchlistAction(JobTypeSpec):
    operation: str = ""

    def inputs_for(self, ctx):
        if self.operation == "add":
            return [{"name": "ticker", "label": "公司代码", "required": True,
                     "placeholder": "例如 600887.SH、600887 或 600887_伊利"},
                    {"name": "name", "label": "公司简称", "required": True,
                     "placeholder": "例如 伊利股份"}]
        if self.operation == "remove":
            try:
                entries = _entries(ctx)
            except WebUIError:
                entries = []  # 预检已给出存储不可用理由，选择器保持空状态。
            return [{"name": "ticker", "label": "停止跟踪的公司", "required": True,
                     "choices": [{"value": e["ticker"], "label": f"{e['ticker'].split('.')[0]} {e['display_name']}"}
                                 for e in entries]}]
        return []

    def prepare(self, ctx, params, context):
        from watchlist_action import resolve_ticker, clean_name
        from datalayer.errors import UniverseError
        allowed = {"ticker", "name"} if self.operation == "add" else (
            {"ticker"} if self.operation == "remove" else set())
        if set(params) - allowed:
            raise BadRequest("清单操作只接受页面里的公司信息")
        values = {"archive_root": str(Path(ctx.config.archive_root).resolve()),
                  "output_root": str(Path(ctx.config.output_root).resolve())}
        try:
            if self.operation != "import":
                if not isinstance(params.get("ticker"), str):
                    raise UniverseError("请填写或选择公司代码")
                values["ticker"] = resolve_ticker(params["ticker"])
            if self.operation == "add":
                if not isinstance(params.get("name"), str):
                    raise UniverseError("请填写公司简称")
                values["name"] = clean_name(params["name"])
            if self.operation == "remove" and values["ticker"] not in {e["ticker"] for e in _entries(ctx)}:
                raise UniverseError("这家公司已不在清单里，请刷新后重新选择")
        except UniverseError as exc:
            raise BadRequest(str(exc)) from exc
        return values


def _store_blockers(ctx, selection):
    _entries(ctx)
    return {"blockers": []}


def _remove_blockers(ctx, selection):
    return {"blockers": [] if _entries(ctx) else ["自选股清单还是空的：先添加公司或从既有产物导入。"]}


def _import_blockers(ctx, selection):
    _entries(ctx)
    root = Path(ctx.config.output_root)
    available = root.is_dir() and any(p.is_dir() and not p.name.startswith(".") for p in root.iterdir())
    return {"blockers": [] if available else ["还没有既有公司产物，可以先添加公司。"]}


def contribute(registry):
    script = REPO_ROOT / "scripts" / "watchlist_action.py"
    registry.command(CommandSpec(id="watchlist_maintain", title="维护自选股清单", group="清单",
                                 argv=(sys.executable, "-m", "scripts.watchlist_action"),
                                 params=tuple(scan_cli_params(script))))
    for operation, title, description, preflight in (
        ("add", "添加公司", "填写公司代码与简称，把它加入自选股清单；已有公司不会重复添加。", _store_blockers),
        ("import", "从既有产物导入", "把当前已接入的公司产物导入清单；展示导入与跳过家数，并逐项说明跳过原因。", _import_blockers),
        ("remove", "移除公司", "停止跟踪选中的公司，只改自选股清单；数据包、公司记录与其他产物均保留。", _remove_blockers),
    ):
        bind = {"operation": operation, "archive_root": "{archive_root}", "output_root": "{output_root}"}
        if operation != "import":
            bind["ticker"] = "{ticker}"
        if operation == "add":
            bind["name"] = "{name}"
        registry.job_type(WatchlistAction(
            id=f"universe.{operation}", title=title, description=description, operation=operation,
            group="自选股清单", order={"add": 1, "import": 2, "remove": 3}[operation],
            steps=(CommandStep("watchlist_maintain", title=title, bind=bind),),
            effects={"network": False, "quota": False, "writes": ("自选股清单",)},
            danger=operation == "remove", preflight=preflight,
            confirm={"title": "停止跟踪这家公司？", "body": "只从自选股清单移除，所有公司产物均会保留。",
                     "confirm_label": "停止跟踪"} if operation == "remove" else {},
        ))
