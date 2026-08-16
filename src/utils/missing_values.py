"""
Eksik deger tespiti — TEK kaynak.

NEDEN ORTAK MODUL: bu kontrol uc motorda ayri ayri yaziliyordu ve ucunde de
ayni hata vardi (yalnizca `is None`). Kopyalanan kontrol, kopyalanan hata
demektir; yeni bir motor eklendiginde ayni acik yeniden acilir.

KOK NEDEN: PostgreSQL NULL degeri pandas'a gecerken `None` olarak KALMAZ.
  - pandas metin sutunlari      -> nan (float)
  - pandas nullable sutunlari   -> pd.NA (NAType)
  - numpy dizileri              -> nan
Yalnizca `value is None` kontrol eden kod bu degerleri "dolu" sanip
dogrulama hatasi uretir.

SOZLESME: eksik deger ile GECERSIZ deger AYRIDIR.
  eksik   -> None, NaN, pd.NA            : "bilgi yok", varsayilana dusulur
  gecersiz-> inf, -inf, bool, bozuk metin: HATA
Bu ayrim korunmalidir; `inf`i eksik saymak sessizce yanlis sonuc uretir.

pandas ve numpy ZORUNLU bagimlilik degildir; tip ADI ve `dtype.kind`
uzerinden tespit edilir.
"""
from __future__ import annotations

import math
from typing import Any

__all__ = ["is_missing_like", "is_bool_like"]

try:                                    # opsiyonel
    import numpy as _np
    _NUMPY_BOOL: Any = _np.bool_
except Exception:                       # pragma: no cover
    _NUMPY_BOOL = None


def is_bool_like(value: Any) -> bool:
    """
    Python bool VE numpy.bool_ (ve benzeri kutuphane bool skalerleri).

    `isinstance(np.bool_(True), bool)` FALSE doner; bu yuzden yalniz
    isinstance kontrolu yetmez. JSON'daki true/false Python'da 1/0 gibi
    davranip gecerli gorunen yanlis degerler uretebilir.
    """
    if isinstance(value, bool):
        return True
    if _NUMPY_BOOL is not None and isinstance(value, _NUMPY_BOOL):
        return True
    dtype = getattr(value, "dtype", None)
    return getattr(dtype, "kind", None) == "b"


def is_missing_like(value: Any) -> bool:
    """
    EKSIK deger mi? None, pandas.NA (NAType) veya gercek NaN ise True.

    bool ve inf EKSIK DEGILDIR: False doner ve cagiran taraf bunlari
    gecersiz girdi olarak reddetmeye devam eder.
    """
    if value is None:
        return True
    value_type = type(value)
    if (value_type.__name__ == "NAType"
            and value_type.__module__.split(".")[0] == "pandas"):
        return True
    if is_bool_like(value):
        return False
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
