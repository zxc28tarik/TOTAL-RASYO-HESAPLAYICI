"""
ROE belirsizlik tahmini — v4.7.1 referans uygulamasi.

Trend ile gurultuyu ayirir. Trend zaten `g` uzerinden degerlemeye girdigi icin
belirsizlige ikinci kez girmemelidir (cift sayim).

v4.7.1 duzeltmeleri:
  - Eksik ceyrekler ARTIK zaman eksenini bozmuyor (gercek indeks korunuyor).
  - NaN/inf/negatif giris dogrulamasi.
  - Tek uc deger icin ayri TANI (bandi kalici genisletmek yerine V_conf cezasi).
"""
from __future__ import annotations
import itertools
import math
from collections.abc import Mapping, Set as AbcSet

try:                                    # opsiyonel: NumPy zorunlu bagimlilik degil
    import numpy as _np
    _NUMPY_BOOL = _np.bool_
except Exception:                       # pragma: no cover
    _NUMPY_BOOL = None
from statistics import median
from typing import Optional, Sequence, Tuple

ABSOLUTE_FLOOR_DEFAULT = 0.005      # kalibre edilecek baslangic degeri
MIN_SECTOR_SAMPLE_DEFAULT = 20
SECTOR_QUANTILE_DEFAULT = 0.15
MIN_OBS = 4                          # trend cikarmak icin asgari gozlem
OUTLIER_ABS_FLOOR = 0.05             # mutlak artik esigi (yer tutucu)
OUTLIER_MULTIPLIER = 4.0             # sd_effective katsayisi (yer tutucu)
OUTLIER_CONF_PENALTY = 0.85          # V_conf carpani (yer tutucu)


def is_missing_like(value) -> bool:
    """
    EKSIK deger tespiti: None, pandas.NA ve gercek NaN.

    PostgreSQL -> pandas hattinda nullable Float64 sutunundaki eksik deger NaN
    DEGIL pd.NA (NAType) olarak gelir. Once pd.NA sayisal olmayan deger sanilip
    reddediliyordu: payout_sus=pd.NA -> INVALID_PAYOUT (varsayilan kullanilmaliydi),
    ROE serisinde ise yukleme ValueError ile kiriliyordu.
    pandas zorunlu bagimlilik olmasin diye tip ADI uzerinden tespit edilir.
    """
    if value is None:
        return True
    t = type(value)
    if t.__name__ == "NAType" and t.__module__.split(".")[0] == "pandas":
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def is_bool_like(value) -> bool:
    """
    Python bool VE numpy.bool_ (ve benzeri kutuphane bool skalerleri) yakalar.

    isinstance(np.bool_(True), bool) FALSE dondugu icin Python bool icin kapatilan
    sessiz hata NumPy True/False ile devam ediyordu:
      bvps=np.bool_(True)      -> 1.0 olarak kullaniliyordu
      macro_cap=np.bool_(False)-> 0.0 kabul edilip V_mid 6.89 -> 12.32 oluyordu
      roe=[np.bool_(True)]*6   -> roe_sus=1.0, V_mid=79.28
    Zorunlu NumPy bagimliligi eklemek yerine iki katmanli tespit:
      1) opsiyonel numpy import (varsa np.bool_ dogrudan)
      2) dtype.kind == 'b' ordek tipi kontrolu (numpy skalerlerinde dtype var,
         Python bool/float'ta yok) -- baska kutuphanelerin bool skalerlerini de yakalar
    """
    if isinstance(value, bool):
        return True
    if _NUMPY_BOOL is not None and isinstance(value, _NUMPY_BOOL):
        return True
    dtype = getattr(value, "dtype", None)
    return getattr(dtype, "kind", None) == "b"


def coerce_finite_number(name, value, *, minimum=None, strict_minimum=False,
                         maximum=None) -> float:
    """
    Sayisal parametreleri TEK yerden dogrular ve kanonik float'a cevirir.

    Neden ortak yardimci: ayni hata sinifi (yeni esik parametresi eklerken
    dogrulamayi atlamak) uc kez tekrarlandi -- absolute_floor=NaN, max_halfwidth=NaN,
    band_width_shadow_mode="false". Her yeni parametre buradan gecmelidir.

    bool OZELLIKLE reddedilir: JSON'daki true/false Python'da 1/0 gibi davranip
    bvps=True -> 1.0, payout_sus=True -> 1.0 gibi gecerli gorunen yanlis
    degerlemeler uretiyordu. Sayisal string'ler bilincli olarak DESTEKLENIR.
    """
    if is_bool_like(value):
        raise ValueError(f"{name} cannot be bool")
    try:
        result = float(value)          # 10**10000 kontrolsuz OverflowError veriyordu
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None:
        if strict_minimum and result <= minimum:
            raise ValueError(f"{name} must be > {minimum}")
        if not strict_minimum and result < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return result


def require_bool(name, value) -> bool:
    """
    Bos olmayan string ('false' dahil) True sayiliyordu.
    Konfigurasyon alanlarinda YALNIZ gercek Python bool kabul edilir (siki politika);
    np.bool_ de reddedilir cunku konfigurasyon JSON/env'den gelir, NumPy'den gelmez.
    """
    if type(value) is not bool:
        raise ValueError(f"{name} must be a Python bool")
    return value


