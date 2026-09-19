
# 覆盖需求：REQ-003（分析迭代台账）—— AC-6/AC-7 状态判定与 reasons、failed run 不得判 up_to_date
"""Tests for the analysis status detector (``scripts/analysis_status.py``)."""

import json
import os
from pathlib import Path

import pytest

import analysis_status
import runs
from analysis_status import evaluate_company, evaluate_root, exit_code_for
from results.manifest import build_manifest, describe_input, write_manifest


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _company_dir_with_run(base: Path, *, name: str = "600887_伊利") -> Path:
    company = base / name
    company.mkdir(parents=True)
    return company


def _ledger_company(base: Path, *, name: str = "600887_伊利", run_id: str = "20260919T221000000000Z"):
    company = base / name
    company.mkdir(parents=True)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        primary_period="2025FY",
        run_id=run_id,
    )
    runs.finish_run(company, created["run_dir"], report_periods=["2025FY"])
    return company, Path(created["run_dir"])


def test_up_to_date_is_exit_zero(tmp_path):
    company, run_path = _ledger_company(tmp_path)
    result = evaluate_company(company)

    assert result["state"] == "up_to_date"
    assert result["recommended_action"] == "none"
    assert result["reasons"] == []
    assert result["latest_run"] == "20260919T221000000000Z"
    assert result["primary_period"] == "2025FY"
    assert result["subject"] == {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}
    assert exit_code_for(result) == 0
    assert set(result) == {"subject", "state", "recommended_action", "reasons", "latest_run", "primary_period"}


def test_local_new_report_requests_incremental_update(tmp_path):
    company, _run_path = _ledger_company(tmp_path)
    _write(company / "sources" / "pdf" / "600887_2026_半年报.pdf", "%PDF-1.4 fake\n")

    result = evaluate_company(company)
    assert result["state"] == "stale"
    assert result["recommended_action"] == "report-update"
    assert [reason["code"] for reason in result["reasons"]] == ["new_report"]
    assert "2026H1" in result["reasons"][0]["detail"]
    assert exit_code_for(result) == 1

    # A flat PDF at the company root is detected too.
    (company / "sources" / "pdf" / "600887_2026_半年报.pdf").unlink()
    _write(company / "600887_2026_年报.pdf", "%PDF-1.4 fake\n")
    assert exit_code_for(evaluate_company(company)) == 1


def test_older_local_report_is_not_a_new_report(tmp_path):
    """N1: backfilling an older period must not trigger a report-update."""

    company, _run_path = _ledger_company(tmp_path)
    _write(company / "sources" / "pdf" / "600887_2023_年报.pdf", "%PDF-1.4 fake\n")

    result = evaluate_company(company)
    assert result["state"] == "up_to_date"
    assert result["reasons"] == []
    assert exit_code_for(result) == 0


@pytest.mark.parametrize("status", ["failed", "partial"])
def test_incomplete_run_is_stale_and_needs_full_rerun(tmp_path, status):
    """B3: a failed/partial latest run must never look up_to_date."""

    company = _company_dir_with_run(tmp_path)
    created = runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="run-status")
    runs.finish_run(company, created["run_dir"], status=status, report_periods=["2025FY"])

    result = evaluate_company(company)
    assert result["state"] == "stale"
    assert result["recommended_action"] == "full-rerun"
    codes = {reason["code"] for reason in result["reasons"]}
    assert "run_failed" in codes
    assert exit_code_for(result) == 3


def test_downstream_stale_is_surfaced(tmp_path):
    """B3: downstream.stale must be visible, not silently up_to_date."""

    company = _company_dir_with_run(tmp_path)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        kind="report-update",
        primary_period="2026H1",
        run_id="run-update",
    )
    runs.finish_run(company, created["run_dir"], primary_period="2026H1")
    assert runs.read_record(company)["downstream"]["stale"] is True

    result = evaluate_company(company)
    assert result["state"] == "stale"
    assert result["recommended_action"] == "report-update"
    assert {reason["code"] for reason in result["reasons"]} == {"downstream_stale"}
    assert exit_code_for(result) == 1


def test_complete_baseline_run_stays_up_to_date(tmp_path):
    """B3: the complete path is unchanged (no false positives)."""

    company, _run_path = _ledger_company(tmp_path)
    record = runs.read_record(company)
    assert record["status"] == "complete"
    assert record["downstream"]["stale"] is False
    assert evaluate_company(company)["state"] == "up_to_date"


def test_downstream_refresh_returns_status_to_up_to_date(tmp_path):
    """NEW-3: report-update -> stale/1 -> mark fresh -> up_to_date/0."""

    company = _company_dir_with_run(tmp_path)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        kind="report-update",
        primary_period="2026H1",
        run_id="run-refresh",
    )
    runs.finish_run(company, created["run_dir"], primary_period="2026H1")

    stale = evaluate_company(company)
    assert stale["state"] == "stale"
    assert {reason["code"] for reason in stale["reasons"]} == {"downstream_stale"}
    assert exit_code_for(stale) == 1

    assert runs.main(["downstream", "--company-dir", str(company), "--fresh", "all"]) == 0

    fresh = evaluate_company(company)
    assert fresh["state"] == "up_to_date"
    assert fresh["recommended_action"] == "none"
    assert fresh["reasons"] == []
    assert exit_code_for(fresh) == 0


