"""
Eksik deger sozlesmesi — depo geneli tarama.

KOK NEDEN: PostgreSQL NULL degeri pandas'a gecerken `None` KALMAZ (metin
sutunlarinda nan, nullable sutunlarda pd.NA). Yalnizca `is None` kontrol eden
kod bu degerleri "dolu" sanip dogrulama hatasi uretir.

KAPSAM ANALIZI (bu test onu kilitler):
  RISKLI  : pandas'tan beslenen ve OPSIYONEL VERI alani olan degerleme motorlari
            -> financial_institution, insurance, nonfin
  RISKSIZ : holding, gyo (opsiyonel veri alani yok)
  RISKSIZ : ingest/api yardimcilari (JSON/mapping alirlar; JSON'da NaN yoktur,
            orada `None` DOGRU gosterimdir)
"""
import importlib
import re
from pathlib import Path

import pandas as pd
import pytest

from src.utils.missing_values import is_bool_like, is_missing_like

PANDAS_YUZLU_MOTORLAR = [
    ("src.analytics.financial_institution_valuation", "_optional_finite"),
    ("src.analytics.insurance_valuation", "_optional_finite"),
    ("src.analytics.nonfin_valuation", "_optional_number"),
]


# ------------------------------------------------ ortak yardimci
@pytest.mark.parametrize("bos", [None, float("nan"), pd.NA])
def test_ortak_yardimci_eksik_degeri_tanir(bos):
    assert is_missing_like(bos) is True


@pytest.mark.parametrize("dolu", [float("inf"), float("-inf"), True, False, 0, 0.0, "", "abc"])
def test_ortak_yardimci_gecersizi_eksik_saymaz(dolu):
    """SOZLESME: inf ve bool EKSIK DEGILDIR; cagiran taraf reddetmeye devam eder."""
    assert is_missing_like(dolu) is False


def test_numpy_bool_taninir():
    np = pytest.importorskip("numpy")
    assert is_bool_like(np.bool_(True)) and is_bool_like(True)
    assert not is_bool_like(np.float64(1.0))
    assert is_missing_like(np.nan) is True
    assert is_missing_like(np.bool_(False)) is False


# ------------------------------------------------ motorlar
@pytest.mark.parametrize("mod,fn", PANDAS_YUZLU_MOTORLAR)
@pytest.mark.parametrize("bos", [None, float("nan"), pd.NA])
def test_pandas_yuzlu_motorlar_eksik_degeri_tanir(mod, fn, bos):
    fonksiyon = getattr(importlib.import_module(mod), fn)
    assert fonksiyon("x", bos) is None


@pytest.mark.parametrize("mod,fn", PANDAS_YUZLU_MOTORLAR)
@pytest.mark.parametrize("bozuk", [float("inf"), True, "abc"])
def test_pandas_yuzlu_motorlar_gecersizi_reddeder(mod, fn, bozuk):
    fonksiyon = getattr(importlib.import_module(mod), fn)
    with pytest.raises(Exception):
        fonksiyon("x", bozuk)


@pytest.mark.parametrize("mod,fn", PANDAS_YUZLU_MOTORLAR)
def test_motorlar_ortak_yardimciyi_kullanir(mod, fn):
    """
    Kopyalanan kontrol, kopyalanan hata demektir: bu kontrol uc motorda ayri
    ayri yazilmisti ve ucunde de ayni hata vardi. Artik tek kaynak var.
    """
    kaynak = Path(importlib.import_module(mod).__file__).read_text(encoding="utf-8")
    assert "from src.utils.missing_values import" in kaynak, f"{mod} ortak modulu kullanmali"
    assert "def _is_missing_like" not in kaynak, f"{mod} yerel kopya tutmamali"


# ------------------------------------------------ kapsam analizinin kilidi
def test_holding_ve_gyo_opsiyonel_veri_alani_tutmaz():
    """
    Bu iki motor RISKSIZ cunku opsiyonel VERI alani yok. Ileride eklenirse
    bu test kirilir ve ortak yardimciya baglanmasi gerektigi hatirlatilir.
    """
    for mod in ("src.analytics.holding_valuation", "src.analytics.gyo_valuation"):
        kaynak = Path(importlib.import_module(mod).__file__).read_text(encoding="utf-8")
        # dataclass alanlarinda `| None` aramasi (fonksiyon parametresi degil)
        alanlar = re.findall(r"^    [a-z_]+: [A-Za-z]+ \| None$", kaynak, re.M)
        assert not alanlar, (
            f"{mod} artik opsiyonel veri alani tutuyor: {alanlar}. "
            "src.utils.missing_values.is_missing_like kullanilmali."
        )


def test_yeni_motor_eklenirse_liste_guncellenmeli():
    """
    Degerleme motoru sayisi degisirse bu test kirilir ve yeni motorun eksik
    deger sozlesmesine dahil edilip edilmedigi gozden gecirilir.
    """
    motorlar = sorted(p.name for p in Path("src/analytics").glob("*_valuation.py"))
    assert motorlar == [
        "financial_institution_valuation.py",
        "gyo_valuation.py",
        "holding_valuation.py",
        "insurance_valuation.py",
        "nonfin_valuation.py",
    ], f"motor listesi degisti: {motorlar}"


# ------------------------------------------------ pandas surum matrisi
def test_pandas_surum_davranisi_belgelenir():
    """
    Bu hata SURUM FARKINDAN ortaya cikti: pandas 2.x'te object sutunda `None`
    korunurken 3.x'te metin sutunlari `nan` uretiyor. Test hangi surumde
    kosarsa kossun gecmeli; amaci davranisi KAYIT ALTINA almak.
    """
    surum = tuple(int(x) for x in pd.__version__.split(".")[:2])
    frame = pd.DataFrame([
        {"ticker": "A", "sector_code": None},
        {"ticker": "B", "sector_code": "GYO"},
    ])
    deger = frame.sector_code.iloc[0]
    # Hangi surum olursa olsun ORTAK YARDIMCI dogru cevabi vermeli:
    assert is_missing_like(deger) is True, (
        f"pandas {pd.__version__}: sector_code eksik degeri taninmadi ({deger!r})"
    )
    # Surum davranisini kayda gecir (assert degil, tani):
    print(f"\n  [tani] pandas {pd.__version__} -> None saklanan deger: {deger!r} "
          f"(tip {type(deger).__name__}), surum ailesi {surum[0]}.x")


@pytest.mark.parametrize("dtype,beklenen_eksik", [
    ("object", True),
    ("Float64", True),      # nullable -> pd.NA
    ("float64", True),      # numpy -> nan
])
def test_farkli_dtype_lerde_null_taninir(dtype, beklenen_eksik):
    """PostgreSQL NULL, sutun dtype'ina gore None / pd.NA / nan olabilir."""
    dizi = pd.array([None, 1.0], dtype=dtype) if dtype != "object" else pd.array([None, "x"], dtype="object")
    assert is_missing_like(dizi[0]) is beklenen_eksik


def test_read_sql_benzeri_null_akisi():
    """DataFrame'den motora giden yolun uctan uca eksik deger davranisi."""
    from src.analytics.financial_institution_valuation import _optional_finite
    frame = pd.DataFrame([
        {"ticker": "A", "capital_adequacy_ratio": None},
        {"ticker": "B", "capital_adequacy_ratio": 0.18},
    ])
    satirlar = frame.to_dict("records")
    assert _optional_finite("capital_adequacy_ratio", satirlar[0]["capital_adequacy_ratio"]) is None
    assert _optional_finite("capital_adequacy_ratio", satirlar[1]["capital_adequacy_ratio"]) == pytest.approx(0.18)
