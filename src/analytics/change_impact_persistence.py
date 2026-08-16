"""
Change-impact — ETKI PLANI KALICILIGI.

IDEMPOTENT AMA IMMUTABLE
------------------------
    ayni impact_plan_id + ayni plan_sha256   -> IDEMPOTENT BASARI
                                                yeni satir OLUSMAZ,
                                                created_at TAZELENMEZ
    ayni impact_plan_id + FARKLI plan_sha256 -> ImpactPlanConflict, hicbir
                                                sey overwrite EDILMEZ

"Idempotent" adi altinda mevcut satiri UPDATE edip zamanini tazelemek,
planin TARIHSEL KANIT niteligini yok eder: plan gercekte ne zaman
uretildigi bilinemez hale gelir. Bu yuzden ikinci yazim tabloya HIC
DOKUNMAZ.

Planin tekrar UYGULANMASI ayri tabloda izlenir
(`analytics.impact_application_run`, append-only). Bir planin birden fazla
uygulama denemesi olabilir ve bunlar plan satirini kirletmez.

`with conn:` ZORUNLU -- V19 dersi: commit eden yapi budur.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from src.analytics.change_impact_detector import ImpactPlan
from src.analytics.change_impact_registry import REGISTRY_VERSION
from src.analytics.total_rasyo_engine_isolation import sanitize_error_message

PLAN_COLUMNS: tuple[str, ...] = (
    "impact_plan_id", "plan_sha256", "knowledge_basis", "run_scope",
    "direct_ticker", "source_fact_id", "source_statement_id",
    "source_version_id", "statement_type", "fact_key", "changed_period_end",
    "published_at", "analysis_at", "knowledge_cutoff_at", "registry_version",
    "registry_sha256", "detector_version", "entry_count",
    "impacted_ticker_count", "diagnostics",
)

ENTRY_COLUMNS: tuple[str, ...] = (
    "impact_plan_id", "entry_seq", "direct_ticker", "impacted_ticker",
    "impact_type", "engine_family", "module", "dependency_edge_id",
    "dependency_group_key", "reason_code", "actual_effects", "effective_from",
    "affected_anchor_period_ends", "eligibility_scope",
)

APPLICATION_COLUMNS: tuple[str, ...] = (
    "application_run_id", "impact_plan_id", "attempt_no", "orchestrator_run_id",
    "analysis_at", "started_at", "finished_at", "status",
    "targeted_ticker_count", "error_type", "error_message", "diagnostics",
)


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


PLAN_INSERT = _insert_sql("analytics.impact_plan", PLAN_COLUMNS)
ENTRY_INSERT = _insert_sql("analytics.impact_plan_entry", ENTRY_COLUMNS)
APPLICATION_INSERT = _insert_sql("analytics.impact_application_run",
                                 APPLICATION_COLUMNS)

PLAN_LOOKUP = ("SELECT plan_sha256, created_at, entry_count "
               "FROM analytics.impact_plan WHERE impact_plan_id=%s")

NEXT_ATTEMPT = ("SELECT coalesce(max(attempt_no), 0) + 1 "
                "FROM analytics.impact_application_run WHERE impact_plan_id=%s")


class ImpactPersistenceError(ValueError):
    pass


class ImpactPlanConflict(ImpactPersistenceError):
    """
    Ayni `impact_plan_id` FARKLI icerikle geldi.

    Bu sessizce cozulemez: ya detector surumu/registry degismis ama kimlik
    girdilerine yansimamistir (kimlik hesabinda eksiklik), ya da iki farkli
    plan ayni kimlige carpmistir. Ikisi de overwrite ile gizlenemez.
    """


def _plan_row(plan: ImpactPlan, meta: Mapping[str, Any]) -> tuple:
    import json
    return (
        plan.impact_plan_id, plan.plan_sha256(), plan.knowledge_basis,
        plan.run_scope, meta["direct_ticker"], meta["source_fact_id"],
        meta["source_statement_id"], meta["source_version_id"],
        meta["statement_type"], meta["fact_key"], meta["changed_period_end"],
        meta["published_at"], plan.analysis_at, plan.knowledge_cutoff_at,
        REGISTRY_VERSION, plan.registry_sha256, plan.detector_version,
        len(plan.entries), len(plan.targeted_tickers()),
        json.dumps(dict(plan.diagnostics), ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), default=str),
    )


def _entry_rows(plan: ImpactPlan) -> list[tuple]:
    satirlar = []
    # entry_seq DETERMINISTIK siralamadan gelir; giris sirasindan degil.
    # Aksi halde ayni plan iki kez uretildiginde farkli seq atanabilirdi.
    sirali = sorted(plan.entries, key=lambda e: (
        e.impacted_ticker, e.impact_type, e.module, e.dependency_edge_id,
        e.reason_code))
    for seq, e in enumerate(sirali):
        satirlar.append((
            plan.impact_plan_id, seq, e.direct_ticker, e.impacted_ticker,
            e.impact_type, e.engine_family, e.module, e.dependency_edge_id,
            e.dependency_group_key, e.reason_code, list(e.actual_effects),
            e.effective_from, list(e.affected_anchor_period_ends),
            e.eligibility_scope,
        ))
    return satirlar


def persist_impact_plan(conn: Any, plan: ImpactPlan,
                        meta: Mapping[str, Any]) -> dict[str, Any]:
    """
    Plani yazar. Zaten varsa ve ICERIK AYNIYSA hicbir sey yazmaz.

    Doner: {"created": bool, "plan_sha256": str, "entry_rows": int}
    """
    import psycopg2.extras

    if not isinstance(plan, ImpactPlan):
        raise ImpactPersistenceError("plan ImpactPlan olmali")
    for alan in ("direct_ticker", "source_fact_id", "source_statement_id",
                 "source_version_id", "statement_type", "fact_key",
                 "changed_period_end", "published_at"):
        if meta.get(alan) is None:
            raise ImpactPersistenceError(f"meta.{alan} zorunlu")

    sha = plan.plan_sha256()
    plan_row = _plan_row(plan, meta)
    entry_rows = _entry_rows(plan)
    if len(plan_row) != len(PLAN_COLUMNS):
        raise ImpactPersistenceError("plan tuple sutun sayisi uyusmuyor")
    for satir in entry_rows:
        if len(satir) != len(ENTRY_COLUMNS):
            raise ImpactPersistenceError("entry tuple sutun sayisi uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            cur.execute(PLAN_LOOKUP, (plan.impact_plan_id,))
            mevcut = cur.fetchone()
            if mevcut is not None:
                if mevcut[0] != sha:
                    raise ImpactPlanConflict(
                        f"impact_plan_id yeniden kullanildi ve icerik farkli: "
                        f"{plan.impact_plan_id}")
                # IDEMPOTENT: tabloya HIC DOKUNULMAZ. UPDATE edilseydi
                # created_at tazelenir ve planin ne zaman uretildigi
                # kaybolurdu.
                return {"created": False, "plan_sha256": sha,
                        "entry_rows": int(mevcut[2])}

            psycopg2.extras.execute_values(cur, PLAN_INSERT, [plan_row])
            if entry_rows:
                psycopg2.extras.execute_values(cur, ENTRY_INSERT, entry_rows,
                                               page_size=500)
    return {"created": True, "plan_sha256": sha, "entry_rows": len(entry_rows)}


def record_application_attempt(
    conn: Any,
    *,
    impact_plan_id: str,
    application_run_id: str,
    started_at: datetime,
    status: str = "PENDING",
    orchestrator_run_id: Optional[str] = None,
    analysis_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    targeted_ticker_count: int = 0,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    diagnostics: Optional[Mapping[str, Any]] = None,
) -> int:
    """
    Uygulama denemesi kaydeder. APPEND-ONLY: her deneme yeni satirdir,
    onceki denemeyi silmez veya gizlemez.
    """
    import json

    import psycopg2.extras

    with conn:
        with conn.cursor() as cur:
            cur.execute(NEXT_ATTEMPT, (impact_plan_id,))
            attempt = int(cur.fetchone()[0])
            satir = (
                application_run_id, impact_plan_id, attempt,
                orchestrator_run_id, analysis_at, started_at, finished_at,
                status, int(targeted_ticker_count), error_type,
                sanitize_error_message(error_message) or None,
                json.dumps(dict(diagnostics or {}), ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"), default=str),
            )
            if len(satir) != len(APPLICATION_COLUMNS):
                raise ImpactPersistenceError("application tuple uyusmuyor")
            psycopg2.extras.execute_values(cur, APPLICATION_INSERT, [satir])
    return attempt


def load_targeted_tickers(conn: Any, impact_plan_id: str) -> tuple[str, ...]:
    """Plandan orkestratöre verilecek hedef kume."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT impacted_ticker FROM "
                    "analytics.impact_plan_entry WHERE impact_plan_id=%s "
                    "ORDER BY impacted_ticker", (impact_plan_id,))
        return tuple(r[0] for r in cur.fetchall())
