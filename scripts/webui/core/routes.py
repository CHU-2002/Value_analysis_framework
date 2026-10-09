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


def _panel_meta(data) -> dict:
    """面板数据里的 `meta` 提到**载荷顶层**（`REQ-012.3` 的 `AC-6`）。

    新鲜度徽标（数据生成时间 / 命中缓存 / 指纹）是**框架级**的信息，前端只该从一处读
    （`panel.meta`）。provider 把它放在自己的 `data` 里（图表面板就是这么写的）是面板的
    内部实现——内核负责统一搬上来，否则「`panel.meta` 一直有下发」这句话是假的：
    实测前端读的是顶层、provider 写的是 `data.meta`，两处永远对不上，徽标永远不显示。
    """
    if isinstance(data, dict):
        meta = data.get("meta")
        if isinstance(meta, dict):
            return dict(meta)
    return {}


def _render_one(spec, ctx) -> dict:
    data = _panel_data(spec, ctx)
    return panel_render.render_panel(spec, data, meta=_panel_meta(data))


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
            # 真实绑定端口：`--port 0` 时 config.port 是 0，诊断接口不能撒谎（D7）。
            # 值来自 RequestContext（由服务在每次请求里注入），不挂在注册表上（复验 N8）。
            "port": ctx.bound_port or ctx.config.port,
            "configured_port": ctx.config.port,
        }
    )


def _nav(ctx, **_):
    """导航树 + 默认落地页。

    `REQ-012.1`：`items` 是**树**（每项可带 `children`），每项带 `requires` / `default`。
    另外回 `default_page`（谁声明 `default=True` 谁生效），前端据此决定冷开落在哪一页——
    核心仍然不认识任何具体页面 id。
    """
    items = [item.to_json() for item in ctx.registry.nav_items()]
    try:
        default_page = ctx.registry.default_page_id()
    except WebUIError:  # pragma: no cover - 空注册表
        default_page = ""
    return envelope.ok({"items": items, "default_page": default_page})


def _degraded_panel(spec, code: str, message: str, hint: str, *, unexpected: bool = False) -> dict:
    """把失败的面板渲染成降级卡片载荷（D3）。"""
    payload = panel_render.render_panel(
        spec, None, meta={"degraded": True, "error": {"code": code, "unexpected": unexpected}}
    )
    payload["html"] = panel_render.render_panel_error(spec, code, message, hint)
    payload["render"] = "server"
    payload["fallback"] = True
    return payload


def _context_empty_state(item, ctx):
    """页面声明的上下文没满足时，回**正常空状态**而不是「渲染失败」（`REQ-012.1` 的 `AC-1.1`）。

    背景（真实走查抓到）：缺公司时前端 shell 已经不挂面板了，但服务端仍然逐个渲染、
    然后把失败写进信封 `warnings`，前端横幅照原文显示「面板 report.view 渲染失败：
    BAD_REQUEST」——**降级卡没了、错误码还在主视觉**，`AC-10` 的广义读法不成立。

    所以这里在**服务端**就把「缺上下文」判定掉：不渲染面板、不产生 warnings，改回一份
    空状态描述（`message` / `hint` / `supports`），前端按 `supports` 决定给哪些入口按钮。
    核心只比对 `requires` 里的字符串与查询串，不解释任何业务。

    **两种「不满足」**（门② 第四轮补的第二种）：

    - 上下文**没给**（URL 与选择器都空）；
    - 上下文**给了但解析不出来**（记住了上次选择、而产物被删/改名/换了根目录）——
      交给插件声明的 `context_resolver` 判断，核心不解释业务。
    """
    for key in item.requires:
        name = key.split(".", 1)[-1]
        value = (ctx.query.get(name) or "").strip()
        if value and item.context_resolver is not None:
            try:
                resolvable = bool(item.context_resolver(ctx, value))
            except Exception:  # noqa: BLE001（解析器自己炸了不该打崩页面：当作解析不出来）
                resolvable = False
            if not resolvable:
                return {
                    "reason": f"{key}.unresolved",
                    "message": "这家公司找不到了",
                    "hint": "页面记住的上次选择在这份数据里已经不存在了（可能是产物被移走、"
                            "改名，或者换了数据目录）。用右上角的「当前公司」重新选一家。",
                    "supports": ["company_picker", "home_link"],
                }
            continue
        if not value:
            if key == "selection.company":
                return {
                    "reason": key,
                    "message": "先选一家公司",
                    "hint": "这一页展示的是某一家公司的内容。用右上角的「当前公司」选一家，"
                            "或从工作台的公司列表点进去。",
                    "supports": ["company_picker", "home_link"],
                }
            return {
                "reason": key,
                "message": "这一页还需要一个上下文",
                "hint": f"缺的是 {name}：在页面上先选好它再打开这一页。",
                "supports": [],
            }
    return None


def _page(ctx, page_id: str, **_):
    """页面聚合**逐面板隔离**：某一块挂了只降级那一块，不带崩整页（独立验收 D3）。

    例外是「页面声明的上下文没满足」：那是**正常空状态**，不是故障——见 `_context_empty_state`。
    """
    item = ctx.registry.page(page_id)
    empty_state = _context_empty_state(item, ctx)
    if empty_state is not None:
        payload = ctx.registry.page_payload(page_id)
        payload["panels"] = []
        payload["empty_state"] = empty_state
        return envelope.ok(payload)
    rendered = []
    warnings = []
    for panel_id in item.panels:
        spec = ctx.registry.panel_spec(panel_id)
        try:
            rendered.append(_render_one(spec, ctx))
        except WebUIError as exc:
            # 降级也必须**留痕**：否则「页面看起来正常」会掩盖真实故障（复验 N6）。
            if ctx.log:
                ctx.log(f"面板 {panel_id} 渲染失败：{exc.code} {exc.message}")
            warnings.append(f"面板 {panel_id} 渲染失败：{exc.code}")
            rendered.append(_degraded_panel(spec, exc.code, exc.message, exc.hint))
        except Exception as exc:  # noqa: BLE001（未预期异常也只降级这一块）
            if ctx.log:
                import traceback as _traceback

                ctx.log(
                    f"面板 {panel_id} 渲染出现未预期异常：{type(exc).__name__}: {exc}\n"
                    f"{_traceback.format_exc()}"
                )
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
