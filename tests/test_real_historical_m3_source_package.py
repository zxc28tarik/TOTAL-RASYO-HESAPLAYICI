from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd

from scripts.build_historical_m3_source_package import (
    COVERAGE_END,
    COVERAGE_START,
    GRTHO_CHANGE_DATE,
    PACKAGE_RELATIVE,
    _historical_membership,
    build_canonical_bytes,
)
from src.analytics.historical_m3_source_package import verify_historical_m3_source_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / PACKAGE_RELATIVE


def _read_gzip_csv(name: str) -> pd.DataFrame:
    with gzip.open(PACKAGE / name, "rt", encoding="utf-8", newline="") as handle:
        return pd.read_csv(handle, dtype=str, keep_default_na=False)


def test_committed_raw_evidence_rebuilds_byte_identical_canonical_files():
    rebuilt = build_canonical_bytes(ROOT)

    assert rebuilt["sector_routes.csv.gz"] == (PACKAGE / "sector_routes.csv.gz").read_bytes()
    assert rebuilt["index_closes.csv.gz"] == (PACKAGE / "index_closes.csv.gz").read_bytes()


def test_real_routes_cover_209_historical_tickers_without_xu100_fallback():
    routes = _read_gzip_csv("sector_routes.csv.gz")
    membership = _historical_membership(ROOT)

    assert len(membership) == 6000
    assert membership["signal_date"].nunique() == 60
    assert membership["ticker"].nunique() == 209
    assert len(routes) == 210
    assert set(routes["ticker"]) == set(membership["ticker"])
    assert set(routes["sector_index_code"]) == {"XUSIN", "XUHIZ", "XUMAL", "XUTEK"}
    assert "XU100" not in set(routes["sector_index_code"])


def test_grtho_sector_change_is_half_open_and_uses_kap_1331451():
    routes = _read_gzip_csv("sector_routes.csv.gz")
    grtho = routes.loc[routes["ticker"] == "GRTHO"].reset_index(drop=True)

    assert grtho.to_dict("records") == [
        {
            "ticker": "GRTHO",
            "valid_from": COVERAGE_START,
            "valid_to": GRTHO_CHANGE_DATE,
            "sector_index_code": "XUHIZ",
            "source_id": "KAP_BILDIRIM_1331451",
        },
        {
            "ticker": "GRTHO",
            "valid_from": GRTHO_CHANGE_DATE,
            "valid_to": "",
            "sector_index_code": "XUMAL",
            "source_id": "KAP_BILDIRIM_1331451",
        },
    ]


def test_official_borsa_closes_are_complete_on_one_locked_calendar():
    closes = _read_gzip_csv("index_closes.csv.gz")
    expected_codes = {"XU100", "XUSIN", "XUHIZ", "XUMAL", "XUTEK"}

    assert len(closes) == 7415
    assert set(closes["index_code"]) == expected_codes
    assert not closes.duplicated(["index_code", "trade_date"]).any()
    counts = closes.groupby("index_code")["trade_date"].nunique()
    assert counts.eq(1483).all()
    date_sets = [
        tuple(group.sort_values("trade_date")["trade_date"])
        for _, group in closes.groupby("index_code")
    ]
    assert all(dates == date_sets[0] for dates in date_sets[1:])
    assert date_sets[0][0] == COVERAGE_START
    assert date_sets[0][-1] == COVERAGE_END
    assert pd.to_numeric(closes["close"], errors="raise").gt(0).all()


def test_real_package_is_closed_against_6000_memberships_and_252_day_windows():
    membership = _historical_membership(ROOT)
    closes = _read_gzip_csv("index_closes.csv.gz")
    calendar = (
        closes.loc[closes["index_code"] == "XU100", ["trade_date"]]
        .sort_values("trade_date")
        .reset_index(drop=True)
    )

    report = verify_historical_m3_source_package(
        manifest_path=PACKAGE / "manifest.json",
        repo_root=ROOT,
        historical_membership=membership,
        trading_calendar=calendar,
        require_closed=True,
    )

    assert report.closed
    assert report.membership_rows == 6000
    assert report.signal_months == 60
    assert report.route_rows == 210
    assert report.index_close_rows == 7415
    assert report.required_index_codes == ("XU100", "XUHIZ", "XUMAL", "XUSIN", "XUTEK")
    assert report.raw_source_count == 7
    assert report.blockers == ()
