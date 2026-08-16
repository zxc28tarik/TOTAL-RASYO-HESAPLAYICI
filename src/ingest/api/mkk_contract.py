from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from src.ingest.api.mkk_kap import (
    KapApiConfigError,
    KapContractSampleCapture,
    KapApiProtocolError,
    MkkKapApiClient,
    MkkKapApiConfig,
    _canonical_json,
    _get_path,
    _require_aware_datetime,
)


MAX_CONTRACT_SAMPLE_ITEMS = 10_000


def _write_private_temp(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return tmp_path


def _install_private_pair(
    first_path: Path,
    first_text: str,
    second_path: Path,
    second_text: str,
    *,
    overwrite: bool,
) -> tuple[Path, Path]:
    for target in (first_path, second_path):
        if target.exists() and not overwrite:
            raise KapApiConfigError(f"dosya zaten var; --force gerekli: {target}")

    first_tmp: Path | None = None
    second_tmp: Path | None = None
    backups: dict[Path, Path] = {}
    installed: list[Path] = []
    try:
        first_tmp = _write_private_temp(first_path, first_text)
        second_tmp = _write_private_temp(second_path, second_text)
        if overwrite:
            for target in (first_path, second_path):
                if target.exists():
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{target.name}.backup.", dir=str(target.parent)
                    )
                    os.close(fd)
                    backup = Path(backup_name)
                    backup.unlink(missing_ok=True)
                    os.replace(target, backup)
                    backups[target] = backup
        os.replace(first_tmp, first_path)
        installed.append(first_path)
        os.replace(second_tmp, second_path)
        installed.append(second_path)
        for target in installed:
            os.chmod(target, 0o600)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for target in installed:
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        if first_tmp is not None:
            first_tmp.unlink(missing_ok=True)
        if second_tmp is not None:
            second_tmp.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    return first_path, second_path


@dataclass(frozen=True)
class MkkContractSampleReport:
    source_name: str
    endpoint_host: str
    endpoint_path: str
    method: str
    config_sha256: str
    sample_sha256: str
    items_seen: int
    items_validated: int
    duplicate_ids: int
    first_disclosure_id: str | None
    last_disclosure_id: str | None
    min_published_at: datetime | None
    max_published_at: datetime | None
    optional_field_coverage: Mapping[str, int]
    next_cursor_present: bool
    live_ready: bool
    live_ready_error: str | None
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "endpoint_host": self.endpoint_host,
            "endpoint_path": self.endpoint_path,
            "method": self.method,
            "config_sha256": self.config_sha256,
            "sample_sha256": self.sample_sha256,
            "items_seen": self.items_seen,
            "items_validated": self.items_validated,
            "duplicate_ids": self.duplicate_ids,
            "first_disclosure_id": self.first_disclosure_id,
            "last_disclosure_id": self.last_disclosure_id,
            "min_published_at": None if self.min_published_at is None else self.min_published_at.isoformat(),
            "max_published_at": None if self.max_published_at is None else self.max_published_at.isoformat(),
            "optional_field_coverage": dict(self.optional_field_coverage),
            "next_cursor_present": self.next_cursor_present,
            "live_ready": self.live_ready,
            "live_ready_error": self.live_ready_error,
            "checked_at": self.checked_at.isoformat(),
        }


def _config_fingerprint(config: MkkKapApiConfig) -> str:
    if not isinstance(config, MkkKapApiConfig):
        raise TypeError("config MkkKapApiConfig olmali")
    canonical_text, digest = _canonical_json(asdict(config))
    if not canonical_text:
        raise AssertionError("config canonical JSON bos olamaz")
    return digest


