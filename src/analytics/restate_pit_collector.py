"""
V23-B — toplayici katman.

`analytics.restate_vs_pit_comparison` (sql/031) HUKUM KAYNAGI YAPILMAZ:
LEFT JOIN + IS DISTINCT FROM, RESTATE her zaman eksikken (bugunku V23-A
gercekliginde HER ticker icin gecerli -- M2 nedeniyle) decision_changed=TRUE
DONER. Bu SAHTE bir farktir. Bu modul iki kaynak tabloyu (company_total_
rasyo_result, company_total_rasyo_restate_result) BAGIMSIZ sorgular.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.analytics.restate_pit_reconciliation import PitSnapshot, RestateSnapshot

PIT_SQL = """
SELECT ticker, total_rasyo_status, final_score, decision, run_id
FROM analytics.company_total_rasyo_result
WHERE analysis_at = %(target_analysis_at)s AND ticker = ANY(%(tickers)s::text[])
"""

RESTATE_SQL = """
SELECT ticker, total_rasyo_status, final_score, decision
FROM analytics.company_total_rasyo_restate_result
WHERE restate_run_id = %(restate_run_id)s AND ticker = ANY(%(tickers)s::text[])
"""


RESTATE_TICKERS_SQL = """
SELECT ticker FROM analytics.company_total_rasyo_restate_result
WHERE restate_run_id = %(restate_run_id)s ORDER BY ticker
"""


def fetch_restate_run_tickers(conn: Any, *, restate_run_id: str) -> tuple[str, ...]:
    """
    Bir restate_run_id'nin GERCEKTEN kapsadigi ticker kumesini getirir.
    reconcile_pit_vs_restate() yalniz bu kumeyle cagrilmalidir -- RESTATE
    satiri OLMAYAN bir ticker gonderilirse saf hesaplayici HATA VERIR
    (RestateSnapshot(exists=False) durumu KABUL EDILMEZ).
    """
    with conn.cursor() as cur:
        cur.execute(RESTATE_TICKERS_SQL, {"restate_run_id": restate_run_id})
        return tuple(r[0] for r in cur.fetchall())


def fetch_pit_snapshots(conn: Any, *, target_analysis_at, tickers: Iterable[str]
                        ) -> dict[str, PitSnapshot]:
    kodlar = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    out = {t: PitSnapshot(exists=False) for t in kodlar}
    if not kodlar:
        return out
    with conn.cursor() as cur:
        cur.execute(PIT_SQL, {"target_analysis_at": target_analysis_at,
                              "tickers": kodlar})
        for ticker, status, score, decision, run_id in cur.fetchall():
            out[ticker] = PitSnapshot(exists=True, total_rasyo_status=status,
                                      final_score=score, decision=decision,
                                      run_id=run_id)
    return out


def fetch_restate_snapshots(conn: Any, *, restate_run_id: str, tickers: Iterable[str]
                            ) -> dict[str, RestateSnapshot]:
    kodlar = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    out = {t: RestateSnapshot(exists=False) for t in kodlar}
    if not kodlar:
        return out
    with conn.cursor() as cur:
        cur.execute(RESTATE_SQL, {"restate_run_id": restate_run_id, "tickers": kodlar})
        for ticker, status, score, decision in cur.fetchall():
            out[ticker] = RestateSnapshot(exists=True, total_rasyo_status=status,
                                          final_score=score, decision=decision)
    return out
