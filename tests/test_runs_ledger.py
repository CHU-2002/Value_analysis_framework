
# 覆盖需求：REQ-003（分析迭代台账）—— AC-2…AC-5 run 生命周期、快照不可变、adopt 接管、指针与台账
"""Tests for the run-store ledger (``scripts/runs.py``)."""

import json
import os
from pathlib import Path

import pytest

import runs
from results.manifest import (
    build_manifest,
    describe_artifact,
    describe_input,
    validate_manifest_artifacts,
    validate_manifest_inputs,
    write_manifest,
)


def _company_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "600887_伊利"
    directory.mkdir()
    return directory


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source_files(tmp_path: Path) -> tuple[Path, Path]:
    market = _write(tmp_path / "src" / "data_pack_market.md", "market data\n")
    pdf = _write(tmp_path / "src" / "600887_2025_年报.pdf", "%PDF-1.4 fake\n")
    return market, pdf


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


def test_new_snapshots_inputs_with_immutable_copies(tmp_path):
    company = _company_dir(tmp_path)
    market, pdf = _source_files(tmp_path)

    result = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        market="CN",
        primary_period="2025FY",
        inputs=[market, pdf],
        run_id="20260919T221000000000Z",
    )

    run_path = company / "runs" / "20260919T221000000000Z"
    assert Path(result["run_dir"]) == run_path.resolve()
    assert (run_path / "run.json").is_file()
    assert (run_path / "inputs" / "data_pack_market.md").is_file()
    assert (run_path / "inputs" / "600887_2025_年报.pdf").is_file()

    run_meta = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_meta["run_id"] == "20260919T221000000000Z"
    assert run_meta["kind"] == "baseline"
    assert run_meta["primary_period"] == "2025FY"
    assert run_meta["supersedes"] is None
    assert run_meta["subject"] == {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}
    assert run_meta["framework"]["version"] == "0.2.0"

    manifest = json.loads((run_path / "inputs" / "sources_manifest.json").read_text(encoding="utf-8"))
    entries = {Path(item["source_path"]).name: item for item in manifest["sources"]}
    assert set(entries) == {"data_pack_market.md", "600887_2025_年报.pdf"}
    for item in entries.values():
        assert set(item) == {"source_path", "snapshot_path", "sha256", "size_bytes", "hardlinked", "method"}
        assert Path(item["snapshot_path"]).is_file()
        assert item["size_bytes"] == Path(item["snapshot_path"]).stat().st_size
        # B1: the default is a real copy, never a shared inode.
        assert item["method"] == "copy"
        assert item["hardlinked"] is False


def test_new_snapshot_is_not_rewritten_when_source_is_overwritten(tmp_path):
    """B1: refreshing a shared source in place must not mutate the run snapshot."""

    company = _company_dir(tmp_path)
    market, _pdf = _source_files(tmp_path)
    result = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        inputs=[market],
        run_id="frozen",
    )
    entry = result["sources"][0]
    snapshot = Path(entry["snapshot_path"])
    assert entry["hardlinked"] is False

    # pdf_preprocessor / tushare_collector refresh paths overwrite in place.
    market.write_text("refreshed market data that must not leak\n", encoding="utf-8")

    assert snapshot.read_text(encoding="utf-8") == "market data\n"
    assert runs.sha256_file(snapshot) == entry["sha256"]
    manifest = json.loads((company / "runs" / "frozen" / "inputs" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sources"][0]["sha256"] == entry["sha256"]


def test_new_explicit_hardlink_opt_in(tmp_path):
    company = _company_dir(tmp_path)
    market, _pdf = _source_files(tmp_path)
    result = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        inputs=[market],
        run_id="linked",
        hardlink=True,
    )
    entry = result["sources"][0]
    if entry["method"] != "hardlink":
        pytest.skip("filesystem does not support hardlinks")
    assert entry["hardlinked"] is True
    assert os.stat(market).st_ino == os.stat(entry["snapshot_path"]).st_ino


def test_new_rejects_duplicate_run_id(tmp_path):
    company = _company_dir(tmp_path)
    runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="dup")
    with pytest.raises(runs.DuplicateRunError):
        runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="dup")


