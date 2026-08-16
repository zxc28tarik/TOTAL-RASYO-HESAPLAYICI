"""
v4.7 — ROE belirsizlik ve doyma test paketi (GPT'nin 10 maddelik listesi).

Kabul olcutleri:
  - yukselen/dusen duzgun seriler BENZER belirsizlik, FARKLI buyume yonu
  - trendli+gurultulu > duzgun trend belirsizligi
  - BNK1 bandi 11.2x -> ~2.5x
  - tani alanlari skora sizmaz
"""
import math
import pytest
import math
from roe_uncertainty import (
    estimate_roe_uncertainty, theil_sen, mad_sd, quantile,
    ABSOLUTE_FLOOR_DEFAULT, MIN_SECTOR_SAMPLE_DEFAULT,
)
from bank_v47 import bank_valuation, bank_valuation_with_estimated_uncertainty as bv_est
from spec_v46 import INSUFFICIENT, OK
from spec_v46 import valuation_score, OK, INSUFFICIENT

COE, MACRO, BVPS, PAYOUT = 0.3705, 0.140135, 21.24, 0.25
SABIT      = [0.21] * 6
YUKSELEN   = [0.12, 0.16, 0.20, 0.24, 0.28, 0.32]
DUSEN      = [0.32, 0.28, 0.24, 0.20, 0.16, 0.12]
BNK1       = [0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]
GURULTULU  = [0.12, 0.19, 0.17, 0.26, 0.22, 0.33]


# ---------------------------------------------- 1-4: temel seriler
def test_1_sabit_roe():
    u = estimate_roe_uncertainty(SABIT)
    assert u["sd_roe_residual"] == pytest.approx(0.0, abs=1e-12)
    assert u["sd_roe_effective"] == pytest.approx(ABSOLUTE_FLOOR_DEFAULT)
    assert u["floor_binding"] is True
    assert u["trend_slope"] == pytest.approx(0.0, abs=1e-12)


def test_2_kusursuz_yukselen_dogrusal():
    u = estimate_roe_uncertainty(YUKSELEN)
    assert u["sd_roe_residual"] == pytest.approx(0.0, abs=1e-9), "duzgun trend gurultu DEGIL"
    assert u["trend_slope"] > 0
    assert u["sd_roe_raw"] > 10 * u["sd_roe_effective"], "ham MAD trendi oynaklik sayiyordu"


def test_3_kusursuz_dusen_dogrusal():
    u = estimate_roe_uncertainty(DUSEN)
    assert u["sd_roe_residual"] == pytest.approx(0.0, abs=1e-9)
    assert u["trend_slope"] < 0


def test_4_yukselen_dusen_ayni_belirsizlik_farkli_yon():
    """KABUL OLCUTU: benzer belirsizlik, farkli buyume yonu."""
    up, dn = estimate_roe_uncertainty(YUKSELEN), estimate_roe_uncertainty(DUSEN)
    assert up["sd_roe_effective"] == pytest.approx(dn["sd_roe_effective"])
    assert up["trend_slope"] == pytest.approx(-dn["trend_slope"])


def test_5_trendli_gurultulu_daha_yuksek_belirsizlik():
    """KABUL OLCUTU: gercek gurultu KORUNUR."""
    g = estimate_roe_uncertainty(GURULTULU)
    y = estimate_roe_uncertainty(YUKSELEN)
    assert g["sd_roe_residual"] > y["sd_roe_residual"]
    assert g["sd_roe_effective"] > 5 * ABSOLUTE_FLOOR_DEFAULT
    assert g["floor_source"] == "RESIDUAL_SCALE"


# ---------------------------------------------- 6-7: bozuk girdiler
def test_6_tek_uc_deger_egimi_bozmaz():
    """Theil-Sen tek uc degerden dayanikli olmali (istenen davranis)."""
    temiz = estimate_roe_uncertainty(YUKSELEN)
    bozuk = list(YUKSELEN); bozuk[3] = 0.95
    u = estimate_roe_uncertainty(bozuk)
    assert u["trend_slope"] == pytest.approx(temiz["trend_slope"], rel=0.5)


@pytest.mark.xfail(reason="BILINEN SINIR: MAD kirilma noktasi %50. Duzgun dogrusal seri + "
                          "tek uc degerde artiklarin yarisindan fazlasi tam 0 oldugu icin "
                          "MAD=0 kalir; uc deger belirsizlige HIC katki vermez. "
                          "Cozum adaylari: Qn/Sn olcek tahmincisi veya ayri uc deger bayragi.",
                   strict=True)
def test_6b_tek_uc_deger_belirsizligi_artirmali():
    temiz = estimate_roe_uncertainty(YUKSELEN)
    bozuk = list(YUKSELEN); bozuk[3] = 0.95
    u = estimate_roe_uncertainty(bozuk)
    assert u["sd_roe_residual"] > temiz["sd_roe_residual"]


def test_6c_uc_deger_sayisal_kanit():
    """Bulgunun sayisal kaydi: iki durumda da artik MAD tam sifir."""
    bozuk = list(YUKSELEN); bozuk[3] = 0.95
    assert estimate_roe_uncertainty(YUKSELEN)["sd_roe_residual"] == pytest.approx(0.0, abs=1e-12)
    assert estimate_roe_uncertainty(bozuk)["sd_roe_residual"] == pytest.approx(0.0, abs=1e-12)
    # gurultulu seride ise uc deger GORULUR (yarisindan fazlasi sifir degil)
    g_bozuk = list(GURULTULU); g_bozuk[3] = 0.95
    assert estimate_roe_uncertainty(g_bozuk)["sd_roe_residual"] > \
           estimate_roe_uncertainty(GURULTULU)["sd_roe_residual"]


def test_7_eksik_donemler():
    u = estimate_roe_uncertainty([0.12, None, 0.20, None, 0.28, 0.32])
    assert u["n_valid"] == 4
    assert u["sd_roe_residual"] is not None
    u2 = estimate_roe_uncertainty([0.12, None, None, None, None, 0.32])
    assert u2["n_valid"] == 2
    assert u2["floor_source"] == "INSUFFICIENT_DATA"
    assert u2["sd_roe_effective"] == pytest.approx(ABSOLUTE_FLOOR_DEFAULT)


# ---------------------------------------------- 8-10: taban davranisi
def test_8_sektor_orneklemi_yetersiz():
    az = [0.03] * (MIN_SECTOR_SAMPLE_DEFAULT - 1)
    u = estimate_roe_uncertainty(SABIT, sector_residual_scales=az)
    assert u["floor_source"] == "ABSOLUTE_FLOOR", "yetersiz orneklemde sektor tabani KULLANILMAMALI"
    assert u["sd_roe_effective"] == pytest.approx(ABSOLUTE_FLOOR_DEFAULT)


def test_9_sektor_dagilimi_sifira_yigilmis():
    """Dejenere dagilim korumasiz birakmamali -> mutlak taban devreye girer."""
    yigin = [0.0] * 25
    u = estimate_roe_uncertainty(SABIT, sector_residual_scales=yigin)
    assert u["sd_roe_effective"] == pytest.approx(ABSOLUTE_FLOOR_DEFAULT)
    assert u["floor_source"] == "ABSOLUTE_FLOOR"


def test_10_sektor_yuzdeligi_baglar():
    olcekler = [0.02 + 0.001 * i for i in range(30)]
    u = estimate_roe_uncertainty(SABIT, sector_residual_scales=olcekler)
    assert u["floor_source"] == "SECTOR_QUANTILE"
    assert u["sd_roe_effective"] > ABSOLUTE_FLOOR_DEFAULT
    assert u["sd_roe_effective"] == pytest.approx(quantile(olcekler, 0.15))


