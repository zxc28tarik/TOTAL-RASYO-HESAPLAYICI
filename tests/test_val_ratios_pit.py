"""
V24-A saf testleri — VAL oran formullerinin kendisi (fiyat/PIT sorgusu
haric, o kisim canli testte).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.analytics.val_ratios_pit import (
    VAL_RATIO_NAMES,
    ValFinancialRow,
    ValRatioPitError,
    _compute_val_ratios_for_row,
    _sum4q,
    resolve_t0_date,
)

TZ = timezone(timedelta(hours=3))
Q4 = date(2025, 12, 31)


def row(pe, *, rev=100.0, ni=10.0, teq=200.0, ebit=15.0, dst=5.0, dlt=20.0,
       cash=8.0, sti=2.0, shares=1000.0, pub=None):
    return ValFinancialRow(
        ticker="GARFA", period_end=pe, version_tag="v1",
        published_at=pub or datetime(pe.year, pe.month, 28, 10, 0, tzinfo=TZ),
        version_sequence=1, source_disclosure_id="D1", lineage_sha256="L1",
        revenue=rev, net_income=ni, total_equity=teq, ebit=ebit,
        debt_st=dst, debt_lt=dlt, cash_and_eq=cash, st_investments=sti,
        shares_out=shares)


def dort_ceyrek(anchor=Q4, **kw):
    from src.analytics.val_ratios_pit import _shift_quarter_end
    return {_shift_quarter_end(anchor, off): row(_shift_quarter_end(anchor, off), **kw)
           for off in (-3, -2, -1, 0)}


def test_market_cap_ve_pb_tek_donemle_hesaplanir():
    periods = {Q4: row(Q4)}
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["MARKET_CAP_PROXY"] == 10.0 * 1000.0
    assert r["PB"] == pytest.approx(10000.0 / 200.0)


def test_pe_ps_ev_ebit_dort_ceyrek_gerektirir():
    periods = {Q4: row(Q4)}
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["PE_TTM"] is None
    assert r["PS_TTM"] is None
    assert r["EV_EBIT_TTM"] is None
    assert r["MARKET_CAP_PROXY"] is not None


def test_dort_ceyrek_tamsa_ttm_oranlar_hesaplanir():
    periods = dort_ceyrek()
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["PE_TTM"] == pytest.approx(10000.0 / 40.0)
    assert r["PS_TTM"] == pytest.approx(10000.0 / 400.0)
    assert r["EV_EBIT_TTM"] is not None


def test_fiyat_yoksa_hepsi_none():
    periods = dort_ceyrek()
    r = _compute_val_ratios_for_row(periods, Q4, price=None)
    assert all(v is None for v in r.values())


def test_negatif_fiyat_none_uretir():
    periods = dort_ceyrek()
    r = _compute_val_ratios_for_row(periods, Q4, price=-5.0)
    assert all(v is None for v in r.values())


def test_shares_out_eksikse_none():
    periods = {Q4: row(Q4, shares=None)}
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["MARKET_CAP_PROXY"] is None


def test_negatif_net_income_pe_na_verir():
    periods = dort_ceyrek(ni=-10.0)
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["PE_TTM"] is None


def test_ev_proxy_negatifse_sifira_kirpilir():
    periods = dort_ceyrek(dst=0.0, dlt=0.0, cash=1000.0, sti=1000.0)
    r = _compute_val_ratios_for_row(periods, Q4, price=1.0)
    assert r["EV_PROXY"] == 0.0


def test_ev_ebit_ev_proxy_uzerinden_bagimli():
    periods = dort_ceyrek()
    for k in list(periods):
        eski = periods[k]
        periods[k] = ValFinancialRow(
            ticker=eski.ticker, period_end=eski.period_end,
            version_tag=eski.version_tag, published_at=eski.published_at,
            version_sequence=eski.version_sequence,
            source_disclosure_id=eski.source_disclosure_id,
            lineage_sha256=eski.lineage_sha256, revenue=eski.revenue,
            net_income=eski.net_income, total_equity=eski.total_equity,
            ebit=eski.ebit, debt_st=eski.debt_st, debt_lt=eski.debt_lt,
            cash_and_eq=None, st_investments=eski.st_investments,
            shares_out=eski.shares_out)
    r = _compute_val_ratios_for_row(periods, Q4, price=10.0)
    assert r["EV_PROXY"] is None
    assert r["EV_EBIT_TTM"] is None


def test_sum4q_ara_ceyrek_eksikse_none():
    from src.analytics.val_ratios_pit import _shift_quarter_end
    periods = dort_ceyrek()
    del periods[_shift_quarter_end(Q4, -2)]
    assert _sum4q(periods, Q4, "revenue") is None


def test_sum4q_tam_ise_dogru_toplar():
    periods = dort_ceyrek(rev=100.0)
    assert _sum4q(periods, Q4, "revenue") == 400.0


def test_resolve_t0_date_yerel_gun_sonraki_islem_gunu():
    trading_days = [date(2025, 12, 26), date(2025, 12, 29), date(2025, 12, 30)]
    pub = datetime(2025, 12, 26, 22, 0, tzinfo=TZ)
    t0 = resolve_t0_date(pub, trading_days)
    assert t0 in trading_days
    assert t0 >= date(2025, 12, 26)


def test_resolve_t0_date_utc_ile_istanbul_farkli_gun_verebilir():
    """
    next_trading_day() KESIN SONRAKI islem gununu doner (yayin gununun
    KENDISI islem gunu olsa bile dahil edilmez) -- fill_missing_t0_dates()
    ile AYNI, doğrulanmis semantik.
    """
    trading_days = [date(2025, 12, 29), date(2025, 12, 30)]
    pub_utc = datetime(2025, 12, 28, 22, 0, tzinfo=timezone.utc)  # Istanbul'da 29'a geçer
    t0 = resolve_t0_date(pub_utc, trading_days)
    assert t0 == date(2025, 12, 30)


def test_naive_analysis_at_reddedilir():
    from src.analytics.val_ratios_pit import _aware
    with pytest.raises(ValRatioPitError):
        _aware("analysis_at", datetime(2025, 12, 31))


def test_val_ratio_names_tam_alti():
    assert len(VAL_RATIO_NAMES) == 6
    assert set(VAL_RATIO_NAMES) == {
        "MARKET_CAP_PROXY", "PE_TTM", "PB", "PS_TTM", "EV_PROXY", "EV_EBIT_TTM"}
