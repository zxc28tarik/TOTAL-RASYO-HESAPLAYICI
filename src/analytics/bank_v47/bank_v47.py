"""bank_valuation — sd_roe ZORUNLU parametre (v4.7.1)."""
from __future__ import annotations
import math
from collections.abc import Mapping
from statistics import median
from typing import Optional, Sequence
try:
    from .spec_v46 import (apply_wing_floor, PAYOUT_DEFAULT, MIN_COE_GAP, D_COE, D_G,
                           MAX_LOG_VALUE_HALFWIDTH, INSUFFICIENT, OK)
    from .roe_uncertainty import (estimate_roe_uncertainty, _finite, coerce_finite_number,
                                  require_bool, coerce_roe_series, is_missing_like)
except ImportError:  # standalone test-package compatibility
    from spec_v46 import (apply_wing_floor, PAYOUT_DEFAULT, MIN_COE_GAP, D_COE, D_G,
                           MAX_LOG_VALUE_HALFWIDTH, INSUFFICIENT, OK)
    from roe_uncertainty import (estimate_roe_uncertainty, _finite, coerce_finite_number,
                                 require_bool, coerce_roe_series, is_missing_like)


def _is_nan(x) -> bool:
    """Geriye uyumluluk sarumu; asil tespit roe_uncertainty.is_missing_like."""
    return is_missing_like(x)


