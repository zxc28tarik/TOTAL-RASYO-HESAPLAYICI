from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

import requests


class KapApiConfigError(ValueError):
    """Raised when the portal-specific endpoint mapping is incomplete."""


class KapApiProtocolError(RuntimeError):
    """Raised when an API response violates the configured contract."""


class KapApiTransportError(RuntimeError):
    """Raised when the official API cannot be reached after retries."""


MAX_API_PAGE_SIZE = 100_000
MAX_API_PAGES = 10_000
MAX_API_RETRIES = 10
MAX_API_TIMEOUT_SECONDS = 300.0
MAX_FUTURE_SKEW_SECONDS = 86_400
MAX_MIN_REQUEST_INTERVAL_SECONDS = 60.0
MAX_RETRY_AFTER_SECONDS = 300.0
MAX_RESPONSE_BYTES_LIMIT = 100_000_000
MAX_ITEM_PAYLOAD_BYTES_LIMIT = 25_000_000


def _validate_json_tree(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KapApiProtocolError(f"JSON sonlu olmayan sayi iceriyor: {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_tree(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise KapApiProtocolError(f"JSON nesne anahtari string olmali: {path}")
            _validate_json_tree(item, path=f"{path}.{key}")
        return
    raise KapApiProtocolError(f"JSON desteklenmeyen tip iceriyor: {path}={type(value).__name__}")


def _scalar_id_text(name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise KapApiProtocolError(f"{name} metin veya tam sayi olmali")
    text = str(value).strip()
    if not text:
        raise KapApiProtocolError(f"{name} bos olamaz")
    return text


def _optional_string(name: str, value: Any, *, uppercase: bool = False) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KapApiProtocolError(f"{name} string olmali")
    text = value.strip()
    if not text:
        return None
    return text.upper() if uppercase else text


def _require_aware_datetime(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} datetime olmali")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} timezone icermeli")
    return value


def _parse_aware_datetime(name: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise KapApiProtocolError(f"{name} dolu ISO-8601 metni olmali")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise KapApiProtocolError(f"{name} gecersiz zaman damgasi: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KapApiProtocolError(f"{name} timezone icermeli: {value!r}")
    return parsed


def _get_path(payload: Any, path: Optional[str], *, required: bool = False) -> Any:
    if path is None or path == "":
        if required:
            raise KapApiConfigError("zorunlu JSON yolu bos")
        return None
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                if required:
                    raise KapApiProtocolError(f"JSON yolu bulunamadi: {path}")
                return None
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                if required:
                    raise KapApiProtocolError(f"liste yolu sayisal olmali: {path}") from exc
                return None
            if index < 0 or index >= len(current):
                if required:
                    raise KapApiProtocolError(f"liste yolu aralik disi: {path}")
                return None
            current = current[index]
            continue
        if required:
            raise KapApiProtocolError(f"JSON yolu nesne/listede ilerlemiyor: {path}")
        return None
    return current


def _canonical_json(payload: Any) -> tuple[str, str]:
    _validate_json_tree(payload)
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise KapApiProtocolError("KAP payload JSON olarak kanoniklestirilemiyor") from exc
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


def _canonical_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    return _canonical_json(payload)


@dataclass(frozen=True)
class KapDisclosureEnvelope:
    disclosure_id: str
    published_at: datetime
    ticker: Optional[str]
    company_id: Optional[str]
    notification_type: Optional[str]
    subject: Optional[str]
    source_url: Optional[str]
    payload: Mapping[str, Any]
    payload_sha256: str
    fetched_at: datetime
    source: str = "MKK_KAP_API"


@dataclass(frozen=True)
class KapQuarantinedItem:
    page_number: int
    item_index: int
    cursor_value: Optional[str]
    reason: str
    payload: Any
    payload_sha256: str
    fetched_at: datetime
    source: str = "MKK_KAP_API"

    def __post_init__(self) -> None:
        if isinstance(self.page_number, bool) or not isinstance(self.page_number, int) or self.page_number <= 0:
            raise ValueError("page_number pozitif Python int olmali")
        if isinstance(self.item_index, bool) or not isinstance(self.item_index, int) or self.item_index < 0:
            raise ValueError("item_index negatif olmayan Python int olmali")
        if self.cursor_value is not None and (
            not isinstance(self.cursor_value, str) or not self.cursor_value.strip()
        ):
            raise ValueError("cursor_value None veya dolu metin olmali")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason dolu metin olmali")
        if len(self.reason.encode("utf-8")) > 8192:
            raise ValueError("reason 8192 byte sinirini asiyor")
        if not isinstance(self.payload_sha256, str) or not __import__("re").fullmatch(
            r"[0-9a-f]{64}", self.payload_sha256
        ):
            raise ValueError("payload_sha256 64 karakter kucuk harf hex olmali")
        _require_aware_datetime("fetched_at", self.fetched_at)
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source dolu metin olmali")
        _canonical_json(self.payload)


@dataclass(frozen=True)
class KapFetchResult:
    disclosures: tuple[KapDisclosureEnvelope, ...]
    next_cursor: Optional[str]
    pages_fetched: int
    start_at: datetime
    end_at: datetime
    completed_at: datetime
    quarantined_items: tuple[KapQuarantinedItem, ...] = ()
    complete: bool = True
    source: str = "MKK_KAP_API"

    def __post_init__(self) -> None:
        for name in ("start_at", "end_at", "completed_at"):
            _require_aware_datetime(name, getattr(self, name))
        if self.end_at < self.start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if isinstance(self.pages_fetched, bool) or not isinstance(self.pages_fetched, int) or self.pages_fetched < 0:
            raise ValueError("pages_fetched negatif olmayan Python int olmali")
        if self.next_cursor is not None and (
            not isinstance(self.next_cursor, str) or not self.next_cursor.strip()
        ):
            raise ValueError("next_cursor None veya dolu metin olmali")
        if not isinstance(self.complete, bool):
            raise ValueError("complete Python bool olmali")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source dolu metin olmali")
        if any(not isinstance(item, KapDisclosureEnvelope) for item in self.disclosures):
            raise TypeError("disclosures yalniz KapDisclosureEnvelope icermeli")
        if any(not isinstance(item, KapQuarantinedItem) for item in self.quarantined_items):
            raise TypeError("quarantined_items yalniz KapQuarantinedItem icermeli")
        if self.complete == bool(self.quarantined_items):
            raise ValueError("complete ve quarantined_items birbiriyle tutarsiz")


@dataclass(frozen=True)
class KapApiProbeReport:
    endpoint_url: str
    method: str
    items_seen: int
    items_validated: int
    next_cursor_present: bool
    checked_at: datetime
    first_disclosure_id: Optional[str]
    last_disclosure_id: Optional[str]


@dataclass(frozen=True)
class KapContractSampleCapture:
    """One raw official product page captured for offline contract onboarding.

    The payload is intentionally kept byte-for-byte equivalent at the JSON
    value level. Authentication headers and API keys are never included.
    """

    payload: Any
    payload_sha256: str
    endpoint_url: str
    method: str
    source_name: str
    start_at: datetime
    end_at: datetime
    captured_at: datetime
    items_seen: int
    items_validated: int
    next_cursor_present: bool

    def __post_init__(self) -> None:
        for name in ("start_at", "end_at", "captured_at"):
            _require_aware_datetime(name, getattr(self, name))
        if self.end_at < self.start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            raise ValueError("endpoint_url dolu metin olmali")
        if self.method not in {"GET", "POST"}:
            raise ValueError("method GET veya POST olmali")
        if not isinstance(self.source_name, str) or not self.source_name.strip():
            raise ValueError("source_name dolu metin olmali")
        for name in ("items_seen", "items_validated"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} negatif olmayan Python int olmali")
        if self.items_validated > self.items_seen:
            raise ValueError("items_validated items_seen degerini asamaz")
        if not isinstance(self.next_cursor_present, bool):
            raise ValueError("next_cursor_present Python bool olmali")
        if not isinstance(self.payload_sha256, str) or not __import__("re").fullmatch(
            r"[0-9a-f]{64}", self.payload_sha256
        ):
            raise ValueError("payload_sha256 64 karakter kucuk harf hex olmali")
        _, digest = _canonical_json(self.payload)
        if digest != self.payload_sha256:
            raise ValueError("payload_sha256 payload ile uyusmuyor")


@dataclass(frozen=True)
class MkkKapApiConfig:
    base_url: str
    api_key_header: str
    path: str
    method: str
    items_path: str
    fields: Mapping[str, str]
    start_param: str
    end_param: str
    cursor_param: Optional[str] = None
    next_cursor_path: Optional[str] = None
    page_size_param: Optional[str] = None
    page_size: Optional[int] = None
    static_params: Mapping[str, Any] | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 3
    max_future_skew_seconds: int = 300
    min_request_interval_seconds: float = 0.0
    max_retry_after_seconds: float = 60.0
    source_name: str = "MKK_KAP_API"
    max_response_bytes: int = 25_000_000
    max_item_payload_bytes: int = 5_000_000

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MkkKapApiConfig":
        required = (
            "base_url", "api_key_header", "path", "method", "items_path",
            "fields", "start_param", "end_param",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise KapApiConfigError(f"MKK KAP API config eksik alanlar: {missing}")
        for key in ("base_url", "api_key_header", "path", "items_path", "start_param", "end_param"):
            if not isinstance(data[key], str) or not data[key].strip():
                raise KapApiConfigError(f"{key} dolu metin olmali")
        parsed_url = urlparse(data["base_url"].strip())
        if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
            raise KapApiConfigError("base_url gecerli HTTP(S) URL olmali")
        if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise KapApiConfigError("base_url kullanici bilgisi, query veya fragment iceremez")
        if not __import__("re").fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", data["api_key_header"].strip()):
            raise KapApiConfigError("api_key_header gecersiz HTTP header adi")
        if data["api_key_header"].strip().lower() in {
            "accept", "content-length", "content-type", "host", "user-agent"
        }:
            raise KapApiConfigError("api_key_header istemci tarafindan yonetilen header ile cakismamali")
        if "?" in data["path"] or "#" in data["path"]:
            raise KapApiConfigError("path query veya fragment iceremez")
        fields = data["fields"]
        if not isinstance(fields, Mapping):
            raise KapApiConfigError("fields bir nesne olmali")
        for key, path_value in fields.items():
            if not isinstance(key, str) or not isinstance(path_value, str) or not path_value.strip():
                raise KapApiConfigError("fields anahtar ve yolları dolu metin olmali")
        for key in ("disclosure_id", "published_at"):
            if not isinstance(fields.get(key), str) or not fields[key].strip():
                raise KapApiConfigError(f"fields.{key} zorunlu")
        static_params = data.get("static_params", {})
        if static_params is None:
            static_params = {}
        if not isinstance(static_params, Mapping):
            raise KapApiConfigError("static_params bir nesne olmali")
        try:
            _validate_json_tree(static_params)
        except KapApiProtocolError as exc:
            raise KapApiConfigError(f"static_params gecersiz JSON: {exc}") from exc
        method = str(data["method"]).upper()
        if method not in {"GET", "POST"}:
            raise KapApiConfigError("method GET veya POST olmali")
        page_size = data.get("page_size")
        if page_size is not None:
            if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
                raise KapApiConfigError("page_size pozitif Python int olmali")
            if page_size > MAX_API_PAGE_SIZE:
                raise KapApiConfigError("page_size guvenli API sinirini asiyor")
        max_retries = data.get("max_retries", 3)
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise KapApiConfigError("max_retries negatif olmayan Python int olmali")
        if max_retries > MAX_API_RETRIES:
            raise KapApiConfigError("max_retries guvenli siniri asiyor")
        max_future_skew = data.get("max_future_skew_seconds", 300)
        if isinstance(max_future_skew, bool) or not isinstance(max_future_skew, int) or max_future_skew < 0:
            raise KapApiConfigError("max_future_skew_seconds negatif olmayan Python int olmali")
        if max_future_skew > MAX_FUTURE_SKEW_SECONDS:
            raise KapApiConfigError("max_future_skew_seconds guvenli siniri asiyor")
        timeout_raw = data.get("timeout_seconds", 30.0)
        if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, (int, float)):
            raise KapApiConfigError("timeout_seconds sonlu sayi olmali")
        timeout_seconds = float(timeout_raw)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise KapApiConfigError("timeout_seconds pozitif sonlu olmali")
        if timeout_seconds > MAX_API_TIMEOUT_SECONDS:
            raise KapApiConfigError("timeout_seconds guvenli siniri asiyor")
        min_interval_raw = data.get("min_request_interval_seconds", 0.0)
        if isinstance(min_interval_raw, bool) or not isinstance(min_interval_raw, (int, float)):
            raise KapApiConfigError("min_request_interval_seconds sonlu sayi olmali")
        min_request_interval_seconds = float(min_interval_raw)
        if not math.isfinite(min_request_interval_seconds) or min_request_interval_seconds < 0:
            raise KapApiConfigError("min_request_interval_seconds negatif olmayan sonlu sayi olmali")
        if min_request_interval_seconds > MAX_MIN_REQUEST_INTERVAL_SECONDS:
            raise KapApiConfigError("min_request_interval_seconds guvenli siniri asiyor")
        max_retry_after_raw = data.get("max_retry_after_seconds", 60.0)
        if isinstance(max_retry_after_raw, bool) or not isinstance(max_retry_after_raw, (int, float)):
            raise KapApiConfigError("max_retry_after_seconds sonlu sayi olmali")
        max_retry_after_seconds = float(max_retry_after_raw)
        if not math.isfinite(max_retry_after_seconds) or max_retry_after_seconds < 0:
            raise KapApiConfigError("max_retry_after_seconds negatif olmayan sonlu sayi olmali")
        if max_retry_after_seconds > MAX_RETRY_AFTER_SECONDS:
            raise KapApiConfigError("max_retry_after_seconds guvenli siniri asiyor")
        cursor_param = data.get("cursor_param")
        next_cursor_path = data.get("next_cursor_path")
        for key, value in (
            ("cursor_param", cursor_param),
            ("next_cursor_path", next_cursor_path),
            ("page_size_param", data.get("page_size_param")),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise KapApiConfigError(f"{key} None veya dolu metin olmali")
        if bool(cursor_param) != bool(next_cursor_path):
            raise KapApiConfigError("cursor_param ve next_cursor_path birlikte tanimlanmali")
        page_size_param = data.get("page_size_param")
        if bool(page_size_param) != (page_size is not None):
            raise KapApiConfigError("page_size ve page_size_param birlikte tanimlanmali")
        dynamic_names = [str(data["start_param"]), str(data["end_param"])]
        if cursor_param:
            dynamic_names.append(cursor_param)
        if page_size_param:
            dynamic_names.append(page_size_param)
        if len(set(dynamic_names)) != len(dynamic_names):
            raise KapApiConfigError("dinamik istek parametre adlari benzersiz olmali")
        collisions = set(static_params) & set(dynamic_names)
        if collisions:
            raise KapApiConfigError(
                f"static_params dinamik parametrelerle cakismamali: {sorted(collisions)}"
            )
        source_name = data.get("source_name", "MKK_KAP_API")
        if not isinstance(source_name, str) or not source_name.strip():
            raise KapApiConfigError("source_name dolu metin olmali")
        max_response_bytes = data.get("max_response_bytes", 25_000_000)
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
            or max_response_bytes > MAX_RESPONSE_BYTES_LIMIT
        ):
            raise KapApiConfigError("max_response_bytes guvenli aralikta pozitif Python int olmali")
        max_item_payload_bytes = data.get("max_item_payload_bytes", 5_000_000)
        if (
            isinstance(max_item_payload_bytes, bool)
            or not isinstance(max_item_payload_bytes, int)
            or max_item_payload_bytes <= 0
            or max_item_payload_bytes > MAX_ITEM_PAYLOAD_BYTES_LIMIT
        ):
            raise KapApiConfigError("max_item_payload_bytes guvenli aralikta pozitif Python int olmali")
        if max_item_payload_bytes > max_response_bytes:
            raise KapApiConfigError("max_item_payload_bytes max_response_bytes degerini asamaz")
        return cls(
            base_url=str(data["base_url"]).rstrip("/"),
            api_key_header=str(data["api_key_header"]),
            path="/" + str(data["path"]).lstrip("/"),
            method=method,
            items_path=str(data["items_path"]),
            fields=dict(fields),
            start_param=str(data["start_param"]),
            end_param=str(data["end_param"]),
            cursor_param=None if cursor_param is None else str(cursor_param),
            next_cursor_path=None if next_cursor_path is None else str(next_cursor_path),
            page_size_param=None if page_size_param is None else str(page_size_param),
            page_size=page_size,
            static_params=dict(static_params),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_future_skew_seconds=max_future_skew,
            min_request_interval_seconds=min_request_interval_seconds,
            max_retry_after_seconds=max_retry_after_seconds,
            source_name=source_name.strip(),
            max_response_bytes=max_response_bytes,
            max_item_payload_bytes=max_item_payload_bytes,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "MkkKapApiConfig":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise KapApiConfigError("API config JSON nesne olmali")
        return cls.from_dict(data)

    def validate_live_ready(self) -> None:
        """Reject example placeholders and unsafe transport before a live provider call."""
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise KapApiConfigError("canli MKK KAP endpoint HTTPS kullanmali")
        try:
            import ipaddress
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise KapApiConfigError("canli MKK KAP endpoint IP literal kullanmamali")
        suspicious_hosts = (
            host.endswith(".invalid"),
            host.endswith(".example"),
            host.endswith(".test"),
            host in {"example.com", "localhost"},
        )
        placeholder_text = " ".join(
            (self.base_url, self.api_key_header, self.path)
        ).upper()
        if any(suspicious_hosts) or any(
            marker in placeholder_text
            for marker in ("RESMI_DOKUMAN", "API-PORTAL-URUN", "PLACEHOLDER")
        ):
            raise KapApiConfigError(
                "MKK KAP API config ornek/placeholder degerler iceriyor; "
                "resmi urun dokumanindaki endpoint ve header ile doldurulmali"
            )


class MkkKapApiClient:
    """Configurable client for the official MKK API Portal KAP products.

    MKK publishes multiple KAP API products and requires an approved account,
    application/API key and product registration. Endpoint paths, auth header and
    response field mappings are therefore configuration, not guessed constants.
    """

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        config: MkkKapApiConfig,
        api_key: str,
        *,
        session: Optional[requests.Session] = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(config, MkkKapApiConfig):
            raise TypeError("config MkkKapApiConfig olmali")
        config = MkkKapApiConfig.from_dict(asdict(config))
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("MKK API key bos olamaz")
        if "\n" in api_key or "\r" in api_key:
            raise ValueError("MKK API key satir sonu iceremez")
        self.config = config
        self.api_key = api_key.strip()
        self.session = session or requests.Session()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper
        self.monotonic = monotonic
        self._last_request_started: float | None = None

    def _pace_request(self) -> None:
        interval = self.config.min_request_interval_seconds
        if interval <= 0:
            self._last_request_started = self.monotonic()
            return
        now = self.monotonic()
        if self._last_request_started is not None:
            remaining = interval - (now - self._last_request_started)
            if remaining > 0:
                self.sleeper(remaining)
                now = self.monotonic()
        self._last_request_started = now

    def _retry_delay(self, response: Any, attempt: int) -> float:
        fallback = min(8.0, 0.5 * (2**attempt))
        headers = getattr(response, "headers", None)
        if not isinstance(headers, Mapping):
            return fallback
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is None:
            return fallback
        try:
            delay = float(str(raw).strip())
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(str(raw).strip())
                if retry_at.tzinfo is None or retry_at.utcoffset() is None:
                    return fallback
                now = _require_aware_datetime("clock", self.clock())
                delay = max(0.0, (retry_at - now).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return fallback
        if not math.isfinite(delay) or delay < 0:
            return fallback
        return min(delay, self.config.max_retry_after_seconds)

    def _request_json(self, request_data: Mapping[str, Any]) -> Any:
        url = self.config.base_url + self.config.path
        headers = {
            self.config.api_key_header: self.api_key,
            "Accept": "application/json",
            "User-Agent": "total-rasyo-hesaplayici/kap-sync",
        }
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                self._pace_request()
                kwargs: dict[str, Any] = {
                    "method": self.config.method,
                    "url": url,
                    "headers": headers,
                    "timeout": self.config.timeout_seconds,
                }
                if self.config.method == "GET":
                    kwargs["params"] = dict(request_data)
                else:
                    kwargs["json"] = dict(request_data)
                response = self.session.request(**kwargs)
                if response.status_code in self.RETRY_STATUS and attempt < self.config.max_retries:
                    self.sleeper(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                response_headers = getattr(response, "headers", None)
                if isinstance(response_headers, Mapping):
                    raw_length = response_headers.get("Content-Length") or response_headers.get("content-length")
                    if raw_length is not None:
                        try:
                            content_length = int(str(raw_length).strip())
                        except ValueError as exc:
                            raise KapApiProtocolError("Content-Length gecersiz tam sayi") from exc
                        if content_length < 0 or content_length > self.config.max_response_bytes:
                            raise KapApiProtocolError("MKK KAP API yaniti byte sinirini asiyor")
                raw_content = getattr(response, "content", None)
                if isinstance(raw_content, (bytes, bytearray)) and len(raw_content) > self.config.max_response_bytes:
                    raise KapApiProtocolError("MKK KAP API yaniti byte sinirini asiyor")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise KapApiProtocolError("MKK KAP API JSON olmayan yanit dondurdu") from exc
                _validate_json_tree(payload)
                return payload
            except (requests.RequestException, KapApiProtocolError) as exc:
                last_error = exc
                if isinstance(exc, KapApiProtocolError):
                    raise
                if attempt >= self.config.max_retries:
                    break
                self.sleeper(min(8.0, 0.5 * (2**attempt)))
        raise KapApiTransportError(
            f"MKK KAP API {self.config.max_retries + 1} denemede basarisiz"
        ) from last_error

    def _normalize_item(self, item: Any, fetched_at: datetime) -> KapDisclosureEnvelope:
        if not isinstance(item, Mapping):
            raise KapApiProtocolError("bildirim kaydi JSON nesnesi olmali")
        fields = self.config.fields
        disclosure_id = _scalar_id_text(
            "disclosure_id", _get_path(item, fields["disclosure_id"], required=True)
        )
        published_at = _parse_aware_datetime(
            "published_at", _get_path(item, fields["published_at"], required=True)
        )
        max_future = fetched_at + timedelta(seconds=self.config.max_future_skew_seconds)
        if published_at > max_future:
            raise KapApiProtocolError(
                f"bildirim gelecekte gorunuyor: {published_at.isoformat()} > {max_future.isoformat()}"
            )

        def optional_text(name: str, *, uppercase: bool = False) -> Optional[str]:
            path = fields.get(name)
            raw = _get_path(item, path) if path else None
            if name == "company_id" and raw is not None:
                return _scalar_id_text(name, raw)
            return _optional_string(name, raw, uppercase=uppercase)

        payload_text, payload_sha256 = _canonical_payload(item)
        if len(payload_text.encode("utf-8")) > self.config.max_item_payload_bytes:
            raise KapApiProtocolError("bildirim payload'i byte sinirini asiyor")
        return KapDisclosureEnvelope(
            disclosure_id=disclosure_id,
            published_at=published_at,
            ticker=optional_text("ticker", uppercase=True),
            company_id=optional_text("company_id"),
            notification_type=optional_text("notification_type"),
            subject=optional_text("subject"),
            source_url=optional_text("source_url"),
            payload=dict(item),
            payload_sha256=payload_sha256,
            fetched_at=fetched_at,
            source=self.config.source_name,
        )

    def capture_contract_sample(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        cursor: Optional[str] = None,
        validate_items_limit: int = 100,
    ) -> KapContractSampleCapture:
        """Capture exactly one official response page for offline validation.

        This method does not persist anything and never returns request headers.
        It validates the configured item and cursor paths before handing the raw
        JSON to the caller.
        """

        self.config.validate_live_ready()
        start_at = _require_aware_datetime("start_at", start_at)
        end_at = _require_aware_datetime("end_at", end_at)
        if end_at < start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
            raise ValueError("cursor None veya dolu metin olmali")
        if (
            isinstance(validate_items_limit, bool)
            or not isinstance(validate_items_limit, int)
            or validate_items_limit <= 0
            or validate_items_limit > MAX_API_PAGE_SIZE
        ):
            raise ValueError("validate_items_limit guvenli aralikta pozitif Python int olmali")

        request_data = dict(self.config.static_params)
        request_data[self.config.start_param] = start_at.isoformat()
        request_data[self.config.end_param] = end_at.isoformat()
        if self.config.cursor_param and cursor:
            request_data[self.config.cursor_param] = cursor.strip()
        if self.config.page_size_param and self.config.page_size:
            request_data[self.config.page_size_param] = self.config.page_size

        payload = self._request_json(request_data)
        items = _get_path(payload, self.config.items_path, required=True)
        if not isinstance(items, list):
            raise KapApiProtocolError("items_path bir listeye cikmali")
        if not items:
            raise KapApiProtocolError(
                "contract sample sayfasi bos; alan sozlesmesi dogrulanamaz"
            )
        if self.config.page_size is not None and len(items) > self.config.page_size:
            raise KapApiProtocolError("API sayfasi config page_size sinirindan fazla item dondurdu")
        captured_at = _require_aware_datetime("clock", self.clock())
        for item in items[:validate_items_limit]:
            self._normalize_item(item, captured_at)

        next_raw = (
            _get_path(payload, self.config.next_cursor_path)
            if self.config.next_cursor_path
            else None
        )
        next_cursor_present = next_raw not in (None, "")
        if next_cursor_present:
            _scalar_id_text("next_cursor", next_raw)
        _, digest = _canonical_json(payload)
        return KapContractSampleCapture(
            payload=payload,
            payload_sha256=digest,
            endpoint_url=self.config.base_url + self.config.path,
            method=self.config.method,
            source_name=self.config.source_name,
            start_at=start_at,
            end_at=end_at,
            captured_at=captured_at,
            items_seen=len(items),
            items_validated=min(len(items), validate_items_limit),
            next_cursor_present=next_cursor_present,
        )

    def fetch_disclosures(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        initial_cursor: Optional[str] = None,
        max_pages: int = 100,
        quarantine_invalid_items: bool = False,
    ) -> KapFetchResult:
        start_at = _require_aware_datetime("start_at", start_at)
        end_at = _require_aware_datetime("end_at", end_at)
        if end_at < start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages pozitif Python int olmali")
        if max_pages > MAX_API_PAGES:
            raise ValueError("max_pages guvenli siniri asiyor")
        if initial_cursor is not None and (
            not isinstance(initial_cursor, str) or not initial_cursor.strip()
        ):
            raise ValueError("initial_cursor None veya dolu metin olmali")
        if not isinstance(quarantine_invalid_items, bool):
            raise ValueError("quarantine_invalid_items Python bool olmali")

        cursor = None if initial_cursor is None else initial_cursor.strip()
        seen_cursors: set[str] = set()
        by_id: dict[str, KapDisclosureEnvelope] = {}
        quarantined: list[KapQuarantinedItem] = []
        pages = 0

        while pages < max_pages:
            request_data = dict(self.config.static_params)
            request_data[self.config.start_param] = start_at.isoformat()
            request_data[self.config.end_param] = end_at.isoformat()
            if self.config.cursor_param and cursor:
                request_data[self.config.cursor_param] = cursor
            if self.config.page_size_param and self.config.page_size:
                request_data[self.config.page_size_param] = self.config.page_size

            payload = self._request_json(request_data)
            items = _get_path(payload, self.config.items_path, required=True)
            if not isinstance(items, list):
                raise KapApiProtocolError("items_path bir listeye cikmali")
            if self.config.page_size is not None and len(items) > self.config.page_size:
                raise KapApiProtocolError("API sayfasi config page_size sinirindan fazla item dondurdu")
            fetched_at = _require_aware_datetime("clock", self.clock())
            for item_index, item in enumerate(items):
                try:
                    envelope = self._normalize_item(item, fetched_at)
                except KapApiProtocolError as exc:
                    if not quarantine_invalid_items:
                        raise
                    _, payload_sha256 = _canonical_json(item)
                    quarantined.append(KapQuarantinedItem(
                        page_number=pages + 1,
                        item_index=item_index,
                        cursor_value=cursor,
                        reason=str(exc),
                        payload=item,
                        payload_sha256=payload_sha256,
                        fetched_at=fetched_at,
                        source=self.config.source_name,
                    ))
                    continue
                previous = by_id.get(envelope.disclosure_id)
                if previous is not None and previous.payload_sha256 != envelope.payload_sha256:
                    reason = f"ayni disclosure_id farkli payload ile geldi: {envelope.disclosure_id}"
                    if not quarantine_invalid_items:
                        raise KapApiProtocolError(reason)
                    _, payload_sha256 = _canonical_json(item)
                    quarantined.append(KapQuarantinedItem(
                        page_number=pages + 1,
                        item_index=item_index,
                        cursor_value=cursor,
                        reason=reason,
                        payload=item,
                        payload_sha256=payload_sha256,
                        fetched_at=fetched_at,
                        source=self.config.source_name,
                    ))
                    continue
                if previous is None:
                    by_id[envelope.disclosure_id] = envelope
            pages += 1

            next_cursor_raw = (
                _get_path(payload, self.config.next_cursor_path)
                if self.config.next_cursor_path
                else None
            )
            if next_cursor_raw is None or next_cursor_raw == "":
                next_cursor = None
            else:
                next_cursor = _scalar_id_text("next_cursor", next_cursor_raw)
            if not next_cursor:
                cursor = None
                break
            if next_cursor in seen_cursors or next_cursor == cursor:
                raise KapApiProtocolError(f"pagination cursor tekrarlandi: {next_cursor}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            if cursor:
                raise KapApiProtocolError(
                    f"max_pages={max_pages} doldu; cursor tamamlanmadi: {cursor}"
                )

        disclosures = tuple(
            sorted(by_id.values(), key=lambda x: (x.published_at, x.disclosure_id))
        )
        completed_at = _require_aware_datetime("clock", self.clock())
        return KapFetchResult(
            disclosures=disclosures,
            next_cursor=cursor,
            pages_fetched=pages,
            start_at=start_at,
            end_at=end_at,
            completed_at=completed_at,
            quarantined_items=tuple(quarantined),
            complete=not quarantined,
            source=self.config.source_name,
        )

    def probe(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        validate_items_limit: int = 5,
    ) -> KapApiProbeReport:
        """Validate auth, endpoint shape and a bounded sample without checkpointing."""
        self.config.validate_live_ready()
        start_at = _require_aware_datetime("start_at", start_at)
        end_at = _require_aware_datetime("end_at", end_at)
        if end_at < start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if (
            isinstance(validate_items_limit, bool)
            or not isinstance(validate_items_limit, int)
            or validate_items_limit < 0
            or validate_items_limit > 1000
        ):
            raise ValueError("validate_items_limit 0..1000 arasi Python int olmali")
        request_data = dict(self.config.static_params)
        request_data[self.config.start_param] = start_at.isoformat()
        request_data[self.config.end_param] = end_at.isoformat()
        if self.config.page_size_param and self.config.page_size:
            request_data[self.config.page_size_param] = self.config.page_size
        payload = self._request_json(request_data)
        items = _get_path(payload, self.config.items_path, required=True)
        if not isinstance(items, list):
            raise KapApiProtocolError("items_path bir listeye cikmali")
        if self.config.page_size is not None and len(items) > self.config.page_size:
            raise KapApiProtocolError("API sayfasi config page_size sinirindan fazla item dondurdu")
        checked_at = _require_aware_datetime("clock", self.clock())
        validated: list[KapDisclosureEnvelope] = []
        for item in items[:validate_items_limit]:
            validated.append(self._normalize_item(item, checked_at))
        next_raw = (
            _get_path(payload, self.config.next_cursor_path)
            if self.config.next_cursor_path
            else None
        )
        if next_raw in (None, ""):
            next_cursor_present = False
        else:
            _scalar_id_text("next_cursor", next_raw)
            next_cursor_present = True
        return KapApiProbeReport(
            endpoint_url=self.config.base_url + self.config.path,
            method=self.config.method,
            items_seen=len(items),
            items_validated=len(validated),
            next_cursor_present=next_cursor_present,
            checked_at=checked_at,
            first_disclosure_id=validated[0].disclosure_id if validated else None,
            last_disclosure_id=validated[-1].disclosure_id if validated else None,
        )
