from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from src.ingest.api.mkk_contract import verify_mkk_contract_lock
from src.ingest.api.mkk_kap import (
    KapApiConfigError,
    KapApiProtocolError,
    KapApiTransportError,
    MkkKapApiClient,
    MkkKapApiConfig,
    _require_aware_datetime,
)
from src.ingest.api.mkk_suite import (
    MkkProductDefinition,
    MkkProductSuite,
    MkkProductValidation,
    MkkSuiteValidationReport,
)
from src.ingest.kap_raw import persist_kap_disclosures
from src.ingest.kap_sync import (
    acquire_kap_sync_lock,
    load_kap_sync_checkpoint,
    plan_kap_sync_window,
    release_kap_sync_lock,
)

_PRODUCT_STATUSES = {"COMPLETE", "PARTIAL", "UP_TO_DATE", "QUARANTINED", "FAILED", "NOT_RUN"}
_SUITE_STATUSES = {"COMPLETE", "PARTIAL", "FAILED"}
_REQUIRED_RELATIONS = (
    "raw.kap_disclosures",
    "raw.kap_sync_state",
    "raw.kap_sync_runs",
    "raw.kap_api_quarantine",
    "raw.mkk_suite_sync_runs",
    "raw.mkk_suite_product_runs",
)
MAX_SUITE_WINDOWS_PER_PRODUCT = 10_000
MAX_SUITE_PRODUCT_ATTEMPTS = 5


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} dolu metin olmali")
    return value.strip()


def _nonnegative_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} negatif olmayan Python int olmali")
    return value


def _positive_int(name: str, value: Any, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} pozitif Python int olmali")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} guvenli siniri asiyor")
    return value


def _sha256_hex(name: str, value: Any) -> str:
    value = _text(name, value)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} 64 karakter kucuk harf hex olmali")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class MkkSuiteDatabaseReadiness:
    server_version_num: int
    required_relations: tuple[str, ...]
    present_relations: tuple[str, ...]

    def __post_init__(self) -> None:
        version = _positive_int("server_version_num", self.server_version_num)
        if not isinstance(self.required_relations, tuple) or not self.required_relations:
            raise ValueError("required_relations dolu tuple olmali")
        if not isinstance(self.present_relations, tuple):
            raise TypeError("present_relations tuple olmali")
        for name, values in (("required_relations", self.required_relations), ("present_relations", self.present_relations)):
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} yalniz dolu metin icermeli")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} tekrarli deger iceremez")
        if any(item not in self.required_relations for item in self.present_relations):
            raise ValueError("present_relations required_relations disinda deger iceriyor")
        object.__setattr__(self, "server_version_num", version)

    @property
    def postgres_16(self) -> bool:
        return 160000 <= self.server_version_num < 170000

    @property
    def missing_relations(self) -> tuple[str, ...]:
        present = set(self.present_relations)
        return tuple(item for item in self.required_relations if item not in present)

    @property
    def ready(self) -> bool:
        return self.postgres_16 and not self.missing_relations

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_version_num": self.server_version_num,
            "postgres_16": self.postgres_16,
            "required_relations": list(self.required_relations),
            "present_relations": list(self.present_relations),
            "missing_relations": list(self.missing_relations),
            "ready": self.ready,
        }


def check_mkk_suite_database_readiness(conn: Any) -> MkkSuiteDatabaseReadiness:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version_num")
        version_row = cur.fetchone()
        if not isinstance(version_row, (tuple, list)) or len(version_row) != 1:
            raise RuntimeError("PostgreSQL server_version_num sorgusu tek kolon dondurmeli")
        raw_version = version_row[0]
        if isinstance(raw_version, bool):
            raise RuntimeError("PostgreSQL server_version_num sayisal olmali")
        try:
            version = int(raw_version)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("PostgreSQL server_version_num sayisal olmali") from exc

        placeholders = ", ".join(["to_regclass(%s)"] * len(_REQUIRED_RELATIONS))
        cur.execute(f"SELECT {placeholders}", _REQUIRED_RELATIONS)
        relation_row = cur.fetchone()
    if not isinstance(relation_row, (tuple, list)) or len(relation_row) != len(_REQUIRED_RELATIONS):
        raise RuntimeError("PostgreSQL relation hazirlik sorgusu beklenen kolon sayisini dondurmedi")
    present = tuple(
        required
        for required, actual in zip(_REQUIRED_RELATIONS, relation_row)
        if actual is not None
    )
    return MkkSuiteDatabaseReadiness(
        server_version_num=version,
        required_relations=_REQUIRED_RELATIONS,
        present_relations=present,
    )


