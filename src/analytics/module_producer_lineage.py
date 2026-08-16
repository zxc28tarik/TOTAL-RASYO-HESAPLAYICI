"""
V22-A — URETICI TARAFI modul soy kutugu fan-out.

analytics.module_scores GENIS bir tablodur: (ticker, asof_date, horizon_days)
basina TEK satirda m1..ek9 birlikte tutulur. analytics.module_production_lineage
ise DAR/uzun bir tablodur: (ticker, module, analysis_at) basina TEK satir.
Bu modul genis satiri dar satirlara FAN-OUT eder.

KAPSAM: yalniz M1, M3, Ek1, Ek4, Ek9. M2 BILEREK DISLANIR -- Total Rasyo
sozlesmesinde M2 module_scores'tan DEGIL sektor motorundan gelir (V19 cift
sayim yasagi); buraya M2 yazmak yanlis bir kimlik iddia ederdi.

Bu fonksiyon run_daily_pipeline.py'nin GENIS upsert'inden HEMEN SONRA,
AYNI (ticker, analysis_at, source_run_key) degerleriyle cagrilir. V19/V20/
V21'in kapali dosyalarina DOKUNULMAZ; bu tamamen YENI bir yazma yoludur.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

MODULE_COLUMN_MAP: tuple[tuple[str, str], ...] = (
    ("m1", "M1"), ("m3", "M3"), ("ek1", "Ek1"), ("ek4", "Ek4"), ("ek9", "Ek9"),
)

LINEAGE_INSERT_SQL = """
INSERT INTO analytics.module_production_lineage
  (ticker, module, analysis_at, engine_family, source_version_id,
   source_period_end, produced_at, calculation_profile, calculation_version,
   impact_plan_id, upstream_fingerprint, diagnostics)
VALUES %s
ON CONFLICT (ticker, module, analysis_at) DO UPDATE SET
  source_version_id = EXCLUDED.source_version_id,
  source_period_end = EXCLUDED.source_period_end,
  produced_at = EXCLUDED.produced_at,
  calculation_profile = EXCLUDED.calculation_profile,
  calculation_version = EXCLUDED.calculation_version
"""


class ProducerLineageError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleRow:
    """
    Genis module_scores satirindan cikarilan tek bir ticker'in kimligi.

    Skor DEGERLERI BILEREK burada YOK: lineage kimlik tasir, deger tasimaz
    (deger module_scores'ta zaten gercek kaynaktir). Bu satirin varligi
    "bu ticker bu kosuda uretime GIRDI" demektir; tek tek modullerin o gun
    eksik olup olmadigi module_scores/total_rasyo_module_input'ta izlenir.
    """
    ticker: str
    period_end: Optional[date]


def _validate(row: ModuleRow) -> ModuleRow:
    if not isinstance(row.ticker, str) or not row.ticker.strip():
        raise ProducerLineageError("ticker dolu metin olmali")
    return row


def fanout_lineage_rows(
    rows: list[ModuleRow],
    *,
    analysis_at: datetime,
    produced_at: datetime,
    source_run_key: Optional[str],
    calculation_profile: str = "RUN_DAILY_PIPELINE_V1",
    calculation_version: int = 1,
) -> list[tuple]:
    """
    SAF fonksiyon: genis satirlari dar lineage tuple'larina cevirir.
    Veritabanina DOKUNMAZ.

    NULL degerli modul icin de satir YAZILIR (missing=NULL/None deger ile);
    boylece "bu modul o gun hic uretilmedi" ile "uretildi ama eksikti" ayirt
    edilebilir kalir -- lineage kaydinin kendisi VAR olmasi degeri
    bilinmedigi anlamina gelmez.
    """
    if not isinstance(analysis_at, datetime) or analysis_at.tzinfo is None:
        raise ProducerLineageError("analysis_at timezone bilgili olmali")
    if not isinstance(produced_at, datetime) or produced_at.tzinfo is None:
        raise ProducerLineageError("produced_at timezone bilgili olmali")

    tuple_lar: list[tuple] = []
    for ham in rows:
        satir = _validate(ham)
        kod = satir.ticker.strip().upper()
        # NOT: skor degeri BURAYA yazilmaz. Lineage yalniz KIMLIK tasir;
        # skor module_scores'ta zaten GERCEK KAYNAKTIR. Ayni degeri iki
        # yerde tutmak, birinin bayat kalma riskini dogururdu.
        for _, modul in MODULE_COLUMN_MAP:
            tuple_lar.append((
                kod, modul, analysis_at, None, source_run_key,
                satir.period_end, produced_at, calculation_profile,
                calculation_version, None, None, "{}",
            ))
    return tuple_lar


def persist_producer_lineage(conn: Any, rows: list[ModuleRow], *,
                             analysis_at: datetime, produced_at: datetime,
                             source_run_key: Optional[str],
                             calculation_profile: str = "RUN_DAILY_PIPELINE_V1",
                             calculation_version: int = 1) -> int:
    """
    Fan-out edilmis satirlari kalicilastirir. `with conn:` ile atomik.

    Bu, GUNLUK PIPELINE'in normal akisina eklenen YENI bir yan etkidir;
    module_scores yazimini DEGISTIRMEZ, yalniz ONA EK bir kayit üretir.
    """
    import psycopg2.extras

    if not rows:
        return 0
    tuple_lar = fanout_lineage_rows(
        rows, analysis_at=analysis_at, produced_at=produced_at,
        source_run_key=source_run_key, calculation_profile=calculation_profile,
        calculation_version=calculation_version)
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, LINEAGE_INSERT_SQL, tuple_lar,
                                           page_size=1000)
    return len(tuple_lar)
