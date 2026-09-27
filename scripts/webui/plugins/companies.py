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
from pathlib import Path

from ..core import envelope
from ..core.errors import ArtifactMissing, BadRequest, NotFound
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
    """把命中的产物文件归到各自的公司目录，并带上最近一次 run 与期次（AC-2.1）。"""
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
        companies.append(
            {
                "dir": directory.name,
                "name": directory.name,
                "ticker": subject.get("ticker", ""),
                "company": subject.get("company", ""),
                "last_run": latest.get("run_id") or record.get("latest_run") or "",
                "primary_period": latest.get("primary_period") or record.get("primary_period") or "",
                "downstream_stale": bool((record.get("downstream") or {}).get("stale")),
            }
        )
    companies.sort(key=lambda item: item["dir"])
    return {"companies": companies, "count": len(companies)}


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


def resolve_company(ctx, company_dir: str | None) -> str:
    """当前公司目录：显式给的就用；没给就退回**最近有 run 的那家**（都没有 run 则退回目录序第一家）。

    为什么要有默认值：`图表` / `报告` / `迭代记录` 三页的面板都声明
    `source=selection.company`，从左侧导航直接进来时还没有任何选择——此前整页会渲染成
    `BAD_REQUEST：缺少公司目录参数 company` 的错误卡片（用户实机体验发现）。
    默认取「最近有 run 的那家」而不是目录序第一家：控制台一打开就该看到你最近在跟进的公司，
    否则 `迭代记录` 很可能落在一条 run 都没有的公司上、显示空时间线。
    给一个**确定的**默认值、并在面板上写明当前公司，比「先报错、让用户去公司页点一下」
    更符合控制台的用法；`output/` 下没有任何公司时仍然是明确的错误（不假装有数据）。
    """
    if company_dir:
        return company_dir
    try:
        data, _ = companies_dataset(ctx)
    except ArtifactMissing:
        return ""
    companies = data["companies"]
    with_run = [item for item in companies if item.get("last_run")]
    if with_run:  # run_id 形如 20260925T042255414048Z，按字典序即按时间
        return max(with_run, key=lambda item: item["last_run"])["dir"]
    return companies[0]["dir"] if companies else ""


def company_caption(company: str) -> str:
    """面板上「当前在看哪家公司」的说明——默认选中不能是静默的。"""
    return f"当前公司：{company}（可在「公司」页切换）"


def company_base(ctx, company_dir: str | None) -> Path:
    """公司目录 → 真实路径；越界一律 403（AC-2.2）。未给目录时退回第一家（见 `resolve_company`）。"""
    company_dir = resolve_company(ctx, company_dir)
    if not company_dir:
        raise BadRequest(
            "output/ 下还没有可展示的公司目录",
            hint="先跑一次分析产出 output/<公司>/…，或确认 webui.config.json 的 output_root 指向正确。",
        )
    return safe_join(ctx.config.output_root, company_dir)


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
            "name": item["name"],
            "href": f"#charts?company={item['dir']}",
            "last_run": item["last_run"],
            "primary_period": item["primary_period"],
            "stale": "是" if item["downstream_stale"] else "",
        }
        for item in data["companies"]
    ]
    return {"columns": _COMPANY_COLUMNS, "rows": rows, "meta": meta}


_COMPANY_COLUMNS = [
    {"key": "name", "title": "公司", "href_key": "href"},
    {"key": "last_run", "title": "最近 run"},
    {"key": "primary_period", "title": "财报期"},
    {"key": "stale", "title": "下游过期"},
]


def _artifacts_panel(ctx, company=None, **_):
    company = resolve_company(ctx, company)
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
            {"key": "name", "title": "产物", "href_key": "href"},
            {"key": "group", "title": "类型"},
            {"key": "rel", "title": "相对路径"},
            {"key": "size", "title": "字节", "align": "right"},
        ],
        "rows": rows,
        "meta": meta,
        "caption": company_caption(company),
    }


def _report_panel(ctx, company=None, id=None, run=None, **_):
    return _report_payload(ctx, company, id, run)


def _list_companies(ctx, **_):
    try:
        data, meta = companies_dataset(ctx)
    except ArtifactMissing:
        return envelope.ok({"companies": [], "count": 0}, meta={"empty": True})
    return envelope.ok(data, meta=meta)


def _list_artifacts(ctx, dir, **_):
    base = company_base(ctx, dir)
    data, meta = artifacts_dataset(ctx, base)
    return envelope.ok({"dir": dir, **data}, meta=meta)


def _report_payload(ctx, company, artifact_id, run_id=None) -> dict:
    company = resolve_company(ctx, company)
    base = company_base(ctx, company)
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
        "meta": meta,
        "caption": company_caption(company),
    }


def _report(ctx, dir, **_):
    # `id` 是查询参数（不是路径段）：只接受索引里的 id，不接受路径。
    return envelope.ok(_report_payload(ctx, dir, ctx.query.get("id")))


def contribute(registry):
    register_parsers()
    registry.dataset(DatasetSpec(
        name="companies.index", sources=COMPANY_MARKERS, parser="companies.index",
        parser_version=1, schema_version="1.0",
    ))
    registry.dataset(DatasetSpec(
        name="companies.artifacts", sources=("*", "runs/*/*"), parser="companies.artifacts",
        parser_version=1, schema_version="1.0",
    ))
    registry.panel(PanelSpec(
        id="companies.list", kind="table", title="公司", provider=_list_panel, size="full",
        description="output/ 下含产物的公司目录；点公司名进入图表页。",
    ))
    registry.panel(PanelSpec(
        id="companies.artifacts", kind="table", title="产物",
        provider=_artifacts_panel, size="full",
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
    registry.nav(NavItem(id="companies", title="公司", group="浏览", order=10,
                         panels=("companies.list",)))
    registry.nav(NavItem(id="report", title="报告", group="浏览", order=30,
                         panels=("report.view", "companies.artifacts")))
    registry.route("GET", "/api/v1/companies", _list_companies, name="company list")
    registry.route("GET", "/api/v1/companies/{dir}/artifacts", _list_artifacts,
                   name="company artifacts")
    registry.route("GET", "/api/v1/companies/{dir}/report", _report, name="company report")


__all__ = [
    "contribute", "parse_companies", "parse_artifacts", "company_base", "resolve_company",
    "company_caption", "artifacts_dataset", "companies_dataset", "COMPANY_MARKERS",
]
