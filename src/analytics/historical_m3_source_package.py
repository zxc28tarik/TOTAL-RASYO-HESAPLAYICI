from __future__ import annotations

"""Fail-closed contract for historical M3 sector routes and index closes.

The validator deliberately does not download data.  A package can become CLOSED
only when every raw source used by the canonical rows is committed, hash locked,
and independently reproducible by committed transformation code and tests.
"""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import shlex
from typing import Iterable
from urllib.parse import urlparse

import pandas as pd


CONTRACT = "HISTORICAL_M3_SOURCE_PACKAGE_V1"
ROUTE_SCHEMA = (
    "ticker",
    "valid_from",
    "valid_to",
    "sector_index_code",
    "source_id",
)
INDEX_CLOSE_SCHEMA = ("index_code", "trade_date", "close", "source_id")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^X[A-Z0-9]{2,15}$")


class HistoricalM3SourcePackageError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalM3SourcePackageReport:
    contract: str
    declared_status: str
    effective_status: str
    membership_rows: int
    signal_months: int
    route_rows: int
    index_close_rows: int
    required_index_codes: tuple[str, ...]
    raw_source_count: int
    blockers: tuple[str, ...]

    @property
    def closed(self) -> bool:
        return self.effective_status == "CLOSED"


def _fail(message: str) -> None:
    raise HistoricalM3SourcePackageError(message)


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        _fail(f"{field} object olmali")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        _fail(f"{field} list olmali")
    return value


def _exact_keys(value: dict[str, object], expected: Iterable[str], field: str) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        _fail(
            f"{field} alanlari tam eslesmeli; eksik={sorted(expected_set-actual)} "
            f"fazla={sorted(actual-expected_set)}"
        )


def _text(value: object, field: str, *, upper: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} dolu metin olmali")
    out = value.strip()
    return out.upper() if upper else out


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{field} pozitif int olmali")
    return value


def _sha(value: object, field: str) -> str:
    out = _text(value, field)
    if not SHA256_RE.fullmatch(out):
        _fail(f"{field} kucuk harf 64 haneli SHA256 olmali")
    return out


def _day(value: object, field: str) -> pd.Timestamp:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(f"{field} YYYY-MM-DD takvim tarihi olmali")
    try:
        out = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalM3SourcePackageError(f"{field} gecerli tarih olmali") from exc
    if pd.isna(out) or out.tz is not None or out != out.normalize():
        _fail(f"{field} YYYY-MM-DD takvim tarihi olmali")
    return out


def _aware_time(value: object, field: str) -> datetime:
    try:
        out = datetime.fromisoformat(_text(value, field).replace("Z", "+00:00"))
    except Exception as exc:
        raise HistoricalM3SourcePackageError(
            f"{field} timezone-aware ISO-8601 olmali"
        ) from exc
    if out.tzinfo is None or out.utcoffset() is None:
        _fail(f"{field} timezone-aware ISO-8601 olmali")
    return out


def _public_https_url(value: object, field: str) -> str:
    out = _text(value, field)
    parsed = urlparse(out)
    host = (parsed.hostname or "").lower()
    private_host = host == "localhost" or host.endswith(".local")
    try:
        private_host = private_host or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    if (
        parsed.scheme != "https"
        or not host
        or private_host
        or parsed.username is not None
        or parsed.password is not None
    ):
        _fail(f"{field} halka acik HTTPS URL olmali")
    return out