def require_int(name, value, *, minimum=None) -> int:
    """bool, int alt sinifi oldugu icin ayrica reddedilir."""
    if is_bool_like(value) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int (not bool)")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def require_ordered_iterable(name, value, *, allow_none=False):
    """
    SIRALI koleksiyon zorunlu. set/frozenset zaman serisinde sirayi kaybettiriyordu:
    ayni yukselen seride liste egim 0.0400 verirken set 0.0200 veriyor ve
    sd_roe_effective 0.005'ten 0.074'e ciktigi halde sonuc yine OK donuyordu.
    Sektor olceklerinde sira onemsiz ama set tekrarlari silip orneklem buyuklugunu
    ve quantile dagilimini degistirdigi icin orada da reddedilir.
    """
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{name} must be an ordered numeric sequence")
    if isinstance(value, (str, bytes, bytearray, Mapping, AbcSet)):
        raise ValueError(
            f"{name} must be an ordered numeric sequence (got {type(value).__name__})")
    try:
        return list(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be iterable") from exc


# geriye uyumluluk adi
require_number_iterable = require_ordered_iterable


def coerce_roe_series(name, value):
    """
    ROE zaman serisini OGE OGE dogrular ve kanoniklestirir.

    Eksik donem ile gecersiz veri ayrilir:
      - None ve gercek NaN -> None olarak KORUNUR (zaman yuvasi bozulmaz)
      - bool, bozuk metin, nesne, inf -> ValueError
      - sayisal string -> float'a cevrilir
    Onceden "oops" sessizce eksik donem sayiliyor, [True]*6 ise roe_sus=1.0
    uretip V_mid=79.28 ile OK donuyordu.
    """
    items = require_ordered_iterable(name, value)
    out, dropped = [], 0
    for item in items:
        if is_bool_like(item):
            raise ValueError(f"{name} contains bool")
        if is_missing_like(item):      # None, pd.NA, NaN -> zaman yuvasi korunur
            out.append(None); dropped += 1; continue
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} contains non-numeric value: {item!r}") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} contains non-finite value: {item!r}")
        else:
            out.append(number)
    return out, dropped


def coerce_sector_scales(name, value):
    """
    Sektor artik olceklerini oge oge dogrular. Onceden bozuk/negatif deger sessizce
    atiliyor, orneklem 20'nin altina dusuyor ve SECTOR_QUANTILE -> ABSOLUTE_FLOOR
    gecisi hicbir uyari olmadan yasaniyordu (0.010 -> 0.005).
    """
    items = require_ordered_iterable(name, value, allow_none=True)
    if items is None:
        return None, 0
    out, dropped = [], 0
    for item in items:
        if is_bool_like(item):
            raise ValueError(f"{name} contains bool")
        if is_missing_like(item):      # None, pd.NA, NaN -> atilir ve sayilir
            dropped += 1; continue
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} contains non-numeric value: {item!r}") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} contains non-finite value: {item!r}")
        if number < 0:
            raise ValueError(f"{name} cannot be negative: {number}")
        out.append(number)
    return out, dropped


def _finite(x) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError, OverflowError):
        return False


def theil_sen_xy(points: Sequence[Tuple[float, float]]):
    """
    (x, y) noktalari uzerinden dayanikli egim.

    x ZAMAN INDEKSIDIR ve eksik donemlerde bosluk birakir. Gozlemleri yan yana
    sikistirmak yapay trend ve yapay belirsizlik uretir (v4.7'de bu hata vardi:
    [0.12, None, 0.20, None, 0.28, 0.32] serisinde egim 0.0733 cikiyordu,
    dogrusu 0.0400).
    """
    slopes = [
        (points[j][1] - points[i][1]) / (points[j][0] - points[i][0])
        for i, j in itertools.combinations(range(len(points)), 2)
        if points[j][0] != points[i][0]
    ]
    if not slopes:
        return 0.0, 0.0
    b = median(slopes)
    a = median([y - b * x for x, y in points])
    return a, b


def theil_sen(y: Sequence[float]):
    """Esit araliklı seri icin kolaylik sarumu."""
    return theil_sen_xy([(float(i), float(v)) for i, v in enumerate(y)])


def mad_sd(v: Sequence[float]) -> float:
    vals = [float(t) for t in v if _finite(t)]
    if not vals:
        return 0.0
    m = median(vals)
    return 1.4826 * median([abs(t - m) for t in vals])