@dataclass(frozen=True)
class MkkProductSyncResult:
    product_name: str
    source_name: str
    stream_name: str
    config_sha256: str
    status: str
    windows_completed: int
    attempts: int
    rows_persisted: int
    pages_fetched: int
    quarantined_count: int
    requested_end: datetime
    last_window_start: datetime | None = None
    last_window_end: datetime | None = None
    checkpoint_window_end: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        for name in ("product_name", "source_name", "stream_name"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "config_sha256", _sha256_hex("config_sha256", self.config_sha256))
        if self.status not in _PRODUCT_STATUSES:
            raise ValueError(f"status gecersiz: {self.status}")
        for name in ("windows_completed", "attempts", "rows_persisted", "pages_fetched", "quarantined_count"):
            object.__setattr__(self, name, _nonnegative_int(name, getattr(self, name)))
        _require_aware_datetime("requested_end", self.requested_end)
        for name in ("last_window_start", "last_window_end", "checkpoint_window_end"):
            value = getattr(self, name)
            if value is not None:
                _require_aware_datetime(name, value)
        if (self.last_window_start is None) != (self.last_window_end is None):
            raise ValueError("last_window_start/end birlikte bulunmali")
        if self.last_window_start is not None and self.last_window_end < self.last_window_start:
            raise ValueError("last_window_end last_window_start'tan once olamaz")
        if self.error is not None:
            object.__setattr__(self, "error", _text("error", self.error))
        if self.status == "FAILED" and self.error is None:
            raise ValueError("FAILED sonucu error icermeli")
        if self.status != "FAILED" and self.error is not None:
            raise ValueError("yalniz FAILED sonucu error icerebilir")
        if self.status == "QUARANTINED" and self.quarantined_count <= 0:
            raise ValueError("QUARANTINED sonucu karantina kaydi icermeli")
        if self.status in {"UP_TO_DATE", "NOT_RUN"} and self.windows_completed != 0:
            raise ValueError("UP_TO_DATE/NOT_RUN pencere tamamlayamaz")

    @property
    def successful(self) -> bool:
        return self.status in {"COMPLETE", "UP_TO_DATE"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "source_name": self.source_name,
            "stream_name": self.stream_name,
            "config_sha256": self.config_sha256,
            "status": self.status,
            "successful": self.successful,
            "windows_completed": self.windows_completed,
            "attempts": self.attempts,
            "rows_persisted": self.rows_persisted,
            "pages_fetched": self.pages_fetched,
            "quarantined_count": self.quarantined_count,
            "requested_end": self.requested_end.isoformat(),
            "last_window_start": None if self.last_window_start is None else self.last_window_start.isoformat(),
            "last_window_end": None if self.last_window_end is None else self.last_window_end.isoformat(),
            "checkpoint_window_end": None if self.checkpoint_window_end is None else self.checkpoint_window_end.isoformat(),
            "error": self.error,
        }


