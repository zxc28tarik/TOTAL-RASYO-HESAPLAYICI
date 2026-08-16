"""
V23-B saf hesaplayici testleri.

EN KRITIK KORUMA (kullanicinin ozel duzeltmesi): mismatch_count == 0 TEK
BASINA PASS uretmez. compared_count == 0 -> INCOMPLETE, MISMATCH_COUNT'a
BAKILMAKSIZIN.
"""
from __future__ import annotations

import pytest

from src.analytics.restate_pit_reconciliation import (
    FINDING_DECISION_CHANGED,
    FINDING_PIT_MISSING,
    FINDING_RESTATE_INCOMPLETE,
    FINDING_VALUE_CHANGED,
    PitSnapshot,
    RestatePitReconciliationError,
    RestateSnapshot,
    STATUS_INCOMPLETE,
    STATUS_MISMATCH,
    STATUS_PASS,
    reconcile_pit_vs_restate,
    reconciliation_sha256,
)

RESTATE_RUN = "r" * 64


def pit_ok(score=0.62, decision="IZLE", run_id="RUN-1"):
    return PitSnapshot(exists=True, total_rasyo_status="OK", final_score=score,
                       decision=decision, run_id=run_id)


def pit_yok():
    return PitSnapshot(exists=False)


def restate_ok(score=0.62, decision="IZLE"):
    return RestateSnapshot(exists=True, total_rasyo_status="OK", final_score=score,
                           decision=decision)


def restate_incomplete():
    return RestateSnapshot(exists=True, total_rasyo_status="YETERSIZ_VERI")


def kos(pit=None, restate=None, tickers=("GARFA",)):
    return reconcile_pit_vs_restate(
        restate_run_id=RESTATE_RUN, tickers=tickers,
        pit_snapshots=pit or {"GARFA": pit_ok()},
        restate_snapshots=restate or {"GARFA": restate_ok()})


def test_hicbir_ticker_karsilastirilamadiysa_INCOMPLETE_mismatch_sifir_olsa_bile():
    """
    KULLANICININ OZEL DUZELTMESI: bugunku V23-A gerceginde 10/10 M2 nedeniyle
    RESTATE_INCOMPLETE ise, mismatch_count=0 olmasina RAGMEN PASS DENEMEZ.
    """
    r = kos(
        pit={"GARFA": pit_ok(), "PEER1": pit_ok()},
        restate={"GARFA": restate_incomplete(), "PEER1": restate_incomplete()},
        tickers=("GARFA", "PEER1"))
    assert r.compared_count == 0
    assert r.mismatch_count == 0
    assert r.status == STATUS_INCOMPLETE
    assert r.status != STATUS_PASS


def test_kismi_durum_7_karsilastirilmis_3_incomplete_PASS_verir():
    pit = {f"T{i}": pit_ok() for i in range(10)}
    restate = {f"T{i}": (restate_incomplete() if i < 3 else restate_ok())
              for i in range(10)}
    r = kos(pit=pit, restate=restate, tickers=tuple(pit))
    assert r.compared_count == 7
    assert r.status == STATUS_PASS
    assert r.fully_verified is False


def test_en_az_bir_gercek_karsilastirma_ve_temizse_PASS():
    r = kos()
    assert r.compared_count == 1
    assert r.status == STATUS_PASS
    assert r.fully_verified is True


def test_pit_yoksa_PIT_MISSING_ve_karsilastirilamaz():
    r = kos(pit={"GARFA": pit_yok()})
    c = r.comparisons["GARFA"]
    assert c.compared is False
    assert FINDING_PIT_MISSING in c.findings
    assert r.status == STATUS_INCOMPLETE


def test_pit_missing_mismatch_SAYILMAZ():
    r = kos(
        pit={"GARFA": pit_ok(), "PEER1": pit_yok()},
        restate={"GARFA": restate_ok(), "PEER1": restate_ok()},
        tickers=("GARFA", "PEER1"))
    assert r.mismatch_count == 0
    assert "PEER1" not in r.mismatched_tickers()
    assert "PEER1" in r.pit_missing_tickers()


def test_restate_incomplete_deger_karsilastirmasi_YAPILMAZ():
    r = kos(restate={"GARFA": restate_incomplete()})
    c = r.comparisons["GARFA"]
    assert c.compared is False
    assert FINDING_RESTATE_INCOMPLETE in c.findings
    assert FINDING_VALUE_CHANGED not in c.findings
    assert FINDING_DECISION_CHANGED not in c.findings


