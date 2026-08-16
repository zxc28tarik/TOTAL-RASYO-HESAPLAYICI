"""
V23-A — RESTATE icin nokta-zamanli (point-in-time) okuyucu.

V19'un fetch_module_context()'ine (total_rasyo_module_reader.py) DOKUNULMAZ
ve o fonksiyon RESTATE icin YENIDEN KULLANILMAZ -- cunku o fonksiyon TEK bir
`analysis_at` parametresini IKI FARKLI role birden kullanir:
  (1) "hangi islem gununun baglami" (context_asof, asof_date siniri)
  (2) "ne zamana kadar bilgi biliniyor" (analysis_at siniri)
Normal PIT'te bu ikisi AYNI degerdir. RESTATE'te ZORUNLU olarak FARKLIDIR
(sema kisiti zaten bunu soyler: knowledge_cutoff_at >= target_analysis_at):
  context_asof   -> target_analysis_at'e baglanir (HANGI gunu degerliyoruz)
  analysis_at siniri -> knowledge_cutoff_at'e baglanir (NE KADAR SONRAKI
                        bilgiyi kullanmamiza izin var)

`analysis_at IS NULL` OLAN SATIRLAR CUTOFF'UN GENISLETILMIS BILGI
PENCERESINDEN YARARLANAMAZ: ne zaman bilindigi kanitlanamayan bir satira
RESTATE'in tanidigi ek sureyi vermek, kanitlanamayan kimlige uydurma imtiyaz
tanimak olurdu (V22-A ilkesi). Bu satirlar YALNIZ V19'un zaten kabul ettigi
dar tarihsel fallback kapsaminda (kendi asof_date'i target gunden ONCEYSE)
kullanilabilir -- cutoff'a gore DEGIL, target gune gore.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional

from src.analytics.bank_batch_pipeline import daily_price_cutoff_date
from src.analytics.total_rasyo_module_reader import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_MAX_CONTEXT_AGE_DAYS,
    READ_MODULE_KEYS,
    ModuleReadError,
)

RESTATE_MODULE_SCORES_SQL = """
WITH reference AS (
  -- ADIM 1: target_analysis_at'e gore HANGI DONEM (period_end) "guncel"di.
  -- Bu, V19'un normal PIT cozumlemesiyle AYNI kural -- knowledge_cutoff_at
  -- DEGIL, target_analysis_at kullanilir.
  SELECT DISTINCT ON (upper(ms.ticker))
    upper(ms.ticker) AS ticker, ms.period_end
  FROM analytics.module_scores ms
  WHERE upper(ms.ticker) = ANY(%(tickers)s)
    AND ms.horizon_days = %(horizon_days)s
    AND ms.asof_date <= %(context_asof)s
    AND ms.asof_date >= %(min_asof)s
    AND (
      (ms.analysis_at IS NOT NULL AND ms.analysis_at <= %(target_analysis_at)s)
      OR (ms.analysis_at IS NULL AND ms.asof_date < %(local_date)s)
    )
  ORDER BY upper(ms.ticker), ms.asof_date DESC,
           ms.analysis_at DESC NULLS LAST, ms.period_end DESC NULLS LAST
),
candidates AS (
  -- ADIM 2: AYNI donem (period_end) icin, knowledge_cutoff_at'e KADAR
  -- gelen TUM satirlar arasindan EN GUNCELINI sec -- asof_date target
  -- gununden SONRA olsa bile (bu, o donem icin gelen bir DUZELTMEDIR).
  -- asof_date/analysis_at CIFTI sema kisitiyla ZATEN AYNI takvim gunune
  -- kilitlidir (ck_module_scores_analysis_asof); bu yuzden analysis_at
  -- sinirinin kendisi asof_date'i de dolayli olarak sinirlar.
  SELECT ms.ticker, ms.asof_date, ms.analysis_at, ms.source_run_key,
         ms.m1, ms.m3, ms.ek4, ms.ek1, ms.ek9, ms.good_count_ge8,
         row_number() OVER (
           PARTITION BY upper(ms.ticker)
           ORDER BY ms.analysis_at DESC NULLS LAST, ms.asof_date DESC
         ) AS rn
  FROM analytics.module_scores ms
  JOIN reference r
    ON upper(ms.ticker) = r.ticker
   AND ms.period_end IS NOT DISTINCT FROM r.period_end
  WHERE ms.horizon_days = %(horizon_days)s
    AND (
      (ms.analysis_at IS NOT NULL AND ms.analysis_at <= %(knowledge_cutoff_at)s)
      OR (ms.analysis_at IS NULL AND ms.asof_date < %(local_date)s)
    )
)
SELECT upper(ticker) AS ticker, asof_date, analysis_at, source_run_key,
       m1, m3, ek4, ek1, ek9, good_count_ge8