def test_new_rejects_unknown_kind_and_invalid_period(tmp_path):
    company = _company_dir(tmp_path)
    with pytest.raises(runs.LedgerError):
        runs.create_run(company, ticker="600887.SH", company="伊利股份", kind="magic")
    with pytest.raises(runs.LedgerError):
        runs.create_run(company, ticker="600887.SH", company="伊利股份", primary_period="2025Q2")
    with pytest.raises(runs.LedgerError):
        runs.create_run(company, ticker="600887.SH", company="伊利股份", inputs=[tmp_path / "missing.md"])


def test_new_failed_invocation_leaves_no_half_built_run(tmp_path):
    """S3: a rejected input must not create runs/<id>/ or lock the run id."""

    company = _company_dir(tmp_path)
    with pytest.raises(runs.LedgerError):
        runs.create_run(
            company,
            ticker="600887.SH",
            company="伊利股份",
            inputs=[tmp_path / "missing.md"],
            run_id="retryable",
        )
    assert not (company / "runs" / "retryable").exists()

    # The same id is still usable once the inputs are valid.
    market, _pdf = _source_files(tmp_path)
    result = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        inputs=[market],
        run_id="retryable",
    )
    assert result["run_id"] == "retryable"


def test_new_reports_oserror_as_ledger_error(tmp_path):
    """S1(a): unreadable inputs must not escape as an uncaught OSError."""

    company = _company_dir(tmp_path)
    market, _pdf = _source_files(tmp_path)
    if os.access(market, os.R_OK):
        market.chmod(0o000)
    try:
        if os.access(market, os.R_OK):  # running as root: the bit is meaningless
            pytest.skip("cannot make a file unreadable in this environment")
        with pytest.raises(runs.LedgerError):
            runs.create_run(
                company,
                ticker="600887.SH",
                company="伊利股份",
                inputs=[market],
                run_id="unreadable",
            )
        assert not (company / "runs" / "unreadable").exists()
    finally:
        market.chmod(0o644)


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_latest_and_by_run_id(tmp_path):
    company = _company_dir(tmp_path)
    created = runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="run-a")
    runs.finish_run(company, created["run_dir"], report_periods=["2025FY"])

    assert runs.resolve_run(company, latest=True) == (company / "runs" / "run-a").resolve()
    assert runs.resolve_run(company, run_id="run-a") == (company / "runs" / "run-a").resolve()
    # Neither flag defaults to the latest pointer.
    assert runs.resolve_run(company) == (company / "runs" / "run-a").resolve()

    with pytest.raises(runs.ResolutionError):
        runs.resolve_run(company, run_id="nope")
    with pytest.raises(runs.ResolutionError):
        runs.resolve_run(tmp_path / "empty-company", latest=True)


def test_resolve_cli_exit_codes(tmp_path, capsys):
    company = _company_dir(tmp_path)
    assert runs.main(["resolve", "--company-dir", str(company), "--latest"]) == 3
    assert "no latest.json" in capsys.readouterr().err

    assert runs.main(["new", "--company-dir", str(company), "--ticker", "600887.SH", "--company", "伊利股份", "--primary-period", "2025FY", "--run-id", "cli-run"]) == 0
    run_path = capsys.readouterr().out.strip()
    assert Path(run_path) == (company / "runs" / "cli-run").resolve()
    assert runs.main(["finish", "--company-dir", str(company), "--run-dir", run_path, "--primary-period", "2025FY"]) == 0
    capsys.readouterr()

    assert runs.main(["resolve", "--company-dir", str(company), "--latest"]) == 0
    assert Path(capsys.readouterr().out.strip()) == Path(run_path)


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------


