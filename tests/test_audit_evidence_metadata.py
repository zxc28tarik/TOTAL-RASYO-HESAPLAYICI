"""
Denetim kanit dosyalarinin METADATA tutarliligi.

V20 kapanis denetiminde bulundu: `--count 1500` ile kosuldugunda JSON
`total=1500` yaziyordu ama `distribution` hala sabit 15.000'lik tabloyu
tasiyordu. Kanit dosyasinin makine-okunur metadata'si, insan okunur
ozetten daha az dogru OLAMAZ.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.analytics.change_impact_self_audit import DISTRIBUTION, TOTAL


def calistir(tmp_path: Path, count: int) -> dict:
    hedef = tmp_path / f"audit_{count}.json"
    sonuc = subprocess.run(
        [sys.executable, "-m", "src.analytics.change_impact_self_audit",
         "--count", str(count), "--json", str(hedef)],
        capture_output=True, text=True, timeout=1800,
        cwd=Path(__file__).resolve().parents[1])
    assert sonuc.returncode == 0, sonuc.stdout[-2000:] + sonuc.stderr[-2000:]
    return json.loads(hedef.read_text(encoding="utf-8"))


@pytest.mark.parametrize("count", [1500, 3000])
def test_dagilim_toplami_total_ile_esit(tmp_path, count):
    """sum(distribution) == total. Sabit tablo yazilirsa bu kirilir."""
    d = calistir(tmp_path, count)
    assert sum(d["distribution"].values()) == d["total"]
    assert d["total"] == count or abs(d["total"] - count) <= len(DISTRIBUTION)


@pytest.mark.parametrize("count", [1500, 3000])
def test_dagilim_by_type_ile_tutarli(tmp_path, count):
    d = calistir(tmp_path, count)
    for tur, adet in d["distribution"].items():
        g = d["by_type"][tur]
        assert adet == g["gecti"] + g["kaldi"], f"{tur} tutarsiz"


@pytest.mark.parametrize("count", [1500, 3000])
def test_kismi_kosu_full_run_isaretlemez(tmp_path, count):
    """Kismi kosu, tam denetim kaniti gibi gorunmemeli."""
    d = calistir(tmp_path, count)
    assert d["is_full_run"] is False
    assert sum(d["planned_distribution"].values()) == TOTAL


def test_tam_kosu_planlanan_dagilimla_ayni(tmp_path):
    """15.000'lik tam kosuda iki dagilim BIREBIR ayni olmali."""
    d = calistir(tmp_path, TOTAL)
    assert d["is_full_run"] is True
    assert d["total"] == TOTAL
    assert sum(d["distribution"].values()) == TOTAL
    assert d["distribution"] == d["planned_distribution"]
    assert d["passed"] == TOTAL and d["failed"] == 0


def test_paketlenen_kanit_dosyasi_tutarli():
    """Depoda duran kanit JSON'u kendi icinde tutarli olmali."""
    yol = Path(__file__).resolve().parents[1] / "SELF_AUDIT_CHANGE_IMPACT_V20.json"
    if not yol.exists():
        pytest.skip("kanit dosyasi yok")
    d = json.loads(yol.read_text(encoding="utf-8"))
    assert sum(d["distribution"].values()) == d["total"]
    for tur, adet in d["distribution"].items():
        g = d["by_type"][tur]
        assert adet == g["gecti"] + g["kaldi"]