@dataclass(frozen=True)
class MkkSuiteSyncReport:
    run_key: str
    suite_name: str
    suite_version: int
    started_at: datetime
    completed_at: datetime
    requested_start: datetime | None
    requested_end: datetime
    resume: bool
    continue_on_error: bool
    max_windows_per_product: int
    max_product_attempts: int
    database_ready: bool
    products: tuple[MkkProductSyncResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_key", _sha256_hex("run_key", self.run_key))
        object.__setattr__(self, "suite_name", _text("suite_name", self.suite_name))
        _positive_int("suite_version", self.suite_version)
        _require_aware_datetime("started_at", self.started_at)
        _require_aware_datetime("completed_at", self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError("completed_at started_at'tan once olamaz")
        if self.requested_start is not None:
            _require_aware_datetime("requested_start", self.requested_start)
        _require_aware_datetime("requested_end", self.requested_end)
        if self.requested_start is not None and self.requested_end < self.requested_start:
            raise ValueError("requested_end requested_start'tan once olamaz")
        if not isinstance(self.resume, bool) or not isinstance(self.continue_on_error, bool):
            raise ValueError("resume/continue_on_error Python bool olmali")
        _positive_int("max_windows_per_product", self.max_windows_per_product, maximum=MAX_SUITE_WINDOWS_PER_PRODUCT)
        _positive_int("max_product_attempts", self.max_product_attempts, maximum=MAX_SUITE_PRODUCT_ATTEMPTS)
        if not isinstance(self.database_ready, bool):
            raise ValueError("database_ready Python bool olmali")
        if not isinstance(self.products, tuple) or not self.products:
            raise ValueError("products dolu tuple olmali")
        if any(not isinstance(item, MkkProductSyncResult) for item in self.products):
            raise TypeError("products yalniz MkkProductSyncResult icermeli")
        names = [item.product_name for item in self.products]
        if len(set(names)) != len(names):
            raise ValueError("products tekrarli product_name iceremez")
        if all(item.status == "NOT_RUN" for item in self.products):
            raise ValueError("suite report en az bir denenmis urun icermeli")

    @property
    def status(self) -> str:
        statuses = {item.status for item in self.products}
        if statuses <= {"COMPLETE", "UP_TO_DATE"}:
            return "COMPLETE"
        if "FAILED" in statuses and not any(item.successful for item in self.products):
            return "FAILED"
        return "PARTIAL"

    @property
    def total_rows_persisted(self) -> int:
        return sum(item.rows_persisted for item in self.products)

    @property
    def total_quarantined(self) -> int:
        return sum(item.quarantined_count for item in self.products)

    def to_dict(self) -> dict[str, Any]:
        status = self.status
        if status not in _SUITE_STATUSES:
            raise AssertionError("hesaplanan suite status gecersiz")
        return {
            "run_key": self.run_key,
            "suite_name": self.suite_name,
            "suite_version": self.suite_version,
            "status": status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "requested_start": None if self.requested_start is None else self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "resume": self.resume,
            "continue_on_error": self.continue_on_error,
            "max_windows_per_product": self.max_windows_per_product,
            "max_product_attempts": self.max_product_attempts,
            "database_ready": self.database_ready,
            "total_rows_persisted": self.total_rows_persisted,
            "total_quarantined": self.total_quarantined,
            "products": [item.to_dict() for item in self.products],
        }


def _make_run_key(
    suite: MkkProductSuite,
    validation: MkkSuiteValidationReport,
    *,
    started_at: datetime,
    requested_start: datetime | None,
    requested_end: datetime,
    resume: bool,
    continue_on_error: bool,
    overlap_seconds: int,
    max_window_hours: float,
    max_windows_per_product: int,
    max_product_attempts: int,
    max_pages: int,
    quarantine_invalid_items: bool,
) -> str:
    payload = {
        "suite_name": suite.suite_name,
        "suite_version": suite.suite_version,
        "started_at": started_at.isoformat(),
        "requested_start": None if requested_start is None else requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "resume": resume,
        "continue_on_error": continue_on_error,
        "overlap_seconds": overlap_seconds,
        "max_window_hours": float(max_window_hours),
        "max_windows_per_product": max_windows_per_product,
        "max_product_attempts": max_product_attempts,
        "max_pages": max_pages,
        "quarantine_invalid_items": quarantine_invalid_items,
        "products": [
            {
                "product_name": product.product_name,
                "stream_name": product.stream_name,
                "config_sha256": next(
                    row.config_sha256 for row in validation.products
                    if row.product_name == product.product_name
                ),
                "max_window_hours": product.max_window_hours,
                "overlap_seconds": product.overlap_seconds,
            }
            for product in suite.products if product.enabled
        ],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _validation_by_product(validation: MkkSuiteValidationReport) -> dict[str, MkkProductValidation]:
    rows = {item.product_name: item for item in validation.products}
    if len(rows) != len(validation.products):
        raise ValueError("validation tekrarli product_name iceriyor")
    return rows


class _ProductSyncFailure(RuntimeError):
    def __init__(
        self,
        cause: Exception,
        *,
        windows_completed: int,
        attempts: int,
        rows_persisted: int,
        pages_fetched: int,
        quarantined_count: int,
        last_window_start: datetime | None,
        last_window_end: datetime | None,
        checkpoint_window_end: datetime | None,
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.windows_completed = windows_completed
        self.attempts = attempts
        self.rows_persisted = rows_persisted
        self.pages_fetched = pages_fetched
        self.quarantined_count = quarantined_count
        self.last_window_start = last_window_start
        self.last_window_end = last_window_end
        self.checkpoint_window_end = checkpoint_window_end


def _failed_result(
    product: MkkProductDefinition,
    validation: MkkProductValidation,
    requested_end: datetime,
    *,
    attempts: int,
    error: Exception,
) -> MkkProductSyncResult:
    return MkkProductSyncResult(
        product_name=product.product_name,
        source_name=validation.source_name,
        stream_name=product.stream_name,
        config_sha256=validation.config_sha256,
        status="FAILED",
        windows_completed=0,
        attempts=attempts,
        rows_persisted=0,
        pages_fetched=0,
        quarantined_count=0,
        requested_end=requested_end,
        error=f"{type(error).__name__}: {error}",
    )


def _not_run_result(
    product: MkkProductDefinition,
    validation: MkkProductValidation,
    requested_end: datetime,
) -> MkkProductSyncResult:
    return MkkProductSyncResult(
        product_name=product.product_name,
        source_name=validation.source_name,
        stream_name=product.stream_name,
        config_sha256=validation.config_sha256,
        status="NOT_RUN",
        windows_completed=0,
        attempts=0,
        rows_persisted=0,
        pages_fetched=0,
        quarantined_count=0,
        requested_end=requested_end,
    )


def _sync_one_product(
    conn: Any,
    product: MkkProductDefinition,
    validation: MkkProductValidation,
    *,
    requested_start: datetime | None,
    requested_end: datetime,
    resume: bool,
    default_overlap_seconds: int,
    default_max_window_hours: float,
    max_windows_per_product: int,
    max_product_attempts: int,
    max_pages: int,
    quarantine_invalid_items: bool,
    environment: Mapping[str, str],
    client_factory: Callable[[MkkKapApiConfig, str], Any],
) -> MkkProductSyncResult:
    config = MkkKapApiConfig.from_json_file(product.config_path)
    config.validate_live_ready()
    lock = verify_mkk_contract_lock(product.contract_lock_path, config)
    if not isinstance(lock, Mapping):
        raise ValueError(f"{product.product_name}: contract lock nesne olmali")
    if lock.get("config_sha256") != validation.config_sha256:
        raise ValueError(f"{product.product_name}: validation sonrasi config/lock degisti")
    if config.source_name != validation.source_name:
        raise ValueError(f"{product.product_name}: validation source_name config ile uyusmuyor")
    api_key = environment.get(product.api_key_env)
    if not isinstance(api_key, str) or not api_key.strip():
        raise KapApiConfigError(f"{product.product_name}: {product.api_key_env} ortam degiskeni eksik")
    if "\n" in api_key or "\r" in api_key:
        raise KapApiConfigError(f"{product.api_key_env} satir sonu iceremez")

    overlap_seconds = default_overlap_seconds if product.overlap_seconds is None else product.overlap_seconds
    max_window_hours = default_max_window_hours if product.max_window_hours is None else product.max_window_hours
    lock_key = acquire_kap_sync_lock(conn, source=config.source_name, stream_name=product.stream_name)
    primary_error: Exception | None = None
    windows_completed = 0
    attempts = 0
    rows_persisted = 0
    pages_fetched = 0
    quarantined_count = 0
    last_start = None
    last_end = None
    checkpoint_end = None
    try:
        checkpoint = load_kap_sync_checkpoint(
            conn, source=config.source_name, stream_name=product.stream_name,
        ) if resume else None
        if checkpoint is not None:
            checkpoint_end = checkpoint.window_end
            if checkpoint.window_end >= requested_end:
                return MkkProductSyncResult(
                    product_name=product.product_name,
                    source_name=config.source_name,
                    stream_name=product.stream_name,
                    config_sha256=validation.config_sha256,
                    status="UP_TO_DATE",
                    windows_completed=0,
                    attempts=0,
                    rows_persisted=0,
                    pages_fetched=0,
                    quarantined_count=0,
                    requested_end=requested_end,
                    checkpoint_window_end=checkpoint.window_end,
                )

        internal_resume = resume and checkpoint is not None
        while windows_completed < max_windows_per_product:
            plan = plan_kap_sync_window(
                requested_start=requested_start,
                requested_end=requested_end,
                checkpoint=checkpoint,
                resume=internal_resume,
                overlap_seconds=overlap_seconds,
                max_window_hours=max_window_hours,
            )
            last_start, last_end = plan.start_at, plan.end_at
            result = None
            for attempt in range(1, max_product_attempts + 1):
                attempts += 1
                try:
                    client = client_factory(config, api_key.strip())
                    result = client.fetch_disclosures(
                        start_at=plan.start_at,
                        end_at=plan.end_at,
                        initial_cursor=None,
                        max_pages=max_pages,
                        quarantine_invalid_items=quarantine_invalid_items,
                    )
                    break
                except KapApiTransportError:
                    if attempt >= max_product_attempts:
                        raise
            assert result is not None
            if result.source != config.source_name:
                raise ValueError(
                    f"{product.product_name}: API result source config ile uyusmuyor"
                )
            if result.start_at != plan.start_at or result.end_at != plan.end_at:
                raise ValueError(
                    f"{product.product_name}: API result zaman penceresi plan ile uyusmuyor"
                )
            if result.complete and result.next_cursor is not None:
                raise ValueError(
                    f"{product.product_name}: tamamlanmis API result cursor iceremez"
                )
            rows_persisted += persist_kap_disclosures(conn, result, stream_name=product.stream_name)
            pages_fetched += result.pages_fetched
            quarantined_count += len(result.quarantined_items)
            windows_completed += 1
            if not result.complete:
                return MkkProductSyncResult(
                    product_name=product.product_name,
                    source_name=config.source_name,
                    stream_name=product.stream_name,
                    config_sha256=validation.config_sha256,
                    status="QUARANTINED",
                    windows_completed=windows_completed,
                    attempts=attempts,
                    rows_persisted=rows_persisted,
                    pages_fetched=pages_fetched,
                    quarantined_count=quarantined_count,
                    requested_end=requested_end,
                    last_window_start=last_start,
                    last_window_end=last_end,
                    checkpoint_window_end=checkpoint_end,
                )
            checkpoint = load_kap_sync_checkpoint(
                conn, source=config.source_name, stream_name=product.stream_name,
            )
            if checkpoint is None:
                raise RuntimeError("tamamlanan pencere sonrasi checkpoint bulunamadi")
            checkpoint_end = checkpoint.window_end
            if checkpoint.window_end >= requested_end:
                return MkkProductSyncResult(
                    product_name=product.product_name,
                    source_name=config.source_name,
                    stream_name=product.stream_name,
                    config_sha256=validation.config_sha256,
                    status="COMPLETE",
                    windows_completed=windows_completed,
                    attempts=attempts,
                    rows_persisted=rows_persisted,
                    pages_fetched=pages_fetched,
                    quarantined_count=quarantined_count,
                    requested_end=requested_end,
                    last_window_start=last_start,
                    last_window_end=last_end,
                    checkpoint_window_end=checkpoint.window_end,
                )
            internal_resume = True

        return MkkProductSyncResult(
            product_name=product.product_name,
            source_name=config.source_name,
            stream_name=product.stream_name,
            config_sha256=validation.config_sha256,
            status="PARTIAL",
            windows_completed=windows_completed,
            attempts=attempts,
            rows_persisted=rows_persisted,
            pages_fetched=pages_fetched,
            quarantined_count=quarantined_count,
            requested_end=requested_end,
            last_window_start=last_start,
            last_window_end=last_end,
            checkpoint_window_end=checkpoint_end,
        )
    except Exception as exc:
        primary_error = exc
        if isinstance(exc, _ProductSyncFailure):
            raise
        raise _ProductSyncFailure(
            exc,
            windows_completed=windows_completed,
            attempts=attempts,
            rows_persisted=rows_persisted,
            pages_fetched=pages_fetched,
            quarantined_count=quarantined_count,
            last_window_start=last_start,
            last_window_end=last_end,
            checkpoint_window_end=checkpoint_end,
        ) from exc
    finally:
        try:
            release_kap_sync_lock(conn, lock_key)
        except Exception:
            if primary_error is None:
                raise


def run_mkk_product_suite_sync(
    conn: Any,
    suite: MkkProductSuite,
    validation: MkkSuiteValidationReport,
    *,
    requested_start: datetime | None,
    requested_end: datetime,
    resume: bool = False,
    continue_on_error: bool = False,
    overlap_seconds: int = 300,
    max_window_hours: float = 24.0,
    max_windows_per_product: int = 1,
    max_product_attempts: int = 1,
    max_pages: int = 100,
    quarantine_invalid_items: bool = True,
    environment: Mapping[str, str] | None = None,
    client_factory: Callable[[MkkKapApiConfig, str], Any] = MkkKapApiClient,
    clock: Callable[[], datetime] | None = None,
    require_database_ready: bool = True,
) -> tuple[MkkSuiteSyncReport, MkkSuiteDatabaseReadiness]:
    if not isinstance(suite, MkkProductSuite):
        raise TypeError("suite MkkProductSuite olmali")
    if not isinstance(validation, MkkSuiteValidationReport):
        raise TypeError("validation MkkSuiteValidationReport olmali")
    if validation.suite_name != suite.suite_name or validation.suite_version != suite.suite_version:
        raise ValueError("validation farkli suite'e ait")
    requested_start = None if requested_start is None else _require_aware_datetime("requested_start", requested_start)
    requested_end = _require_aware_datetime("requested_end", requested_end)
    if requested_start is not None and requested_end < requested_start:
        raise ValueError("requested_end requested_start'tan once olamaz")
    if not resume and requested_start is None:
        raise ValueError("requested_start resume olmadan zorunlu")
    if not isinstance(resume, bool) or not isinstance(continue_on_error, bool):
        raise ValueError("resume/continue_on_error Python bool olmali")
    max_windows_per_product = _positive_int(
        "max_windows_per_product", max_windows_per_product,
        maximum=MAX_SUITE_WINDOWS_PER_PRODUCT,
    )
    max_product_attempts = _positive_int(
        "max_product_attempts", max_product_attempts,
        maximum=MAX_SUITE_PRODUCT_ATTEMPTS,
    )
    max_pages = _positive_int("max_pages", max_pages, maximum=10_000)
    if not isinstance(quarantine_invalid_items, bool) or not isinstance(require_database_ready, bool):
        raise ValueError("quarantine/require_database_ready Python bool olmali")
    env = os.environ if environment is None else environment
    if not isinstance(env, Mapping):
        raise TypeError("environment Mapping olmali")
    if not callable(client_factory):
        raise TypeError("client_factory callable olmali")
    clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
    if not callable(clock):
        raise TypeError("clock callable olmali")

    readiness = check_mkk_suite_database_readiness(conn)
    if require_database_ready and not readiness.ready:
        raise RuntimeError(
            "MKK suite PostgreSQL hazir degil: "
            f"postgres_16={readiness.postgres_16}, missing={list(readiness.missing_relations)}"
        )
    validation_rows = _validation_by_product(validation)
    enabled = tuple(item for item in suite.products if item.enabled)
    if set(validation_rows) != {item.product_name for item in enabled}:
        raise ValueError("validation etkin suite urunleriyle birebir uyusmuyor")
    if not validation.live_ready:
        raise KapApiConfigError("MKK suite canli sync icin hazir degil")

    started_at = _require_aware_datetime("clock", clock())
    suite_lock = acquire_kap_sync_lock(conn, source="MKK_SUITE", stream_name=suite.suite_name)
    primary_error: Exception | None = None
    results: list[MkkProductSyncResult] = []
    stop = False
    try:
        for product in enabled:
            validation_row = validation_rows[product.product_name]
            if stop:
                results.append(_not_run_result(product, validation_row, requested_end))
                continue
            try:
                result = _sync_one_product(
                    conn,
                    product,
                    validation_row,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    resume=resume,
                    default_overlap_seconds=overlap_seconds,
                    default_max_window_hours=max_window_hours,
                    max_windows_per_product=max_windows_per_product,
                    max_product_attempts=max_product_attempts,
                    max_pages=max_pages,
                    quarantine_invalid_items=quarantine_invalid_items,
                    environment=env,
                    client_factory=client_factory,
                )
            except _ProductSyncFailure as exc:
                result = MkkProductSyncResult(
                    product_name=product.product_name,
                    source_name=validation_row.source_name,
                    stream_name=product.stream_name,
                    config_sha256=validation_row.config_sha256,
                    status="FAILED",
                    windows_completed=exc.windows_completed,
                    attempts=exc.attempts,
                    rows_persisted=exc.rows_persisted,
                    pages_fetched=exc.pages_fetched,
                    quarantined_count=exc.quarantined_count,
                    requested_end=requested_end,
                    last_window_start=exc.last_window_start,
                    last_window_end=exc.last_window_end,
                    checkpoint_window_end=exc.checkpoint_window_end,
                    error=f"{type(exc.cause).__name__}: {exc.cause}",
                )
            except (
                KapApiConfigError,
                KapApiProtocolError,
                KapApiTransportError,
                ValueError,
                RuntimeError,
                OSError,
            ) as exc:
                result = _failed_result(
                    product, validation_row, requested_end, attempts=0, error=exc,
                )
            results.append(result)
            if result.status in {"FAILED", "QUARANTINED"} and not continue_on_error:
                stop = True
        completed_at = _require_aware_datetime("clock", clock())
        report = MkkSuiteSyncReport(
            run_key=_make_run_key(
                suite, validation, started_at=started_at,
                requested_start=requested_start, requested_end=requested_end,
                resume=resume,
                continue_on_error=continue_on_error,
                overlap_seconds=overlap_seconds,
                max_window_hours=max_window_hours,
                max_windows_per_product=max_windows_per_product,
                max_product_attempts=max_product_attempts,
                max_pages=max_pages,
                quarantine_invalid_items=quarantine_invalid_items,
            ),
            suite_name=suite.suite_name,
            suite_version=suite.suite_version,
            started_at=started_at,
            completed_at=completed_at,
            requested_start=requested_start,
            requested_end=requested_end,
            resume=resume,
            continue_on_error=continue_on_error,
            max_windows_per_product=max_windows_per_product,
            max_product_attempts=max_product_attempts,
            database_ready=readiness.ready,
            products=tuple(results),
        )
        return report, readiness
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        try:
            release_kap_sync_lock(conn, suite_lock)
        except Exception:
            if primary_error is None:
                raise


def persist_mkk_suite_sync_report(conn: Any, report: MkkSuiteSyncReport) -> None:
    if not isinstance(report, MkkSuiteSyncReport):
        raise TypeError("report MkkSuiteSyncReport olmali")
    payload = report.to_dict()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.mkk_suite_sync_runs (
                  run_key, suite_name, suite_version, started_at, completed_at,
                  status, requested_start, requested_end, resume,
                  continue_on_error, max_windows_per_product, max_product_attempts,
                  total_rows_persisted, total_quarantined, report
                ) VALUES (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s::jsonb
                )
                ON CONFLICT (run_key) DO NOTHING
                """,
                (
                    report.run_key, report.suite_name, report.suite_version,
                    report.started_at, report.completed_at, report.status,
                    report.requested_start, report.requested_end, report.resume,
                    report.continue_on_error, report.max_windows_per_product,
                    report.max_product_attempts, report.total_rows_persisted,
                    report.total_quarantined, _canonical_json(payload),
                ),
            )
            for item in report.products:
                cur.execute(
                    """
                    INSERT INTO raw.mkk_suite_product_runs (
                      run_key, product_name, source_name, stream_name,
                      config_sha256, status, windows_completed, attempts,
                      rows_persisted, pages_fetched, quarantined_count,
                      requested_end, last_window_start, last_window_end,
                      checkpoint_window_end, error, details
                    ) VALUES (
                      %s, %s, %s, %s,
                      %s, %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s::jsonb
                    )
                    ON CONFLICT (run_key, product_name) DO NOTHING
                    """,
                    (
                        report.run_key, item.product_name, item.source_name,
                        item.stream_name, item.config_sha256, item.status,
                        item.windows_completed, item.attempts,
                        item.rows_persisted, item.pages_fetched,
                        item.quarantined_count, item.requested_end,
                        item.last_window_start, item.last_window_end,
                        item.checkpoint_window_end, item.error,
                        _canonical_json(item.to_dict()),
                    ),
                )