def _repo_file(repo_root: Path, value: object, field: str, *, required: bool = True) -> Path:
    raw = _text(value, field)
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        _fail(f"{field} repo-kokune gore guvenli bagil yol olmali")
    root = repo_root.resolve()
    candidate = root / relative
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        _fail(f"{field} repo disina cikamaz")
    if candidate.is_symlink():
        _fail(f"{field} symlink olamaz: {raw}")
    if required and not candidate.is_file():
        _fail(f"{field} commit edilmis normal dosya olmali: {raw}")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(repo_root: Path, descriptor: dict[str, object], field: str) -> Path:
    _exact_keys(descriptor, ("path", "sha256", "row_count", "schema"), field)
    path = _repo_file(repo_root, descriptor["path"], f"{field}.path")
    expected = _sha(descriptor["sha256"], f"{field}.sha256")
    if _file_sha256(path) != expected:
        _fail(f"{field}.sha256 dosya icerigiyle eslesmiyor")
    _positive_int(descriptor["row_count"], f"{field}.row_count")
    schema = _list(descriptor["schema"], f"{field}.schema")
    if any(not isinstance(item, str) for item in schema):
        _fail(f"{field}.schema yalniz metin kolonlar icermeli")
    return path


def _read_csv(path: Path, schema: tuple[str, ...], field: str) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise HistoricalM3SourcePackageError(f"{field} okunamadi") from exc
    if tuple(frame.columns) != schema:
        _fail(f"{field} kolon sirasi {list(schema)} olmali")
    if frame.empty:
        _fail(f"{field} bos olamaz")
    return frame


def _normalize_membership(
    frame: pd.DataFrame,
    *,
    expected_signal_months: int,
    expected_members_per_signal: int,
    required_signal_start_month: str,
    required_signal_end_month: str,
) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    if not isinstance(frame, pd.DataFrame):
        _fail("historical_membership DataFrame olmali")
    missing = {"signal_date", "ticker"} - set(frame.columns)
    if missing:
        _fail(f"historical_membership missing columns: {sorted(missing)}")
    out = frame.loc[:, ["signal_date", "ticker"]].copy()
    try:
        out["signal_date"] = pd.to_datetime(out["signal_date"], errors="raise").dt.normalize()
    except Exception as exc:
        raise HistoricalM3SourcePackageError(
            "historical_membership.signal_date gecersiz"
        ) from exc
    out["ticker"] = out["ticker"].map(
        lambda value: _text(value, "historical_membership.ticker", upper=True)
    )
    if out.duplicated(["signal_date", "ticker"]).any():
        _fail("historical_membership duplicate signal_date+ticker iceriyor")
    counts = out.groupby("signal_date")["ticker"].size().sort_index()
    if len(counts) != expected_signal_months:
        _fail(
            f"historical_membership signal ayi {len(counts)}, beklenen {expected_signal_months}"
        )
    if not counts.eq(expected_members_per_signal).all():
        _fail(
            "historical_membership her signal tarihinde beklenen uye sayisini icermeli"
        )
    if len(out) != expected_signal_months * expected_members_per_signal:
        _fail("historical_membership toplam satir kapsami bozuk")
    months = counts.index.to_period("M").astype(str).tolist()
    expected_months = pd.period_range(
        required_signal_start_month, required_signal_end_month, freq="M"
    ).astype(str).tolist()
    if len(expected_months) != expected_signal_months or months != expected_months:
        _fail("historical_membership signal aylari zorunlu kesintisiz pencereyle eslesmiyor")
    return out.sort_values(["signal_date", "ticker"]).reset_index(drop=True), list(counts.index)


def _normalize_calendar(frame: pd.DataFrame) -> list[pd.Timestamp]:
    if not isinstance(frame, pd.DataFrame) or "trade_date" not in frame.columns:
        _fail("trading_calendar trade_date kolonlu DataFrame olmali")
    try:
        days = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    except Exception as exc:
        raise HistoricalM3SourcePackageError("trading_calendar.trade_date gecersiz") from exc
    if days.empty or days.duplicated().any():
        _fail("trading_calendar bos veya duplicate trade_date iceriyor")
    if not days.is_monotonic_increasing:
        _fail("trading_calendar trade_date artan sirada olmali")
    return days.tolist()