def bank_valuation(bvps, roe_ttm_series, coe, macro_cap, payout_sus=None,
                   *, sd_roe, d_coe=D_COE, d_g=D_G,
                   max_halfwidth=MAX_LOG_VALUE_HALFWIDTH,
                   band_width_shadow_mode):
    """
    sd_roe ZORUNLUDUR (keyword-only). Ic hesap yolu YOKTUR — unutulan parametre
    sessizce eski davranisi calistiramaz. Gecersiz deger YETERSIZ_VERI uretir.
    """
    _kanonik = {}
    # --- GIRIS KAPISI ---
    # Tum sayisal parametreler ORTAK yardimcidan gecer. Ayni hata sinifi (yeni esik
    # parametresi eklerken dogrulamayi atlamak) uc kez tekrarlandi: absolute_floor=NaN,
    # max_halfwidth=NaN, band_width_shadow_mode="false". Ortak yardimci bunu yapisal
    # olarak onler; bool ozellikle reddedilir (bvps=True -> 1.0 gecerli gorunuyordu).
    for isim, deger, kural in (
        ("sd_roe", sd_roe, dict(minimum=0.0)),
        ("bvps", bvps, dict(minimum=0.0, strict_minimum=True)),
        ("coe", coe, dict(minimum=0.0, strict_minimum=True)),
        ("macro_cap", macro_cap, dict(minimum=0.0)),
        ("d_coe", d_coe, dict(minimum=0.0)),
        ("d_g", d_g, dict(minimum=0.0)),
        ("max_halfwidth", max_halfwidth, dict(minimum=0.0, strict_minimum=True)),
    ):
        try:
            _kanonik[isim] = coerce_finite_number(isim, deger, **kural)
        except ValueError:
            return dict(status=INSUFFICIENT, reason="INVALID_" + isim.upper())
    sd_roe = _kanonik["sd_roe"]
    bvps = _kanonik["bvps"]
    coe = _kanonik["coe"]
    macro_cap = _kanonik["macro_cap"]
    d_coe = _kanonik["d_coe"]
    d_g = _kanonik["d_g"]
    max_halfwidth = _kanonik["max_halfwidth"]
    try:
        band_width_shadow_mode = require_bool("band_width_shadow_mode",
                                              band_width_shadow_mode)
    except ValueError:
        return dict(status=INSUFFICIENT, reason="INVALID_BAND_WIDTH_SHADOW_MODE")

    # bytes b"abcdef" -> [97,98,...] olarak gecerli ROE'ler saniliyordu (roe_sus=99.5)
    try:
        roe_list, _ = coerce_roe_series("roe_ttm_series", roe_ttm_series)
    except ValueError:
        return dict(status=INSUFFICIENT, reason="INVALID_ROE_SERIES")
    valid_q = [float(r) for r in roe_list if _finite(r)]
    if len(valid_q) < 6:
        return dict(status=INSUFFICIENT, reason="ROE_QUARTERS_LT_6")

    s = sorted(valid_q)
    roe_sus = median(s[1:-1])

    # EKSIK (None/NaN) ile GECERSIZ (inf, aralik disi) ayrilir:
    # pandas'tan gelen eksik payout None degil NaN olur ve varsayilan uygulanmazdi.
    if is_missing_like(payout_sus):      # None, pd.NA, NaN -> varsayilan
        payout_used, payout_defaulted = PAYOUT_DEFAULT, True
    else:
        try:
            # bool ozellikle reddedilir: payout_sus=True -> 1.00 gecerli gorunuyordu
            payout_used = coerce_finite_number("payout_sus", payout_sus)
        except ValueError:
            return dict(status=INSUFFICIENT, reason="INVALID_PAYOUT")
        payout_defaulted = False
    if not 0.0 <= payout_used <= 1.0:
        return dict(status=INSUFFICIENT, reason="PAYOUT_OUT_OF_RANGE")
    cands = {"PAYOUT": (1 - payout_used) * roe_sus, "MACRO_CAP": macro_cap,
             "COE_GAP": coe - MIN_COE_GAP}
    g_raw = min(cands.values())
    binding = {k for k, v in cands.items() if abs(v - g_raw) < 1e-9}
    # Kose taramasi buyumeyi max(..., 0.0) ile tabanliyor; MERKEZ de ayni kurali
    # kullanmali, yoksa merkez ve band farkli ekonomik varsayimla hesaplanir.
    # (COE < MIN_COE_GAP yolundan da negatif g gelebiliyor.)
    g = max(g_raw, 0.0)
    if g_raw < 0.0:
        binding = {"ZERO_FLOOR"}

    if coe - g < MIN_COE_GAP - 1e-12 or roe_sus <= g:
        return dict(status=INSUFFICIENT, reason="CENTER_INVALID")
    v_mid = bvps * (roe_sus - g) / (coe - g)

    vals, n_total = [], 0
    tier_valid = {"low": 0, "mid": 0, "high": 0}
    unique_inputs = set()
    for tier, r in (("low", roe_sus - sd_roe), ("mid", roe_sus), ("high", roe_sus + sd_roe)):
        for c in (coe - d_coe, coe, coe + d_coe):
            for sh in (-d_g, 0.0, +d_g):
                n_total += 1
                gs = max(min(g + sh, macro_cap, c - MIN_COE_GAP), 0.0)
                if c - gs >= MIN_COE_GAP - 1e-12 and r > gs:
                    vals.append(bvps * (r - gs) / (c - gs))
                    tier_valid[tier] += 1
                    unique_inputs.add((round(r, 12), round(c, 12), round(gs, 12)))
    if len(vals) < 6:
        return dict(status=INSUFFICIENT, reason="CORNERS_LT_6", n_valid=len(vals))
    if len(unique_inputs) < 6:
        return dict(status=INSUFFICIENT, reason="UNIQUE_CORNERS_LT_6",
                    n_valid=len(vals), n_unique=len(unique_inputs))

    # MODEL TANIM ALANI: roe_sus - sd_roe <= g ise asagi senaryoda banka kendi
    # buyume oranini bile surdurememektedir ve gerekcelendirilmis PD/DD tanimsizdir.
    # Bu kosede band kurmak yerine (kirpma tabani hayatta kalan kosslerden DAHA
    # YUKSEK olabildigi icin belirsizlik artarken band DARALIYORDU) modelin
    # uygulanamadigi acikca soylenir.
    if tier_valid["low"] == 0:
        return dict(status=INSUFFICIENT, reason="SD_ROE_EXCEEDS_MODEL_DOMAIN",
                    n_valid=len(vals), corner_tiers=dict(tier_valid),
                    roe_sus=roe_sus, sd_roe_used=sd_roe, g=g)
    if tier_valid["high"] == 0:
        return dict(status=INSUFFICIENT, reason="HIGH_ROE_TIER_EMPTY",
                    n_valid=len(vals), corner_tiers=dict(tier_valid))

    corner_flags = []
    if tier_valid["low"] < 9 or tier_valid["high"] < 9:
        corner_flags.append("PARTIAL_CORNER_LOSS")

    band = apply_wing_floor(v_mid, min(vals), max(vals))
    if band.get("status") == OK:
        # KULLANILABILIRLIK TAVANI. Kose taramasi + gecerlilik filtresi tasarimi
        # geregi, sd_roe (roe_sus - g)'ye yaklasirken asagi kose degerlemesi sifira
        # gider, sonra tanim alanindan cikip listeden duser. Bu yuzden band genisligi
        # bu bolgede MONOTON DEGIL: 40x'ten 11x'e dusebiliyor. Cozum bandi zorla
        # monotonlastirmak degil, kullanilamayacak kadar genis bandi reddetmek.
        too_wide = any(hw > max_halfwidth + 1e-12
                       for hw in (band["lower_halfwidth"], band["upper_halfwidth"]))
        # GOLGE MOD: ilk canli calismada oran olculur, otomatik ret YAPILMAZ.
        if too_wide and not band_width_shadow_mode:
            return dict(status=INSUFFICIENT, reason="BAND_TOO_WIDE",
                        lower_halfwidth=band["lower_halfwidth"],
                        upper_halfwidth=band["upper_halfwidth"],
                        n_valid=len(vals), corner_tiers=dict(tier_valid),
                        sd_roe_used=sd_roe)
        band["would_be_band_too_wide"] = bool(too_wide)
    # --- CIKIS KAPISI (savunma katmani) ---
    # Ileride bir giris kontrolu unutulsa bile NaN iceren OK sonucu uretilemez.
    # implied/payout_gap KAPIDAN ONCE hesaplanir ki denetime dahil olsunlar.
    if roe_sus <= 0:
        return dict(status=INSUFFICIENT, reason="INVALID_SUSTAINABLE_ROE")
    implied = 1 - g / roe_sus
    payout_gap_val = implied - payout_used

    gerekli = [v_mid, band.get("V_low"), band.get("V_high"),
               band.get("lower_halfwidth"), band.get("upper_halfwidth"),
               g, roe_sus, implied, payout_gap_val]
    if not all(_finite(x) for x in gerekli):
        return dict(status=INSUFFICIENT, reason="NONFINITE_VALUATION_OUTPUT")
    if not (band["V_low"] > 0 and v_mid > 0 and band["V_high"] > 0
            and band["V_low"] <= v_mid <= band["V_high"]):
        return dict(status=INSUFFICIENT, reason="INVALID_VALUATION_GEOMETRY")
    if not 0.0 <= implied <= 1.0:
        return dict(status=INSUFFICIENT, reason="IMPLIED_PAYOUT_OUT_OF_RANGE")

    band.update(
        status=OK, method="BANK_JUSTIFIED_PB",
        n_valid=len(vals), n_total=n_total, n_unique=len(unique_inputs),
        corner_tiers=dict(tier_valid), roe_sus=roe_sus,
        sd_roe=sd_roe,          # v4.6 sozlesmesi: gecis suresince KORUNUR
        sd_roe_used=sd_roe,     # daha aciklayici yeni ad
        g=g, growth_binding_source=binding,
        implied_payout=implied,
        payout_gap=payout_gap_val,
        payout_defaulted=payout_defaulted,
    )
    band["flags"] = list(band.get("flags", [])) + corner_flags
    if corner_flags:
        band["corner_conf_penalty"] = 0.70
    return band


