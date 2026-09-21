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
from .errors import InvalidParam, WebUIError
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
            # 真实绑定端口：`--port 0` 时 config.port 是 0，诊断接口不能撒谎（D7）
            "port": ctx.registry.bound_port or ctx.config.port,
            "configured_port": ctx.config.port,
        }
    )


def _nav(ctx, **_):
    items = [item.to_json() for item in ctx.registry.nav_items()]
    return envelope.ok({"items": items})


def _degraded_panel(spec, code: str, message: str, hint: str, *, unexpected: bool = False) -> dict:
    """把失败的面板渲染成降级卡片载荷（D3）。"""
    payload = panel_render.render_panel(
        spec, None, meta={"degraded": True, "error": {"code": code, "unexpected": unexpected}}
    )
    payload["html"] = panel_render.render_panel_error(spec, code, message, hint)
    payload["render"] = "server"
    payload["fallback"] = True
    return payload


def _page(ctx, page_id: str, **_):
    """页面聚合**逐面板隔离**：某一块挂了只降级那一块，不带崩整页（独立验收 D3）。"""
    item = ctx.registry.page(page_id)
    rendered = []
    warnings = []
    for panel_id in item.panels:
        spec = ctx.registry.panel_spec(panel_id)
        try:
            rendered.append(_render_one(spec, ctx))
        except WebUIError as exc:
            warnings.append(f"面板 {panel_id} 渲染失败：{exc.code}")
            rendered.append(_degraded_panel(spec, exc.code, exc.message, exc.hint))
        except Exception as exc:  # noqa: BLE001（未预期异常也只降级这一块）
            if ctx.log:
                ctx.log(f"面板 {panel_id} 渲染出现未预期异常：{type(exc).__name__}: {exc}")
            warnings.append(f"面板 {panel_id} 渲染失败：INTERNAL")
            rendered.append(
                _degraded_panel(
                    spec, "INTERNAL", "面板内部错误", "细节见服务端日志。", unexpected=True
                )
            )
    payload = ctx.registry.page_payload(page_id)
    payload["panels"] = rendered
    return envelope.ok(payload, warnings=warnings)


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
    return envelope.ok(
        {
            "nav": [item.id for item in ctx.registry.nav_items()],
            "panels": ctx.registry.panel_ids(),
            "datasets": ctx.registry.datasets(),
            "commands": [spec.id for spec in ctx.registry.commands()],
            "routes": [f"{route.method} {route.template}" for route in ctx.registry.routes()],
            "job_types": list(ctx.registry.job_types()),
            "origins": {
                f"{kind}:{key}": origin
                for (kind, key), origin in sorted(ctx.registry.origins().items())
            },
        }
    )
