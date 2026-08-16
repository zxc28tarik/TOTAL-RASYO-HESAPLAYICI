"""
V24-A — Historical PIT Backtest Foundation: VAL oranlari icin PIT hatti.

SECENEK B (kilitlendi): core.financials_quarterly SEMASINA DOKUNULMADI,
yeni migration/backfill YOK. Kaynak core.company_metrics_quarterly --
CORE pipeline'in (company_ratio_pipeline.py) zaten guvendigi, published_at/
version_sequence/lineage tasiyan tablo. VAL ratios'un CORE'dan BILEREK
DISLANDIGI ("VAL ratios are deliberately excluded from this pipeline",
company_ratio_pipeline.py:88) tasarim tercihini SIMDI kapatiyoruz.

Bu modul CORE pipeline'a KARDESdir; CORE'un davranisini DEGISTIRMEZ,
company_ratio_pipeline.py/ratios_calc.py DOSYALARINA DOKUNMAZ.

ALTI ORAN: MARKET_CAP_PROXY, PE_TTM, PB, PS_TTM, EV_PROXY, EV_EBIT_TTM.

FINANSAL PIT SOZLESMESI:
  - analysis_at timezone-aware olmak ZORUNDA.
  - published_at <= analysis_at.
  - derivation_profile + derivation_version ACIKCA PINLENIR.
  - ticker+period_end basina TEK gorunur surum:
      published_at DESC, version_sequence DESC,
      source_disclosure_id DESC, lineage_sha256 DESC.

FIYAT SOZLESMESI:
  - CORE pipeline'daki PUBLISHED_AT_TRACE_ONLY t0 YAKLASIMI KULLANILMAZ.
  - Legacy VAL semantigi KORUNUR: publication'in Istanbul yerel GUNU ->
    next_trading_day() -> t0_date.
  - Fiyat lookup: trade_date <= t0_date, en fazla 10 gun geriye.
  - YENI KORUMA: t0_date, analysis_at'in market cutoff'unu ASIYORSA,
    o donemin VAL orani SESSIZCE eski bir fiyatla URETILMEZ -- NA olur.

QUARTER-SERIES: sum4q() gerektiren PE_TTM/PS_TTM/EV_EBIT_TTM icin, dort
ceyregin HER BIRI BAGIMSIZ olarak ayni PIT sozlesmesiyle secilir.

YAZMA HEDEFI: mevcut analytics.ratios_quarterly (CORE'un 27 oranini
YENIDEN HESAPLAMAZ/YAZMAZ; yalniz VAL 6 oranini uretir).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from src.analytics.bank_batch_pipeline import daily_price_cutoff_date
from src.analytics.ratios_calc import _quarter_end, _shift_quarter_end
from src.utils.calendar import get_trading_days, next_trading_day

ISTANBUL_TZ = timezone(timedelta(hours=3))

VAL_RATIO_NAMES: tuple[str, ...] = (
    "MARKET_CAP_PROXY", "PE_TTM", "PB", "PS_TTM", "EV_PROXY", "EV_EBIT_TTM",
)

REASON_NO_PRICE_WITHIN_CUTOFF = "PRICE_UNAVAILABLE_AT_CUTOFF"


class ValRatioPitError(ValueError):
    pass


@dataclass(frozen=True)
class ValFinancialRow:
    ticker: str
    period_end: date
    version_tag: str
    published_at: datetime
    version_sequence: int
    source_disclosure_id: Optional[str]
    lineage_sha256: Optional[str]
    revenue: Optional[float]
    net_income: Optional[float]
    total_equity: Optional[float]
    ebit: Optional[float]
    debt_st: Optional[float]
    debt_lt: Optional[float]
    cash_and_eq: Optional[float]
    st_investments: Optional[float]
    shares_out: Optional[float]


VAL_FINANCIALS_SQL = """
SELECT DISTINCT ON (ticker, period_end)
  ticker, period_end, version_tag, published_at, version_sequence,
  source_disclosure_id, lineage_sha256,
  revenue, net_income, total_equity, ebit,
  debt_st, debt_lt, cash_and_eq, st_investments, shares_out
FROM core.company_metrics_quarterly
WHERE ticker = ANY(%(tickers)s::text[])
  AND period_end BETWEEN %(min_period_end)s AND %(max_period_end)s
  AND published_at <= %(analysis_at)s
  AND derivation_profile = %(derivation_profile)s
  AND derivation_version = %(derivation_version)s