def test_finish_writes_history_pointer_and_record(tmp_path):
    company = _company_dir(tmp_path)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        primary_period="2026H1",
        run_id="20260919T221000000000Z",
    )
    run_path = Path(created["run_dir"])
    _write(run_path / "qualitative_report.md", "report body")

    result = runs.finish_run(
        company,
        run_path,
        status="complete",
        report_periods=["2023FY", "2024FY", "2026H1"],
        conclusions_changed=["moat_rating 强→较强"],
        artifacts=[f"report={run_path / 'qualitative_report.md'}"],
    )

    history = runs.read_history(company)
    assert len(history) == 1
    entry = history[0]
    assert entry["run_id"] == "20260919T221000000000Z"
    assert entry["kind"] == "baseline"
    assert entry["status"] == "complete"
    assert entry["subject"] == {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}
    assert entry["report_periods"] == ["2023FY", "2024FY", "2026H1"]
    assert entry["primary_period"] == "2026H1"
    assert entry["framework"]["version"] == "0.2.0"
    assert entry["framework"]["schema_versions"]["manifest"] == "1.0"
    assert entry["conclusions_changed"] == ["moat_rating 强→较强"]
    assert entry["artifacts"]["report"] == str((run_path / "qualitative_report.md").resolve())
    assert entry["trigger"]["type"] == "manual"

    latest = json.loads((company / "latest.json").read_text(encoding="utf-8"))
    assert latest["schema"] == "investment.latest"
    assert latest["schema_version"] == "1.0"
    assert latest["run_id"] == entry["run_id"]
    assert latest["primary_period"] == "2026H1"
    assert latest["run_dir"] == str(run_path)
    assert latest["artifacts"] == entry["artifacts"]

    record = json.loads((company / "record.json").read_text(encoding="utf-8"))
    assert record["schema"] == "investment.record"
    assert record["latest_run"] == entry["run_id"]
    assert record["report_periods"] == ["2023FY", "2024FY", "2026H1"]
    assert record["primary_period"] == "2026H1"
    assert record["downstream"] == {"stale": False, "value_computed": False, "buy_sell_basis": False}
    assert runs.read_latest(company) == latest
    assert runs.read_record(company) == record
    assert result["replaced"] is False


def test_finish_marks_downstream_stale_for_update_runs(tmp_path):
    company = _company_dir(tmp_path)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        kind="report-update",
        primary_period="2026H1",
        supersedes="baseline-run",
        run_id="update-run",
    )
    runs.finish_run(company, created["run_dir"], primary_period="2026H1")

    record = runs.read_record(company)
    assert record["downstream"]["stale"] is True
    assert record["kind"] == "report-update"
    assert runs.read_history(company)[0]["supersedes"] == "baseline-run"
    # No explicit --report-periods: default to the primary period only.
    assert record["report_periods"] == ["2026H1"]


def test_finish_rejects_duplicate_run_id_and_force_replaces(tmp_path):
    company = _company_dir(tmp_path)
    created = runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="run-1")
    runs.finish_run(company, created["run_dir"], status="complete")

    with pytest.raises(runs.DuplicateRunError):
        runs.finish_run(company, created["run_dir"], status="partial")

    replaced = runs.finish_run(company, created["run_dir"], status="partial", force=True)
    assert replaced["replaced"] is True
    history = runs.read_history(company)
    assert len(history) == 1
    assert history[0]["status"] == "partial"
    assert runs.read_record(company)["status"] == "partial"


def test_finish_accepts_cwd_relative_run_dir(tmp_path, monkeypatch):
    company = _company_dir(tmp_path)
    runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="relative-run")
    monkeypatch.chdir(company)
    result = runs.finish_run(company, "runs/relative-run")
    assert result["run_id"] == "relative-run"
    assert Path(result["run_dir"]) == (company / "runs" / "relative-run").resolve()


def test_finish_cli_returns_two_for_duplicate(tmp_path, capsys):
    company = _company_dir(tmp_path)
    run_path = runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="cli-dup")["run_dir"]
    assert runs.main(["finish", "--company-dir", str(company), "--run-dir", run_path]) == 0
    capsys.readouterr()
    assert runs.main(["finish", "--company-dir", str(company), "--run-dir", run_path]) == 2
    assert "already recorded" in capsys.readouterr().err


