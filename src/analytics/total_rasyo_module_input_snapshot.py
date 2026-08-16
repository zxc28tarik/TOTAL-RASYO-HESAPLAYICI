"""
V22-A — TUKETIM-ANI lineage snapshot.

Total Rasyo hesaplanirken TUKETILEN alti modul girdisini, tuketim aninda
kalicilastirir. SONRADAN YENIDEN TURETILMEZ.

BAGIMSIZ DOGRULAMA VE TOCTOU KORUMASI
--------------------------------------
V19'un point-in-time okuyucusu (`total_rasyo_module_reader.py`) kimlik
alanlarini (source_run_key) HIC tasimaz -- CompanyResult nesnesinde de
yoktur. Bu modul V19'un dosyalarina DOKUNMADAN, module_scores'u BAGIMSIZ
bir sorguyla TEKRAR okur ve donen `analysis_at`'in, orkestratörün zaten
kullandigi `module_source_at` ile TAM ESLESIP eslesmedigini kontrol eder.

Eslesirse: bu SATIRIN gercekten kullanilan satir oldugu makul guvenle
kabul edilir, kimlik alanlari (source_run_key, asof_date, analysis_at)
kaydedilir, `identity_known=True`.

Eslesmezse (module_scores esnada baskaca degismis olabilir -- TOCTOU):
kimlik alanlari YAZILMAZ, `identity_known=False`. Sessizce yanlis kimlik
iddia ETMEK yerine acikca "bilinmiyor" denir.

M2: sektor motorundan gelir, module_scores'ta YOKTUR. Bu modul icin
identity_known HER ZAMAN False'tur -- deger + kaynak zamani kaydedilir,
kimlik KANITLANAMAZ.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from src.analytics.total_rasyo_score import MODULE_KEYS

INPUT_COLUMNS: tuple[str, ...] = (
    "total_rasyo_run_id", "ticker", "module", "module_score", "module_missing",
    "module_source_at", "module_analysis_at", "module_asof_date",
    "module_source_run_key", "identity_known",
)

_INDEPENDENT_LOOKUP_SQL = """
SELECT asof_date, analysis_at, source_run_key, m1, m3, ek1, ek4, ek9
FROM analytics.module_scores
WHERE ticker = %(ticker)s AND analysis_at = %(module_source_at)s
"""

_MODULE_COLUMN = {"M1": "m1", "M3": "m3", "Ek1": "ek1", "Ek4": "ek4", "Ek9": "ek9"}


class ModuleInputSnapshotError(ValueError):
    pass


def _insert_sql() -> str:
    return f"INSERT INTO analytics.total_rasyo_module_input\n  ({', '.join(INPUT_COLUMNS)})\nVALUES %s\n"


INPUT_INSERT = _insert_sql()


def _independent_identity(conn: Any, *, ticker: str, module: str,
                          module_source_at: Optional[datetime],
                          module_score: Optional[float]) -> tuple:
    """
    BAGIMSIZ sorgu + DEGER TUTARLILIGI kontrolu. Donus: (analysis_at,
    asof_date, source_run_key, identity_known).

    module_source_at None ise (modul zaten eksikti) kimlik sorgulanamaz --
    identity_known=False, sorgu bile YAPILMAZ.

    ONEMLI TASARIM NOKTASI: SQL zaten `analysis_at = module_source_at` ile
    filtreledigi icin donen satirin analysis_at'i HER ZAMAN eslesir --
    bunu Python'da AYRICA kontrol etmek YANILTICI bir "iki katmanli guven"
    izlenimi verir ama aslinda ERISILEMEZ koddur (mutasyon testiyle
    dogrulandi). GERCEK risk baskadir: bir DUZELTME kosusu AYNI analysis_at
    etiketiyle DEGERI SESSIZCE degistirebilir (upsert PK'si ticker+asof_date
    +horizon_days'tir, analysis_at bir SUTUNDUR, ozgunlugu GARANTI ETMEZ).
    Bu yuzden asil kontrol DEGER TUTARLILIGIDIR: donen satirin bu modul
    icin tasidigi deger, orkestratörün kullandigi degerle (kucuk bir
    tolerans disinda) ESLESMIYORSA kimlik BILINMEYEN sayilir.

    Deger EŞLEŞMESI kimligi KANITLAMAZ (ayni deger iki farkli kaynaktan
    gelebilir) -- yalniz deger UYUŞMAZLIĞI kimligi GECERSIZ KILAR. Bu
    asimetri bilinclidir.
    """
    if module_source_at is None:
        return None, None, None, False
    sutun = _MODULE_COLUMN.get(module)
    if sutun is None:
        return None, None, None, False
    with conn.cursor() as cur:
        cur.execute(_INDEPENDENT_LOOKUP_SQL,
                   {"ticker": ticker, "module_source_at": module_source_at})
        satirlar = cur.fetchall()
    if len(satirlar) != 1:
        # Hic satir yok VEYA tekil degil -- kimligi GUVENLE belirleyemeyiz.
        return None, None, None, False
    asof_date, analysis_at, source_run_key, m1, m3, ek1, ek4, ek9 = satirlar[0]
    guncel_deger = {"m1": m1, "m3": m3, "ek1": ek1, "ek4": ek4, "ek9": ek9}[sutun]
    if module_score is None or guncel_deger is None:
        return None, None, None, False
    if abs(float(guncel_deger) - float(module_score)) > 1e-9:
        # AYNI analysis_at etiketi altinda DEGER DEGISMIS -- sessiz bir
        # duzeltme/backfill olabilir. Kimlik GECERSIZ.
        return None, None, None, False
    if source_run_key is None:
        # KRITIK AYRIM: "lineage satiri var" != "source identity biliniyor".
        # analysis_at + deger eslesmesi yalniz HANGI module_scores satirinin
        # tuketildigini gosterir; GERCEK URETIM KOSUSU kimligini KANITLAMAZ.
        # module_scores.source_run_key SIRADAN (BANK-disi) gunluk pipeline
        # icin genelde NULL'dur -- bu durumda sahte/gecici bir kimlik
        # UYDURULMAZ; identity_known acikca False kalir. Zaman/deger
        # eslesmesi ek guvencedir, gercek kimlik kanitinin YERINE gecmez.
        return None, None, None, False
    return analysis_at, asof_date, source_run_key, True


def build_module_input_rows(
    conn: Any, *, total_rasyo_run_id: str, results: list[Mapping[str, Any]],
) -> list[tuple]:
    """
    Orkestratörün rapor sonuclarindan (CompanyResult -> dict, 'modules'
    alt-sozlugu) alti modulun TUKETIM-ANI satirlarini kurar.

    M2 icin BAGIMSIZ sorgu hic DENENMEZ -- module_scores'ta M2 yoktur,
    denemek yanlis bir arama YAPMAKTAN farksiz olurdu. identity_known
    dogrudan False yazilir.
    """
    if not isinstance(total_rasyo_run_id, str) or not total_rasyo_run_id.strip():
        raise ModuleInputSnapshotError("total_rasyo_run_id dolu metin olmali")

    satirlar: list[tuple] = []
    for r in results:
        ticker = r.get("ticker")
        if not isinstance(ticker, str) or not ticker.strip():
            raise ModuleInputSnapshotError("sonucta ticker eksik")
        kod = ticker.strip().upper()
        moduller = r.get("modules") or {}

        for anahtar in MODULE_KEYS:
            girdi = moduller.get(anahtar) or {}
            skor = girdi.get("score")
            eksik = bool(girdi.get("missing"))
            kaynak_zamani = girdi.get("source_at")

            if anahtar == "M2":
                analysis_at = asof_date = source_run_key = None
                identity_known = False
            else:
                analysis_at, asof_date, source_run_key, identity_known = (
                    _independent_identity(conn, ticker=kod, module=anahtar,
                                          module_source_at=kaynak_zamani,
                                          module_score=skor))

            satirlar.append((
                total_rasyo_run_id, kod, anahtar, skor, eksik, kaynak_zamani,
                analysis_at, asof_date, source_run_key, identity_known,
            ))
    return satirlar


def persist_module_input_snapshot(conn: Any, *, total_rasyo_run_id: str,
                                  results: list[Mapping[str, Any]]) -> int:
    """
    Tuketim-ani snapshot'i kalicilastirir. `with conn:` ile atomik.

    ATOMIKLIK SINIRI (acikca belirtilir): bu, V19'un
    persist_total_rasyo_report() ile AYNI transaction'da DEGILDIR --
    V19'un kapali dosyasina dokunmamak icin BILEREK ayri tutuldu. Kanonik
    sonuc commit olur ama bu snapshot HERHANGI bir nedenle basarisiz
    olursa, o kosu icin tuketim kaniti EKSIK kalir. Reconciliation
    (V22-B) bu durumu sessizce PASS saymamali; ayri bir "kanit yok"
    durumuyla ele alinmalidir.
    """
    import psycopg2.extras

    satirlar = build_module_input_rows(
        conn, total_rasyo_run_id=total_rasyo_run_id, results=results)
    if not satirlar:
        return 0
    for satir in satirlar:
        if len(satir) != len(INPUT_COLUMNS):
            raise ModuleInputSnapshotError("tuple sutun sayisi uyusmuyor")
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, INPUT_INSERT, satirlar,
                                           page_size=500)
    return len(satirlar)
