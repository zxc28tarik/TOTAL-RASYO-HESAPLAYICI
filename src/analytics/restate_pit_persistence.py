"""
V23-B — kalicilik. Ayni desen: IMMUTABLE + IDEMPOTENT.

Bulgu tablosuna YALNIZ GERCEK bulgular yazilir -- "temiz" karsilastirilmis
bir ticker HIC SATIR almaz (V21 deseniyle ayni).
"""
from __future__ import annotations

from typing import Any

from src.analytics.restate_pit_reconciliation import RestatePitReconciliation

RUN_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "reconciliation_sha256", "reconciliation_type",
    "reconciler_version", "restate_run_id", "started_at", "finished_at",
    "status", "fully_verified", "ticker_count", "compared_count",
    "mismatch_count", "pit_missing_count", "restate_incomplete_count",
    "diagnostics",
)

FINDING_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "ticker", "compared", "finding_type",
    "pit_run_id", "pit_final_score", "restate_final_score", "pit_decision",
    "restate_decision", "restate_status",
)

RUN_LOOKUP = ("SELECT reconciliation_sha256 FROM analytics"
             ".reconciliation_restate_run WHERE reconciliation_run_id=%s")


class RestatePitPersistenceError(ValueError):
    pass


class RestatePitConflict(RestatePitPersistenceError):
    """Ayni reconciliation_run_id FARKLI icerikle geldi."""


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


RUN_INSERT = _insert_sql("analytics.reconciliation_restate_run", RUN_COLUMNS)
FINDING_INSERT = _insert_sql("analytics.reconciliation_restate_finding", FINDING_COLUMNS)


def _run_row(result: RestatePitReconciliation, sha: str, *, started_at,
            finished_at) -> tuple:
    import json
    return (
        result.reconciliation_run_id, sha, result.reconciliation_type,
        result.reconciler_version, result.restate_run_id, started_at, finished_at,
        result.status, result.fully_verified, len(result.tickers),
        result.compared_count, result.mismatch_count, result.pit_missing_count,
        result.restate_incomplete_count,
        json.dumps(dict(result.diagnostics), ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), default=str),
    )


def _finding_rows(result: RestatePitReconciliation) -> list[tuple]:
    satirlar = []
    for t in sorted(result.comparisons):
        c = result.comparisons[t]
        for bulgu in c.findings:
            satirlar.append((
                result.reconciliation_run_id, t, c.compared, bulgu, c.pit_run_id,
                c.pit_final_score, c.restate_final_score, c.pit_decision,
                c.restate_decision, c.restate_status,
            ))
    return satirlar


def persist_restate_pit_reconciliation(
    conn: Any, result: RestatePitReconciliation, *, started_at, finished_at,
) -> dict[str, Any]:
    """
    Idempotent + immutable yazim. Ayni kimlik + ayni icerik -> tabloya
    DOKUNULMAZ. Ayni kimlik + farkli icerik -> RestatePitConflict.
    """
    import psycopg2.extras

    from src.analytics.restate_pit_reconciliation import reconciliation_sha256

    if not isinstance(result, RestatePitReconciliation):
        raise RestatePitPersistenceError("result RestatePitReconciliation olmali")

    sha = reconciliation_sha256(result)
    run_row = _run_row(result, sha, started_at=started_at, finished_at=finished_at)
    if len(run_row) != len(RUN_COLUMNS):
        raise RestatePitPersistenceError("run tuple sutun sayisi uyusmuyor")
    finding_rows = _finding_rows(result)
    for satir in finding_rows:
        if len(satir) != len(FINDING_COLUMNS):
            raise RestatePitPersistenceError("finding tuple uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            cur.execute(RUN_LOOKUP, (result.reconciliation_run_id,))
            mevcut = cur.fetchone()
            if mevcut is not None:
                if mevcut[0] != sha:
                    raise RestatePitConflict(
                        "reconciliation_run_id yeniden kullanildi ve icerik "
                        f"farkli: {result.reconciliation_run_id}")
                return {"created": False, "reconciliation_sha256": sha,
                        "finding_rows": len(finding_rows)}

            psycopg2.extras.execute_values(cur, RUN_INSERT, [run_row])
            if finding_rows:
                psycopg2.extras.execute_values(cur, FINDING_INSERT, finding_rows)
    return {"created": True, "reconciliation_sha256": sha,
            "finding_rows": len(finding_rows)}
