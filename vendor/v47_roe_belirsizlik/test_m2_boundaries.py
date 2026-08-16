"""
M2 v4.6 sinir durumu test paketi.

Her test v4.6 belgesindeki bir maddeye baglidir; kirilinca hangi kuralin
ihlal edildigi dogrudan gorunur. Motor yazildiginda `spec_v46` yerine
gercek modul import edilir; beklenen degerler ayni kalir.
"""
import math
import pytest
from spec_v46 import (
    calibration, return_band, lag_axis, tier_of, apply_wing_floor,
    bank_valuation, nav_valuation, ev_valuation, valuation_score, m2,
    MIN_DAILY_RESIDUAL_SIGMA, MIN_LOG_VALUE_HALFWIDTH, MAX_LOG_VALUE_HALFWIDTH,
    CONF_MIN, LAG_RELIABILITY_MIN, PAYOUT_DEFAULT, INSUFFICIENT, OK,
)

P0 = [18.0, 19.5, 17.2, 21.0, 20.4, 22.1, 19.8, 20.0]
AL = [0.07, 0.09, 0.06, 0.10, 0.08, 0.11, 0.07, 0.08]
FL = [1.04, 0.97, 1.11, 0.92, 1.02, 0.88, 1.06, 0.95]


# ============================================ §1 kalibrasyon
def test_belge_ornegi_birebir():
    """v4.6 §8. Genisleyen pencere kurali (uygulama notu 3)."""
    c = calibration(P0, AL, FL)
    assert c["model_error"] == pytest.approx(0.067347, abs=1e-6)
    assert c["base_error"] == pytest.approx(0.061216, abs=1e-6)
    assert c["raw_skill"] == 0.0
    assert c["sample_weight"] == pytest.approx(0.5)
    assert c["band_reliability"] == 0.0
    assert c["n_eval"] == 4


def test_son4_penceresi_farkli_sonuc_verir():
    """Pencere kurali onemli: sadece son 4 kullanilsa 0.0548 cikardi."""
    assert calibration(P0, AL, FL)["model_error"] != pytest.approx(0.054781, abs=1e-6)


def test_base_error_sifirsa_raw_skill_sifir():
    """v4.6 §1 D3. Naif baz da model de hatasizsa TAM BECERI sanilmamali."""
    p0 = [20.0] * 10
    al = [0.0] * 10
    fl = [1.0] * 10          # P63 == p0 == mid -> her iki hata da 0
    c = calibration(p0, al, fl)
    assert c["base_error"] == pytest.approx(0.0, abs=1e-12)
    assert c["raw_skill"] == 0.0, "base_error<=eps iken raw_skill 1 olmamali"


def test_dort_donemden_az_ise_guvenilirlik_sifir():
    c = calibration(P0[:5], AL[:5], FL[:5])
    assert c["n_eval"] < 4 and c["band_reliability"] == 0.0


def test_mukemmel_model_tam_beceri():
    """Kontrol: model her donem tam isabet -> raw_skill 1."""
    p0 = [20.0] * 12
    al = [0.08] * 12
    fl = [1.0] * 12          # P63 == mid, ama p0 != mid -> base_error > 0
    c = calibration(p0, al, fl)
    assert c["raw_skill"] == pytest.approx(1.0)
    assert c["sample_weight"] == pytest.approx(1.0)


# ============================================ §1-2 getiri bandi
def test_sifir_volatilite_z_patlamaz():
    """v4.6 §1 D3. sigma_daily=0 -> taban devreye girer."""
    rb = return_band(20.0, 0.08, 0.0, 0.0)
    assert rb["sigma_daily_used"] == MIN_DAILY_RESIDUAL_SIGMA
    la = lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], 43, 1.0)
    assert math.isfinite(la["z_return"])


def test_vade_sifira_giderken_z_sinirli():
    """v4.6 §2. sqrt(max(remaining,10)) tabani."""
    rb = return_band(20.0, 0.08, 0.02, 0.0)
    zs = [lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], r, 1.0)["z_return"]
          for r in (10, 5, 1)]
    assert zs[0] == pytest.approx(zs[1]) == pytest.approx(zs[2]), "10 gun altinda sigma sabit kalmali"