def test_11_taban_baglanma_orani_makul():
    """Taban ISTISNA olmali, varsayilan degil (GPT kalibrasyon olcutu)."""
    seriler = [SABIT, YUKSELEN, DUSEN, BNK1, GURULTULU,
               [0.15, 0.19, 0.14, 0.22, 0.17, 0.25], [0.30, 0.24, 0.29, 0.21, 0.26, 0.19]]
    olcekler = [estimate_roe_uncertainty(s)["sd_roe_residual"] for s in seriler] * 5
    baglayan = sum(estimate_roe_uncertainty(s, sector_residual_scales=olcekler)["floor_binding"]
                   for s in seriler)
    assert baglayan / len(seriler) <= 0.6, f"taban {baglayan}/{len(seriler)} seride bagliyor - fazla guclu"


# ---------------------------------------------- band genisligi
def test_12_bnk1_bandi_daralir():
    """
    KABUL OLCUTU: ham MAD ile band kullanilamaz genislikte (11.2x); trend
    cikarilinca ~1.9x. Kullanilabilirlik tavani devredeyken ham MAD zaten
    reddediliyor -- daha guclu bir sonuc.
    """
    eski = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=mad_sd(BNK1),
                          max_halfwidth=99.0, band_width_shadow_mode=False)          # tavan kapali: eski davranis
    yeni = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    assert eski["V_high"] / eski["V_low"] > 9.0
    assert 1.5 < yeni["V_high"] / yeni["V_low"] < 3.5

    # tavan acikken ham MAD kullanilamaz sayilir
    r = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=mad_sd(BNK1), band_width_shadow_mode=False)
    assert r["status"] == INSUFFICIENT and r["reason"] == "BAND_TOO_WIDE"


def test_13_duzgun_trend_artik_asiri_genis_band_almaz():
    for seri in (YUKSELEN, DUSEN):
        r = bv_est(BVPS, seri, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
        assert r["V_high"] / r["V_low"] < 3.5


def test_14_gurultulu_seri_daha_genis_band_alir():
    duz = bv_est(BVPS, YUKSELEN, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    gur = bv_est(BVPS, GURULTULU, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    assert gur["V_high"] / gur["V_low"] > duz["V_high"] / duz["V_low"]


def test_15_sd_roe_disaridan_gecirilir():
    """Motor sd_roe'yu ICERIDE yeniden hesaplamamali."""
    a = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=0.01, band_width_shadow_mode=False)
    b = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=0.08, max_halfwidth=99.0, band_width_shadow_mode=False)
    assert a["sd_roe_used"] == 0.01 and b["sd_roe_used"] == 0.08
    assert a["V_low"] > b["V_low"], "buyuk sd daha genis band vermeli"
    assert a["V_mid"] == pytest.approx(b["V_mid"]), "merkez sd'den ETKILENMEMELI"


# ---------------------------------------------- doyma (ekonomik olarak tutarli)
@pytest.mark.parametrize("v_kati,doymali", [(0.7, False), (1.0, False), (1.5, False),
                                            (3.0, True), (10.0, True)])
def test_16_doyma_ADIL_DEGERE_uzakliga_bagli(v_kati, doymali):
    """
    Doyma MUTLAK PD/DD'ye degil, adil degere uzakliga baglidir.
    Onceki sentetik verinin hatasi fiyatin defter degeriyle iliskisiz olmasiydi;
    burada fiyat V_mid'in kati olarak uretiliyor.
    """
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    price = r["V_mid"] * v_kati
    s = valuation_score(r["V_mid"], r["V_low"], r["V_high"], price,
                        r["lower_halfwidth"], r["upper_halfwidth"], v_conf=1.0)
    doydu = s["s_valuation"] in (0.0, 1.0)
    assert doydu == doymali, f"V_mid x{v_kati}: z_val={s['z_val']:.2f} s={s['s_valuation']:.3f}"


def test_16b_deger_yok_eden_banka_defterin_altinda_degerlenir():
    """ROE < COE ise gerekcelendirilmis PD/DD < 1 olmalidir (ekonomik dogruluk)."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    justified_pb = r["V_mid"] / BVPS
    assert r["roe_sus"] < COE
    assert justified_pb < 1.0
    assert justified_pb == pytest.approx((r["roe_sus"] - r["g"]) / (COE - r["g"]), rel=1e-9)


def test_16c_deger_yaratan_banka_defterin_ustunde_degerlenir():
    yuksek_roe = [0.42, 0.44, 0.43, 0.45, 0.44, 0.46]
    r = bv_est(BVPS, yuksek_roe, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    assert r["roe_sus"] > COE
    assert r["V_mid"] / BVPS > 1.0


def test_17_z_val_monoton():
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    zs = [valuation_score(r["V_mid"], r["V_low"], r["V_high"], r["V_mid"] * k,
                          r["lower_halfwidth"], r["upper_halfwidth"], 1.0)["z_val"]
          for k in (0.5, 0.8, 1.0, 1.3, 2.0)]
    assert all(a > b for a, b in zip(zs, zs[1:])), "fiyat arttikca z_val azalmali"


def test_18_olcek_degismezligi():
    """Fiyat ve BVPS ayni oranda olceklenirse sonuc DEGISMEMELI."""
    for k in (0.1, 10.0, 1000.0):
        r1 = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
        r2 = bv_est(BVPS * k, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
        s1 = valuation_score(r1["V_mid"], r1["V_low"], r1["V_high"], r1["V_mid"] * 1.5,
                             r1["lower_halfwidth"], r1["upper_halfwidth"], 1.0)
        s2 = valuation_score(r2["V_mid"], r2["V_low"], r2["V_high"], r2["V_mid"] * 1.5,
                             r2["lower_halfwidth"], r2["upper_halfwidth"], 1.0)
        assert s1["z_val"] == pytest.approx(s2["z_val"], rel=1e-9)


# ---------------------------------------------- degismemesi gerekenler
def test_19_merkez_ve_buyume_degismedi():
    """sd_roe degisikligi V_mid, g ve growth_binding_source'u ETKILEMEMELI."""
    eski = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=mad_sd(BNK1),
                          max_halfwidth=99.0, band_width_shadow_mode=False)
    yeni = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    assert yeni["V_mid"] == pytest.approx(eski["V_mid"])
    assert yeni["g"] == pytest.approx(eski["g"])
    assert yeni["growth_binding_source"] == eski["growth_binding_source"]
    assert yeni["implied_payout"] == pytest.approx(eski["implied_payout"])


def test_20_dof_duzeltmesi_opsiyonel():
    """sqrt(n/(n-2)) OTOMATIK uygulanmamali; kalibrasyonda secilecek."""
    a = estimate_roe_uncertainty(GURULTULU, dof_correction=False)
    b = estimate_roe_uncertainty(GURULTULU, dof_correction=True)
    assert a["dof_corrected"] is False
    assert b["sd_roe_residual"] == pytest.approx(a["sd_roe_residual"] * math.sqrt(6 / 4))


def test_21_tani_alanlari_skor_seviyesine_sizmaz():
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    for alan in ("trend_slope", "floor_source", "sd_roe_raw", "floor_binding", "outlier_flag"):
        assert alan not in r, f"{alan} ust seviyeye sizmis"
    assert "uncertainty" in r and "trend_slope" in r["uncertainty"], "tani blogu ayri tutulmali"


# ---------------------------------------------- v4.7.1 duzeltmeleri
def test_22_eksik_donem_zaman_eksenini_bozmaz():
    """v4.7 HATASI: eksikler silinip yan yana sikistiriliyordu -> yapay trend."""
    u = estimate_roe_uncertainty([0.12, None, 0.20, None, 0.28, 0.32])
    assert u["trend_slope"] == pytest.approx(0.0400, abs=1e-6), "sikistirma 0.0733 veriyordu"
    assert u["sd_roe_residual"] == pytest.approx(0.0, abs=1e-9), "yapay belirsizlik uretmemeli"
    assert u["n_valid"] == 4