def test_restate_incomplete_mismatch_SAYILMAZ():
    r = kos(
        pit={"GARFA": pit_ok(), "PEER1": pit_ok()},
        restate={"GARFA": restate_ok(), "PEER1": restate_incomplete()},
        tickers=("GARFA", "PEER1"))
    assert r.mismatch_count == 0
    assert "PEER1" not in r.mismatched_tickers()
    assert "PEER1" in r.restate_incomplete_tickers()


def test_m2_nedenli_incomplete_sahte_fark_uretmez():
    r = kos(pit={"GARFA": pit_ok(score=0.90, decision="AL")},
           restate={"GARFA": restate_incomplete()})
    assert r.mismatch_count == 0
    assert r.comparisons["GARFA"].restate_final_score is None


def test_value_changed_yalniz():
    r = kos(pit={"GARFA": pit_ok(score=0.60, decision="IZLE")},
           restate={"GARFA": restate_ok(score=0.90, decision="IZLE")})
    c = r.comparisons["GARFA"]
    assert FINDING_VALUE_CHANGED in c.findings
    assert FINDING_DECISION_CHANGED not in c.findings
    assert r.status == STATUS_MISMATCH


def test_decision_changed_yalniz():
    r = kos(pit={"GARFA": pit_ok(score=0.60, decision="IZLE")},
           restate={"GARFA": restate_ok(score=0.60, decision="AL")})
    c = r.comparisons["GARFA"]
    assert FINDING_DECISION_CHANGED in c.findings
    assert FINDING_VALUE_CHANGED not in c.findings


def test_ikisi_birlikte_bagimsiz():
    r = kos(pit={"GARFA": pit_ok(score=0.60, decision="IZLE")},
           restate={"GARFA": restate_ok(score=0.90, decision="AL")})
    c = r.comparisons["GARFA"]
    assert FINDING_VALUE_CHANGED in c.findings
    assert FINDING_DECISION_CHANGED in c.findings


def test_deger_ayni_karar_ayniysa_bulgu_yok():
    r = kos()
    assert r.comparisons["GARFA"].findings == ()


def test_kucuk_ondalik_farki_value_changed_SAYILMAZ():
    r = kos(pit={"GARFA": pit_ok(score=0.6000000001)},
           restate={"GARFA": restate_ok(score=0.6000000002)})
    assert FINDING_VALUE_CHANGED not in r.comparisons["GARFA"].findings


def test_restate_her_zaman_incomplete_iken_view_YANLIS_gorunurdu_ama_burada_dogru():
    r = kos(pit={"GARFA": pit_ok(decision="AL")},
           restate={"GARFA": restate_incomplete()})
    assert FINDING_DECISION_CHANGED not in r.comparisons["GARFA"].findings
    assert r.mismatch_count == 0


def test_ayni_girdi_ayni_kimlik_ve_sha():
    r1 = kos()
    r2 = kos()
    assert r1.reconciliation_run_id == r2.reconciliation_run_id
    assert reconciliation_sha256(r1) == reconciliation_sha256(r2)


def test_farkli_bulgu_ayni_kimlik_farkli_sha():
    r1 = kos()
    r2 = kos(restate={"GARFA": restate_ok(score=0.99)})
    assert r1.reconciliation_run_id == r2.reconciliation_run_id
    assert reconciliation_sha256(r1) != reconciliation_sha256(r2)


def test_farkli_restate_run_farkli_kimlik():
    r1 = kos()
    r2 = reconcile_pit_vs_restate(
        restate_run_id="s" * 64, tickers=("GARFA",),
        pit_snapshots={"GARFA": pit_ok()}, restate_snapshots={"GARFA": restate_ok()})
    assert r1.reconciliation_run_id != r2.reconciliation_run_id


def test_eksik_snapshot_reddedilir():
    with pytest.raises(RestatePitReconciliationError):
        reconcile_pit_vs_restate(restate_run_id=RESTATE_RUN, tickers=("GARFA",),
                                 pit_snapshots={}, restate_snapshots={})


def test_bos_ticker_reddedilir():
    with pytest.raises(RestatePitReconciliationError):
        kos(tickers=())


def test_restate_exists_false_reddedilir():
    with pytest.raises(RestatePitReconciliationError):
        kos(restate={"GARFA": RestateSnapshot(exists=False)})
