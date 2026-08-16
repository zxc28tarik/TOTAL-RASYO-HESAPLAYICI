from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.build_universe import build_universe_rows
from src.ingest.sector_routing import (
    SectorRoutingConfig,
    SectorRoutingError,
    infer_sector_family,
)


def test_default_routing_never_treats_broad_financial_index_as_bank():
    assert infer_sector_family("XBANK") == "BANK"
    assert infer_sector_family("XUMAL") == "FINANCIAL"
    assert infer_sector_family("XHOLD") == "HOLDING"
    assert infer_sector_family("XGMYO") == "GYO"
    assert infer_sector_family("XUSIN") == "NONFIN"


def test_explicit_sector_code_is_more_precise_than_broad_index():
    cfg = SectorRoutingConfig.default()
    assert cfg.route(ticker="ISFIN", sector_index_code="XUMAL", sector_code="FINANCIAL") == "FINANCIAL"
    assert cfg.route(ticker="OVERRIDE", sector_index_code="XUMAL", sector_code="INSURANCE") == "INSURANCE"


def test_ticker_override_has_highest_priority():
    cfg = SectorRoutingConfig.from_dict({
        "routing_profile": "T",
        "routing_version": 1,
        "default_family": "NONFIN",
        "index_to_family": {"XUMAL": "FINANCIAL"},
        "ticker_overrides": {"AAA": "BANK"},
    })
    assert cfg.route(ticker="aaa", sector_index_code="XUMAL", sector_code="FINANCIAL") == "BANK"


def test_build_universe_uses_safe_default_router():
    rows = build_universe_rows(
        ["AAA", "BBB"],
        {
            "AAA": {"sector_index_code": "XUMAL"},
            "BBB": {"sector_index_code": "XBANK"},
        },
    )
    assert [(row.ticker, row.sector_code) for row in rows] == [
        ("AAA", "FINANCIAL"),
        ("BBB", "BANK"),
    ]


def test_checked_in_routing_config_matches_runtime_defaults():
    payload = json.loads(Path("config/sector_routing.v1.json").read_text())
    cfg = SectorRoutingConfig.from_dict(payload)
    for code in ("XBANK", "XUMAL", "XHOLD", "XGMYO"):
        assert cfg.route(ticker="AAA", sector_index_code=code) == infer_sector_family(code)


@pytest.mark.parametrize(
    "patch",
    [
        {"routing_version": True},
        {"default_family": "UNKNOWN"},
        {"index_to_family": []},
        {"ticker_overrides": {"AAA": "UNKNOWN"}},
        {"unexpected": 1},
    ],
)
def test_routing_config_rejects_invalid_contract(patch):
    base = {
        "routing_profile": "T",
        "routing_version": 1,
        "default_family": "NONFIN",
        "index_to_family": {},
        "ticker_overrides": {},
    }
    base.update(patch)
    with pytest.raises(SectorRoutingError):
        SectorRoutingConfig.from_dict(base)


def test_fill_sector_sql_does_not_route_xumal_to_bank():
    sql = Path("sql/004_fill_sector_group.sql").read_text().upper()
    assert "XUMAL' THEN 'FINANCIAL'" in sql
    assert "('XBANK','XUMAL')" not in sql.replace(" ", "")


def test_daily_rsc_group_map_respects_explicit_sector_before_broad_index():
    import pandas as pd
    from src.analytics.rsc_scoring import build_sector_group_map

    universe = pd.DataFrame([
        {"ticker": "SIGRT", "sector_index_code": "XUMAL", "sector_code": "INSURANCE"},
        {"ticker": "FAKT", "sector_index_code": "XUMAL", "sector_code": "FINANCIAL"},
        {"ticker": "BANKA", "sector_index_code": "XBANK", "sector_code": ""},
    ])
    result = build_sector_group_map(
        universe,
        {"XBANK": "BANK", "XUMAL": "FINANCIAL", "*": "NONFIN"},
    )
    assert result == {"SIGRT": "INSURANCE", "FAKT": "FINANCIAL", "BANKA": "BANK"}
