"""
V23-A saf hesaplayici testleri.

En kritik koruma: M1/M3/Ek1/Ek4/Ek9 kusursuz olsa bile M2 yoksa COMPLETE
olamaz. Karsi mutasyon: PIT M2'yi fallback yapan implementasyon kirilmali.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.analytics.total_rasyo_restate_calculator import (
    READER_VERSION,
    REASON_MODULE_UNAVAILABLE_AT_CUTOFF,
    REASON_NO_MODULE_RECORD,
    REASON_NO_RESTATE_SOURCE_M2,
    RESTATE_CONTRACT_VERSION,
    RestateCalculationError,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    compute_restate,
)
from src.analytics.total_rasyo_restate_reader import (
    RestateCompanyContext,
    RestateModuleComponent,
)

TZ = timezone(timedelta(hours=3))
HEDEF = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
CUTOFF = datetime(2026, 4, 1, 10, 0, tzinfo=TZ)
KAYNAK = HEDEF - timedelta(hours=1)


def tam_context(ticker="GARFA", *, source_run_key="RUN-1", good_count=9):
    comps = {
        k: RestateModuleComponent(k, 0.5 + i * 0.05, False, KAYNAK, source_run_key,
                                  source_run_key is not None)
        for i, k in enumerate(("M1", "M3", "Ek4", "Ek1", "Ek9"))
    }
    return RestateCompanyContext(ticker, comps, good_count, False,
                                 KAYNAK.date(), KAYNAK)


def kos(contexts=None, tickers=("GARFA",), **kw):
    args = dict(target_analysis_at=HEDEF, knowledge_cutoff_at=CUTOFF,
               tickers=tickers, module_contexts=contexts or {})
    args.update(kw)
    return compute_restate(**args)


def test_m1_m3_ek1_ek4_ek9_kusursuz_M2_yoksa_COMPLETE_OLAMAZ():
    """KULLANICININ ACIKCA ISTEDIGI KORUMA."""
    r = kos({"GARFA": tam_context()})
    sonuc = r.company_results["GARFA"]
    assert sonuc.total_rasyo_status != STATUS_OK
    assert sonuc.total_rasyo_status == STATUS_INSUFFICIENT
    assert sonuc.insufficiency_reason == REASON_NO_RESTATE_SOURCE_M2
    assert sonuc.final_score is None
    assert sonuc.decision is None


def test_m2_her_zaman_eksik_isaretlenir():
    r = kos({"GARFA": tam_context()})
    m2 = r.company_results["GARFA"].modules["M2"]
    assert m2.missing is True
    assert m2.score is None
    assert m2.identity_known is False


def test_hicbir_ticker_COMPLETE_uretemez_su_an():
    r = kos({"GARFA": tam_context(), "PEER1": tam_context("PEER1")},
           tickers=("GARFA", "PEER1"))
    assert r.complete_tickers() == ()
    assert set(r.incomplete_tickers()) == {"GARFA", "PEER1"}


def test_diagnostics_complete_incomplete_sayaci():
    r = kos({"GARFA": tam_context()})
    assert r.diagnostics["complete_count"] == 0
    assert r.diagnostics["incomplete_count"] == 1


def test_M1_de_eksikse_neden_MODULE_UNAVAILABLE():
    ctx = tam_context()
    from dataclasses import replace
    yeni_comps = dict(ctx.components)
    yeni_comps["M1"] = replace(yeni_comps["M1"], score=None, missing=True,
                               source_at=None, source_run_key=None,
                               identity_known=False)
    ctx2 = replace(ctx, components=yeni_comps)
    r = kos({"GARFA": ctx2})
    assert r.company_results["GARFA"].insufficiency_reason == REASON_MODULE_UNAVAILABLE_AT_CUTOFF


def test_hic_kayit_yoksa_neden_MODULE_KAYDI_YOK():
    r = kos({})
    sonuc = r.company_results["GARFA"]
    assert sonuc.total_rasyo_status == STATUS_INSUFFICIENT
    assert sonuc.insufficiency_reason == REASON_NO_MODULE_RECORD
    assert all(v.missing for v in sonuc.modules.values())


def test_good_count_eksikse_de_yetersiz():
    ctx = tam_context()
    from dataclasses import replace
    ctx2 = replace(ctx, good_count_ge8=None, good_count_missing=True)
    r = kos({"GARFA": ctx2})
    assert r.company_results["GARFA"].total_rasyo_status == STATUS_INSUFFICIENT


def test_ticker_tekillestirilir_ve_siralanir():
    r1 = kos({"GARFA": tam_context()}, tickers=("garfa", "GARFA", " Garfa "))
    assert r1.tickers == ("GARFA",)


def test_farkli_sira_ayni_kimlik():
    r1 = kos({"AAA": tam_context("AAA"), "BBB": tam_context("BBB")},
            tickers=("AAA", "BBB"))
    r2 = kos({"AAA": tam_context("AAA"), "BBB": tam_context("BBB")},
            tickers=("BBB", "AAA"))
    assert r1.restate_run_id == r2.restate_run_id
    assert r1.results_sha256 == r2.results_sha256


def test_farkli_tz_ayni_an_ayni_kimlik():
    baska_tz = timezone(timedelta(hours=0))
    hedef2 = HEDEF.astimezone(baska_tz)
    cutoff2 = CUTOFF.astimezone(baska_tz)
    r1 = kos({"GARFA": tam_context()})
    r2 = kos({"GARFA": tam_context()}, target_analysis_at=hedef2,
            knowledge_cutoff_at=cutoff2)
    assert r1.restate_run_id == r2.restate_run_id


def test_naive_datetime_reddedilir():
    with pytest.raises(RestateCalculationError):
        kos({"GARFA": tam_context()}, target_analysis_at=datetime(2026, 3, 2, 20, 0))


def test_cutoff_hedeften_once_olamaz():
    with pytest.raises(RestateCalculationError):
        kos({"GARFA": tam_context()}, knowledge_cutoff_at=HEDEF - timedelta(days=1))


def test_bos_ticker_listesi_reddedilir():
    with pytest.raises(RestateCalculationError):
        kos({}, tickers=())


def test_ayni_girdi_ayni_kimlik_ve_hash():
    r1 = kos({"GARFA": tam_context()})
    r2 = kos({"GARFA": tam_context()})
    assert r1.restate_run_id == r2.restate_run_id
    assert r1.inputs_sha256 == r2.inputs_sha256
    assert r1.results_sha256 == r2.results_sha256


def test_farkli_ticker_kumesi_farkli_kimlik():
    r1 = kos({"GARFA": tam_context()}, tickers=("GARFA",))
    r2 = kos({"GARFA": tam_context(), "PEER1": tam_context("PEER1")},
            tickers=("GARFA", "PEER1"))
    assert r1.restate_run_id != r2.restate_run_id


def test_farkli_modul_girdisi_farkli_inputs_sha_ama_ayni_sonuc_hash_olabilir():
    """
    KULLANICININ ISTEDIGI AYRIM: sonuc tesadufen ayni cikabilir (ikisi de
    YETERSIZ_VERI/M2 eksik) ama TUKETILEN modul girdileri farkliysa
    inputs_sha256 farkli olmali.
    """
    ctx_a = tam_context(source_run_key="RUN-A")
    ctx_b = tam_context(source_run_key="RUN-B")
    ra = kos({"GARFA": ctx_a})
    rb = kos({"GARFA": ctx_b})
    assert ra.results_sha256 == rb.results_sha256, "ikisi de YETERSIZ_VERI/M2 eksik olmali"
    assert ra.inputs_sha256 != rb.inputs_sha256, "farkli source_run_key farkli inputs_sha uretmeli"


def test_restate_contract_version_kimlige_girer():
    r = kos({"GARFA": tam_context()})
    assert r.restate_contract_version == RESTATE_CONTRACT_VERSION
    assert r.reader_version == READER_VERSION


def test_calculation_profile_version_kimlige_girer():
    r1 = kos({"GARFA": tam_context()}, calculation_profile="V1")
    r2 = kos({"GARFA": tam_context()}, calculation_profile="V2")
    assert r1.restate_run_id != r2.restate_run_id
