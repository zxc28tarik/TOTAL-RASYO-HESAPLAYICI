"""
Fikstur referans dogrulamasi — canli PostgreSQL'e karsi otomatik kabul testi.

Entegrasyon paketi geldiginde SORGU_DOSYASI uretim sorgusuna cevrilir;
beklenen degerler AYNI kalir. Fark cikan alan hatanin yerini gosterir:
  selected_version_tag / selected_published_at -> point-in-time secim
  quarter_slots / roe_missing_count            -> takvim yuvasi
  roe_series_canonical                         -> kanonik donusum
  trend_slope / sd_roe_effective / roe_sus     -> motor girdisi
"""
import os
import subprocess
import sys
from pathlib import Path
from statistics import median

import pytest

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
# KABUL TESTI: motor bulunamazsa SERT kirilir.
# Onceki hali module-level pytest.skip kullaniyordu; motor dosyasi paketlenmemis
# veya import yolu yanlis oldugunda butun kabul testi SESSIZCE atlaniyordu
# (1 skipped, exit 5). SQL tarafindaki skip duzeltildi ama bu ikinci skip kalmisti.
try:
    from roe_uncertainty import estimate_roe_uncertainty
except ImportError as exc:               # pragma: no cover
    raise RuntimeError(
        "ROE belirsizlik motoru yuklenemedi; kabul testi calistirilamaz"
    ) from exc

SORGU_DOSYASI = "sorgu_dogru.sql"        # <- entegrasyonda uretim sorgusuyla degistir
TICKER, SON_DONEM = "FIXBNK", "2025-12-31"

BEKLENEN_SLOTS = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
                  "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]

REFERANS = {
    "2026-03-01": dict(
        seri=[0.1560, 0.1898, None, 0.2346, 0.2100, 0.2400, 0.2950, 0.3080],
        surumler=["ORIGINAL", "ORIGINAL", None, "ORIGINAL",
                  "RESTATED", "RESTATED", "ORIGINAL", "ORIGINAL"],
        yayinlar=["2024-05-10", "2024-08-09", None, "2025-02-14",
                  "2025-11-20", "2025-08-08", "2025-11-07", "2026-02-13"],
        slope=0.021040, sd_eff=0.01192010, n_valid=7, eksik=1, roe_sus=0.234600,
    ),
    "2025-10-01": dict(
        seri=[0.1560, 0.1898, None, 0.2346, 0.2689, 0.2400, None, None],
        surumler=["ORIGINAL", "ORIGINAL", None, "ORIGINAL",
                  "ORIGINAL", "RESTATED", None, None],
        yayinlar=["2024-05-10", "2024-08-09", None, "2025-02-14",
                  "2025-05-09", "2025-08-08", None, None],
        slope=0.024300, sd_eff=0.00845082, n_valid=5, eksik=3, roe_sus=0.234600,
    ),
}


def _psql(args):
    """
    KABUL TESTI: SQL hatasi SERT kirilmali.

    Onceki hali pytest.skip kullaniyordu; uretim sorgusunda sozdizimi hatasi,
    baglanti sorunu veya yanlis dosya yolu olsa test BASARISIZ olmak yerine
    ATLANIYORDU -- bir kabul testinin en tehlikeli hali. Ayrica psql hic yoksa
    FileNotFoundError yakalanmadigi icin skip degil error uretiyordu.

    ON_ERROR_STOP=1 sart: aksi halde `psql -f` bazi SQL hatalarindan sonra devam eder.
    """
    env = dict(os.environ)
    env.setdefault("PGHOST", "localhost")
    env.setdefault("PGUSER", "postgres")
    env.setdefault("PGPASSWORD", "postgres")
    env.setdefault("PGDATABASE", "postgres")
    try:
        r = subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-F", "|", *args],
            cwd=BASE_DIR, capture_output=True, text=True, env=env)
    except OSError as exc:
        # FileNotFoundError, PermissionError vb. tek anlasilir kabul testi hatasina
        # cevrilir. Yalniz FileNotFoundError yakalanirken psql bulunup
        # CALISTIRILAMADIGI durumda kontrolsuz PermissionError disariya tasiyordu.
        pytest.fail(f"psql baslatilamadi: {type(exc).__name__}: {exc}")
    if r.returncode != 0:
        pytest.fail("PostgreSQL sorgusu basarisiz:\n" + r.stderr.strip())
    return r.stdout


