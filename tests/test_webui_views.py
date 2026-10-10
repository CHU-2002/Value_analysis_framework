# 覆盖需求：REQ-015.3（AC-3.1～3.4：正式指针、目录、安全材料、同公司版本比较）
# 覆盖需求：REQ-009.2（报告浏览、图表与迭代台账视图）—— AC-2.1 公司列表、
# AC-2.2 产物索引与路径 jail、AC-2.3 报告安全渲染、AC-2.4 图表序列与缓存、
# AC-2.5 迭代台账时间线
"""视图测试：全部在 `tmp_path` 造公司目录，不依赖真实 `output/`，不联网。"""

import json
from pathlib import Path

import pytest

from webui.config import Config
from webui.core.context import RequestContext
from webui.core.errors import ArtifactMissing, BadRequest, NotFound, PathOutsideRoot, WebUIError
from webui.core.models import DatasetSpec, Param
from webui.core.registry import build_registry
from webui.core.router import find_route
from webui.core.routes import install_core_routes
from webui.datastore import DataStore, parsers
from webui.plugins import charts as charts_plugin
from webui.plugins import companies as companies_plugin
from webui.plugins import run_history as run_history_plugin

DATA_PACK = """## 3. 合并利润表

| 项目 (百万元) | 2026 | 2025 |
| --- | ---: | ---: |
| 营业收入 | 120 | 100 |
| 归母净利润 | 12 | 10 |

## 11. 十年周线行情

| 指标 | 数值 |
| --- | ---: |
| 10年最高 | 32.00 (20260101) |

### 年度行情汇总

| 年度 | 最高 | 最低 | 年末收盘 | 周均成交量(手) |
| --- | ---: | ---: | ---: | ---: |
| 2026 | 32.00 | 22.00 | 28.00 | 110 |
| 2025 | 30.00 | 20.00 | 25.00 | 100 |

## 12. 关键财务指标

| 指标 | 2026 | 2025 |
| --- | ---: | ---: |
| ROE (%) | 21.00 | 20.00 |
| 毛利率 (%) | 31.00 | 30.00 |
| 净利率 (%) | 10.00 | 9.00 |
| 资产负债率 (%) | 61.00 | 60.00 |
"""

REPORT_MD = """# 标题

<script>alert(1)</script>

[点我](javascript:alert(2))

| 指标 | 值 |
| --- | ---: |
| ROE | 20 |
"""


# --------------------------------------------------------------- 测试辅助


def make_config(tmp_path: Path) -> Config:
    return Config(
        host="127.0.0.1", port=0,
        output_root=tmp_path / "output", cache_dir=tmp_path / "output",
        archive_root=tmp_path / "archive",
    )


def make_app(tmp_path: Path):
    config = make_config(tmp_path)
    registry = build_registry()
    install_core_routes(registry, config)
    registry.config = config
    for module in (companies_plugin, charts_plugin, run_history_plugin):
        module.contribute(registry)
    registry.datastore = DataStore(config, spec_lookup=registry.dataset_spec)
    return config, registry


def company_dir(tmp_path: Path, name: str = "111111_甲") -> Path:
    directory = tmp_path / "output" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")
    return directory


def call_route(registry, method: str, path: str, **query):
    route, params = find_route(registry.routes(), method, path)
    assert route is not None, f"没有匹配的路由：{method} {path}"
    context = RequestContext(
        method=method, path=path, query=dict(query), config=registry.config, registry=registry
    )
    return route.handler(context, **params)


@pytest.fixture(autouse=True)
def _isolated_parsers():
    parsers.reset_parsers()
    parsers.register_builtin_parsers()
    yield
    parsers.reset_parsers()
    parsers.register_builtin_parsers()


# --------------------------------------------------------------- AC-2.1 公司列表


