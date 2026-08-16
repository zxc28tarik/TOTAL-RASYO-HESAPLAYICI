"""
V22-B — kalicilik. Ayni desen: IMMUTABLE + IDEMPOTENT (V20/V21 ile ayni).
"""
from __future__ import annotations

from typing import Any

from src.analytics.reconciliation_module_freshness import (
    ModuleReconciliationResult,
    reconciliation_sha256,
)

RUN_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "reconciliation_sha256", "reconciliation_type",
    "reconciler_version", "total_rasyo_run_id", "ticker", "analysis_at",
    "started_at", "finished_at", "status", "fully_verified",
    "expected_module_count", "missing_count", "total_stale_count",
    "lineage_stale_count", "freshness_performed_count",
    "lineage_performed_count", "diagnostics",
)

CHECK_COLUMNS: tuple[str, ...] = (
    "reconciliation_run_id", "module", "module_missing", "freshness_performed",
    "freshness_reason", "total_stale", "lineage_performed", "lineage_reason",
    "lineage_stale",
)

RUN_LOOKUP = ("SELECT reconciliation_sha256 FROM analytics"
             ".reconciliation_module_run WHERE reconciliation_run_id=%s")


class ModuleReconciliationPersistenceError(ValueError):
    pass


class ModuleReconciliationConflict(ModuleReconciliationPersistenceError):
    """Ayni reconciliation_run_id FARKLI icerikle geldi."""


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


RUN_INSERT = _insert_sql("analytics.reconciliation_module_run", RUN_COLUMNS)
CHECK_INSERT = _insert_sql("analytics.reconciliation_module_check", CHECK_COLUMNS)


def _run_row(result: ModuleReconciliationResult, sha: str) -> tuple:
    import json

    checks = result.checks
    return (
        result.reconciliation_run_id, sha, result.reconciliation_type,
        result.reconciler_version, result.total_rasyo_run_id, result.ticker,
        result.analysis_at, result.started_at, result.finished_at,
        result.status, result.fully_verified, len(checks),
        len(result.missing_modules()), len(result.total_stale_modules()),
        len(result.lineage_stale_modules()),
        sum(1 for c in checks.values() if c.freshness_performed),
        sum(1 for c in checks.values() if c.lineage_performed),
        json.dumps(dict(result.diagnostics), ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), default=str),
    )


def _check_rows(result: ModuleReconciliationResult) -> list[tuple]:
    satirlar = []
    for modul in sorted(result.checks):
        c = result.checks[modul]
        satirlar.append((
            result.reconciliation_run_id, c.module, c.missing,
            c.freshness_performed, c.freshness_reason, c.total_stale,
            c.lineage_performed, c.lineage_reason, c.lineage_stale,
        ))
    return satirlar


def persist_module_reconciliation(conn: Any,
                                  result: ModuleReconciliationResult) -> dict[str, Any]:
    """
    Idempotent + immutable yazim. Ayni kimlik + ayni icerik -> tabloya
    DOKUNULMAZ. Ayni kimlik + farkli icerik -> ModuleReconciliationConflict.
    """
    import psycopg2.extras

    if not isinstance(result, ModuleReconciliationResult):
        raise ModuleReconciliationPersistenceError(
            "result ModuleReconciliationResult olmali")

    sha = reconciliation_sha256(result)
    run_row = _run_row(result, sha)
    if len(run_row) != len(RUN_COLUMNS):
        raise ModuleReconciliationPersistenceError("run tuple sutun sayisi uyusmuyor")
    check_rows = _check_rows(result)
    for satir in check_rows:
        if len(satir) != len(CHECK_COLUMNS):
            raise ModuleReconciliationPersistenceError("check tuple uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            cur.execute(RUN_LOOKUP, (result.reconciliation_run_id,))
            mevcut = cur.fetchone()
            if mevcut is not None:
                if mevcut[0] != sha:
                    raise ModuleReconciliationConflict(
                        "reconciliation_run_id yeniden kullanildi ve icerik "
                        f"farkli: {result.reconciliation_run_id}")
                return {"created": False, "reconciliation_sha256": sha,
                        "check_rows": len(check_rows)}

            psycopg2.extras.execute_values(cur, RUN_INSERT, [run_row])
            if check_rows:
                psycopg2.extras.execute_values(cur, CHECK_INSERT, check_rows)
    return {"created": True, "reconciliation_sha256": sha,
            "check_rows": len(check_rows)}