# Sarumun ACIKCA verdigi parametreler tekrar sozlukte gelirse "multiple values"
# TypeError'i olusuyordu; bilinmeyen anahtar da "unexpected keyword" veriyordu.
# Beyaz liste ikisini de kontrollu rede cevirir.
UNCERTAINTY_ALLOWED = frozenset({
    "min_sector_sample", "sector_quantile", "absolute_floor",
    "dof_correction", "outlier_absolute_floor", "outlier_multiplier",
})
VALUATION_ALLOWED = frozenset({"d_coe", "d_g", "max_halfwidth"})


def _canonical_kwargs(name, value, allowed) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    out = dict(value)
    # sorted(unknown) karisik turde anahtarlarda TypeError veriyordu
    if any(not isinstance(k, str) for k in out):
        raise ValueError(f"{name} keys must be strings")
    unknown = set(out) - set(allowed)
    if unknown:
        raise ValueError(f"{name} has unsupported keys: {sorted(unknown)}")
    return out


def bank_valuation_with_estimated_uncertainty(
    bvps, roe_ttm_series, coe, macro_cap, payout_sus=None,
    sector_residual_scales=None, *, band_width_shadow_mode,
    uncertainty_kwargs=None, valuation_kwargs=None,
):
    """
    GECICI SARUM -- uretim hatti iki adimli cagriya gectikten sonra KALDIRILMALIDIR.

    Uretimde kullanilacak sekil:
        u = estimate_roe_uncertainty(roe_series, sector_residual_scales=...)
        r = bank_valuation(..., sd_roe=u["sd_roe_effective"],
                           band_width_shadow_mode=...)
        v_conf = tier_cap * payout_f * u["conf_penalty"] * r.get("corner_conf_penalty", 1.0)

    Iki fonksiyonun parametreleri AYRI sozluklerle gecirilir; tek bir **kw
    kullanmak belirsizlik parametrelerini yanlislikla degerleme motoruna
    yonlendiriyordu (ornegin dof_correction -> TypeError).

    `conf_penalty` (uc deger cezasi) cagirana dondurulur; V_conf'a ORADA uygulanir.
    """
    # Seri IKI kez kullanildigi icin bir kez materyalize edilir; iterator/generator
    # verilirse ilk cagri seriyi tuketip ikinciye bos seri birakiyordu.
    try:
        roe_values, _ = coerce_roe_series("roe_ttm_series", roe_ttm_series)
    except ValueError:
        return dict(status=INSUFFICIENT, reason="INVALID_ROE_SERIES")

    # Falsy kontrolu ([] gibi bos koleksiyonu sessizce {} kabul ediyordu; dolu
    # liste veya string ise kontrolsuz TypeError uretiyordu).
    try:
        u_kw = _canonical_kwargs("uncertainty_kwargs", uncertainty_kwargs,
                                 UNCERTAINTY_ALLOWED)
        v_kw = _canonical_kwargs("valuation_kwargs", valuation_kwargs,
                                 VALUATION_ALLOWED)
    except ValueError:
        return dict(status=INSUFFICIENT, reason="INVALID_KWARGS")

    u = estimate_roe_uncertainty(
        roe_values, sector_residual_scales=sector_residual_scales, **u_kw,
    )
    r = bank_valuation(bvps, roe_values, coe, macro_cap, payout_sus,
                       sd_roe=u["sd_roe_effective"],
                       band_width_shadow_mode=band_width_shadow_mode, **v_kw)
    if r.get("status") == OK:
        r["uncertainty"] = u          # tani bloku; skora girmez
    return r