def _normalize_routes(frame: pd.DataFrame, market_index: str) -> pd.DataFrame:
    out = frame.copy()
    out["ticker"] = out["ticker"].map(lambda value: _text(value, "routes.ticker", upper=True))
    out["sector_index_code"] = out["sector_index_code"].map(
        lambda value: _text(value, "routes.sector_index_code", upper=True)
    )
    out["source_id"] = out["source_id"].map(lambda value: _text(value, "routes.source_id"))
    if not out["sector_index_code"].map(lambda value: bool(CODE_RE.fullmatch(value))).all():
        _fail("routes.sector_index_code kanonik X... endeks kodu olmali")
    if out["sector_index_code"].eq(market_index).any():
        _fail("routes XU100 piyasa endeksini sektor fallback olarak kullanamaz")
    try:
        out["valid_from"] = pd.to_datetime(out["valid_from"], errors="raise").dt.normalize()
        out["valid_to"] = pd.to_datetime(
            out["valid_to"].replace("", pd.NA), errors="coerce"
        ).dt.normalize()
    except Exception as exc:
        raise HistoricalM3SourcePackageError("routes valid_from/valid_to gecersiz") from exc
    if out["valid_from"].isna().any():
        _fail("routes.valid_from bos olamaz")
    raw_to = frame["valid_to"].astype(str).str.strip()
    if (raw_to.ne("") & out["valid_to"].isna()).any():
        _fail("routes.valid_to gecerli tarih veya bos olmali")
    if (out["valid_to"].notna() & (out["valid_to"] <= out["valid_from"])).any():
        _fail("routes yarim-acik aralikta valid_to valid_from'dan sonra olmali")
    if out.duplicated(list(ROUTE_SCHEMA)).any():
        _fail("routes duplicate satir iceriyor")
    for ticker, group in out.sort_values(["ticker", "valid_from"]).groupby("ticker"):
        previous_to: pd.Timestamp | None = None
        for position, row in enumerate(group.itertuples(index=False)):
            if position > 0 and previous_to is None:
                _fail(f"routes {ticker} acik-uclu araliktan sonra baska aralik iceriyor")
            if previous_to is not None and row.valid_from < previous_to:
                _fail(f"routes {ticker} cakisan tarih araligi iceriyor")
            previous_to = row.valid_to if not pd.isna(row.valid_to) else None
    return out


def _route_for(routes: pd.DataFrame, ticker: str, day: pd.Timestamp) -> pd.Series:
    matches = routes.loc[
        (routes["ticker"] == ticker)
        & (routes["valid_from"] <= day)
        & (routes["valid_to"].isna() | (day < routes["valid_to"]))
    ]
    if len(matches) != 1:
        _fail(f"route coverage {day.date()} {ticker} icin tam bir satir olmali")
    return matches.iloc[0]