FROM candidates
WHERE rn = 1
ORDER BY upper(ticker)
"""


@dataclass(frozen=True)
class RestateModuleComponent:
    key: str
    score: Optional[float]
    missing: bool
    source_at: Optional[datetime]
    source_run_key: Optional[str]
    identity_known: bool


@dataclass(frozen=True)
class RestateCompanyContext:
    ticker: str
    components: Mapping[str, RestateModuleComponent]
    good_count_ge8: Optional[int]
    good_count_missing: bool
    asof_date: Optional[date]
    analysis_at: Optional[datetime]


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ModuleReadError(f"{name} timezone bilgili datetime olmali")
    return value


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    out = []
    for t in tickers:
        if not isinstance(t, str) or not t.strip():
            raise ModuleReadError("ticker dolu metin olmali")
        kod = t.strip().upper()
        if kod not in out:
            out.append(kod)
    return sorted(out)


def _good_count(value: Any) -> tuple[Optional[int], bool]:
    if value is None:
        return None, True
    try:
        as_float = float(value)
        result = int(as_float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ModuleReadError("good_count_ge8 tam sayi olmali") from exc
    if as_float != float(result) or result < 0:
        raise ModuleReadError("good_count_ge8 gecerli tam sayi olmali")
    return result, False


def fetch_restate_module_context(
    conn: Any,
    *,
    tickers: Iterable[str],
    target_analysis_at: datetime,
    knowledge_cutoff_at: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_context_age_days: int = DEFAULT_MAX_CONTEXT_AGE_DAYS,
) -> dict[str, RestateCompanyContext]:
    """
    M1/M3/Ek4/Ek1/Ek9 + good_count_ge8'i RESTATE icin nokta-zamanli okur.

    M2 BU FONKSIYONDA HIC YOKTUR -- sektor motorlerinin cutoff'a duyarli bir
    uretim/kimlik kaynagi olmadigi icin (V22-A bulgusu), M2 restate icin
    HICBIR ZAMAN bu okuyucudan gelmez. Cagiran taraf M2'yi HER ZAMAN eksik
    saymalidir; PIT'teki mevcut M2 degeri FALLBACK OLARAK ASLA KULLANILMAZ.

    Kaydi HIC OLMAYAN sirket sonuca GIRMEZ; cagiran taraf "tum moduller
    eksik" olarak isler (V19 okuyucusuyla AYNI sozlesme).
    """
    hedef = _aware("target_analysis_at", target_analysis_at)
    cutoff = _aware("knowledge_cutoff_at", knowledge_cutoff_at)
    if cutoff < hedef:
        raise ModuleReadError("knowledge_cutoff_at target_analysis_at'ten once olamaz")
    horizon = horizon_days
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ModuleReadError("horizon_days pozitif tam sayi olmali")
    max_age = max_context_age_days
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        raise ModuleReadError("max_context_age_days pozitif tam sayi olmali")

    ticker_list = _normalize_tickers(tickers)
    if not ticker_list:
        return {}

    context_asof = daily_price_cutoff_date(hedef)
    params = {
        "tickers": ticker_list,
        "horizon_days": horizon,
        "context_asof": context_asof,
        "min_asof": context_asof - timedelta(days=max_age),
        "target_analysis_at": hedef,
        "knowledge_cutoff_at": cutoff,
        "local_date": context_asof,
    }

    with conn.cursor() as cur:
        cur.execute(RESTATE_MODULE_SCORES_SQL, params)
        rows = cur.fetchall()

    out: dict[str, RestateCompanyContext] = {}
    for (ticker, asof_date_val, analysis_at_val, source_run_key,
         m1, m3, ek4, ek1, ek9, good_count_raw) in rows:
        degerler = {"M1": m1, "M3": m3, "Ek4": ek4, "Ek1": ek1, "Ek9": ek9}
        components = {}
        for key in READ_MODULE_KEYS:
            deger = degerler[key]
            eksik = deger is None
            components[key] = RestateModuleComponent(
                key=key, score=None if eksik else float(deger), missing=eksik,
                source_at=None if eksik else analysis_at_val,
                source_run_key=None if eksik else source_run_key,
                identity_known=(not eksik) and source_run_key is not None)
        good_count, good_missing = _good_count(good_count_raw)
        out[ticker] = RestateCompanyContext(
            ticker=ticker, components=components, good_count_ge8=good_count,
            good_count_missing=good_missing, asof_date=asof_date_val,
            analysis_at=analysis_at_val)
    return out