def test_band_suresi_dolunca_lag_kapanir():
    rb = return_band(20.0, 0.08, 0.02, 0.0)
    la = lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], 0, 1.0)
    assert la["lag_active"] is False and la["lag_usable"] is False
    assert la["s_lag_effective"] == 0.5


def test_bias_tek_kez_uygulanir():
    """v4.6 §2. Merkez kalibre oldugu icin z formulunde ayrica bias YOK."""
    bias = -0.05
    rb = return_band(20.0, 0.08, 0.02, bias)
    assert rb["mid"] == pytest.approx(rb["mid_raw"] * math.exp(bias))
    la = lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], 43, 1.0)
    beklenen = math.log(rb["mid"] / 17.4) / (rb["sigma_daily_used"] * math.sqrt(43))
    assert la["z_return"] == pytest.approx(beklenen)


def test_yayimlanan_band_skorla_ayni_merkez():
    """v4.6 §1 D2. Ekran-skor tutarliligi."""
    rb = return_band(20.0, 0.08, 0.02, -0.05)
    assert rb["low"] < rb["mid"] < rb["high"]
    assert rb["mid"] != rb["mid_raw"], "yayimlanan merkez kalibre olmali"


def test_lag_usable_guvenilirlige_bagli():
    """v4.6 §2 D5. Vade dolu ama beceri sifirsa uygunluk kapisi ACILMAMALI."""
    rb = return_band(20.0, 0.08, 0.02, 0.0)
    la = lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], 43, 0.0)
    assert la["lag_active"] is True
    assert la["lag_usable"] is False
    la2 = lag_axis(rb["mid"], 17.4, rb["sigma_daily_used"], 43, LAG_RELIABILITY_MIN)
    assert la2["lag_usable"] is True


# ============================================ §3 kademe sinirlari
@pytest.mark.parametrize("n,beklenen", [
    (3, "D"), (4, "C"), (7, "C"), (8, "B"), (19, "B"), (20, "A"), (100, "A"),
])
def test_kademe_sinirlari(n, beklenen):
    assert tier_of(n) == beklenen


# ============================================ §6 kanat / sinir durumlari
def test_negatif_alt_sinir_kesilir():
    """v4.6 §6.1 D5. Agir borclu sirkette V_low_raw<0 -> ln tanimsiz olmamali."""
    r = ev_valuation(multiple=7.5, driver_value=4500, net_debt=30000, shares=1200,
                     half_width_mult=0.20)
    assert r["status"] == OK
    assert "LOWER_BOUND_TRUNCATED" in r["flags"]
    assert r["V_low"] > 0 and math.isfinite(r["lower_halfwidth"])
    assert r["lower_halfwidth"] == pytest.approx(MAX_LOG_VALUE_HALFWIDTH)


def test_negatif_merkez_yetersiz_veri():
    r = ev_valuation(multiple=1.0, driver_value=4500, net_debt=90000, shares=1200,
                     half_width_mult=0.20)
    assert r["status"] == INSUFFICIENT


def test_kanat_tabani_dar_bandi_acar():
    r = apply_wing_floor(v_mid=10.0, v_low_raw=9.9, v_high_raw=10.1)
    assert r["lower_halfwidth"] == pytest.approx(MIN_LOG_VALUE_HALFWIDTH)
    assert r["V_low"] == pytest.approx(10.0 * math.exp(-MIN_LOG_VALUE_HALFWIDTH))
    assert r["V_low_raw"] == 9.9, "ham deger tani icin korunmali"


def test_genis_band_tabandan_etkilenmez():
    r = apply_wing_floor(v_mid=10.0, v_low_raw=6.0, v_high_raw=17.0)
    assert r["V_low"] == pytest.approx(6.0) and r["V_high"] == pytest.approx(17.0)