ORDER BY ticker, period_end,
         published_at DESC, version_sequence DESC,
         source_disclosure_id DESC NULLS LAST, lineage_sha256 DESC NULLS LAST
"""

PRICE_LOOKBACK_SQL = """
SELECT COALESCE(adj_close, close) AS px, trade_date
FROM core.prices_daily
WHERE ticker = %(ticker)s
  AND trade_date <= %(t0_date)s
  AND trade_date >= %(t0_date)s - INTERVAL '10 days'
ORDER BY trade_date DESC
LIMIT 1
"""


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValRatioPitError(f"{name} timezone bilgili olmali")
    return value


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    out = []
    for t in tickers:
        if not isinstance(t, str) or not t.strip():
            raise ValRatioPitError("ticker dolu metin olmali")
        kod = t.strip().upper()
        if kod not in out:
            out.append(kod)
    return sorted(out)


def fetch_val_financials_asof(
    conn: Any, *, analysis_at: datetime, tickers: Iterable[str],
    derivation_profile: str, derivation_version: int,
    lookback_quarters: int = 7,
) -> dict[str, dict[date, ValFinancialRow]]:
    """
    Her ticker icin, analysis_at aninda GORUNUR olan period_end -> satir
    esleyisini doner. lookback_quarters en az sum4q() icin gereken 4
    ceyregi kapsayacak kadar geriye gider (varsayilan 7: 4 sum4q + tampon).
    """
    analysis = _aware("analysis_at", analysis_at)
    ticker_list = _normalize_tickers(tickers)
    if not ticker_list:
        return {}
    if not isinstance(derivation_profile, str) or not derivation_profile.strip():
        raise ValRatioPitError("derivation_profile dolu metin olmali")
    if isinstance(derivation_version, bool) or not isinstance(derivation_version, int) \
            or derivation_version < 1:
        raise ValRatioPitError("derivation_version pozitif tam sayi olmali")

    context_asof = daily_price_cutoff_date(analysis)
    max_period_end = _quarter_end(context_asof)
    min_period_end = _shift_quarter_end(max_period_end, -lookback_quarters)

    with conn.cursor() as cur:
        cur.execute(VAL_FINANCIALS_SQL, {
            "tickers": ticker_list, "analysis_at": analysis,
            "min_period_end": min_period_end, "max_period_end": max_period_end,
            "derivation_profile": derivation_profile,
            "derivation_version": derivation_version,
        })
        rows = cur.fetchall()

    out: dict[str, dict[date, ValFinancialRow]] = {t: {} for t in ticker_list}
    for (ticker, pe, vt, pub, vseq, disc_id, lineage, rev, ni, teq, ebit,
         dst, dlt, cash, sti, shares) in rows:
        out[ticker][pe] = ValFinancialRow(
            ticker=ticker, period_end=pe, version_tag=vt, published_at=pub,
            version_sequence=vseq, source_disclosure_id=disc_id,
            lineage_sha256=lineage, revenue=rev, net_income=ni,
            total_equity=teq, ebit=ebit, debt_st=dst, debt_lt=dlt,
            cash_and_eq=cash, st_investments=sti, shares_out=shares)
    return out


def resolve_t0_date(published_at: datetime, trading_days: list[date]) -> date:
    """
    Legacy VAL semantigi: publication'in ISTANBUL YEREL GUNU ->
    next_trading_day(). CORE pipeline'daki PUBLISHED_AT_TRACE_ONLY
    yaklasimindan FARKLIDIR -- burada fiyat GERCEKTEN tuketilir.
    """
    yerel_gun = published_at.astimezone(ISTANBUL_TZ).date()
    return next_trading_day(yerel_gun, trading_days)


def fetch_price_at_t0(conn: Any, *, ticker: str, t0_date: date,
                      analysis_at: datetime) -> Optional[float]:
    """
    t0_date, analysis_at'in market cutoff'unu ASARSA (henuz o an
    BILINEMEYECEK bir gun ise) fiyat SESSIZCE eski bir gunle
    DOLDURULMAZ -- None doner (VAL orani NA olur).
    """
    cutoff = daily_price_cutoff_date(analysis_at)
    if t0_date > cutoff:
        return None
    with conn.cursor() as cur:
        cur.execute(PRICE_LOOKBACK_SQL, {"ticker": ticker, "t0_date": t0_date})
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _sum4q(periods: Mapping[date, ValFinancialRow], anchor: date,
          field: str) -> Optional[float]:
    toplam = 0.0
    for offset in (-3, -2, -1, 0):
        pe = _shift_quarter_end(anchor, offset)
        row = periods.get(pe)
        if row is None:
            return None
        deger = getattr(row, field)
        if deger is None:
            return None
        toplam += float(deger)
    return toplam


def _compute_val_ratios_for_row(
    periods: Mapping[date, ValFinancialRow], anchor: date, *, price: Optional[float],
) -> dict[str, Optional[float]]:
    row = periods[anchor]
    shares = row.shares_out
    sonuc: dict[str, Optional[float]] = {name: None for name in VAL_RATIO_NAMES}

    if price is None or price <= 0 or shares is None or shares <= 0:
        return sonuc

    market_cap = price * float(shares)
    sonuc["MARKET_CAP_PROXY"] = market_cap

    net_income_4q = _sum4q(periods, anchor, "net_income")
    if net_income_4q is not None and net_income_4q > 0:
        sonuc["PE_TTM"] = market_cap / net_income_4q

    if row.total_equity is not None and row.total_equity > 0:
        sonuc["PB"] = market_cap / float(row.total_equity)

    revenue_4q = _sum4q(periods, anchor, "revenue")
    if revenue_4q is not None and revenue_4q > 0:
        sonuc["PS_TTM"] = market_cap / revenue_4q

    debt_st = row.debt_st
    debt_lt = row.debt_lt
    cash = row.cash_and_eq
    sti = row.st_investments
    if (debt_st is not None or debt_lt is not None) and cash is not None and sti is not None:
        borc = float(debt_st or 0.0) + float(debt_lt or 0.0)
        ev = max(market_cap + borc - (float(cash) + float(sti)), 0.0)
        sonuc["EV_PROXY"] = ev
        ebit_4q = _sum4q(periods, anchor, "ebit")
        if ebit_4q is not None and ebit_4q > 0:
            sonuc["EV_EBIT_TTM"] = ev / ebit_4q

    return sonuc


def compute_val_ratios_asof(
    conn: Any, *, analysis_at: datetime, tickers: Iterable[str],
    derivation_profile: str, derivation_version: int,
) -> pd.DataFrame:
    """
    Donus: ticker, period_end, version_tag, ratio_name, ratio_value, is_na
    (analytics.ratios_quarterly semasiyla UYUMLU).
    """
    analysis = _aware("analysis_at", analysis_at)
    financials = fetch_val_financials_asof(
        conn, analysis_at=analysis, tickers=tickers,
        derivation_profile=derivation_profile, derivation_version=derivation_version)
    if not any(financials.values()):
        return pd.DataFrame(columns=["ticker", "period_end", "version_tag",
                                     "ratio_name", "ratio_value", "is_na"])

    trading_days = get_trading_days(conn)
    satirlar: list[dict] = []
    for ticker, periods in financials.items():
        for pe, row in sorted(periods.items()):
            t0 = resolve_t0_date(row.published_at, trading_days)
            fiyat = fetch_price_at_t0(conn, ticker=ticker, t0_date=t0,
                                      analysis_at=analysis)
            degerler = _compute_val_ratios_for_row(periods, pe, price=fiyat)
            for ratio_name, deger in degerler.items():
                satirlar.append({
                    "ticker": ticker, "period_end": pe, "version_tag": row.version_tag,
                    "ratio_name": ratio_name, "ratio_value": deger,
                    "is_na": deger is None,
                })
    return pd.DataFrame(satirlar, columns=["ticker", "period_end", "version_tag",
                                           "ratio_name", "ratio_value", "is_na"])


def run_val_ratios_asof(
    conn: Any, *, analysis_at: datetime, tickers: Iterable[str],
    derivation_profile: str, derivation_version: int, persist: bool = True,
) -> pd.DataFrame:
    """
    V24-A giris noktasi. analytics.ratios_quarterly'ye YALNIZ VAL 6
    oranini yazar; CORE'un 27 oranina DOKUNMAZ.
    """
    from src.db.bulk_upsert_ratios import upsert_ratios_copy

    df = compute_val_ratios_asof(
        conn, analysis_at=analysis_at, tickers=tickers,
        derivation_profile=derivation_profile, derivation_version=derivation_version)
    if persist and not df.empty:
        upsert_ratios_copy(conn, df)
    return df
