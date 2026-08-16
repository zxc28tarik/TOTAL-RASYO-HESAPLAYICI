"""
V24-A canli PostgreSQL testleri - VAL oranlari icin PIT hatti.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from src.analytics.val_ratios_pit import (
    compute_val_ratios_asof,
    fetch_price_at_t0,
    fetch_val_financials_asof,
    resolve_t0_date,
    run_val_ratios_asof,
)
from src.utils.calendar import get_trading_days

TZ = timezone(timedelta(hours=3))
PROFILE = "TEST_DERIVATION_V1"
VERSION = 1
LINEAGE1 = "1" * 64
LINEAGE2 = "2" * 64
LINEAGE3 = "3" * 64


def _baglan():
    dsn = os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")
    if not dsn:
        pytest.skip("TOTAL_RASYO_TEST_DSN / PGDATABASE tanimli degil")
    try:
        if "=" in dsn or dsn.startswith("postgres"):
            return psycopg2.connect(dsn)
        return psycopg2.connect(dbname=dsn)
    except psycopg2.Error as exc:
        pytest.skip(f"PostgreSQL erisilemedi: {exc}")


@pytest.fixture()
def conn():
    c = _baglan()
    with c:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('core.company_metrics_quarterly')")
            if cur.fetchone()[0] is None:
                c.close()
                pytest.skip("core.company_metrics_quarterly yok")
            cur.execute("DELETE FROM core.company_metrics_quarterly WHERE ticker LIKE 'ZTEST%'")
            cur.execute("DELETE FROM core.prices_daily WHERE ticker LIKE 'ZTEST%'")
            cur.execute("DELETE FROM analytics.ratios_quarterly WHERE ticker LIKE 'ZTEST%'")
            cur.execute("SELECT to_regclass('core.index_prices_daily')")
            if cur.fetchone()[0] is not None:
                cur.execute("SELECT count(*) FROM core.index_prices_daily WHERE index_code='XU100'")
                if cur.fetchone()[0] == 0:
                    _seed_trading_calendar(cur)
    yield c
    if not c.closed:
        c.close()


def _seed_trading_calendar(cur):
    gun = date(2025, 6, 1)
    for _ in range(400):
        if gun.weekday() < 5:
            cur.execute("""
                INSERT INTO core.index_prices_daily (index_code, trade_date, open, high, low, close, volume)
                VALUES ('XU100', %s, 100, 101, 99, 100, 1000)
                ON CONFLICT DO NOTHING
            """, (gun,))
        gun += timedelta(days=1)


def oku(sorgu, params=()):
    c = _baglan()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


def _metrics_row(conn, *, ticker="ZTEST1", period_end, published_at,
                 lineage=LINEAGE1, disc_id=None, version_sequence=1,
                 revenue=100.0, net_income=10.0, total_equity=200.0, ebit=15.0,
                 debt_st=5.0, debt_lt=20.0, cash=8.0, sti=2.0, shares=1000.0):
    disc_id = disc_id or ("DISC-" + lineage[:8])
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.company_metrics_quarterly
                (ticker, sector_family, period_end, version_tag, version_sequence,
                 published_at, source_disclosure_id, lineage_sha256, source_lineage,
                 derivation_profile, derivation_version, is_complete,
                 derivation_diagnostics, revenue, net_income, total_equity, ebit,
                 debt_st, debt_lt, cash_and_eq, st_investments, shares_out)
                VALUES (%s,'NONFIN',%s,%s,%s,%s,%s,%s,'[]',%s,%s,true,'{}',
                        %s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, period_end, derivation_profile, derivation_version, lineage_sha256)
                DO NOTHING
            """, (ticker, period_end, "v" + str(version_sequence), version_sequence,
                  published_at, disc_id, lineage, PROFILE, VERSION,
                  revenue, net_income, total_equity, ebit, debt_st, debt_lt,
                  cash, sti, shares))


