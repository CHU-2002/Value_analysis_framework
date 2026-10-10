# 覆盖需求：REQ-015.1 —— 新加入无产物公司显示明确无OHLC状态，离线浏览不取凭据/网络
# 覆盖需求：REQ-014 —— AC-1 离线添加与别名、AC-2 幂等导入与逐项原因、AC-3 保留产物、
# AC-4 文案、AC-5 空清单入口、AC-6 实跑共用的入口（真实产物实跑另留报告）。
"""真实临时仓 + 动作 HTTP handler + 真实子进程，锁住清单与页面的可观察行为。"""
import json
import re
import time
from pathlib import Path

import pytest

from datalayer.store import DataStore
from datalayer.universe import Universe
from watchlist_action import execute, resolve_ticker
from webui.core.errors import BadRequest, WebUIError
from webui.core.jobs import JobRunner
from tests.test_console_actions import FORBIDDEN
from tests.test_console_data_page import make_app, call_route, panel_data


def run(registry, operation, params=None, context=None):
    job = call_route(registry, "POST", f"/api/v1/actions/universe.{operation}/run",
                     body={"params": params or {}, "context": context or {}})["data"]
    deadline = time.monotonic() + 10
    while job["status"] in ("running", "queued") and time.monotonic() < deadline:
        time.sleep(.02)
        job = registry.jobs.get(job["id"])
    assert job["status"] == "finished", job
    assert job["exit_code"] == 0
    report = next(json.loads(line[line.index('{"watchlist_result":'):])["watchlist_result"]
                  for line in job["log"] if '{"watchlist_result":' in line)
    return job, report


def company(root, label="600887_伊利", ticker="600887.SH", record=True):
    folder = root / label
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data_pack_market.md").write_text("真实保留对象", encoding="utf-8")
    if record:
        (folder / "record.json").write_text(json.dumps({"subject": {"ticker": ticker, "company": "伊利股份"}}), encoding="utf-8")
    return folder


@pytest.mark.parametrize("identifier", ["600887.SH", "600887", "600887_伊利"])
def test_add_aliases_refresh_all_company_views_offline(tmp_path, identifier, monkeypatch):
    config, registry = make_app(tmp_path)
    # 这些函数是凭据与远程数据的唯一入口；添加链不能碰它们。
    import datalayer.security as security
    import datalayer.access as access
    def forbidden(*args, **kwargs):
        raise AssertionError("离线动作访问了凭据或网络")
    monkeypatch.setattr(security, "resolve_token", forbidden)
    monkeypatch.setattr(access.DataAccess, "_invoke", forbidden)
    guard = tmp_path / "child_guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import sys\ndef guard(event, args):\n"
        "    if event == 'socket.connect' or (event == 'import' and args[0] in "
        "{'scripts.datalayer.security', 'datalayer.security', 'tushare_client'}):\n"
        "        raise AssertionError('offline child attempted credentials or network')\n"
        "sys.addaudithook(guard)\n")
    registry.jobs = JobRunner(config, spec_lookup=registry.command_spec,
                              job_lookup=registry.job_type_spec, env={"PYTHONPATH": str(guard)}, history_dir=tmp_path / "jobs")
    _, report = run(registry, "add", {"ticker": identifier, "name": "伊利股份"})
    assert report["created"] is True
    assert Universe(DataStore(config.archive_root)).tickers() == ["600887.SH"]
    assert call_route(registry, "GET", "/api/v1/panels/data.universe")["data"]["rows"][0]["ticker"] == "600887.SH"
    assert "600887" in panel_data(registry, "home.universe")["rows"][0]["company"]
    assert panel_data(registry, "companies.list")["rows"][0]["ticker"] == "600887.SH"
    assert call_route(registry, "GET", "/api/v1/companies")["data"]["count"] == 1
    page = call_route(registry, "GET", "/api/v1/pages/charts", company="600887.SH")["data"]
    # REQ-015: an enabled company now opens its research chart with truthful
    # no-data feedback rather than a page saying the selected company is unknown.
    assert not page.get("empty_state")
    timeline = panel_data(registry, "charts.timeline", company="600887.SH")
    assert timeline["points"] == [] and timeline["coverage"]["incomplete"]
    assert "尚未采集" in timeline["empty_hint"] and "company=600887.SH" in timeline["update_href"]
    _, repeated = run(registry, "add", {"ticker": "600887", "name": "替代简称"})
    assert not repeated["created"]
    assert Universe(DataStore(config.archive_root)).entries()[0]["display_name"] == "伊利股份"


def test_import_reports_every_skip_and_is_idempotent(tmp_path):
    config, registry = make_app(tmp_path)
    company(config.output_root)
    company(config.output_root, "000858_五粮液", "000858.SZ")
    company(config.output_root, "600036_招商银行", record=False)
    company(config.output_root, "portfolio_2026", "600887.SH")
    company(config.output_root, "002594_比亚迪", "600887.SH")
    bad = company(config.output_root, "600519_贵州茅台", "600519.SH")
    (bad / "record.json").write_text("[]")
    first_job, first = run(registry, "import")
    assert first["imported"] == 2 and first["skipped"] == 4 and first["total"] == 2
    assert len(first["items"]) == first["skipped"]
    assert all(item["company"] and item["reason"] for item in first["items"])
    assert any("缺少公司记录" in item["reason"] for item in first["items"])
    _, second = run(registry, "import")
    assert second["imported"] == 0 and second["skipped"] == 6 and second["total"] == 2
    assert sum("已在" in item["reason"] for item in second["items"]) == 2
    assert len(panel_data(registry, "data.universe")["rows"]) == first["total"]
    assert "--archive-root" in first_job["argv"]
    assert str(config.archive_root.resolve()) in first_job["argv"]


