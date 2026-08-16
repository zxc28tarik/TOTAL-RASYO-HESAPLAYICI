"""
Total Rasyo orkestratoru — ATOMIK ve OTORITATIF kalicilik.

`with conn:` ZORUNLUDUR
-----------------------
psycopg2'de islemi COMMIT eden yapi `with conn:` baglamidir. Yalniz
`with conn.cursor()` kullanmak INSERT'leri calistirir, sayaci dogru
doldurur, hata vermez -- ve baglanti kapaninca HER SEY KAYBOLUR.

V18'de tam olarak bu oldu: CLI `persisted_count=9, persisted=true`
bildirdi, tabloda 0 satir vardi. Birim testleri sahte baglanti kullandigi
icin hepsi geciyordu. Bu yuzden burada `with conn:` hem kaynak duzeyinde
test edilir hem GERCEK PostgreSQL'de yazip YENIDEN OKUYARAK dogrulanir.

Atomiklik de ayni yapidan gelir: transaction ortasinda SQL hatasi olursa
tumu geri alinir, yarim kayit kalmaz.

SUTUN SOZLESMESI TEK KAYNAKTAN
------------------------------
Sutun listeleri asagida TEK yerde tanimlanir; INSERT metni ve satir
tuple'lari ikisi de ondan URETILIR. Elle yazilmis iki liste zamanla
kayar ve sutunlar sessizce kayarak yanlis alana yazar.

OTORITATIFLIK
-------------
Ayni `analysis_at` yeniden calistirildiginda yeni kosu OTORITATIFTIR.
Silme YALNIZ (analysis_at, bu kosuda gercekten denenen ticker/motor)
kumesini hedefler. Kesim genelinde silmek, o kosuda hic denenmemis
sirketlerin sonuclarini da yok ederdi.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.analytics.total_rasyo_engine_isolation import (
    MAX_ERROR_MESSAGE,
    sanitize_error_message,
)

MAX_RUN_ID = 128

# ---------------------------------------------------------------- sutunlar
ENGINE_RUN_COLUMNS: tuple[str, ...] = (
    "analysis_at", "engine", "status", "result_count", "rejection_count",
    "routed_company_count", "error_type", "error_message", "duration_ms",
    "config_sha256", "diagnostics", "run_id",
)

COMPANY_RESULT_COLUMNS: tuple[str, ...] = (
    "analysis_at", "ticker", "routed_engine", "engine_status", "engine_reason",
    "m2_score", "m2_source", "m2_source_at", "m2_source_type", "m2_missing",
    "valuation_confidence",
    "m1_score", "m1_source_at", "m1_missing",
    "m3_score", "m3_source_at", "m3_missing",
    "ek4_score", "ek4_source_at", "ek4_missing",
    "ek1_score", "ek1_source_at", "ek1_missing",
    "ek9_score", "ek9_source_at", "ek9_missing",
    "module_source_type", "good_count_ge8", "good_count_missing",
    "base_score", "final_score", "total_rasyo_100", "veto_flag", "decision",
    "weights_profile", "total_rasyo_status", "rejection_reason",
    "insufficiency_reason",
    "missing_modules", "data_confidence", "diagnostics", "run_id",
)

RUN_COLUMNS: tuple[str, ...] = (
    "run_id", "analysis_at", "payload_sha256", "started_at", "finished_at",
    "overall_status", "persistence_status", "run_scope",
    "universe_company_count", "not_run_policy", "engine_error_count", "company_count",
    "successful_company_count", "insufficient_data_count",
    "engine_failed_company_count", "not_run_company_count",
    "routing_conflict_count", "missing_m1_count", "missing_m2_count",
    "missing_m3_count", "missing_ek4_count", "missing_ek1_count",
    "missing_ek9_count", "missing_good_count", "weights_profile", "diagnostics",
)


def _insert_sql(table: str, columns: Sequence[str]) -> str:
    return f"INSERT INTO {table}\n  ({', '.join(columns)})\nVALUES %s\n"


ENGINE_RUN_INSERT = _insert_sql("analytics.daily_engine_run", ENGINE_RUN_COLUMNS)
COMPANY_RESULT_INSERT = _insert_sql(
    "analytics.company_total_rasyo_result", COMPANY_RESULT_COLUMNS)
RUN_INSERT = _insert_sql("analytics.total_rasyo_run", RUN_COLUMNS)

# Silme YALNIZ bu kosuda denenen anahtarlari hedefler.
ENGINE_RUN_DELETE = (
    "DELETE FROM analytics.daily_engine_run "
    "WHERE analysis_at=%s AND engine=ANY(%s::text[])"
)
COMPANY_RESULT_DELETE = (
    "DELETE FROM analytics.company_total_rasyo_result "
    "WHERE analysis_at=%s AND ticker=ANY(%s::text[])"
)
RUN_DELETE = "DELETE FROM analytics.total_rasyo_run WHERE run_id=%s"

RUN_FINGERPRINT_SQL = (
    "SELECT payload_sha256 FROM analytics.total_rasyo_run WHERE run_id=%s"
)

# Ayni kesim uzerinde iki orkestratör YARISAMAZ. Advisory lock transaction
# sonunda otomatik birakilir; uygulama cokse bile kilit sizmaz.
ADVISORY_LOCK_SQL = "SELECT pg_advisory_xact_lock(%s, %s)"
ADVISORY_LOCK_NAMESPACE = 0x54524F52  # "TROR"


class PersistenceError(ValueError):
    pass


def _canonical_json(value: Any, name: str) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise PersistenceError(f"{name} mapping olmali")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False, default=str)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PersistenceError(f"{name} kanonik JSON olmali") from exc


def _clamped(text: Any) -> Optional[str]:
    """
    Veritabanina yazilirken de sinirlandirilir ve gizli bilgi temizlenir.

    Sanitizasyonu yalniz raporlama katmaninda yapmak yetmez: DB'ye giden yol
    ayri bir sinirdir ve CHECK kisiti asilirsa TUM transaction rollback olur.
    """
    if text is None:
        return None
    temiz = sanitize_error_message(text, limit=MAX_ERROR_MESSAGE)
    return temiz or None


def _lock_keys(analysis_at: datetime) -> tuple[int, int]:
    ham = hashlib.sha256(analysis_at.isoformat().encode("utf-8")).digest()
    ikinci = int.from_bytes(ham[:4], "big", signed=True)
    return ADVISORY_LOCK_NAMESPACE, ikinci


def run_payload_fingerprint(report: Mapping[str, Any]) -> str:
    """
    Kosunun kanonik parmak izi.

    Ayni `run_id` FARKLI icerikle yeniden kullanilirsa bu deger degisir ve
    yazim reddedilir. Zaman damgalari (started_at/finished_at) DISARIDA
    birakilir: ayni girdi ayni parmak izini vermelidir, yoksa kontrol
    her kosuda tetiklenir ve ise yaramaz.
    """
    ozet = {
        "analysis_at": str(report.get("analysis_at")),
        "weights_profile": report.get("weights_profile"),
        "engines": sorted(
            (str(e.get("engine")), str(e.get("status")), int(e.get("result_count") or 0))
            for e in report.get("engine_runs", [])
        ),
        "results": sorted(
            (
                str(r.get("ticker")),
                str(r.get("routed_engine")),
                str(r.get("total_rasyo_status")),
                repr(r.get("final_score")),
            )
            for r in report.get("results", [])
        ),
    }
    ham = json.dumps(ozet, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest()


def _engine_row(analysis_at: datetime, run_id: str, entry: Mapping[str, Any]) -> tuple:
    return (
        analysis_at,
        entry["engine"],
        entry["status"],
        int(entry.get("result_count") or 0),
        int(entry.get("rejection_count") or 0),
        int(entry.get("routed_company_count") or 0),
        entry.get("error_type"),
        _clamped(entry.get("error_message")),
        entry.get("duration_ms"),
        entry.get("config_sha256"),
        _canonical_json(entry.get("diagnostics"), "engine.diagnostics"),
        run_id,
    )


def _company_row(analysis_at: datetime, run_id: str, r: Mapping[str, Any]) -> tuple:
    moduller = r.get("modules") or {}

    def mod(key: str, alan: str):
        girdi = moduller.get(key) or {}
        return girdi.get(alan)

    eksikler = r.get("missing_modules") or ()
    return (
        analysis_at,
        r["ticker"],
        r["routed_engine"],
        r["engine_status"],
        _clamped(r.get("engine_reason")),
        r.get("m2_score"),
        r.get("m2_source"),
        r.get("m2_source_at"),
        r.get("m2_source_type"),
        bool(r.get("m2_missing")),
        r.get("valuation_confidence"),
        mod("M1", "score"), mod("M1", "source_at"), bool(mod("M1", "missing")),
        mod("M3", "score"), mod("M3", "source_at"), bool(mod("M3", "missing")),
        mod("Ek4", "score"), mod("Ek4", "source_at"), bool(mod("Ek4", "missing")),
        mod("Ek1", "score"), mod("Ek1", "source_at"), bool(mod("Ek1", "missing")),
        mod("Ek9", "score"), mod("Ek9", "source_at"), bool(mod("Ek9", "missing")),
        mod("M1", "source_type"),
        r.get("good_count_ge8"),
        bool(r.get("good_count_missing")),
        r.get("base_score"),
        r.get("final_score"),
        r.get("total_rasyo_100"),
        r.get("veto_flag"),
        r.get("decision"),
        r.get("weights_profile"),
        r["total_rasyo_status"],
        _clamped(r.get("rejection_reason")),
        r.get("insufficiency_reason"),
        ", ".join(eksikler) if eksikler else None,
        r.get("data_confidence"),
        _canonical_json(r.get("diagnostics"), "result.diagnostics"),
        run_id,
    )


def _run_row(report: Mapping[str, Any], parmak_izi: str) -> tuple:
    s = report.get("counters") or {}
    return (
        report["run_id"], report["analysis_at"], parmak_izi,
        report["started_at"], report["finished_at"], report["overall_status"],
        report.get("persistence_status"), report.get("run_scope"),
        report.get("universe_company_count"), report.get("not_run_policy"),
        int(s.get("engine_error_count", 0)), int(s.get("company_count", 0)),
        int(s.get("successful_company_count", 0)),
        int(s.get("insufficient_data_count", 0)),
        int(s.get("engine_failed_company_count", 0)),
        int(s.get("not_run_company_count", 0)),
        int(s.get("routing_conflict_count", 0)),
        int(s.get("missing_m1_count", 0)), int(s.get("missing_m2_count", 0)),
        int(s.get("missing_m3_count", 0)), int(s.get("missing_ek4_count", 0)),
        int(s.get("missing_ek1_count", 0)), int(s.get("missing_ek9_count", 0)),
        int(s.get("missing_good_count", 0)),
        report.get("weights_profile") or "TOTAL_RASYO_SCORE_V1",
        _canonical_json(report.get("diagnostics"), "report.diagnostics"),
    )


def _validate_report(report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping):
        raise PersistenceError("rapor mapping olmali")
    for alan in ("run_id", "analysis_at", "started_at", "finished_at",
                 "overall_status"):
        if report.get(alan) is None:
            raise PersistenceError(f"rapor.{alan} zorunlu")
    run_id = report["run_id"]
    if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > MAX_RUN_ID:
        raise PersistenceError("run_id dolu ve <=128 karakter metin olmali")
    for alan in ("analysis_at", "started_at", "finished_at"):
        deger = report[alan]
        if not isinstance(deger, datetime) or deger.tzinfo is None:
            raise PersistenceError(f"rapor.{alan} timezone bilgili olmali")
    if report["finished_at"] < report["started_at"]:
        raise PersistenceError("finished_at started_at'ten once olamaz")


def persist_total_rasyo_report(conn: Any, report: Mapping[str, Any]) -> dict[str, int]:
    """
    Raporu ATOMIK ve OTORITATIF olarak yazar.

    `with conn:` islemi commit eder; ayni yapi hepsi-ya-hicbiri atomikligini
    saglar. Transaction ortasinda hata olursa yarim kayit KALMAZ.
    """
    import psycopg2.extras  # yerel import: pandas'siz ortamlarda modul yuklenebilsin

    _validate_report(report)
    analysis_at = report["analysis_at"]
    run_id = report["run_id"].strip()
    parmak_izi = run_payload_fingerprint(report)

    engine_entries = list(report.get("engine_runs", []))
    results = list(report.get("results", []))

    # Bu kosuda GERCEKTEN denenen anahtarlar. Silme yalniz bunlari hedefler;
    # kesim genelinde silmek, denenmemis sirketleri de yok ederdi.
    denenen_motorlar = sorted({str(e["engine"]) for e in engine_entries})
    denenen_tickerlar = sorted({str(r["ticker"]) for r in results})

    engine_rows = [_engine_row(analysis_at, run_id, e) for e in engine_entries]
    company_rows = [_company_row(analysis_at, run_id, r) for r in results]
    run_row = _run_row(report, parmak_izi)

    # Tuple uzunlugu = sutun sayisi. Uyusmazlik veritabanina GITMEDEN yakalanir.
    for satir in engine_rows:
        if len(satir) != len(ENGINE_RUN_COLUMNS):
            raise PersistenceError("engine_run tuple sutun sayisi uyusmuyor")
    for satir in company_rows:
        if len(satir) != len(COMPANY_RESULT_COLUMNS):
            raise PersistenceError("company_result tuple sutun sayisi uyusmuyor")
    if len(run_row) != len(RUN_COLUMNS):
        raise PersistenceError("run tuple sutun sayisi uyusmuyor")

    with conn:
        with conn.cursor() as cur:
            # 1) Ayni kesimde iki orkestratör yarisamaz.
            ns, key = _lock_keys(analysis_at)
            cur.execute(ADVISORY_LOCK_SQL, (ns, key))

            # 2) Ayni run_id FARKLI icerikle yeniden kullanilamaz.
            cur.execute(RUN_FINGERPRINT_SQL, (run_id,))
            mevcut = cur.fetchone()
            if mevcut is not None and mevcut[0] != parmak_izi:
                raise PersistenceError(
                    f"run_id yeniden kullanildi ve icerik farkli: {run_id}"
                )

            # 3) OTORITATIF silme — yalniz bu kosuda denenenler.
            cur.execute(RUN_DELETE, (run_id,))
            if denenen_motorlar:
                cur.execute(ENGINE_RUN_DELETE, (analysis_at, denenen_motorlar))
            if denenen_tickerlar:
                cur.execute(COMPANY_RESULT_DELETE, (analysis_at, denenen_tickerlar))

            # 4) Yazim
            psycopg2.extras.execute_values(cur, RUN_INSERT, [run_row])
            if engine_rows:
                psycopg2.extras.execute_values(cur, ENGINE_RUN_INSERT, engine_rows,
                                               page_size=500)
            if company_rows:
                psycopg2.extras.execute_values(cur, COMPANY_RESULT_INSERT, company_rows,
                                               page_size=500)

    return {
        "run_rows": 1,
        "engine_run_rows": len(engine_rows),
        "company_result_rows": len(company_rows),
    }


def count_persisted(conn: Any, *, run_id: str) -> dict[str, int]:
    """
    Veritabanindan YENIDEN SAYAR.

    Rapor sayacina guvenmek yerine gercek satirlari saymak, V18'deki
    "sayac dogru ama tablo bos" hatasinin dogrudan panzehiridir.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM analytics.total_rasyo_run WHERE run_id=%s),"
            "       (SELECT count(*) FROM analytics.daily_engine_run WHERE run_id=%s),"
            "       (SELECT count(*) FROM analytics.company_total_rasyo_result WHERE run_id=%s)",
            (run_id, run_id, run_id),
        )
        run_c, engine_c, company_c = cur.fetchone()
    return {"run_rows": int(run_c), "engine_run_rows": int(engine_c),
            "company_result_rows": int(company_c)}