def test_companies_endpoint_lists_dirs_and_tolerates_missing_output(tmp_path):
    """AC-2.1：列出 output/ 下的公司目录与最近 run / 期次；output/ 不存在时空列表而不是 500。"""
    _, registry = make_app(tmp_path)
    first = company_dir(tmp_path, "111111_甲")
    (first / "latest.json").write_text(json.dumps(
        {"run_id": "20260101T000000000000Z", "primary_period": "2026H1"}
    ), encoding="utf-8")
    (first / "record.json").write_text(json.dumps({
        "latest_run": "20260101T000000000000Z", "primary_period": "2026H1",
        "subject": {"ticker": "111111.SH", "company": "甲公司"}, "downstream": {"stale": True},
    }, ensure_ascii=False), encoding="utf-8")
    company_dir(tmp_path, "222222_乙")
    hidden = tmp_path / "output" / ".webui_cache"
    hidden.mkdir()
    (hidden / "data_pack_market.md").write_text(DATA_PACK, encoding="utf-8")

    data = call_route(registry, "GET", "/api/v1/companies")["data"]
    assert [item["dir"] for item in data["companies"]] == ["111111_甲", "222222_乙"]
    assert data["companies"][0]["ticker"] == "111111.SH"
    assert data["companies"][0]["last_run"] == "20260101T000000000000Z"
    assert data["companies"][0]["primary_period"] == "2026H1"
    assert data["companies"][0]["downstream_stale"] is True
    assert data["companies"][1]["last_run"] == ""

    empty_config, empty_registry = make_app(tmp_path / "elsewhere")
    payload = call_route(empty_registry, "GET", "/api/v1/companies")
    assert payload["ok"] is True and payload["data"]["companies"] == []
    assert empty_config.output_root.exists() is False


# --------------------------------------------------------------- AC-2.2 产物索引与 jail