# ============================================ §5 banka
def test_banka_belge_ornegi():
    """v4.6 §8 BANKA-A. P_t=6.00 (uygulama notu)."""
    roe = [0.32] * 8
    r = bank_valuation(bvps=12.0, roe_ttm_series=roe, coe=0.366,
                       macro_cap=83132 / 72915 - 1, payout_sus=0.25)
    assert r["status"] == OK
    assert r["V_mid"] == pytest.approx(9.5561, abs=1e-3)
    assert r["n_valid"] == 27 and r["n_total"] == 27
    assert r["growth_binding_source"] == {"MACRO_CAP"}
    assert r["implied_payout"] == pytest.approx(0.5621, abs=1e-3)


def test_banka_sd_roe_kucukse_taban_baglar():
    """v4.6 §8. sd_roe~0 -> ham band daralir, taban devreye girer."""
    roe = [0.32, 0.3202, 0.3198, 0.32, 0.3201, 0.3199, 0.32, 0.32]
    r = bank_valuation(bvps=12.0, roe_ttm_series=roe, coe=0.366,
                       macro_cap=83132 / 72915 - 1, payout_sus=0.25)
    assert r["lower_halfwidth"] >= MIN_LOG_VALUE_HALFWIDTH
    assert r["V_low"] < r["V_low_raw"], "taban yayimlanan bandi genisletmeli"


def test_banka_alti_ceyrekten_az_yetersiz():
    r = bank_valuation(12.0, [0.3] * 5, 0.366, 0.14, 0.25)
    assert r["status"] == INSUFFICIENT and r["reason"] == "ROE_QUARTERS_LT_6"


def test_banka_roe_coe_altinda_pb_birden_kucuk():
    r = bank_valuation(12.0, [0.20] * 8, 0.366, 0.14, 0.25)
    assert r["status"] == OK and r["V_mid"] < 12.0


def test_banka_payout_varsayilani_isaretlenir():
    r = bank_valuation(12.0, [0.32] * 8, 0.366, 0.14, payout_sus=None)
    assert r["payout_defaulted"] is True
    assert r["payout_gap"] == pytest.approx(r["implied_payout"] - PAYOUT_DEFAULT)


def test_banka_coklu_kisit_baglayabilir():
    """v4.6 §5. growth_binding_source bir KUME; kayan nokta == kullanilmaz."""
    coe, macro = 0.366, 0.316            # macro == coe - MIN_COE_GAP
    # payout dali baglamasin: (1-0.30)*0.50 = 0.35 > 0.316
    r = bank_valuation(12.0, [0.50] * 8, coe, macro, payout_sus=0.30)
    assert {"MACRO_CAP", "COE_GAP"} <= r["growth_binding_source"]


def test_banka_roe_g_altindaysa_yetersiz():
    r = bank_valuation(12.0, [0.05] * 8, 0.366, 0.14, 0.0)
    assert r["status"] == INSUFFICIENT


# ============================================ §6.2 NAV
def test_nav_siralama_ihlali_kirpilir():
    """v4.6 §6.2 D4. hedef %40, emsal %10-%30 -> merkez alt sinirin ALTINDA kalirdi."""
    r = nav_valuation(nav_per_share=100.0, target_discount=0.40,
                      peer_discounts=[0.10, 0.15, 0.20, 0.25, 0.30])
    assert "NAV_DISCOUNT_CLIPPED" in r["flags"]
    assert r["V_low_raw"] <= r["V_mid"] <= r["V_high_raw"]


def test_nav_tutarli_siralamada_kirpma_yok():
    r = nav_valuation(100.0, 0.20, peer_discounts=[0.10, 0.15, 0.20, 0.25, 0.30])
    assert "NAV_DISCOUNT_CLIPPED" not in r["flags"]
    assert r["V_mid"] == pytest.approx(80.0)


def test_nav_yetersiz_emsal_fallback():
    r = nav_valuation(100.0, 0.20, peer_discounts=[0.15])
    assert r["V_low_raw"] <= r["V_mid"] <= r["V_high_raw"]


def test_nav_fallback_araliga_kirpilir():
    """discount ~0 iken taban [0,0.95] disina tasmamali."""
    r = nav_valuation(100.0, 0.02, peer_discounts=None)
    assert r["status"] == OK and r["V_high_raw"] <= 100.0


def test_nav_bayat_veri_reddedilir():
    assert nav_valuation(100.0, 0.20, None, freshness_months=24)["status"] == INSUFFICIENT


