from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from src.ingest.api.mkk_contract import (
    load_contract_sample,
    validate_mkk_contract_sample,
    verify_mkk_contract_lock,
)
from src.ingest.api.mkk_kap import KapApiConfigError, MkkKapApiConfig, _require_aware_datetime
from src.ingest.kap_sync import KapBackfillWindow, plan_kap_backfill_windows

MAX_MKK_SUITE_PRODUCTS = 100
MAX_MKK_SUITE_TOTAL_WINDOWS = 100_000


def _nonempty_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KapApiConfigError(f"{name} dolu metin olmali")
    return value.strip()


def _optional_number(name: str, value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise KapApiConfigError(f"{name} sayi olmali")
    if integer:
        if not isinstance(value, int):
            raise KapApiConfigError(f"{name} Python int olmali")
        return value
    if not isinstance(value, (int, float)):
        raise KapApiConfigError(f"{name} sayi olmali")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise KapApiConfigError(f"{name} sonlu sayi olmali")
    return numeric


@dataclass(frozen=True)
class MkkProductDefinition:
    product_name: str
    config_path: Path
    contract_lock_path: Path
    sample_path: Path
    api_key_env: str
    stream_name: str
    enabled: bool = True
    max_window_hours: float | None = None
    overlap_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_name", _nonempty_text("product_name", self.product_name))
        object.__setattr__(self, "api_key_env", _nonempty_text("api_key_env", self.api_key_env))
        object.__setattr__(self, "stream_name", _nonempty_text("stream_name", self.stream_name))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise KapApiConfigError("api_key_env gecerli ortam degiskeni adi olmali")
        if not isinstance(self.enabled, bool):
            raise KapApiConfigError("enabled Python bool olmali")
        for name in ("config_path", "contract_lock_path", "sample_path"):
            path = getattr(self, name)
            if not isinstance(path, Path):
                raise TypeError(f"{name} Path olmali")
        if self.max_window_hours is not None:
            value = _optional_number("max_window_hours", self.max_window_hours)
            assert isinstance(value, float)
            if value <= 0:
                raise KapApiConfigError("max_window_hours pozitif olmali")
            object.__setattr__(self, "max_window_hours", value)
        if self.overlap_seconds is not None:
            value = _optional_number("overlap_seconds", self.overlap_seconds, integer=True)
            assert isinstance(value, int)
            if value < 0:
                raise KapApiConfigError("overlap_seconds negatif olamaz")
            object.__setattr__(self, "overlap_seconds", value)


@dataclass(frozen=True)
class MkkProductSuite:
    suite_name: str
    suite_version: int
    manifest_path: Path
    products: tuple[MkkProductDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_name", _nonempty_text("suite_name", self.suite_name))
        if isinstance(self.suite_version, bool) or not isinstance(self.suite_version, int) or self.suite_version <= 0:
            raise KapApiConfigError("suite_version pozitif Python int olmali")
        if not isinstance(self.manifest_path, Path):
            raise TypeError("manifest_path Path olmali")
        if not self.products:
            raise KapApiConfigError("MKK suite en az bir urun icermeli")
        if len(self.products) > MAX_MKK_SUITE_PRODUCTS:
            raise KapApiConfigError("MKK suite urun sayisi guvenli siniri asiyor")
        names = [item.product_name for item in self.products]
        if len(set(names)) != len(names):
            raise KapApiConfigError("MKK suite product_name degerleri benzersiz olmali")

    @classmethod
    def from_json_file(cls, path: str | Path) -> "MkkProductSuite":
        manifest_path = Path(path).resolve()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise KapApiConfigError(f"MKK suite manifest okunamadi: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise KapApiConfigError("MKK suite manifest gecersiz JSON") from exc
        if not isinstance(payload, Mapping):
            raise KapApiConfigError("MKK suite manifest JSON nesne olmali")
        unknown_top = set(payload) - {"suite_name", "suite_version", "products", "_warning"}
        if unknown_top:
            raise KapApiConfigError(
                f"MKK suite manifest desteklenmeyen alanlar: {sorted(unknown_top)}"
            )
        suite_name = _nonempty_text("suite_name", payload.get("suite_name"))
        suite_version = payload.get("suite_version")
        if isinstance(suite_version, bool) or not isinstance(suite_version, int) or suite_version <= 0:
            raise KapApiConfigError("suite_version pozitif Python int olmali")
        raw_products = payload.get("products")
        if not isinstance(raw_products, list) or not raw_products:
            raise KapApiConfigError("products dolu liste olmali")
        if len(raw_products) > MAX_MKK_SUITE_PRODUCTS:
            raise KapApiConfigError("MKK suite urun sayisi guvenli siniri asiyor")
        base = manifest_path.parent
        products: list[MkkProductDefinition] = []
        for index, raw in enumerate(raw_products):
            if not isinstance(raw, Mapping):
                raise KapApiConfigError(f"products[{index}] nesne olmali")
            allowed_product = {
                "product_name", "config", "contract_lock", "sample", "api_key_env",
                "stream_name", "enabled", "max_window_hours", "overlap_seconds", "_warning",
            }
            unknown = set(raw) - allowed_product
            if unknown:
                raise KapApiConfigError(
                    f"products[{index}] desteklenmeyen alanlar: {sorted(unknown)}"
                )
            def resolve_path(field: str) -> Path:
                text = _nonempty_text(f"products[{index}].{field}", raw.get(field))
                candidate = Path(text)
                return (candidate if candidate.is_absolute() else base / candidate).resolve()
            config_path = resolve_path("config")
            contract_lock_path = resolve_path("contract_lock")
            sample_path = resolve_path("sample")
            if len({config_path, contract_lock_path, sample_path}) != 3:
                raise KapApiConfigError(
                    f"products[{index}] config, sample ve contract_lock yollari farkli olmali"
                )
            products.append(MkkProductDefinition(
                product_name=_nonempty_text(f"products[{index}].product_name", raw.get("product_name")),
                config_path=config_path,
                contract_lock_path=contract_lock_path,
                sample_path=sample_path,
                api_key_env=_nonempty_text(f"products[{index}].api_key_env", raw.get("api_key_env")),
                stream_name=_nonempty_text(f"products[{index}].stream_name", raw.get("stream_name")),
                enabled=raw.get("enabled", True),
                max_window_hours=raw.get("max_window_hours"),
                overlap_seconds=raw.get("overlap_seconds"),
            ))
        return cls(
            suite_name=suite_name,
            suite_version=suite_version,
            manifest_path=manifest_path,
            products=tuple(products),
        )


@dataclass(frozen=True)
class MkkProductValidation:
    product_name: str
    source_name: str
    stream_name: str
    config_path: str
    contract_lock_path: str
    sample_path: str
    config_sha256: str
    sample_sha256: str
    items_validated: int
    live_ready: bool
    api_key_env: str
    api_key_present: bool

    def __post_init__(self) -> None:
        for name in (
            "product_name", "source_name", "stream_name", "config_path",
            "contract_lock_path", "sample_path", "api_key_env",
        ):
            object.__setattr__(self, name, _nonempty_text(name, getattr(self, name)))
        for name in ("config_sha256", "sample_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not __import__("re").fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} 64 karakter kucuk harf hex olmali")
        if isinstance(self.items_validated, bool) or not isinstance(self.items_validated, int) or self.items_validated < 0:
            raise ValueError("items_validated negatif olmayan Python int olmali")
        if not isinstance(self.live_ready, bool) or not isinstance(self.api_key_present, bool):
            raise ValueError("live_ready ve api_key_present Python bool olmali")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "source_name": self.source_name,
            "stream_name": self.stream_name,
            "config_path": self.config_path,
            "contract_lock_path": self.contract_lock_path,
            "sample_path": self.sample_path,
            "config_sha256": self.config_sha256,
            "sample_sha256": self.sample_sha256,
            "items_validated": self.items_validated,
            "live_ready": self.live_ready,
            "api_key_env": self.api_key_env,
            "api_key_present": self.api_key_present,
        }


@dataclass(frozen=True)
class MkkSuiteValidationReport:
    suite_name: str
    suite_version: int
    checked_at: datetime
    products: tuple[MkkProductValidation, ...]

    def __post_init__(self) -> None:
        _require_aware_datetime("checked_at", self.checked_at)
        if not self.products:
            raise ValueError("validation report en az bir urun icermeli")

    @property
    def live_ready(self) -> bool:
        return all(item.live_ready and item.api_key_present for item in self.products)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "suite_version": self.suite_version,
            "checked_at": self.checked_at.isoformat(),
            "enabled_product_count": len(self.products),
            "live_ready": self.live_ready,
            "products": [item.to_dict() for item in self.products],
        }


def validate_mkk_product_suite(
    suite: MkkProductSuite,
    *,
    checked_at: datetime,
    environment: Mapping[str, str] | None = None,
    require_api_keys: bool = False,
    require_live_ready: bool = False,
    validate_items_limit: int = 100,
) -> MkkSuiteValidationReport:
    if not isinstance(suite, MkkProductSuite):
        raise TypeError("suite MkkProductSuite olmali")
    checked_at = _require_aware_datetime("checked_at", checked_at)
    if not isinstance(require_api_keys, bool) or not isinstance(require_live_ready, bool):
        raise ValueError("require flags Python bool olmali")
    env = os.environ if environment is None else environment
    if not isinstance(env, Mapping):
        raise TypeError("environment Mapping olmali")

    rows: list[MkkProductValidation] = []
    stream_keys: set[tuple[str, str]] = set()
    for product in suite.products:
        if not product.enabled:
            continue
        config = MkkKapApiConfig.from_json_file(product.config_path)
        lock = verify_mkk_contract_lock(product.contract_lock_path, config)
        sample = load_contract_sample(product.sample_path)
        report = validate_mkk_contract_sample(
            config, sample, checked_at=checked_at,
            validate_items_limit=validate_items_limit,
        )
        if lock.get("sample_sha256") != report.sample_sha256:
            raise KapApiConfigError(
                f"{product.product_name}: sample contract lock ile uyusmuyor"
            )
        key = (config.source_name, product.stream_name)
        if key in stream_keys:
            raise KapApiConfigError(
                f"ayni source_name/stream_name iki urunde kullaniliyor: {key}"
            )
        stream_keys.add(key)
        raw_secret = env.get(product.api_key_env)
        api_key_present = isinstance(raw_secret, str) and bool(raw_secret.strip())
        if api_key_present and ("\n" in raw_secret or "\r" in raw_secret):
            raise KapApiConfigError(f"{product.api_key_env} satir sonu iceremez")
        if require_api_keys and not api_key_present:
            raise KapApiConfigError(
                f"{product.product_name}: {product.api_key_env} ortam degiskeni eksik"
            )
        if require_live_ready and not report.live_ready:
            raise KapApiConfigError(
                f"{product.product_name}: config canli cagrıya hazir degil: {report.live_ready_error}"
            )
        rows.append(MkkProductValidation(
            product_name=product.product_name,
            source_name=config.source_name,
            stream_name=product.stream_name,
            config_path=str(product.config_path),
            contract_lock_path=str(product.contract_lock_path),
            sample_path=str(product.sample_path),
            config_sha256=report.config_sha256,
            sample_sha256=report.sample_sha256,
            items_validated=report.items_validated,
            live_ready=report.live_ready,
            api_key_env=product.api_key_env,
            api_key_present=api_key_present,
        ))
    if not rows:
        raise KapApiConfigError("MKK suite etkin urun icermiyor")
    return MkkSuiteValidationReport(
        suite_name=suite.suite_name,
        suite_version=suite.suite_version,
        checked_at=checked_at,
        products=tuple(rows),
    )


@dataclass(frozen=True)
class MkkProductBackfillPlan:
    product_name: str
    source_name: str
    stream_name: str
    config_sha256: str
    live_ready: bool
    api_key_present: bool
    windows: tuple[KapBackfillWindow, ...]

    def __post_init__(self) -> None:
        for name in ("product_name", "source_name", "stream_name"):
            object.__setattr__(self, name, _nonempty_text(name, getattr(self, name)))
        if not isinstance(self.config_sha256, str) or not __import__("re").fullmatch(
            r"[0-9a-f]{64}", self.config_sha256
        ):
            raise ValueError("config_sha256 64 karakter kucuk harf hex olmali")
        if not isinstance(self.live_ready, bool) or not isinstance(self.api_key_present, bool):
            raise ValueError("live_ready ve api_key_present Python bool olmali")
        if not isinstance(self.windows, tuple) or not self.windows:
            raise ValueError("windows dolu tuple olmali")
        if any(not isinstance(item, KapBackfillWindow) for item in self.windows):
            raise TypeError("windows yalniz KapBackfillWindow icermeli")

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_name": self.product_name,
            "source_name": self.source_name,
            "stream_name": self.stream_name,
            "config_sha256": self.config_sha256,
            "live_ready": self.live_ready,
            "api_key_present": self.api_key_present,
            "window_count": len(self.windows),
            "windows": [
                {
                    "index": item.index,
                    "start_at": item.start_at.isoformat(),
                    "end_at": item.end_at.isoformat(),
                    "overlap_seconds": item.overlap_seconds,
                }
                for item in self.windows
            ],
        }