def test_artifacts_endpoint_groups_by_type_and_rejects_traversal(tmp_path):
    """AC-2.2：按类型分组返回产物（相对路径 / 大小 / 修改时间）；穿越与越界符号链接 4xx。"""
    _, registry = make_app(tmp_path)
    base = company_dir(tmp_path)
    (base / "qualitative_report.md").write_text(REPORT_MD, encoding="utf-8")
    (base / "111111_2025_年报.pdf").write_bytes(b"%PDF-1.4 minimal")
    (base / "runs" / "20260101T000000000000Z").mkdir(parents=True)
    (base / "runs" / "20260101T000000000000Z" / "qualitative_report.md").write_text(
        "# 历史", encoding="utf-8"
    )

    data = call_route(registry, "GET", "/api/v1/companies/111111_甲/artifacts")["data"]
    assert data["count"] == 4
    assert {"数据包", "定性报告", "年报 PDF"} <= {group["name"] for group in data["groups"]}
    ids = [item["id"] for item in data["artifacts"]]
    assert len(set(ids)) == len(ids) and all(len(item) == 12 for item in ids)
    top = next(item for item in data["artifacts"] if item["name"] == "qualitative_report.md")
    assert top["rel"] == "qualitative_report.md" and top["size"] > 0 and top["mtime"] > 0
    assert top["markdown"] is True
    assert any(item["rel"].startswith("runs/") for item in data["artifacts"])

    for bad in ("..", "%2E%2E%2F%2E%2E%2F.env", "%2Fetc%2Fpasswd"):
        with pytest.raises(PathOutsideRoot):
            call_route(registry, "GET", f"/api/v1/companies/{bad}/artifacts")

    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "output" / "linked"
    try:
        link.symlink_to(outside.parent, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - 平台不支持符号链接
        pytest.skip("本平台不支持创建符号链接")
    with pytest.raises(PathOutsideRoot):
        call_route(registry, "GET", "/api/v1/companies/linked/artifacts")


# --------------------------------------------------------------- AC-2.3 报告渲染


def test_report_renders_markdown_and_escapes_injection(tmp_path):
    """AC-2.3：报告渲染成 HTML，原始 HTML 被转义，`javascript:` 链接被拒绝。"""
    _, registry = make_app(tmp_path)
    base = company_dir(tmp_path)
    (base / "qualitative_report.md").write_text(REPORT_MD, encoding="utf-8")
    (base / "111111_2025_年报.pdf").write_bytes(b"%PDF-1.4 minimal")

    index = call_route(registry, "GET", "/api/v1/companies/111111_甲/artifacts")["data"]
    report_id = next(
        item["id"] for item in index["artifacts"] if item["name"] == "qualitative_report.md"
    )
    data = call_route(registry, "GET", "/api/v1/companies/111111_甲/report", id=report_id)["data"]
    assert '<h1 id="report-heading-1">标题</h1>' in data["html"]
    assert "<table>" in data["html"] and "<strong>" not in data["html"]
    # B1 回归（独立验收阻断项）：表格**表体**必须逐单元格渲染，而不是把整行当字符串逐字符拆。
    assert "<td>ROE</td><td>20</td>" in data["html"]
    assert "<td>R</td>" not in data["html"] and "<td>O</td>" not in data["html"]
    assert "<script>" not in data["html"] and "&lt;script&gt;alert(1)&lt;/script&gt;" in data["html"]
    assert "javascript:" not in data["html"]           # 危险协议不生成链接
    assert "点我" in data["html"]

    default = call_route(registry, "GET", "/api/v1/companies/111111_甲/report")["data"]
    assert default["artifact"] is None and "正式发布指针" in default["notice"]
    assert any(item["id"] == report_id for item in default["versions"])

    with pytest.raises(NotFound):
        call_route(registry, "GET", "/api/v1/companies/111111_甲/report", id="deadbeef0000")
    pdf_id = next(
        item["id"] for item in index["artifacts"] if item["name"].endswith(".pdf")
    )
    material = call_route(registry, "GET", "/api/v1/companies/111111_甲/report", id=pdf_id)["data"]
    assert material["artifact"]["markdown"] is False and material["html"] == ""


# --------------------------------------------------------------- AC-2.4 图表与缓存


def test_charts_endpoint_returns_three_series_and_caches_parsing(tmp_path):
    """AC-2.4：返回 3 组序列；重复请求命中缓存（解析器调用计数）；格式变化给带错误码的失败。"""
    _, registry = make_app(tmp_path)
    base = company_dir(tmp_path)
    calls: list = []
    for name in charts_plugin.CHART_DATASETS:
        original = parsers.get_parser(name)

        def counting(sources, params, _name=name, _original=original):
            calls.append(_name)
            return _original(sources, params)

        parsers.register_parser(name, counting, replace=True)

    data = call_route(registry, "GET", "/api/v1/companies/111111_甲/charts")["data"]["charts"]
    assert set(data) == set(charts_plugin.CHART_DATASETS)
    annual = data["charts.annual_price"]
    assert annual["labels"] == ["2025", "2026"]
    assert [series["name"] for series in annual["series"]] == ["年度最高", "年度最低", "年末收盘"]
    assert annual["series"][1]["values"] == [20.0, 22.0]
    metrics = data["charts.metrics"]
    assert metrics["labels"] == ["2025", "2026"]
    assert [series["name"] for series in metrics["series"]] == [
        "ROE (%)", "毛利率 (%)", "净利率 (%)", "资产负债率 (%)"
    ]
    revenue = data["charts.revenue_profit"]
    assert revenue["series"][0]["values"] == [100.0, 120.0]
    assert revenue["series"][1]["values"] == [10.0, 12.0]
    assert sorted(calls) == sorted(charts_plugin.CHART_DATASETS)

    again = call_route(registry, "GET", "/api/v1/companies/111111_甲/charts")["data"]["charts"]
    assert again == data
    assert sorted(calls) == sorted(charts_plugin.CHART_DATASETS), "第二次应命中缓存，不再解析"

    (base / "data_pack_market.md").write_text(
        DATA_PACK.replace("### 年度行情汇总", "### 行情年度汇总"), encoding="utf-8"
    )
    with pytest.raises(WebUIError) as excinfo:
        call_route(registry, "GET", "/api/v1/companies/111111_甲/charts")
    assert excinfo.value.code in ("ARTIFACT_MISSING", "PARSE_FAILED", "PARSE_COLUMNS_MISMATCH")


# --------------------------------------------------------------- AC-2.5 迭代台账


def test_runs_timeline_is_reverse_ordered_and_marks_the_current_run(tmp_path):
    """AC-2.5：时间倒序、标出当前 run、给出来源与结论变化；缺失时给空时间线。"""
    _, registry = make_app(tmp_path)
    base = company_dir(tmp_path)
    entries = [
        {"run_id": "20260101T000000000000Z", "kind": "baseline",
         "created_at": "2026-01-01T00:00:00+00:00", "primary_period": "2025FY",
         "report_periods": ["2025FY"], "framework": {"git_commit": "a" * 40},
         "conclusions_changed": [], "subject": {"ticker": "111111.SH"}},
        {"run_id": "20260202T000000000000Z", "kind": "report-update",
         "created_at": "2026-02-02T00:00:00+00:00", "supersedes": "20260101T000000000000Z",
         "primary_period": "2026H1", "report_periods": ["2025H1", "2026H1"],
         "framework": {"git_commit": "b" * 40},
         "conclusions_changed": [{"conclusion": "护城河", "change": "由强转中"}],
         "subject": {"ticker": "111111.SH"}},
    ]
    (base / "history.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    (base / "latest.json").write_text(json.dumps(
        {"run_id": entries[1]["run_id"], "primary_period": "2026H1"}
    ), encoding="utf-8")

    payload = call_route(registry, "GET", "/api/v1/companies/111111_甲/runs")
    data = payload["data"]
    assert [run["run_id"] for run in data["runs"]] == [entries[1]["run_id"], entries[0]["run_id"]]
    assert [run["is_current"] for run in data["runs"]] == [True, False]
    assert data["latest_run"] == entries[1]["run_id"]
    assert data["runs"][0]["supersedes"] == entries[0]["run_id"]
    assert data["runs"][0]["changes"] == ["护城河：由强转中"]
    assert data["runs"][1]["kind"] == "baseline"

    page = call_route(registry, "GET", "/api/v1/pages/runs", company="111111_甲")["data"]["panels"]
    html = next(panel["html"] for panel in page if panel["id"] == "runs.timeline")
    assert "当前生效" in html and "护城河" in html and "报告阅读" not in html

    # 从时间线点进某一次 run：报告页要优先给这一次 run 自己的报告（按 run 切换）。
    run_report = base / "runs" / entries[0]["run_id"] / "qualitative_report.md"
    run_report.parent.mkdir(parents=True, exist_ok=True)
    run_report.write_text("# 历史 run 的报告", encoding="utf-8")
    report_page = call_route(
        registry, "GET", "/api/v1/pages/report", company="111111_甲", run=entries[0]["run_id"]
    )["data"]["panels"]
    report_html = next(panel["data"]["html"] for panel in report_page if panel["id"] == "report.view")
    assert "历史 run 的报告" in report_html

    company_dir(tmp_path, "222222_乙")
    empty = call_route(registry, "GET", "/api/v1/companies/222222_乙/runs")
    assert empty["ok"] is True and empty["data"]["runs"] == [] and empty["data"]["count"] == 0

    (base / "history.jsonl").write_text(
        json.dumps(entries[0], ensure_ascii=False) + "\n{ 坏行\n", encoding="utf-8"
    )
    broken = call_route(registry, "GET", "/api/v1/companies/111111_甲/runs")
    assert any("损坏" in warning for warning in broken["warnings"])
    assert len(broken["data"]["runs"]) == 1


