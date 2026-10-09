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
CLIENT_KINDS = ("chart", "form", "jobs", "actions")
KNOWN_KINDS = SERVER_KINDS + CLIENT_KINDS

_STATE_CLASS = {"ok": "state-ok", "warn": "state-warn", "error": "state-error"}


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
    # 空表要给**引导**而不是空白（`REQ-012.1` 的 `AC-1.3`）：provider 可以给出
    # `guide`/`empty_hint`，这里渲染成一块可读的空状态。
    if not rows:
        guide = str((data or {}).get("guide") or (data or {}).get("empty_hint") or "").strip()
        if guide:
            return (
                '<div class="panel-table panel-table-empty">'
                f'<p class="panel-empty-state-inline">{_escape(guide)}</p></div>'
            )
    if not columns:
        return _empty("没有列定义")
    head = "".join(
        _table_head_html(col, index) for index, col in enumerate(columns)
    )
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="align-{_escape(col.get("align", "left"))}">{_cell_html(col, row)}</td>'
            for col in columns
        )
        body.append(f"<tr>{cells}</tr>")
    # 通用表格能力的**服务端契约**（`AC-3.4`）：声明式控件标记 + 客户端按这些属性接管
    # 搜索/排序/分页。放在服务端产出，CI 因此能断言「声明了 search 的表就有搜索控件」。
    controls = [name for name in ("search", "sort", "page")
                if _table_option(data, name)]
    attrs = ""
    if controls:
        attrs = f' data-table-controls="{",".join(controls)}"'
        attrs += f' data-page-size="{int(_table_option(data, "page") or 50)}"'
    return (
        f'<div class="panel-table"{attrs}><table><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        f'<p class="panel-note">{len(rows)} 行</p></div>'
    )


def _table_option(data: dict, name: str):
    table = ((data or {}).get("table") or {})
    return table.get(name)


def _table_head_html(col: dict, index: int) -> str:
    title = _escape(col.get("title", col.get("key", "")))
    attrs = ""
    if col.get("sort"):
        attrs = (f' data-sort-key="{_escape(col.get("key", ""))}"'
                 f' data-sort-type="{_escape(col.get("sort"))}"')
    return f'<th class="align-{_escape(col.get("align", "left"))}"{attrs}>{title}</th>'


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


def render_markdown(data: dict) -> str:
    """`data["html"]` 必须已经由 `render.markdown_safe` 转义+渲染过（AC-2.3）。

    `data["company"]` 有值时先渲染一行「公司：`600887 伊利股份`」——`REQ-012.1` 的 `AC-1`
    要求「页面标题或面包屑上能看出当前公司」，而报告正文里**未必**写着公司名。
    """
    html = (data or {}).get("html") or ""
    company = (data or {}).get("company") or {}
    head = ""
    if company.get("display_name"):
        head = f'<p class="panel-company">公司：{_escape(company["display_name"])}</p>'
    return f'<div class="panel-markdown">{head}{html}</div>'


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
    """面板渲染失败时的降级卡片（独立验收 D3；文案契约见 `REQ-012.3` 的 `AC-10`）。

    三件事必须同时成立：

    1. 一个面板挂掉不能让**整页**失败——同页其他面板照常显示；
    2. 主视觉是**人话**（发生了什么 + 怎么办），标题里不出现 `BAD_REQUEST` 这类错误码；
    3. 错误码与面板 id 收进折叠的「技术细节」——审计价值不丢，只是不再占据主视觉。
    """
    hint_html = f'<p class="panel-problem-hint">下一步：{_escape(hint)}</p>' if hint else ""
    return (
        '<div class="panel-problem panel-error">'
        '<p class="panel-problem-title panel-error-title">这块内容暂时看不到</p>'
        f'<p class="panel-problem-detail panel-error-detail">{_escape(message)}</p>'
        f"{hint_html}"
        '<details class="panel-technical">'
        "<summary>技术细节</summary>"
        f'<p class="panel-error-code">错误码：{_escape(code)}</p>'
        f'<p class="panel-note">面板 id：{_escape(spec.id)}</p>'
        "</details></div>"
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

    **声明式控件要真的接上**（`REQ-012.3` 的 `AC-8`）：面板在 `options.table` 里声明的
    搜索/排序/分页，以及 provider 自己给的 `table` 键，都会并进渲染数据，否则
    `render_table` 里那段产出 `data-table-controls` 的分支就是**不可达的死代码**
    （实测踩到：契约写得漂亮、浏览器里一个标记都没有）。
    """
    payload = spec.to_json()
    if spec.kind in SERVER_KINDS:
        merged = dict(data or {})
        merged.setdefault("table", dict(spec.options.get("table") or {}))
        renderer = {
            "table": render_table,
            "timeline": render_timeline,
            "stat": render_stat,
            "markdown": render_markdown,
            "fallback": lambda _data: render_fallback(spec),
        }[spec.kind]
        payload["html"] = renderer(merged)
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
