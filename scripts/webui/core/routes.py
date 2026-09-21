"""内核对外的框架级接口（不含任何业务）。

- `GET /api/v1/healthz` —— 存活与版本（AC-3.1）
- `GET /api/v1/nav` —— 导航（AC-3.3）
- `GET /api/v1/pages/{page_id}` —— 页面描述 + 已渲染面板（AC-3.3）
- `GET /api/v1/panels/{panel_id}` —— 单个面板（参数从查询串来）
- `GET /api/v1/kinds` —— 服务端认识的 kind 与降级说明（AC-3.3）
- `GET /api/v1/registry` —— 注册表快照（诊断用：谁注册了什么、数据集有哪些）

业务接口一律由插件注册；这里出现具体业务就是 AC-9 的违规。
"""

from __future__ import annotations

from .. import API_VERSION, __version__
from ..render import panels as panel_render
from . import envelope
from .errors import InvalidParam
from .security import reject_shell_metachars


def _coerce(param, raw: str):
    """按声明把查询串里的字符串转成目标类型；转不了就 422，不静默用默认值。"""
    if param.type in ("int", "float"):
        try:
            return int(raw) if param.type == "int" else float(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidParam(
                f"参数 {param.name!r} 需要 {param.type}，实际收到 {raw!r}"
            ) from exc
    if param.type == "bool":
        lowered = str(raw).strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise InvalidParam(f"参数 {param.name!r} 需要布尔值，实际收到 {raw!r}")
    if param.type == "enum" and param.choices and raw not in param.choices:
        raise InvalidParam(
            f"参数 {param.name!r} 只能是 {list(param.choices)} 之一，实际收到 {raw!r}"
        )
    reject_shell_metachars(param.name, raw)
    return raw


def resolve_params(spec, ctx) -> dict:
    """按面板/按键声明的参数从查询串取值、校验。缺必填 → 422。"""
    values = {}
    for param in spec.params:
        if param.name in ctx.query:
            values[param.name] = _coerce(param, ctx.query[param.name])
            continue
        if param.required:
            raise InvalidParam(
                f"缺少必填参数 {param.name!r}",
                hint=f"面板 {spec.id!r} 需要该参数（来自当前选择或手工传入）。",
            )
        if param.default is not None:
            values[param.name] = param.default
    return values


def _panel_data(spec, ctx):
    if spec.provider is None:
        return None
    return spec.provider(ctx, **resolve_params(spec, ctx))


def _render_one(spec, ctx) -> dict:
    data = _panel_data(spec, ctx)
    return panel_render.render_panel(spec, data)


def install_core_routes(registry, config) -> None:
    """把这些框架接口挂到注册表上（内核自己的路由，不算插件）。"""

    with registry.contribution_from("core"):
        registry.route("GET", f"/api/{API_VERSION}/healthz", _healthz)
        registry.route("GET", f"/api/{API_VERSION}/nav", _nav)
        registry.route("GET", f"/api/{API_VERSION}/pages/{{page_id}}", _page)
        registry.route("GET", f"/api/{API_VERSION}/panels/{{panel_id}}", _panel)
        registry.route("GET", f"/api/{API_VERSION}/kinds", _kinds)
        registry.route("GET", f"/api/{API_VERSION}/registry", _registry_snapshot)


def _healthz(ctx, **_):
    return envelope.ok(
        {
            "status": "ok",
            "version": __version__,
            "api_version": API_VERSION,
            "host": ctx.config.host,
            "port": ctx.config.port,
        }
    )


def _nav(ctx, **_):
    items = [item.to_json() for item in ctx.registry.nav_items()]
    return envelope.ok({"items": items})


def _page(ctx, page_id: str, **_):
    item = ctx.registry.page(page_id)
    rendered = [_render_one(ctx.registry.panel_spec(panel_id), ctx) for panel_id in item.panels]
    payload = ctx.registry.page_payload(page_id)
    payload["panels"] = rendered
    return envelope.ok(payload)


def _panel(ctx, panel_id: str, **_):
    spec = ctx.registry.panel_spec(panel_id)
    return envelope.ok(_render_one(spec, ctx))


def _kinds(ctx, **_):
    return envelope.ok(
        {
            "server_kinds": list(panel_render.SERVER_KINDS),
            "client_kinds": list(panel_render.CLIENT_KINDS),
            "fallback_kind": "fallback",
            "unknown_kind_policy": "degrade_to_fallback_card",
        }
    )


def _registry_snapshot(ctx, **_):
    """诊断用：谁注册了什么。冲突在注册时已经报错，这里给的是「已生效」的事实。"""
    origins = ctx.registry.origins()
    return envelope.ok(
        {
            "nav": [item.id for item in ctx.registry.nav_items()],
            "panels": sorted(name for (kind, name) in origins if kind == "panel"),
            "datasets": ctx.registry.datasets(),
            "commands": [spec.id for spec in ctx.registry.commands()],
            "routes": [f"{route.method} {route.template}" for route in ctx.registry.routes()],
            "job_types": list(ctx.registry.job_types()),
        }
    )
