from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.analytics.historical_m3_source_package import (
    CONTRACT,
    INDEX_CLOSE_SCHEMA,
    ROUTE_SCHEMA,
    HistoricalM3SourcePackageError,
    verify_historical_m3_source_package,
)


SCHEMA_PATH = Path("config/historical_m3_source_package_v1.schema.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class PackageFixture:
    root: Path
    manifest_path: Path
    membership: pd.DataFrame
    calendar: pd.DataFrame

    def payload(self) -> dict[str, object]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write_payload(self, payload: dict[str, object]) -> None:
        self.manifest_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def refresh_hashes(self) -> None:
        payload = self.payload()
        expected: dict[str, str] = {}
        for descriptor in payload["canonical_files"].values():
            path = self.root / descriptor["path"]
            descriptor["sha256"] = _sha(path)
            descriptor["row_count"] = len(pd.read_csv(path, keep_default_na=False))
            expected[descriptor["path"]] = descriptor["sha256"]
        for source in payload["raw_sources"]:
            path = self.root / source["raw_path"]
            if path.exists():
                source["raw_sha256"] = _sha(path)
                expected[source["raw_path"]] = source["raw_sha256"]
        transform = payload["transformation"]
        for path_key, sha_key in (
            ("entrypoint_path", "entrypoint_sha256"),
            ("determinism_test_path", "determinism_test_sha256"),
        ):
            transform[sha_key] = _sha(self.root / transform[path_key])
            expected[transform[path_key]] = transform[sha_key]
        sums = self.root / payload["sha256sums_path"]
        sums.write_text(
            "".join(f"{digest}  {path}\n" for path, digest in sorted(expected.items())),
            encoding="utf-8",
        )
        payload["sha256sums_sha256"] = _sha(sums)
        self.write_payload(payload)


@pytest.fixture
def package(tmp_path: Path) -> PackageFixture:
    routes_path = tmp_path / "sector_routes.csv"
    closes_path = tmp_path / "index_closes.csv"
    raw_routes = tmp_path / "raw_sector_routes.bin"
    raw_closes = tmp_path / "raw_index_closes.bin"
    transform = tmp_path / "build_package.py"
    determinism_test = tmp_path / "test_build_package.py"
    manifest_path = tmp_path / "manifest.json"

    calendar_days = pd.bdate_range(end="2023-02-01", periods=330)
    calendar = pd.DataFrame({"trade_date": calendar_days})
    membership = pd.DataFrame(
        [
            {"signal_date": signal, "ticker": ticker}
            for signal in ("2023-01-02", "2023-02-01")
            for ticker in ("AAA", "BBB")
        ]
    )
    routes = pd.DataFrame(
        [
            {
                "ticker": ticker,
                "valid_from": "2020-01-01",
                "valid_to": "",
                "sector_index_code": "XTEST",
                "source_id": "ROUTE_RAW",
            }
            for ticker in ("AAA", "BBB")
        ]
    )
    routes.to_csv(routes_path, index=False)
    step = np.arange(len(calendar_days), dtype=float)
    closes = pd.concat(
        [
            pd.DataFrame(
                {
                    "index_code": code,
                    "trade_date": calendar_days.strftime("%Y-%m-%d"),
                    "close": base * np.power(growth, step),
                    "source_id": "INDEX_RAW",
                }
            )
            for code, base, growth in (
                ("XU100", 100.0, 1.001),
                ("XTEST", 80.0, 1.0012),
            )
        ],
        ignore_index=True,
    )
    closes.to_csv(closes_path, index=False)
    raw_routes.write_bytes(b"immutable route source\n")
    raw_closes.write_bytes(b"immutable index close source\n")
    transform.write_text("# deterministic fixture builder\n", encoding="utf-8")
    determinism_test.write_text("def test_reproduction():\n    assert True\n", encoding="utf-8")

    payload = {
        "contract": CONTRACT,
        "package_status": "CLOSED",
        "assembled_at": "2023-02-02T12:00:00+03:00",
        "market_index": "XU100",
        "coverage": {
            "start_date": calendar_days[0].date().isoformat(),
            "end_date": calendar_days[-1].date().isoformat(),
            "beta_lookback_trading_days": 252,
            "alpha_window_trading_days": 63,
        },
        "canonical_files": {
            "sector_routes": {
                "path": routes_path.name,
                "sha256": "0" * 64,
                "row_count": len(routes),
                "schema": [
                    "ticker",
                    "valid_from",
                    "valid_to",
                    "sector_index_code",
                    "source_id",
                ],
            },
            "index_closes": {
                "path": closes_path.name,
                "sha256": "0" * 64,
                "row_count": len(closes),
                "schema": ["index_code", "trade_date", "close", "source_id"],
            },
        },
        "raw_sources": [
            {
                "source_id": "ROUTE_RAW",
                "publisher": "Official Route Publisher",
                "source_url": "https://example.com/official/routes",
                "artifact_identity": "route-document-2023-02",
                "retrieved_at": "2023-02-02T08:00:00+03:00",
                "raw_path": raw_routes.name,
                "raw_sha256": "0" * 64,
            },
            {
                "source_id": "INDEX_RAW",
                "publisher": "Official Index Publisher",
                "source_url": "https://example.com/official/index-closes",
                "artifact_identity": "index-close-export-2023-02",
                "retrieved_at": "2023-02-02T08:30:00+03:00",
                "raw_path": raw_closes.name,
                "raw_sha256": "0" * 64,
            },
        ],
        "transformation": {
            "entrypoint_path": transform.name,
            "entrypoint_sha256": "0" * 64,
            "determinism_test_path": determinism_test.name,
            "determinism_test_sha256": "0" * 64,
            "reproduction_command": "python -m pytest -q test_build_package.py",
        },
        "sha256sums_path": "SHA256SUMS",
        "sha256sums_sha256": "0" * 64,
    }
    fixture = PackageFixture(tmp_path, manifest_path, membership, calendar)
    fixture.write_payload(payload)
    fixture.refresh_hashes()
    return fixture


def _verify(package: PackageFixture, **overrides):
    kwargs = {
        "manifest_path": package.manifest_path,
        "repo_root": package.root,
        "historical_membership": package.membership,
        "trading_calendar": package.calendar,
        "expected_signal_months": 2,
        "expected_members_per_signal": 2,
        "required_signal_start_month": "2023-01",
        "required_signal_end_month": "2023-02",
    }
    kwargs.update(overrides)
    return verify_historical_m3_source_package(**kwargs)


def test_closed_package_locks_raw_canonical_transform_and_full_m3_coverage(package):
    report = _verify(package)

    assert report.closed
    assert report.membership_rows == 4
    assert report.signal_months == 2
    assert report.required_index_codes == ("XTEST", "XU100")
    assert report.blockers == ()


def test_machine_readable_schema_locks_contract_and_canonical_columns():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["contract"] == {"const": CONTRACT}
    assert schema["properties"]["market_index"] == {"const": "XU100"}
    assert schema["properties"]["coverage"]["properties"]["beta_lookback_trading_days"] == {
        "const": 252
    }
    assert ROUTE_SCHEMA == (
        "ticker", "valid_from", "valid_to", "sector_index_code", "source_id"
    )
    assert INDEX_CLOSE_SCHEMA == ("index_code", "trade_date", "close", "source_id")


def test_open_package_never_becomes_effectively_closed(package):
    payload = package.payload()
    payload["package_status"] = "OPEN"
    package.write_payload(payload)

    report = _verify(package, require_closed=False)
    assert not report.closed
    assert report.blockers == ("PACKAGE_DECLARED_OPEN",)
    with pytest.raises(HistoricalM3SourcePackageError, match="CLOSED degil"):
        _verify(package)


def test_closed_package_rejects_uncommitted_raw_source(package):
    (package.root / "raw_sector_routes.bin").unlink()
    with pytest.raises(HistoricalM3SourcePackageError, match="commit edilmis normal dosya"):
        _verify(package)


def test_open_package_reports_uncommitted_raw_source_as_blocker(package):
    payload = package.payload()
    payload["package_status"] = "OPEN"
    package.write_payload(payload)
    (package.root / "raw_sector_routes.bin").unlink()
    package.refresh_hashes()

    report = _verify(package, require_closed=False)
    assert report.blockers == ("PACKAGE_DECLARED_OPEN", "RAW_SOURCE_NOT_COMMITTED:ROUTE_RAW")


def test_open_package_cannot_mask_unsafe_raw_path_as_missing_source(package):
    payload = package.payload()
    payload["package_status"] = "OPEN"
    payload["raw_sources"][0]["raw_path"] = "../outside.bin"
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="guvenli bagil yol"):
        _verify(package, require_closed=False)


