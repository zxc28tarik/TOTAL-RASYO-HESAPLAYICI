"""
M2 v4.6 — testlerin dayandigi referans uygulama (saf fonksiyonlar).

Bu dosya motorun KENDISI degildir; testlerin bagimsiz bir referansa karsi
kosabilmesi icin v4.6 belgesindeki formullerin birebir cevirisidir.
Gercek motor yazildiginda testler ona baglanir; bu referans capraz kontrol
olarak kalir.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from statistics import median
from typing import Optional, Sequence

# --- v4.6 §9 parametreleri ---
MIN_LOG_MULTIPLE_HALFWIDTH = 0.20
MIN_LOG_VALUE_HALFWIDTH    = 0.20
MAX_LOG_VALUE_HALFWIDTH    = 0.80
MIN_DAILY_RESIDUAL_SIGMA   = 0.005
NAV_DISCOUNT_HALFWIDTH     = 0.15
NAV_MIN_PEER               = 3
PAYOUT_DEFAULT             = 0.30
D_COE, D_G, MIN_COE_GAP    = 0.03, 0.02, 0.05
Z_CAP_RETURN = Z_CAP_VAL   = 2.0
CONF_MIN, LAG_RELIABILITY_MIN = 0.35, 0.20
W_VAL, W_LAG               = 0.60, 0.40
EPS                        = 1e-12

INSUFFICIENT = "YETERSIZ_VERI"
OK = "OK"


def clip(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- kalibrasyon
def calibration(p0: Sequence[float], alpha: Sequence[float], follow: Sequence[float]):
    """v4.6 §1. Genisleyen pencere: k_i = min(8, onceki tamamlanmis), k_i < 4 -> disla."""
    mid = [p * (1 + a) for p, a in zip(p0, alpha)]
    p63 = [f * m for f, m in zip(follow, mid)]
    b = [math.log(x / m) for x, m in zip(p63, mid)]

    cal_err, base_err, idx = [], [], []
    for i in range(len(b)):
        k = min(8, i)
        if k < 4:
            continue
        cal_err.append(abs(b[i] - median(b[i - k:i])))
        base_err.append(abs(math.log(p63[i] / p0[i])))
        idx.append(i)

    n_eval = len(cal_err)
    if n_eval < 4:
        return dict(bias_calib=0.0, model_error=None, base_error=None,
                    raw_skill=0.0, sample_weight=0.0, band_reliability=0.0, n_eval=n_eval)

    model_error = median(cal_err)
    base_error = median(base_err)
    raw_skill = 0.0 if base_error <= EPS else clip(1 - model_error / base_error, 0.0, 1.0)
    sample_weight = clip(n_eval / 8, 0.0, 1.0)
    tail = b[-8:] if len(b) >= 4 else b
    return dict(bias_calib=median(tail), model_error=model_error, base_error=base_error,
                raw_skill=raw_skill, sample_weight=sample_weight,
                band_reliability=raw_skill * sample_weight, n_eval=n_eval)


# ------------------------------------------------------------- getiri / lag
def return_band(p0, alpha, sigma_daily, bias_calib):
    sd = max(sigma_daily, MIN_DAILY_RESIDUAL_SIGMA)
    sh = sd * math.sqrt(63)
    raw = dict(mid=p0 * (1 + alpha), low=p0 * (1 + alpha - sh), high=p0 * (1 + alpha + sh))
    k = math.exp(bias_calib)
    return dict(sigma_daily_used=sd,
                mid_raw=raw["mid"], low_raw=raw["low"], high_raw=raw["high"],
                mid=raw["mid"] * k, low=raw["low"] * k, high=raw["high"] * k)


def lag_axis(mid_calibrated, price, sigma_daily_used, remaining, band_reliability):
    sigma_eff = sigma_daily_used * math.sqrt(max(remaining, 10))
    maturity = clip(remaining / 10, 0.0, 1.0)
    active = remaining > 0
    usable = active and band_reliability >= LAG_RELIABILITY_MIN and maturity > 0
    if not active:
        return dict(lag_active=False, lag_usable=False, s_lag_effective=0.5,
                    z_return=None, maturity_weight=maturity)
    z = math.log(mid_calibrated / price) / sigma_eff
    s = clip(0.5 + z / (2 * Z_CAP_RETURN), 0.0, 1.0)
    conf = band_reliability * maturity
    return dict(lag_active=True, lag_usable=usable, z_return=z,
                maturity_weight=maturity, s_lag_effective=0.5 + conf * (s - 0.5))


# ------------------------------------------------------------------- kademe
def tier_of(n_peer: int) -> str:
    if n_peer >= 20:
        return "A"
    if n_peer >= 8:
        return "B"
    if n_peer >= 4:
        return "C"
    return "D"


TIER_CAP = {"A": 1.00, "B": 0.70, "B_MEDIAN_FALLBACK": 0.55, "C": 0.45,
            "BANK": 0.80, "NAV": 0.60}


# ------------------------------------------------------------------- kanatlar
def apply_wing_floor(v_mid, v_low_raw, v_high_raw):
    """v4.6 §6.1 + §6.3. Bozuk sinirlar once onarilir, sonra taban uygulanir."""
    flags = []
    if v_mid <= 0:
        return dict(status=INSUFFICIENT, flags=["V_MID_NONPOSITIVE"])
    if v_low_raw <= 0:
        v_low_raw = v_mid * math.exp(-MAX_LOG_VALUE_HALFWIDTH)
        flags.append("LOWER_BOUND_TRUNCATED")
    if v_high_raw <= v_mid:
        v_high_raw = v_mid * math.exp(+MIN_LOG_VALUE_HALFWIDTH)
        flags.append("UPPER_BOUND_DEGENERATE")
    lo_hw = max(math.log(v_mid / v_low_raw), MIN_LOG_VALUE_HALFWIDTH)
    hi_hw = max(math.log(v_high_raw / v_mid), MIN_LOG_VALUE_HALFWIDTH)
    return dict(status=OK, flags=flags, lower_halfwidth=lo_hw, upper_halfwidth=hi_hw,
                V_low=v_mid * math.exp(-lo_hw), V_high=v_mid * math.exp(+hi_hw),
                V_low_raw=v_low_raw, V_high_raw=v_high_raw, V_mid=v_mid)


# --------------------------------------------------------------------- banka
def bank_valuation(bvps, roe_ttm_series, coe, macro_cap, payout_sus=None,
                   d_coe=D_COE, d_g=D_G):
    """v4.6 §5. 3x3x3 = 27 kose."""
    valid_q = [r for r in roe_ttm_series if r is not None]
    if len(valid_q) < 6:
        return dict(status=INSUFFICIENT, reason="ROE_QUARTERS_LT_6")

    s = sorted(valid_q)
    roe_sus = median(s[1:-1])                       # trimmed: en yuksek 1, en dusuk 1 at
    m = median(valid_q)
    sd_roe = 1.4826 * median([abs(x - m) for x in valid_q])

    payout_used = PAYOUT_DEFAULT if payout_sus is None else payout_sus
    cands = {"PAYOUT": (1 - payout_used) * roe_sus,
             "MACRO_CAP": macro_cap,
             "COE_GAP": coe - MIN_COE_GAP}
    g = min(cands.values())
    binding = {k for k, v in cands.items() if abs(v - g) < 1e-9}

    if coe - g < MIN_COE_GAP - 1e-12 or roe_sus <= g:
        return dict(status=INSUFFICIENT, reason="CENTER_INVALID")
    v_mid = bvps * (roe_sus - g) / (coe - g)

    vals, n_total = [], 0
    for r in (roe_sus - sd_roe, roe_sus, roe_sus + sd_roe):
        for c in (coe - d_coe, coe, coe + d_coe):
            for sh in (-d_g, 0.0, +d_g):
                n_total += 1
                gs = max(min(g + sh, macro_cap, c - MIN_COE_GAP), 0.0)
                if c - gs >= MIN_COE_GAP - 1e-12 and r > gs:      # >= (v4.5 D6)
                    vals.append(bvps * (r - gs) / (c - gs))
    n_valid = len(vals)
    if n_valid < 6:
        return dict(status=INSUFFICIENT, reason="CORNERS_LT_6", n_valid=n_valid)

    band = apply_wing_floor(v_mid, min(vals), max(vals))
    band.update(status=OK, method="BANK_JUSTIFIED_PB", n_valid=n_valid, n_total=n_total,
                roe_sus=roe_sus, sd_roe=sd_roe, g=g,
                growth_binding_source=binding,
                implied_payout=1 - g / roe_sus,
                payout_gap=(1 - g / roe_sus) - payout_used,
                payout_defaulted=payout_sus is None)
    return band


# ----------------------------------------------------------------------- NAV
def nav_valuation(nav_per_share, target_discount, peer_discounts=None, freshness_months=0):
    if freshness_months > 18:
        return dict(status=INSUFFICIENT, reason="NAV_STALE")
    flags = []
    if peer_discounts and len(peer_discounts) >= NAV_MIN_PEER:
        srt = sorted(peer_discounts)
        n = len(srt)
        d_lo = srt[max(int(0.25 * (n - 1)), 0)]
        d_hi = srt[min(int(math.ceil(0.75 * (n - 1))), n - 1)]
    else:
        d_lo = clip(target_discount - NAV_DISCOUNT_HALFWIDTH, 0.0, 0.95)
        d_hi = clip(target_discount + NAV_DISCOUNT_HALFWIDTH, 0.0, 0.95)

    # v4.6 §6.2: 0 <= d_lo <= target <= d_hi < 1
    t = target_discount
    if not (0 <= d_lo <= t <= d_hi < 1):
        t = clip(t, d_lo, d_hi)
        flags.append("NAV_DISCOUNT_CLIPPED")

    v_mid = nav_per_share * (1 - t)
    band = apply_wing_floor(v_mid, nav_per_share * (1 - d_hi), nav_per_share * (1 - d_lo))
    band["flags"] = band.get("flags", []) + flags
    band.update(method="NAV_DISCOUNT", target_used=t,
                nav_freshness=clip(1 - freshness_months / 12, 0.4, 1.0))
    return band


# ------------------------------------------------------------------ EV tabanli
def ev_valuation(multiple, driver_value, net_debt, shares, half_width_mult):
    hw = max(half_width_mult, MIN_LOG_MULTIPLE_HALFWIDTH)
    f = lambda mm: (mm * driver_value - net_debt) / shares
    return apply_wing_floor(f(multiple), f(multiple * math.exp(-hw)), f(multiple * math.exp(hw)))


# --------------------------------------------------------------- skor ve M2
def valuation_score(v_mid, v_low, v_high, price, lower_hw, upper_hw, v_conf):
    z = (math.log(v_mid / price) / lower_hw) if price < v_mid else (-math.log(price / v_mid) / upper_hw)
    s = clip(0.5 + z / (2 * Z_CAP_VAL), 0.0, 1.0)
    return dict(z_val=z, s_valuation=s, s_val_effective=0.5 + v_conf * (s - 0.5))


def m2(s_val_effective, v_status, v_conf, s_lag_effective, lag_active):
    """v4.6 + uygulama notu 4: deger kapisi duserse eksen NOTRLESIR, M2 kaybolmaz."""
    usable = (v_status == OK) and (v_conf >= CONF_MIN)
    val_component = s_val_effective if usable else 0.5
    score = 0.5 + W_VAL * (val_component - 0.5)
    if lag_active:
        score += W_LAG * (s_lag_effective - 0.5)
    return dict(m2=clip(score, 0.0, 1.0), valuation_usable=usable)