def test_23_nan_eksik_donem_inf_gecersiz():
    """
    v4.7.9 SOZLESME DEGISIKLIGI: NaN eksik donemdir (korunur), inf gecersiz
    veridir (reddedilir). Once ikisi de sessizce filtreleniyordu.
    """
    a = estimate_roe_uncertainty([0.12, float("nan"), 0.20, 0.24, 0.28, 0.32])
    assert a["n_valid"] == 5 and math.isfinite(a["trend_slope"])
    assert a["roe_missing_count"] == 1
    with pytest.raises(ValueError):
        estimate_roe_uncertainty([0.12, float("inf"), 0.20, 0.24, 0.28, 0.32])


def test_24_sektor_olceklerinde_nan_atilir_inf_negatif_reddedilir():
    """v4.7.9: NaN/None atilir ve sayilir; inf ve negatif ValueError uretir."""
    u = estimate_roe_uncertainty(SABIT,
                                 sector_residual_scales=[0.02] * 25 + [float("nan"), None])
    assert math.isfinite(u["sd_roe_effective"]) and u["sd_roe_effective"] > 0
    assert u["scales_missing_count"] == 2
    for bozuk in ([0.02] * 25 + [float("inf")], [0.02] * 25 + [-0.5]):
        with pytest.raises(ValueError):
            estimate_roe_uncertainty(SABIT, sector_residual_scales=bozuk)


def test_25_sd_roe_zorunlu_ve_dogrulanir():
    with pytest.raises(TypeError):
        bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)          # sd_roe yok
    for gecersiz in (None, -0.01, float("nan"), float("inf")):
        r = bank_valuation(BVPS, BNK1, COE, MACRO, PAYOUT, sd_roe=gecersiz, band_width_shadow_mode=False)
        assert r["status"] == INSUFFICIENT and r["reason"] == "INVALID_SD_ROE"


def test_26_uc_deger_tani_uretir_bandi_genisletmez():
    """Tek seferlik ROE sicramasi KALICI oynaklik gibi ele alinmamali."""
    temiz = estimate_roe_uncertainty(YUKSELEN)
    bozuk_seri = list(YUKSELEN); bozuk_seri[3] = 0.95
    bozuk = estimate_roe_uncertainty(bozuk_seri)
    assert bozuk["outlier_flag"] is True and temiz["outlier_flag"] is False
    assert bozuk["conf_penalty"] < 1.0 and temiz["conf_penalty"] == 1.0
    assert bozuk["sd_roe_effective"] == pytest.approx(temiz["sd_roe_effective"]), \
        "band KALICI genislememeli; ceza V_conf uzerinden"


# ---------------------------------------------- v4.7.2: giris guvenligi
@pytest.mark.parametrize("kw", [
    {"absolute_floor": float("nan")},
    {"absolute_floor": float("inf")},
    {"absolute_floor": -0.01},
    {"sector_quantile": 1.1},
    {"sector_quantile": -0.1},
    {"sector_quantile": float("nan")},
    {"min_sector_sample": 0},
    {"outlier_multiplier": -1.0},
    {"outlier_absolute_floor": float("nan")},
])
def test_27_kalibrasyon_parametreleri_dogrulanir(kw):
    """Gecersiz absolute_floor koruyucu tabani SESSIZCE etkisizlestiriyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, **kw)


def test_28_gecerli_parametreler_kabul_edilir():
    r = estimate_roe_uncertainty(SABIT, absolute_floor=0.0, sector_quantile=0.0,
                                 min_sector_sample=1, outlier_multiplier=0.0)
    assert math.isfinite(r["sd_roe_effective"])


def test_29_wrapper_kwargs_ayrimi():
    """Tek **kw belirsizlik parametrelerini degerleme motoruna yonlendiriyordu."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT,
               uncertainty_kwargs={"dof_correction": True}, band_width_shadow_mode=False)
    assert r["status"] == OK
    assert r["uncertainty"]["dof_corrected"] is True
    r2 = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT,
                valuation_kwargs={"d_coe": 0.05}, band_width_shadow_mode=False)
    assert r2["status"] == OK
    assert r2["V_low"] < r["V_low"], "daha genis COE araligi daha genis band vermeli"


# ---------------------------------------------- v4.7.3: cikti sozlesmesi
def test_30_v46_banka_cikti_sozlesmesi_korunur():
    """v4.7'de method/n_total/payout_defaulted/payout_gap/sd_roe DUSMUSTU."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    gerekli = {"method", "n_total", "n_valid", "sd_roe", "sd_roe_used",
               "payout_defaulted", "payout_gap", "implied_payout",
               "roe_sus", "g", "growth_binding_source"}
    assert gerekli <= set(r), f"eksik: {gerekli - set(r)}"
    assert r["method"] == "BANK_JUSTIFIED_PB"
    assert r["n_total"] == 27
    assert r["sd_roe"] == pytest.approx(r["sd_roe_used"])


def test_31_payout_varsayilani_tanisi_kaybolmaz():
    """Bu alan kaybolursa uretimde x0.70 guven cezasi SESSIZCE devre disi kalir."""
    yok = bv_est(BVPS, BNK1, COE, MACRO, payout_sus=None, band_width_shadow_mode=False)
    var = bv_est(BVPS, BNK1, COE, MACRO, payout_sus=0.25, band_width_shadow_mode=False)
    assert yok["payout_defaulted"] is True
    assert var["payout_defaulted"] is False
    assert yok["payout_gap"] == pytest.approx(yok["implied_payout"] - 0.30)
    assert var["payout_gap"] == pytest.approx(var["implied_payout"] - 0.25)


def test_32_v46_ile_alan_kumesi_uyumlu():
    """v4.6 motorunun DONDURDUGU her alan v4.7'de de bulunmali."""
    from spec_v46 import bank_valuation as v46
    eski = v46(BVPS, BNK1, COE, MACRO, PAYOUT)
    yeni = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    kayip = set(eski) - set(yeni)
    assert not kayip, f"v4.6 alanlari kaybolmus: {sorted(kayip)}"


def test_33_v_conf_uctan_uca_iki_ceza_da_uygulanir():
    """
    Uretim hattinin uygulamasi gereken zincir:
        v_conf = tier_cap x payout_cezasi x conf_penalty
    outlier_flag uretilip skora YANSIMAZSA bayrak isiksiz kalir.
    """
    TIER_CAP, PAYOUT_PENALTY = 0.80, 0.70
    bozuk = list(BNK1); bozuk[3] = 0.95        # tek seferlik sicrama

    def v_conf(res):
        c = TIER_CAP
        if res["payout_defaulted"]:
            c *= PAYOUT_PENALTY
        c *= res["uncertainty"]["conf_penalty"]
        return c

    temiz_payout = bv_est(BVPS, BNK1, COE, MACRO, payout_sus=0.25, band_width_shadow_mode=False)
    assert v_conf(temiz_payout) == pytest.approx(0.800)          # canli testte dogrulanmisti

    yok_payout = bv_est(BVPS, BNK1, COE, MACRO, payout_sus=None, band_width_shadow_mode=False)
    assert v_conf(yok_payout) == pytest.approx(0.560)            # 0.80 x 0.70

    uc_deger = bv_est(BVPS, bozuk, COE, MACRO, payout_sus=0.25, band_width_shadow_mode=False)
    assert uc_deger["uncertainty"]["outlier_flag"] is True
    assert v_conf(uc_deger) == pytest.approx(0.800 * 0.85)       # 0.68

    ikisi = bv_est(BVPS, bozuk, COE, MACRO, payout_sus=None, band_width_shadow_mode=False)
    assert v_conf(ikisi) == pytest.approx(0.800 * 0.70 * 0.85)   # 0.476