# ============================================ §7 skor ve M2
def test_sanayi_b_belge_ornegi():
    """v4.6 §8 SANAYI-B."""
    r = ev_valuation(7.525445121, 4500, 6000, 1200, half_width_mult=0.20)
    assert r["V_mid"] == pytest.approx(23.220419, abs=1e-5)
    assert r["V_low"] == pytest.approx(18.104925, abs=1e-4)
    assert r["V_high"] == pytest.approx(29.468498, abs=1e-4)
    s = valuation_score(r["V_mid"], r["V_low"], r["V_high"], 18.50,
                        r["lower_halfwidth"], r["upper_halfwidth"], v_conf=0.35)
    assert s["z_val"] == pytest.approx(0.913253, abs=1e-5)
    assert s["s_valuation"] == pytest.approx(0.728313, abs=1e-5)
    assert s["s_val_effective"] == pytest.approx(0.579910, abs=1e-5)
    assert m2(s["s_val_effective"], OK, 0.35, 0.5, False)["m2"] == pytest.approx(0.547946, abs=1e-5)


def test_band_sinirinda_z_tam_bir():
    """v4.6 §7 kalibrasyon: P_t=V_low -> z=+1, P_t=V_high -> z=-1."""
    r = apply_wing_floor(10.0, 10.0 * math.exp(-0.3), 10.0 * math.exp(0.25))
    assert valuation_score(10.0, r["V_low"], r["V_high"], r["V_low"],
                           r["lower_halfwidth"], r["upper_halfwidth"], 1.0)["z_val"] == pytest.approx(1.0)
    assert valuation_score(10.0, r["V_low"], r["V_high"], r["V_high"],
                           r["lower_halfwidth"], r["upper_halfwidth"], 1.0)["z_val"] == pytest.approx(-1.0)


def test_doyma_yuzde_3297_iskontoda():
    """z_val=2 icin fiyat adil degere gore %32.97 iskontolu olmali."""
    r = apply_wing_floor(10.0, 10.0 * math.exp(-0.2), 10.0 * math.exp(0.2))
    px = 10.0 * math.exp(-0.40)
    s = valuation_score(10.0, r["V_low"], r["V_high"], px,
                        r["lower_halfwidth"], r["upper_halfwidth"], 1.0)
    assert s["z_val"] == pytest.approx(2.0)
    assert s["s_valuation"] == pytest.approx(1.0)
    assert (1 - px / 10.0) == pytest.approx(0.3297, abs=1e-4)


def test_dusuk_guven_notre_ceker():
    s_hi = valuation_score(23.22, 18.10, 29.47, 18.50, 0.2487, 0.2384, v_conf=1.0)
    s_lo = valuation_score(23.22, 18.10, 29.47, 18.50, 0.2487, 0.2384, v_conf=0.35)
    assert abs(s_lo["s_val_effective"] - 0.5) < abs(s_hi["s_val_effective"] - 0.5)


def test_deger_kapisi_duserse_eksen_notrlesir_m2_kaybolmaz():
    """Uygulama notu 4. Deger yoksa lag ekseni CALISMAYA DEVAM eder."""
    r = m2(s_val_effective=0.9, v_status=INSUFFICIENT, v_conf=0.0,
           s_lag_effective=0.8, lag_active=True)
    assert r["valuation_usable"] is False
    assert r["m2"] == pytest.approx(0.5 + 0.40 * 0.3)     # yalniz lag katkisi


def test_conf_min_altinda_deger_notrlesir():
    r = m2(0.9, OK, CONF_MIN - 0.01, 0.5, False)
    assert r["valuation_usable"] is False and r["m2"] == pytest.approx(0.5)


def test_iki_eksen_de_notrse_m2_yarim():
    assert m2(0.5, OK, 1.0, 0.5, True)["m2"] == pytest.approx(0.5)


def test_m2_sinirlarda_kalir():
    assert m2(1.0, OK, 1.0, 1.0, True)["m2"] == pytest.approx(1.0)
    assert m2(0.0, OK, 1.0, 0.0, True)["m2"] == pytest.approx(0.0)