def load_contract_sample(path: str | Path) -> Any:
    sample_path = Path(path)
    try:
        text = sample_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KapApiConfigError(f"MKK contract sample okunamadi: {sample_path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KapApiProtocolError("MKK contract sample gecersiz JSON") from exc
    _canonical_json(payload)
    return payload


def write_mkk_contract_capture(
    *,
    sample_path: str | Path,
    metadata_path: str | Path,
    config: MkkKapApiConfig,
    capture: KapContractSampleCapture,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Persist a raw contract sample without leaking authentication material."""

    if not isinstance(config, MkkKapApiConfig):
        raise TypeError("config MkkKapApiConfig olmali")
    if not isinstance(capture, KapContractSampleCapture):
        raise TypeError("capture KapContractSampleCapture olmali")
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite Python bool olmali")
    if capture.source_name != config.source_name:
        raise ValueError("capture source_name config ile uyusmuyor")
    if capture.endpoint_url != config.base_url + config.path or capture.method != config.method:
        raise ValueError("capture endpoint config ile uyusmuyor")

    sample_target = Path(sample_path)
    metadata_target = Path(metadata_path)
    if sample_target.resolve() == metadata_target.resolve():
        raise ValueError("sample_path ve metadata_path farkli olmali")
    sample_text, sample_sha = _canonical_json(capture.payload)
    if sample_sha != capture.payload_sha256:
        raise ValueError("capture payload SHA uyusmuyor")
    metadata = {
        "capture_version": 1,
        "source_name": capture.source_name,
        "endpoint": {
            "host": (urlparse(config.base_url).hostname or "").lower(),
            "path": config.path,
            "method": config.method,
            "api_key_header": config.api_key_header,
        },
        "request_window": {
            "start_at": capture.start_at.isoformat(),
            "end_at": capture.end_at.isoformat(),
        },
        "captured_at": capture.captured_at.isoformat(),
        "items_seen": capture.items_seen,
        "items_validated": capture.items_validated,
        "next_cursor_present": capture.next_cursor_present,
        "config_sha256": _config_fingerprint(config),
        "sample_sha256": sample_sha,
        "sample_file": sample_target.name,
        "authentication_material_persisted": False,
    }
    metadata_text = json.dumps(
        metadata, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    return _install_private_pair(
        sample_target,
        sample_text + "\n",
        metadata_target,
        metadata_text,
        overwrite=overwrite,
    )


def validate_mkk_contract_sample(
    config: MkkKapApiConfig,
    payload: Any,
    *,
    checked_at: datetime,
    validate_items_limit: int = 100,
) -> MkkContractSampleReport:
    if not isinstance(config, MkkKapApiConfig):
        raise TypeError("config MkkKapApiConfig olmali")
    checked_at = _require_aware_datetime("checked_at", checked_at)
    if (
        isinstance(validate_items_limit, bool)
        or not isinstance(validate_items_limit, int)
        or validate_items_limit <= 0
        or validate_items_limit > MAX_CONTRACT_SAMPLE_ITEMS
    ):
        raise ValueError("validate_items_limit guvenli aralikta pozitif Python int olmali")

    sample_text, sample_sha256 = _canonical_json(payload)
    if not sample_text:
        raise AssertionError("sample canonical JSON bos olamaz")

    items = _get_path(payload, config.items_path, required=True)
    if not isinstance(items, list):
        raise KapApiProtocolError("items_path bir listeye cikmali")
    if config.page_size is not None and len(items) > config.page_size:
        raise KapApiProtocolError("sample config page_size sinirindan fazla item iceriyor")

    client = MkkKapApiClient(config, "CONTRACT-SAMPLE-ONLY")
    validated = []
    duplicate_ids = 0
    seen: dict[str, str] = {}
    optional_fields = (
        "ticker", "company_id", "notification_type", "subject", "source_url"
    )
    coverage = {field: 0 for field in optional_fields}

    for item in items[:validate_items_limit]:
        envelope = client._normalize_item(item, checked_at)  # same live normalization contract
        previous = seen.get(envelope.disclosure_id)
        if previous is None:
            seen[envelope.disclosure_id] = envelope.payload_sha256
        elif previous == envelope.payload_sha256:
            duplicate_ids += 1
        else:
            raise KapApiProtocolError(
                f"sample ayni disclosure_id icin farkli payload iceriyor: {envelope.disclosure_id}"
            )
        for field in optional_fields:
            if getattr(envelope, field) is not None:
                coverage[field] += 1
        validated.append(envelope)

    next_raw = (
        _get_path(payload, config.next_cursor_path)
        if config.next_cursor_path
        else None
    )
    next_cursor_present = next_raw not in (None, "")
    if next_cursor_present and not isinstance(next_raw, (str, int)):
        raise KapApiProtocolError("next cursor metin veya tam sayi olmali")

    published = [item.published_at for item in validated]
    parsed_url = urlparse(config.base_url)
    try:
        config.validate_live_ready()
    except KapApiConfigError as exc:
        live_ready = False
        live_ready_error = str(exc)
    else:
        live_ready = True
        live_ready_error = None
    return MkkContractSampleReport(
        source_name=config.source_name,
        endpoint_host=(parsed_url.hostname or "").lower(),
        endpoint_path=config.path,
        method=config.method,
        config_sha256=_config_fingerprint(config),
        sample_sha256=sample_sha256,
        items_seen=len(items),
        items_validated=len(validated),
        duplicate_ids=duplicate_ids,
        first_disclosure_id=validated[0].disclosure_id if validated else None,
        last_disclosure_id=validated[-1].disclosure_id if validated else None,
        min_published_at=min(published) if published else None,
        max_published_at=max(published) if published else None,
        optional_field_coverage=coverage,
        next_cursor_present=next_cursor_present,
        live_ready=live_ready,
        live_ready_error=live_ready_error,
        checked_at=checked_at,
    )


def write_mkk_contract_lock(
    path: str | Path,
    config: MkkKapApiConfig,
    report: MkkContractSampleReport,
) -> Path:
    if not isinstance(config, MkkKapApiConfig):
        raise TypeError("config MkkKapApiConfig olmali")
    if not isinstance(report, MkkContractSampleReport):
        raise TypeError("report MkkContractSampleReport olmali")
    if report.config_sha256 != _config_fingerprint(config):
        raise ValueError("report config fingerprint ile config uyusmuyor")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract_lock_version": 1,
        "source_name": config.source_name,
        "endpoint": {
            "host": report.endpoint_host,
            "path": config.path,
            "method": config.method,
            "api_key_header": config.api_key_header,
        },
        "request_contract": {
            "start_param": config.start_param,
            "end_param": config.end_param,
            "cursor_param": config.cursor_param,
            "page_size_param": config.page_size_param,
            "page_size": config.page_size,
            "static_param_keys": sorted(config.static_params or {}),
        },
        "response_contract": {
            "items_path": config.items_path,
            "next_cursor_path": config.next_cursor_path,
            "fields": dict(config.fields),
        },
        "config_sha256": report.config_sha256,
        "sample_sha256": report.sample_sha256,
        "sample_validation": report.to_dict(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    target.write_text(text + "\n", encoding="utf-8")
    return target


def verify_mkk_contract_lock(
    path: str | Path,
    config: MkkKapApiConfig,
) -> Mapping[str, Any]:
    if not isinstance(config, MkkKapApiConfig):
        raise TypeError("config MkkKapApiConfig olmali")
    lock_path = Path(path)
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KapApiConfigError(f"MKK contract lock okunamadi: {lock_path}") from exc
    except json.JSONDecodeError as exc:
        raise KapApiConfigError("MKK contract lock gecersiz JSON") from exc
    if not isinstance(payload, Mapping):
        raise KapApiConfigError("MKK contract lock JSON nesne olmali")
    if payload.get("contract_lock_version") != 1:
        raise KapApiConfigError("MKK contract lock surumu desteklenmiyor")
    expected = _config_fingerprint(config)
    actual = payload.get("config_sha256")
    if not isinstance(actual, str) or actual != expected:
        raise KapApiConfigError("MKK API config contract lock ile uyusmuyor")
    sample_sha = payload.get("sample_sha256")
    if not isinstance(sample_sha, str) or not __import__("re").fullmatch(r"[0-9a-f]{64}", sample_sha):
        raise KapApiConfigError("MKK contract lock sample_sha256 gecersiz")
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, Mapping):
        raise KapApiConfigError("MKK contract lock endpoint nesnesi eksik")
    host = (urlparse(config.base_url).hostname or "").lower()
    if endpoint.get("host") != host or endpoint.get("path") != config.path or endpoint.get("method") != config.method:
        raise KapApiConfigError("MKK contract lock endpoint ozeti config ile uyusmuyor")
    return payload
