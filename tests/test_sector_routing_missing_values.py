"""
Sektor yonlendirmesinde EKSIK deger sozlesmesi.

PostgreSQL NULL -> pandas donusumunde eksik degerin FIZIKSEL TEMSILI
SURUME VE DTYPE'A GORE DEGISIR:
  - pandas 2.2.x object sutunu  -> `None` KORUNABILIR
  - pandas 3.x metin sutunlari  -> nan (float)
  - nullable sutunlar           -> pd.NA (NAType)

UCU DE DESTEKLENMELIDIR. Kod hicbirini varsaymaz; `is_missing_like()`
uctan uce tek sozlesmedir. Rota kodu yalnizca `is not None` kontrol
ettiginde bos sector_code "dolu metin olmali" hatasi veriyor ve evren
sorgulari kiriliyordu.

TEST TASARIM KURALI: bu dosyadaki testler belirli bir pandas surumunun
fiziksel eksik-deger temsilini ZORUNLU KILMAZ. Onceki surumde
`assert ilk is not None` vardi; bu, pandas 3.x davranisini butun
`pandas>=2.2` icin sozlesme sanmakti ve pandas 2.2.3 ortaminda testi
kiriyordu -- urun davranisi dogru oldugu halde. Bir surumun rastlantisal
temsilini kilitlemek, tasinabilirligi sessizce yok eder.

Eksik deger HATA degildir: "bilgi yok" demektir ve endekse dusulur.
Bos/gecersiz METIN ise hata olarak kalir.
"""
import math

import pandas as pd
import pytest

from src.ingest.sector_routing import (
    SectorRoutingConfig,
    SectorRoutingError,
    is_missing_like,
)


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA])
def test_eksik_deger_tespiti(value):
    assert is_missing_like(value) is True


@pytest.mark.parametrize("value", ["GYO", "  BANK ", 0.0, 1, "", "   "])
def test_dolu_deger_eksik_sayilmaz(value):
    assert is_missing_like(value) is False


@pytest.mark.parametrize("bos", [None, float("nan"), pd.NA])
def test_eksik_sector_code_endekse_duser(bos):
    """Eksik sector_code hata vermemeli; sector_index_code'a dusulmeli."""
    config = SectorRoutingConfig.default()
    assert config.route(ticker="AGYO", sector_index_code="XGMYO",
                        sector_code=bos) == "GYO"
    assert config.route(ticker="GARAN", sector_index_code="XBANK",
                        sector_code=bos) == "BANK"


@pytest.mark.parametrize("bos", [None, float("nan"), pd.NA])
def test_ikisi_de_eksikse_nonfin(bos):
    config = SectorRoutingConfig.default()
    assert config.route(ticker="XXXX", sector_index_code=bos,
                        sector_code=bos) == "NONFIN"


def test_bos_metin_hala_hata():
    """Eksik deger ile GECERSIZ METIN ayri: bos string hata olmali."""
    config = SectorRoutingConfig.default()
    for bozuk in ("", "   "):
        with pytest.raises(SectorRoutingError):
            config.route(ticker="AGYO", sector_index_code="XGMYO",
                         sector_code=bozuk)


def test_pandas_eksik_degeri_temsili_ne_olursa_olsun_tanINIR():
    """
    Hatanin kok nedeni: pandas eksik degeri HANGI BICIMDE tasiyacagini
    garanti etmez. `None` korunabilir, `nan`a veya `pd.NA`ya donusebilir.

    Bu test hangisinin secildigini UMURSAMAZ; yalniz `is_missing_like()`
    ucunu de tanidigini dogrular. Belirli bir temsili zorunlu kilmak,
    testi pandas surumune baglar.
    """
    df = pd.DataFrame([
        {"ticker": "AGYO", "sector_index_code": "XGMYO", "sector_code": None},
        {"ticker": "GYOX", "sector_index_code": "XUSIN", "sector_code": "GYO"},
    ])
    ilk = df.sector_code.iloc[0]
    assert is_missing_like(ilk), (
        f"eksik deger tespiti pandas ciktisini tanimali "
        f"(pandas {pd.__version__} temsili: {ilk!r})")
    assert not is_missing_like(df.sector_code.iloc[1])


@pytest.mark.parametrize("dtype", [None, "object", "string"])
def test_eksik_deger_her_dtype_altinda_tanINIR(dtype):
    """
    Ayni sutun uc farkli dtype altinda uc farkli eksik temsili verebilir.
    Ucu de eksik SAYILMALI ve hicbiri gecerli metin sayilmamali.
    """
    seri = pd.Series([None, "GYO"], dtype=dtype)
    assert is_missing_like(seri.iloc[0]) is True
    assert is_missing_like(seri.iloc[1]) is False


def test_pandas_temsilinin_hangisi_oldugu_SOZLESME_DEGIL():
    """
    Gozlemlenen temsili KAYIT ALTINA alir ama zorunlu KILMAZ.
    Uc gecerli temsilden biri olmalidir; hangisi oldugu surume kalmistir.
    """
    df = pd.DataFrame([{"sector_code": None}])
    ilk = df.sector_code.iloc[0]
    gecerli = (ilk is None) or (isinstance(ilk, float) and math.isnan(ilk)) \
        or (ilk is pd.NA)
    assert gecerli, f"beklenmeyen eksik temsili: {ilk!r} ({type(ilk)})"
    assert is_missing_like(ilk) is True