def test_canonical_file_hash_mutation_is_rejected(package):
    with (package.root / "sector_routes.csv").open("a", encoding="utf-8") as handle:
        handle.write("CCC,2020-01-01,,XTEST,ROUTE_RAW\n")
    with pytest.raises(HistoricalM3SourcePackageError, match="sha256"):
        _verify(package)


def test_source_url_must_be_public_https_and_retrieval_cannot_be_future(package):
    payload = package.payload()
    payload["raw_sources"][0]["source_url"] = "https://localhost/private"
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="halka acik HTTPS"):
        _verify(package)

    payload = package.payload()
    payload["raw_sources"][0]["source_url"] = "https://example.com/official/routes"
    payload["raw_sources"][0]["retrieved_at"] = "2023-02-03T12:00:00+03:00"
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="assembled_at sonrasinda"):
        _verify(package)


def test_sha256sums_must_cover_all_and_only_evidence_files(package):
    sums = package.root / "SHA256SUMS"
    sums.write_text(sums.read_text(encoding="utf-8") + f"{'a'*64}  extra.bin\n", encoding="utf-8")
    payload = package.payload()
    payload["sha256sums_sha256"] = _sha(sums)
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="tum ve yalniz"):
        _verify(package)


def test_route_coverage_must_equal_historical_member_union(package):
    routes = pd.read_csv(package.root / "sector_routes.csv", keep_default_na=False)
    routes.loc[routes["ticker"] != "BBB"].to_csv(package.root / "sector_routes.csv", index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="ticker kapsami"):
        _verify(package)


