"""
Total Rasyo orkestratoru — MOTOR YALITIMI ve TEK MOTOR SAHIPLIGI.

YALITIM: bir sektor motoru coktugunde digerleri DEVAM EDER. Coken motora
yonlendirilen sirketler rapordan KAYBOLMAZ; `MOTOR_COKTU` durumuyla kalir.
"sonuc yok" tek bir durum degildir:

    OK             -> motor calisti, sirket icin sonuc uretti
    MOTOR_COKTU    -> motor calisti ve coktu
    CALISTIRILMADI -> motor bu kosuda hic calistirilmadi
    REDDEDILDI     -> motor calisti, sirketi kontrollu reddetti

Bu ayrim, "motor coktu" ile "veri yok"u ayni kefeye koymayi engeller.

TEK MOTOR SAHIPLIGI: ayni sirket iki sektor motorundan basarili sonuc
ALAMAZ. Cakisma sessizce cozulmez (ne "ilk gelen kazanir" ne de oncelik
sirasi); FAIL-CLOSED davranilir ve ilgili sirket `YONLENDIRME_CAKISMASI`
durumuna alinir. Sessiz secim, iki farkli degerleme modelinin ayni sirkete
uygulandigini gizlerdi.

HATA MESAJI: kanonik ve UZUNLUK SINIRLI. Hassas config degeri, parola veya
API anahtari sizmamasi icin mesaj metni normalize edilir.
"""
from __future__ import annotations

import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

ENGINE_FAMILIES: tuple[str, ...] = (
    "BANK", "NONFIN", "HOLDING", "GYO", "INSURANCE", "FINANCIAL",
)

RUN_OK = "OK"
RUN_FAILED = "FAILED"
RUN_SKIPPED = "SKIPPED"

ENGINE_STATUS_OK = "OK"
ENGINE_STATUS_CRASHED = "MOTOR_COKTU"
ENGINE_STATUS_NOT_RUN = "CALISTIRILMADI"
ENGINE_STATUS_REJECTED = "REDDEDILDI"

MAX_ERROR_MESSAGE = 500

# Hata metninde gorulmemesi gereken anahtarlar. Motor istisnasi config
# sozlugunu string'e cevirip tasiyabilir; bu, parolayi loga yazmak demektir.
#
# ANAHTAR ADI PARCASI DA SAYILIR: `DB_SECRET` icinde `\bsecret\b` ESLESMEZ,
# cunku `_` kelime karakteridir. Bu yuzden onek/sonek serbest birakildi.
#
# DEGER SATIR SONUNA KADAR silinir. `\S+` kullanmak "Authorization: Bearer
# xyz123" ornegini yalniz `Bearer`e kadar temizler ve ASIL SIRRI birakirdi.
# Fazla silmek, sizdirmaktan iyidir.
_SECRET_PATTERN = re.compile(
    r"(?i)([A-Za-z0-9_-]*"
    r"(?:password|passwd|pwd|secret|api[_-]?key|apikey|token|"
    r"authorization|bearer|dsn|conninfo|connection[_-]?string|"
    r"private[_-]?key|credential)"
    r"[A-Za-z0-9_-]*)\s*[:=].*"
)
_URI_CREDENTIAL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@")


class EngineIsolationError(ValueError):
    pass


def sanitize_error_message(text: Any, *, limit: int = MAX_ERROR_MESSAGE) -> str:
    """
    Kanonik, sinirli uzunlukta ve HASSAS DEGER ICERMEYEN hata mesaji.

    Kesme sinirin ORTASINDAN degil sonundan yapilir ve kesildigi belli olur;
    sessizce kirpilmis mesaj yanlis teshise yol acar.
    """
    if text is None:
        return ""
    raw = str(text)
    raw = _URI_CREDENTIAL.sub(r"\1***:***@", raw)
    raw = _SECRET_PATTERN.sub(r"\1=***", raw)
    raw = " ".join(raw.split())
    if len(raw) > limit:
        raw = raw[: limit - 3].rstrip() + "..."
    return raw


