"""
V21 Reconciliation-1 — kalicilik.

Ayni desen: IMMUTABLE (icerik degistirilemez) + IDEMPOTENT (ayni kimlik +
ayni icerik -> yeni satir olusmaz, created_at tazelenmez; ayni kimlik +
farkli icerik -> sert ret).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.analytics.reconciliation_impact_orchestrator import (
    RECONCILER_VERSION,
    RECONCILIATION_TYPE,
    ReconciliationResult,
    reconciliation_sha256,
)

TARGET_COLUMNS: tuple[str, ...] = (
    "application_run_id", "ticker", "readiness_status",
)

RUN_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "reconciliation_sha256", "reconciliation_type",
    "reconciler_version", "application_run_id", "impact_plan_id",
    "analysis_at", "started_at", "finished_at", "status", "expected_count",
    "actual_count", "missing_count", "unexpected_count", "stale_count",
    "diagnostics",
)

FINDING_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "finding_seq", "ticker", "finding_type", "detail",
)

RUN_LOOKUP = ("SELECT reconciliation_sha256 FROM analytics.reconciliation_run "
             "WHERE reconciliation_run_id=%s")

ACTUAL_ROWS_SQL = """
SELECT ticker, run_id FROM analytics.company_total_rasyo_result
WHERE analysis_at = %(analysis_at)s AND ticker = ANY(%(tickers)s::text[])
"""

ACTUAL_ROWS_BY_RUN_SQL = """
SELECT ticker, run_id FROM analytics.company_total_rasyo_result
WHERE analysis_at = %(analysis_at)s AND run_id = %(run_id)s
"""


class ReconciliationPersistenceError(ValueError):
    pass


class ReconciliationConflict(ReconciliationPersistenceError):
    """Ayni reconciliation_run_id FARKLI icerikle geldi."""


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


TARGET_INSERT = _insert_sql("analytics.impact_application_target", TARGET_COLUMNS)
RUN_INSERT = _insert_sql("analytics.reconciliation_run", RUN_COLUMNS)
FINDING_INSERT = _insert_sql("analytics.reconciliation_finding", FINDING_COLUMNS)


def persist_application_targets(conn: Any, *, application_run_id: str,
                                tickers: Iterable[str]) -> int:
    """
    Bir uygulama denemesinin HEDEFLEDIGI (readiness bariyerini gecmis)
    ticker kumesini kalicilastirir. Reconciliation'in "beklenen kume"
    kaynagidir.
    """
    import psycopg2.extras

    kodlar = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not application_run_id or not application_run_id.strip():
        raise ReconciliationPersistenceError("application_run_id dolu metin olmali")
    if not kodlar:
        return 0
    satirlar = [(application_run_id, t, "READY") for t in kodlar]
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, TARGET_INSERT, satirlar)
    return len(satirlar)


def fetch_actual_rows_full(conn: Any, *, analysis_at, expected_tickers: Iterable[str],
                           orchestrator_run_id: str):
    """
    RECONCILIATION ICIN DOGRU "actual" kumesi. Iki alt sorgunun BIRLESIMI:

      1) beklenen ticker'lar icin KAYNAK NE OLURSA OLSUN mevcut satir
         (MISSING'i -- yoksa -- ve STALE'i -- var ama run_id farkli --
         yakalamak icin ticker filtresiyle sorgular)
      2) BU run_id altinda kalicilastirilmis TUM ticker'lar (beklenen
         disinda kalan bir ticker'i UNEXPECTED olarak yakalamak icin
         run_id filtresiyle sorgular)

    Yalniz (1) kullanmak UNEXPECTED'i KACIRIR (beklenmeyen ticker sorgu
    listesinde hic yoktur). Yalniz (2) kullanmak STALE'i BOZAR (yanlis
    run_id'li satir bu sorguda hic donmez, MISSING gibi gorunur). Bu
    fonksiyon ikisini ayri ayri calistirip TICKER BAZINDA birlestirir.
    """
    from src.analytics.reconciliation_impact_orchestrator import ActualRow

    beklenen_satirlari = fetch_actual_rows(conn, analysis_at=analysis_at,
                                           tickers=expected_tickers)
    run_satirlari = fetch_actual_rows_for_run(conn, analysis_at=analysis_at,
                                              run_id=orchestrator_run_id)
    birlesim: dict[str, str] = {}
    for satir in (*beklenen_satirlari, *run_satirlari):
        if satir.ticker in birlesim and birlesim[satir.ticker] != satir.current_run_id:
            raise ReconciliationPersistenceError(
                f"{satir.ticker} icin iki sorgu farkli run_id verdi -- "
                "veri tutarsizligi")
        birlesim[satir.ticker] = satir.current_run_id
    return [ActualRow(ticker=t, current_run_id=r) for t, r in birlesim.items()]


def fetch_actual_rows(conn: Any, *, analysis_at, tickers: Iterable[str]):
    """
    SINIRLI SORGU: yalniz VERILEN ticker'larin GUNCEL durumunu getirir.

    UNEXPECTED tespiti icin YETERSIZDIR -- beklenen kume disindaki bir
    ticker zaten sorgu listesinde olmadigi icin hic gorunmez. UNEXPECTED'i
    yakalamak istiyorsan `fetch_actual_rows_for_run()` kullan.

    Bu fonksiyon yalniz "beklenen ticker'larin durumu ne" sorusuna hizli
    cevap gerektiginde (orn. MISSING/STALE'i dogrulama) kullanilmalidir.
    """
    from src.analytics.reconciliation_impact_orchestrator import ActualRow

    kodlar = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not kodlar:
        return []
    with conn.cursor() as cur:
        cur.execute(ACTUAL_ROWS_SQL, {"analysis_at": analysis_at, "tickers": kodlar})
        return [ActualRow(ticker=t, current_run_id=r) for t, r in cur.fetchall()]


def fetch_actual_rows_for_run(conn: Any, *, analysis_at, run_id: str):
    """
    TAM SORGU: bu `run_id` altinda kalicilastirilmis TUM ticker'lari getirir
    (ticker listesiyle SINIRLANMAZ). Reconciliation'in "actual" tarafi bu
    olmalidir -- aksi halde beklenen kumenin DISINDA islenen bir ticker
    hicbir zaman sorgulanmaz ve UNEXPECTED asla yakalanamaz.
    """
    from src.analytics.reconciliation_impact_orchestrator import ActualRow

    if not run_id or not run_id.strip():
        raise ReconciliationPersistenceError("run_id dolu metin olmali")
    with conn.cursor() as cur:
        cur.execute(ACTUAL_ROWS_BY_RUN_SQL,
                   {"analysis_at": analysis_at, "run_id": run_id})
        return [ActualRow(ticker=t, current_run_id=r) for t, r in cur.fetchall()]


def _run_row(result: ReconciliationResult, sha: str) -> tuple:
    import json
    # stale_check_performed GORUNUR olmali: persisted JSONB'de de tasinir,
    # boylece SQL ile sorgulanabilir (diagnostics->>'stale_check_performed').
    tanilar = dict(result.diagnostics)
    tanilar["stale_check_performed"] = result.stale_check_performed
    return (
        result.reconciliation_run_id, sha, result.reconciliation_type,
        result.reconciler_version, result.application_run_id,
        result.impact_plan_id, result.analysis_at, result.started_at,
        result.finished_at, result.status, len(result.expected),
        len(result.actual), len(result.missing()), len(result.unexpected()),
        len(result.stale()),
        json.dumps(tanilar, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), default=str),
    )


def persist_reconciliation_result(conn: Any,
                                  result: ReconciliationResult) -> dict[str, Any]:
    """
    Idempotent + immutable yazim. Ayni kimlik + ayni icerik -> tabloya
    DOKUNULMAZ (created_at TAZELENMEZ). Ayni kimlik + farkli icerik ->
    ReconciliationConflict, HICBIR SEY overwrite EDILMEZ.
    """
    import psycopg2.extras

    if not isinstance(result, ReconciliationResult):
        raise ReconciliationPersistenceError("result ReconciliationResult olmali")

    sha = reconciliation_sha256(result)
    run_row = _run_row(result, sha)
    if len(run_row) != len(RUN_COLUMNS):
        raise ReconciliationPersistenceError("run tuple sutun sayisi uyusmuyor")

    bulgu_satirlari = []
    for seq, f in enumerate(sorted(
            result.findings, key=lambda x: (x.ticker, x.finding_type))):
        bulgu_satirlari.append(
            (result.reconciliation_run_id, seq, f.ticker, f.finding_type, f.detail))
    for satir in bulgu_satirlari:
        if len(satir) != len(FINDING_COLUMNS):
            raise ReconciliationPersistenceError("finding tuple uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            cur.execute(RUN_LOOKUP, (result.reconciliation_run_id,))
            mevcut = cur.fetchone()
            if mevcut is not None:
                if mevcut[0] != sha:
                    raise ReconciliationConflict(
                        "reconciliation_run_id yeniden kullanildi ve icerik "
                        f"farkli: {result.reconciliation_run_id}")
                return {"created": False, "reconciliation_sha256": sha,
                        "finding_rows": len(bulgu_satirlari)}

            psycopg2.extras.execute_values(cur, RUN_INSERT, [run_row])
            if bulgu_satirlari:
                psycopg2.extras.execute_values(cur, FINDING_INSERT, bulgu_satirlari,
                                               page_size=500)
    return {"created": True, "reconciliation_sha256": sha,
            "finding_rows": len(bulgu_satirlari)}