# ---------------------------------------------- v4.7.4: giris/cikis kapilari
SD_OK = 0.0102


def _bv(**kw):
    a = dict(bvps=BVPS, roe_ttm_series=BNK1, coe=COE, macro_cap=MACRO,
             payout_sus=PAYOUT, sd_roe=SD_OK, band_width_shadow_mode=False)
    a.update(kw)
    return bank_valuation(**a)


@pytest.mark.parametrize("kw,reason", [
    ({"bvps": float("nan")}, "INVALID_BVPS"),
    ({"bvps": float("inf")}, "INVALID_BVPS"),
    ({"bvps": 0.0}, "INVALID_BVPS"),
    ({"bvps": -5.0}, "INVALID_BVPS"),
    ({"coe": float("nan")}, "INVALID_COE"),
    ({"coe": float("inf")}, "INVALID_COE"),
    ({"coe": 0.0}, "INVALID_COE"),
    ({"coe": -0.1}, "INVALID_COE"),
    ({"macro_cap": float("nan")}, "INVALID_MACRO_CAP"),
    ({"macro_cap": float("inf")}, "INVALID_MACRO_CAP"),
    ({"d_coe": float("nan")}, "INVALID_D_COE"),
    ({"d_coe": -0.01}, "INVALID_D_COE"),
    ({"d_g": float("inf")}, "INVALID_D_G"),
    ({"d_g": -0.01}, "INVALID_D_G"),
    ({"payout_sus": float("inf")}, "INVALID_PAYOUT"),
    ({"payout_sus": True}, "INVALID_PAYOUT"),
    ({"bvps": True}, "INVALID_BVPS"),
    ({"coe": True}, "INVALID_COE"),
    ({"d_g": True}, "INVALID_D_G"),
    ({"max_halfwidth": True}, "INVALID_MAX_HALFWIDTH"),
    ({"roe_ttm_series": b"abcdef"}, "INVALID_ROE_SERIES"),
    ({"roe_ttm_series": "abcdef"}, "INVALID_ROE_SERIES"),
    ({"roe_ttm_series": {"a": 1}}, "INVALID_ROE_SERIES"),
    ({"payout_sus": 1.5}, "PAYOUT_OUT_OF_RANGE"),
    ({"payout_sus": -0.1}, "PAYOUT_OUT_OF_RANGE"),
])
def test_34_gecersiz_girdiler_reddedilir(kw, reason):
    """Bu girdilerin hepsi ONCEDEN status=OK + NaN/gecersiz sonuc uretiyordu."""
    r = _bv(**kw)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == reason


def test_35_payout_nan_varsayilana_duser():
    """pandas'tan gelen eksik payout None DEGIL NaN olur; varsayilan uygulanmiyordu."""
    r = _bv(payout_sus=float("nan"))
    assert r["status"] == OK
    assert r["payout_defaulted"] is True
    assert r["payout_gap"] == pytest.approx(r["implied_payout"] - 0.30)
    assert r["V_mid"] == pytest.approx(_bv(payout_sus=None)["V_mid"])


def test_36_macro_cap_nan_sessizce_yoksayilmaz():
    """min() NaN'i atlayip FARKLI bir g seciyordu (0.1401 yerine 0.1612)."""
    assert _bv(macro_cap=float("nan"))["status"] == INSUFFICIENT


def test_37_ok_sonucta_tum_sayisal_alanlar_finite():
    r = _bv()
    assert r["status"] == OK
    for k, v in r.items():
        if isinstance(v, float):
            assert math.isfinite(v), f"{k} finite degil: {v}"


def test_38_ok_sonucta_band_geometrisi_gecerli():
    r = _bv()
    assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]
    assert r["lower_halfwidth"] > 0 and r["upper_halfwidth"] > 0


def test_39_cikis_kapisi_savunma_katmani():
    """Giris kapisi asilsa bile OK+NaN uretilemez (asiri sd_roe ile band bozulur)."""
    r = _bv(sd_roe=1e9)
    if r["status"] == OK:
        assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]
        assert all(math.isfinite(v) for v in r.values() if isinstance(v, float))
    else:
        assert r["reason"] in ("NONFINITE_VALUATION_OUTPUT", "INVALID_VALUATION_GEOMETRY",
                              "CORNERS_LT_6", "CENTER_INVALID", "BAND_TOO_WIDE",
                              "SD_ROE_EXCEEDS_MODEL_DOMAIN", "UNIQUE_CORNERS_LT_6")


# ---------------------------------------------- v4.7.5: negatif g ve seri tipleri
@pytest.mark.parametrize("mc", [-0.10, -0.001, -1.0])
def test_40_negatif_macro_cap_reddedilir(mc):
    """ONCEDEN: status=OK, g negatif, implied_payout %152 -> merkez ve band farkli kural."""
    r = _bv(macro_cap=mc)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == "INVALID_MACRO_CAP"


def test_41_sifir_roe_negatif_macro_cokmez():
    """ONCEDEN: ZeroDivisionError (cikis kapisindan SONRA implied hesaplaniyordu)."""
    r = _bv(roe_ttm_series=[0.0] * 6, macro_cap=-0.10)
    assert r["status"] == INSUFFICIENT


def test_42_sifir_roe_kontrollu_reddedilir():
    r = _bv(roe_ttm_series=[0.0] * 6)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] in ("INVALID_SUSTAINABLE_ROE", "CENTER_INVALID")


def test_43_dusuk_coe_negatif_g_uretmez():
    """
    GPT'nin bulmadigi IKINCI yol: COE < MIN_COE_GAP iken coe-0.05 negatif olup
    macro_cap gecerli olsa bile g'yi negatife cekiyordu.
    """
    for coe in (0.04, 0.02, 0.001):
        r = _bv(coe=coe, macro_cap=0.14)
        if r["status"] == OK:
            assert r["g"] >= 0.0, f"coe={coe} icin g negatif: {r['g']}"


def test_44_merkez_ve_kose_ayni_g_tabanini_kullanir():
    """Kose taramasi max(...,0) yapiyordu; merkez yapmiyordu -> tutarsizlik."""
    r = _bv()
    assert r["status"] == OK
    assert r["g"] >= 0.0
    assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]