@pytest.fixture(scope="module", autouse=True)
def _kur():
    for f in ("fixture_pit_bank.sql", "fixture_pit_bank_orders.sql",
              "fixture_intraday_timestamptz.sql"):
        _psql(["-f", f])


def _sorgu(asof, dosya=SORGU_DOSYASI):
    out = _psql(["-v", f"ticker='{TICKER}'", "-v", f"analysis_date='{asof}'",
                 "-v", f"son_donem='{SON_DONEM}'", "-f", dosya])
    slots, seri, surumler, yayin = [], [], [], []
    for ln in out.strip().splitlines():
        p = ln.split("|")
        if len(p) < 2:
            continue
        slots.append(p[0].strip())
        seri.append(float(p[1]) if p[1].strip() else None)
        surumler.append(p[2].strip() or None if len(p) > 2 else None)
        yayin.append(p[3].strip() or None if len(p) > 3 else None)
    return slots, seri, surumler, yayin


@pytest.mark.parametrize("asof", list(REFERANS))
def test_quarter_slots_takvim_uzerinden(asof):
    """'Son sekiz kayit' degil TAKVIM yuvasi: eksik donem yuvayi kaydirmamali."""
    slots, _, _, _ = _sorgu(asof)
    assert slots == BEKLENEN_SLOTS


@pytest.mark.parametrize("asof", list(REFERANS))
def test_roe_series_canonical(asof):
    """Eksikler None olarak KORUNUR (sikistirma yok)."""
    _, seri, _, _ = _sorgu(asof)
    ref = REFERANS[asof]["seri"]
    assert len(seri) == 8
    for i, (a, b) in enumerate(zip(seri, ref)):
        if b is None:
            assert a is None, f"yuva {i} None olmaliydi"
        else:
            assert a == pytest.approx(b, abs=1e-9), f"yuva {i}"


@pytest.mark.parametrize("asof", list(REFERANS))
def test_point_in_time_surum_secimi(asof):
    """selected_version_tag analysis_date'e gore DEGISMELI."""
    _, _, surumler, _ = _sorgu(asof)
    assert surumler == REFERANS[asof]["surumler"]


def test_gelecek_verisi_gecmise_sizmaz():
    """2025-Q1 RESTATED 2025-11-20'de yayimlandi; 2025-10-01 analizinde GORUNMEMELI."""
    _, s26, v26, _ = _sorgu("2026-03-01")
    _, s25, v25, _ = _sorgu("2025-10-01")
    assert v26[4] == "RESTATED" and s26[4] == pytest.approx(0.2100)
    assert v25[4] == "ORIGINAL" and s25[4] == pytest.approx(0.2689)


@pytest.mark.parametrize("asof", list(REFERANS))
def test_selected_published_at(asof):
    """Point-in-time izlenebilirligi: hangi surumun HANGI TARIHTE secildigi."""
    _, _, _, yayin = _sorgu(asof)
    assert yayin == REFERANS[asof]["yayinlar"]


@pytest.mark.parametrize("asof", list(REFERANS))
def test_motor_metrikleri(asof):
    """Seri motora verildiginde referans metrikler cikmali."""
    _, seri, _, _ = _sorgu(asof)
    ref = REFERANS[asof]
    u = estimate_roe_uncertainty(seri)
    assert u["trend_slope"] == pytest.approx(ref["slope"], abs=1e-6)
    assert u["sd_roe_effective"] == pytest.approx(ref["sd_eff"], abs=1e-8)
    assert u["n_valid"] == ref["n_valid"]
    assert u["roe_missing_count"] == ref["eksik"]
    v = sorted(x for x in seri if x is not None)
    assert median(v[1:-1]) == pytest.approx(ref["roe_sus"], abs=1e-6)


