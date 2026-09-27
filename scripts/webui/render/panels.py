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
SERVER_KINDS = ("table", "timeline", "stat", "markdown", "bars", "fallback")
# 客户端渲染（服务端只回数据/占位）
CLIENT_KINDS = ("chart", "form", "jobs")
KNOWN_KINDS = SERVER_KINDS + CLIENT_KINDS

_STATE_CLASS = {"ok": "state-ok", "warn": "state-warn", "error": "state-error"}
# 分段条允许的状态（白名单：数据里的字符串只用于拼 CSS 类，不能让任意值进来）
BAR_STATES = ("ok", "empty", "denied", "limited", "error", "neutral")
_DEFAULT_BAR_STATE = "neutral"


def _escape(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def _cell_html(column: dict, row: dict) -> str:
    """单元格 HTML。列可声明 `href_key`：该行的这个键是**站内**链接。

    只允许 `#`（页内路由）与 `/`（同源接口）两种形式——表格数据来自文件系统，
    任何 `javascript:` / `//evil` 形式都不该被渲染成可点的链接。
    """
    text = _escape(row.get(column.get("key")))
    href_key = column.get("href_key")
    if href_key:
        href = str(row.get(href_key) or "").strip()
        if href.startswith("#") or (href.startswith("/") and not href.startswith("//")):
            return f'<a href="{_escape(href)}">{text}</a>'
    return text


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
            f'<td class="align-{_escape(col.get("align", "left"))}">{_cell_html(col, row)}</td>'
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
        title = _escape(item.get("title", ""))
        link = str(item.get("link") or "").strip()
        if link.startswith("#") or (link.startswith("/") and not link.startswith("//")):
            title = f'<a href="{_escape(link)}">{title}</a>'
        rendered.append(
            '<li class="timeline-item">'
            f'<div class="timeline-at">{_escape(item.get("at", ""))}</div>'
            f'<div class="timeline-body"><p class="timeline-title">{title}'
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


def render_bars(data: dict) -> str:
    """分段横条（`kind="bars"`）：一行一个对象，条上按「部分」分段着色，右侧一句读数。

    数据形状：

        {"rows": [{"label": "600887.SH · 20260630", "note": "4/4 · 100%",
                   "total": 4, "parts": [{"name": "已获取", "value": 4, "state": "ok"}]}],
         "legend": [{"name": "已获取", "state": "ok"}]}   # 可选，缺省时按各行求和

    为什么放在服务端：CI 没有浏览器，只有 Python 产出的 HTML 能被断言（AC-3.3）；
    而「一眼看懂」只需要分段宽度 + 图例，不需要 canvas——所以不引入前端图表库。
    数据里的 `state` 只用于拼 CSS 类，走白名单，任何值都不能注入标签/属性。
    """
    rows = list((data or {}).get("rows") or [])
    if not rows:
        return _empty("暂无数据")
    legend = _bar_legend(data, rows)
    legend_html = "".join(
        f'<span class="bar-legend-item"><i class="bar-swatch bar-{_bar_state(item)}"></i>'
        f'{_escape(item["name"])} {_escape(item["value"])}</span>'
        for item in legend
    )
    rendered = []
    for row in rows:
        total = row.get("total")
        if total is None:
            total = sum(int(part.get("value") or 0) for part in row.get("parts") or [])
        total = int(total or 0)
        segments = []
        for part in row.get("parts") or []:
            value = int(part.get("value") or 0)
            if value <= 0:
                continue
            width = 100.0 if total <= 0 else min(100.0, value * 100.0 / total)
            title = f'{part.get("name", "")} {value}'
            segments.append(
                f'<span class="bar-fill bar-{_bar_state(part)}" style="width:{width:.4g}%"'
                f' title="{_escape(title)}"></span>'
            )
        note = row.get("note") or (f"{total}" if total else "")
        rendered.append(
            '<div class="bar-row">'
            f'<div class="bar-label">{_escape(row.get("label", ""))}</div>'
            f'<div class="bar-track">{"".join(segments)}</div>'
            f'<div class="bar-note">{_escape(note)}</div></div>'
        )
    legend_block = f'<p class="bar-legend">{legend_html}</p>' if legend_html else ""
    return f'<div class="panel-bars">{legend_block}{"".join(rendered)}</div>'


def _bar_state(item: dict) -> str:
    state = str((item or {}).get("state") or "")
    return state if state in BAR_STATES else _DEFAULT_BAR_STATE


def _bar_legend(data: dict, rows: list) -> list:
    """图例：显式给就用（`[]` = 不显示）；否则把各行的部分按（状态, 名称）求和。

    什么时候该显式关掉：每行标签本身就是类别（例如「无权限 / 频率超限」），
    或同名列会因状态不同被拆成多份（例如 done/partial 的「已完成」）——那只会添乱。
    """
    explicit = (data or {}).get("legend")
    if explicit is not None:
        return list(explicit)
    totals: dict = {}
    order: list = []
    for row in rows:
        for part in row.get("parts") or []:
            key = (_bar_state(part), str(part.get("name") or ""))
            if key not in totals:
                totals[key] = 0
                order.append(key)
            totals[key] += int(part.get("value") or 0)
    return [{"state": state, "name": name, "value": totals[(state, name)]}
            for state, name in order]


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


def _caption(data) -> str:
    """面板数据里的可选 `caption`：说明「这块在看哪个对象」，插在正文最前面。

    为什么放在渲染层：默认选中（例如未选公司时退回第一家）不能是静默的，
    但每个 kind 各自拼一遍既重复又容易漏——统一在这里处理，服务端 kind 都有。
    """
    text = (data or {}).get("caption") if isinstance(data, dict) else ""
    return f'<p class="panel-caption">{_escape(text)}</p>' if text else ""


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
            "bars": render_bars,
            "fallback": lambda _data: render_fallback(spec),
        }[spec.kind]
        payload["html"] = _caption(data) + renderer(data or {})
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
