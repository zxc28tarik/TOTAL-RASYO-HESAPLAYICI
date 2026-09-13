from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from scripts.build_historical_m3_source_package import (
    COVERAGE_END,
    COVERAGE_START,
    GRTHO_CHANGE_DATE,
    GRTHO_LINEAGE_CSV,
    GRTHO_LINEAGE_PROVENANCE,
    PACKAGE_RELATIVE,
    _historical_membership,
    _verify_grtho_identity_lineage,
    build_canonical_bytes,
)
from src.analytics.historical_m3_source_package import verify_historical_m3_source_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / PACKAGE_RELATIVE


def _read_gzip_csv(name: str) -> pd.DataFrame:
    with gzip.open(PACKAGE / name, "rt", encoding="utf-8", newline="") as handle:
        return pd.read_csv(handle, dtype=str, keep_default_na=False)


@pytest.mark.skipif(sys.platform == "win32", reason="Pinned Linux compressor byte contract; Windows checks canonical payload and same-runtime determinism separately")
def test_committed_raw_evidence_rebuilds_byte_identical_canonical_files():
    rebuilt = build_canonical_bytes(ROOT)

    assert rebuilt["sector_routes.csv.gz"] == (PACKAGE / "sector_routes.csv.gz").read_bytes()
    assert rebuilt["index_closes.csv.gz"] == (PACKAGE / "index_closes.csv.gz").read_bytes()


def test_cross_platform_rebuild_preserves_payload_and_is_repeatable():
    first = build_canonical_bytes(ROOT)
    second = build_canonical_bytes(ROOT)
    for name in ("sector_routes.csv.gz", "index_closes.csv.gz"):
        assert first[name] == second[name]
        # Original source-byte hashes are still enforced by the package verifier.
        assert gzip.decompress(first[name]) == gzip.decompress((PACKAGE / name).read_bytes())


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


def test_grtho_identity_lineage_is_explicit_and_hash_locked_in_manifest():
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    descriptor = next(
        source
        for source in manifest["raw_sources"]
        if source["source_id"] == "KAP_BILDIRIM_1331451"
    )
    identity = descriptor["artifact_identity"]
    csv_path = ROOT / "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv"
    provenance_path = ROOT / (
        "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.provenance.json"
    )

    assert "relatedStocks=GRTRK" in identity
    assert "GRTRK->GRTHO effective 2024-10-01" in identity
    assert csv_path.relative_to(ROOT).as_posix() in identity
    assert hashlib.sha256(csv_path.read_bytes()).hexdigest() in identity
    assert provenance_path.relative_to(ROOT).as_posix() in identity
    assert hashlib.sha256(provenance_path.read_bytes()).hexdigest() in identity

    lineage = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    event = lineage.loc[
        (lineage["effective_date"] == "2024-10-01")
        & (lineage["old_ticker"] == "GRTRK")
        & (lineage["new_ticker"] == "GRTHO")
    ]
    assert len(event) == 1
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert {
        "effective_date": "2024-10-01",
        "old_ticker": "GRTRK",
        "new_ticker": "GRTHO",
    } in provenance["notable_bist100_relevant_changes"]
    assert event.iloc[0]["source_workbook_sha256"] == provenance["workbook_sha256"]


@pytest.mark.parametrize("mutated_source", ["csv", "provenance"])
def test_grtho_identity_lineage_rejects_missing_official_mapping(tmp_path, mutated_source):
    for relative in (GRTHO_LINEAGE_CSV, GRTHO_LINEAGE_PROVENANCE):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())

    if mutated_source == "csv":
        path = tmp_path / GRTHO_LINEAGE_CSV
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "2024-10-01,GRTRK,GRTHO", "2024-10-01,GRTRK,WRONG"
            ),
            encoding="utf-8",
        )
    else:
        path = tmp_path / GRTHO_LINEAGE_PROVENANCE
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["notable_bist100_relevant_changes"] = [
            event
            for event in payload["notable_bist100_relevant_changes"]
            if event.get("old_ticker") != "GRTRK"
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="GRTRK->GRTHO"):
        _verify_grtho_identity_lineage(tmp_path)


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