@pytest.mark.parametrize("tip", ["list", "tuple", "iterator", "generator"])
def test_45_seri_tipleri_ayni_sonucu_verir(tip):
    """Wrapper seriyi IKI kez kullaniyor; iterator ilk cagrida tukeniyordu."""
    yapicilar = {
        "list": lambda: list(BNK1),
        "tuple": lambda: tuple(BNK1),
        "iterator": lambda: iter(BNK1),
        "generator": lambda: (x for x in BNK1),
    }
    referans = bv_est(BVPS, list(BNK1), COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    r = bv_est(BVPS, yapicilar[tip](), COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    assert r["status"] == OK
    assert r["V_mid"] == pytest.approx(referans["V_mid"])
    assert r["V_low"] == pytest.approx(referans["V_low"])


def test_46_roe_serisi_none_kontrollu():
    for fn in (lambda: bv_est(BVPS, None, COE, MACRO, PAYOUT, band_width_shadow_mode=False),
               lambda: bank_valuation(bvps=BVPS, roe_ttm_series=None, coe=COE,
                                      macro_cap=MACRO, payout_sus=PAYOUT, sd_roe=SD_OK, band_width_shadow_mode=False)):
        r = fn()
        assert r["status"] == INSUFFICIENT
        assert r["reason"] == "INVALID_ROE_SERIES"


def test_47_implied_payout_sozlesme_araliginda():
    """status=OK ise implied_payout [0,1] araliginda olmali (ekonomik sozlesme)."""
    r = _bv()
    assert 0.0 <= r["implied_payout"] <= 1.0
    assert math.isfinite(r["payout_gap"])


def test_48_cikis_kapisi_implied_alanlarini_da_denetler():
    """implied/payout_gap ARTIK kapidan once hesaplaniyor ve finite kontrolune giriyor."""
    r = _bv()
    for alan in ("implied_payout", "payout_gap"):
        assert math.isfinite(r[alan]), f"{alan} finite degil"


# ---------------------------------------------- v4.7.6: kose dagilimi ve tarama
SD_SWEEP = [0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.25, 1.00]


@pytest.mark.parametrize("sd", SD_SWEEP)
def test_49_sd_roe_taramasi_degismezler(sd):
    """Her sd_roe degerinde: cokme yok, OK ise finite + gecerli geometri."""
    r = _bv(sd_roe=sd)
    assert r["status"] in (OK, INSUFFICIENT)
    if r["status"] == OK:
        assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]
        assert all(math.isfinite(v) for v in r.values() if isinstance(v, float))
    else:
        assert r["reason"]


@pytest.mark.parametrize("sd", SD_SWEEP)
def test_50_v_mid_sd_roe_den_etkilenmez(sd):
    r = _bv(sd_roe=sd)
    if r["status"] == OK:
        assert r["V_mid"] == pytest.approx(_bv(sd_roe=0.01)["V_mid"])


def test_51_band_genisligi_ok_bolgesinde_monoton():
    """
    ONCEDEN: sd_roe 0.05->0.10'da band 6.91x -> 3.33x DARALIYORDU (dusuk ROE
    koseleri tanim alanindan cikip listeden dusunce V_low merkeze geri donuyordu).
    Sonra 0.070->0.075'te 40.85x -> 10.93x ile kismi kayipta ayni sorun.
    """
    katlar = []
    for sd in [0.0, 0.005, 0.01, 0.02, 0.03]:
        r = _bv(sd_roe=sd)
        assert r["status"] == OK, f"sd={sd} icin OK beklenirdi"
        katlar.append(r["V_high"] / r["V_low"])
    assert all(a <= b + 1e-9 for a, b in zip(katlar, katlar[1:])), \
        f"band genisligi monoton degil: {[round(k,2) for k in katlar]}"


def test_52_dusuk_roe_katmani_bosalirsa_model_uygulanamaz():
    """roe_sus - sd_roe <= g ise gerekcelendirilmis PD/DD tanimsiz -> band kurulmaz."""
    r = _bv(sd_roe=0.50)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == "SD_ROE_EXCEEDS_MODEL_DOMAIN"
    assert r["corner_tiers"]["low"] == 0


def test_53_asiri_genis_band_reddedilir():
    """Kullanilabilirlik tavani: yari genislik MAX_LOG_VALUE_HALFWIDTH'i asamaz."""
    r = _bv(sd_roe=0.06)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == "BAND_TOO_WIDE"


def test_54_kismi_kose_kaybi_isaretlenir():
    for sd in [0.0, 0.01, 0.02, 0.03]:
        r = _bv(sd_roe=sd)
        if r["status"] == OK and min(r["corner_tiers"].values()) < 9:
            assert "PARTIAL_CORNER_LOSS" in r["flags"]


@pytest.mark.parametrize("k", [0.0, 0.5, 1.0, 2.0, 10.0])
def test_55_d_g_taramasi(k):
    """d_g > macro_cap kirpma yaratir; V_mid degismemeli, kose g'leri sinirda kalmali."""
    r = _bv(d_g=k * MACRO)
    assert r["status"] in (OK, INSUFFICIENT)
    if r["status"] == OK:
        assert r["V_mid"] == pytest.approx(_bv(d_g=0.0)["V_mid"])
        assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"]
        assert r["g"] >= 0.0


def test_56_yinelenen_koseler_n_unique_ile_izlenir():
    """d_g buyukse kose g'leri ayni sinira kirpilir; n_unique bunu gosterir."""
    az = _bv(d_g=0.0)
    cok = _bv(d_g=10 * MACRO)
    assert az["n_unique"] <= az["n_valid"]
    assert cok["n_unique"] <= cok["n_valid"]
    assert cok["n_valid"] == 27


def test_57_ozgun_kose_sayisi_kapisi():
    """n_valid=27 ama ozgun senaryo <6 ise kapi devreye girer."""
    r = _bv(d_coe=0.0, d_g=0.0, sd_roe=0.0)
    assert r["status"] == INSUFFICIENT and r["reason"] == "UNIQUE_CORNERS_LT_6"


# ---------------------------------------------- ozellik tabanli tarama
def test_58_genel_sozlesme_rastgele_tarama():
    """
    hypothesis yerine deterministik rastgele tarama (tohum sabit).
    Aranan genel sozlesme:
      - hicbir girdide kontrolsuz exception yok
      - OK ise: tum sayisal alanlar finite, 0 < V_low <= V_mid <= V_high,
        0 <= implied_payout <= 1, n_valid <= n_total, sd_roe == sd_roe_used
      - OK degilse: reason bos degil
    """
    import random
    rng = random.Random(20260724)
    ok = bad = 0
    for _ in range(3000):
        # Cogunlukla GECERLI, %10 olasilikla bozuk girdi -> iki dal da taranir
        def bazen(gecerli, bozuk, p=0.10):
            return bozuk() if rng.random() < p else gecerli()

        n = rng.choice([6, 8, 10])
        roe = [rng.uniform(0.05, 0.60) if rng.random() > 0.15 else None for _ in range(n)]
        kw = dict(
            bvps=bazen(lambda: rng.uniform(1.0, 500.0), lambda: rng.choice([0.0, -5.0, float("nan")])),
            roe_ttm_series=roe,
            coe=bazen(lambda: rng.uniform(0.10, 0.60), lambda: rng.choice([0.0, -0.1, float("inf")])),
            macro_cap=bazen(lambda: rng.uniform(0.0, 0.25), lambda: rng.choice([-0.1, float("nan")])),
            payout_sus=bazen(lambda: rng.uniform(0.0, 0.9),
                             lambda: rng.choice([None, float("nan"), 1.4, -0.2])),
            sd_roe=bazen(lambda: rng.uniform(0.0, 0.06), lambda: rng.choice([-0.01, float("nan")])),
            d_coe=rng.uniform(0.0, 0.10),
            d_g=rng.uniform(0.0, 0.10),
        )
        try:
            r = bank_valuation(**kw, band_width_shadow_mode=False)
        except Exception as e:                       # noqa: BLE001
            pytest.fail(f"kontrolsuz exception: {type(e).__name__}: {e}\ngirdi={kw}")
        assert isinstance(r, dict) and "status" in r
        if r["status"] == OK:
            ok += 1
            assert 0 < r["V_low"] <= r["V_mid"] <= r["V_high"], kw
            assert 0.0 <= r["implied_payout"] <= 1.0, kw
            assert r["n_valid"] <= r["n_total"], kw
            assert r["sd_roe"] == pytest.approx(r["sd_roe_used"]), kw
            assert all(math.isfinite(v) for v in r.values() if isinstance(v, float)), kw
        else:
            bad += 1
            assert r.get("reason"), kw
    assert ok > 50, f"tarama gecerli vaka uretmedi (ok={ok}, red={bad})"


def test_59_olcek_degismezligi_bvps():
    """BVPS x10 -> band x10; justified_pb, g, implied_payout DEGISMEZ."""
    a = _bv()
    for k in (0.1, 10.0, 1000.0):
        b = _bv(bvps=BVPS * k)
        assert b["status"] == OK
        for alan in ("V_low", "V_mid", "V_high"):
            assert b[alan] == pytest.approx(a[alan] * k, rel=1e-9)
        assert b["g"] == pytest.approx(a["g"])
        assert b["implied_payout"] == pytest.approx(a["implied_payout"])
        assert b["V_mid"] / (BVPS * k) == pytest.approx(a["V_mid"] / BVPS)


# ---------------------------------------------- v4.7.7: tip kapilari ve golge mod
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0.0, -0.1])
def test_60_max_halfwidth_gecersizse_kapali_davranir(bad):
    """NaN/inf tavani SESSIZCE devre disi birakiyordu (absolute_floor=NaN ile ayni sinif)."""
    r = _bv(max_halfwidth=bad)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == "INVALID_MAX_HALFWIDTH"