def test_dort_batch_hatti_ayni_rotayi_kullanir():
    """Duzeltme tek noktada: dort hat da SectorRoutingConfig.route cagiriyor."""
    from pathlib import Path
    kaynak = Path("src/analytics")
    hatlar = ["gyo_batch_pipeline.py", "holding_batch_pipeline.py",
              "insurance_batch_pipeline.py", "nonfin_batch_pipeline.py"]
    for ad in hatlar:
        metin = (kaynak / ad).read_text(encoding="utf-8")
        assert "config.route(" in metin, f"{ad} merkezi rotayi kullanmali"


# ------------------------------------------------ alt tur -> aile eslemesi
@pytest.mark.parametrize("kod,aile", [
    ("FACTORING", "FINANCIAL"),
    ("LEASING", "FINANCIAL"),
    ("CONSUMER_FINANCE", "FINANCIAL"),
    ("NON_LIFE", "INSURANCE"),
    ("LIFE_PENSION", "INSURANCE"),
])
def test_alt_tur_kodu_aileye_cevrilir(kod, aile):
    """
    Sirketin sector_code alani ALT TUR tasiyabilir (FACTORING gibi). Bu bir AILE
    adi degildir; eslesme olmadan sirket genis XUMAL endeksine veya NONFIN'e duserdi.
    """
    config = SectorRoutingConfig.default()
    assert config.route(ticker="XXXX", sector_index_code="XUSIN", sector_code=kod) == aile


def test_aile_adi_dogrudan_gecerli_kalir():
    config = SectorRoutingConfig.default()
    for aile in ("BANK", "HOLDING", "GYO", "INSURANCE", "FINANCIAL", "NONFIN"):
        assert config.route(ticker="X", sector_index_code="XUSIN", sector_code=aile) == aile


def test_alt_tur_anahtari_aile_adi_olamaz():
    """Karisikligi onlemek icin sector_code_to_family anahtari aile adi olamaz."""
    with pytest.raises(SectorRoutingError, match="aile adi olamaz"):
        SectorRoutingConfig.from_dict({
            "routing_profile": "P", "routing_version": 1,
            "sector_code_to_family": {"BANK": "FINANCIAL"},
        })


def test_paketlenen_routing_config_alt_turleri_icerir():
    from pathlib import Path
    config = SectorRoutingConfig.from_json_file(Path("config/sector_routing.v1.json"))
    assert config.sector_code_to_family["FACTORING"] == "FINANCIAL"
    assert config.sector_code_to_family["LEASING"] == "FINANCIAL"
    assert config.sector_code_to_family["CONSUMER_FINANCE"] == "FINANCIAL"
    assert config.route(ticker="X", sector_index_code="XUSIN", sector_code="LEASING") == "FINANCIAL"


def test_bilinmeyen_alt_tur_endekse_duser():
    """Tanimsiz alt tur SESSIZCE aileye atanmaz; endekse/varsayilana duser."""
    config = SectorRoutingConfig.default()
    assert config.route(ticker="X", sector_index_code="XBANK", sector_code="BILINMEYEN") == "BANK"
    assert config.route(ticker="X", sector_index_code="XUSIN", sector_code="BILINMEYEN") == "NONFIN"


# ==================================================== SURUM TASINABILIRLIGI
def test_hicbir_test_belirli_pandas_temsilini_zorunlu_kilmaz():
    """
    KORUMA TESTI. V19 kapanis denetiminde su kusur bulundu: bir test
    `assert ilk is not None` diyerek pandas 3.x'in fiziksel eksik-deger
    temsilini butun `pandas>=2.2` icin sozlesme sanmisti. pandas 2.2.3
    object sutununda `None` korundugu icin test kiriliyordu -- URUN
    DAVRANISI DOGRU OLDUGU HALDE.

    requirements.txt `pandas>=2.2` diyorsa testler 2.2.x altinda da
    gecmelidir. Bu test, ayni kalibi geri gelirse yakalar.
    """
    import ast
    import pathlib

    kaynak = pathlib.Path(__file__).read_text(encoding="utf-8")
    agac = ast.parse(kaynak)
    for dugum in ast.walk(agac):
        if not isinstance(dugum, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Is, ast.IsNot)) for op in dugum.ops):
            continue
        # `x is None` / `x is not None` yalniz "gecerli temsillerden biri mi"
        # kontrolu icinde serbesttir; ciplak assert olarak degil.
        for karsilastirilan in dugum.comparators:
            if isinstance(karsilastirilan, ast.Constant) and karsilastirilan.value is None:
                satir = kaynak.splitlines()[dugum.lineno - 1].strip()
                assert not satir.startswith("assert ilk is"), (
                    f"satir {dugum.lineno}: belirli pandas temsili zorunlu "
                    f"kilinamaz -> {satir}")


def test_requirements_pandas_alt_siniri_belgelenmis():
    """
    Desteklenen alt sinir requirements.txt'te ACIKCA yazili olmali.
    Yazili degilse hangi surumlerde test edilmesi gerektigi belirsizlesir.
    """
    import pathlib
    import re

    req = pathlib.Path(__file__).resolve().parents[1] / "requirements.txt"
    metin = req.read_text(encoding="utf-8")
    eslesme = re.search(r"^pandas>=(\d+)\.(\d+)", metin, re.M)
    assert eslesme, "requirements.txt pandas alt sinirini belirtmeli"
    ana, alt = int(eslesme.group(1)), int(eslesme.group(2))
    assert (ana, alt) >= (2, 2), "beklenmeyen pandas alt siniri"
