from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import src.ingest.bank_fact_materializer as mod
from src.ingest.bank_fact_materializer import (
    BankDerivationConfig,
    BankDerivationError,
    BankDerivedMetric,
    materialize_bank_metrics_batch,
)


CONFIG = BankDerivationConfig.from_dict({
    "derivation_profile": "BATCH_TEST",
    "derivation_version": 1,
    "semantic_profile": "SEM",
    "semantic_version": 1,
    "total_equity_field": "EQ",
    "shares_out_field": "SH",
    "net_income_field": "NI",
    "target_periods": 8,
    "history_periods": 12,
})
ANALYSIS = datetime(2026, 8, 4, 17, tzinfo=timezone.utc)
ANCHOR = date(2026, 6, 30)


class Cursor:
    def __init__(self): self.executed = []
    def execute(self, sql, params=None): self.executed.append((" ".join(sql.split()), params))
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *_): return False


def metric(ticker):
    return BankDerivedMetric(
        ticker=ticker, period_end=ANCHOR, version_tag="DERIVED_X",
        version_sequence=1, published_at=ANALYSIS,
        source_disclosure_id=f"SEMANTIC:{ticker}", roe_ttm=0.2,
        bvps=10.0, payout_sus=0.25, lineage_sha256=(ticker.lower()+"0"*64)[:64],
        source_lineage=(), derivation_profile="BATCH_TEST", derivation_version=1,
        diagnostics={},
    )


def test_batch_dedupes_tickers_isolates_rejections_and_persists_success(monkeypatch):
    conn = Conn()
    calls = []
    monkeypatch.setattr(mod, "fetch_semantic_facts_batch_asof", lambda conn, **kw: {t: (t,) for t in kw["tickers"]})

    def fake_derive(facts, **kw):
        calls.append(kw["ticker"])
        if kw["ticker"] == "BAD":
            raise BankDerivationError("broken facts")
        return (metric(kw["ticker"]),)

    persisted = []
    monkeypatch.setattr(mod, "derive_bank_metrics", fake_derive)
    monkeypatch.setattr(mod, "_persist_bank_metric_cursor", lambda cur, row: persisted.append(row))
    report = materialize_bank_metrics_batch(
        conn, config=CONFIG, tickers=["garAn", "BAD", "GARAN", "AKBNK"],
        analysis_at=ANALYSIS, anchor_period_end=ANCHOR, persist=True,
    )
    assert calls == ["GARAN", "BAD", "AKBNK"]
    assert report.tickers_seen == 3
    assert report.tickers_materialized == 2
    assert report.tickers_rejected == 1
    assert report.metrics_written == 2
    assert report.rejected == {"BAD": "broken facts"}
    assert [row.ticker for row in persisted] == ["GARAN", "AKBNK"]
    sql_text = " ".join(sql for sql, _ in conn.cur.executed)
    assert "INSERT INTO core.bank_metric_derivation_rejections" in sql_text
    assert "DELETE FROM core.bank_metric_derivation_rejections" in sql_text


def test_batch_no_persist_does_not_touch_rejection_table(monkeypatch):
    conn = Conn()
    monkeypatch.setattr(mod, "fetch_semantic_facts_batch_asof", lambda *a, **k: {"GARAN": ()})
    monkeypatch.setattr(mod, "derive_bank_metrics", lambda *a, **k: ())
    report = materialize_bank_metrics_batch(
        conn, config=CONFIG, tickers=["GARAN"], analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR, persist=False,
    )
    assert report.tickers_rejected == 1
    assert report.rejected["GARAN"] == "NO_DERIVABLE_METRICS"
    assert conn.cur.executed == []


@pytest.mark.parametrize("tickers", [[""], [1], [True]])
def test_batch_ticker_contract_is_strict(tickers):
    with pytest.raises(BankDerivationError, match="ticker"):
        materialize_bank_metrics_batch(
            Conn(), config=CONFIG, tickers=tickers, analysis_at=ANALYSIS,
            anchor_period_end=ANCHOR, persist=False,
        )


def test_batch_persist_requires_real_python_bool():
    with pytest.raises(BankDerivationError, match="Python bool"):
        materialize_bank_metrics_batch(
            Conn(), config=CONFIG, tickers=[], analysis_at=ANALYSIS,
            anchor_period_end=ANCHOR, persist=1,
        )