def quantile(values: Sequence[float], q: float) -> float:
    s = sorted(float(v) for v in values if _finite(v))
    if not s:
        return 0.0
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def estimate_roe_uncertainty(
    roe_series: Sequence[Optional[float]],
    sector_residual_scales: Optional[Sequence[float]] = None,
    min_sector_sample: int = MIN_SECTOR_SAMPLE_DEFAULT,
    sector_quantile: float = SECTOR_QUANTILE_DEFAULT,
    absolute_floor: float = ABSOLUTE_FLOOR_DEFAULT,
    dof_correction: bool = False,
    outlier_absolute_floor: float = OUTLIER_ABS_FLOOR,
    outlier_multiplier: float = OUTLIER_MULTIPLIER,
) -> dict:
    """
    Iki katmanli taban:
      1) sektor artik olcekleri yeterliyse -> o dagilimin `sector_quantile` yuzdeligi
      2) her durumda mutlak taban ile alttan sinirlanir

    Uc deger: bandi kalici genisletmek yerine `outlier_flag` uretilir. Tek seferlik
    ROE sicramasi (tek seferlik kar, varlik satisi, muhasebe etkisi) kalici
    oynaklik gibi ele alinmamalidir; V_conf cezasi ve aciklama daha dogrudur.

    dof_correction: sqrt(n/(n-2)) yaklasik duzeltmesi OPSIYONELDIR; gercek veri
    kalibrasyonunda karsilastirilarak secilmelidir.

    Kalibrasyon parametreleri konfigurasyondan gelecegi icin BASLANGICTA dogrulanir:
    gecersiz `absolute_floor` koruyucu tabani sessizce etkisizlestirebilirdi.
    """
    # --- kalibrasyon parametresi dogrulamasi (giris guvenligi, kalibrasyon degil) ---
    # bool, int alt sinifi oldugu icin ONCE tip kontrolu; sonra deger kontrolu.
    # Dogrulamadan sonra hepsi KANONIK tipe cevrilir: aksi halde "0.15" gibi bir
    # string dogrulamayi float() ile gecip sonraki hesapta cokuyordu.
    dof_correction = require_bool("dof_correction", dof_correction)
    min_sector_sample = require_int("min_sector_sample", min_sector_sample, minimum=1)
    absolute_floor = coerce_finite_number("absolute_floor", absolute_floor, minimum=0.0)
    sector_quantile = coerce_finite_number("sector_quantile", sector_quantile,
                                           minimum=0.0, maximum=1.0)
    outlier_absolute_floor = coerce_finite_number("outlier_absolute_floor",
                                                  outlier_absolute_floor, minimum=0.0)
    outlier_multiplier = coerce_finite_number("outlier_multiplier", outlier_multiplier,
                                              minimum=0.0)
    roe_series, roe_dropped = coerce_roe_series("roe_series", roe_series)
    sector_residual_scales, scales_dropped = coerce_sector_scales(
        "sector_residual_scales", sector_residual_scales)

    points = [(float(i), float(v)) for i, v in enumerate(roe_series) if _finite(v)]
    n_valid = len(points)
    values = [y for _, y in points]

    sd_raw = mad_sd(values) if n_valid >= 2 else 0.0

    if n_valid < MIN_OBS:
        floor = max(float(absolute_floor), 0.0)
        return dict(sd_roe_raw=sd_raw, sd_roe_residual=None, sd_roe_floor=floor,
                    sd_roe_effective=floor, floor_source="INSUFFICIENT_DATA",
                    trend_slope=None, n_valid=n_valid, floor_binding=True,
                    dof_corrected=False, outlier_flag=False,
                    max_abs_residual=None, conf_penalty=1.0,
                    roe_missing_count=roe_dropped, scales_missing_count=scales_dropped)

    a, b = theil_sen_xy(points)
    residuals = [y - (a + b * x) for x, y in points]
    sd_residual = mad_sd(residuals)
    if dof_correction and n_valid > 2:
        sd_residual *= (n_valid / (n_valid - 2)) ** 0.5

    # --- taban secimi ---
    clean_scales = None
    if sector_residual_scales is not None:
        clean_scales = [float(x) for x in sector_residual_scales if _finite(x) and float(x) >= 0.0]

    if clean_scales is not None and len(clean_scales) >= min_sector_sample:
        data_floor = quantile(clean_scales, sector_quantile)
        if data_floor > absolute_floor:
            floor, source = data_floor, "SECTOR_QUANTILE"
        else:
            floor, source = float(absolute_floor), "ABSOLUTE_FLOOR"
    else:
        floor, source = float(absolute_floor), "ABSOLUTE_FLOOR"

    effective = max(sd_residual, floor)
    if effective > floor:
        source = "RESIDUAL_SCALE"

    # --- uc deger tanisi (bandi genisletmez) ---
    max_abs_res = max(abs(r) for r in residuals) if residuals else 0.0
    threshold = max(float(outlier_absolute_floor), float(outlier_multiplier) * effective)
    outlier_flag = max_abs_res > threshold

    return dict(sd_roe_raw=sd_raw, sd_roe_residual=sd_residual, sd_roe_floor=floor,
                sd_roe_effective=effective, floor_source=source,
                trend_slope=b, n_valid=n_valid,
                floor_binding=(effective <= floor + 1e-15),
                dof_corrected=bool(dof_correction),
                outlier_flag=outlier_flag, max_abs_residual=max_abs_res,
                conf_penalty=(OUTLIER_CONF_PENALTY if outlier_flag else 1.0),
                roe_missing_count=roe_dropped, scales_missing_count=scales_dropped)
