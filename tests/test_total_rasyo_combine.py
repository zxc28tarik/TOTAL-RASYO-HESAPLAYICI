"""
Alti modullu birlestirme cekirdegi testleri.

EN KRITIK KILIT: formul TEK kaynaktan gelir (total_rasyo_score.py) ve
orkestratör ikinci bir agirlik kumesi TANIMLAMAZ. Kayip taslaktaki
0.30/0.45/0.25 agirliklari hicbir yerde kullanilamaz.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.analytics.total_rasyo_combine import (
    FORBIDDEN_DRAFT_WEIGHTS,
    STATUS_ENGINE_CRASHED,
    STATUS_INSUFFICIENT,
    STATUS_NOT_RUN,
    STATUS_OK,
    STATUS_ROUTING_CONFLICT,
    WEIGHTS_PROFILE,
    CombineError,
    combine_company_result,
    resolve_weights,
)
from src.analytics.total_rasyo_engine_isolation import (
    ENGINE_STATUS_CRASHED,
    ENGINE_STATUS_NOT_RUN,
    ENGINE_STATUS_OK,
    ENGINE_STATUS_REJECTED,
    run_engine_safely,
    skipped_engine_run,
)
from src.analytics.total_rasyo_module_reader import (
    MODULE_SOURCE_TYPE,
    READ_MODULE_KEYS,
    CompanyModuleContext,
    ModuleComponent,
)
from src.analytics.total_rasyo_score import DEFAULT_WEIGHTS, MODULE_KEYS, compute_total_rasyo

TZ = ZoneInfo("Europe/Istanbul")
KAYNAK_AT = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
KAYNAK = Path("src/analytics/total_rasyo_combine.py")


def m2payload(score=0.8, usable=True, conf=0.7, source="TEST_V1", reason=None):
    return {"m2": score, "m2_source": source, "valuation_usable": usable,
            "valuation_status": "OK" if usable else "REJECTED",
            "valuation_reason": reason, "valuation_confidence": conf,
            "m2_source_at": KAYNAK_AT}


def calisan(engine="BANK", results=None, rejections=None):
    return run_engine_safely(engine, lambda: {"results": results or {},
                                              "rejections": rejections or {}})


def coken(engine="GYO"):
    def patla():
        raise ValueError("motor patladi")
    return run_engine_safely(engine, patla)


def ctx(ticker="GARAN", *, m1=0.6, m3=0.7, ek4=0.5, ek1=0.4, ek9=0.3, good=9):
    degerler = {"M1": m1, "M3": m3, "Ek4": ek4, "Ek1": ek1, "Ek9": ek9}
    return CompanyModuleContext(
        ticker=ticker,
        components={
            k: ModuleComponent(
                key=k, score=degerler[k],
                source_at=None if degerler[k] is None else KAYNAK_AT,
                source_type=None if degerler[k] is None else MODULE_SOURCE_TYPE,
                missing=degerler[k] is None,
                reason=f"{k}_KAYNAGI_YOK" if degerler[k] is None else None)
            for k in READ_MODULE_KEYS
        },
        good_count_ge8=good, good_count_missing=good is None,
        good_count_reason=None if good is not None else "GOOD_COUNT_KAYNAGI_YOK",
        asof_date=None, analysis_at=KAYNAK_AT,
    )


def birlestir(**kw):
    kw.setdefault("ticker", "GARAN")
    kw.setdefault("routed_engine", "BANK")
    kw.setdefault("engine_run", calisan("BANK", {"GARAN": m2payload()}))
    kw.setdefault("module_context", ctx())
    return combine_company_result(**kw)


# ============================================ TEK FORMUL KAYNAGI
def test_orkestratorde_ikinci_agirlik_kumesi_yok():
    """
    Kaynak kodda MODULE_KEYS disinda bir agirlik sozlugu TANIMLANMAMALI.
    FORBIDDEN_DRAFT_WEIGHTS yalniz YASAK listesi olarak durur.
    """
    agac = ast.parse(KAYNAK.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Assign):
            continue
        # Yalniz SAYISAL sozluk sabitleri agirlik kumesi sayilir;
        # WEIGHTS_PROFILE gibi metin sabitleri kapsam disidir.
        if not isinstance(dugum.value, ast.Dict):
            continue
        if not dugum.value.values or not all(
            isinstance(v, ast.Constant) and isinstance(v.value, (int, float))
            and not isinstance(v.value, bool)
            for v in dugum.value.values
        ):
            continue
        hedefler = [t.id for t in dugum.targets if isinstance(t, ast.Name)]
        assert hedefler == ["FORBIDDEN_DRAFT_WEIGHTS"], (
            f"orkestratör kendi agirlik kumesini tanimlamamali: {hedefler}")


def test_kayip_taslak_agirliklari_reddedilir():
    """0.30/0.45/0.25 alti anahtarli sozlesmeden GECEMEZ."""
    with pytest.raises(Exception):
        resolve_weights(FORBIDDEN_DRAFT_WEIGHTS)


def test_uc_modullu_agirlik_kumesi_reddedilir():
    with pytest.raises(Exception):
        resolve_weights({"M1": 0.30, "M2": 0.45, "M3": 0.25})


def test_varsayilan_agirliklar_kanitlanmis_sozlesme():
    assert resolve_weights() == dict(DEFAULT_WEIGHTS)
    assert resolve_weights()["M2"] == 0.40
    assert resolve_weights()["Ek4"] == 0.16
    assert sum(resolve_weights().values()) == pytest.approx(1.0)


def test_skor_compute_total_rasyo_ile_birebir():
    """Orkestratör kendi aritmetigini yapmaz; referansla birebir esitlik."""
    r = birlestir()
    beklenen = compute_total_rasyo(
        {"M2": 0.8, "M1": 0.6, "M3": 0.7, "Ek4": 0.5, "Ek1": 0.4, "Ek9": 0.3},
        good_count_ge8=9)
    assert r.final_score == pytest.approx(beklenen["final_score"])
    assert r.base_score == pytest.approx(beklenen["base_score"])
    assert r.total_rasyo_100 == pytest.approx(beklenen["total_rasyo_100"])
    assert r.decision == beklenen["decision"]
    assert r.veto_flag == beklenen["veto_flag"]
    assert r.weights_profile == WEIGHTS_PROFILE


def test_veto_orkestratorde_yeniden_yazilmaz():
    """good_count esigin altinda: veto compute_total_rasyo'dan gelmeli."""
    r = birlestir(module_context=ctx(good=2))
    beklenen = compute_total_rasyo(
        {"M2": 0.8, "M1": 0.6, "M3": 0.7, "Ek4": 0.5, "Ek1": 0.4, "Ek9": 0.3},
        good_count_ge8=2)
    assert r.veto_flag is True
    assert r.final_score == pytest.approx(beklenen["final_score"])
    assert r.final_score == pytest.approx(r.base_score * 0.60)