def test_migration_sayisi_makefile_ile_tutarli():
    """
    V20 kapanis denetiminde "30 migration" raporlanmisti; gercek sayi 29.
    sql/ dizinindeki dosya sayisi migration sayisi DEGILDIR:
      004_fill_sector_group      -> data/backfill, ayri hedef
      012/014_bank_point_in_time -> parametrik sorgu sablonu, hic calismaz
    """
    import re

    kok = Path(__file__).resolve().parents[1]
    makefile = (kok / "Makefile").read_text(encoding="utf-8")

    def hedef_bloklari(ad: str) -> str:
        eslesme = re.search(rf"^{ad}:\n((?:\t.*\n)+)", makefile, re.M)
        return eslesme.group(1) if eslesme else ""

    calisan = set()
    for hedef in ("core", "migrate"):
        calisan |= set(re.findall(r"psql -f sql/([0-9_a-z]+\.sql)",
                                  hedef_bloklari(hedef)))

    assert len(calisan) == 34, f"schema migration sayisi {len(calisan)}"
    assert "004_fill_sector_group.sql" not in calisan
    assert "012_bank_point_in_time_slots.sql" not in calisan
    assert "014_bank_point_in_time_slots_batch.sql" not in calisan
    # V20'nin dort migration'i zincirde olmali.
    for yeni in ("031_total_rasyo_restate.sql", "032_impact_plan.sql",
                 "033_impact_runtime_roles.sql",
                 "034_module_production_lineage.sql",
                 "035_reconciliation_impact_vs_actual.sql",
                 "036_total_rasyo_module_input.sql",
                 "037_reconciliation_module_freshness.sql",
                 "038_total_rasyo_restate_hardening.sql",
                 "039_restate_pit_reconciliation.sql"):
        assert yeni in calisan, f"{yeni} zincirde yok"


def test_belgelerde_yanlis_migration_sayisi_yok():
    """'30 migration' ifadesi belgelerde KALMAMALI."""
    kok = Path(__file__).resolve().parents[1]
    for yol in [kok / "docs" / "CLAUDE_DEVIR_NOTU.md",
                kok / "docs" / "CALISMA_GUNLUGU.md"]:
        if not yol.exists():
            continue
        metin = yol.read_text(encoding="utf-8")
        assert "30 migration" not in metin, f"{yol.name}: yanlis migration sayisi"


def test_belgeler_ayni_regresyon_sayisini_tasir():
    """
    V20 kapanisinda ucuncu kez ayni sinif hata cikti: bir kanit belgesi
    guncellenip digeri BAYAT kaldi (devir notu 1056/1190, son dogrulama
    1066/1200 diyordu). Iki belge ayni HEAD'i tarif ettigi icin ayni sayiyi
    tasimak ZORUNDA.

    Bu test, kanit belgeleri arasindaki sayi celiskisini otomatik yakalar.
    """
    import re

    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")

    def sayi(desen: str):
        e = re.search(desen, metin, re.I)
        return int(e.group(1)) if e else None

    pg_var = sayi(r"Tam regresyon \(PG var\)\s*:\s*(\d+) passed")
    pg_yok = sayi(r"PostgreSQL olmayan ortam\s*:\s*(\d+) passed")
    atlanan = sayi(r"PostgreSQL olmayan ortam\s*:\s*\d+ passed,\s*(\d+) skipped")

    assert pg_var and pg_yok and atlanan, "devir notunda regresyon sayilari yok"
    # PG'siz kosuda atlanan testler, PG'li kosuda gecer: toplam AYNI olmali.
    assert pg_yok + atlanan == pg_var, (
        f"tutarsiz: {pg_yok} + {atlanan} != {pg_var}")


def test_devir_notunda_yanlis_backfill_iddiasi_yok():
    """
    004_fill_sector_group.sql ayri bir hedeftedir ve temiz kurulumda
    CALISTIRILMAZ; '+1 sector backfill' diye raporlanamaz.
    """
    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")
    assert "1 sector backfill, ayrı hedef)" not in metin


def test_devir_notu_v21_sayilarini_tasir():
    """
    V21 kapanisinda devir notu, self-audit ve E2E sayilarini ACIKCA
    belirtmeli; sessizce eksik birakilmasin.
    """
    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")
    assert "V21 reconciliation öz denetimi    : 15000 / 15000" in metin
    assert "V21 reconciliation E2E            :     4 / 4" in metin


def test_reconciliation_self_audit_dagilimi_15000():
    from src.analytics.reconciliation_self_audit import DISTRIBUTION, TOTAL
    assert TOTAL == 15000
    assert sum(v for _, v in DISTRIBUTION) == 15000


def test_reconciliation_e2e_tam_dort_senaryo():
    from src.analytics.reconciliation_e2e_audit import SCENARIOS
    assert len(SCENARIOS) == 4
    adlar = {ad for ad, _ in SCENARIOS}
    assert any("PASS" in a for a in adlar)
    assert any("MISSING" in a for a in adlar)
    assert any("UNEXPECTED" in a for a in adlar)
    assert any("STALE" in a for a in adlar)


