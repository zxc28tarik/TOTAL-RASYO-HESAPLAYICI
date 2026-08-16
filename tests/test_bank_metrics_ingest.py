from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.ingest.csv_to_core import BANK_METRICS_SPEC, _normalize_df


def test_bank_metrics_ingest_preserves_timezone_aware_publication_time():
    df = pd.DataFrame([
        {
            "ticker": "FIXBNK",
            "period_end": "2025-06-30",
            "version_tag": "ORIGINAL",
            "version_sequence": 1,
            "published_at": "2025-08-08T10:00:00+03:00",
            "source_disclosure_id": "KAP-1",
            "roe_ttm": 0.2809,
            "bvps": 21.24,
            "payout_sus": 0.25,
        }
    ])
    out = _normalize_df(df, None, BANK_METRICS_SPEC)
    value = out.loc[0, "published_at"]
    assert isinstance(value, datetime)
    assert value.utcoffset().total_seconds() == 3 * 3600
    assert out.loc[0, "period_end"].isoformat() == "2025-06-30"


def test_bank_metrics_ingest_rejects_naive_publication_time():
    df = pd.DataFrame([
        {
            "ticker": "FIXBNK",
            "period_end": "2025-06-30",
            "version_tag": "ORIGINAL",
            "version_sequence": 1,
            "published_at": "2025-08-08T10:00:00",
            "source_disclosure_id": "KAP-1",
            "roe_ttm": 0.2809,
            "bvps": 21.24,
            "payout_sus": 0.25,
        }
    ])
    with pytest.raises(ValueError, match="timezone icermeli"):
        _normalize_df(df, None, BANK_METRICS_SPEC)


def test_bank_metrics_ingest_requires_all_contract_columns():
    df = pd.DataFrame([{"ticker": "FIXBNK"}])
    with pytest.raises(ValueError, match="CSV missing required columns"):
        _normalize_df(df, None, BANK_METRICS_SPEC)


class _CopyCursor:
    def __init__(self):
        self.executed = []
        self.copied = []

    def execute(self, sql):
        self.executed.append(sql)

    def copy_expert(self, sql, buf):
        self.copied.append((sql, buf.read()))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _CopyConn:
    def __init__(self):
        self.cur = _CopyCursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_copy_temp_table_does_not_inherit_bigserial_sequence_defaults():
    from src.ingest.csv_to_core import copy_dataframe

    df = pd.DataFrame([
        {
            "ticker": "FIXBNK", "period_end": "2025-06-30", "version_tag": "ORIGINAL",
            "version_sequence": 1, "published_at": "2025-08-08T10:00:00+03:00",
            "source_disclosure_id": "KAP-1", "roe_ttm": 0.2809, "bvps": 21.24,
            "payout_sus": 0.25,
        }
    ])
    df = _normalize_df(df, None, BANK_METRICS_SPEC)
    conn = _CopyConn()
    copy_dataframe(
        conn, BANK_METRICS_SPEC, df, upsert=True,
        key_cols=["ticker", "period_end", "version_tag", "version_sequence", "published_at"],
    )
    create_sql = conn.cur.executed[0]
    assert "SELECT ticker, period_end" in create_sql
    assert "WITH NO DATA" in create_sql
    assert "INCLUDING DEFAULTS" not in create_sql
    assert "LIKE core.bank_metrics_quarterly" not in create_sql


def test_migration_contains_database_level_fail_closed_constraints():
    from pathlib import Path
    sql = (Path(__file__).resolve().parents[1] / "sql" / "011_bank_valuation_integration.sql").read_text().lower()
    for constraint in (
        "ck_bank_metrics_version_sequence",
        "ck_bank_metrics_bvps",
        "ck_bank_metrics_payout",
        "ck_bank_valuation_eight_slots",
        "ck_bank_valuation_missing_count",
        "ck_bank_valuation_confidence",
        "ck_bank_valuation_geometry",
    ):
        assert constraint in sql
    assert "jsonb_array_length(quarter_slots) = 8" in sql
    assert "v_conf is null or v_conf between 0 and 1" in sql


def test_bank_assumption_ingest_uppercases_scope_and_preserves_timezone():
    from src.ingest.csv_to_core import BANK_ASSUMPTIONS_SPEC

    df = pd.DataFrame([{
        "scope_type": "ticker",
        "scope_code": "garan",
        "effective_at": "2026-01-01T00:00:00+03:00",
        "coe": 0.37,
        "macro_cap": 0.14,
        "risk_free_rate": 0.30,
        "tier_cap": 0.8,
        "payout_missing_factor": 0.7,
        "band_width_shadow_mode": True,
        "max_halfwidth": 0.8,
        "source": "MANUAL",
        "metadata": "{}",
    }])
    out = _normalize_df(df, None, BANK_ASSUMPTIONS_SPEC)
    assert out.loc[0, "scope_type"] == "TICKER"
    assert out.loc[0, "scope_code"] == "GARAN"
    assert out.loc[0, "effective_at"].utcoffset().total_seconds() == 3 * 3600
    assert out.loc[0, "risk_free_rate"] == pytest.approx(0.30)


def test_bank_assumption_ingest_rejects_naive_effective_time():
    from src.ingest.csv_to_core import BANK_ASSUMPTIONS_SPEC

    df = pd.DataFrame([{
        "scope_type": "BANK", "scope_code": "BANK",
        "effective_at": "2026-01-01T00:00:00",
        "coe": 0.37, "macro_cap": 0.14, "risk_free_rate": 0.30, "tier_cap": 0.8,
        "payout_missing_factor": 0.7, "band_width_shadow_mode": True,
        "max_halfwidth": 0.8, "source": "MANUAL", "metadata": "{}",
    }])
    with pytest.raises(ValueError, match="timezone icermeli"):
        _normalize_df(df, None, BANK_ASSUMPTIONS_SPEC)
