"""Tests for the analysis status detector (``scripts/analysis_status.py``)."""

import json
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