def _normalize_index_closes(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["index_code"] = out["index_code"].map(
        lambda value: _text(value, "index_closes.index_code", upper=True)
    )
    out["source_id"] = out["source_id"].map(
        lambda value: _text(value, "index_closes.source_id")
    )
    if not out["index_code"].map(lambda value: bool(CODE_RE.fullmatch(value))).all():
        _fail("index_closes.index_code kanonik X... endeks kodu olmali")
    try:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.normalize()
    except Exception as exc:
        raise HistoricalM3SourcePackageError("index_closes.trade_date gecersiz") from exc
    if out.duplicated(["index_code", "trade_date"]).any():
        _fail("index_closes duplicate index_code+trade_date iceriyor")
    close = pd.to_numeric(out["close"], errors="coerce")
    if close.isna().any() or not close.map(lambda x: math.isfinite(float(x)) and float(x) > 0).all():
        _fail("index_closes.close pozitif sonlu olmali")
    out["close"] = close.astype(float)
    return out


def verify_historical_m3_source_package(
    *,
    manifest_path: str | Path,
    repo_root: str | Path,
    historical_membership: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    require_closed: bool = True,
    expected_signal_months: int = 60,
    expected_members_per_signal: int = 100,
    required_signal_start_month: str = "2021-08",
    required_signal_end_month: str = "2026-07",
) -> HistoricalM3SourcePackageReport:
    """Verify hashes, lineage, PIT route coverage, and daily M3 price coverage."""

    root = Path(repo_root)
    root_resolved = root.resolve()
    manifest_input = Path(manifest_path)
    if manifest_input.is_absolute():
        if manifest_input.is_symlink():
            _fail("manifest_path symlink olamaz")
        try:
            manifest_relative = manifest_input.resolve().relative_to(root_resolved)
        except ValueError:
            _fail("manifest_path repo disina cikamaz")
    else:
        manifest_relative = manifest_input
    manifest_file = _repo_file(root, str(manifest_relative), "manifest_path")
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HistoricalM3SourcePackageError("manifest okunamadi veya JSON gecersiz") from exc
    manifest = _mapping(payload, "manifest")
    _exact_keys(
        manifest,
        (
            "contract",
            "package_status",
            "assembled_at",
            "market_index",
            "coverage",
            "canonical_files",
            "raw_sources",
            "transformation",
            "sha256sums_path",
            "sha256sums_sha256",
        ),
        "manifest",
    )
    if manifest["contract"] != CONTRACT:
        _fail(f"manifest.contract {CONTRACT} olmali")
    declared_status = _text(manifest["package_status"], "manifest.package_status", upper=True)
    if declared_status not in {"OPEN", "CLOSED"}:
        _fail("manifest.package_status OPEN veya CLOSED olmali")
    assembled_at = _aware_time(manifest["assembled_at"], "manifest.assembled_at")
    market_index = _text(manifest["market_index"], "manifest.market_index", upper=True)
    if market_index != "XU100":
        _fail("manifest.market_index XU100 olmali")

    coverage = _mapping(manifest["coverage"], "manifest.coverage")
    _exact_keys(
        coverage,
        ("start_date", "end_date", "beta_lookback_trading_days", "alpha_window_trading_days"),
        "manifest.coverage",
    )
    coverage_start = _day(coverage["start_date"], "manifest.coverage.start_date")
    coverage_end = _day(coverage["end_date"], "manifest.coverage.end_date")
    if coverage_start > coverage_end:
        _fail("manifest.coverage tarih araligi ters")
    beta_lookback = _positive_int(
        coverage["beta_lookback_trading_days"], "manifest.coverage.beta_lookback_trading_days"
    )
    alpha_window = _positive_int(
        coverage["alpha_window_trading_days"], "manifest.coverage.alpha_window_trading_days"
    )
    if beta_lookback != 252 or alpha_window != 63:
        _fail("manifest.coverage M3 icin 252/63 trading-day pencerelerini kilitlemeli")

    canonical = _mapping(manifest["canonical_files"], "manifest.canonical_files")
    _exact_keys(canonical, ("sector_routes", "index_closes"), "manifest.canonical_files")
    route_descriptor = _mapping(canonical["sector_routes"], "canonical_files.sector_routes")
    close_descriptor = _mapping(canonical["index_closes"], "canonical_files.index_closes")
    route_path = _verify_file(root, route_descriptor, "canonical_files.sector_routes")
    close_path = _verify_file(root, close_descriptor, "canonical_files.index_closes")
    if tuple(route_descriptor["schema"]) != ROUTE_SCHEMA:
        _fail("canonical_files.sector_routes.schema contract ile eslesmiyor")
    if tuple(close_descriptor["schema"]) != INDEX_CLOSE_SCHEMA:
        _fail("canonical_files.index_closes.schema contract ile eslesmiyor")
    routes_raw = _read_csv(route_path, ROUTE_SCHEMA, "sector_routes")
    closes_raw = _read_csv(close_path, INDEX_CLOSE_SCHEMA, "index_closes")
    if len(routes_raw) != route_descriptor["row_count"]:
        _fail("sector_routes row_count manifest ile eslesmiyor")
    if len(closes_raw) != close_descriptor["row_count"]:
        _fail("index_closes row_count manifest ile eslesmiyor")

    raw_sources = _list(manifest["raw_sources"], "manifest.raw_sources")
    if not raw_sources:
        _fail("manifest.raw_sources bos olamaz")
    source_ids: set[str] = set()
    expected_sums: dict[str, str] = {
        str(route_descriptor["path"]): str(route_descriptor["sha256"]),
        str(close_descriptor["path"]): str(close_descriptor["sha256"]),
    }
    blockers: list[str] = []
    for index, raw_value in enumerate(raw_sources):
        field = f"manifest.raw_sources[{index}]"
        source = _mapping(raw_value, field)
        _exact_keys(
            source,
            (
                "source_id",
                "publisher",
                "source_url",
                "artifact_identity",
                "retrieved_at",
                "raw_path",
                "raw_sha256",
            ),
            field,
        )
        source_id = _text(source["source_id"], f"{field}.source_id")
        if source_id in source_ids:
            _fail("manifest.raw_sources duplicate source_id iceriyor")
        source_ids.add(source_id)
        _text(source["publisher"], f"{field}.publisher")
        _public_https_url(source["source_url"], f"{field}.source_url")
        _text(source["artifact_identity"], f"{field}.artifact_identity")
        if _aware_time(source["retrieved_at"], f"{field}.retrieved_at") > assembled_at:
            _fail(f"{field}.retrieved_at assembled_at sonrasinda olamaz")
        expected_raw_sha = _sha(source["raw_sha256"], f"{field}.raw_sha256")
        raw_path = _repo_file(
            root, source["raw_path"], f"{field}.raw_path", required=False
        )
        if not raw_path.is_file():
            if declared_status == "CLOSED":
                _fail(
                    f"{field}.raw_path commit edilmis normal dosya olmali: "
                    f"{source['raw_path']}"
                )
            blockers.append(f"RAW_SOURCE_NOT_COMMITTED:{source_id}")
            continue
        if _file_sha256(raw_path) != expected_raw_sha:
            _fail(f"{field}.raw_sha256 dosya icerigiyle eslesmiyor")
        expected_sums[str(source["raw_path"])] = expected_raw_sha

    transformation = _mapping(manifest["transformation"], "manifest.transformation")
    _exact_keys(
        transformation,
        (
            "entrypoint_path",
            "entrypoint_sha256",
            "determinism_test_path",
            "determinism_test_sha256",
            "reproduction_command",
        ),
        "manifest.transformation",
    )
    for path_key, sha_key in (
        ("entrypoint_path", "entrypoint_sha256"),
        ("determinism_test_path", "determinism_test_sha256"),
    ):
        path = _repo_file(root, transformation[path_key], f"manifest.transformation.{path_key}")
        expected = _sha(transformation[sha_key], f"manifest.transformation.{sha_key}")
        if _file_sha256(path) != expected:
            _fail(f"manifest.transformation.{sha_key} dosya icerigiyle eslesmiyor")
        expected_sums[str(transformation[path_key])] = expected
    command = _text(
        transformation["reproduction_command"], "manifest.transformation.reproduction_command"
    )
    try:
        command_tokens = shlex.split(command)
    except ValueError as exc:
        raise HistoricalM3SourcePackageError("reproduction_command gecersiz") from exc
    expected_test_path = str(transformation["determinism_test_path"])
    if (
        len(command_tokens) < 4
        or command_tokens[:3] not in (["python", "-m", "pytest"], ["python3", "-m", "pytest"])
        or expected_test_path not in command_tokens[3:]
        or any(token in {";", "&&", "||", "|"} for token in command_tokens)
    ):
        _fail("reproduction_command determinism testini dogrudan python -m pytest ile calistirmali")

    sums_path = _repo_file(root, manifest["sha256sums_path"], "manifest.sha256sums_path")
    sums_sha = _sha(manifest["sha256sums_sha256"], "manifest.sha256sums_sha256")
    if _file_sha256(sums_path) != sums_sha:
        _fail("manifest.sha256sums_sha256 dosya icerigiyle eslesmiyor")
    observed_sums: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_RE.fullmatch(parts[0]) or not parts[1]:
            _fail("SHA256SUMS her satirda '<sha256>  <repo-path>' olmali")
        if parts[1] in observed_sums:
            _fail("SHA256SUMS duplicate path iceriyor")
        observed_sums[parts[1]] = parts[0]
    if observed_sums != expected_sums:
        _fail("SHA256SUMS manifestteki tum ve yalniz kanit dosyalarini icermeli")

    routes = _normalize_routes(routes_raw, market_index)
    closes = _normalize_index_closes(closes_raw)
    used_sources = set(routes["source_id"]) | set(closes["source_id"])
    if used_sources != source_ids:
        _fail("canonical source_id kapsami raw_sources ile birebir eslesmiyor")

    membership, signals = _normalize_membership(
        historical_membership,
        expected_signal_months=expected_signal_months,
        expected_members_per_signal=expected_members_per_signal,
        required_signal_start_month=required_signal_start_month,
        required_signal_end_month=required_signal_end_month,
    )
    member_tickers = set(membership["ticker"])
    if set(routes["ticker"]) != member_tickers:
        _fail("routes ticker kapsami historical membership ticker birlesimiyle birebir olmali")
    calendar = _normalize_calendar(trading_calendar)
    calendar_set = set(calendar)
    calendar_position = {day: index for index, day in enumerate(calendar)}
    if any(signal not in calendar_set for signal in signals):
        _fail("historical_membership signal_date trading_calendar icinde olmali")
    if calendar[0] < coverage_start or calendar[-1] > coverage_end:
        _fail("trading_calendar manifest.coverage disina cikamaz")
    if any(day < coverage_start or day > coverage_end for day in closes["trade_date"]):
        _fail("index_closes manifest.coverage disina cikamaz")
    if not set(closes["trade_date"]).issubset(calendar_set):
        _fail("index_closes trading_calendar disi tarih iceriyor")

    route_by_member: dict[tuple[pd.Timestamp, str], str] = {}
    for row in membership.itertuples(index=False):
        route = _route_for(routes, row.ticker, row.signal_date)
        route_by_member[(row.signal_date, row.ticker)] = str(route["sector_index_code"])
    required_codes = {market_index, *route_by_member.values()}
    if set(closes["index_code"]) != required_codes:
        _fail("index_closes kod kapsami XU100 + kullanilan sektor endeksleriyle birebir olmali")

    close_keys = set(zip(closes["index_code"], closes["trade_date"]))
    for signal in signals:
        position = calendar_position[signal]
        if position < beta_lookback:
            _fail(f"{signal.date()} icin {beta_lookback} trading-day lookback takvimde yok")
        required_days = calendar[position - beta_lookback : position + 1]
        if required_days[0] < coverage_start:
            _fail("ilk M3 beta penceresi manifest.coverage.start_date oncesine tasiyor")
        signal_codes = {market_index}
        signal_members = membership.loc[membership["signal_date"] == signal, "ticker"]
        signal_codes.update(route_by_member[(signal, ticker)] for ticker in signal_members)
        missing = [
            (code, day)
            for code in sorted(signal_codes)
            for day in required_days
            if (code, day) not in close_keys
        ]
        if missing:
            code, day = missing[0]
            _fail(f"M3 index close coverage eksik: {signal.date()} {code} {day.date()}")

    if declared_status == "OPEN":
        blockers.append("PACKAGE_DECLARED_OPEN")
    effective_status = "CLOSED" if declared_status == "CLOSED" and not blockers else "OPEN"
    if require_closed and effective_status != "CLOSED":
        _fail(f"M3 source package CLOSED degil: {sorted(set(blockers))}")
    return HistoricalM3SourcePackageReport(
        contract=CONTRACT,
        declared_status=declared_status,
        effective_status=effective_status,
        membership_rows=len(membership),
        signal_months=len(signals),
        route_rows=len(routes),
        index_close_rows=len(closes),
        required_index_codes=tuple(sorted(required_codes)),
        raw_source_count=len(raw_sources),
        blockers=tuple(sorted(set(blockers))),
    )
