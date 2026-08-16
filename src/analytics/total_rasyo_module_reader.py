"""
Total Rasyo orkestratoru — modul skoru POINT-IN-TIME okuma sozlesmesi.

NE OKUR: M1, M3, Ek4, Ek1, Ek9 ve veto girdisi `good_count_ge8`.

NE OKUMAZ: `m2`. M2'nin OTORITATIF kaynagi sirketin yonlendirildigi SEKTOR
MOTORUDUR. analytics.module_scores icindeki eski `m2` alani ayni sinyali
ikinci kez tasir; ikisini birlikte puanlamak CIFT SAYIMDIR. Bu modul m2
sutununu SELECT listesine hic almaz -- boylece yanlislikla kullanilamaz.

ZAMAN KURALI (SOZLESME 3): kesim esitligi ARANMAZ. Her kayit icin
`kaynak_zamani <= analysis_at` uygulanir ve GELECEKTEKI veri sizmaz.
Bir sirket icin birden fazla uygun kayit varsa EN YENISI secilir
(row_number ... ORDER BY asof_date DESC, analysis_at DESC).

Bu desen src/analytics/kap_bank_db_workflow.py icinde zaten dogrulanmistir;
oradan kopyalanmadi, ayni sozlesmeye BAGLANDI ve testle kilitlendi.

TAHMIN URETMEME (SOZLESME 5): eksik modul icin notr deger, eski kayitla
tamamlama veya `fillna` YOKTUR. SQL yalniz NULL OLMAYAN satirlari getirir;
eksik modul cagirana `None` olarak doner ve eksiklik nedeni gorunur kalir.

BAYATLIK: `max_context_age_days` disindaki kayit SECILMEZ. Sinirsiz geriye
gitmek, aylar oncesinin skorunu bugunun analizi gibi gostermek olurdu.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.utils.missing_values import is_bool_like, is_missing_like

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

# module_scores sutunu -> Total Rasyo modul anahtari.
# `m2` BILEREK YOKTUR; bkz. modul docstring'i (cift sayim yasagi).
MODULE_COLUMN_TO_KEY: Mapping[str, str] = {
    "m1": "M1",
    "m3": "M3",
    "ek4": "Ek4",
    "ek1": "Ek1",
    "ek9": "Ek9",
}

READ_MODULE_KEYS: tuple[str, ...] = ("M1", "M3", "Ek4", "Ek1", "Ek9")

DEFAULT_MAX_CONTEXT_AGE_DAYS = 120
DEFAULT_HORIZON_DAYS = 20

MODULE_SOURCE_TYPE = "ANALYTICS_MODULE_SCORES"


class ModuleReadError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleComponent:
    """Tek bir modulun okunmus hali. Eksikse score None KALIR."""
    key: str
    score: Optional[float]
    source_at: Optional[datetime]
    source_type: Optional[str]
    missing: bool
    reason: Optional[str]


@dataclass(frozen=True)
class CompanyModuleContext:
    ticker: str
    components: Mapping[str, ModuleComponent]
    good_count_ge8: Optional[int]
    good_count_missing: bool
    good_count_reason: Optional[str]
    asof_date: Optional[date]
    analysis_at: Optional[datetime]

    def missing_keys(self) -> list[str]:
        eksik = [k for k in READ_MODULE_KEYS if self.components[k].missing]
        if self.good_count_missing:
            eksik.append("good_count_ge8")
        return eksik


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ModuleReadError(f"{name} timezone bilgili datetime olmali")
    return value


def _positive_int(name: str, value: Any) -> int:
    if is_bool_like(value) or not isinstance(value, int) or value <= 0:
        raise ModuleReadError(f"{name} pozitif Python int olmali")
    return value


def daily_price_cutoff_date(analysis_at: datetime) -> date:
    """Analiz aninin Istanbul yerel tarihi. Gelecek gun verisi ALINMAZ."""
    return analysis_at.astimezone(ISTANBUL_TZ).date()


def _normalize_tickers(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ModuleReadError("ticker dolu metin olmali")
        code = item.strip().upper()
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _score(name: str, value: Any) -> tuple[Optional[float], bool, Optional[str]]:
    """
    (skor, eksik_mi, neden) doner.

    EKSIK ile GECERSIZ ayrimi korunur:
      None / NaN / pd.NA -> eksik  (skor None kalir, tahmin URETILMEZ)
      bool / inf / aralik disi / sayiya cevrilemeyen -> GECERSIZ (hata)
    `inf`i eksik saymak sessizce yanlis sonuc uretirdi.
    """
    if is_missing_like(value):
        return None, True, f"{name}_KAYNAGI_YOK"
    if is_bool_like(value):
        raise ModuleReadError(f"{name} bool olamaz")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ModuleReadError(f"{name} sayiya cevrilemedi") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ModuleReadError(f"{name} sonlu olmali")
    if result < 0.0 or result > 1.0:
        raise ModuleReadError(f"{name} [0, 1] araliginda olmali")
    return result, False, None


def _good_count(value: Any) -> tuple[Optional[int], bool, Optional[str]]:
    """
    Veto girdisi. SOZLESME 7: eksikse SIFIR VARSAYILMAZ.

    Sifir varsaymak `good_count < veto_threshold` kosulunu dogurur ve skoru
    0.60 ile carpar -- yani eksik veri SESSIZCE CEZAYA donusur. Bu, tahmin
    uretmenin bir baska bicimidir.
    """
    if is_missing_like(value):
        return None, True, "GOOD_COUNT_KAYNAGI_YOK"
    if is_bool_like(value):
        raise ModuleReadError("good_count_ge8 bool olamaz")
    try:
        as_float = float(value)
        result = int(as_float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ModuleReadError("good_count_ge8 tam sayi olmali") from exc
    if as_float != float(result):
        raise ModuleReadError("good_count_ge8 tam sayi olmali")
    if result < 0:
        raise ModuleReadError("good_count_ge8 negatif olamaz")
    return result, False, None


MODULE_SCORES_PIT_SQL = """
WITH candidates AS (
  SELECT ms.ticker, ms.asof_date, ms.analysis_at,
         ms.m1, ms.m3, ms.ek4, ms.ek1, ms.ek9, ms.good_count_ge8,
         row_number() OVER (
           PARTITION BY upper(ms.ticker)
           ORDER BY ms.asof_date DESC,
                    ms.analysis_at DESC NULLS LAST,
                    ms.period_end DESC NULLS LAST
         ) AS rn
  FROM analytics.module_scores ms
  WHERE upper(ms.ticker) = ANY(%(tickers)s)
    AND ms.horizon_days = %(horizon_days)s
    AND ms.asof_date <= %(context_asof)s
    AND ms.asof_date >= %(min_asof)s
    AND (
      (ms.analysis_at IS NOT NULL AND ms.analysis_at <= %(analysis_at)s)
      OR (ms.analysis_at IS NULL AND ms.asof_date < %(local_date)s)
    )
)
SELECT upper(ticker) AS ticker, asof_date, analysis_at,
       m1, m3, ek4, ek1, ek9, good_count_ge8