def test_karar_bantlari_kaynaktan():
    yuksek = birlestir(
        engine_run=calisan("BANK", {"GARAN": m2payload(0.95)}),
        module_context=ctx(m1=0.9, m3=0.9, ek4=0.9, ek1=0.9, ek9=0.9))
    assert yuksek.decision == "AL"
    dusuk = birlestir(
        engine_run=calisan("BANK", {"GARAN": m2payload(0.1)}),
        module_context=ctx(m1=0.1, m3=0.1, ek4=0.1, ek1=0.1, ek9=0.1))
    assert dusuk.decision == "UZAK"


def test_alti_modul_de_katki_verir():
    """Ek4/Ek1/Ek9 DUSURULMEMELI: degistirince skor degismeli."""
    taban = birlestir().final_score
    degisik = birlestir(module_context=ctx(ek4=0.9)).final_score
    assert degisik != taban
    assert degisik - taban == pytest.approx((0.9 - 0.5) * 0.16, abs=1e-9)
    for key, agirlik in [("ek1", 0.08), ("ek9", 0.06)]:
        yeni = birlestir(module_context=ctx(**{key: 0.9})).final_score
        temel = 0.4 if key == "ek1" else 0.3
        assert yeni - taban == pytest.approx((0.9 - temel) * agirlik, abs=1e-9)


def test_katkilar_tanilarda_gorunur():
    r = birlestir()
    assert set(r.diagnostics["contributions"]) == set(MODULE_KEYS)
    assert r.diagnostics["weights"]["M2"] == 0.40


# ============================================ EKSIK BILESEN
def test_fillna_kullanilmaz():
    agac = ast.parse(KAYNAK.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Attribute) and dugum.attr == "fillna":
            pytest.fail("birlestirme fillna kullanmamali")