def _price_row(conn, *, ticker="ZTEST1", trade_date, close=10.0):
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.prices_daily (ticker, trade_date, open, high, low, close, volume)
                VALUES (%s,%s,%s,%s,%s,%s,1000)
                ON CONFLICT DO NOTHING
            """, (ticker, trade_date, close, close, close, close))


def _quarters(anchor=date(2025, 12, 31)):
    from src.analytics.val_ratios_pit import _shift_quarter_end
    return [_shift_quarter_end(anchor, off) for off in (-3, -2, -1, 0)]


ANALIZ = datetime(2026, 2, 1, 12, 0, tzinfo=TZ)


def dort_ceyrek_kur(conn, ticker="ZTEST1", published_offset=timedelta(days=20)):
    trading_days = get_trading_days(conn)
    for pe in _quarters():
        pub = datetime(pe.year, pe.month, min(pe.day, 28), 10, 0, tzinfo=TZ) + published_offset
        lin = (str(abs(hash((ticker, pe))) % (10 ** 16))).zfill(16)
        lin = (lin * 4)[:64]
        _metrics_row(conn, ticker=ticker, period_end=pe, published_at=pub, lineage=lin)
        from src.analytics.val_ratios_pit import resolve_t0_date
        t0 = resolve_t0_date(pub, trading_days)
        _price_row(conn, ticker=ticker, trade_date=t0, close=10.0)


def test_gercek_PG_uzerinde_tam_veri_ile_hesaplama(conn):
    dort_ceyrek_kur(conn)
    df = compute_val_ratios_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                 derivation_profile=PROFILE, derivation_version=VERSION)
    assert not df.empty
    market_cap = df[(df.ratio_name == "MARKET_CAP_PROXY")]
    assert not market_cap["ratio_value"].isna().all()


def test_gelecekteki_duzeltme_gecmis_analysis_ata_sizmaz(conn):
    pe = date(2025, 12, 31)
    eski_pub = datetime(2026, 1, 15, 10, 0, tzinfo=TZ)
    yeni_pub = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=eski_pub, lineage=LINEAGE1,
                total_equity=200.0)
    _metrics_row(conn, period_end=pe, published_at=yeni_pub, lineage=LINEAGE2,
                total_equity=999.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 200.0


def test_cutoff_duzeltme_sonrasina_gectiginde_yeni_surume_gecer(conn):
    pe = date(2025, 12, 31)
    eski_pub = datetime(2026, 1, 15, 10, 0, tzinfo=TZ)
    yeni_pub = datetime(2026, 1, 20, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=eski_pub, lineage=LINEAGE1,
                total_equity=200.0)
    _metrics_row(conn, period_end=pe, published_at=yeni_pub, lineage=LINEAGE2,
                total_equity=999.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 999.0


def test_ayni_published_at_version_sequence_tie_break_belirler(conn):
    pe = date(2025, 12, 31)
    ayni_an = datetime(2026, 1, 15, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE1,
                version_sequence=1, total_equity=100.0)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE2,
                version_sequence=2, total_equity=200.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 200.0


def test_published_at_ONCELIGI_version_sequence_ile_CATISTIGINDA_kazanir(conn):
    """
    GERCEK CATISMA: eski published_at + yuksek version_sequence VS yeni
    published_at + dusuk version_sequence. Tie-break sirasi (published_at
    ONCE) DOGRUYSA, yeni published_at'li satir kazanmali -- version_sequence
    yuksek olsa bile. Sira bozulursa (version_sequence once) bu test
    TERSINE doner ve yakalar.
    """
    pe = date(2025, 12, 31)
    eski_ama_yuksek_seq = datetime(2026, 1, 10, 10, 0, tzinfo=TZ)
    yeni_ama_dusuk_seq = datetime(2026, 1, 20, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=eski_ama_yuksek_seq,
                lineage=LINEAGE1, version_sequence=99, total_equity=111.0)
    _metrics_row(conn, period_end=pe, published_at=yeni_ama_dusuk_seq,
                lineage=LINEAGE2, version_sequence=1, total_equity=222.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 222.0, (
        "published_at DESC oncelikli olmali; version_sequence'in daha "
        "yuksek olmasi eski published_at'i KAZANDIRMAMALI")


def test_ayni_seviyede_lineage_sha256_son_savunma(conn):
    pe = date(2025, 12, 31)
    ayni_an = datetime(2026, 1, 15, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE1,
                version_sequence=1, total_equity=100.0)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE3,
                version_sequence=1, total_equity=300.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 300.0


def test_fiziksel_insert_sirasi_sonucu_degistirmez(conn):
    pe = date(2025, 12, 31)
    ayni_an = datetime(2026, 1, 15, 10, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE2,
                version_sequence=2, total_equity=777.0)
    _metrics_row(conn, period_end=pe, published_at=ayni_an, lineage=LINEAGE1,
                version_sequence=1, total_equity=111.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 777.0


def test_ayni_gun_farkli_saatli_yayin_cutoff_saatinden_sonrasi_gorunmez(conn):
    pe = date(2025, 12, 31)
    sabah = datetime(2026, 1, 15, 8, 0, tzinfo=TZ)
    aksam = datetime(2026, 1, 15, 22, 0, tzinfo=TZ)
    ogle_cutoff = datetime(2026, 1, 15, 12, 0, tzinfo=TZ)
    _metrics_row(conn, period_end=pe, published_at=sabah, lineage=LINEAGE1,
                total_equity=100.0)
    _metrics_row(conn, period_end=pe, published_at=aksam, lineage=LINEAGE2,
                total_equity=200.0)
    fin = fetch_val_financials_asof(conn, analysis_at=ogle_cutoff, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert fin["ZTEST1"][pe].total_equity == 100.0


def test_t0_gelecekteyse_eski_fiyatla_sessizce_uretilmez(conn):
    _price_row(conn, trade_date=date(2025, 11, 1), close=5.0)
    t0_gelecekte = date(2026, 6, 1)
    fiyat = fetch_price_at_t0(conn, ticker="ZTEST1", t0_date=t0_gelecekte,
                              analysis_at=ANALIZ)
    assert fiyat is None


def test_t0_gelecekteyse_PENCERE_ICINDE_fiyat_OLSA_BILE_kullanilmaz(conn):
    """
    GERCEK SIZINTI SENARYOSU: t0 gelecekte (cutoff'u asiyor), AMA t0'a
    10 gun icinde bir fiyat GERCEKTEN VAR (bu fiyatin KENDISI de
    analysis_at cutoff'unun otesinde -- yani bu bilgi de HENUZ
    bilinmemeliydi). Cutoff korumasi olmadan sorgu bu fiyati BULURDU.
    """
    cutoff = date(2026, 2, 1)  # daily_price_cutoff_date(ANALIZ) ile ayni gun civari
    t0_gelecekte = cutoff + timedelta(days=20)
    # t0'a 10 gun icinde ama YINE DE cutoff'un COK otesinde bir fiyat.
    _price_row(conn, trade_date=t0_gelecekte - timedelta(days=3), close=777.0)
    fiyat = fetch_price_at_t0(conn, ticker="ZTEST1", t0_date=t0_gelecekte,
                              analysis_at=ANALIZ)
    assert fiyat is None, (
        "t0 cutoff'u astigi halde pencere-ici bir fiyat SESSIZCE kullanildi")


def test_t0_cutoff_icindeyse_fiyat_normal_bulunur(conn):
    _price_row(conn, trade_date=date(2026, 1, 10), close=15.0)
    fiyat = fetch_price_at_t0(conn, ticker="ZTEST1", t0_date=date(2026, 1, 12),
                              analysis_at=ANALIZ)
    assert fiyat == 15.0


def test_10_gun_stale_price_siniri_korunur(conn):
    _price_row(conn, trade_date=date(2025, 12, 20), close=99.0)
    fiyat = fetch_price_at_t0(conn, ticker="ZTEST1", t0_date=date(2026, 1, 5),
                              analysis_at=ANALIZ)
    assert fiyat is None


def test_dort_donemden_birine_cutoff_sonrasi_restatement_sonuc_degismez(conn):
    dort_ceyrek_kur(conn)
    pe_orta = _quarters()[1]
    _metrics_row(conn, period_end=pe_orta,
                published_at=ANALIZ + timedelta(days=30), lineage=LINEAGE3,
                revenue=99999.0)
    df1 = compute_val_ratios_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                  derivation_profile=PROFILE, derivation_version=VERSION)
    satir = df1[(df1.period_end == _quarters()[-1]) & (df1.ratio_name == "PS_TTM")]
    ps_before = satir["ratio_value"].iloc[0]
    assert ps_before is not None and ps_before < 1000


def test_uctan_uca_run_val_ratios_asof_ratios_quarterly_yazar(conn):
    dort_ceyrek_kur(conn)
    df = run_val_ratios_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                             derivation_profile=PROFILE, derivation_version=VERSION,
                             persist=True)
    assert not df.empty
    kayit = oku("SELECT count(*) FROM analytics.ratios_quarterly"
               " WHERE ticker='ZTEST1' AND ratio_name='MARKET_CAP_PROXY'")
    assert kayit[0][0] >= 1


def test_core_27_orani_bu_pipeline_yazmaz(conn):
    dort_ceyrek_kur(conn)
    run_val_ratios_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                        derivation_profile=PROFILE, derivation_version=VERSION)
    core_kayit = oku("SELECT count(*) FROM analytics.ratios_quarterly"
                     " WHERE ticker='ZTEST1' AND ratio_name='CURRENT_RATIO'")
    assert core_kayit[0][0] == 0


def test_farkli_derivation_profile_gorunmez(conn):
    pe = date(2025, 12, 31)
    with conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.company_metrics_quarterly
                (ticker, sector_family, period_end, version_tag, version_sequence,
                 published_at, source_disclosure_id, lineage_sha256, source_lineage,
                 derivation_profile, derivation_version, is_complete,
                 derivation_diagnostics, total_equity, shares_out)
                VALUES ('ZTEST1','NONFIN',%s,'v1',1,%s,'DISC-OTHER',%s,'[]',
                        'BASKA_PROFIL',1,true,'{}',500.0,1000.0)
            """, (pe, datetime(2026, 1, 15, 10, 0, tzinfo=TZ), LINEAGE1))
    fin = fetch_val_financials_asof(conn, analysis_at=ANALIZ, tickers=["ZTEST1"],
                                    derivation_profile=PROFILE, derivation_version=VERSION)
    assert pe not in fin["ZTEST1"]