def test_framework_change_requests_full_rerun(tmp_path, monkeypatch):
    company, _run_path = _ledger_company(tmp_path)
    record = runs.read_record(company)
    changed = dict(record["framework"])
    changed["version"] = "9.9.9"

    monkeypatch.setattr(analysis_status, "framework_block", lambda *args, **kwargs: changed)
    result = evaluate_company(company)

    assert result["state"] == "stale"
    assert result["recommended_action"] == "full-rerun"
    codes = {reason["code"] for reason in result["reasons"]}
    assert codes == {"framework_changed"}
    assert any("version" in reason["detail"] for reason in result["reasons"])
    assert exit_code_for(result) == 3


def test_schema_change_requests_full_rerun(tmp_path):
    company, _run_path = _ledger_company(tmp_path)
    record = runs.read_record(company)
    changed = dict(record["framework"])
    changed["schema_versions"] = {"result": "2.0", "manifest": "1.0", "evidence_index": "1.0", "context_bundle": "1.0"}

    result = evaluate_company(company, current_framework=changed)
    assert result["state"] == "stale"
    assert {reason["code"] for reason in result["reasons"]} == {"schema_changed"}
    assert exit_code_for(result) == 3


def test_changed_inputs_invalidate_the_latest_run(tmp_path):
    company, run_path = _ledger_company(tmp_path)
    data_pack = _write(company / "data_pack_market.md", "market data\n")
    manifest = build_manifest(
        run_id="20260919T221000000000Z",
        subject={"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
        inputs=[describe_input(data_pack, source_id="market_data")],
    )
    write_manifest(manifest, run_path / "run_manifest.json")

    assert evaluate_company(company)["state"] == "up_to_date"

    data_pack.write_text("mutated market data\n", encoding="utf-8")
    result = evaluate_company(company)
    assert result["state"] == "stale"
    assert {reason["code"] for reason in result["reasons"]} == {"inputs_changed"}
    assert result["recommended_action"] == "full-rerun"
    assert exit_code_for(result) == 3


def test_no_record_state(tmp_path):
    company = tmp_path / "600887_伊利"
    company.mkdir()
    result = evaluate_company(company)
    assert result["state"] == "no_record"
    assert result["recommended_action"] == "full-rerun"
    assert result["latest_run"] is None
    assert exit_code_for(result) == 3


def test_legacy_layout_state(tmp_path):
    company = tmp_path / "600887_伊利"
    company.mkdir()
    _write(company / "run_manifest.json", "{}\n")
    _write(company / "evidence" / "index.json", "{}\n")
    result = evaluate_company(company)
    assert result["state"] == "legacy_layout"
    assert result["recommended_action"] == "full-rerun"
    assert result["reasons"][0]["code"] == "legacy_layout"
    assert exit_code_for(result) == 3


def test_broken_when_latest_run_directory_is_missing(tmp_path):
    company = tmp_path / "600887_伊利"
    company.mkdir()
    _write(
        company / "latest.json",
        json.dumps({"schema": "investment.latest", "run_id": "gone", "run_dir": str(company / "runs" / "gone")}),
    )
    _write(company / "record.json", json.dumps({"schema": "investment.record", "latest_run": "gone"}))
    result = evaluate_company(company)
    assert result["state"] == "broken"
    assert result["reasons"][0]["code"] == "run_dir_missing"
    assert exit_code_for(result) == 3


def test_broken_when_ledger_is_incomplete(tmp_path):
    company = tmp_path / "600887_伊利"
    company.mkdir()
    _write(company / "latest.json", json.dumps({"schema": "investment.latest", "run_id": "x"}))
    result = evaluate_company(company)
    assert result["state"] == "broken"
    assert result["reasons"][0]["code"] == "incomplete_ledger"
    assert exit_code_for(result) == 3


def test_unsupported_market(tmp_path):
    company = tmp_path / "00700_腾讯"
    company.mkdir()
    result = evaluate_company(company, ticker="00700.HK")
    assert result["state"] == "unsupported_market"
    assert result["recommended_action"] == "none"
    assert result["reasons"][0]["code"] == "unsupported_market"
    assert exit_code_for(result) == 3


def test_non_ticker_directory_name_is_not_unsupported(tmp_path):
    company = tmp_path / "mycompany"
    company.mkdir()
    created = runs.create_run(company, ticker=None, company="mycompany", run_id="r1")
    runs.finish_run(company, created["run_dir"])
    result = evaluate_company(company)
    assert result["state"] == "up_to_date"
    assert result["subject"]["ticker"] is None


def test_check_upstream_is_off_by_default(tmp_path, monkeypatch):
    company, _run_path = _ledger_company(tmp_path)

    def explode(*_args, **_kwargs):
        raise AssertionError("discover_periods must not be called without --check-upstream")

    monkeypatch.setattr(analysis_status, "discover_periods", explode)
    assert evaluate_company(company)["state"] == "up_to_date"

    monkeypatch.setattr(analysis_status, "discover_periods", lambda _ticker: [{"period": "2026H1"}])
    result = evaluate_company(company, check_upstream=True)
    assert result["state"] == "stale"
    assert {reason["code"] for reason in result["reasons"]} == {"new_report"}
    assert exit_code_for(result) == 1


def test_cli_single_company_json_and_exit_codes(tmp_path, capsys):
    company, _run_path = _ledger_company(tmp_path)
    assert analysis_status.main(["--company-dir", str(company), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "up_to_date"

    _write(company / "sources" / "pdf" / "600887_2026_半年报.pdf", "%PDF-1.4 fake\n")
    assert analysis_status.main(["--company-dir", str(company), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["recommended_action"] == "report-update"


def test_cli_rejects_bad_arguments(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        analysis_status.main(["--company-dir", str(tmp_path / "missing")])
    assert excinfo.value.code == 2

    with pytest.raises(SystemExit) as excinfo:
        analysis_status.main(["--all"])
    assert excinfo.value.code == 2


def test_ticker_filter_never_relabels_companies(tmp_path):
    """S2: --ticker is a filter; it must not overwrite other subjects."""

    root = tmp_path / "output"
    root.mkdir()
    _ledger_company(root, name="600887_伊利")
    legacy = root / "000858_五粮液"
    legacy.mkdir()
    _write(legacy / "run_manifest.json", "{}\n")
    unknown = root / "portfolio_2026Q2_trial"
    unknown.mkdir()
    _write(unknown / "qualitative_report.md", "legacy\n")

    filtered = evaluate_root(root, ticker="600887.SH")
    assert [item["subject"]["ticker"] for item in filtered["companies"]] == ["600887.SH"]
    assert filtered["summary"]["total"] == 1

    everything = evaluate_root(root)
    tickers = {item["subject"]["ticker"] for item in everything["companies"]}
    assert tickers == {"600887.SH", "000858", None}


def test_all_scan_reports_oserror_with_exit_two(tmp_path, monkeypatch, capsys):
    """S1(b): an OSError during a batch scan must not escape as a traceback."""

    root = tmp_path / "output"
    root.mkdir()

    def explode(_root):
        raise PermissionError("permission denied")

    monkeypatch.setattr(analysis_status, "discover_company_dirs", explode)
    assert analysis_status.main(["--root", str(root), "--all", "--json"]) == 2
    assert "permission denied" in capsys.readouterr().err


def test_unreadable_subdirectory_reports_clean_error(tmp_path, capsys):
    """S1(b): an unreadable child must fail cleanly with exit 2, not traceback."""

    root = tmp_path / "output"
    root.mkdir()
    _ledger_company(root, name="600887_伊利")
    broken = root / "000858_五粮液"
    broken.mkdir()
    _write(broken / "run_manifest.json", "{}\n")
    broken.chmod(0o000)
    try:
        if os.access(broken, os.R_OK):  # running as root: permission bits do not apply
            pytest.skip("cannot make a directory unreadable in this environment")
        assert analysis_status.main(["--root", str(root), "--all", "--json"]) == 2
        assert "Permission denied" in capsys.readouterr().err
    finally:
        broken.chmod(0o755)


def test_root_all_summary_and_exit_code(tmp_path, capsys):
    root = tmp_path / "output"
    root.mkdir()
    _ledger_company(root, name="600887_伊利")

    legacy = root / "000858_五粮液"
    legacy.mkdir()
    _write(legacy / "run_manifest.json", "{}\n")

    payload = evaluate_root(root)
    assert payload["summary"] == {"total": 2, "stale": 1, "up_to_date": 1, "no_record": 0, "broken": 0}
    states = {item["subject"]["ticker"]: item["state"] for item in payload["companies"]}
    assert states["600887.SH"] == "up_to_date"
    assert states["000858"] == "legacy_layout"
    for item in payload["companies"]:
        assert "company_dir" in item and "exit_code" in item

    # 3 (full rerun needed for the legacy directory) outranks 0.
    assert analysis_status.main(["--root", str(root), "--all", "--json"]) == 3
    printed = json.loads(capsys.readouterr().out)
    assert set(printed) == {"companies", "summary"}
    assert printed["summary"]["total"] == 2


def test_downstream_empty_component_is_rejected(tmp_path, capsys):
    import runs

    company = tmp_path / "600887_伊利"
    company.mkdir()
    (company / "record.json").write_text(
        json.dumps(
            {
                "schema": "investment.record",
                "schema_version": "1.0",
                "subject": {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
                "latest_run": "r1",
                "report_periods": [],
                "downstream": {"stale": True, "value_computed": True, "buy_sell_basis": True},
            }
        ),
        encoding="utf-8",
    )

    assert runs.main(["downstream", "--company-dir", str(company), "--fresh", ""]) == 2
    assert "at least one component" in capsys.readouterr().err