# --------------------------------------------------------------- REQ-015.3

def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _reader_versions(tmp_path):
    _, registry = make_app(tmp_path)
    base = company_dir(tmp_path)
    for run, period, body in (("old", "2025FY", "# 重复\n旧正文\n# 重复\n"),
                              ("current", "2026H1", "# 重复\n新正文\n# 重复\n")):
        directory = base / "runs" / run
        directory.mkdir(parents=True)
        (directory / "qualitative_report.md").write_text(body, encoding="utf-8")
        (directory / "change_report.md").write_text("# 变化\n业务结论来自既有报告", encoding="utf-8")
        _write_json(directory / "run.json", {"run_id": run, "primary_period": period,
                                             "created_at": "2026-01-01" if run == "old" else "2026-06-01",
                                             "status": "complete", "as_of": "2026-05-31"})
    _write_json(base / "latest.json", {"run_id": "current", "primary_period": "2026H1"})
    return registry, base


def test_reader_default_follows_ledger_and_metadata_not_filename_or_mtime(tmp_path):
    """AC-3.1：正式run指针权威，历史元信息可辨识，顶层新文件不能冒充正式版。"""
    registry, base = _reader_versions(tmp_path)
    (base / "qualitative_report.md").write_text("# 新mtime但非正式", encoding="utf-8")
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report")["data"]
    assert result["artifact"]["rel"] == "runs/current/qualitative_report.md"
    assert result["artifact"]["period"] == "2026H1"
    assert result["artifact"]["source"] == "run current"
    old = next(item for item in result["versions"] if item["rel"] == "runs/old/qualitative_report.md")
    assert old["period"] == "2025FY" and old["generated_at"] == "2026-01-01"
    assert old["label"] != result["artifact"]["label"]
    legacy = next(item for item in result["versions"] if item["rel"] == "qualitative_report.md")
    assert legacy["generated_at"] == legacy["period"] == legacy["source"] == "未知"
    _write_json(base / "latest.json", {"run_id": "missing"})
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report")["data"]
    assert result["artifact"] is None and "未猜测最新版" in result["notice"]
    assert len([item for item in result["versions"] if item["type"] == "business"]) == 3
    _write_json(base / "latest.json", {"artifacts": ["broken pointer"]})
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report")["data"]
    assert result["artifact"] is None and result["versions"]


