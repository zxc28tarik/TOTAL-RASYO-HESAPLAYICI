from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from src.analytics.kap_bank_batch_io import (
    KapBankBatchIoError,
    json_safe,
    load_batch_contexts_json,
    load_disclosures_jsonl,
    run_batch_preview_from_files,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test_fixtures" / "kap_bank_batch_e2e"


def _run():
    return run_batch_preview_from_files(
        disclosures_path=FIXTURE / "disclosures.jsonl",
        contexts_path=FIXTURE / "contexts.json",
        analysis_at=datetime.fromisoformat("2026-05-15T20:00:00+03:00"),
        anchor_period_end=date.fromisoformat("2026-03-31"),
        fact_config_path=ROOT / "config" / "mkk_kap_financial_facts_mapping.example.json",
        semantic_config_path=ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json",
        derivation_config_path=ROOT / "config" / "bank_fact_derivation.official_v1.json",
    )


def _summary(report):
    return json_safe({
        "status": report["status"],
        "requested_count": report["requested_count"],
        "result_count": report["result_count"],
        "rejected_count": report["rejected_count"],
        "sector_scale_eligible_count": report["sector_scale_eligible_count"],
        "valuation_ok_count": report["valuation_ok_count"],
        "ranking": report["ranking"],
        "per_ticker": {
            row["ticker"]: {
                "disclosures_used": row["disclosures_used"],
                "raw_facts_extracted": row["raw_facts_extracted"],
                "semantic_facts_mapped": row["semantic_facts_mapped"],
                "bank_metrics_derived": row["bank_metrics_derived"],
                "payout_defaulted": row["valuation"]["valuation"].get("payout_defaulted"),
                "sector_sample_size": row["valuation"].get("sector_sample_size"),
            }
            for row in report["results"]
        },
    })


def test_frozen_batch_fixture_matches_expected_summary():
    expected = json.loads((FIXTURE / "expected_summary.json").read_text(encoding="utf-8"))
    assert _summary(_run()) == expected
    assert len(load_disclosures_jsonl(FIXTURE / "disclosures.jsonl")) == 37
    assert sorted(load_batch_contexts_json(FIXTURE / "contexts.json")) == ["AKBNK", "GARAN", "YKBNK"]
    report = _run()
    by_ticker = {row["ticker"]: row for row in report["results"]}
    assert any("RESTATED" in row["disclosure_id"] for row in by_ticker["YKBNK"]["disclosure_lineage"])
    yk_slots = dict(zip(
        by_ticker["YKBNK"]["canonical"]["quarter_slots"],
        by_ticker["YKBNK"]["canonical"]["selected_publication_times"],
    ))
    assert yk_slots[date(2025, 6, 30)] == datetime.fromisoformat("2025-11-07T00:00:00+00:00")
    assert by_ticker["YKBNK"]["disclosures_used"] == 13
    assert by_ticker["GARAN"]["valuation"]["valuation"]["payout_defaulted"] is True
    assert by_ticker["GARAN"]["valuation"]["v_conf"] == pytest.approx(0.56)
    assert by_ticker["AKBNK"]["valuation"]["v_conf"] == pytest.approx(0.80)


def test_preview_cli_runs_without_opening_a_database_connection(tmp_path):
    report_path = tmp_path / "report.json"
    command = [
        sys.executable, "-m", "src.app.cli", "preview-kap-bank-batch",
        "--file", str(FIXTURE / "disclosures.jsonl"),
        "--contexts-config", str(FIXTURE / "contexts.json"),
        "--mapping-config", "config/mkk_kap_financial_facts_mapping.example.json",
        "--semantic-config", "config/kap_bank_semantic_mapping.official_v1.json",
        "--derivation-config", "config/bank_fact_derivation.official_v1.json",
        "--analysis-at", "2026-05-15T20:00:00+03:00",
        "--anchor", "2026-03-31",
        "--report-out", str(report_path),
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    stdout = json.loads(completed.stdout)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert stdout == saved
    assert stdout["status"] == "COMPLETE"
    assert [row["ticker"] for row in stdout["ranking"]] == ["YKBNK", "AKBNK", "GARAN"]


def test_jsonl_and_context_io_fail_closed_on_bad_boundaries(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(KapBankBatchIoError, match="bos"):
        load_disclosures_jsonl(empty)

    malformed = tmp_path / "bad.jsonl"
    malformed.write_text('{"published_at":"2026-01-01"}\n', encoding="utf-8")
    with pytest.raises(KapBankBatchIoError, match="eksik alanlar"):
        load_disclosures_jsonl(malformed)

    row = json.loads((FIXTURE / "disclosures.jsonl").read_text(encoding="utf-8").splitlines()[0])
    row["published_at"] = "2026-01-01T10:00:00"
    naive = tmp_path / "naive.jsonl"
    naive.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(KapBankBatchIoError, match="timezone"):
        load_disclosures_jsonl(naive)

    structured = dict(row)
    structured["published_at"] = json.loads((FIXTURE / "disclosures.jsonl").read_text(encoding="utf-8").splitlines()[0])["published_at"]
    structured["disclosure_id"] = ["bad"]
    structured_file = tmp_path / "structured.jsonl"
    structured_file.write_text(json.dumps(structured) + "\n", encoding="utf-8")
    with pytest.raises(KapBankBatchIoError, match="disclosure_id"):
        load_disclosures_jsonl(structured_file)

    contexts = tmp_path / "contexts.json"
    contexts.write_text("[]", encoding="utf-8")
    with pytest.raises(KapBankBatchIoError, match="mapping"):
        load_batch_contexts_json(contexts)


def test_removing_the_known_restatement_changes_ykbnk_lineage_and_result(tmp_path):
    rows = load_disclosures_jsonl(FIXTURE / "disclosures.jsonl")
    original_only = [row for row in rows if not (row.ticker == "YKBNK" and "RESTATED" in row.disclosure_id)]
    target = tmp_path / "original_only.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        for row in original_only:
            handle.write(json.dumps(json_safe({
                "disclosure_id": row.disclosure_id,
                "published_at": row.published_at,
                "ticker": row.ticker,
                "company_id": row.company_id,
                "notification_type": row.notification_type,
                "subject": row.subject,
                "source_url": row.source_url,
                "payload": row.payload,
                "payload_sha256": row.payload_sha256,
                "fetched_at": row.fetched_at,
                "source": row.source,
            }), ensure_ascii=False, sort_keys=True) + "\n")
    changed = run_batch_preview_from_files(
        disclosures_path=target,
        contexts_path=FIXTURE / "contexts.json",
        analysis_at=datetime.fromisoformat("2026-05-15T20:00:00+03:00"),
        anchor_period_end=date.fromisoformat("2026-03-31"),
        fact_config_path=ROOT / "config" / "mkk_kap_financial_facts_mapping.example.json",
        semantic_config_path=ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json",
        derivation_config_path=ROOT / "config" / "bank_fact_derivation.official_v1.json",
    )
    baseline = _run()
    base_yk = next(row for row in baseline["results"] if row["ticker"] == "YKBNK")
    changed_yk = next(row for row in changed["results"] if row["ticker"] == "YKBNK")
    base_slots = dict(zip(base_yk["canonical"]["quarter_slots"], base_yk["canonical"]["selected_publication_times"]))
    changed_slots = dict(zip(changed_yk["canonical"]["quarter_slots"], changed_yk["canonical"]["selected_publication_times"]))
    assert base_slots[date(2025, 6, 30)] == datetime.fromisoformat("2025-11-07T00:00:00+00:00")
    assert changed_slots[date(2025, 6, 30)] == datetime.fromisoformat("2025-08-09T00:00:00+00:00")
    assert changed_yk["canonical"]["roe_series"] != base_yk["canonical"]["roe_series"]
    assert changed_yk["valuation"]["valuation"]["V_mid"] != base_yk["valuation"]["valuation"]["V_mid"]
    # Both runs saturate the valuation score at 1.0, so M2/Total can remain equal.
    assert changed_yk["m2"]["diagnostics"]["s_valuation"] == 1.0
    assert base_yk["m2"]["diagnostics"]["s_valuation"] == 1.0