def test_finish_rejects_run_dir_outside_company_runs(tmp_path):
    """S5: latest.json.run_dir must never point outside <company>/runs/."""

    company = _company_dir(tmp_path)
    outsider = tmp_path / "elsewhere"
    outsider.mkdir()
    (outsider / "run.json").write_text(json.dumps({"run_id": "outsider"}), encoding="utf-8")

    with pytest.raises(runs.LedgerError):
        runs.finish_run(company, outsider)
    assert not (company / "latest.json").exists()


# ---------------------------------------------------------------------------
# downstream refresh (NEW-3)
# ---------------------------------------------------------------------------


def test_mark_downstream_fresh_clears_components_and_aggregate(tmp_path):
    company = _company_dir(tmp_path)
    created = runs.create_run(
        company,
        ticker="600887.SH",
        company="伊利股份",
        kind="report-update",
        primary_period="2026H1",
        run_id="update-1",
    )
    runs.finish_run(company, created["run_dir"], primary_period="2026H1")
    assert runs.read_record(company)["downstream"] == {
        "stale": True,
        "value_computed": True,
        "buy_sell_basis": True,
    }

    # Refreshing /value-analysis only leaves the aggregate stale.
    record = runs.mark_downstream_fresh(company, components=["value_computed"])
    assert record["downstream"]["value_computed"] is False
    assert record["downstream"]["buy_sell_basis"] is True
    assert record["downstream"]["stale"] is True

    # The CLI clears everything (default --fresh all).
    assert runs.main(["downstream", "--company-dir", str(company)]) == 0
    downstream = runs.read_record(company)["downstream"]
    assert downstream == {"stale": False, "value_computed": False, "buy_sell_basis": False}


def test_mark_downstream_fresh_rejects_unknown_component(tmp_path):
    company = _company_dir(tmp_path)
    runs.finish_run(
        company,
        runs.create_run(company, ticker="600887.SH", company="伊利股份", run_id="update-2")["run_dir"],
    )
    with pytest.raises(runs.LedgerError):
        runs.mark_downstream_fresh(company, components=["nope"])
    assert runs.main(["downstream", "--company-dir", str(company), "--fresh", "nope"]) == 2


def test_mark_downstream_fresh_requires_record(tmp_path):
    company = _company_dir(tmp_path)
    with pytest.raises(runs.ResolutionError):
        runs.mark_downstream_fresh(company)


# ---------------------------------------------------------------------------
# adopt
# ---------------------------------------------------------------------------

LEGACY_RUN_ID = "20260913T152947378594Z"