def test_61_golge_mod_reddetmez_ama_isaretler():
    """Ilk canli calismada oran olculur, otomatik ret yapilmaz."""
    genis = _bv(sd_roe=0.0586, band_width_shadow_mode=True)
    assert genis["status"] == OK
    assert genis["would_be_band_too_wide"] is True
    normal = _bv(sd_roe=0.0102, band_width_shadow_mode=True)
    assert normal["would_be_band_too_wide"] is False
    # golge mod KAPALI iken ayni girdi reddedilir
    assert _bv(sd_roe=0.0586)["reason"] == "BAND_TOO_WIDE"


@pytest.mark.parametrize("kw", [
    {"dof_correction": "false"},
    {"dof_correction": 1},
    {"dof_correction": None},
    {"min_sector_sample": True},
    {"min_sector_sample": 2.5},
    {"min_sector_sample": "20"},
])
def test_62_bool_ve_int_tipleri_siki_dogrulanir(kw):
    """dof_correction='false' bos olmayan string oldugu icin True sayiliyordu;
    min_sector_sample=True ise bool int alt sinifi oldugu icin 1 kabul ediliyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, **kw)


@pytest.mark.parametrize("bad", [None, "abc", b"abc", 5, 3.14])
def test_63_roe_serisi_tip_kapisi(bad):
    """String iterable oldugu icin sessizce n_valid=0 ile 'bos veri' sayiliyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(bad)


@pytest.mark.parametrize("bad", ["abc", b"abc", 5, 3.14])
def test_64_sektor_olcekleri_tip_kapisi(bad):
    """Yanlis tip sessizce mutlak tabana dusuyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, sector_residual_scales=bad)


def test_65_sayisal_string_parametreler_kanonik_tipe_cevrilir():
    """Dogrulamayi float() ile gecen string sonraki hesapta cokuyordu."""
    sc = [0.02 + 0.001 * i for i in range(30)]
    a = estimate_roe_uncertainty(SABIT, sector_residual_scales=sc, sector_quantile=0.15)
    b = estimate_roe_uncertainty(SABIT, sector_residual_scales=sc, sector_quantile="0.15")
    assert a["sd_roe_effective"] == pytest.approx(b["sd_roe_effective"])
    assert a["floor_source"] == b["floor_source"]


def test_66_belirsizlik_ozellik_taramasi():
    """estimate_roe_uncertainty icin genel sozlesme taramasi (once yalniz motor taranmisti)."""
    import random
    rng = random.Random(20260725)
    for _ in range(2000):
        n = rng.choice([2, 4, 6, 8, 12])
        roe = [rng.uniform(-0.3, 0.9) if rng.random() > 0.2 else None for _ in range(n)]
        olcek = None
        if rng.random() < 0.5:
            olcek = [rng.uniform(0.0, 0.2) for _ in range(rng.choice([0, 5, 25, 40]))]
        try:
            u = estimate_roe_uncertainty(
                roe, sector_residual_scales=olcek,
                absolute_floor=rng.uniform(0.0, 0.05),
                sector_quantile=rng.uniform(0.0, 1.0),
                min_sector_sample=rng.choice([1, 5, 20, 50]),
                dof_correction=rng.random() < 0.5,
            )
        except Exception as exc:                     # noqa: BLE001
            pytest.fail(f"kontrolsuz exception: {type(exc).__name__}: {exc}\nroe={roe}")
        assert math.isfinite(u["sd_roe_effective"]) and u["sd_roe_effective"] >= 0
        assert u["sd_roe_effective"] >= u["sd_roe_floor"] - 1e-15
        assert u["floor_source"] in ("RESIDUAL_SCALE", "SECTOR_QUANTILE",
                                     "ABSOLUTE_FLOOR", "INSUFFICIENT_DATA")
        assert isinstance(u["outlier_flag"], bool)
        assert 0 < u["conf_penalty"] <= 1.0
        if u["trend_slope"] is not None:
            assert math.isfinite(u["trend_slope"])


# ---------------------------------------------- v4.7.8: bool reddi ve zorunlu golge mod
@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, 1.0])
def test_67_shadow_mode_sadece_bool_kabul_eder(bad):
    """dof_correction icin duzeltilen string tuzagi yeni parametrede tekrarlanmisti."""
    r = _bv(sd_roe=0.0586, band_width_shadow_mode=bad)
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == "INVALID_BAND_WIDTH_SHADOW_MODE"


def test_68_shadow_mode_zorunlu_parametre():
    """Varsayilani olsa entegrasyonda unutuldugunda kalibre edilmemis 0.80 politikasi
    sessizce sert ret olarak etkinlesirdi."""
    with pytest.raises(TypeError):
        bank_valuation(bvps=BVPS, roe_ttm_series=BNK1, coe=COE, macro_cap=MACRO,
                       payout_sus=PAYOUT, sd_roe=SD_OK)
    with pytest.raises(TypeError):
        bv_est(BVPS, BNK1, COE, MACRO, PAYOUT)


@pytest.mark.parametrize("kw", [
    {"absolute_floor": True}, {"sector_quantile": True},
    {"outlier_multiplier": True}, {"outlier_absolute_floor": True},
])
def test_69_belirsizlik_parametrelerinde_bool_reddedilir(kw):
    """JSON'daki true/false Python'da 1/0 gibi davraniyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, **kw)


def test_70_corner_conf_penalty_uretim_zincirine_dahil():
    """PARTIAL_CORNER_LOSS de uc deger bayragi gibi 'isiksiz tani' kalmamali."""
    TIER_CAP, PAYOUT_PENALTY = 0.80, 0.70

    def v_conf(res):
        c = TIER_CAP
        if res["payout_defaulted"]:
            c *= PAYOUT_PENALTY
        c *= res["uncertainty"]["conf_penalty"]
        c *= res.get("corner_conf_penalty", 1.0)
        return c

    temiz = bv_est(BVPS, BNK1, COE, MACRO, payout_sus=0.25, band_width_shadow_mode=False)
    assert "corner_conf_penalty" not in temiz
    assert v_conf(temiz) == pytest.approx(0.800)

    # kismi kose kaybi olan bir senaryo ara
    for sd in (0.030, 0.032, 0.035):
        r = bank_valuation(BVPS, BNK1, COE, MACRO, 0.25, sd_roe=sd,
                           band_width_shadow_mode=True)
        if r.get("status") == OK and "PARTIAL_CORNER_LOSS" in r.get("flags", []):
            assert r["corner_conf_penalty"] == 0.70
            break


# ---------------------------------------------- v4.7.9: seri kapilari
@pytest.mark.parametrize("bad", [set(BNK1), frozenset(BNK1), {1: 2}])
def test_71_sirasiz_koleksiyon_reddedilir(bad):
    """
    ROE zaman serisinde SIRA ekonomik anlam tasir. set kullaninca ayni yukselen
    seride egim 0.0400 yerine 0.0200, sd_roe_effective 0.005 yerine 0.07413
    cikiyor ve sonuc yine OK donuyordu.
    """
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(bad)
    assert _bv(roe_ttm_series=bad)["reason"] == "INVALID_ROE_SERIES"