def test_route_intervals_cannot_overlap(package):
    routes = pd.read_csv(package.root / "sector_routes.csv", keep_default_na=False)
    routes.loc[routes["ticker"] == "AAA", "valid_to"] = "2023-02-01"
    routes = pd.concat(
        [
            routes,
            pd.DataFrame(
                [{
                    "ticker": "AAA",
                    "valid_from": "2023-01-15",
                    "valid_to": "",
                    "sector_index_code": "XTEST",
                    "source_id": "ROUTE_RAW",
                }]
            ),
        ],
        ignore_index=True,
    )
    routes.to_csv(package.root / "sector_routes.csv", index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="cakisan"):
        _verify(package)


def test_route_cannot_silently_fallback_to_xu100(package):
    routes = pd.read_csv(package.root / "sector_routes.csv", keep_default_na=False)
    routes.loc[routes["ticker"] == "AAA", "sector_index_code"] = "XU100"
    routes.to_csv(package.root / "sector_routes.csv", index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="fallback"):
        _verify(package)


def test_missing_daily_sector_close_in_any_beta_window_is_rejected(package):
    closes = pd.read_csv(package.root / "index_closes.csv", keep_default_na=False)
    victim = closes.loc[closes["index_code"] == "XTEST"].iloc[100]
    closes = closes.loc[
        ~((closes["index_code"] == "XTEST") & (closes["trade_date"] == victim["trade_date"]))
    ]
    closes.to_csv(package.root / "index_closes.csv", index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="coverage eksik"):
        _verify(package)


def test_duplicate_or_nonpositive_index_close_is_rejected(package):
    closes_path = package.root / "index_closes.csv"
    closes = pd.read_csv(closes_path, keep_default_na=False)
    pd.concat([closes, closes.iloc[[0]]], ignore_index=True).to_csv(closes_path, index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="duplicate"):
        _verify(package)

    closes = closes.copy()
    closes.loc[0, "close"] = 0
    closes.to_csv(closes_path, index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="pozitif sonlu"):
        _verify(package)


def test_index_close_outside_declared_window_is_rejected(package):
    closes_path = package.root / "index_closes.csv"
    closes = pd.read_csv(closes_path, keep_default_na=False)
    future = closes.iloc[[0]].copy()
    future["trade_date"] = "2023-02-02"
    closes = pd.concat([closes, future], ignore_index=True)
    closes.to_csv(closes_path, index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="coverage disina"):
        _verify(package)


def test_canonical_source_lineage_must_equal_raw_source_registry(package):
    closes_path = package.root / "index_closes.csv"
    closes = pd.read_csv(closes_path, keep_default_na=False)
    closes.loc[0, "source_id"] = "UNKNOWN"
    closes.to_csv(closes_path, index=False)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="source_id kapsami"):
        _verify(package)


def test_signal_membership_is_exhaustive_and_month_contiguous(package):
    broken = package.membership.iloc[:-1].copy()
    with pytest.raises(HistoricalM3SourcePackageError, match="uye sayisini"):
        _verify(package, historical_membership=broken)


def test_production_defaults_cannot_be_reduced_by_manifest_claims(package):
    with pytest.raises(HistoricalM3SourcePackageError, match="signal ayi 2, beklenen 60"):
        verify_historical_m3_source_package(
            manifest_path=package.manifest_path,
            repo_root=package.root,
            historical_membership=package.membership,
            trading_calendar=package.calendar,
        )


def test_first_signal_requires_full_252_trading_day_lookback(package):
    short_calendar = package.calendar.tail(220).reset_index(drop=True)
    allowed = set(pd.to_datetime(short_calendar["trade_date"]).dt.strftime("%Y-%m-%d"))
    closes_path = package.root / "index_closes.csv"
    closes = pd.read_csv(closes_path, keep_default_na=False)
    closes.loc[closes["trade_date"].isin(allowed)].to_csv(closes_path, index=False)
    payload = package.payload()
    payload["coverage"]["start_date"] = pd.Timestamp(short_calendar.iloc[0]["trade_date"]).date().isoformat()
    package.write_payload(payload)
    package.refresh_hashes()
    with pytest.raises(HistoricalM3SourcePackageError, match="252 trading-day lookback"):
        _verify(package, trading_calendar=short_calendar)


def test_manifest_paths_cannot_escape_repository(package):
    payload = package.payload()
    payload["canonical_files"]["sector_routes"]["path"] = "../sector_routes.csv"
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="guvenli bagil yol"):
        _verify(package)


def test_transformation_and_determinism_test_are_hash_locked(package):
    (package.root / "build_package.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(HistoricalM3SourcePackageError, match="entrypoint_sha256"):
        _verify(package)


def test_reproduction_command_cannot_merely_echo_pytest(package):
    payload = package.payload()
    payload["transformation"]["reproduction_command"] = "echo pytest test_build_package.py"
    package.write_payload(payload)
    with pytest.raises(HistoricalM3SourcePackageError, match="dogrudan python -m pytest"):
        _verify(package)