def plan_mkk_suite_backfill(
    suite: MkkProductSuite,
    validation: MkkSuiteValidationReport,
    *,
    start_at: datetime,
    end_at: datetime,
    max_window_hours: float = 24.0,
    overlap_seconds: int = 300,
) -> tuple[MkkProductBackfillPlan, ...]:
    if not isinstance(suite, MkkProductSuite):
        raise TypeError("suite MkkProductSuite olmali")
    if not isinstance(validation, MkkSuiteValidationReport):
        raise TypeError("validation MkkSuiteValidationReport olmali")
    if validation.suite_name != suite.suite_name or validation.suite_version != suite.suite_version:
        raise ValueError("validation farkli suite'e ait")
    # Validate caller-level defaults even when every product supplies an
    # override. Otherwise a malformed CLI value could be silently ignored.
    plan_kap_backfill_windows(
        start_at=start_at,
        end_at=end_at,
        max_window_hours=max_window_hours,
        overlap_seconds=overlap_seconds,
    )
    by_name = {row.product_name: row for row in validation.products}
    plans: list[MkkProductBackfillPlan] = []
    total_windows = 0
    for product in suite.products:
        if not product.enabled:
            continue
        row = by_name.get(product.product_name)
        if row is None:
            raise ValueError(f"validation urun sonucu eksik: {product.product_name}")
        product_hours = max_window_hours if product.max_window_hours is None else product.max_window_hours
        product_overlap = overlap_seconds if product.overlap_seconds is None else product.overlap_seconds
        windows = plan_kap_backfill_windows(
            start_at=start_at,
            end_at=end_at,
            max_window_hours=product_hours,
            overlap_seconds=product_overlap,
        )
        total_windows += len(windows)
        if total_windows > MAX_MKK_SUITE_TOTAL_WINDOWS:
            raise ValueError("MKK suite toplam backfill pencere sayisi guvenli siniri asiyor")
        plans.append(MkkProductBackfillPlan(
            product_name=product.product_name,
            source_name=row.source_name,
            stream_name=row.stream_name,
            config_sha256=row.config_sha256,
            live_ready=row.live_ready,
            api_key_present=row.api_key_present,
            windows=windows,
        ))
    return tuple(plans)