@pytest.mark.parametrize("bad", [
    [True] * 6,
    [0.12, 0.16, "oops", 0.20, 0.24, 0.28],
    [0.12, 0.16, float("inf"), 0.20, 0.24, 0.28],
    [0.12, 0.16, object(), 0.20, 0.24, 0.28],
    [0.12, False, 0.20, 0.24, 0.28, 0.32],
])
def test_72_seri_ogeleri_dogrulanir(bad):
    """[True]*6 -> roe_sus=1.0, V_mid=79.28 ile OK donuyordu; 'oops' sessizce
    eksik donem sayiliyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(bad)
    assert _bv(roe_ttm_series=bad)["reason"] == "INVALID_ROE_SERIES"


def test_73_none_ve_nan_zaman_yuvasini_korur():
    """Eksik donem ile gecersiz veri AYRI: None/NaN korunur, digerleri reddedilir."""
    u = estimate_roe_uncertainty([0.12, None, 0.20, float("nan"), 0.28, 0.32])
    assert u["trend_slope"] == pytest.approx(0.0400, abs=1e-9)
    assert u["n_valid"] == 4
    assert u["roe_missing_count"] == 2


def test_74_sayisal_string_seri_esdeger():
    a = estimate_roe_uncertainty(YUKSELEN)
    b = estimate_roe_uncertainty([str(x) for x in YUKSELEN])
    assert a["trend_slope"] == pytest.approx(b["trend_slope"])
    assert a["sd_roe_effective"] == pytest.approx(b["sd_roe_effective"])


@pytest.mark.parametrize("bad", [
    [0.01] * 19 + ["oops"],
    [0.01] * 19 + [-0.50],
    [0.01] * 19 + [True],
    [0.01] * 19 + [float("inf")],
    set([0.01, 0.02, 0.03]),
])
def test_75_sektor_olcekleri_ogeleri_dogrulanir(bad):
    """Bozuk deger sessizce atilip orneklem 20'nin altina dusuyor, SECTOR_QUANTILE
    -> ABSOLUTE_FLOOR gecisi (0.010 -> 0.005) hicbir uyari olmadan yasaniyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, sector_residual_scales=bad, min_sector_sample=20)


def test_76_sektor_tabani_sessizce_kapanmaz():
    """Gecerli 20 olcekle SECTOR_QUANTILE kullanilir; bozuk deger artik hata verir."""
    iyi = estimate_roe_uncertainty(SABIT, sector_residual_scales=[0.01] * 20,
                                   min_sector_sample=20)
    assert iyi["floor_source"] == "SECTOR_QUANTILE"
    assert iyi["sd_roe_effective"] == pytest.approx(0.010)
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, sector_residual_scales=[0.01] * 19 + ["oops"],
                                 min_sector_sample=20)


def test_77_atilan_kayit_sayisi_raporlanir():
    """Kac None/NaN atildigi tanida gorunmeli."""
    u = estimate_roe_uncertainty([0.12, None, 0.20, None, 0.28, 0.32],
                                 sector_residual_scales=[0.01] * 20 + [None, float("nan")],
                                 min_sector_sample=20)
    assert u["roe_missing_count"] == 2
    assert u["scales_missing_count"] == 2


# ---------------------------------------------- v4.7.10: numpy bool_ varyanti
np_mod = pytest.importorskip("numpy")


@pytest.mark.parametrize("alan,hata", [
    ("bvps", "INVALID_BVPS"), ("coe", "INVALID_COE"),
    ("macro_cap", "INVALID_MACRO_CAP"), ("sd_roe", "INVALID_SD_ROE"),
    ("d_coe", "INVALID_D_COE"), ("d_g", "INVALID_D_G"),
    ("max_halfwidth", "INVALID_MAX_HALFWIDTH"), ("payout_sus", "INVALID_PAYOUT"),
])
@pytest.mark.parametrize("deger", [True, False])
def test_78_numpy_bool_sayisal_parametre_olamaz(alan, hata, deger):
    """
    isinstance(np.bool_(True), bool) FALSE oldugu icin Python bool icin kapatilan
    sessiz hata NumPy True/False ile devam ediyordu. En belirgin ornek:
    macro_cap=np.bool_(False) -> 0.0 kabul edilip V_mid 6.89 yerine 12.32 oluyordu.
    """
    r = _bv(**{alan: np_mod.bool_(deger)})
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == hata


@pytest.mark.parametrize("deger", [True, False])
def test_79_numpy_bool_roe_olamaz(deger):
    seri = [np_mod.bool_(deger)] * 6
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(seri)
    assert _bv(roe_ttm_series=seri)["reason"] == "INVALID_ROE_SERIES"


@pytest.mark.parametrize("deger", [True, False])
def test_80_numpy_bool_sektor_olcegi_olamaz(deger):
    """[np.bool_(True)]*20 -> sektor tabani 1.0, sd_roe_effective 1.0 oluyordu."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, sector_residual_scales=[np_mod.bool_(deger)] * 20,
                                 min_sector_sample=20)


def test_81_numpy_bool_konfigurasyon_alanlarinda_da_reddedilir():
    """Konfigurasyon JSON/env'den gelir; siki politika: yalniz Python bool."""
    assert _bv(sd_roe=0.0586,
               band_width_shadow_mode=np_mod.bool_(True))["reason"] == \
        "INVALID_BAND_WIDTH_SHADOW_MODE"
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, dof_correction=np_mod.bool_(True))


def test_82_numpy_sayisal_tipler_ETKILENMEZ():
    """np.float64/np.int64 gecerli sayilardir; yalniz bool skalerleri reddedilir."""
    from roe_uncertainty import is_bool_like
    assert is_bool_like(np_mod.bool_(True)) and is_bool_like(True)
    assert not is_bool_like(np_mod.float64(1.5))
    assert not is_bool_like(np_mod.int64(3))
    r = _bv(bvps=np_mod.float64(BVPS), sd_roe=np_mod.float64(SD_OK))
    assert r["status"] == OK
    assert r["V_mid"] == pytest.approx(_bv()["V_mid"])
    u = estimate_roe_uncertainty([np_mod.float64(x) for x in BNK1])
    assert u["sd_roe_effective"] == pytest.approx(
        estimate_roe_uncertainty(BNK1)["sd_roe_effective"])


# ---------------------------------------------- v4.7.11: pd.NA ve OverflowError
pd_mod = pytest.importorskip("pandas")
HUGE = 10 ** 10000


def test_83_pd_na_payout_varsayilana_duser():
    """PostgreSQL -> pandas: nullable Float64'te eksik deger NaN DEGIL pd.NA'dir.
    Once INVALID_PAYOUT donuyordu; varsayilan kullanilmaliydi."""
    r = _bv(payout_sus=pd_mod.NA)
    assert r["status"] == OK
    assert r["payout_defaulted"] is True
    assert r["V_mid"] == pytest.approx(_bv(payout_sus=None)["V_mid"])


def test_84_pd_na_nullable_sutundan_gelirse_de_calisir():
    kolon = pd_mod.array([1.0, None], dtype="Float64")
    r = _bv(payout_sus=kolon[1])
    assert r["status"] == OK and r["payout_defaulted"] is True


