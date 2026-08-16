"""
V21 Reconciliation-1 — saf hesaplama testleri.

Kullanicinin acikca istedigi kilit: gercek yeniden hesaplanan kumeden bir
ticker/modul CIKARILDIGINDA test KIRILMALI (MISSING yakalanmali); fazladan
ticker EKLENDIGINDE ayri sekilde KIRILMALI (UNEXPECTED yakalanmali). Ayrica
kismi kosu (INCOMPLETE) ve tekrar kosu (idempotency) ayri test edilir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.analytics.reconciliation_impact_orchestrator import (
    FINDING_MISSING,
    FINDING_STALE,
    FINDING_UNEXPECTED,
    RECONCILER_VERSION,
    RECONCILIATION_TYPE,
    STATUS_ERROR,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    ActualRow,
    ReconciliationError,
    reconcile_impact_vs_actual,
    reconciliation_sha256,
)

TZ = timezone(timedelta(hours=3))
KESIM = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
BASLA = KESIM
BITIR = KESIM + timedelta(seconds=5)
APP_ID = "APP-1"
PLAN_ID = "P" * 64


ORCH_ID = "ORCH-1"


def kos(expected, actual_rows, *, app_status="APPLIED", app_id=APP_ID,
        orch_id=ORCH_ID):
    return reconcile_impact_vs_actual(
        application_run_id=app_id, impact_plan_id=PLAN_ID, analysis_at=KESIM,
        started_at=BASLA, finished_at=BITIR, application_status=app_status,
        expected_tickers=expected, actual_rows=actual_rows,
        orchestrator_run_id=orch_id)


# ============================================================ HAPPY PATH
def test_beklenen_gercek_ayni_PASS():
    r = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID), ActualRow("BBB", ORCH_ID)])
    assert r.status == STATUS_PASS
    assert r.findings == ()
    assert r.missing() == () and r.unexpected() == () and r.stale() == ()


def test_bos_beklenen_bos_gercek_PASS():
    r = kos(set(), [])
    assert r.status == STATUS_PASS


# ============================================================ MISSING
def test_ciKARILAN_ticker_MISSING_yakalanir():
    """Gercekten yeniden hesaplanan kumeden bir ticker CIKARILDIGINDA."""
    r = kos({"AAA", "BBB", "CCC"}, [ActualRow("AAA", ORCH_ID), ActualRow("BBB", ORCH_ID)])
    assert r.status == STATUS_MISMATCH
    assert r.missing() == ("CCC",)
    assert r.unexpected() == () and r.stale() == ()


def test_coklu_missing():
    r = kos({"AAA", "BBB", "CCC"}, [])
    assert set(r.missing()) == {"AAA", "BBB", "CCC"}
    assert r.status == STATUS_MISMATCH


# ============================================================ UNEXPECTED
def test_FAZLADAN_ticker_UNEXPECTED_yakalanir():
    """Fazladan bir ticker EKLENDIGINDE ayri sekilde kirilir."""
    r = kos({"AAA"}, [ActualRow("AAA", ORCH_ID), ActualRow("ZZZ", ORCH_ID)])
    assert r.status == STATUS_MISMATCH
    assert r.unexpected() == ("ZZZ",)
    assert r.missing() == () and r.stale() == ()


def test_missing_ve_unexpected_ayni_anda():
    r = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID), ActualRow("ZZZ", ORCH_ID)])
    assert r.missing() == ("BBB",)
    assert r.unexpected() == ("ZZZ",)


# ============================================================ STALE
def test_baska_run_tarafindan_EZILEN_ticker_STALE():
    """
    Ticker beklenen VE gercek kumede, ama guncel satirin run_id'si BASKA
    bir kosuya ait -- bu attempt'in etkisi dogrulanamiyor.
    """
    r = kos({"AAA"}, [ActualRow("AAA", "BASKA-RUN-ID")])
    assert r.status == STATUS_MISMATCH
    assert r.stale() == ("AAA",)
    assert r.missing() == () and r.unexpected() == ()


def test_dogru_run_id_ile_STALE_olusmaz():
    r = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)])
    assert r.stale() == ()


def test_orchestrator_run_id_verilmezse_STALE_atlanir():
    """
    orchestrator_run_id bilinmiyorsa STALE kontrolu ATLANIR -- yanlislikla
    her seyi STALE ya da hicbir seyi STALE gostermez, sessizce dogru
    gorunmesin diye acikca isaretlenir (diagnostics uzerinden degil, kontrolun
    hic calismamasiyla).
    """
    r = kos({"AAA"}, [ActualRow("AAA", "HERHANGI-BIR-RUN-ID")], orch_id=None)
    assert r.stale() == ()
    assert r.status == STATUS_PASS


# ============================================================ stale_check_performed
def test_orch_id_verilince_stale_check_performed_True():
    r = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)])
    assert r.stale_check_performed is True


def test_orch_id_verilmeyince_stale_check_performed_False():
    """
    EN KRITIK GORUNURLUK SOZLESMESI: orchestrator_run_id verilmezse PASS
    donebilir (STALE hic aranmadigi icin), ama bu PASS'in "kontrol edilip
    temiz cikti" ile "kontrol hic calismadi" arasindaki farki GIZLEMEMESI
    gerekir. stale_check_performed=False bu farki gorunur kilar.
    """
    r = kos({"AAA"}, [ActualRow("AAA", "HERHANGI-BIR-RUN-ID")], orch_id=None)
    assert r.status == STATUS_PASS
    assert r.stale_check_performed is False, (
        "PASS uretiliyor ama STALE kontrolu hic calismadigi gorunmuyor")


def test_pending_kosuda_stale_check_performed_False():
    r = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)], app_status="PENDING")
    assert r.stale_check_performed is False


def test_stale_check_performed_icerik_ozetini_etkiler():
    """Bayrak degisirse SHA da degismeli; sessizce yutulmasin."""
    a = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)], orch_id=ORCH_ID)
    b = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)], orch_id=None)
    assert reconciliation_sha256(a) != reconciliation_sha256(b)


# ============================================================ INCOMPLETE
def test_pending_kosu_INCOMPLETE():
    """Kosu henuz bitmemisse MISMATCH DENMEZ; yargi ertelenir."""
    r = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID)], app_status="PENDING")
    assert r.status == STATUS_INCOMPLETE
    assert r.findings == ()
    assert r.status != STATUS_MISMATCH


def test_incomplete_expected_actual_yine_de_kaydeder():
    r = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID)], app_status="PENDING")
    assert set(r.expected) == {"AAA", "BBB"}
    assert set(r.actual) == {"AAA"}


# ============================================================ IDEMPOTENCY / KIMLIK
def test_ayni_girdi_ayni_kimlik():
    a = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)])
    b = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)])
    assert a.reconciliation_run_id == b.reconciliation_run_id
    assert reconciliation_sha256(a) == reconciliation_sha256(b)


def test_farkli_application_run_farkli_kimlik():
    a = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)], app_id="APP-1")
    b = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)], app_id="APP-2")
    assert a.reconciliation_run_id != b.reconciliation_run_id


def test_farkli_bulgu_farkli_sha():
    """
    Kimlik (run_id) yalniz (application_run, plan, analysis_at)'a baglidir;
    icerige DEGIL -- boylece ayni attempt'i iki kez calistirinca ayni
    kimlik cikar. Icerik farkliligini AYRI bir alan (sha256) yakalar.
    """
    a = kos({"AAA"}, [ActualRow("AAA", ORCH_ID)])
    b = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID)])
    assert a.reconciliation_run_id == b.reconciliation_run_id  # ayni attempt
    assert reconciliation_sha256(a) != reconciliation_sha256(b)  # farkli bulgu


def test_run_id_sira_bagimsiz():
    """Kume sirasi kimligi etkilemez -- kume esitligi onemlidir."""
    a = kos({"AAA", "BBB", "CCC"}, [ActualRow("CCC", ORCH_ID), ActualRow("AAA", ORCH_ID),
                                    ActualRow("BBB", ORCH_ID)])
    b = kos({"CCC", "BBB", "AAA"}, [ActualRow("AAA", ORCH_ID), ActualRow("BBB", ORCH_ID),
                                    ActualRow("CCC", ORCH_ID)])
    assert a.reconciliation_run_id == b.reconciliation_run_id
    assert reconciliation_sha256(a) == reconciliation_sha256(b)


# ============================================================ KIMLIK KUMESI, SAYI DEGIL
def test_ayni_BUYUKLUKTE_farkli_ticker_kumesi_yakalanir():
    """
    Karsilastirma SAYI uzerinden yapilirsa bu senaryo PASS gorunurdu:
    ikisi de 2 eleman. Kimlik kumesi uzerinden MISMATCH olmali.
    """
    r = kos({"AAA", "BBB"}, [ActualRow("AAA", ORCH_ID), ActualRow("CCC", ORCH_ID)])
    assert len(r.expected) == len(r.actual) == 2
    assert r.status == STATUS_MISMATCH
    assert set(r.missing()) == {"BBB"}
    assert set(r.unexpected()) == {"CCC"}


# ============================================================ GIRDI DOGRULAMA
def test_naive_analysis_at_reddedilir():
    with pytest.raises(ReconciliationError):
        reconcile_impact_vs_actual(
            application_run_id=APP_ID, impact_plan_id=PLAN_ID,
            analysis_at=datetime(2026, 3, 2, 20, 0), started_at=BASLA,
            finished_at=BITIR, application_status="APPLIED",
            expected_tickers=set(), actual_rows=[])


def test_finished_before_started_reddedilir():
    with pytest.raises(ReconciliationError):
        reconcile_impact_vs_actual(
            application_run_id=APP_ID, impact_plan_id=PLAN_ID, analysis_at=KESIM,
            started_at=BITIR, finished_at=BASLA, application_status="APPLIED",
            expected_tickers=set(), actual_rows=[])


def test_yinelenen_actual_ticker_reddedilir():
    with pytest.raises(ReconciliationError, match="yinelenen"):
        kos({"AAA"}, [ActualRow("AAA", ORCH_ID), ActualRow("aaa", ORCH_ID)])


def test_ticker_normalize_edilir():
    r = kos({" aaa "}, [ActualRow("AAA", ORCH_ID)])
    assert r.status == STATUS_PASS
    assert r.expected == ("AAA",)