@pytest.mark.parametrize("eksik", ["m1", "m3", "ek4", "ek1", "ek9"])
def test_tek_modul_eksikse_skor_uretilmez(eksik):
    r = birlestir(module_context=ctx(**{eksik: None}))
    assert r.total_rasyo_status == STATUS_INSUFFICIENT
    assert r.final_score is None and r.decision is None and r.base_score is None
    beklenen = {"m1": "M1", "m3": "M3", "ek4": "Ek4", "ek1": "Ek1", "ek9": "Ek9"}[eksik]
    assert r.missing_modules == (beklenen,)
    assert beklenen in r.rejection_reason


def test_m2_eksikse_skor_uretilmez():
    r = birlestir(engine_run=calisan("BANK", {"GARAN": m2payload(None)}))
    assert r.total_rasyo_status == STATUS_INSUFFICIENT
    assert r.missing_modules == ("M2",)
    assert r.final_score is None


def test_good_count_eksikse_skor_uretilmez():
    """Veto girdisi ZORUNLU sozlesmenin parcasidir."""
    r = birlestir(module_context=ctx(good=None))
    assert r.total_rasyo_status == STATUS_INSUFFICIENT
    assert r.missing_modules == ("good_count_ge8",)
    assert r.veto_flag is None


def test_uc_modul_birden_eksik():
    r = birlestir(module_context=ctx(m1=None, ek1=None, good=None))
    assert set(r.missing_modules) == {"M1", "Ek1", "good_count_ge8"}
    assert r.total_rasyo_status == STATUS_INSUFFICIENT


def test_tum_moduller_eksik():
    r = birlestir(engine_run=calisan("BANK", {"GARAN": m2payload(None)}),
                  module_context=ctx(m1=None, m3=None, ek4=None, ek1=None,
                                     ek9=None, good=None))
    assert set(r.missing_modules) == set(MODULE_KEYS) | {"good_count_ge8"}
    assert r.final_score is None


def test_eksikte_agirlik_dagitilmaz():
    """
    En tehlikeli sessiz hata: eksik modulun agirligini kalanlara dagitmak.
    Eksik varsa HIC skor olmamali.
    """
    r = birlestir(module_context=ctx(ek9=None))
    assert r.final_score is None and r.base_score is None
    assert r.diagnostics.get("contributions") is None


def test_mevcut_moduller_yine_de_gorunur():
    """Eksik veri sonucu dusurmez; dolu moduller raporda kalir."""
    r = birlestir(module_context=ctx(m1=None))
    assert r.modules["M3"]["score"] == pytest.approx(0.7)
    assert r.modules["M3"]["missing"] is False
    assert r.modules["M1"]["score"] is None
    assert r.modules["M1"]["missing"] is True
    assert r.m2_score == pytest.approx(0.8)


# ============================================ M2 SOZLESMESI
def test_m2_kaynagi_sektor_motoru():
    r = birlestir()
    assert r.m2_source_type == "SECTOR_ENGINE"
    assert r.m2_source == "TEST_V1"
    assert r.modules["M2"]["source_type"] == "SECTOR_ENGINE"
    assert r.modules["M2"]["source_type"] != MODULE_SOURCE_TYPE


def test_m2_score_anahtari_da_kabul():
    """BANK hatti m2_score kullanir; bes motor m2 kullanir."""
    yuk = {"m2_score": 0.8, "m2_source": "BANK_V1", "valuation_usable": True,
           "valuation_confidence": 0.7}
    r = birlestir(engine_run=calisan("BANK", {"GARAN": yuk}))
    assert r.m2_score == pytest.approx(0.8)
    assert r.total_rasyo_status == STATUS_OK


def test_celisen_m2_anahtarlari_reddedilir():
    yuk = {"m2": 0.8, "m2_score": 0.3, "m2_source": "X",
           "valuation_usable": True, "valuation_confidence": 0.7}
    with pytest.raises(CombineError, match="farkli deger"):
        birlestir(engine_run=calisan("BANK", {"GARAN": yuk}))


def test_kullanilamaz_degerleme_m2_eksik_sayilir():
    r = birlestir(engine_run=calisan(
        "BANK", {"GARAN": m2payload(0.8, usable=False, reason="BAYAT_FIYAT")}))
    assert r.m2_missing is True and r.m2_score is None
    assert r.total_rasyo_status == STATUS_INSUFFICIENT
    assert r.engine_status == ENGINE_STATUS_REJECTED
    assert "BAYAT_FIYAT" in r.engine_reason


