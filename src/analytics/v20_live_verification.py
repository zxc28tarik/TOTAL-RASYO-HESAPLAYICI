#!/usr/bin/env python3
"""
V20 CANLI POSTGRESQL UCTAN UCA DOGRULAMA.

Bellekteki Python nesnesine GUVENILMEZ: her kritik adimda baglanti KAPATILIR
ve sonuclar YENI BAGLANTIYLA geri okunur. V18'de "sayac dogru ama tablo bos"
hatasi tam olarak bu adim atlandigi icin uretime kadar gitmisti.

Kullanim: python3 -m src.analytics.v20_live_verification
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import psycopg2

from src.analytics.change_impact_bridge import (
    ModuleLineage,
    assert_no_plan_ticker_lost,
    bridge_targeted_tickers,
    evaluate_readiness,
)
from src.analytics.change_impact_detector import (
    DETECTOR_VERSION,
    EMPTY_DIAGNOSTIC_ONLY,
    EMPTY_NO_SCORING_DEPENDENCY,
    IMPACT_DIRECT,
    IMPACT_PEER,
    ChangeImpactError,
    FactChange,
    PeerCandidate,
    detect_change_impact,
)
from src.analytics.change_impact_periods import (
    affected_anchor_period_ends,
    shift_quarters,
)
from src.analytics.change_impact_persistence import (
    ImpactPlanConflict,
    persist_impact_plan,
    record_application_attempt,
)
from src.analytics.change_impact_registry import (
    DEPENDENCY_EDGES,
    REGISTRY_VERSION,
    registry_sha256,
)
from src.analytics.total_rasyo_combine import STATUS_INSUFFICIENT, STATUS_OK
from src.analytics.total_rasyo_orchestrator import run_total_rasyo_orchestrator
import src.analytics.total_rasyo_self_audit as V19

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
YAYIN = datetime(2026, 3, 1, 10, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 12, 0, tzinfo=TZ)
SURUM = "V2"

DSN = os.environ.get("TOTAL_RASYO_TEST_DSN") or os.environ.get("PGDATABASE")
RAPOR: list[tuple[str, bool, str]] = []


def yeni_baglanti():
    """HER ZAMAN yeni baglanti: commit edilmemis veri gorunmez."""
    if "=" in DSN or DSN.startswith("postgres"):
        return psycopg2.connect(DSN)
    return psycopg2.connect(dbname=DSN)


def kanit(ad: str, kosul: bool, ayrinti: str = "") -> None:
    RAPOR.append((ad, bool(kosul), ayrinti))
    print(f"  [{'OK  ' if kosul else 'FAIL'}] {ad}"
          + (f"  -- {ayrinti}" if ayrinti else ""))


def temizle():
    c = yeni_baglanti()
    with c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE analytics.reconciliation_finding,"
                        " analytics.reconciliation_run,"
                        " analytics.impact_application_target,"
                        " analytics.impact_application_run,"
                        " analytics.impact_plan_entry, analytics.impact_plan")
            cur.execute("TRUNCATE analytics.reconciliation_module_check,"
                        " analytics.reconciliation_module_run,"
                        " analytics.total_rasyo_module_input,"
                        " analytics.company_total_rasyo_result,"
                        " analytics.daily_engine_run, analytics.total_rasyo_run")
            cur.execute("TRUNCATE analytics.total_rasyo_restate_module_input,"
                        " analytics.company_total_rasyo_restate_result,"
                        " analytics.total_rasyo_restate_runs")
            cur.execute("TRUNCATE analytics.module_production_lineage")
            cur.execute("DELETE FROM analytics.module_scores")
    c.close()


def oku(sorgu, params=()):
    c = yeni_baglanti()
    try:
        with c.cursor() as cur:
            cur.execute(sorgu, params)
            return cur.fetchall()
    finally:
        c.close()


def fact(ticker="GARFA", *, engine="FINANCIAL", statement="BALANCE_SHEET",
         key="total_equity", period=date(2025, 12, 31), old=1000.0, new=800.0,
         surum=SURUM):
    return FactChange(ticker=ticker, statement_type=statement, fact_key=key,
                      period_end=period, old_value=old, new_value=new,
                      published_at=YAYIN, source_fact_id="F1",
                      source_statement_id="S1", source_version_id=surum,
                      routed_engine=engine)


def meta(c):
    return dict(direct_ticker=c.ticker, source_fact_id=c.source_fact_id,
                source_statement_id=c.source_statement_id,
                source_version_id=c.source_version_id,
                statement_type=c.statement_type, fact_key=c.fact_key,
                changed_period_end=c.period_end, published_at=c.published_at)


def soy(ticker, modul, *, surum=SURUM, uretim=URETIM):
    return ModuleLineage(ticker, modul, None, surum, uretim, "V1", 1)


def lineage_yaz(conn, kayitlar):
    with conn:
        with conn.cursor() as cur:
            for k in kayitlar:
                cur.execute("""
                    INSERT INTO analytics.module_production_lineage
                    (ticker, module, analysis_at, source_version_id,
                     produced_at, calculation_profile, calculation_version)
                    VALUES (%s,%s,%s,%s,%s,'V1',1)
                    ON CONFLICT (ticker, module, analysis_at) DO UPDATE
                    SET source_version_id = EXCLUDED.source_version_id,
                        produced_at = EXCLUDED.produced_at
                """, (k.ticker, k.module, KESIM, k.source_version_id,
                      k.produced_at))


def tam_soy(plan):
    out = []
    for t in plan.targeted_tickers():
        mods = {e.module for e in plan.entries if e.impacted_ticker == t}
        if mods & {"M1", "Ek1", "GOOD_COUNT"}:
            mods |= {"M1", "Ek1", "GOOD_COUNT"}
        out += [soy(t, m) for m in sorted(mods)]
    return out


def modul_satiri(conn, ticker, **kw):
    d = {"M1": 0.62, "M3": 0.71, "Ek4": 0.55, "Ek1": 0.48, "Ek9": 0.33}
    good = kw.pop("good", 9)
    d.update(kw)
    V19.modul_satiri(conn, ticker, asof=date(2026, 3, 2),
                     analysis_at=KESIM - timedelta(hours=1), degerler=d,
                     good=good)


def kos(conn, routing, m2_map, hedefler, run_id):
    runners = {}
    for aile in set(routing.values()):
        runners[aile] = V19.motor({t: V19.m2cikti(m2_map[t])
                                   for t, a in routing.items()
                                   if a == aile and t in m2_map})
    return run_total_rasyo_orchestrator(
        conn, analysis_at=KESIM, routing=routing, engine_runners=runners,
        run_id=run_id, targeted_tickers=list(hedefler) or None)


def sonuc(ticker):
    r = oku("SELECT total_rasyo_status, final_score, decision FROM analytics"
            ".company_total_rasyo_result WHERE ticker=%s AND analysis_at=%s",
            (ticker, KESIM))
    return r[0] if r else None


# ==================================================================== 2
def adim2_gercek_zincir():
    print("\n[2] GERCEK CHANGE-IMPACT ZINCIRI (baglanti kapatilip yeniden okunur)")
    temizle()
    conn = yeni_baglanti()
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA")
    kos(conn, routing, {"GARFA": 0.80}, [], "LIVE-2-once")

    c = fact()
    plan = detect_change_impact(c, impact_run_id="LIVE-2", analysis_at=KESIM)
    persist_impact_plan(conn, plan, meta(c))
    lineage_yaz(conn, tam_soy(plan))
    rapor = evaluate_readiness(plan, tam_soy(plan),
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    hedefler = bridge_targeted_tickers(rapor)
    assert_no_plan_ticker_lost(plan, rapor, hedefler)
    app_no = record_application_attempt(
        conn, impact_plan_id=plan.impact_plan_id,
        application_run_id="LIVE-APP-1", started_at=KESIM, status="APPLIED",
        finished_at=KESIM, orchestrator_run_id="LIVE-2-sonra",
        targeted_ticker_count=len(hedefler))
    kos(conn, routing, {"GARFA": 0.40}, hedefler, "LIVE-2-sonra")

    conn.close()  # >>> BAGLANTI KAPATILDI <<<

    kanit("impact plan commit edildi",
          oku("SELECT count(*) FROM analytics.impact_plan WHERE impact_plan_id=%s",
              (plan.impact_plan_id,))[0][0] == 1)
    kanit("plan entry'leri commit edildi",
          oku("SELECT count(*) FROM analytics.impact_plan_entry "
              "WHERE impact_plan_id=%s", (plan.impact_plan_id,))[0][0]
          == len(plan.entries), f"{len(plan.entries)} kayit")
    kanit("application run commit edildi",
          oku("SELECT attempt_no, status FROM analytics.impact_application_run "
              "WHERE impact_plan_id=%s", (plan.impact_plan_id,))
          == [(app_no, "APPLIED")])
    kanit("lineage commit edildi",
          oku("SELECT count(*) FROM analytics.module_production_lineage")[0][0]
          == len(tam_soy(plan)))
    s = sonuc("GARFA")
    kanit("Total Rasyo sonucu commit edildi", s is not None and s[0] == STATUS_OK)
    zincir = oku("""SELECT p.impact_plan_id, p.plan_sha256, a.orchestrator_run_id,
                    r.run_id FROM analytics.impact_plan p
                    JOIN analytics.impact_application_run a
                      ON a.impact_plan_id = p.impact_plan_id
                    JOIN analytics.total_rasyo_run r
                      ON r.run_id = a.orchestrator_run_id""")
    kanit("plan -> application -> orchestrator zinciri ID ile izlenebiliyor",
          len(zincir) == 1 and zincir[0][1] == plan.plan_sha256(),
          f"plan={zincir[0][0][:12]} orch={zincir[0][3]}" if zincir else "zincir yok")


# ==================================================================== 3
def adim3_otoritatif_degisim():
    print("\n[3] OTORITATIF DEGISIM")
    temizle()
    conn = yeni_baglanti()
    routing = {"GARFA": "FINANCIAL"}
    modul_satiri(conn, "GARFA")
    kos(conn, routing, {"GARFA": 0.80}, [], "LIVE-3-a")
    conn.close()
    once = sonuc("GARFA")
    kanit("baslangic OK", once[0] == STATUS_OK and once[1] is not None)

    conn = yeni_baglanti()
    kos(conn, routing, {}, ["GARFA"], "LIVE-3-b")  # motor M2 uretmiyor
    conn.close()
    sonra = sonuc("GARFA")
    kanit("basari -> yetersiz otoritatif", sonra[0] == STATUS_INSUFFICIENT)
    kanit("eski basarili skora FALLBACK yok", sonra[1] is None)
    kanit("eski karar temizlendi", sonra[2] is None)

    conn = yeni_baglanti()
    kos(conn, routing, {"GARFA": 0.85}, ["GARFA"], "LIVE-3-c")
    conn.close()
    geri = sonuc("GARFA")
    kanit("yetersiz -> basari otoritatif",
          geri[0] == STATUS_OK and geri[1] is not None)
    kanit("eski ret nedeni temizlendi",
          oku("SELECT rejection_reason FROM analytics.company_total_rasyo_result"
              " WHERE ticker='GARFA'")[0][0] is None)


# ==================================================================== 4
def adim4_peer_canli():
    print("\n[4] PEER PROPAGATION (canli)")
    temizle()
    conn = yeni_baglanti()
    routing = {"GARFA": "FINANCIAL", "PEER1": "FINANCIAL", "PEER2": "FINANCIAL"}
    for t in routing:
        modul_satiri(conn, t)
    kos(conn, routing, {t: 0.80 for t in routing}, [], "LIVE-4-once")

    c = fact()
    plan = detect_change_impact(
        c, impact_run_id="LIVE-4", analysis_at=KESIM,
        peer_candidates={"FINANCIAL": [
            PeerCandidate("GARFA", True, False, 1.2, 0.9),   # havuzdan CIKIS
            PeerCandidate("PEER1", True, True, 1.0, 1.0),
            PeerCandidate("PEER2", True, True, 1.5, 1.5)]},
        minimum_peer_count={"FINANCIAL": 2})
    persist_impact_plan(conn, plan, meta(c))
    conn.close()

    satirlar = oku("SELECT impacted_ticker, impact_type, module, actual_effects"
                   " FROM analytics.impact_plan_entry WHERE impact_plan_id=%s",
                   (plan.impact_plan_id,))
    direct = {r[0] for r in satirlar if r[1] == IMPACT_DIRECT}
    peer = {r[0] for r in satirlar if r[1] == IMPACT_PEER}
    kanit("A DIRECT", "GARFA" in direct)
    kanit("B/C PEER_PROPAGATED", peer == {"PEER1", "PEER2"}, str(sorted(peer)))
    kanit("A kendisine PEER_PROPAGATED ALMIYOR", "GARFA" not in peer)
    etkiler = {e for r in satirlar if r[1] == IMPACT_PEER for e in r[3]}
    kanit("ELIGIBILITY_EXIT canli gorunuyor", "ELIGIBILITY_EXIT" in etkiler)
    kanit("MIN_PEER_COUNT_CROSSING canli gorunuyor",
          "MIN_PEER_COUNT_CROSSING" in etkiler, str(sorted(etkiler)))

    rapor = evaluate_readiness(plan, tam_soy(plan),
                               expected_source_version_id=SURUM,
                               change_published_at=YAYIN)
    kanit("peer hedefte yalniz M2 isteniyor",
          set(rapor.per_ticker["PEER1"].required_modules) == {"M2"},
          str(sorted(rapor.per_ticker["PEER1"].required_modules)))
    kanit("peer hedefte M1/Ek1/GOOD_COUNT refresh EDILMIYOR",
          not ({"M1", "Ek1", "GOOD_COUNT"}
               & set(rapor.per_ticker["PEER1"].required_modules)))


# ==================================================================== 5
def adim5_zaman_sinirlari():
    print("\n[5] 4Q / 8Q ZAMAN SINIRI")
    Q = date(2025, 3, 31)
    dort = affected_anchor_period_ends(Q, affected_anchor_count=4,
                                       max_forward_period_offset=3)
    kanit("TTM_4Q tam 4 anchor", len(dort) == 4, str([d.isoformat() for d in dort]))
    kanit("Q+4 ETKILENMEZ", shift_quarters(Q, 4) not in dort,
          f"Q+4={shift_quarters(Q, 4)}")
    sekiz = affected_anchor_period_ends(Q, affected_anchor_count=8,
                                        max_forward_period_offset=7)
    kanit("SERIES_8Q tam 8 anchor", len(sekiz) == 8)
    kanit("Q+8 ETKILENMEZ", shift_quarters(Q, 8) not in sekiz,
          f"Q+8={shift_quarters(Q, 8)}")
    kanit("takvim gunu DEGIL kanonik ceyrek ordinali",
          shift_quarters(date(2025, 3, 31), 1) == date(2025, 6, 30)
          and shift_quarters(date(2024, 3, 31), 1) == date(2024, 6, 30),
          "2025Q1+1=2025-06-30 (90 gun eklense 06-29 olurdu)")

    c = fact(statement="INCOME_STATEMENT", key="net_income", period=Q)
    plan = detect_change_impact(c, impact_run_id="LIVE-5", analysis_at=KESIM)
    ttm = [e for e in plan.entries if "TTM_SUM_4Q" in e.reason_code]
    s8 = [e for e in plan.entries if "PERIOD_8Q" in e.reason_code]
    kanit("planda TTM kenari 4 anchor",
          bool(ttm) and all(len(e.affected_anchor_period_ends) == 4 for e in ttm))
    kanit("planda 8Q kenari 8 anchor",
          bool(s8) and all(len(e.affected_anchor_period_ends) == 8 for e in s8))


# ==================================================================== 6
def adim6_pit_restate():
    print("\n[6] PIT / RESTATE CANLI AYRIMI")
    temizle()
    conn = yeni_baglanti()
    modul_satiri(conn, "GARFA")
    kos(conn, {"GARFA": "FINANCIAL"}, {"GARFA": 0.80}, [], "LIVE-6")
    conn.close()
    pit_once = oku("SELECT * FROM analytics.company_total_rasyo_result"
                   " WHERE ticker='GARFA'")[0]

    conn = yeni_baglanti()
    cutoffs = [KESIM + timedelta(days=30), KESIM + timedelta(days=90)]
    for i, cutoff in enumerate(cutoffs):
        rid = f"{i:064d}".replace("0", "a", 1) if False else ("%064x" % (i + 1))
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO analytics.total_rasyo_restate_runs
                    (restate_run_id, target_analysis_at, knowledge_cutoff_at,
                     started_at, finished_at, status, restate_contract_version,
                     reader_version, inputs_sha256, results_sha256,
                     registry_version, registry_sha256, detector_version,
                     calculation_profile, calculation_version, company_count,
                     successful_company_count, diagnostics)
                    VALUES (%s,%s,%s,%s,%s,'COMPLETE',1,1,%s,%s,%s,%s,%s,'V1',1,1,0,'{}')
                """, (rid, KESIM, cutoff, cutoff, cutoff, "b" * 64, "c" * 64,
                      REGISTRY_VERSION, registry_sha256(), DETECTOR_VERSION))
                cur.execute("""
                    INSERT INTO analytics.company_total_rasyo_restate_result
                    (restate_run_id, ticker, target_analysis_at,
                     knowledge_cutoff_at, engine_family, m2_missing, m1_missing,
                     m3_missing, ek4_missing, ek1_missing, ek9_missing,
                     good_count_missing, total_rasyo_status, rejection_reason,
                     insufficiency_reason, diagnostics)
                    VALUES (%s,'GARFA',%s,%s,'FINANCIAL',false,false,false,
                            false,false,false,false,'YETERSIZ_VERI','x',
                            'EKSIK_BILESEN','{}')
                """, (rid, KESIM, cutoff))
    conn.close()

    pit_sonra = oku("SELECT * FROM analytics.company_total_rasyo_result"
                    " WHERE ticker='GARFA'")[0]
    kanit("PIT satiri DEGER OLARAK degismedi", pit_once == pit_sonra)
    kanit("restate AYRI tabloda",
          oku("SELECT count(*) FROM analytics"
              ".company_total_rasyo_restate_result")[0][0] == 2)
    kanit("iki farkli cutoff iki ayri restate run",
          oku("SELECT count(DISTINCT restate_run_id) FROM analytics"
              ".total_rasyo_restate_runs")[0][0] == 2)
    kanit("target != cutoff semantigi",
          oku("SELECT count(*) FROM analytics.total_rasyo_restate_runs"
              " WHERE knowledge_cutoff_at > target_analysis_at")[0][0] == 2)
    kanit("V19 latest_* view'i restate GORMUYOR",
          oku("SELECT count(*) FROM analytics.latest_total_rasyo_result")[0][0] == 1)

    # cutoff sonrasi kaynak sizamaz
    conn = yeni_baglanti()
    sizdi = False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE analytics.company_total_rasyo_restate_result
                    SET m1_source_at = knowledge_cutoff_at + interval '1 day'
                """)
        sizdi = True
    except psycopg2.Error:
        pass
    conn.close()
    kanit("cutoff SONRASI kaynak restate'e sizamaz", not sizdi)


# ==================================================================== 7
def adim7_plan_idempotency():
    print("\n[7] IMPACT PLAN IDEMPOTENCY")
    temizle()
    conn = yeni_baglanti()
    c = fact()
    plan = detect_change_impact(c, impact_run_id="LIVE-7", analysis_at=KESIM)
    persist_impact_plan(conn, plan, meta(c))
    once = oku("SELECT created_at, plan_sha256 FROM analytics.impact_plan")[0]
    import time
    time.sleep(0.05)
    ikinci = persist_impact_plan(conn, plan, meta(c))
    conn.close()
    sonra = oku("SELECT created_at, plan_sha256 FROM analytics.impact_plan")[0]

    kanit("satir sayisi ARTMADI",
          oku("SELECT count(*) FROM analytics.impact_plan")[0][0] == 1)
    kanit("created_at DEGISMEDI", once[0] == sonra[0])
    kanit("plan SHA ayni", once[1] == sonra[1] == plan.plan_sha256())
    kanit("idempotent basari bildirildi", ikinci["created"] is False)

    conn = yeni_baglanti()
    catisma = False
    if len(plan.entries) > 1:
        bozuk = replace(plan, entries=plan.entries[:1])
        try:
            persist_impact_plan(conn, bozuk, meta(c))
        except ImpactPlanConflict:
            catisma = True
    conn.close()
    kanit("ayni kimlik + farkli icerik -> ImpactPlanConflict", catisma)
    kanit("catisma sonrasi eski plan DEGISMEDI",
          oku("SELECT created_at, plan_sha256 FROM analytics.impact_plan")[0]
          == once)
    kanit("catisma sonrasi entry sayisi degismedi",
          oku("SELECT count(*) FROM analytics.impact_plan_entry")[0][0]
          == len(plan.entries))


# ==================================================================== 8
def adim8_registry():
    print("\n[8] REGISTRY INTEGRITY")
    toplam = len(DEPENDENCY_EDGES)
    tetikleyen = sum(1 for e in DEPENDENCY_EDGES if e.v20_triggers)
    sha = registry_sha256()
    print(f"       registry_version = {REGISTRY_VERSION}")
    print(f"       registry_sha256  = {sha}")
    print(f"       toplam edge      = {toplam}")
    print(f"       V20 trigger edge = {tetikleyen}")
    kanit("registry SHA 64 haneli hex", len(sha) == 64)
    kanit("edge sayisi HEAD'de olculdu", toplam > 0, f"{toplam} edge")
    kanit("HOLDING/GYO V20 trigger edge = 0",
          sum(1 for e in DEPENDENCY_EDGES
              if e.engine_family in ("HOLDING", "GYO") and e.v20_triggers) == 0)
    kanit("CASH_FLOW scoring edge = 0",
          sum(1 for e in DEPENDENCY_EDGES
              if e.statement_type == "CASH_FLOW") == 0)

    # SHA uyusmazliginda plan kimligi DEGISIR -> eski kayitla catisir.
    conn = yeni_baglanti()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT registry_sha256 FROM analytics.impact_plan LIMIT 1")
            kayitli = cur.fetchone()
    conn.close()
    kanit("plan kaydi registry SHA tasiyor",
          kayitli is not None and kayitli[0] == sha)
    return {"registry_version": REGISTRY_VERSION, "registry_sha256": sha,
            "total_edges": toplam, "v20_trigger_edges": tetikleyen}


# ==================================================================== 9
def adim9_cash_flow():
    print("\n[9] CASH_FLOW vs KAPSAM DISI KAYNAK")
    cf = detect_change_impact(fact(statement="CASH_FLOW",
                                   key="operating_cash_flow"),
                              impact_run_id="LIVE-9", analysis_at=KESIM)
    kanit("CASH_FLOW statement type KABUL edildi", True, "plan uretildi")
    kanit("CASH_FLOW scoring entry uretmedi", len(cf.entries) == 0)
    kanit("CASH_FLOW neden kodu NO_SCORING_DEPENDENCY",
          cf.diagnostics.get("empty_reason") == EMPTY_NO_SCORING_DEPENDENCY,
          str(cf.diagnostics.get("empty_reason")))

    tanisal = detect_change_impact(fact(key="provisions"),
                                   impact_run_id="LIVE-9b", analysis_at=KESIM)
    kanit("tanisal degisiklik AYRI neden kodu",
          tanisal.diagnostics.get("empty_reason") == EMPTY_DIAGNOSTIC_ONLY,
          str(tanisal.diagnostics.get("empty_reason")))

    reddedildi = []
    for kaynak in ("MARKET_PRICE", "NAV_REPORT"):
        try:
            detect_change_impact(
                FactChange(ticker="AGYO", statement_type=kaynak,
                           fact_key="current_price", period_end=date(2025, 12, 31),
                           old_value=1.0, new_value=2.0, published_at=YAYIN,
                           source_fact_id="F", source_statement_id="S",
                           source_version_id=SURUM, routed_engine="GYO"),
                impact_run_id="LIVE-9c")
        except ChangeImpactError:
            reddedildi.append(kaynak)
    kanit("MARKET_PRICE / NAV kapsam DISI (plan bile uretilmez)",
          reddedildi == ["MARKET_PRICE", "NAV_REPORT"])
    kanit("iki neden BIRBIRINDEN AYRI",
          cf.diagnostics.get("empty_reason") != EMPTY_DIAGNOSTIC_ONLY
          and len(reddedildi) == 2)


# ==================================================================== 11
def adim11_rollback():
    print("\n[11] TRANSACTION / ROLLBACK")
    temizle()
    conn = yeni_baglanti()
    c = fact()
    plan = detect_change_impact(c, impact_run_id="LIVE-11", analysis_at=KESIM)
    # Plan yaziminin ortasinda kontrollu hata: gecersiz meta.
    bozuk_meta = meta(c)
    bozuk_meta["direct_ticker"] = "kucukharf"   # CHECK ihlali
    patladi = False
    try:
        persist_impact_plan(conn, plan, bozuk_meta)
    except psycopg2.Error:
        patladi = True
    conn.close()
    kanit("plan yazimi ortasinda hata -> istisna", patladi)
    kanit("yarim plan kaydi KALMADI",
          oku("SELECT count(*) FROM analytics.impact_plan")[0][0] == 0)
    kanit("yarim entry kaydi KALMADI",
          oku("SELECT count(*) FROM analytics.impact_plan_entry")[0][0] == 0)

    # Orkestratör kalicilik hatasi -> kanonik sonuc yarim kalmaz.
    conn = yeni_baglanti()
    modul_satiri(conn, "GARFA")
    r = kos(conn, {"GARFA": "FINANCIAL"}, {"GARFA": 0.80}, [], "LIVE-11-ok")
    conn.close()
    kanit("saglikli kosu commit edildi", sonuc("GARFA") is not None)

    conn = yeni_baglanti()
    ilk = sonuc("GARFA")
    hata = False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE analytics.company_total_rasyo_result"
                            " SET total_rasyo_status='BOYLE_DURUM_YOK'")
    except psycopg2.Error:
        hata = True
    conn.close()
    kanit("gecersiz kanonik durum reddedildi", hata)
    kanit("kanonik sonuc rollback sonrasi DEGISMEDI", sonuc("GARFA") == ilk)


def main() -> int:
    if not DSN:
        print("ATLANDI: PGDATABASE / TOTAL_RASYO_TEST_DSN yok.")
        return 2
    print("=" * 66)
    print("V20 CANLI POSTGRESQL UCTAN UCA DOGRULAMA")
    print("=" * 66)
    ozet = {}
    adim2_gercek_zincir()
    adim3_otoritatif_degisim()
    adim4_peer_canli()
    adim5_zaman_sinirlari()
    adim6_pit_restate()
    adim7_plan_idempotency()
    ozet["registry"] = adim8_registry()
    adim9_cash_flow()
    adim11_rollback()

    gecti = sum(1 for _, ok, _ in RAPOR if ok)
    print("\n" + "-" * 66)
    print(f"TOPLAM KANIT: {gecti} / {len(RAPOR)}")
    if gecti != len(RAPOR):
        print("\nBASARISIZ:")
        for ad, ok, ayrinti in RAPOR:
            if not ok:
                print(f"  - {ad} {ayrinti}")
    with open("V20_LIVE_VERIFICATION.json", "w", encoding="utf-8") as fh:
        json.dump({"total": len(RAPOR), "passed": gecti,
                   "checks": [{"name": a, "ok": o, "detail": d}
                              for a, o, d in RAPOR], **ozet},
                  fh, ensure_ascii=False, indent=2)
    print("JSON: V20_LIVE_VERIFICATION.json")
    return 0 if gecti == len(RAPOR) else 1


if __name__ == "__main__":
    sys.exit(main())
