"""
V23-A — kalicilik. Ayni desen: IMMUTABLE + IDEMPOTENT (V20/V21/V22-B ile ayni).

Ayni restate_run_id + ayni (inputs_sha256, results_sha256) -> idempotent
kabul, tabloya DOKUNULMAZ. Ayni restate_run_id + FARKLI icerik -> sert ret.
"""
from __future__ import annotations

from typing import Any

from src.analytics.total_rasyo_restate_calculator import RestateComputation

RUN_COLUMNS: tuple[str, ...] = (
    "restate_run_id", "target_analysis_at", "knowledge_cutoff_at", "started_at",
    "finished_at", "status", "restate_contract_version", "reader_version",
    "inputs_sha256", "results_sha256", "calculation_profile", "calculation_version",
    "company_count", "successful_company_count", "diagnostics",
)

RESULT_COLUMNS: tuple[str, ...] = (
    "restate_run_id", "ticker", "target_analysis_at", "knowledge_cutoff_at",
    "engine_family", "m2_score", "m2_source", "m2_source_at", "m2_missing",
    "m1_score", "m1_source_at", "m1_missing", "m3_score", "m3_source_at",
    "m3_missing", "ek4_score", "ek4_source_at", "ek4_missing", "ek1_score",
    "ek1_source_at", "ek1_missing", "ek9_score", "ek9_source_at", "ek9_missing",
    "good_count_ge8", "good_count_missing", "base_score", "final_score",
    "veto_flag", "decision", "weights_profile", "total_rasyo_status",
    "rejection_reason", "insufficiency_reason", "diagnostics",
)

INPUT_COLUMNS: tuple[str, ...] = (
    "restate_run_id", "ticker", "module", "module_score", "module_missing",
    "module_source_at", "module_source_run_key", "identity_known",
)

RUN_LOOKUP = ("SELECT inputs_sha256, results_sha256 FROM analytics"
             ".total_rasyo_restate_runs WHERE restate_run_id=%s")

ENGINE_FAMILY_DEFAULT = "FINANCIAL"


class RestatePersistenceError(ValueError):
    pass


class RestateConflict(RestatePersistenceError):
    """Ayni restate_run_id FARKLI icerikle geldi."""


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


RUN_INSERT = _insert_sql("analytics.total_rasyo_restate_runs", RUN_COLUMNS)
RESULT_INSERT = _insert_sql("analytics.company_total_rasyo_restate_result", RESULT_COLUMNS)
INPUT_INSERT = _insert_sql("analytics.total_rasyo_restate_module_input", INPUT_COLUMNS)


def _run_row(comp: RestateComputation, *, started_at, finished_at, status: str) -> tuple:
    import json
    return (
        comp.restate_run_id, comp.target_analysis_at, comp.knowledge_cutoff_at,
        started_at, finished_at, status, comp.restate_contract_version,
        comp.reader_version, comp.inputs_sha256, comp.results_sha256,
        comp.calculation_profile, comp.calculation_version, len(comp.tickers),
        len(comp.complete_tickers()),
        json.dumps(dict(comp.diagnostics), ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), default=str),
    )


def _result_rows(comp: RestateComputation) -> list[tuple]:
    satirlar = []
    for t in comp.tickers:
        r = comp.company_results[t]
        m = r.modules
        satirlar.append((
            comp.restate_run_id, t, comp.target_analysis_at, comp.knowledge_cutoff_at,
            ENGINE_FAMILY_DEFAULT,
            m["M2"].score, None, m["M2"].source_at, m["M2"].missing,
            m["M1"].score, m["M1"].source_at, m["M1"].missing,
            m["M3"].score, m["M3"].source_at, m["M3"].missing,
            m["Ek4"].score, m["Ek4"].source_at, m["Ek4"].missing,
            m["Ek1"].score, m["Ek1"].source_at, m["Ek1"].missing,
            m["Ek9"].score, m["Ek9"].source_at, m["Ek9"].missing,
            r.good_count_ge8, r.good_count_missing, r.base_score, r.final_score,
            r.veto_flag, r.decision, comp.calculation_profile, r.total_rasyo_status,
            (r.insufficiency_reason if r.total_rasyo_status != "OK" else None),
            r.insufficiency_reason, "{}",
        ))
    return satirlar


def _input_rows(comp: RestateComputation) -> list[tuple]:
    satirlar = []
    for t in comp.tickers:
        for modul, v in sorted(comp.company_results[t].modules.items()):
            satirlar.append((
                comp.restate_run_id, t, modul, v.score, v.missing, v.source_at,
                v.source_run_key, v.identity_known,
            ))
    return satirlar


def persist_restate(conn: Any, comp: RestateComputation, *, started_at, finished_at,
                    status: str = "COMPLETE") -> dict[str, Any]:
    """
    Idempotent + immutable yazim.

    Ayni restate_run_id + ayni (inputs_sha256, results_sha256) ->
    tabloya DOKUNULMAZ (started_at/finished_at TAZELENMEZ). Ayni
    restate_run_id + farkli icerik -> RestateConflict, HICBIR SEY
    overwrite EDILMEZ.
    """
    import psycopg2.extras

    if not isinstance(comp, RestateComputation):
        raise RestatePersistenceError("comp RestateComputation olmali")
    if status not in ("COMPLETE", "COMPLETE_NO_RESULTS", "PARTIAL", "FAILED"):
        raise RestatePersistenceError("gecersiz status")

    run_row = _run_row(comp, started_at=started_at, finished_at=finished_at, status=status)
    if len(run_row) != len(RUN_COLUMNS):
        raise RestatePersistenceError("run tuple sutun sayisi uyusmuyor")
    result_rows = _result_rows(comp)
    for satir in result_rows:
        if len(satir) != len(RESULT_COLUMNS):
            raise RestatePersistenceError("result tuple uyusmuyor")
    input_rows = _input_rows(comp)
    for satir in input_rows:
        if len(satir) != len(INPUT_COLUMNS):
            raise RestatePersistenceError("input tuple uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            cur.execute(RUN_LOOKUP, (comp.restate_run_id,))
            mevcut = cur.fetchone()
            if mevcut is not None:
                if (mevcut[0], mevcut[1]) != (comp.inputs_sha256, comp.results_sha256):
                    raise RestateConflict(
                        "restate_run_id yeniden kullanildi ve icerik farkli: "
                        f"{comp.restate_run_id}")
                return {"created": False, "inputs_sha256": comp.inputs_sha256,
                        "results_sha256": comp.results_sha256,
                        "result_rows": len(result_rows), "input_rows": len(input_rows)}

            psycopg2.extras.execute_values(cur, RUN_INSERT, [run_row])
            if result_rows:
                psycopg2.extras.execute_values(cur, RESULT_INSERT, result_rows, page_size=500)
            if input_rows:
                psycopg2.extras.execute_values(cur, INPUT_INSERT, input_rows, page_size=500)
    return {"created": True, "inputs_sha256": comp.inputs_sha256,
            "results_sha256": comp.results_sha256, "result_rows": len(result_rows),
            "input_rows": len(input_rows)}
