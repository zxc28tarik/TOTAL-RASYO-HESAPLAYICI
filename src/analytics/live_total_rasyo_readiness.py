from __future__ import annotations

"""Database-free readiness ledger for a current Total Rasyo run."""

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


MODULE_KEYS = ("M1", "M2", "M3", "Ek1", "Ek4", "Ek9")
SPECIALIST_FAMILIES = frozenset({"BANK", "HOLDING", "GYO", "INSURANCE", "FINANCIAL"})


def bind_current_report_families(
    tickers: Iterable[str], reports: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_token: dict[str, list[Mapping[str, Any]]] = {}
    for report in reports:
        entity = str(report.get("source_entity_code") or "").strip().upper()
        for token in entity.split("-"):
            if token:
                by_token.setdefault(token, []).append(report)

    output: dict[str, dict[str, Any]] = {}
    for raw in tickers:
        ticker = str(raw).strip().upper()
        candidates = by_token.get(ticker, [])
        families = sorted({str(row["family"]) for row in candidates if row.get("family")})
        if len(families) == 1 and families[0] in SPECIALIST_FAMILIES:
            family = families[0]
            status = "SPECIALIST_FAMILY_BOUND"
        elif candidates and all(row.get("schema") == "GENERAL" for row in candidates):
            family = None
            status = "GENERAL_SCHEMA_NOT_ECONOMIC_NONFIN_PROOF"
        elif candidates:
            family = None
            status = "AMBIGUOUS_OR_UNSUPPORTED_FAMILY"
        else:
            family = None
            status = "CURRENT_FINANCIAL_ENTITY_UNBOUND"
        output[ticker] = {
            "ticker": ticker,
            "family": family,
            "family_status": status,
            "report_count": len(candidates),
            "source_entity_codes": sorted({str(row.get("source_entity_code")) for row in candidates}),
        }
    return output


def build_live_readiness(
    *,
    universe_rows: Iterable[Mapping[str, Any]],
    report_rows: Iterable[Mapping[str, Any]],
    database_available: bool,
    share_basis_tickers: Iterable[str] = (),
    raw_close_tickers: Iterable[str] = (),
    market_cap_tickers: Iterable[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    universe = list(universe_rows)
    tickers = [str(row.get("ticker") or "").strip().upper() for row in universe]
    if not tickers or any(not ticker for ticker in tickers) or len(tickers) != len(set(tickers)):
        raise ValueError("live universe tickers must be nonempty and unique")
    bindings = bind_current_report_families(tickers, report_rows)
    share_ready = {str(value).strip().upper() for value in share_basis_tickers}
    price_ready = {str(value).strip().upper() for value in raw_close_tickers}
    market_cap_ready = {str(value).strip().upper() for value in market_cap_tickers}
    rejections: list[dict[str, Any]] = []
    for ticker in sorted(tickers):
        binding = bindings[ticker]
        reasons = []
        if binding["family"] is None:
            reasons.append(binding["family_status"])
        if ticker not in share_ready:
            reasons.append("CURRENT_EXPLICIT_SHARE_BASIS_NOT_CAPTURED")
        if ticker not in price_ready:
            reasons.append("CURRENT_RAW_CLOSE_NOT_CAPTURED")
        if ticker not in market_cap_ready:
            reasons.append("CURRENT_PRICE_LEVEL_MARKET_CAP_NOT_MATERIALIZED")
        if not database_available:
            reasons.append("MODULE_CONTEXT_DATABASE_NOT_CONFIGURED")
        if binding["family"] == "HOLDING":
            reasons.append("CURRENT_HOLDING_NAV_NOT_MATERIALIZED")
        elif binding["family"] == "GYO":
            reasons.append("CURRENT_GYO_NAV_NOT_MATERIALIZED")
        rejections.append({**binding, "reasons": reasons, "total_materialized": False})

    family_counts = Counter((row["family"] or row["family_status"]) for row in bindings.values())
    receipt = {
        "universe_count": len(tickers),
        "family_counts": dict(sorted(family_counts.items())),
        "module_coverage": {key: 0 for key in MODULE_KEYS},
        "total_rasyo_count": 0,
        "ranking_count": 0,
        "explicit_rejection_count": len(rejections),
        "database_available": bool(database_available),
        "explicit_share_basis_count": len(set(tickers) & share_ready),
        "raw_close_count": len(set(tickers) & price_ready),
        "price_and_share_ready_count": len(set(tickers) & share_ready & price_ready),
        "market_cap_ready_count": len(set(tickers) & market_cap_ready),
        "silent_drop_count": 0,
        "neutral_score_injection": False,
    }
    return receipt, rejections