def test_reader_value_pointer_digest_classification_and_staleness(tmp_path):
    """AC-3.1 / REQ-009 M1：价值版本正确分类，摘要不匹配不可当正式报告。"""
    import hashlib
    registry, base = _reader_versions(tmp_path)
    directory = base / "value_reports" / "old" / "sha12"
    directory.mkdir(parents=True)
    report = directory / "report.md"
    report.write_text("# 已发布价值报告", encoding="utf-8")
    _write_json(directory / "manifest.json", {"source_run": "old", "primary_period": "2025FY", "published_at": "2026-02-01"})
    pointer = {"report": str(report), "source_run": "old", "primary_period": "2025FY", "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}
    _write_json(base / "value_report.json", pointer)
    (base / "甲价值分析报告.md").write_text("# 老报告", encoding="utf-8")
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report", type="value")["data"]
    assert result["artifact"]["rel"].startswith("value_reports/")
    assert result["artifact"]["status"] == "过期"
    assert all(item["group"] == "价值分析" for item in result["versions"] if item["type"] == "value")
    report.write_text("# 被更改", encoding="utf-8")
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report", type="value")["data"]
    assert result["artifact"] is None and "正式发布指针" in result["notice"]


def test_reader_real_toc_handles_duplicates_and_json_is_folded(tmp_path):
    """AC-3.2：唯一锚点与目录同趟解析；代码围栏伪标题排除，JSON折叠且安全。"""
    from webui.render.markdown_safe import render_document
    html, toc = render_document('# A\n## A\n```json\n{"x":"<script>"}\n# 不是标题\n```\n# A')
    assert [item["id"] for item in toc] == ["report-heading-1", "report-heading-2", "report-heading-3"]
    assert [item["title"] for item in toc] == ["A", "A", "A"]
    assert all(f'id="{item["id"]}"' in html for item in toc)
    assert '<details class="report-json">' in html and '<details class="report-json" open' not in html
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_reader_comparison_same_type_and_company_and_no_investment_conclusion(tmp_path):
    """AC-3.3：明确正文差异、两版期次/数据时点及已有变化报告；跨公司/类型拒绝。"""
    registry, _ = _reader_versions(tmp_path)
    result = call_route(registry, "GET", "/api/v1/companies/111111_甲/report")["data"]
    current = result["artifact"]
    old = next(item for item in result["versions"] if item["rel"] == "runs/old/qualitative_report.md")
    diff = call_route(registry, "GET", "/api/v1/companies/111111_甲/report/compare", left=old["id"], right=current["id"])["data"]
    assert "-旧正文" in diff["diff"] and "+新正文" in diff["diff"]
    assert diff["left"]["period"] == "2025FY" and diff["right"]["data_as_of"] == "2026-05-31"
    assert "不代表业务结论" in diff["notice"] and diff["changes"]
    wrong_type = next(item for item in result["versions"] if item["type"] == "change")
    with pytest.raises(BadRequest):
        call_route(registry, "GET", "/api/v1/companies/111111_甲/report/compare", left=old["id"], right=wrong_type["id"])
    company_dir(tmp_path, "222222_乙")
    with pytest.raises(NotFound):
        call_route(registry, "GET", "/api/v1/companies/222222_乙/report/compare", left=old["id"], right=current["id"])


def test_reader_material_and_download_bind_selected_version_and_keep_jail(tmp_path):
    """AC-3.4：非Markdown正常载荷；版本id可刷新且下载匹配；不可读任意文件。"""
    import base64
    registry, base = _reader_versions(tmp_path)
    (base / "source.pdf").write_bytes(b"%PDF-1.7 fixture")
    (base / "source.json").write_text('{"value":1}', encoding="utf-8")
    index = call_route(registry, "GET", "/api/v1/companies/111111_甲/artifacts")["data"]
    for name, expected in (("source.pdf", b"%PDF-1.7 fixture"), ("source.json", b'{"value":1}')):
        identifier = next(item["id"] for item in index["artifacts"] if item["name"] == name)
        data = call_route(registry, "GET", "/api/v1/companies/111111_甲/report", id=identifier)["data"]
        assert data["artifact"]["id"] == identifier and data["html"] == "" and data["type"] == "material"
        content = call_route(registry, "GET", "/api/v1/companies/111111_甲/report/content", id=identifier)["data"]
        assert base64.b64decode(content["base64"]) == expected and content["openable"] is True
    with pytest.raises(NotFound):
        call_route(registry, "GET", "/api/v1/companies/111111_甲/report/content", id="../../.env")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"private")
    (base / "escaped.pdf").symlink_to(secret)
    # 数据集入口或下载入口必须拒绝/排除越界符号链接。
    with pytest.raises(PathOutsideRoot):
        call_route(registry, "GET", "/api/v1/companies/111111_甲/report/content", id=companies_plugin._artifact_id("escaped.pdf"))