def test_reconciliation_module_freshness_self_audit_dagilimi_15000():
    from src.analytics.reconciliation_module_freshness_self_audit import (
        DISTRIBUTION, TOTAL)
    assert TOTAL == 15000
    assert sum(v for _, v in DISTRIBUTION) == 15000


def test_reconciliation_module_freshness_e2e_tam_alti_senaryo():
    from src.analytics.reconciliation_module_freshness_e2e_audit import SCENARIOS
    assert len(SCENARIOS) == 6
    adlar = {ad for ad, _ in SCENARIOS}
    assert any("PASS" in a for a in adlar)
    assert any("TOTAL_STALE" in a for a in adlar)
    assert any("LINEAGE_STALE" in a for a in adlar)
    assert any("INCOMPLETE" in a for a in adlar)
    assert any("look_ahead" in a for a in adlar)
    assert any("m2" in a for a in adlar)


def test_devir_notu_v22b_sayilarini_tasir():
    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")
    assert "V22-B reconciliation öz denetimi  : 15000 / 15000" in metin
    assert "V22-B E2E                         :     6 / 6" in metin


def test_restate_calculator_M2_kontrolu_kod_seviyesinde_var():
    """
    V23-A kilidi: PIT M2 degeri fallback olarak KULLANILAMAZ. Kaynak kodda
    M2'nin HER ZAMAN eksik uretildigini kanitlayan sabit ifade aranir.
    """
    kok = Path(__file__).resolve().parents[1]
    dosya = kok / "src" / "analytics" / "total_rasyo_restate_calculator.py"
    metin = dosya.read_text(encoding="utf-8")
    assert 'RestateModuleValue("M2", None, True, None, None, False)' in metin


def test_devir_notu_v23a_sayilarini_tasir():
    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")
    assert "NO_RESTATE_SOURCE_FOR_M2" in metin
    # Aciklayici cumle icinde "... DENMEZ" seklinde gecmesi SERBEST; ama
    # bir BASARI IDDIASI olarak (DENMEZ/DEGIL olmadan) gecmemeli.
    for satir in metin.splitlines():
        if "RESTATE tamamland" in satir:
            assert "DENMEZ" in satir or "DEGIL" in satir or "değil" in satir, (
                f"RESTATE tamamlandi iddiasi olumsuzlanmadan gecmis: {satir}")


def test_restate_pit_reconciliation_self_audit_dagilimi_15000():
    from src.analytics.restate_pit_reconciliation_self_audit import (
        DISTRIBUTION, TOTAL)
    assert TOTAL == 15000
    assert sum(v for _, v in DISTRIBUTION) == 15000


def test_restate_pit_reconciliation_e2e_tam_dort_senaryo():
    from src.analytics.restate_pit_reconciliation_e2e_audit import SCENARIOS
    assert len(SCENARIOS) == 4
    adlar = {ad for ad, _ in SCENARIOS}
    assert any("INCOMPLETE" in a for a in adlar)
    assert any("view" in a for a in adlar)
    assert any("pit_eksik" in a for a in adlar)
    assert any("idempotent" in a for a in adlar)


def test_restate_pit_kritik_kural_kod_seviyesinde_var():
    """
    'mismatch_count == 0 tek basina PASS uretmez' kuralinin kaynak kodda
    gercekten uygulandigini dogrudan kontrol eder.
    """
    kok = Path(__file__).resolve().parents[1]
    dosya = kok / "src" / "analytics" / "restate_pit_reconciliation.py"
    metin = dosya.read_text(encoding="utf-8")
    assert "if compared_count == 0:" in metin
    assert "durum = STATUS_INCOMPLETE" in metin


def test_devir_notu_v23b_sayilarini_tasir():
    kok = Path(__file__).resolve().parents[1]
    devir = kok / "docs" / "CLAUDE_DEVIR_NOTU.md"
    if not devir.exists():
        pytest.skip("devir notu yok")
    metin = devir.read_text(encoding="utf-8")
    assert "PIT_MISSING" in metin
    assert "RESTATE_INCOMPLETE" in metin
