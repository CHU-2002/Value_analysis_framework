# 覆盖需求：REQ-010（最新价值分析报告发布与历史版本保留）
"""Tests for ``scripts/value_publication.py`` and its ``analysis_status`` wiring.

Covers REQ-010:

- **AC-1**：company status resolves the latest successful analysis run and the
  latest successful value product separately; the value product carries source
  run / fiscal period / freshness, and a newer analysis period makes it `stale`.
- **AC-2**：the single read entry resolves the registered report (or explicitly
  says `stale` / `unavailable`) and never calls an old report current.
- **AC-3**：a publish resolves the latest successful, consumable run, registers
  provenance + a completeness summary, and swaps the pointer atomically.
- **AC-4**：a failed / incomplete product changes no bytes, no metadata and no
  pointer, and leaves a failure record.
- **AC-5**：publishing never rewrites an existing run or historical report;
  history resolves by run id and the pointer digest matches that artifact.
- **AC-6**：incremental-stale, successful refresh, failed refresh and history
  retention are all covered here, fully offline (no network, no real token).
"""

import json
import os
from pathlib import Path

import pytest

import analysis_status
import runs
import value_publication as vp
from results.manifest import build_manifest, describe_input, write_manifest

SUBJECT = {"ticker": "600887.SH", "company": "伊利股份", "market": "CN"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _company(tmp_path: Path, name: str = "600887_伊利") -> Path:
    company = tmp_path / name
    company.mkdir(parents=True)
    return company


def _run(
    company: Path,
    *,
    run_id: str,
    period: str = "2025FY",
    kind: str = "baseline",
    status: str = "complete",
    manifest: bool = True,
) -> Path:
    """Create a recorded analysis run; optionally without a consumable manifest."""

    created = runs.create_run(
        company,
        ticker=SUBJECT["ticker"],
        company=SUBJECT["company"],
        kind=kind,
        primary_period=period,
        run_id=run_id,
    )
    run_path = Path(created["run_dir"])
    report = run_path / "qualitative_report.md"
    report.write_text(f"# {period} 定性分析\n\n- run: {run_id}\n", encoding="utf-8")
    if manifest:
        write_manifest(
            build_manifest(
                run_id=run_id,
                subject=SUBJECT,
                inputs=[describe_input(report, source_id="qualitative_report")],
            ),
            run_path / "run_manifest.json",
        )
    runs.finish_run(company, run_path, status=status, primary_period=period)
    return run_path


def _product(company: Path, *, base_value: float = 28.5, tag: str = "first") -> Path:
    """Write the deterministic value artifacts and return the assembled report."""

    (company / "value_computed.md").write_text(
        "# 价值分析预计算\n\n" + ("锚点与情景明细。\n" * 20), encoding="utf-8"
    )
    (company / "value_computed.json").write_text(
        json.dumps(
            {
                "schema": "investment.value_snapshot",
                "schema_version": "1.0",
                "subject": {"ticker": SUBJECT["ticker"], "currency": "CNY"},
                "as_of": "2026-09-28",
                "financial_period": "2025-12-31",
                "values": {"V_bear": base_value * 0.7, "V_base": base_value, "V_bull": base_value * 1.3},
                "scenarios": [{"scenario": "基准", "per_share": base_value}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report = company / "伊利股份_600887_价值分析报告.md"
    report.write_text(
        f"# 伊利股份 600887 价值分析报告（{tag}）\n\n"
        + ("本报告基于最新一期财报的 owner earnings 与情景估值。\n" * 10),
        encoding="utf-8",
    )
    return report


def _pointer_bytes(company: Path) -> bytes:
    return (company / vp.POINTER_NAME).read_bytes()


# ---------------------------------------------------------------------------
# AC-1 — analysis status resolves run + value product separately
# ---------------------------------------------------------------------------


def test_analysis_status_resolves_the_run_and_the_value_product(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    vp.publish(company)

    result = analysis_status.evaluate_company(company)

    assert result["latest_run"] == "run-A"
    assert result["latest_successful_run"] == {
        "run_id": "run-A",
        "primary_period": "2025FY",
        "kind": "baseline",
        "status": "complete",
    }
    assert result["value"]["state"] == "fresh"
    assert result["value"]["source_run"] == "run-A"
    assert result["value"]["primary_period"] == "2025FY"
    report = Path(result["value"]["report"])
    # The pointer resolves to the frozen revision of that run, and the report is
    # a real file (a tautology here would hide a broken pointer target).
    assert report.is_file()
    assert report.name == vp.SNAPSHOT_REPORT_NAME
    assert report.parent.parent.name == "run-A"
    assert report.parent.parent.parent.name == vp.VALUE_DIR_NAME


def test_newer_analysis_period_makes_the_value_report_stale(tmp_path):
    """AC-1: 最新分析财报期晚于价值分析基准 → `stale`（即使下游标记已清）。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    vp.publish(company)
    assert vp.read_current(company)["state"] == "fresh"

    _run(company, run_id="run-B", period="2026H1")

    current = vp.read_current(company)
    assert current["state"] == "stale"
    assert {reason["code"] for reason in current["reasons"]} == {"newer_run", "newer_period"}
    # The stale report is still identifiable — it is just never called current.
    assert current["source_run"] == "run-A"
    assert current["primary_period"] == "2025FY"

    result = analysis_status.evaluate_company(company)
    assert result["value"]["state"] == "stale"
    assert result["value"]["reason"]["code"] in {"newer_run", "newer_period"}
    assert result["latest_successful_run"]["run_id"] == "run-B"


def test_analysis_status_keeps_the_failed_run_out_of_the_successful_slot(tmp_path):
    """AC-1: 「最新成功分析 run」不是「最新 run」——失败的那个不算数。"""

    from datetime import datetime, timezone

    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _run(company, run_id="run-B", period="2026H1", status="failed")

    result = analysis_status.evaluate_company(company)

    assert result["latest_run"] == "run-B"
    assert result["latest_successful_run"]["run_id"] == "run-A"
    assert result["state"] == "stale"


# ---------------------------------------------------------------------------
# AC-2 — the single read entry
# ---------------------------------------------------------------------------


def test_read_current_returns_the_registered_report(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    published = vp.publish(company)

    current = vp.read_current(company)

    assert current["state"] == "fresh"
    assert current["source_run"] == "run-A"
    assert current["primary_period"] == "2025FY"
    assert current["report"] == published["pointer"]["report"]
    assert current["report_sha256"] == published["pointer"]["report_sha256"]
    assert Path(current["report"]).is_file()


def test_read_current_is_unavailable_without_a_pointer(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "no_pointer"
    assert current["report"] is None
    assert current["source_run"] is None


def test_read_current_refuses_a_tampered_report(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)
    (company / vp.POINTER_NAME).parent  # keep the pointer path explicit for readers
    report = Path(vp.read_current(company)["report"])
    report.write_text("# 被就地改写的报告\n" + "x" * 400, encoding="utf-8")

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "digest_mismatch"


def test_pointer_must_be_tied_to_a_frozen_revision(tmp_path):
    """回归（独立验收 O5）：指针不能改指到历史目录之外的可变报告再「自签」。

    旧实现只比对「指针里那份文件 == 指针里那个摘要」，于是把指针指向公司目录里的活报告、
    并用该文件自己的 sha256 填入 `report_sha256`，一份随时可改的报告就会被读成 `fresh`
    —— 而它并不是 `value_reports/<run_id>/<sha12>/report.md` 这份冻结产物（AC-5）。
    """

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)
    published = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))

    live = company / "伊利股份_600887_价值分析报告.md"
    self_signed = {**published, "report": str(live), "report_sha256": vp.sha256_file(live)}
    (company / vp.POINTER_NAME).write_text(
        json.dumps(self_signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "snapshot_mismatch"
    assert vp.main(["read", "--company-dir", str(company)]) == 3


def test_pointer_pointing_outside_value_reports_is_refused(tmp_path):
    """回归（O5）：`report` 与 `snapshot_dir` 都在 `value_reports/` 之外时一律不可信。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)

    fake = company / "not_a_snapshot"
    fake.mkdir()
    report = fake / "report.md"
    report.write_text("# 自签报告\n" + "x" * 400, encoding="utf-8")
    digest = vp.sha256_file(report)
    (fake / vp.SNAPSHOT_MANIFEST_NAME).write_text(
        json.dumps({"report_sha256": digest}), encoding="utf-8"
    )
    pointer = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))
    pointer.update({"report": str(report), "report_sha256": digest, "snapshot_dir": str(fake)})
    (company / vp.POINTER_NAME).write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "snapshot_mismatch"


def test_pointer_with_a_rehashed_manifest_is_refused(tmp_path):
    """回归（O5）：冻结目录里的 manifest 摘要被改写后，指针不再被信任。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    published = vp.publish(company)
    manifest_path = Path(published["snapshot_dir"]) / vp.SNAPSHOT_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["report_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "snapshot_mismatch"


def test_published_pointer_is_tied_to_its_own_revision(tmp_path):
    """正面用例：`publish` 写出的指针必须满足 O5 的绑定关系。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    published = vp.publish(company)

    current = vp.read_current(company)

    assert current["state"] == "fresh"
    assert current["snapshot_dir"] == published["snapshot_dir"]
    assert Path(current["report"]).parent == Path(current["snapshot_dir"])
    assert Path(current["snapshot_dir"]).is_relative_to(company / vp.VALUE_DIR_NAME / "run-A")


def test_pointer_pointing_at_the_run_directory_is_refused(tmp_path):
    """回归（O6）：只认 `<sha12>` 修订目录，`value_reports/<run_id>/` 这一层不算。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)

    run_level = company / vp.VALUE_DIR_NAME / "run-A"
    report = run_level / vp.SNAPSHOT_REPORT_NAME
    report.write_text("# 手写的 run 层报告\n" + "x" * 400, encoding="utf-8")
    digest = vp.sha256_file(report)
    (run_level / vp.SNAPSHOT_MANIFEST_NAME).write_text(
        json.dumps({"report_sha256": digest}), encoding="utf-8"
    )
    pointer = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))
    pointer.update({"report": str(report), "report_sha256": digest, "snapshot_dir": str(run_level)})
    (company / vp.POINTER_NAME).write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "snapshot_mismatch"


@pytest.mark.parametrize("source_run", ["../..", "run-A/../run-B", ".../x", ""])
def test_pointer_with_a_path_like_source_run_is_refused(tmp_path, source_run):
    """回归（O7）：`source_run` 必须是单个路径分量，不能用来放宽绑定范围。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)

    pointer = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))
    pointer["source_run"] = source_run
    (company / vp.POINTER_NAME).write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] in {"stale", "unavailable"}
    assert current["state"] != "fresh"
    assert current["reason"]["code"] in {"snapshot_mismatch", "pointer_unreadable"}