def test_remove_changes_only_universe_and_preserves_every_file(tmp_path):
    config, registry = make_app(tmp_path)
    folder = company(config.output_root)
    run(registry, "import")
    before = {str(p): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    _, report = run(registry, "remove", {"ticker": "600887.SH"})
    assert report["removed"] is True
    assert not panel_data(registry, "data.universe")["rows"]
    assert all(Path(path).is_file() and Path(path).read_bytes() == content for path, content in before.items())
    assert len(before) == 2
    assert call_route(registry, "GET", "/api/v1/companies")["data"]["count"] == 1


@pytest.mark.parametrize("params", [{"ticker": "../600887", "name": "伊利"},
                                       {"ticker": "600887", "name": ""},
                                       {"ticker": "600887", "name": "伊利", "archive_root": "/tmp/elsewhere"},
                                       {"ticker": ["600887"], "name": "伊利"},
                                       {"ticker": "A--.SH", "name": "公司"},
                                       {"ticker": "600887", "name": "make data"},
                                       {"ticker": "600887", "name": "python"},
                                       {"ticker": "python foo", "name": "公司"}])
def test_bad_inputs_rejected_before_starting_a_job(tmp_path, params):
    _, registry = make_app(tmp_path)
    with pytest.raises(BadRequest):
        call_route(registry, "POST", "/api/v1/actions/universe.add/run", body={"params": params})
    assert not registry.jobs.list_jobs()


def test_paths_are_bound_by_server_not_request_context(tmp_path):
    config, registry = make_app(tmp_path)
    job, _ = run(registry, "add", {"ticker": "600887", "name": "伊利"},
                 {"archive_root": str(tmp_path / "other"), "output_root": str(tmp_path / "other")})
    assert str(config.archive_root.resolve()) in job["argv"]
    assert str(tmp_path / "other") not in job["argv"]
    assert not (tmp_path / "other").exists()


def test_actions_and_empty_guidance_are_user_language(tmp_path):
    _, registry = make_app(tmp_path)
    actions = {a["id"]: a for a in call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]}
    copy = []
    for operation in ("add", "import", "remove"):
        action = actions[f"universe.{operation}"]
        assert action["effects"]["network"] is False and action["effects"]["quota"] is False
        copy.extend([action["title"], action["description"], *action["blockers"],
                     *action["confirm"].values(), *action["effects"]["writes"]])
    assert actions["universe.add"]["enabled"]
    assert not actions["universe.remove"]["enabled"]
    for panel_id in ("data.universe", "home.universe"):
        guide = panel_data(registry, panel_id)["guide"]
        assert actions["universe.add"]["title"] in guide
        assert actions["universe.import"]["title"] in guide
        html = call_route(registry, "GET", f"/api/v1/panels/{panel_id}")["data"]["html"]
        assert "添加公司" in html
        copy.append(html)
    for text in copy:
        assert not any(re.search(pattern, text) for pattern in FORBIDDEN), text


def test_unavailable_archive_disables_actions_and_preserves_product_browsing(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("不是目录")
    config, registry = make_app(tmp_path, archive_root=blocked)
    company(config.output_root)
    actions = call_route(registry, "GET", "/api/v1/actions")["data"]["actions"]
    for action in [a for a in actions if a["id"].startswith("universe.")]:
        assert not action["enabled"]
        assert "存储暂不可用" in action["blockers"][0]
        assert str(tmp_path) not in json.dumps(action["blockers"])
    assert call_route(registry, "GET", "/api/v1/companies")["data"]["count"] == 1
    with pytest.raises(BadRequest) as denied:
        call_route(registry, "POST", "/api/v1/actions/universe.import/run", body={})
    assert "存储暂不可用" in denied.value.hint
    assert not registry.jobs.list_jobs()


def test_import_sanitizes_skipped_labels_with_the_same_copy_rules(tmp_path):
    config, registry = make_app(tmp_path)
    for label in ("python_test", "python", "600887_make data", "600887_company_dir", "A--.SH_公司"):
        (config.output_root / label).mkdir()
    _, report = run(registry, "import")
    assert report["skipped"] == 5 and report["imported"] == 0
    for item in report["items"]:
        assert not any(re.search(pattern, json.dumps(item, ensure_ascii=False)) for pattern in FORBIDDEN)


def test_import_skips_links_without_reading_outside_the_output(tmp_path):
    config, registry = make_app(tmp_path)
    external = company(tmp_path / "external")
    (config.output_root / "600887_链接").symlink_to(external, target_is_directory=True)
    report = execute("import", archive_root=config.archive_root, output_root=config.output_root)
    assert report["imported"] == 0 and report["skipped"] == 1
    assert "链接" in report["items"][0]["reason"]


@pytest.mark.parametrize("identifier, expected", [("000858", "000858.SZ"), ("00700", "00700.HK"), ("AAPL", "AAPL")])
def test_market_resolution(identifier, expected):
    assert resolve_ticker(identifier) == expected