def _with_location(message: str, location: str) -> str:
    """Konumu ekler ve TOPLAM uzunlugu yine sinir icinde tutar."""
    ek = f" @{location}"
    if len(message) + len(ek) > MAX_ERROR_MESSAGE:
        message = message[: MAX_ERROR_MESSAGE - len(ek) - 3].rstrip() + "..."
    return f"{message}{ek}"


@dataclass(frozen=True)
class EngineRun:
    """
    Tek bir sektor motorunun kosu sonucu.

    `m2_by_ticker`: ticker -> M2 sozlesmesi (m2/m2_score, m2_source,
    valuation_usable, valuation_status, valuation_reason, valuation_confidence)
    `rejections`: ticker -> kontrollu ret nedeni
    """
    engine: str
    status: str
    m2_by_ticker: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    rejections: Mapping[str, str] = field(default_factory=dict)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    config_sha256: Optional[str] = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def result_count(self) -> int:
        return len(self.m2_by_ticker)

    @property
    def rejection_count(self) -> int:
        return len(self.rejections)


def _validate_engine(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise EngineIsolationError("motor adi dolu metin olmali")
    engine = name.strip().upper()
    if engine not in ENGINE_FAMILIES:
        raise EngineIsolationError(f"desteklenmeyen motor: {engine}")
    return engine


def _normalize_m2_map(engine: str, value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise EngineIsolationError(f"{engine} motoru mapping dondurmeli")
    out: dict[str, dict[str, Any]] = {}
    for ticker, payload in value.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise EngineIsolationError(f"{engine} sonucunda bos ticker")
        code = ticker.strip().upper()
        if code in out:
            raise EngineIsolationError(f"{engine} sonucunda yinelenen ticker: {code}")
        if not isinstance(payload, Mapping):
            raise EngineIsolationError(f"{engine}.{code} sonucu mapping olmali")
        out[code] = dict(payload)
    return out


def _normalize_rejections(engine: str, value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EngineIsolationError(f"{engine} rejections mapping olmali")
    out: dict[str, str] = {}
    for ticker, reason in value.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise EngineIsolationError(f"{engine} rejections bos ticker")
        out[ticker.strip().upper()] = sanitize_error_message(reason) or "NEDEN_BELIRTILMEDI"
    return out


def run_engine_safely(
    engine: str,
    runner: Callable[[], Any],
    *,
    config_sha256: Optional[str] = None,
) -> EngineRun:
    """
    Bir sektor motorunu YALITILMIS calistirir.

    Motor ne atarsa atsin (Exception) yakalanir ve `EngineRun(status=FAILED)`
    olarak doner; cagiran dongu KIRILMAZ. Boylece bir motorun cokmesi digerlerinin
    sonuclarini goturmez.

    KeyboardInterrupt / SystemExit YAKALANMAZ: bunlar operatorun durdurma
    iradesidir, motor hatasi degildir. Yutmak, iptal edilemeyen kosu demektir.
    """
    name = _validate_engine(engine)
    if not callable(runner):
        raise EngineIsolationError(f"{name} icin runner cagrilabilir olmali")
    baslangic = time.monotonic()
    try:
        ham = runner()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - yalitim sinirinin kendisi
        sure = int((time.monotonic() - baslangic) * 1000)
        izi = traceback.extract_tb(exc.__traceback__)
        konum = f"{izi[-1].filename.rsplit('/', 1)[-1]}:{izi[-1].lineno}" if izi else "?"
        return EngineRun(
            engine=name,
            status=RUN_FAILED,
            error_type=type(exc).__name__,
            # Konum sanitizasyondan SONRA eklenir: sir redaksiyonu satir
            # sonuna kadar sildigi icin, once eklenirse teshis bilgisi de
            # silinirdi.
            error_message=_with_location(sanitize_error_message(exc), konum),
            duration_ms=sure,
            config_sha256=config_sha256,
            diagnostics={"failed_at": konum},
        )

    sure = int((time.monotonic() - baslangic) * 1000)
    if isinstance(ham, EngineRun):
        return ham
    if not isinstance(ham, Mapping):
        raise EngineIsolationError(f"{name} motoru mapping veya EngineRun dondurmeli")
    sonuclar = _normalize_m2_map(name, ham.get("results", {}))
    retler = _normalize_rejections(name, ham.get("rejections"))
    kesisim = set(sonuclar) & set(retler)
    if kesisim:
        raise EngineIsolationError(
            f"{name} ayni ticker hem sonuc hem ret: {sorted(kesisim)[0]}"
        )
    tanilar = ham.get("diagnostics") or {}
    if not isinstance(tanilar, Mapping):
        raise EngineIsolationError(f"{name} diagnostics mapping olmali")
    return EngineRun(
        engine=name,
        status=RUN_OK,
        m2_by_ticker=sonuclar,
        rejections=retler,
        duration_ms=sure,
        config_sha256=ham.get("config_sha256", config_sha256),
        diagnostics=dict(tanilar),
    )


def skipped_engine_run(engine: str, reason: str) -> EngineRun:
    """
    Hic calistirilmamis motor. `CALISTIRILMADI`, `MOTOR_COKTU` DEGILDIR ve
    eski kosunun sonucuyla KARISTIRILMAMALIDIR.
    """
    return EngineRun(
        engine=_validate_engine(engine),
        status=RUN_SKIPPED,
        error_type=None,
        error_message=sanitize_error_message(reason) or "CALISTIRILMADI",
        diagnostics={"skipped_reason": sanitize_error_message(reason)},
    )


@dataclass(frozen=True)
class OwnershipResolution:
    """`conflicts`: ticker -> cakisan motor listesi (sirali)."""
    owner_by_ticker: Mapping[str, str]
    conflicts: Mapping[str, tuple[str, ...]]


def resolve_engine_ownership(
    routing: Mapping[str, str],
    engine_runs: Mapping[str, EngineRun],
) -> OwnershipResolution:
    """
    TEK MOTOR SAHIPLIGI kontrolu.

    Cakisma iki bicimde olusur:
      1) Yonlendirme haritasi sirketi bir aileye verir ama BASKA bir motor da
         o sirket icin BASARILI sonuc uretir.
      2) Birden fazla motor ayni sirket icin basarili sonuc uretir.

    Ikisi de SESSIZCE cozulmez. Cakisan sirket `conflicts` icine alinir ve
    cagiran taraf onu skorsuz `YONLENDIRME_CAKISMASI` durumuna yazar.
    """
    if not isinstance(routing, Mapping):
        raise EngineIsolationError("routing mapping olmali")
    normalized_routing: dict[str, str] = {}
    for ticker, family in routing.items():
        if not isinstance(ticker, str) or not ticker.strip():
            raise EngineIsolationError("routing bos ticker iceriyor")
        normalized_routing[ticker.strip().upper()] = _validate_engine(family)

    uretenler: dict[str, list[str]] = {}
    for engine, run in engine_runs.items():
        name = _validate_engine(engine)
        if run.status != RUN_OK:
            continue
        for ticker in run.m2_by_ticker:
            uretenler.setdefault(ticker, []).append(name)

    owner: dict[str, str] = {}
    conflicts: dict[str, tuple[str, ...]] = {}
    for ticker, family in normalized_routing.items():
        produced = sorted(uretenler.get(ticker, []))
        if len(produced) > 1:
            conflicts[ticker] = tuple(produced)
            continue
        if produced and produced[0] != family:
            # Yonlendirme bir aile diyor, sonucu baska motor uretmis.
            conflicts[ticker] = tuple(sorted({family, produced[0]}))
            continue
        owner[ticker] = family

    # Yonlendirme haritasinda HIC olmayan ama sonuc uretilmis sirketler de
    # cakismadir: hangi aileye ait olduklari bilinmiyor.
    for ticker, produced in uretenler.items():
        if ticker in normalized_routing or ticker in conflicts:
            continue
        conflicts[ticker] = tuple(sorted(set(produced)))

    return OwnershipResolution(owner_by_ticker=owner, conflicts=conflicts)
