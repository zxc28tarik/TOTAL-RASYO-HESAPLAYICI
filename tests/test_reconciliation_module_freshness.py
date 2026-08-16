"""
V22-B saf hesaplayici testleri.

Kullanicinin acikca istedigi UC bagimsiz mutasyon kilidi:
  1. daha yeni modul satirini gormezden gelme (TOTAL_STALE)
  2. identity mismatch'i gormezden gelme (MODULE_LINEAGE_STALE)
  3. identity_known=false iken sahte lineage PASS uretme
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.analytics.reconciliation_module_freshness import (
    FINDING_LINEAGE_STALE,
    FINDING_MISSING,
    FINDING_TOTAL_STALE,
    REASON_IDENTITY_UNAVAILABLE,
    REASON_MODULE_MISSING,
    REASON_NO_BASELINE_CONTEXT,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    ConsumedModule,
    ModuleReconciliationError,
    ProducerSuccessor,
    reconcile_module_freshness,
    reconciliation_sha256,
)
from src.analytics.total_rasyo_score import MODULE_KEYS

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
BASLA = ANALIZ
BITIR = ANALIZ + timedelta(seconds=5)
KAYNAK = ANALIZ - timedelta(hours=1)


def temiz_modul(module, *, missing=False, analysis_at=KAYNAK,
                identity_known=True, source_run_key="RUN-1"):
    return ConsumedModule(
        module=module, missing=missing,
        source_at=None if missing else KAYNAK,
        analysis_at=None if missing else analysis_at,
        source_run_key=None if missing else source_run_key,
        identity_known=False if missing else identity_known)


def temiz_successor(module, *, newer=False, same_label_key="RUN-1",
                    freshness_available=True, lineage_available=True):
    return ProducerSuccessor(
        module=module, newer_eligible_exists=newer,
        same_label_source_run_key=same_label_key,
        freshness_available=freshness_available,
        lineage_lookup_available=lineage_available)


def tam_temiz_girdiler():
    consumed = {m: temiz_modul(m) for m in MODULE_KEYS}
    successors = {m: temiz_successor(m) for m in MODULE_KEYS}
    return consumed, successors


def kos(consumed=None, successors=None, evidence_available=True, **kw):
    if consumed is None or successors is None:
        c, s = tam_temiz_girdiler()
        consumed = consumed or c
        successors = successors or s
    args = dict(total_rasyo_run_id="RUN-A", ticker="GARFA", analysis_at=ANALIZ,
               started_at=BASLA, finished_at=BITIR, consumed_modules=consumed,
               successors=successors, evidence_available=evidence_available)
    args.update(kw)
    return reconcile_module_freshness(**args)


# ============================================================ HAPPY PATH
def test_tam_temiz_PASS_ve_fully_verified():
    r = kos()
    assert r.status == STATUS_PASS
    assert r.fully_verified is True
    assert r.missing_modules() == ()
    assert r.total_stale_modules() == ()
    assert r.lineage_stale_modules() == ()


# ============================================================ MISSING_MODULE
def test_eksik_modul_MISMATCH_verir():
    consumed, successors = tam_temiz_girdiler()
    consumed["M1"] = temiz_modul("M1", missing=True)
    r = kos(consumed, successors)
    assert r.status == STATUS_MISMATCH
    assert r.missing_modules() == ("M1",)
    assert r.checks["M1"].freshness_performed is False
    assert r.checks["M1"].freshness_reason == REASON_MODULE_MISSING
    assert r.checks["M1"].lineage_performed is False


def test_eksik_modulde_baska_kontrol_yapilmaz():
    consumed, successors = tam_temiz_girdiler()
    consumed["Ek1"] = temiz_modul("Ek1", missing=True)
    r = kos(consumed, successors)
    c = r.checks["Ek1"]
    assert c.total_stale is None and c.lineage_stale is None


# ============================================================ 1) TOTAL_STALE
def test_daha_yeni_modul_satiri_TOTAL_STALE_yakalanir():
    consumed, successors = tam_temiz_girdiler()
    successors["M1"] = temiz_successor("M1", newer=True)
    r = kos(consumed, successors)
    assert r.status == STATUS_MISMATCH
    assert "M1" in r.total_stale_modules()


def test_daha_yeni_satir_YOKSA_stale_degil():
    r = kos()
    assert r.checks["M1"].total_stale is False


def test_m2_zayif_proxy_ile_total_stale():
    """M2 icin de TOTAL_STALE tespit edilebilmeli (zayif proxy uzerinden)."""
    consumed, successors = tam_temiz_girdiler()
    successors["M2"] = temiz_successor("M2", newer=True, lineage_available=False)
    r = kos(consumed, successors)
    assert "M2" in r.total_stale_modules()


# ============================================================ 2) MODULE_LINEAGE_STALE
def test_identity_mismatch_LINEAGE_STALE_yakalanir():
    consumed, successors = tam_temiz_girdiler()
    successors["M3"] = temiz_successor("M3", same_label_key="FARKLI-RUN")
    r = kos(consumed, successors)
    assert r.status == STATUS_MISMATCH
    assert "M3" in r.lineage_stale_modules()


def test_identity_eslesirse_lineage_stale_degil():
    r = kos()
    assert r.checks["M3"].lineage_stale is False


def test_total_stale_ve_lineage_stale_BAGIMSIZ():
    """Ayni modulde biri true digeri false olabilir; birbirine karismaz."""
    consumed, successors = tam_temiz_girdiler()
    successors["Ek4"] = temiz_successor("Ek4", newer=True, same_label_key="RUN-1")
    r = kos(consumed, successors)
    assert r.checks["Ek4"].total_stale is True
    assert r.checks["Ek4"].lineage_stale is False


# ============================================================ 3) identity_known=false
def test_identity_known_false_lineage_HUKUM_VERMEZ():
    """
    KRITIK: identity_known=false olan modul ne temiz ne stale sayilir;
    lineage_performed=false ile kontrolun uygulanamadigi acikca gorunur.
    """
    consumed, successors = tam_temiz_girdiler()
    consumed["Ek9"] = temiz_modul("Ek9", identity_known=False, source_run_key=None)
    r = kos(consumed, successors)
    c = r.checks["Ek9"]
    assert c.lineage_performed is False
    assert c.lineage_reason == REASON_IDENTITY_UNAVAILABLE
    assert c.lineage_stale is None  # NE True NE False
    assert "Ek9" not in r.lineage_stale_modules()


def test_m2_lineage_HICBIR_ZAMAN_yapilmaz():
    r = kos()
    c = r.checks["M2"]
    assert c.lineage_performed is False
    assert c.lineage_reason == REASON_IDENTITY_UNAVAILABLE


def test_toplayicida_M2ye_yanlislikla_identity_known_True_verilse_bile_zorlanir():
    """
    MIMARI ZORUNLULUK: saf hesaplayici caginirin identity_known bayragina
    GUVENMEZ; M2 icin lineage'i KENDISI engeller. Toplayicida bir hata
    (M2'ye yanlislikla identity_known=True vermek) DB CHECK kisitina
    kadar sessiz kalmamali.
    """
    consumed, successors = tam_temiz_girdiler()
    consumed["M2"] = temiz_modul("M2", identity_known=True,
                                 source_run_key="YANLISLIKLA-VERILMIS")
    r = kos(consumed, successors)
    assert r.checks["M2"].lineage_performed is False
    assert r.checks["M2"].lineage_stale is None


def test_m2_ornek_senaryo_PASS_ama_fully_verified_false_DEGIL():
    """
    Kullanicinin ornegi: M2 lineage bilinmiyor ama status=PASS kalabilir.
    M2'nin lineage'i mimari olarak hicbir zaman yapilamadigi icin bu TEK
    BASINA fully_verified'i False'a CEKMEMELI (surekli False olur, anlamsiz).
    """
    r = kos()
    assert r.status == STATUS_PASS
    assert r.fully_verified is True


def test_diger_bir_modulde_identity_bilinmiyorsa_fully_verified_false():
    consumed, successors = tam_temiz_girdiler()
    consumed["M1"] = temiz_modul("M1", identity_known=False, source_run_key=None)
    r = kos(consumed, successors)
    assert r.status == STATUS_PASS  # bulgu yok
    assert r.fully_verified is False  # ama kapsam eksik


# ============================================================ INCOMPLETE
def test_kanit_yoksa_INCOMPLETE():
    r = kos(evidence_available=False)
    assert r.status == STATUS_INCOMPLETE
    assert r.fully_verified is None
    assert r.checks == {}


def test_incomplete_MISMATCH_ile_KARISTIRILMAZ():
    r = kos(evidence_available=False)
    assert r.status != STATUS_MISMATCH
    assert r.status != STATUS_PASS


# ============================================================ KIMLIK / SHA
def test_ayni_girdi_ayni_kimlik_ve_sha():
    a = kos()
    b = kos()
    assert a.reconciliation_run_id == b.reconciliation_run_id
    assert reconciliation_sha256(a) == reconciliation_sha256(b)


def test_farkli_bulgu_ayni_kimlik_farkli_sha():
    consumed, successors = tam_temiz_girdiler()
    successors["M1"] = temiz_successor("M1", newer=True)
    a = kos()
    b = kos(consumed, successors)
    assert a.reconciliation_run_id == b.reconciliation_run_id
    assert reconciliation_sha256(a) != reconciliation_sha256(b)


def test_farkli_ticker_farkli_kimlik():
    a = kos(ticker="AAA")
    b = kos(ticker="BBB")
    assert a.reconciliation_run_id != b.reconciliation_run_id


# ============================================================ GIRDI DOGRULAMA
def test_naive_analysis_at_reddedilir():
    consumed, successors = tam_temiz_girdiler()
    with pytest.raises(ModuleReconciliationError):
        reconcile_module_freshness(
            total_rasyo_run_id="RUN-A", ticker="GARFA",
            analysis_at=datetime(2026, 3, 2, 20, 0), started_at=BASLA,
            finished_at=BITIR, consumed_modules=consumed, successors=successors)


def test_eksik_beklenen_modul_reddedilir():
    consumed, successors = tam_temiz_girdiler()
    del consumed["M1"]
    with pytest.raises(ModuleReconciliationError):
        kos(consumed, successors)


def test_finished_before_started_reddedilir():
    with pytest.raises(ModuleReconciliationError):
        kos(finished_at=BASLA, started_at=BITIR)