def test_dogru_sorgu_fiziksel_siradan_bagimsiz():
    """
    KARARLI CI SARTI: dogru sorgu iki fiziksel kurulumda da AYNI sonucu vermeli.
    (Tie-break'siz sorgunun A/B'de farklilasmasi kanittir ama CI sarti DEGILDIR --
    sonucu tanimsiz oldugu icin baska planda tesadufen ayni cikabilir.)
    """
    sonuc = {}
    for tbl in ("fixture_order_a", "fixture_order_b"):
        out = _psql(["-c", f"""
            SELECT version_tag||'|'||roe_ttm FROM (
                SELECT DISTINCT ON (period_end) version_tag, roe_ttm
                FROM {tbl} WHERE published_at <= '2026-03-01'
                ORDER BY period_end, published_at DESC,
                         version_sequence DESC, record_id DESC) x;"""])
        sonuc[tbl] = out.strip()
    assert sonuc["fixture_order_a"] == sonuc["fixture_order_b"]
    assert "RESTATED" in sonuc["fixture_order_a"]
    assert "0.2400" in sonuc["fixture_order_a"]


def test_izole_tiebreak_kontrollu_kurulumda():
    """
    Tie-break'siz sorgunun sonucu TANIMSIZDIR; ana fiksturde A/B farkini iddia
    etmek KARARSIZ testtir (tablo yeniden kurulunca fiziksel sira degisip iki
    sorgu tesadufen ayni sonucu verebiliyor -- bu gozlemlendi).

    Kararli sekil: fiziksel sirasi KONTROLLU iki kurulumda dogru sorgunun AYNI
    sonucu vermesi. Tie-break'siz sonuc yalniz TANI olarak raporlanir.
    """
    dogru, tiebreaksiz = {}, {}
    for tbl in ("fixture_order_a", "fixture_order_b"):
        dogru[tbl] = _psql(["-c", f"""
            SELECT version_tag||'|'||roe_ttm FROM (
                SELECT DISTINCT ON (period_end) version_tag, roe_ttm FROM {tbl}
                WHERE published_at <= '2026-03-01'
                ORDER BY period_end, published_at DESC,
                         version_sequence DESC, record_id DESC) x;"""]).strip()
        tiebreaksiz[tbl] = _psql(["-c", f"""
            SELECT version_tag||'|'||roe_ttm FROM (
                SELECT DISTINCT ON (period_end) version_tag, roe_ttm FROM {tbl}
                WHERE published_at <= '2026-03-01'
                ORDER BY period_end, published_at DESC) x;"""]).strip()

    # KARARLI SART: dogru sorgu iki kurulumda da ayni
    assert dogru["fixture_order_a"] == dogru["fixture_order_b"] == "RESTATED|0.2400"
    # TANI (assert DEGIL): tie-break'siz sonuclar kayda gecer
    print(f"\n  [tani] tie-break'siz A={tiebreaksiz['fixture_order_a']} "
          f"B={tiebreaksiz['fixture_order_b']}")


def test_izole_sorgu_yalniz_order_by_ile_ayrilir():
    """
    Izole sorgunun ISLEVI: dogru sorguyla ayni takvim yuvasi, ayni pencere,
    ayni LEFT JOIN. Fark cikarsa sebep KESINLIKLE tie-break'tir.
    Fark CIKMAMASI da gecerlidir (tanimsiz davranis).
    """
    slots_d, dogru, v_dogru, _ = _sorgu("2026-03-01")
    slots_i, izole, v_izole, _ = _sorgu("2026-03-01", "sorgu_tiebreak_izole.sql")
    assert slots_d == slots_i == BEKLENEN_SLOTS
    farkli = [i for i in range(8) if dogru[i] != izole[i]]
    assert farkli in ([], [5]), f"fark yalniz 2025-Q2'de olabilir, cikan {farkli}"
    assert v_dogru[5] == "RESTATED", "dogru sorgu her zaman RESTATED secmeli"
    if farkli == [5]:
        u = estimate_roe_uncertainty(izole)
        assert u["trend_slope"] == pytest.approx(0.0217142857, abs=1e-9)
        assert u["sd_roe_effective"] == pytest.approx(0.01128894, abs=1e-8)
        # n_valid / eksik / roe_sus AYNI kalir -> tek imza selected_version_tag
        assert u["n_valid"] == 7 and u["roe_missing_count"] == 1