# ============================================ MOTOR DURUMLARI
def test_motor_coktu_sirket_kaybolmaz():
    r = birlestir(routed_engine="GYO", engine_run=coken("GYO"), ticker="AGYO")
    assert r.ticker == "AGYO"
    assert r.total_rasyo_status == STATUS_ENGINE_CRASHED
    assert r.engine_status == ENGINE_STATUS_CRASHED
    assert r.rejection_reason and "ValueError" in r.rejection_reason
    assert r.final_score is None


def test_calistirilmadi_coktu_degildir():
    r = birlestir(routed_engine="GYO",
                  engine_run=skipped_engine_run("GYO", "config yok"))
    assert r.total_rasyo_status == STATUS_NOT_RUN
    assert r.total_rasyo_status != STATUS_ENGINE_CRASHED
    assert r.engine_status == ENGINE_STATUS_NOT_RUN


def test_engine_run_none_calistirilmadi():
    r = birlestir(engine_run=None)
    assert r.total_rasyo_status == STATUS_NOT_RUN


def test_motor_calisti_ama_sirketi_reddetti():
    r = birlestir(engine_run=calisan("BANK", {}, {"GARAN": "YETERSIZ_METRIK"}))
    assert r.engine_status == ENGINE_STATUS_REJECTED
    assert "YETERSIZ_METRIK" in r.engine_reason
    assert r.total_rasyo_status == STATUS_INSUFFICIENT
    assert "M2" in r.missing_modules


def test_yonlendirme_cakismasi_skor_uretmez():
    r = birlestir(routing_conflict=("BANK", "NONFIN"))
    assert r.total_rasyo_status == STATUS_ROUTING_CONFLICT
    assert r.final_score is None and r.decision is None
    assert "BANK" in r.rejection_reason and "NONFIN" in r.rejection_reason


def test_cakisma_motor_sonucundan_ONCE_gelir():
    """Cakisma varsa basarili motor sonucu bile skor URETMEZ."""
    r = birlestir(engine_run=calisan("BANK", {"GARAN": m2payload()}),
                  routing_conflict=("BANK", "NONFIN"))
    assert r.total_rasyo_status == STATUS_ROUTING_CONFLICT
    assert r.m2_score is None


# ============================================ GENEL SOZLESME
def test_basarili_sonucta_tum_alanlar_dolu():
    r = birlestir()
    assert r.total_rasyo_status == STATUS_OK
    assert r.rejection_reason is None and r.missing_modules == ()
    assert r.engine_status == ENGINE_STATUS_OK
    for key in MODULE_KEYS:
        assert r.modules[key]["score"] is not None
        assert r.modules[key]["missing"] is False
        assert r.modules[key]["source_type"] is not None
    assert r.data_confidence is not None


def test_ok_olmayan_her_sonuc_neden_tasir():
    for kw in [{"module_context": ctx(m1=None)},
               {"engine_run": coken("BANK")},
               {"engine_run": skipped_engine_run("BANK", "x")},
               {"routing_conflict": ("BANK", "GYO")}]:
        r = birlestir(**kw)
        assert r.total_rasyo_status != STATUS_OK
        assert r.rejection_reason, f"{kw} icin neden bos"


def test_skor_ve_karar_birlikte_var_veya_yok():
    for kw in [{}, {"module_context": ctx(m1=None)}, {"engine_run": coken("BANK")}]:
        r = birlestir(**kw)
        assert (r.final_score is None) == (r.decision is None)


def test_ticker_normalize():
    assert birlestir(ticker=" garan ").ticker == "GARAN"


@pytest.mark.parametrize("bozuk", [float("inf"), True, "abc", 1.5, -0.1])
def test_gecersiz_m2_reddedilir(bozuk):
    with pytest.raises(CombineError):
        birlestir(engine_run=calisan("BANK", {"GARAN": m2payload(bozuk)}))


def test_nan_m2_eksik_sayilir():
    import numpy as np
    r = birlestir(engine_run=calisan("BANK", {"GARAN": m2payload(np.nan)}))
    assert r.m2_missing is True
    assert r.total_rasyo_status == STATUS_INSUFFICIENT


def test_data_confidence_modul_tamligiyla_duser():
    tam = birlestir().data_confidence
    eksik = birlestir(module_context=ctx(m1=None)).data_confidence
    assert eksik < tam