FROM candidates
WHERE rn = 1
ORDER BY upper(ticker)
"""


def fetch_module_context(
    conn: Any,
    *,
    tickers: Iterable[str],
    analysis_at: datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_context_age_days: int = DEFAULT_MAX_CONTEXT_AGE_DAYS,
) -> dict[str, CompanyModuleContext]:
    """
    M1/M3/Ek4/Ek1/Ek9 + good_count_ge8'i point-in-time okur.

    Kaydi HIC OLMAYAN sirket sonuca GIRMEZ; cagiran taraf onu "tum moduller
    eksik" olarak isler. Kaydi olup bazi sutunlari NULL olan sirket ise
    girer ve eksik modulleri tek tek isaretlenir.
    """
    analysis = _aware("analysis_at", analysis_at)
    horizon = _positive_int("horizon_days", horizon_days)
    max_age = _positive_int("max_context_age_days", max_context_age_days)
    ticker_list = _normalize_tickers(tickers)
    if not ticker_list:
        return {}

    context_asof = daily_price_cutoff_date(analysis)
    params = {
        "tickers": ticker_list,
        "horizon_days": horizon,
        "context_asof": context_asof,
        "min_asof": context_asof - timedelta(days=max_age),
        "analysis_at": analysis,
        "local_date": context_asof,
    }

    with conn.cursor() as cur:
        cur.execute(MODULE_SCORES_PIT_SQL, params)
        rows = cur.fetchall()

    out: dict[str, CompanyModuleContext] = {}
    for row in rows:
        (ticker, asof_date, row_analysis_at,
         m1, m3, ek4, ek1, ek9, good_count) = row
        if not isinstance(ticker, str) or not ticker.strip():
            raise ModuleReadError("module_scores satirinda bos ticker")
        code = ticker.strip().upper()
        if code in out:
            raise ModuleReadError(f"{code} icin birden fazla point-in-time satir")

        # GELECEK KAYIT SIZINTISI: SQL zaten filtreliyor; burada ikinci kez
        # dogrulanir. Tek katmanli guven, sessiz sizintiya acik kapi birakir.
        if row_analysis_at is not None:
            if not isinstance(row_analysis_at, datetime) or row_analysis_at.tzinfo is None:
                raise ModuleReadError("module_scores.analysis_at tz bilgili olmali")
            if row_analysis_at > analysis:
                raise ModuleReadError(
                    f"{code} gelecekteki module_scores kaydi secildi"
                )
        if isinstance(asof_date, datetime):
            asof_date = asof_date.date()
        if asof_date is not None and asof_date > context_asof:
            raise ModuleReadError(f"{code} gelecekteki asof_date secildi")

        raw = {"M1": m1, "M3": m3, "Ek4": ek4, "Ek1": ek1, "Ek9": ek9}
        components: dict[str, ModuleComponent] = {}
        for key in READ_MODULE_KEYS:
            score, missing, reason = _score(key, raw[key])
            components[key] = ModuleComponent(
                key=key,
                score=score,
                source_at=None if missing else row_analysis_at,
                source_type=None if missing else MODULE_SOURCE_TYPE,
                missing=missing,
                reason=reason,
            )
        count, count_missing, count_reason = _good_count(good_count)
        out[code] = CompanyModuleContext(
            ticker=code,
            components=components,
            good_count_ge8=count,
            good_count_missing=count_missing,
            good_count_reason=count_reason,
            asof_date=asof_date,
            analysis_at=row_analysis_at,
        )
    return out


def absent_module_context(ticker: str) -> CompanyModuleContext:
    """
    module_scores'ta HIC kaydi olmayan sirket icin butun moduller eksik.

    Bu bir hata degil kontrollu bir durumdur: sirket rapordan KAYBOLMAZ,
    `YETERSIZ_VERI` olarak gorunur.
    """
    code = ticker.strip().upper()
    return CompanyModuleContext(
        ticker=code,
        components={
            key: ModuleComponent(
                key=key, score=None, source_at=None, source_type=None,
                missing=True, reason=f"{key}_KAYNAGI_YOK",
            )
            for key in READ_MODULE_KEYS
        },
        good_count_ge8=None,
        good_count_missing=True,
        good_count_reason="GOOD_COUNT_KAYNAGI_YOK",
        asof_date=None,
        analysis_at=None,
    )
