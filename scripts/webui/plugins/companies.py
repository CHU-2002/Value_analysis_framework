"""公司与产物视图（REQ-009.2：AC-2.1 公司列表、AC-2.2 产物索引、AC-2.3 安全渲染报告）。

三条安全边界，都落在**数据层与路由的入口**而不是靠调用方自觉：

- `dir` 先过 `safe_join(output_root, …)`：`..`、绝对路径、指向 `output/` 之外的符号链接一律 403；
- 报告接口只接受**产物索引里的 id**（相对路径的 sha1 前 12 位），不接受路径参数——
  否则「任意文件读取」只要构造一个路径就成立；
- Markdown 一律经 `render.markdown_safe` **先转义再渲染**，报告里的原始 HTML 不生效。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path

from ..core import envelope
from ..core.errors import ArtifactMissing, BadRequest, NotFound, WebUIError
from ..core.models import DatasetSpec, NavItem, PanelSpec, Param
from ..core.security import safe_join
from ..datastore import parsers
from ..render import markdown_safe

# 判定「这是一个公司目录」的依据：至少含其中一个产物。刻意不用「output/ 下所有目录」——
# `handoff/`、`portfolio_*/` 也在 output/ 下，它们不是公司。
COMPANY_MARKERS = (
    "*/data_pack_market.md",
    "*/qualitative_report.md",
    "*/record.json",
    "*/latest.json",
    "*/run_manifest.json",
    "*/value_computed.md",
    "*/buy_sell_plan.json",
    "*/*价值分析报告*.md",
)

_GROUP_RULES = (
    ("数据包", ("data_pack_market.md", "data_pack_report.md", "qualitative_input.json")),
    ("定性报告", ("qualitative_report.md", "business_analysis")),
    ("变化报告", ("change_report",)),
    ("估值与买卖", ("value_computed", "buy_sell")),
    ("迭代台账", ("history.jsonl", "latest.json", "record.json", "run_manifest.json", "run.json")),
    ("年报 PDF", (".pdf",)),
)
_MARKDOWN_SUFFIXES = (".md", ".markdown")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_id(relative: str) -> str:
    return hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]


def _group_of(name: str) -> str:
    lowered = name.lower()
    for group, patterns in _GROUP_RULES:
        if any(pattern in lowered for pattern in patterns):
            return group
    if "价值分析报告" in name:
        return "定性报告"
    return "其他"


def parse_companies(sources: list, params: dict) -> dict:
    """把命中的产物文件归到各自的公司目录，并带上最近一次 run 与期次（AC-2.1）。

    `REQ-012.1`（`AC-1.4` / `AC-9`）加了三样**派生**字段，全部由既有产物推出，不新增事实来源：

    - `display_name`：全站唯一的公司显示名（`600887 伊利股份`）。有 `record.json` 的
      `subject` 就用它，没有就退回目录名——**只在这一处算**，其它页面一律读它；
    - `label`：目录名（`600887_伊利`）——技术标识，只在详情里出现；
    - `ticker`：`600887.SH`，URL 与端点用的规范形式。
    """
    companies = []
    seen = set()
    for path in sorted(sources):
        directory = Path(path).parent
        # 隐藏目录（`.webui_cache/` 之类）不是公司目录：glob 会匹配到它们。
        if directory.name.startswith("."):
            continue
        if directory in seen:
            continue
        seen.add(directory)
        latest = _read_json(directory / "latest.json")
        record = _read_json(directory / "record.json")
        subject = record.get("subject") or {}
        ticker = str(subject.get("ticker") or "").strip()
        name = str(subject.get("company") or "").strip()
        companies.append(
            {
                "dir": directory.name,
                "name": directory.name,
                "label": directory.name,
                "ticker": ticker,
                "company": name,
                "display_name": display_name(ticker, name, directory.name),
                "last_run": latest.get("run_id") or record.get("latest_run") or "",
                "primary_period": latest.get("primary_period") or record.get("primary_period") or "",
                "downstream_stale": bool((record.get("downstream") or {}).get("stale")),
            }
        )
    companies.sort(key=lambda item: item["dir"])
    return {"companies": companies, "count": len(companies)}


def display_name(ticker: str, company: str, label: str = "") -> str:
    """统一显示名（`AC-9`）：`600887 伊利股份`。

    同一家公司在公司页 / 图表页 / 工作台 / 数据页必须**同一个**字符串，所以只在这里拼。
    拼不出来（缺 ticker 或名字）时的顺序是：

    1. 目录名形如 `<代码>_<简称>` → `代码 简称`。**这条是实测补的**：`output/` 下有一批
       早期产物没有 `record.json`（`000858_五粮液`、`600036_招商银行`…），只看 `subject`
       会让工作台把目录名当显示名——`AC-9` 说的「目录名只作为技术标识出现」就不成立了；
    2. 都没有 → 退回目录名（总比空着强）。
    刻意**不**把 `600887.SH` 当公司名：带交易所后缀的代码是技术标识。
    """
    code = str(ticker or "").split(".")[0].strip()
    name = str(company or "").strip()
    label_code, label_name = _split_label(label)
    # 缺的部分从目录名补（`600887_伊利` → 代码 `600887`、简称 `伊利`）。
    # **两边都要补**：只补名字会让「有 ticker 没名字」与「有名字没 ticker」两家公司在
    # 不同页面上拼出两个结果——门② 的阻断项（`600887` vs `600887 伊利`）就是这条链上的。
    code = code or label_code
    name = name or label_name
    if code and name:
        return f"{code} {name}"
    if name:
        return name
    if code:
        return code
    return str(label or "").strip()


def _split_label(label: str) -> tuple:
    """目录名 → (`代码`, `简称`)；不合「数字代码_简称」约定就返回两个空串。"""
    text = str(label or "").strip()
    code, separator, rest = text.partition("_")
    if separator and rest and _CODE_RE.match(code):
        return code, rest
    return "", ""


#: 目录名约定里的「代码」：A 股/港股/美股的数字代码（`000858` / `600887` / `00700`）。
#: 刻意要求**全数字**：`portfolio_2026`、`handoff_x` 这类目录名带下划线但不是公司，
#: 放宽成「字母数字」就会把它们改写成 `portfolio 2026`（实测踩到）。
_CODE_RE = re.compile(r"^[0-9]{3,6}$")


def display_name_from_label(label: str) -> str:
    """目录名（`000858_五粮液` / `600887_伊利`）→ 显示名（`000858 五粮液`）。

    只处理「`<数字代码>_<简称>`」这一种约定；不合约定就原样返回（**不猜**，也不吞掉内容）。
    """
    text = str(label or "").strip()
    code, separator, rest = text.partition("_")
    if separator and rest and _CODE_RE.match(code):
        return f"{code} {rest}"
    return text


def parse_artifacts(sources: list, params: dict) -> dict:
    """产物索引：分类、相对路径、大小、修改时间，以及**只在这里生成**的产物 id（AC-2.2）。"""
    base = Path(params.get("base") or "")
    artifacts = []
    for path in sorted(sources):
        try:
            relative = str(path.relative_to(base))
        except ValueError:  # pragma: no cover - 数据层已保证源文件在 base 之下
            relative = path.name
        try:
            info = path.stat()
        except OSError:  # pragma: no cover - 竞态：索引期间文件被删
            continue
        artifacts.append(
            {
                "id": _artifact_id(relative),
                "rel": relative,
                "name": path.name,
                "group": _group_of(path.name),
                "size": info.st_size,
                "mtime": info.st_mtime,
                "markdown": path.suffix.lower() in _MARKDOWN_SUFFIXES,
                "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            }
        )
    groups: dict = {}
    for artifact in artifacts:
        groups.setdefault(artifact["group"], []).append(artifact)
    return {
        "count": len(artifacts),
        "artifacts": artifacts,
        "groups": [
            {"name": name, "artifacts": groups[name]} for name in sorted(groups)
        ],
    }


def register_parsers() -> None:
    """幂等注册：测试会重置解析器注册表，`replace=True` 让重复装配不炸。"""
    parsers.register_parser("companies.index", parse_companies, replace=True)
    parsers.register_parser("companies.artifacts", parse_artifacts, replace=True)


# --------------------------------------------------------------- 取数辅助


def company_base(ctx, company_id: str) -> Path:
    """公司标识 → 真实路径；越界一律 403（AC-2.2）、标识不存在一律 404。

    `REQ-012.1` 的 `AC-1`：**这里的参数是「公司标识」，ticker 是规范形式**，目录名保留为
    兼容别名（`REQ-009.2` 的端点参数语义变更记录）。解析在服务端做，前端只传 ticker；
    ticker 解析不到时是**正常空状态**（`NotFound` 的可读文案），不是 4xx 技术报错吗——
    它仍然是 404，但文案是「没有这家公司」，而不是「缺少参数 company」。

    解析顺序刻意是 ticker → 目录名：目录名形态（`600887_伊利`）永远走不到 ticker 分支
    （它不含 `.`），所以两条路径不会互相抢。
    """
    if not company_id:
        raise BadRequest(
            "还没选公司",
            hint="在页面右上角选一家公司，或从工作台的公司列表点进去。",
        )
    resolved = resolve_identifier(ctx, company_id)
    if resolved is not None:
        return resolved
    # 都解析不到：先把标识过一遍 jail，再报「没有这家公司」——越界（`..`、绝对路径、
    # 符号链接出树）必须仍然是 403，不因为参数语义从「目录名」变成「公司标识」而放宽
    # （`REQ-012.1` 的约束：安全边界不变）。
    safe_join(ctx.config.output_root, company_id)
    raise NotFound(
        f"没有这家公司：{company_id}",
        hint="公司清单见 /api/v1/companies；也可以从工作台的公司列表点进去。",
    )


def resolve_identifier(ctx, identifier: str) -> Path | None:
    """公司标识（ticker 或目录名）→ 真实目录；解析不到返回 None。

    刻意**不抛异常**：调用方（动作预检、工作台、数据页）需要「解析不到」当作正常分支
    （禁用按钮 + 说原因），而不是把它变成一次请求失败。
    """
    text = str(identifier or "").strip()
    if not text:
        return None
    try:
        entries = companies_dataset(ctx)[0]["companies"]
    except (ArtifactMissing, WebUIError, OSError):
        return None
    wanted = text.upper()
    for item in entries:
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker and ticker == wanted:
            return safe_join(ctx.config.output_root, item["dir"])
        if item["dir"] == text:
            return safe_join(ctx.config.output_root, item["dir"])
    # 索引里没有（例如这家公司只有目录、还没跑过任何产物）：目录名直接用，
    # 但仍要过 jail；ticker 形态则确实找不到——没有目录名能长得像 ticker。
    if "." in text or text.isalpha():
        return None
    try:
        return safe_join(ctx.config.output_root, text)
    except WebUIError:
        return None


def resolve_company(ctx, company_id: str) -> dict:
    """公司标识 → `{dir, ticker, display_name, base, …}`；解析不到给**可读空状态**。

    这是 GUI 侧唯一的「当前公司」解析入口：面板、动作预检、工作台都读它，
    所以「同一家公司在三处显示名一致」（`AC-9`）只需要在这里拼一次。
    """
    base = company_base(ctx, company_id)
    entry = {}
    try:
        entries = companies_dataset(ctx)[0]["companies"]
    except (ArtifactMissing, WebUIError, OSError):
        entries = []
    for item in entries:
        ticker = str(item.get("ticker") or "").strip().upper()
        if (ticker and ticker == str(company_id).strip().upper()) or item["dir"] == base.name:
            entry = item
            break
    name = str(entry.get("company") or "")
    ticker = str(entry.get("ticker") or "")
    if not name:
        record = _read_json(base / "record.json")
        subject = record.get("subject") or {}
        name = str(subject.get("company") or "")
        ticker = ticker or str(subject.get("ticker") or "")
    return {
        "dir": base.name,
        "label": base.name,
        "ticker": ticker,
        "company": name,
        "display_name": display_name(ticker, name, base.name),
        "base": base,
        "primary_period": str(entry.get("primary_period") or ""),
        "last_run": str(entry.get("last_run") or ""),
        "downstream_stale": bool(entry.get("downstream_stale")),
    }


def context_resolvable(ctx, value) -> bool:
    """`requires` 的解析器：这个公司标识在这份数据里真的能找到吗？

    挂在声明 `requires=("selection.company",)` 的页面上（`NavItem.context_resolver`）。
    核心只调用它、不解释它——所以「什么算能解析」仍然由插件决定（`AC-11`）。
    """
    return resolve_identifier(ctx, value) is not None


def companies_dataset(ctx) -> tuple:
    return ctx.registry.datastore.get(
        "companies.index",
        base=ctx.config.output_root,
        params={"root": str(ctx.config.output_root)},
    )


def artifacts_dataset(ctx, base: Path) -> tuple:
    return ctx.registry.datastore.get(
        "companies.artifacts", base=base, params={"base": str(base)}
    )


def _default_artifact(artifacts: list) -> dict:
    for wanted in ("qualitative_report.md",):
        for artifact in artifacts:
            if artifact["name"] == wanted:
                return artifact
    for artifact in artifacts:
        if "价值分析报告" in artifact["name"]:
            return artifact
    for artifact in artifacts:
        if artifact["markdown"]:
            return artifact
    return {}


# --------------------------------------------------------------- 面板与路由


def _list_panel(ctx, **_):
    try:
        data, meta = companies_dataset(ctx)
    except ArtifactMissing:
        return {"columns": _COMPANY_COLUMNS, "rows": [], "meta": {"empty": True}}
    rows = [
        {
            # 显示名全站唯一（AC-1.4 / AC-9）；链接带 **ticker**（URL 契约 AC-1）。
            "name": item["display_name"],
            "href": f"#charts?company={_link_id(item)}",
            "ticker": item["ticker"],
            "last_run": item["last_run"],
            "primary_period": item["primary_period"],
            "stale": "是" if item["downstream_stale"] else "",
        }
        for item in data["companies"]
    ]
    return {"columns": _COMPANY_COLUMNS, "rows": rows, "meta": meta}


def _link_id(item: dict) -> str:
    """站内链接里的公司标识：优先 ticker，没有 ticker 才退回目录名（兼容老产物）。"""
    return str(item.get("ticker") or item.get("dir") or "")


_COMPANY_COLUMNS = [
    {"key": "name", "title": "公司", "href_key": "href", "sort": "text", "search": True},
    {"key": "primary_period", "title": "数据期次", "sort": "text", "search": True},
    {"key": "last_run", "title": "最近分析", "sort": "text"},
    {"key": "stale", "title": "下游过期", "sort": "text"},
]


def _artifacts_panel(ctx, company=None, **_):
    base = company_base(ctx, company)
    data, meta = artifacts_dataset(ctx, base)
    rows = [
        {
            "name": artifact["name"],
            "href": f"#report?company={company}&id={artifact['id']}",
            "group": artifact["group"],
            "size": artifact["size"],
            "rel": artifact["rel"],
        }
        for artifact in data["artifacts"]
    ]
    return {
        "columns": [
            {"key": "name", "title": "产物", "href_key": "href", "search": True, "sort": "text"},
            {"key": "group", "title": "类型", "search": True, "sort": "text"},
            {"key": "rel", "title": "位置（技术标识）", "search": True},
            {"key": "size", "title": "字节", "align": "right", "sort": "number"},
        ],
        "rows": rows,
        "meta": meta,
    }


def _report_panel(ctx, company=None, id=None, run=None, **_):
    return _report_payload(ctx, company, id, run)


def _list_companies(ctx, **_):
    try:
        data, meta = companies_dataset(ctx)
    except ArtifactMissing:
        return envelope.ok(
            {"companies": [], "count": 0, "empty": True,
             "empty_hint": "output/ 下还没有任何公司产物：先在数据页拉取数据，或把既有产物接进来。"},
            meta={"empty": True},
        )
    return envelope.ok(data, meta=meta)


def _list_artifacts(ctx, ticker=None, **_):
    company = resolve_company(ctx, ticker or ctx.query.get("company") or ctx.query.get("ticker"))
    data, meta = artifacts_dataset(ctx, company["base"])
    return envelope.ok({**{key: value for key, value in company.items() if key != "base"},
                        **data}, meta=meta)


def _report_payload(ctx, company, artifact_id, run_id=None) -> dict:
    resolved = resolve_company(ctx, company)
    base = resolved["base"]
    data, meta = artifacts_dataset(ctx, base)
    chosen = None
    if artifact_id:
        chosen = next(
            (item for item in data["artifacts"] if item["id"] == artifact_id), None
        )
        if chosen is None:
            raise NotFound(
                f"产物 id {artifact_id!r} 不在索引里",
                hint="id 取自产物索引；不接受路径参数。",
            )
    elif run_id:
        # 从迭代时间线点过来：优先看这一次 run 自己的报告（AC-4 的按 run 切换）。
        wanted = f"runs/{run_id}/qualitative_report.md"
        chosen = next((item for item in data["artifacts"] if item["rel"] == wanted), None)
    if not chosen:
        chosen = _default_artifact(data["artifacts"])
        if not chosen:
            raise ArtifactMissing(f"{company} 还没有可阅读的报告")
    if not chosen["markdown"]:
        raise BadRequest(
            f"{chosen['name']} 不是 Markdown，面板不渲染",
            hint="PDF/JSON 请直接打开文件；面板只内联渲染 Markdown。",
        )
    path = safe_join(base, *Path(chosen["rel"]).parts)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "artifact": chosen,
        "title": chosen["name"],
        "html": markdown_safe.render(text),
        # 当前公司跟着报告一起下发：页面标题/面包屑据此显示「现在看的是哪家公司」（AC-1）。
        "company": {
            key: value for key, value in resolved.items()
            if key in ("dir", "ticker", "display_name", "primary_period", "last_run")
        },
        "meta": meta,
    }


def _report(ctx, ticker=None, **_):
    # `id` 是查询参数（不是路径段）：只接受索引里的 id，不接受路径。
    company = ticker or ctx.query.get("company") or ctx.query.get("ticker")
    return envelope.ok(_report_payload(ctx, company, ctx.query.get("id")))


def contribute(registry):
    register_parsers()
    # `parser_version` 从 1 升到 2（`REQ-012.1`）：解析结果多了 `label` / `display_name`
    # 两个字段，显示名的兜底规则也变了（没有 `record.json` 的老产物按 `<代码>_<简称>`
    # 约定拼）。数据层的缓存键含 parser_version，所以**不升版本的话老缓存会继续被命中**，
    # 界面上还是目录名——这正是实测踩到的那次（清了派生缓存才对，那就不是修复）。
    registry.dataset(DatasetSpec(
        name="companies.index", sources=COMPANY_MARKERS, parser="companies.index",
        parser_version=2, schema_version="1.0",
    ))
    registry.dataset(DatasetSpec(
        name="companies.artifacts", sources=("*", "runs/*/*"), parser="companies.artifacts",
        parser_version=2, schema_version="1.0",
    ))
    registry.panel(PanelSpec(
        id="companies.list", kind="table", title="公司", provider=_list_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        description="已经分析过的公司；点公司名进入它的图表页。",
    ))
    registry.panel(PanelSpec(
        id="companies.artifacts", kind="table", title="产物",
        provider=_artifacts_panel, size="full",
        options={"table": {"search": True, "sort": True, "page": 50}},
        params=(Param("company", type="company", source="selection.company"),),
        description="按类型分组列出本地产物；点产物名在面板内阅读。",
    ))
    registry.panel(PanelSpec(
        id="report.view", kind="markdown", title="报告阅读",
        provider=_report_panel, size="full",
        params=(
            Param("company", type="company", source="selection.company"),
            Param("id", type="string", source="selection.artifact"),
            Param("run", type="run", source="selection.run"),
        ),
        description="Markdown 先转义再渲染；报告里的原始 HTML 不生效。",
    ))
    # `REQ-012.1` 的 IA：公司级页面收进「公司」组，并声明**上下文依赖**
    # （`requires` 只是字符串，前端 shell 拿 selection 比对后决定「渲染」还是「先选公司」）。
    registry.nav(NavItem(id="report", title="报告", group="公司", order=30,
                         panels=("report.view", "companies.artifacts"),
                         requires=("selection.company",),
                         context_resolver=context_resolvable,
                         description="阅读这家公司的分析报告与产物；未选公司时给你选公司的入口。"))
    registry.nav(NavItem(id="companies", title="公司列表（全部）", group="数据", order=15,
                         panels=("companies.list",),
                         description="已经分析过的公司；点公司名进入它的图表页。"))
    registry.route("GET", "/api/v1/companies", _list_companies, name="company list")
    # 路径参数是**公司标识**：ticker（规范）或目录名（兼容别名），见 REQ-009 的参数语义变更记录。
    registry.route("GET", "/api/v1/companies/{ticker}/artifacts", _list_artifacts,
                   name="company artifacts")
    registry.route("GET", "/api/v1/companies/{ticker}/report", _report, name="company report")


__all__ = [
    "contribute", "parse_companies", "parse_artifacts", "company_base", "resolve_company",
    "resolve_identifier", "context_resolvable", "display_name", "artifacts_dataset",
    "companies_dataset", "COMPANY_MARKERS",
]