def test_reader_no_reports_is_normal_state_and_unknown_type_rejected(tmp_path):
    _, registry = make_app(tmp_path)
    company_dir(tmp_path)
    result = call_route(registry, "GET", "/api/v1/pages/report", company="111111_甲")
    assert result["ok"] and not result["warnings"]
    view = next(item for item in result["data"]["panels"] if item["id"] == "report.view")
    assert view["kind"] == "report" and view["data"]["artifact"] is None
    with pytest.raises(BadRequest):
        call_route(registry, "GET", "/api/v1/companies/111111_甲/report", type="invalid")


def test_reader_watchlist_company_without_directory_remains_normal(tmp_path):
    """AC-3.1/3.4：已知但未采集的公司不降级为找不到；空版本与采集入口准确。"""
    from datalayer.store import DataStore as ArchiveStore
    from datalayer.universe import Universe
    config, registry = make_app(tmp_path)
    Universe(ArchiveStore(config.archive_root)).add("600887.SH", display_name="伊利股份")
    result = call_route(registry, "GET", "/api/v1/pages/report", company="600887.SH")
    assert not result["warnings"] and result["data"].get("empty_state") is None
    view = next(item for item in result["data"]["panels"] if item["id"] == "report.view")
    assert view["data"]["artifact"] is None and view["data"]["versions"] == []
    assert view["data"]["company"]["ticker"] == "600887.SH"
    assert not (config.output_root / "600887_伊利股份").exists()
