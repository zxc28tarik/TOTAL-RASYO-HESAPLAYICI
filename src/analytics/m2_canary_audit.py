from __future__ import annotations

"""Fail-closed evidence helpers for the historical NONFIN M2 canary.

This module deliberately does not calculate an M2 value.  It proves whether a
source derivation and a peer cohort are admissible before the production replay
is called, so an insufficient valuation cannot escape as a neutral score.
"""

from collections.abc import Mapping
from typing import Any


UNSAFE_SHARE_DERIVATIONS = frozenset({"ISSUED_CAPITAL_OVER_NOMINAL"})
SAFE_SHARE_DERIVATIONS = frozenset({"EXPLICIT_CLASS_NOMINAL_SUM"})


def audit_derivation_contract(
    quarter: Mapping[str, Any],
    valuation_config: Mapping[str, Any],
) -> dict[str, Any]:
    actual_profile = quarter.get("derivation_profile")
    actual_version = quarter.get("derivation_version")
    expected_profile = valuation_config.get("source_derivation_profile")
    expected_version = valuation_config.get("source_derivation_version")
    diagnostics = quarter.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    field_sources = diagnostics.get("field_sources")
    if not isinstance(field_sources, Mapping):
        field_sources = {}
    share_derivation = field_sources.get("shares_out")

    blockers: list[str] = []
    if (actual_profile, actual_version) != (expected_profile, expected_version):
        blockers.append(
            "SOURCE_DERIVATION_PROFILE_MISMATCH:"
            f"artifact={actual_profile}@{actual_version};"
            f"config={expected_profile}@{expected_version}"
        )
    if share_derivation in UNSAFE_SHARE_DERIVATIONS:
        blockers.append(f"UNPROVEN_SHARE_DERIVATION:{share_derivation}")

    return {
        "actual_derivation_profile": actual_profile,
        "actual_derivation_version": actual_version,
        "expected_derivation_profile": expected_profile,
        "expected_derivation_version": expected_version,
        "actual_semantic_profile": diagnostics.get("semantic_profile"),
        "actual_semantic_version": diagnostics.get("semantic_version"),
        "share_derivation": share_derivation,
        "compatible": not blockers,
        "forced_profile_rename_allowed": False,
        "blockers": blockers,
    }


def audit_real_peer_cohort(
    per_ticker: Mapping[str, Any],
    *,
    target_ticker: str,
    externally_certified_share_tickers: set[str] | frozenset[str],
    minimum_peer_count: int,
) -> dict[str, Any]:
    target = target_ticker.strip().upper()
    certified = {str(value).strip().upper() for value in externally_certified_share_tickers}
    financial_candidates: list[str] = []
    safe_share_candidates: list[str] = []
    unsafe_share_candidates: list[str] = []

    for raw_ticker, payload in per_ticker.items():
        ticker = str(raw_ticker).strip().upper()
        if not isinstance(payload, Mapping) or payload.get("technical_family") != "NONFIN":
            continue
        quarters = payload.get("quarters")
        if not isinstance(quarters, list) or len(quarters) < 4:
            continue
        financial_candidates.append(ticker)
        latest = quarters[-1]
        diagnostics = latest.get("diagnostics") if isinstance(latest, Mapping) else None
        sources = diagnostics.get("field_sources") if isinstance(diagnostics, Mapping) else None
        share_source = sources.get("shares_out") if isinstance(sources, Mapping) else None
        if ticker in certified or share_source in SAFE_SHARE_DERIVATIONS:
            safe_share_candidates.append(ticker)
        else:
            unsafe_share_candidates.append(ticker)

    peers = sorted(ticker for ticker in safe_share_candidates if ticker != target)
    return {
        "target_ticker": target,
        "financial_candidate_count": len(financial_candidates),
        "safe_share_candidate_count": len(safe_share_candidates),
        "safe_peer_count": len(peers),
        "minimum_peer_count": minimum_peer_count,
        "peer_gate_passed": len(peers) >= minimum_peer_count,
        "safe_share_candidates": sorted(safe_share_candidates),
        "safe_peer_tickers": peers,
        "unsafe_share_candidate_count": len(unsafe_share_candidates),
        "unsafe_share_tickers": sorted(unsafe_share_candidates),
    }