def _make_flat_directory(tmp_path: Path) -> Path:
    company = _company_dir(tmp_path)
    _write(company / "600887_2023_年报.pdf", "%PDF-1.4 PDFSECRETBYTES\n")
    _write(company / "600887_2024_年报.pdf", "%PDF-1.4 fake\n")
    _write(company / "600887_2025_年报.pdf", "%PDF-1.4 fake\n")
    _write(company / "pdf_sections_2024.json", json.dumps({"metadata": {"pdf_file": "600887_2025_年报.pdf"}, "MDA": "x"}))
    _write(company / "data_pack_market.md", "market data\n")
    _write(company / "data_pack_report.md", "footnotes\n")
    _write(company / "qualitative_report.md", "LEGACY-REPORT-BODY-SENTINEL\n")
    _write(company / "qualitative_input.json", "{}\n")
    _write(company / "evidence" / "index.json", '{"schema": "investment.evidence_index"}\n')
    _write(company / "contexts" / "business_moat.json", "{}\n")
    _write(company / "modules" / "business_moat" / "result.json", "{}\n")
    _write(company / "synthesis" / "result.json", "{}\n")

    manifest = build_manifest(
        run_id=LEGACY_RUN_ID,
        subject={"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
        inputs=[describe_input(company / "data_pack_market.md", source_id="market_data")],
        artifacts=[
            describe_artifact(company / "evidence" / "index.json", role="evidence_index"),
            describe_artifact(company / "contexts" / "business_moat.json", role="context_bundle", module="business_moat"),
        ],
        status="prepared",
    )
    write_manifest(manifest, company / "run_manifest.json")
    return company


def test_adopt_copies_flat_layout_without_deleting_originals(tmp_path):
    company = _make_flat_directory(tmp_path)
    result = runs.adopt_legacy(company)

    run_path = company / "runs" / LEGACY_RUN_ID
    assert Path(result["run_dir"]) == run_path.resolve()
    # S6: the adopted run keeps the identity recorded in the legacy manifest.
    assert result["run_id"] == LEGACY_RUN_ID
    for relative in (
        "run_manifest.json",
        "qualitative_report.md",
        "qualitative_input.json",
        "data_pack_market.md",
        "data_pack_report.md",
        "pdf_sections_2024.json",
        "600887_2023_年报.pdf",
        "evidence/index.json",
        "contexts/business_moat.json",
        "modules/business_moat/result.json",
        "synthesis/result.json",
    ):
        assert (run_path / relative).is_file(), relative
        # Default is non-destructive: every original is still in place.
        assert (company / relative).is_file(), relative

    assert result["report_periods"] == ["2023FY", "2024FY", "2025FY"]
    assert result["primary_period"] == "2025FY"
    record = runs.read_record(company)
    assert record["report_periods"] == ["2023FY", "2024FY", "2025FY"]
    assert record["primary_period"] == "2025FY"
    assert runs.read_history(company)[0]["trigger"]["type"] == "adopt"


def test_adopt_rejects_conflicting_run_id(tmp_path):
    company = _make_flat_directory(tmp_path)
    with pytest.raises(runs.LedgerError):
        runs.adopt_legacy(company, run_id="a-different-id")
    assert not (company / "runs").exists()


def test_adopt_ignores_phantom_pdf_sections_period(tmp_path):
    """S4: metadata.pdf_file must not invent a period whose PDF is absent."""

    company = _company_dir(tmp_path)
    _write(company / "600887_2024_年报.pdf", "%PDF-1.4 fake\n")
    _write(
        company / "pdf_sections.json",
        json.dumps({"metadata": {"pdf_file": "600887_2021_年报.pdf"}, "MDA": "x"}),
    )
    write_manifest(
        build_manifest(
            run_id=LEGACY_RUN_ID,
            subject={"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
            inputs=[],
        ),
        company / "run_manifest.json",
    )

    result = runs.adopt_legacy(company)
    assert result["report_periods"] == ["2024FY"]
    assert result["primary_period"] == "2024FY"


def test_adopt_rewrites_manifest_paths_into_the_run(tmp_path):
    company = _make_flat_directory(tmp_path)
    runs.adopt_legacy(company)

    run_path = company / "runs" / LEGACY_RUN_ID
    copied = json.loads((run_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert copied["inputs"][0]["path"].startswith(str(run_path.resolve()))
    assert validate_manifest_inputs(copied) == []
    # Original manifest is untouched by the rewrite.
    original = json.loads((company / "run_manifest.json").read_text(encoding="utf-8"))
    assert original["inputs"][0]["path"] == str((company / "data_pack_market.md").resolve())


def test_adopt_propagates_input_digest_into_evidence_and_contexts(tmp_path):
    """B2: a rewritten manifest digest must reach the evidence index/contexts."""

    company = _make_flat_directory(tmp_path)
    runs.adopt_legacy(company)
    run_path = company / "runs" / LEGACY_RUN_ID

    manifest = json.loads((run_path / "run_manifest.json").read_text(encoding="utf-8"))
    evidence = json.loads((run_path / "evidence" / "index.json").read_text(encoding="utf-8"))
    context = json.loads((run_path / "contexts" / "business_moat.json").read_text(encoding="utf-8"))
    assert evidence["input_digest"] == manifest["input_digest"]
    assert context["input_digest"] == manifest["input_digest"]
    # The two artifacts this migration rewrote are re-stamped...
    assert manifest["artifacts"], "fixture manifest must carry artifacts"
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"])
        assert path.is_file()
        assert artifact["sha256"] == runs.sha256_file(path)
        assert artifact["size_bytes"] == path.stat().st_size
    # ...and the adopted run is fully valid.
    assert validate_manifest_artifacts(manifest) == []


def test_adopt_does_not_launder_tampered_artifact_hashes(tmp_path):
    """NEW-4: only rewritten artifacts may be re-stamped."""

    company = _company_dir(tmp_path)
    _write(company / "600887_2024_年报.pdf", "%PDF-1.4 fake\n")
    trigger = _write(company / "d6_trigger.json", '{"triggered": false}\n')
    write_manifest(
        build_manifest(
            run_id=LEGACY_RUN_ID,
            subject={"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
            inputs=[],
            artifacts=[
                {
                    "path": str(trigger),
                    "role": "routing_decision",
                    "size_bytes": 1,
                    "sha256": "0" * 64,
                }
            ],
        ),
        company / "run_manifest.json",
    )
    before = json.loads((company / "run_manifest.json").read_text(encoding="utf-8"))
    assert validate_manifest_artifacts(before), "fixture must start tampered"

    # Refuse rather than re-stamp: the mismatch is evidence, not noise.
    with pytest.raises(runs.LedgerError, match="refusing to adopt"):
        runs.adopt_legacy(company)

    assert not (company / "runs" / LEGACY_RUN_ID).exists(), "failed adopt leaves no run directory"


def test_adopt_refuses_tampered_evidence_index(tmp_path):
    """The artifacts adoption must rewrite are verified before they are rewritten."""

    company = _company_dir(tmp_path)
    _write(company / "600887_2024_年报.pdf", "%PDF-1.4 fake\n")
    index = _write(company / "evidence" / "index.json", '{"schema": "investment.evidence_index"}\n')
    write_manifest(
        build_manifest(
            run_id=LEGACY_RUN_ID,
            subject={"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
            inputs=[],
            artifacts=[
                {
                    "path": str(index),
                    "role": "evidence_index",
                    "size_bytes": 1,
                    "sha256": "0" * 64,
                }
            ],
        ),
        company / "run_manifest.json",
    )

    with pytest.raises(runs.LedgerError, match="refusing to adopt"):
        runs.adopt_legacy(company)

    assert not (company / "runs" / LEGACY_RUN_ID).exists()


@pytest.mark.parametrize("prune", [False, True])
def test_adopt_produces_consumable_structured_run(tmp_path, prune):
    """B2 end-to-end: an adopted structured run still resolves as 'structured'."""

    from results.resolve_qualitative import resolve_qualitative_input
    from tests.test_results_pipeline import _write_complete_structured_run

    company = tmp_path / "600000_Example"
    _write_complete_structured_run(company)

    result = runs.adopt_legacy(company, prune=prune)
    run_path = Path(result["run_dir"])
    assert result["run_id"] == "test-run"

    payload = resolve_qualitative_input(run_path, ticker="600000.SH")
    assert payload["source"] == "structured", payload["warnings"]


def test_adopt_prune_only_runs_after_verification(tmp_path):
    company = _make_flat_directory(tmp_path)
    runs.adopt_legacy(company, prune=True)

    for relative in (
        "run_manifest.json",
        "qualitative_report.md",
        "data_pack_market.md",
        "600887_2023_年报.pdf",
        "evidence",
        "contexts",
        "modules",
        "synthesis",
    ):
        assert not (company / relative).exists(), relative
        assert (company / "runs" / LEGACY_RUN_ID / relative).exists(), relative
    assert (company / "latest.json").is_file()
    assert (company / "record.json").is_file()
    assert (company / "history.jsonl").is_file()


def test_adopt_aborts_prune_when_verification_fails(tmp_path, monkeypatch):
    company = _make_flat_directory(tmp_path)

    def fail_verification(*_args, **_kwargs):
        raise runs.LedgerError("verification failed")

    monkeypatch.setattr(runs, "_verify_copy", fail_verification)
    with pytest.raises(runs.LedgerError):
        runs.adopt_legacy(company, prune=True)

    assert (company / "run_manifest.json").is_file()
    assert (company / "600887_2023_年报.pdf").is_file()
    assert not (company / "latest.json").exists()


def test_adopt_refuses_directory_that_already_has_a_ledger(tmp_path):
    company = _make_flat_directory(tmp_path)
    runs.adopt_legacy(company)
    with pytest.raises(runs.LedgerError):
        runs.adopt_legacy(company)


def test_adopt_requires_legacy_artifacts(tmp_path):
    company = _company_dir(tmp_path)
    with pytest.raises(runs.LedgerError):
        runs.adopt_legacy(company)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_omits_pdf_and_report_bodies(tmp_path, capsys):
    company = _make_flat_directory(tmp_path)
    runs.adopt_legacy(company)

    markdown = runs.export_ledger(company, format="md")
    assert "LEGACY-REPORT-BODY-SENTINEL" not in markdown
    assert "PDFSECRETBYTES" not in markdown
    assert LEGACY_RUN_ID in markdown
    assert "2023FY" in markdown

    payload = json.loads(runs.export_ledger(company, format="json"))
    assert payload["subject"]["ticker"] == "600887.SH"
    assert payload["report_periods"] == ["2023FY", "2024FY", "2025FY"]
    assert payload["runs"][0]["run_id"] == LEGACY_RUN_ID
    serialised = json.dumps(payload)
    assert "LEGACY-REPORT-BODY-SENTINEL" not in serialised
    assert "PDFSECRETBYTES" not in serialised

    assert runs.main(["export", "--company-dir", str(company), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["latest_run"] == LEGACY_RUN_ID


def test_export_without_ledger_fails(tmp_path):
    company = _company_dir(tmp_path)
    with pytest.raises(runs.ResolutionError):
        runs.export_ledger(company)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def test_new_cli_rejects_missing_input(tmp_path, capsys):
    company = _company_dir(tmp_path)
    code = runs.main(
        [
            "new",
            "--company-dir",
            str(company),
            "--ticker",
            "600887.SH",
            "--company",
            "伊利股份",
            "--input",
            str(tmp_path / "missing.pdf"),
        ]
    )
    assert code == 2
    assert "not a regular file" in capsys.readouterr().err


def test_full_cli_roundtrip_with_artifacts(tmp_path, capsys):
    company = _company_dir(tmp_path)
    assert (
        runs.main(
            [
                "new",
                "--company-dir",
                str(company),
                "--ticker",
                "600887.SH",
                "--company",
                "伊利股份",
                "--market",
                "CN",
                "--kind",
                "baseline",
                "--primary-period",
                "2026H1",
                "--run-id",
                "roundtrip",
            ]
        )
        == 0
    )
    run_path = Path(capsys.readouterr().out.strip())
    _write(run_path / "qualitative_report.md", "body")

    code = runs.main(
        [
            "finish",
            "--company-dir",
            str(company),
            "--run-dir",
            str(run_path),
            "--status",
            "complete",
            "--primary-period",
            "2026H1",
            "--report-periods",
            "2025FY,2026H1",
            "--artifact",
            "report=qualitative_report.md",
            "--conclusion-changed",
            "moat 强→较强",
        ]
    )
    assert code == 0
    capsys.readouterr()

    entry = runs.read_history(company)[0]
    assert entry["report_periods"] == ["2025FY", "2026H1"]
    assert entry["artifacts"]["report"] == str((run_path / "qualitative_report.md").resolve())
    assert entry["conclusions_changed"] == ["moat 强→较强"]


# ---------------------------------------------------------------------------
# manifest framework block (additive)
# ---------------------------------------------------------------------------


def test_build_manifest_framework_block_is_optional_and_additive():
    kwargs = {
        "run_id": "manifest-run",
        "subject": {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"},
        "inputs": [],
    }
    without = build_manifest(**kwargs)
    assert "framework" not in without
    assert without["schema_version"] == "1.0"

    framework = {"version": "0.2.0", "prompt_fingerprint": "sha256:abc"}
    with_framework = build_manifest(**kwargs, framework=framework)
    assert with_framework["framework"] == framework
    assert set(with_framework) - set(without) == {"framework"}
    # Everything except the timestamp and the new key stays byte-for-byte equal.
    def _stable(payload):
        return {key: value for key, value in payload.items() if key not in {"framework", "generated_at"}}

    assert _stable(with_framework) == _stable(without)
    assert with_framework["schema_version"] == "1.0"