def test_relative_report_path_resolves_against_the_company_dir(tmp_path):
    """回归（O8）：相对 `report` 与绑定用同一个基准（公司目录），不自相矛盾。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    published = vp.publish(company)

    relative = Path(published["pointer"]["report"]).relative_to(company)
    pointer = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))
    pointer["report"] = str(relative)
    (company / vp.POINTER_NAME).write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] == "fresh"
    assert current["report"] == published["pointer"]["report"]


def test_read_current_reports_a_missing_report(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)
    Path(vp.read_current(company)["report"]).unlink()

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "report_missing"


@pytest.mark.parametrize("removal", ["delete", "null"])
def test_pointer_without_a_digest_is_not_trusted(tmp_path, removal):
    """回归（独立验收 V1）：指针没有 `report_sha256` 时不得跳过摘要校验。

    旧实现只在 `if recorded` 时比对摘要，于是「删掉指针里的摘要字段 + 把报告就地改写」
    会被读成 `current`——AC-5 的「指针摘要 == 历史产物摘要」就成了空话。
    """

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)

    pointer_path = company / vp.POINTER_NAME
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if removal == "delete":
        pointer.pop("report_sha256")
    else:
        pointer["report_sha256"] = None
    pointer_path.write_text(json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = Path(vp.read_current(company)["report"])
    report.write_text("# 被就地改写的报告\n" + "x" * 400, encoding="utf-8")

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "digest_missing"
    # And an untouched report with a digest-less pointer is not called current either.
    assert current["source_run"] == "run-A"


def test_pointer_without_a_digest_is_unavailable_even_when_bytes_match(tmp_path):
    """回归（V1）：摘要字段缺失本身就不可信，不做「内容恰好一致」的兜底。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)
    published = json.loads((company / vp.POINTER_NAME).read_text(encoding="utf-8"))
    published.pop("report_sha256")
    (company / vp.POINTER_NAME).write_text(
        json.dumps(published, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    current = vp.read_current(company)

    assert current["state"] == "unavailable"
    assert current["reason"]["code"] == "digest_missing"
    assert vp.main(["read", "--company-dir", str(company)]) == 3


# ---------------------------------------------------------------------------
# AC-3 — resolving the source run, provenance, atomic pointer
# ---------------------------------------------------------------------------


def test_publish_resolves_the_latest_successful_consumable_run(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _run(company, run_id="run-B", period="2026H1", status="failed")

    _product(company)
    published = vp.publish(company)

    assert published["pointer"]["source_run"] == "run-A"
    assert published["pointer"]["primary_period"] == "2025FY"


def test_publish_registers_provenance_and_a_completeness_summary(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    report = _product(company)

    published = vp.publish(company)
    manifest = json.loads(
        (Path(published["snapshot_dir"]) / vp.SNAPSHOT_MANIFEST_NAME).read_text(encoding="utf-8")
    )

    assert manifest["schema"] == "investment.value_report_snapshot"
    assert manifest["source_run"] == "run-A"
    assert manifest["primary_period"] == "2025FY"
    assert manifest["report_sha256"] == published["pointer"]["report_sha256"]
    completeness = manifest["completeness"]
    assert completeness["complete"] is True
    assert completeness["problems"] == []
    assert completeness["report"]["sha256"] == published["pointer"]["report_sha256"]
    assert completeness["financial_period"] == "2025-12-31"
    assert set(completeness["artifacts"]) == set(vp.SNAPSHOT_ARTIFACTS)
    assert completeness["artifacts"]["value_computed.json"]["present"] is True
    # The frozen copy is byte-identical to the assembled report.
    assert (Path(published["snapshot_dir"]) / vp.SNAPSHOT_REPORT_NAME).read_bytes() == report.read_bytes()


def test_publish_uses_the_unique_assembled_report_when_omitted(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    report = _product(company)

    published = vp.publish(company)

    assert Path(published["pointer"]["report"]).read_bytes() == report.read_bytes()


def test_publish_requires_a_run_store(tmp_path):
    company = _company(tmp_path)
    _product(company)  # legacy flat layout: no latest.json / record.json

    with pytest.raises(vp.ValueUnavailableError) as error:
        vp.publish(company)

    assert "no successful analysis run" in str(error.value)
    assert not (company / vp.POINTER_NAME).exists()
    assert vp.read_failures(company) == []


def test_publish_refuses_a_run_that_is_not_consumable(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", manifest=False)
    _product(company)

    with pytest.raises(vp.ValueUnavailableError) as error:
        vp.publish(company)

    assert "not consumable" in str(error.value)
    assert not (company / vp.POINTER_NAME).exists()


def test_publish_accepts_an_explicit_run_id(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _run(company, run_id="run-B", period="2026H1")
    _product(company)

    published = vp.publish(company, run_id="run-A")

    assert published["pointer"]["source_run"] == "run-A"
    assert published["pointer"]["primary_period"] == "2025FY"


def test_publish_swaps_the_pointer_atomically(tmp_path, monkeypatch):
    """AC-3: 指针必须原子替换（临时文件 + rename），不能就地截断。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)

    observed = []
    real_replace = os.replace

    def _record(source, destination):
        observed.append((Path(source).name, Path(destination).name))
        return real_replace(source, destination)

    monkeypatch.setattr(vp.os, "replace", _record)
    _product(company)
    vp.publish(company)

    assert (f"{vp.POINTER_NAME}.tmp", vp.POINTER_NAME) in observed
    assert not (company / f"{vp.POINTER_NAME}.tmp").exists()
    assert json.loads(_pointer_bytes(company))["source_run"] == "run-A"


def test_publish_cli_prints_the_frozen_report_path(tmp_path, capsys):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)

    assert vp.main(["publish", "--company-dir", str(company)]) == 0

    printed = capsys.readouterr().out.strip()
    assert Path(printed).is_file()
    assert json.loads(_pointer_bytes(company))["report"] == printed


def test_read_cli_exit_codes_track_the_state(tmp_path, capsys):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")

    assert vp.main(["read", "--company-dir", str(company)]) == 3
    assert "state=unavailable" in capsys.readouterr().out

    _product(company)
    assert vp.main(["publish", "--company-dir", str(company)]) == 0
    assert vp.main(["read", "--company-dir", str(company)]) == 0
    assert "state=fresh" in capsys.readouterr().out

    _run(company, run_id="run-B", period="2026H1")
    assert vp.main(["read", "--company-dir", str(company), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "stale"
    assert payload["source_run"] == "run-A"


def test_read_cli_without_a_run_store_is_unavailable(tmp_path, capsys):
    company = _company(tmp_path)
    _product(company)

    assert vp.main(["read", "--company-dir", str(company), "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unavailable"
    assert payload["reason"]["code"] == "no_pointer"


# ---------------------------------------------------------------------------
# AC-4 — a failed / incomplete refresh protects the last good version
# ---------------------------------------------------------------------------


def test_incomplete_product_keeps_every_byte_and_records_the_attempt(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    vp.publish(company)
    frozen_pointer = _pointer_bytes(company)
    frozen_report = Path(vp.read_current(company)["report"]).read_bytes()

    _product(company, tag="second")
    (company / "value_computed.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(vp.ValuePublicationError) as error:
        vp.publish(company)

    assert "value_computed_json_parsable" in str(error.value)
    assert _pointer_bytes(company) == frozen_pointer
    assert Path(vp.read_current(company)["report"]).read_bytes() == frozen_report
    assert vp.read_current(company)["state"] == "fresh"

    failures = vp.read_failures(company)
    assert [entry["reason"] for entry in failures] == ["incomplete_product"]
    assert failures[0]["source_run"] == "run-A"
    assert "value_computed_json_parsable" in failures[0]["detail"]
    assert json.loads((company / "value_report.json").read_text(encoding="utf-8"))["source_run"] == "run-A"


def test_stub_report_is_rejected_as_incomplete(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    (company / "伊利股份_600887_价值分析报告.md").write_text("占位\n", encoding="utf-8")

    with pytest.raises(vp.ValuePublicationError) as error:
        vp.publish(company)

    assert "report_non_empty" in str(error.value)
    assert not (company / vp.POINTER_NAME).exists()
    assert [entry["reason"] for entry in vp.read_failures(company)] == ["incomplete_product"]


def test_missing_value_artifacts_are_rejected(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    (company / "value_computed.json").unlink()

    summary = vp.validate_product(company, company / "伊利股份_600887_价值分析报告.md")

    assert summary["complete"] is False
    assert "value_computed.json_present" in summary["problems"]
    assert str(company / "value_computed.json") in summary["missing"]


def test_failure_cli_records_an_attempt_without_touching_the_pointer(tmp_path, capsys):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)
    frozen_pointer = _pointer_bytes(company)

    assert (
        vp.main(
            [
                "fail",
                "--company-dir",
                str(company),
                "--reason",
                "engine_crashed",
                "--detail",
                "KeyError: 'scenarios'",
                "--run-id",
                "run-B",
            ]
        )
        == 0
    )

    assert "recorded: engine_crashed" in capsys.readouterr().out
    assert _pointer_bytes(company) == frozen_pointer
    failures = vp.read_failures(company)
    assert failures[-1]["reason"] == "engine_crashed"
    assert failures[-1]["detail"] == "KeyError: 'scenarios'"
    assert failures[-1]["source_run"] == "run-B"


def test_failed_refresh_keeps_reporting_stale_not_fresh(tmp_path):
    """AC-4：重算失败后状态必须仍显示 stale / unavailable，不能变成最新。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    vp.publish(company)
    _run(company, run_id="run-B", period="2026H1")

    vp.record_failure(company, run_id="run-B", reason="engine_crashed")

    current = vp.read_current(company)
    assert current["state"] == "stale"
    assert current["source_run"] == "run-A"
    assert current["reason"]["code"] == "newer_run"
    # The previous report is still the published one; the failure is recorded.
    assert Path(current["report"]).is_file()
    assert vp.read_failures(company)[-1]["reason"] == "engine_crashed"


# ---------------------------------------------------------------------------
# AC-5 — history is immutable and traceable by run id
# ---------------------------------------------------------------------------


def test_publishing_a_newer_run_does_not_rewrite_the_older_history(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company)
    first = vp.publish(company)
    first_report = Path(first["pointer"]["report"])
    frozen = first_report.read_bytes()

    _run(company, run_id="run-B", period="2026H1")
    _product(company, base_value=31.0, tag="second")
    second = vp.publish(company)

    assert second["pointer"]["source_run"] == "run-B"
    assert first_report.read_bytes() == frozen
    assert (Path(first["snapshot_dir"]) / vp.SNAPSHOT_REPORT_NAME).read_bytes() == frozen

    history_a = vp.resolve_revision(company, "run-A")
    assert history_a["report_sha256"] == first["pointer"]["report_sha256"]
    assert Path(history_a["report"]).read_bytes() == frozen
    # The current pointer digest matches the frozen artifact of its own run.
    assert second["pointer"]["report_sha256"] == vp.resolve_revision(company, "run-B")["report_sha256"]


def test_a_second_revision_of_the_same_run_is_added_not_overwritten(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A", period="2025FY")
    _product(company, base_value=28.5, tag="first")
    first = vp.publish(company)

    _product(company, base_value=33.0, tag="second")
    second = vp.publish(company)

    assert second["snapshot_dir"] != first["snapshot_dir"]
    assert Path(first["snapshot_dir"]).is_dir()
    latest = vp.resolve_revision(company, "run-A")
    assert latest["report_sha256"] == second["pointer"]["report_sha256"]
    assert len(latest["revisions"]) == 2
    picked = vp.resolve_revision(company, "run-A", digest=first["pointer"]["report_sha256"])
    assert picked["report_sha256"] == first["pointer"]["report_sha256"]


def test_revision_order_follows_the_publish_log_not_the_digest(tmp_path):
    """回归：同一 run 的「最新版本」按发布顺序取，不能按 `{sha12}` 目录名排序。"""

    company = _company(tmp_path)
    _run(company, run_id="run-A")
    run_dir = company / vp.VALUE_DIR_NAME / "run-A"
    for name, digest in (("bbbbbbbbbbbb", "b" * 64), ("aaaaaaaaaaaa", "a" * 64)):
        revision = run_dir / name
        revision.mkdir(parents=True)
        (revision / vp.SNAPSHOT_REPORT_NAME).write_text("# 报告\n" + "x" * 300, encoding="utf-8")
        (revision / vp.SNAPSHOT_MANIFEST_NAME).write_text(
            json.dumps({"report_sha256": digest, "primary_period": "2025FY"}), encoding="utf-8"
        )
    # `bbbb…` was published first, `aaaa…` second (later publish wins despite the name).
    (run_dir / vp.REVISIONS_NAME).write_text(
        json.dumps({"snapshot_dir": str(run_dir / "bbbbbbbbbbbb")})
        + "\n"
        + json.dumps({"snapshot_dir": str(run_dir / "aaaaaaaaaaaa")})
        + "\n",
        encoding="utf-8",
    )

    latest = vp.resolve_revision(company, "run-A")

    assert Path(latest["snapshot_dir"]).name == "aaaaaaaaaaaa"
    assert latest["report_sha256"] == "a" * 64
    assert [Path(item["snapshot_dir"]).name for item in latest["revisions"]] == [
        "bbbbbbbbbbbb",
        "aaaaaaaaaaaa",
    ]


def test_republishing_identical_bytes_is_idempotent(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    first = vp.publish(company)
    second = vp.publish(company)

    assert second["reused"] is True
    assert second["snapshot_dir"] == first["snapshot_dir"]
    assert second["pointer"]["report_sha256"] == first["pointer"]["report_sha256"]
    revisions = (Path(first["snapshot_dir"]).parent / vp.REVISIONS_NAME).read_text(encoding="utf-8")
    assert len([line for line in revisions.splitlines() if line.strip()]) == 1


def test_resolve_reports_unknown_runs_and_digests(tmp_path):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    vp.publish(company)

    with pytest.raises(vp.ValueUnavailableError):
        vp.resolve_revision(company, "run-unknown")
    with pytest.raises(vp.ValueUnavailableError):
        vp.resolve_revision(company, "run-A", digest="0" * 64)

    assert vp.main(["resolve", "--company-dir", str(company), "--run-id", "run-unknown"]) == 3
    assert (
        vp.main(["resolve", "--company-dir", str(company), "--run-id", "run-A", "--digest", "f" * 64]) == 3
    )


def test_resolve_cli_prints_the_historical_report(tmp_path, capsys):
    company = _company(tmp_path)
    _run(company, run_id="run-A")
    _product(company)
    published = vp.publish(company)

    assert vp.main(["resolve", "--company-dir", str(company), "--run-id", "run-A"]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == published["pointer"]["report"]

    assert vp.main(["resolve", "--company-dir", str(company), "--run-id", "run-A", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_sha256"] == published["pointer"]["report_sha256"]


# ---------------------------------------------------------------------------
# wiring: the /value-analysis commands must publish, and the docs must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command_dir", [".claude/commands", ".opencode/commands"])
def test_value_analysis_publishes_the_current_report(command_dir):
    content = (Path(__file__).resolve().parents[1] / command_dir / "value-analysis.md").read_text(
        encoding="utf-8"
    )

    assert "scripts/value_publication.py publish" in content
    # The failure path must be explicit: record it instead of silently keeping
    # the previous report as if it were current.
    assert "scripts/value_publication.py fail" in content
    # Publishing still clears only the component this command refreshes, so the
    # aggregate downstream.stale keeps meaning what the command contract says.
    assert content.index("--fresh value_computed") < content.index("--fresh all")


def test_architecture_and_readme_document_the_value_pointer():
    root = Path(__file__).resolve().parents[1]
    architecture = (root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for content in (architecture, readme):
        assert vp.POINTER_NAME in content
        assert "value_publication.py" in content