def test_85_pd_na_roe_zaman_yuvasini_korur():
    """Once ValueError ile yukleme kiriliyordu."""
    u = estimate_roe_uncertainty([0.12, pd_mod.NA, 0.20, pd_mod.NA, 0.28, 0.32])
    assert u["trend_slope"] == pytest.approx(0.0400, abs=1e-9)
    assert u["n_valid"] == 4 and u["roe_missing_count"] == 2
    r = _bv(roe_ttm_series=[0.156, pd_mod.NA, 0.1952, 0.2346, 0.2689, 0.2809, 0.30])
    assert r["status"] in (OK, INSUFFICIENT)
    assert r.get("reason") != "INVALID_ROE_SERIES"


def test_86_pd_na_sektor_olceginde_eksik_sayilir():
    u = estimate_roe_uncertainty(SABIT,
                                 sector_residual_scales=[0.01] * 20 + [pd_mod.NA],
                                 min_sector_sample=20)
    assert u["floor_source"] == "SECTOR_QUANTILE"
    assert u["scales_missing_count"] == 1


@pytest.mark.parametrize("alan,hata", [
    ("bvps", "INVALID_BVPS"), ("coe", "INVALID_COE"),
    ("macro_cap", "INVALID_MACRO_CAP"), ("sd_roe", "INVALID_SD_ROE"),
    ("d_coe", "INVALID_D_COE"), ("d_g", "INVALID_D_G"),
    ("max_halfwidth", "INVALID_MAX_HALFWIDTH"), ("payout_sus", "INVALID_PAYOUT"),
])
def test_87_cok_buyuk_tamsayi_kontrollu_reddedilir(alan, hata):
    """10**10000 kontrolsuz OverflowError uretiyordu (float() sadece TypeError/
    ValueError yakaliyordu)."""
    r = _bv(**{alan: HUGE})
    assert r["status"] == INSUFFICIENT
    assert r["reason"] == hata


def test_88_cok_buyuk_tamsayi_seri_ve_olcekte():
    with pytest.raises(ValueError):
        estimate_roe_uncertainty([0.12, HUGE, 0.20, 0.24, 0.28, 0.32])
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, sector_residual_scales=[0.01] * 19 + [HUGE],
                                 min_sector_sample=20)
    assert _bv(roe_ttm_series=[0.12, HUGE, 0.20, 0.24, 0.28, 0.32])["reason"] == \
        "INVALID_ROE_SERIES"


@pytest.mark.parametrize("kw", [
    {"absolute_floor": HUGE}, {"sector_quantile": HUGE},
    {"outlier_multiplier": HUGE}, {"outlier_absolute_floor": HUGE},
])
def test_89_buyuk_tamsayi_kalibrasyon_parametrelerinde(kw):
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, **kw)


def test_90_hicbir_girdide_kontrolsuz_overflow():
    """Genel sozlesme: OverflowError disariya sizmaz."""
    for kw in ({"bvps": HUGE}, {"coe": HUGE}, {"sd_roe": HUGE}, {"payout_sus": HUGE},
               {"roe_ttm_series": [HUGE] * 6}):
        try:
            r = _bv(**kw)
        except OverflowError:
            pytest.fail(f"kontrolsuz OverflowError: {list(kw)}")
        assert r["status"] == INSUFFICIENT and r["reason"]


def test_91_numpy_int64_konfigurasyon_alaninda_reddedilir():
    """SOZLESME NOTU: np.int64 SAYISAL alanlarda gecerli, KONFIGURASYON alaninda degil.
    min_sector_sample yalniz Python int kabul eder (siki politika)."""
    with pytest.raises(ValueError):
        estimate_roe_uncertainty(SABIT, min_sector_sample=np_mod.int64(20))
    # ama sayisal alanlarda np.int64 sorunsuz
    r = _bv(bvps=np_mod.int64(21))
    assert r["status"] == OK


# ---------------------------------------------- v4.7.12: wrapper kwargs tipi
@pytest.mark.parametrize("bad", ["abc", [], [1, 2], 5, ("a", "b")])
def test_92_wrapper_kwargs_mapping_olmali(bad):
    """[] falsy oldugu icin sessizce {} kabul ediliyor, dolu liste cokuyordu."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False,
               uncertainty_kwargs=bad)
    assert r["status"] == INSUFFICIENT and r["reason"] == "INVALID_KWARGS"
    r2 = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False,
                valuation_kwargs=bad)
    assert r2["status"] == INSUFFICIENT and r2["reason"] == "INVALID_KWARGS"


def test_93_wrapper_kwargs_none_ve_mapping_calisir():
    a = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False)
    b = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False,
               uncertainty_kwargs={}, valuation_kwargs={})
    assert a["V_mid"] == pytest.approx(b["V_mid"])
    c = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False,
               uncertainty_kwargs={"dof_correction": True})
    assert c["uncertainty"]["dof_corrected"] is True


# ---------------------------------------------- v4.7.13: wrapper beyaz liste
@pytest.mark.parametrize("kw", [
    {"uncertainty_kwargs": {"sector_residual_scales": [0.01] * 20}},
    {"uncertainty_kwargs": {"roe_series": [0.1] * 6}},
    {"uncertainty_kwargs": {"foo": 1}},
    {"valuation_kwargs": {"sd_roe": 0.02}},
    {"valuation_kwargs": {"band_width_shadow_mode": True}},
    {"valuation_kwargs": {"bvps": 10.0}},
    {"valuation_kwargs": {"bar": 2}},
])
def test_94_wrapper_cakisan_ve_bilinmeyen_anahtar(kw):
    """Sarumun acikca verdigi parametreler tekrar gelince 'multiple values'
    TypeError'i olusuyordu; bilinmeyen anahtar 'unexpected keyword' veriyordu."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False, **kw)
    assert r["status"] == INSUFFICIENT and r["reason"] == "INVALID_KWARGS"


def test_95_wrapper_izinli_anahtarlar_calisir():
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False,
               uncertainty_kwargs={"dof_correction": True, "absolute_floor": 0.004},
               valuation_kwargs={"d_coe": 0.04, "max_halfwidth": 1.2})
    assert r["status"] == OK and r["uncertainty"]["dof_corrected"] is True


def test_96_beyaz_listeler_imzalarla_TAM_ESLESIR():
    """
    ONCEKI HALI YETERSIZDI: `ALLOWED <= set(imza)` yalniz alt kume kontrolu yapar.
    Fonksiyona yeni parametre eklenip beyaz listeye eklenmezse test GECERDI.
    Esitlik kontrolu hem eskimis anahtari hem unutulan yeni anahtari yakalar.
    """
    import inspect
    from bank_v47 import UNCERTAINTY_ALLOWED, VALUATION_ALLOWED
    from roe_uncertainty import estimate_roe_uncertainty as _e

    # sarumun ACIKCA verdigi parametreler beyaz listede olmamali
    uncertainty_managed = {"roe_series", "sector_residual_scales"}
    valuation_managed = {"bvps", "roe_ttm_series", "coe", "macro_cap",
                         "payout_sus", "sd_roe", "band_width_shadow_mode"}

    assert UNCERTAINTY_ALLOWED == (
        set(inspect.signature(_e).parameters) - uncertainty_managed)
    assert VALUATION_ALLOWED == (
        set(inspect.signature(bank_valuation).parameters) - valuation_managed)


@pytest.mark.parametrize("kwargs", [
    {"uncertainty_kwargs": {1: 2}},
    {"uncertainty_kwargs": {1: 2, "x": 3}},
    {"valuation_kwargs": {None: 2}},
    {"valuation_kwargs": {(1, 2): 3}},
])
def test_97_wrapper_kwargs_anahtarlari_string_olmali(kwargs):
    """sorted(unknown) karisik turde anahtarlarda kontrolsuz TypeError veriyordu."""
    r = bv_est(BVPS, BNK1, COE, MACRO, PAYOUT, band_width_shadow_mode=False, **kwargs)
    assert r["status"] == INSUFFICIENT and r["reason"] == "INVALID_KWARGS"
