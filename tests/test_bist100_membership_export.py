from __future__ import annotations

import json

import pandas as pd

from src.analytics.bist100_membership_export import build_bist100_membership_export


SHA="a"*64


def test_combines_periodic_nonperiodic_sources_into_deterministic_monthly_membership(tmp_path):
    snapshot=tmp_path/"snapshot.csv"
    pd.DataFrame([
        {"ticker":"BBB","company_name":"B","snapshot_date":"2026-08-17"},
        {"ticker":"CCC","company_name":"C","snapshot_date":"2026-08-17"},
    ]).to_csv(snapshot,index=False)

    periodic=tmp_path/"periodic.json"
    periodic.write_text(json.dumps({
        "publisher":"Borsa Istanbul A.S.",
        "event_group_count":1,
        "replacement_pair_count":1,
        "events":[{
            "quarter":"2026Q3",
            "effective_date":"2026-07-01",
            "included":["CCC"],
            "excluded":["AAA"],
            "source_final_url":"https://borsaistanbul.com/en/announcement/1/test",
            "source_sha256":SHA,
            "event_type":"PERIODIC_CONSTITUENT_CHANGE",
        }],
    }),encoding="utf-8")

    nonperiodic=tmp_path/"nonperiodic.json"
    nonperiodic.write_text(json.dumps({
        "publisher":"KAP / Borsa Istanbul A.S.",
        "event_count":1,
        "events":[{
            "disclosure_index":123,
            "effective_date":"2026-06-15",
            "source_url":"https://www.kap.org.tr/tr/Bildirim/123",
            "source_detail_sha256":SHA,
            "event_type":"NONPERIODIC_CONSTITUENT_CHANGE",
            "included":["BBB"],
            "excluded":["DDD"],
        }],
    }),encoding="utf-8")

    lineage=tmp_path/"lineage.csv"
    pd.DataFrame(columns=[
        "effective_date","old_ticker","new_ticker","source_workbook_sha256","event_sha256"
    ]).to_csv(lineage,index=False)

    signals=tmp_path/"signals.csv"
    pd.DataFrame([
        {"month":"2026-05","signal_date":"2026-05-04","index_code":"XU100"},
        {"month":"2026-07","signal_date":"2026-07-01","index_code":"XU100"},
    ]).to_csv(signals,index=False)

    result=build_bist100_membership_export(
        snapshot_csv=snapshot,
        periodic_json=periodic,
        nonperiodic_json=nonperiodic,
        ticker_lineage_csv=lineage,
        signal_dates_csv=signals,
        expected_months=2,
        expected_member_count=2,
    )
    assert result.snapshot_date == "2026-08-17"
    assert result.periodic_event_count == 1
    assert result.nonperiodic_event_count == 1
    assert result.ticker_change_count == 0
    got={day:set(group["ticker"]) for day,group in result.frame.groupby("signal_date")}
    assert got == {
        "2026-05-04":{"AAA","DDD"},
        "2026-07-01":{"BBB","CCC"},
    }
    assert len(result.frame) == 4