# ---------------------------------------------- gun ici (timestamptz) look-ahead
INTRADAY_PIT_SQL = """
    SELECT version_tag || '|' || roe_ttm FROM (
        SELECT DISTINCT ON (period_end) version_tag, roe_ttm
        FROM fixture_intraday
        WHERE published_at <= '{ts}'::timestamptz
        ORDER BY period_end, published_at DESC,
                 version_sequence DESC, record_id DESC) x;
"""

INTRADAY_TARIH_SQL = """
    SELECT version_tag || '|' || roe_ttm FROM (
        SELECT DISTINCT ON (period_end) version_tag, roe_ttm
        FROM fixture_intraday
        WHERE published_at::date <= '{ts}'::timestamptz::date
        ORDER BY period_end, published_at DESC,
                 version_sequence DESC, record_id DESC) x;
"""


@pytest.mark.parametrize("analysis_at,beklenen", [
    ("2025-08-08 09:00:00+03", None),               # henuz hicbir sey yayimlanmadi
    ("2025-08-08 12:00:00+03", "ORIGINAL|0.2809"),  # 17:00 verisi SIZMAMALI
    ("2025-08-08 18:00:00+03", "RESTATED|0.2400"),
])
def test_intraday_point_in_time(analysis_at, beklenen):
    """
    `published_at <= analysis_at::timestamptz` ile gun ici sizinti olmamali.
    Bu test olmadan "14 test gecti" sonucu timestamptz guvenligini KANITLAMIYORDU.
    """
    out = _psql(["-c", INTRADAY_PIT_SQL.format(ts=analysis_at)]).strip()
    assert (out or None) == beklenen


@pytest.mark.parametrize("analysis_at,pit,tarihe_indirgenmis", [
    ("2025-08-08 09:00:00+03", None, "RESTATED|0.2400"),
    ("2025-08-08 12:00:00+03", "ORIGINAL|0.2809", "RESTATED|0.2400"),
])
def test_intraday_tarihe_indirgeme_sizdirir(analysis_at, pit, tarihe_indirgenmis):
    """
    `published_at::date <= analysis_date::date` yanlis desendir: 17:00'de yayimlanan
    veri 09:00 ve 12:00 analizlerine sizar. 09:00 satiri en kotusu -- henuz HICBIR
    SEY yayimlanmamisken sistem yedi saat sonraki veriyi kullaniyor.
    """
    dogru = _psql(["-c", INTRADAY_PIT_SQL.format(ts=analysis_at)]).strip() or None
    yanlis = _psql(["-c", INTRADAY_TARIH_SQL.format(ts=analysis_at)]).strip() or None
    assert dogru == pit
    assert yanlis == tarihe_indirgenmis
    assert dogru != yanlis, "bu senaryoda sizinti gosterilebilmeli"


def test_fikstur_semasi_timestamptz():
    """
    DIKKAT -- BU TEST URETIM SORGUSUNU DENETLEMEZ.
    Yalniz fikstur tablosunun `published_at timestamptz` kullandigini dogrular.

    ENTEGRASYONDA: gun ici senaryolar (09:00 / 12:00 / 18:00) URETIM sorgusundan
    gecirilmeli; SQL metninde "timestamptz" kelimesi aramak YETERLI DEGILDIR.
    Uretim sorgusu dogrudan fixture_intraday uzerinde kosturulup
    test_intraday_point_in_time ile ayni sonuclari vermelidir.
    """
    metin = (BASE_DIR / "fixture_intraday_timestamptz.sql").read_text(encoding="utf-8")
    assert "published_at     timestamptz NOT NULL" in metin
