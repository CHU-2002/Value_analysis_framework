"""面板渲染（AC-3.3）。

**为什么表格/时间线/指标卡放服务端渲染**：CI 里没有浏览器、没有 JS 运行时，
唯一能断言「渲染出来的东西对不对」的方式就是让 Python 产出它。图表需要 canvas，
服务端只回**数据契约**（`{labels, series}`），由浏览器画。

**未知 kind 一律降级**成可读卡片——不白屏、不 500。插件完全可以先注册一个
前端还没实现的 kind，页面仍然可用。
"""

from __future__ import annotations

from html import escape

# 服务端渲染（可在无浏览器环境断言）
SERVER_KINDS = ("table", "timeline", "stat", "markdown", "fallback")
# 客户端渲染（服务端只回数据/占位）
CLIENT_KINDS = ("chart", "form", "jobs")
KNOWN_KINDS = SERVER_KINDS + CLIENT_KINDS

_STATE_CLASS = {"ok": "state-ok", "warn": "state-warn", "error": "state-error"}


def _escape(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def render_table(data: dict) -> str:
    columns = list((data or {}).get("columns") or [])
    rows = list((data or {}).get("rows") or [])
    if not columns:
        return _empty("没有列定义")
    head = "".join(
        f'<th class="align-{_escape(col.get("align", "left"))}">{_escape(col.get("title", col.get("key", "")))}</th>'
        for col in columns
    )
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="align-{_escape(col.get("align", "left"))}">{_escape(row.get(col.get("key")))}</td>'
            for col in columns
        )
        body.append(f"<tr>{cells}</tr>")
    return (
        '<div class="panel-table"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        f'<p class="panel-note">{len(rows)} 行</p></div>'
    )


def render_timeline(data: dict) -> str:
    items = list((data or {}).get("items") or [])
    if not items:
        return _empty("暂无记录")
    rendered = []
    for item in items:
        badges = "".join(
            f'<span class="badge">{_escape(badge)}</span>' for badge in item.get("badges") or []
        )
        changes = item.get("changes") or []
        change_html = ""
        if changes:
            lines = "".join(f"<li>{_escape(change)}</li>" for change in changes)
            change_html = f'<div class="timeline-changes"><p>结论变化</p><ul>{lines}</ul></div>'
        detail = item.get("detail") or ""
        rendered.append(
            '<li class="timeline-item">'
            f'<div class="timeline-at">{_escape(item.get("at", ""))}</div>'
            f'<div class="timeline-body"><p class="timeline-title">{_escape(item.get("title", ""))}'
            f"{badges}</p>"
            f'<p class="timeline-detail">{_escape(detail)}</p>{change_html}</div></li>'
        )
    return f'<ol class="panel-timeline">{"".join(rendered)}</ol>'


def render_stat(data: dict) -> str:
    items = list((data or {}).get("items") or [])
    if not items:
        return _empty("暂无指标")
    cards = []
    for item in items:
        state_class = _STATE_CLASS.get(item.get("state", ""), "")
        cards.append(
            f'<div class="stat-card {state_class}">'
            f'<div class="stat-label">{_escape(item.get("label", ""))}</div>'
            f'<div class="stat-value">{_escape(item.get("value", ""))}</div>'
            f'<div class="stat-hint">{_escape(item.get("hint", ""))}</div></div>'
        )
    return f'<div class="panel-stat">{"".join(cards)}</div>'


def render_markdown(data: dict) -> str:
    """`data["html"]` 必须已经由 `render.markdown_safe` 转义+渲染过（AC-2.3）。"""
    html = (data or {}).get("html") or ""
    return f'<div class="panel-markdown">{html}</div>'


def render_fallback(spec, reason: str = "") -> str:
    """未知 kind 的降级卡片（AC-3.3）：说清是什么、要怎么办，而不是白屏。"""
    detail = reason or f"此面板需要更新的前端（kind={spec.kind}）"
    return (
        '<div class="panel-fallback">'
        f'<p class="panel-fallback-title">无法渲染面板：{_escape(spec.title or spec.id)}</p>'
        f'<p class="panel-fallback-detail">{_escape(detail)}</p>'
        f'<p class="panel-note">面板 id：{_escape(spec.id)}；数据仍可通过接口读取。</p></div>'
    )


def render_panel_error(spec, code: str, message: str, hint: str = "") -> str:
    """面板渲染失败时的降级卡片（独立验收 D3）。

    一个面板挂掉（provider 抛错、缺必填参数、数据集解析失败）不能让**整页**失败——
    同页其他面板照常显示，失败的这块用可读卡片说明原因。
    """
    hint_html = f'<p class="panel-note">{_escape(hint)}</p>' if hint else ""
    return (
        '<div class="panel-error">'
        f'<p class="panel-error-title">面板渲染失败：{_escape(spec.title or spec.id)}</p>'
        f'<p class="panel-error-code">{_escape(code)}</p>'
        f'<p class="panel-error-detail">{_escape(message)}</p>'
        f"{hint_html}"
        f'<p class="panel-note">面板 id：{_escape(spec.id)}</p></div>'
    )


def _empty(message: str) -> str:
    return f'<p class="panel-empty">{_escape(message)}</p>'


def render_panel(spec, data, *, meta=None) -> dict:
    """把一个面板渲染成前端可直接插入的载荷。

    - 服务端 kind：带 `html` 片段；
    - 客户端 kind：带 `endpoint` + `options`，数据由浏览器自己取/画；
    - 未知 kind：`render` 变成 `server` 且 html 是降级卡片。

    **客户端 kind 在服务端没有数据时必须不带 `data` 键**：前端的取数契约是
    「`data === undefined` 就去请求 `endpoint`」——写成 `data: null` 会让取数分支
    永远不可达（独立验收 D1，真实 `app.js` 驱动真实服务复现过）。
    """
    payload = spec.to_json()
    if spec.kind in SERVER_KINDS:
        renderer = {
            "table": render_table,
            "timeline": render_timeline,
            "stat": render_stat,
            "markdown": render_markdown,
            "fallback": lambda _data: render_fallback(spec),
        }[spec.kind]
        payload["html"] = renderer(data or {})
        payload["render"] = "server"
        if spec.kind == "fallback":
            payload["fallback"] = True
    elif spec.kind in CLIENT_KINDS:
        payload["render"] = "client"
        if data is not None:      # 没数据就**不写这个键**，让前端去 fetch（D1）
            payload["data"] = data
    else:
        payload["render"] = "server"
        payload["fallback"] = True
        payload["html"] = render_fallback(spec)
    if meta:
        payload["meta"] = dict(meta)
    return payload
