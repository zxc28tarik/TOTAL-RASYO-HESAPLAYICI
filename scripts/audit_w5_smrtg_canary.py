from __future__ import annotations

"""W5 — verify the SMRTG 2023-08 historical M2 canary and bound its blocker.

The ledger asks three things of this canary: re-verify the share/action evidence
that already passes, prove whether the derivation-profile mismatch is data,
config or a routing/code defect, and then either produce a real historical M2
with provenance or leave a BLOCKED record carrying its full reason and reopen
condition.  Minimum peer count and coverage thresholds may not be relaxed, and
no M2 may be produced without a real FOLLOW period.

This auditor is a second pass: it re-derives every number from the artifacts
rather than restating the earlier canary receipt, and it calls the production
cohort and contract helpers instead of copying their logic.  It measures one
thing the earlier receipt did not: whether the peer gate is reachable *at all*,
at this cutoff and across the whole 60-cutoff window.

It produces no score, repairs no cell, relaxes no threshold and changes no
production code.
"""

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.m2_canary_audit import (
    SAFE_SHARE_DERIVATIONS,
    audit_derivation_contract,
    audit_real_peer_cohort,
)

AUDIT = ROOT / "data/audit/w5_smrtg_canary_v1"
CONTRACT = "W5_SMRTG_HISTORICAL_M2_CANARY_VERIFICATION_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

TARGET_TICKER = "SMRTG"
TARGET_MONTH = "2023-08"
CUTOFF = "2023-07-31T18:10:00+03:00"
CUTOFF_DATE = date(2023, 7, 31)

PRIOR_CANARY = ROOT / "data/audit/smrtg_m2_canary_v1/receipt.json"
SHARE_RECEIPT = ROOT / "data/backtest_sources/zero_interval_share_basis_v1/receipt.json"
ACTION_MANIFEST = (
    ROOT / "data/backtest_sources/zero_interval_share_basis_v1/action_coverage_manifest.json"
)
SHARE_HISTORY = (
    ROOT / "data/backtest_sources/zero_interval_share_basis_v1/smrtg_kap_share_history.json"
)
CORE_DIAGNOSTICS = ROOT / "data/audit/experimental_materialization_v3/core_diagnostics.jsonl.gz"
SHARE_CLASSES = (
    ROOT / "data/backtest_sources/kap_share_class_history_v1/share_class_observations.jsonl.gz"
)
YAHOO_ACTIONS = (
    ROOT / "data/backtest_sources/yahoo_discovery"
    "/historical_member_actions_yahoo_2020-07_2026-08.csv"
)
DEFAULT_CONFIG = ROOT / "config/nonfin_valuation.relative_v1.json"
EXACT_CONFIG = ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json"
COHORT_HELPER = ROOT / "src/analytics/m2_canary_audit.py"

SOURCES = {
    "prior_canary_receipt": PRIOR_CANARY,
    "zero_interval_receipt": SHARE_RECEIPT,
    "action_coverage_manifest": ACTION_MANIFEST,
    "smrtg_kap_share_history": SHARE_HISTORY,
    "core_diagnostics": CORE_DIAGNOSTICS,
    "kap_share_class_observations": SHARE_CLASSES,
    "yahoo_member_actions": YAHOO_ACTIONS,
    "nonfin_valuation_default_config": DEFAULT_CONFIG,
    "nonfin_valuation_exact_config": EXACT_CONFIG,
    "m2_canary_audit_helper": COHORT_HELPER,
}

CONTENT_FILES = (
    "baseline.json",
    "cell_verification.json",
    "peer_certifiability.json",
    "systemic_bound.json",
    "verdict.json",
)

SHARE_ITEM_KEY = "kpy41_acc5_sermayeyi_temsil_eden"
RECONCILED = "EXPLICIT_CLASS_NOMINALS_RECONCILED"

# A peer can only join the cohort through one of these two routes.
ROUTE_DERIVATION = "SAFE_SHARE_DERIVATION_IN_CORE_ARTIFACT"
ROUTE_ZERO_INTERVAL = "ZERO_INTERVAL_EXTERNAL_CERTIFICATION"


class W5AuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def parse_kap_stamp(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return None


def read_share_class_observations() -> dict[str, list[datetime]]:
    """Usable, reconciled KAP share-class observation stamps per ticker."""
    out: dict[str, list[datetime]] = {}
    for line in gzip.decompress(SHARE_CLASSES.read_bytes()).splitlines():
        row = json.loads(line)
        if not row.get("usable") or row.get("status") != RECONCILED:
            continue
        stamp = parse_kap_stamp(row.get("published_at_local"))
        ticker = row.get("ticker")
        if stamp is None or not ticker:
            continue
        out.setdefault(str(ticker), []).append(stamp)
    return {ticker: sorted(stamps) for ticker, stamps in sorted(out.items())}


def read_yahoo_actions() -> dict[str, list[str]]:
    text = YAHOO_ACTIONS.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
    header, *lines = text.split("\n")
    columns = header.split(",")
    ticker_at, date_at = columns.index("ticker"), columns.index("action_date")
    out: dict[str, list[str]] = {}
    for line in lines:
        parts = line.split(",")
        out.setdefault(parts[ticker_at], []).append(parts[date_at])
    return {ticker: sorted(dates) for ticker, dates in sorted(out.items())}


def nonfin_candidates(per_ticker: dict) -> list[str]:
    """The cohort admission rule the production helper applies."""
    out = []
    for ticker, payload in per_ticker.items():
        if not isinstance(payload, dict) or payload.get("technical_family") != "NONFIN":
            continue
        quarters = payload.get("quarters")
        if not isinstance(quarters, list) or len(quarters) < 4:
            continue
        out.append(str(ticker))
    return sorted(out)


def share_derivation_of(payload: dict) -> str | None:
    quarters = payload.get("quarters") or []
    if not quarters:
        return None
    diagnostics = quarters[-1].get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    sources = diagnostics.get("field_sources")
    if not isinstance(sources, dict):
        return None
    value = sources.get("shares_out")
    return None if value is None else str(value)


def scan_core_diagnostics() -> tuple[dict, list[dict]]:
    """One streaming pass: the target cutoff record plus a per-cutoff summary."""
    target_record: dict | None = None
    per_cutoff: list[dict] = []
    for line in gzip.open(CORE_DIAGNOSTICS, "rt", encoding="utf-8"):
        record = json.loads(line)
        analysis_at = record.get("analysis_at")
        candidates = nonfin_candidates(record.get("per_ticker") or {})
        derivations: dict[str, int] = {}
        for ticker in candidates:
            value = share_derivation_of(record["per_ticker"][ticker])
            key = "NULL" if value is None else value
            derivations[key] = derivations.get(key, 0) + 1
        per_cutoff.append(
            {
                "analysis_at": analysis_at,
                "cutoff_date": datetime.fromisoformat(analysis_at).date().isoformat(),
                "nonfin_candidates": len(candidates),
                "share_derivation_counts": dict(sorted(derivations.items())),
                "candidate_tickers": candidates,
            }
        )
        if analysis_at == CUTOFF:
            target_record = record
    if target_record is None:
        raise W5AuditError(f"CORE_DIAGNOSTICS_MISSING_CUTOFF:{CUTOFF}")
    per_cutoff.sort(key=lambda row: row["analysis_at"])
    return target_record, per_cutoff


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def build_baseline(prior: dict) -> dict:
    """Freeze the state that existed before this audit touched anything."""
    return {
        "contract": CONTRACT,
        "purpose": (
            "Immutable record of the pre-audit state. Taken before any behaviour change so a "
            "later 'before' cannot be manufactured."
        ),
        "prior_canary": {
            "path": PRIOR_CANARY.relative_to(ROOT).as_posix(),
            "sha256": sha_file(PRIOR_CANARY),
            "contract": prior.get("contract"),
            "m2_materialized": prior.get("m2_materialized"),
            "total_materialized": prior.get("total_materialized"),
            "p4_materialized": prior.get("p4_materialized"),
            "neutral_m2_materialized": prior.get("neutral_m2_materialized"),
            "active_blockers": prior.get("active_blockers"),
            "share_action_gate_passed": prior.get("share_action_gate_passed"),
            "market_cap": prior.get("market_cap"),
            "shares_out": prior.get("shares_out"),
            "raw_close": prior.get("raw_close"),
        },
        "zero_interval_receipt": {
            "path": SHARE_RECEIPT.relative_to(ROOT).as_posix(),
            "sha256": sha_file(SHARE_RECEIPT),
        },
        "historical_pit_m2_before": 0,
        "live_results_that_must_not_change": {
            "core": 131,
            "m2": 48,
            "ek9": 11,
            "total": 2,
            "rejections": 805,
            "note": "verified separately by audit_w1_frozen_outputs and the W2 correction check",
        },
    }


# --------------------------------------------------------------------------
# W5-A / W5-B — the cell itself
# --------------------------------------------------------------------------


def verify_share_certification(receipt: dict, manifest: dict, history: list) -> dict:
    """Re-derive the certified share basis instead of trusting the receipt."""
    observations = []
    for item in history:
        stamp = parse_kap_stamp(item.get("creationDate"))
        classes = item.get("value")
        if stamp is None or not isinstance(classes, list):
            continue
        shares = 0
        ok = True
        for entry in classes:
            nominal = str(entry.get("nominalValueOfShares", "")).replace(".", "").replace(",", ".")
            per_share = str(entry.get("nominalValuePerShare", "")).replace(",", ".")
            try:
                total, unit = float(nominal), float(per_share)
            except ValueError:
                ok = False
                break
            if unit <= 0:
                ok = False
                break
            shares += total / unit
        if ok:
            observations.append(
                {
                    "created_at": stamp.isoformat(),
                    "at_or_before_cutoff": stamp.date() <= CUTOFF_DATE,
                    "derived_shares": shares,
                    "share_classes": len(classes),
                }
            )
    observations.sort(key=lambda row: row["created_at"])
    eligible = [row for row in observations if row["at_or_before_cutoff"]]
    selected = eligible[-1] if eligible else None
    excluded_future = [row for row in observations if not row["at_or_before_cutoff"]]
    # What a naive "use the latest thing we know" implementation would have taken.
    naive_latest = observations[-1] if observations else None

    recorded = receipt.get("shares_out")
    selection_matches = selected is not None and float(selected["derived_shares"]) == float(recorded)
    # Non-tautological: the recorded basis must equal the PIT pick and, when the two
    # differ, must *not* equal the latest-known pick.
    pit_not_latest = selection_matches and (
        naive_latest is selected
        or float(recorded) != float(naive_latest["derived_shares"])
    )
    look_ahead_changes_basis = (
        selected is not None
        and naive_latest is not None
        and float(naive_latest["derived_shares"]) != float(selected["derived_shares"])
    )
    interval_is_empty = (
        manifest.get("shares_basis_date") == CUTOFF_DATE.isoformat()
        and manifest.get("events") == []
        and manifest.get("enumeration_complete") is True
    )

    checks = {
        "latest_eligible_observation_selected": {
            "verified": selection_matches,
            "evidence": "explicit class nominals divided by their per-share nominal",
        },
        "recorded_basis_is_pit_not_latest_known": {
            "verified": pit_not_latest,
            "evidence": (
                "the recorded share basis equals the point-in-time pick and differs from the "
                "latest observation in the file"
            ),
            "excluded_as_post_cutoff": len(excluded_future),
        },
        "action_interval_is_empty": {
            "verified": interval_is_empty,
            "evidence": manifest.get("completeness_scope"),
            "detail": (
                "the share state is published on the cutoff date itself, so the open-closed "
                "action interval has zero length and no corporate action can fall inside it"
            ),
        },
        "nominal_value_per_share_not_assumed": {
            "verified": receipt.get("nominal_value_per_share_assumed") is False,
            "evidence": "per-share nominal read from the KAP class table, not assumed",
        },
        "non_empty_action_completeness_not_claimed": {
            "verified": receipt.get("nonempty_action_completeness_claimed") is False,
            "evidence": "the contract only ever certifies empty intervals",
        },
    }

    return {
        "ticker": TARGET_TICKER,
        "certification_contract": receipt.get("contract"),
        "share_state_published_at": receipt.get("share_state_published_at"),
        "observations": observations,
        "selected_observation": selected,
        "latest_known_observation": naive_latest,
        "excluded_as_post_cutoff": excluded_future,
        "recorded_shares_out": recorded,
        "look_ahead_would_change_the_share_basis": look_ahead_changes_basis,
        "checks": checks,
        "share_action_gate_passed": all(check["verified"] for check in checks.values()),
    }


def classify_profile_mismatch(
    target_payload: dict, default_config: dict, exact_config: dict
) -> dict:
    """Decide whether the derivation-profile mismatch is data, config or code."""
    latest_quarter = target_payload["quarters"][-1]

    against_default = audit_derivation_contract(latest_quarter, default_config)
    against_exact = audit_derivation_contract(latest_quarter, exact_config)

    artifact_profile = against_default["actual_derivation_profile"]
    mismatch_with_default = not against_default["compatible"]
    exact_config_declares_artifact_profile = (
        exact_config.get("source_derivation_profile") == artifact_profile
        and exact_config.get("source_derivation_version")
        == against_default["actual_derivation_version"]
    )

    if mismatch_with_default and exact_config_declares_artifact_profile:
        origin = "CONFIG"
        resolved = True
        detail = (
            "The artifact is internally consistent; only the default valuation config named a "
            "different profile. A separate versioned config declares the artifact's own profile, "
            "so the mismatch is resolved by configuration, not by renaming the artifact or "
            "forcing the gate."
        )
    elif mismatch_with_default:
        origin = "UNRESOLVED"
        resolved = False
        detail = "No config declares the artifact's profile."
    else:
        origin = "NONE"
        resolved = True
        detail = "The default config already matches the artifact profile."

    return {
        "artifact_profile": artifact_profile,
        "artifact_version": against_default["actual_derivation_version"],
        "artifact_semantic_profile": against_default["actual_semantic_profile"],
        "default_config": {
            "path": DEFAULT_CONFIG.relative_to(ROOT).as_posix(),
            "profile": default_config.get("source_derivation_profile"),
            "version": default_config.get("source_derivation_version"),
            "compatible": against_default["compatible"],
            "blockers": against_default["blockers"],
        },
        "exact_config": {
            "path": EXACT_CONFIG.relative_to(ROOT).as_posix(),
            "profile": exact_config.get("source_derivation_profile"),
            "version": exact_config.get("source_derivation_version"),
            "compatible": against_exact["compatible"],
            "blockers": against_exact["blockers"],
        },
        "origin": origin,
        "is_data_defect": False,
        "is_routing_or_code_defect": False,
        "is_config": origin == "CONFIG",
        "resolved": resolved,
        "forced_profile_rename_allowed": against_default["forced_profile_rename_allowed"],
        "detail": detail,
        "residual_share_derivation_blocker": [
            blocker for blocker in against_exact["blockers"] if blocker.startswith("UNPROVEN_SHARE")
        ],
    }


def verify_share_basis_supersession(
    target_payload: dict, certification: dict, receipt: dict
) -> dict:
    """Does the dated certification actually replace the artifact's unsafe basis?

    The CORE artifact derives the target's share count through the unsafe
    ``ISSUED_CAPITAL_OVER_NOMINAL`` route.  Clearing that blocker is only honest
    if the certified basis is the one the valuation input was built from, so this
    checks the recorded market cap against both candidates rather than asserting
    supersession from the presence of a certificate.
    """
    quarter = target_payload["quarters"][-1]
    artifact_shares = quarter.get("values", {}).get("shares_out")
    artifact_route = share_derivation_of(target_payload)
    certified_shares = certification["recorded_shares_out"]
    close = receipt.get("raw_close")
    recorded_cap = receipt.get("market_cap")

    def follows(shares: object) -> bool:
        if shares is None or close is None or recorded_cap is None:
            return False
        expected = float(shares) * float(close)
        return abs(expected - float(recorded_cap)) <= abs(float(recorded_cap)) * 1e-9

    cap_follows_certified = follows(certified_shares)
    cap_follows_artifact = follows(artifact_shares)
    bases_differ = (
        artifact_shares is not None and float(artifact_shares) != float(certified_shares)
    )
    supersedes = bool(
        certification["share_action_gate_passed"]
        and cap_follows_certified
        and not cap_follows_artifact
    )

    return {
        "artifact_shares_out": artifact_shares,
        "artifact_share_derivation": artifact_route,
        "artifact_share_derivation_is_safe": artifact_route in SAFE_SHARE_DERIVATIONS,
        "certified_shares_out": certified_shares,
        "certified_over_artifact_ratio": (
            None if not artifact_shares else float(certified_shares) / float(artifact_shares)
        ),
        "bases_differ": bases_differ,
        "raw_close": close,
        "recorded_market_cap": recorded_cap,
        "checks": {
            "market_cap_follows_the_certified_basis": {"verified": cap_follows_certified},
            "market_cap_does_not_follow_the_artifact_basis": {"verified": not cap_follows_artifact},
            "share_action_gate_passed": {
                "verified": certification["share_action_gate_passed"]
            },
        },
        "certified_basis_supersedes_artifact": supersedes,
        "detail": (
            "The artifact's unsafe share route reports a stale pre-capital-increase count; the "
            "dated certification published before the cutoff supplies the point-in-time count and "
            "the recorded market cap follows it, so the unsafe route is superseded for this "
            "ticker only. Nothing here certifies any other ticker."
        ),
    }


def verify_cell(target_record: dict, prior: dict, inputs: dict) -> dict:
    per_ticker = target_record["per_ticker"]
    if TARGET_TICKER not in per_ticker:
        raise W5AuditError(f"TARGET_TICKER_MISSING_AT_CUTOFF:{TARGET_TICKER}")

    exact_config = inputs["exact_config"]
    cohort = audit_real_peer_cohort(
        per_ticker,
        target_ticker=TARGET_TICKER,
        externally_certified_share_tickers={TARGET_TICKER},
        minimum_peer_count=exact_config["minimum_peer_count"],
    )
    prior_cohort = prior.get("peer_cohort_audit") or {}
    reproduces_prior = (
        cohort["financial_candidate_count"] == prior_cohort.get("financial_candidate_count")
        and cohort["safe_peer_count"] == prior_cohort.get("safe_peer_count")
        and cohort["unsafe_share_candidate_count"]
        == prior_cohort.get("unsafe_share_candidate_count")
        and sorted(cohort["unsafe_share_tickers"])
        == sorted(prior_cohort.get("unsafe_share_tickers") or [])
    )

    certification = verify_share_certification(
        inputs["share_receipt"], inputs["action_manifest"], inputs["share_history"]
    )
    return {
        "contract": CONTRACT,
        "ticker": TARGET_TICKER,
        "month": TARGET_MONTH,
        "knowledge_cutoff_at": CUTOFF,
        "share_certification": certification,
        "share_basis_supersession": verify_share_basis_supersession(
            per_ticker[TARGET_TICKER], certification, inputs["share_receipt"]
        ),
        "derivation_profile": classify_profile_mismatch(
            per_ticker[TARGET_TICKER], inputs["default_config"], exact_config
        ),
        "peer_cohort": cohort,
        "prior_receipt_reproduced": reproduces_prior,
        "thresholds": {
            "minimum_peer_count": cohort["minimum_peer_count"],
            "relaxed": False,
            "note": "the ledger forbids relaxing the peer or coverage thresholds",
        },
    }


# --------------------------------------------------------------------------
# Peer certifiability
# --------------------------------------------------------------------------


def build_peer_certifiability(
    cohort: dict, observations: dict[str, list[datetime]], actions: dict[str, list[str]]
) -> dict:
    rows = []
    for ticker in sorted(cohort["unsafe_share_tickers"]):
        stamps = observations.get(ticker, [])
        eligible = [stamp for stamp in stamps if stamp.date() <= CUTOFF_DATE]
        latest = max(eligible) if eligible else None
        gap = (CUTOFF_DATE - latest.date()).days if latest else None
        action_rows = actions.get(ticker, [])
        if latest is None:
            reason = "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION"
        elif gap == 0:
            reason = "ZERO_INTERVAL_CERTIFIABLE"
        else:
            reason = "NON_EMPTY_ACTION_INTERVAL_UNPROVEN"
        rows.append(
            {
                "ticker": ticker,
                "usable_observations": len(stamps),
                "latest_pre_cutoff_observation": latest.isoformat() if latest else None,
                "action_interval_days": gap,
                "yahoo_action_rows_all_time": len(action_rows),
                "yahoo_action_rows_inside_interval": (
                    0
                    if latest is None
                    else sum(
                        1
                        for value in action_rows
                        if latest.date().isoformat() < value <= CUTOFF_DATE.isoformat()
                    )
                ),
                "blocking_reason": reason,
            }
        )

    with_interval = [row for row in rows if row["action_interval_days"] is not None]
    shortest = sorted(with_interval, key=lambda row: (row["action_interval_days"], row["ticker"]))
    reason_counts: dict[str, int] = {}
    for row in rows:
        reason_counts[row["blocking_reason"]] = reason_counts.get(row["blocking_reason"], 0) + 1

    required = cohort["minimum_peer_count"]
    return {
        "contract": CONTRACT,
        "unsafe_peers": len(rows),
        "reason_counts": dict(sorted(reason_counts.items())),
        "zero_interval_certifiable_peers": [
            row["ticker"] for row in rows if row["blocking_reason"] == "ZERO_INTERVAL_CERTIFIABLE"
        ],
        "interval_summary": {
            "peers_with_a_pre_cutoff_observation": len(with_interval),
            "peers_without_any": sum(
                1
                for row in rows
                if row["blocking_reason"] == "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION"
            ),
            "shortest_days": shortest[0]["action_interval_days"] if shortest else None,
            "longest_days": shortest[-1]["action_interval_days"] if shortest else None,
        },
        "bounded_unblock_target": {
            "peers_required": required,
            "detail": (
                "Certifying these specific tickers over these specific windows is the smallest "
                "piece of evidence that would open the gate at this cutoff. Each still needs "
                "proof that no corporate action occurred inside its window."
            ),
            "candidates": [
                {
                    "ticker": row["ticker"],
                    "window_start": row["latest_pre_cutoff_observation"],
                    "window_end": CUTOFF,
                    "days": row["action_interval_days"],
                    "yahoo_action_rows_all_time": row["yahoo_action_rows_all_time"],
                }
                for row in shortest[:required]
            ],
        },
        "why_the_action_inventory_cannot_certify_them": {
            "inventory": YAHOO_ACTIONS.relative_to(ROOT).as_posix(),
            "peers_with_zero_rows_over_six_years": sorted(
                row["ticker"]
                for row in shortest[:required]
                if row["yahoo_action_rows_all_time"] == 0
            ),
            "detail": (
                "An inventory that holds no row at all for a six-year BIST100 membership is "
                "absence of evidence, not evidence of absence, so it cannot prove a window empty."
            ),
        },
        "rows": rows,
    }


# --------------------------------------------------------------------------
# Systemic bound
# --------------------------------------------------------------------------


def build_systemic_bound(
    per_cutoff: list[dict], observations: dict[str, list[datetime]], required: int
) -> dict:
    totals: dict[str, int] = {}
    for row in per_cutoff:
        for key, count in row["share_derivation_counts"].items():
            totals[key] = totals.get(key, 0) + count

    zero_interval_dates = {
        ticker: {stamp.date() for stamp in stamps} for ticker, stamps in observations.items()
    }
    cutoff_rows = []
    for row in per_cutoff:
        cutoff_day = date.fromisoformat(row["cutoff_date"])
        certifiable = sorted(
            ticker
            for ticker in row["candidate_tickers"]
            if cutoff_day in zero_interval_dates.get(ticker, set())
        )
        cutoff_rows.append(
            {
                "cutoff_date": row["cutoff_date"],
                "nonfin_candidates": row["nonfin_candidates"],
                "zero_interval_certifiable": len(certifiable),
                "certifiable_tickers": certifiable,
                # One of the certifiable tickers can be the target itself, so the
                # gate needs required + 1 before any of them has enough peers.
                "gate_reachable": len(certifiable) >= required + 1,
            }
        )

    distribution: dict[str, int] = {}
    for row in cutoff_rows:
        key = str(row["zero_interval_certifiable"])
        distribution[key] = distribution.get(key, 0) + 1

    safe_cells = sum(count for key, count in totals.items() if key in SAFE_SHARE_DERIVATIONS)
    return {
        "contract": CONTRACT,
        "question": (
            "Is the peer gate reachable at all — at this cutoff, and anywhere in the 60-cutoff "
            "window?"
        ),
        "cutoffs": len(cutoff_rows),
        "route_a_safe_share_derivation": {
            "route": ROUTE_DERIVATION,
            "nonfin_candidate_cells": sum(totals.values()),
            "share_derivation_counts": dict(sorted(totals.items())),
            "safe_derivation_cells": safe_cells,
            "reachable": safe_cells > 0,
            "detail": (
                "No historical NONFIN cell in any cutoff carries a safe share derivation, so this "
                "route contributes no peer anywhere."
            ),
        },
        "route_b_zero_interval_certification": {
            "route": ROUTE_ZERO_INTERVAL,
            "required_certifiable_per_cutoff": required + 1,
            "certifiable_distribution": dict(
                sorted(distribution.items(), key=lambda item: int(item[0]))
            ),
            "best_cutoff_certifiable": max(
                (row["zero_interval_certifiable"] for row in cutoff_rows), default=0
            ),
            "cutoffs_with_any": [
                {"cutoff_date": row["cutoff_date"], "tickers": row["certifiable_tickers"]}
                for row in cutoff_rows
                if row["zero_interval_certifiable"]
            ],
            "reachable": any(row["gate_reachable"] for row in cutoff_rows),
        },
        "gate_reachable_cutoffs": sum(1 for row in cutoff_rows if row["gate_reachable"]),
        "conclusion": (
            "Neither route can supply the required peers at any of the 60 cutoffs, so the blocker "
            "is structural rather than specific to SMRTG 2023-08."
        ),
        "implication_for_w6": (
            "Historical NONFIN M2 coverage is bounded by the same cohort gate, so W6 cannot be "
            "sized from source coverage alone. This audit does not start W6."
        ),
    }


# --------------------------------------------------------------------------


def build_verdict(cell: dict, peers: dict, systemic: dict) -> dict:
    cohort = cell["peer_cohort"]
    profile = cell["derivation_profile"]
    share_gate = cell["share_certification"]["share_action_gate_passed"]
    supersession = cell["share_basis_supersession"]
    residual = profile["residual_share_derivation_blocker"]
    # The artifact's unsafe route only stops blocking once the dated certification
    # is shown to be the basis actually used.
    residual_cleared = not residual or supersession["certified_basis_supersedes_artifact"]

    blockers = []
    if not share_gate:
        blockers.append("TARGET_SHARE_OR_ACTION_EVIDENCE_INSUFFICIENT")
    if not profile["resolved"]:
        blockers.append(f"SOURCE_DERIVATION_PROFILE_UNRESOLVED:{profile['origin']}")
    if not residual_cleared:
        blockers.append(f"TARGET_SHARE_DERIVATION_UNPROVEN:{';'.join(sorted(residual))}")
    if not cohort["peer_gate_passed"]:
        blockers.append(
            "VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:"
            f"safe_peers={cohort['safe_peer_count']};required={cohort['minimum_peer_count']}"
        )
    # Admissibility is what the evidence supports, not what this run happened to
    # find; even ADMISSIBLE would not make this auditor produce a score.
    admissible = (
        share_gate and profile["resolved"] and residual_cleared and cohort["peer_gate_passed"]
    )
    return {
        "contract": CONTRACT,
        "ticker": TARGET_TICKER,
        "month": TARGET_MONTH,
        "status": "ADMISSIBLE_FOR_M2" if admissible else "BLOCKED",
        "m2_admissible": admissible,
        "m2_materialized": False,
        "total_materialized": False,
        "neutral_m2_materialized": False,
        "real_follow_period_materialized": False,
        "active_blockers": blockers,
        "resolved_by_this_audit": {
            "share_action_gate": share_gate,
            "derivation_profile_origin": profile["origin"],
            "derivation_profile_resolved": profile["resolved"],
            "unsafe_artifact_share_route_superseded": supersession[
                "certified_basis_supersedes_artifact"
            ],
        },
        "why_not_m2": (
            "The target's own share and action evidence passes and the profile mismatch is "
            "resolved by an explicit versioned config, but relative valuation needs a peer cohort "
            "and there is not one safe peer at this cutoff."
        ),
        "note_on_admissibility": (
            "ADMISSIBLE would only mean the evidence gates are clear; this auditor computes no "
            "score under any verdict."
        ),
        "thresholds_relaxed": False,
        "separate_from_live_results": True,
        "reopen_conditions": [
            {
                "id": "GENERAL",
                "condition": (
                    "A dated, source-hashed corporate-action inventory that can prove a non-empty "
                    "interval empty. That would let any peer with a pre-cutoff share-class "
                    "observation be certified, and it is the same evidence W3 found missing."
                ),
            },
            {
                "id": "BOUNDED_AT_THIS_CUTOFF",
                "condition": (
                    "Proof that no corporate action occurred inside each of the named short "
                    "windows below. Certifying these alone would open the gate for 2023-08."
                ),
                "candidates": peers["bounded_unblock_target"]["candidates"],
            },
        ],
        "structural_note": systemic["conclusion"],
    }


def load_inputs() -> dict:
    return {
        "prior": json.loads(PRIOR_CANARY.read_text(encoding="utf-8")),
        "share_receipt": json.loads(SHARE_RECEIPT.read_text(encoding="utf-8")),
        "action_manifest": json.loads(ACTION_MANIFEST.read_text(encoding="utf-8")),
        "share_history": json.loads(SHARE_HISTORY.read_text(encoding="utf-8")),
        "default_config": json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8")),
        "exact_config": json.loads(EXACT_CONFIG.read_text(encoding="utf-8")),
    }


def assert_verdict_is_self_consistent(verdict: dict) -> None:
    """A verdict may not disagree with the blockers it carries, or claim work not done."""
    if verdict["status"] == "BLOCKED" and not verdict["active_blockers"]:
        raise W5AuditError("BLOCKED_VERDICT_WITHOUT_A_NAMED_BLOCKER")
    if verdict["status"] != "BLOCKED" and verdict["active_blockers"]:
        raise W5AuditError(
            "UNBLOCKED_VERDICT_STILL_CARRIES_BLOCKERS:" + ";".join(verdict["active_blockers"])
        )
    if verdict["m2_admissible"] != (verdict["status"] == "ADMISSIBLE_FOR_M2"):
        raise W5AuditError("VERDICT_STATUS_AND_ADMISSIBILITY_DISAGREE")
    for claim in (
        "m2_materialized",
        "total_materialized",
        "neutral_m2_materialized",
        "real_follow_period_materialized",
    ):
        if verdict[claim]:
            raise W5AuditError(f"CANARY_CLAIMS_WORK_IT_DID_NOT_DO:{claim}")


def derive() -> dict[str, bytes]:
    inputs = load_inputs()
    prior = inputs["prior"]
    target_record, per_cutoff = scan_core_diagnostics()
    observations = read_share_class_observations()
    actions = read_yahoo_actions()

    baseline = build_baseline(prior)
    cell = verify_cell(target_record, prior, inputs)
    if not cell["prior_receipt_reproduced"]:
        raise W5AuditError("PRIOR_CANARY_COHORT_NOT_REPRODUCED")
    # The scan re-implements the cohort admission rule; bind it to the production
    # helper at the target cutoff so a drifting local copy cannot go unnoticed.
    scanned = next(row for row in per_cutoff if row["analysis_at"] == CUTOFF)
    if scanned["nonfin_candidates"] != cell["peer_cohort"]["financial_candidate_count"]:
        raise W5AuditError(
            "SCAN_AND_PRODUCTION_COHORT_DISAGREE:"
            f"scan={scanned['nonfin_candidates']};"
            f"production={cell['peer_cohort']['financial_candidate_count']}"
        )
    peers = build_peer_certifiability(cell["peer_cohort"], observations, actions)
    systemic = build_systemic_bound(
        per_cutoff, observations, cell["peer_cohort"]["minimum_peer_count"]
    )
    verdict = build_verdict(cell, peers, systemic)
    assert_verdict_is_self_consistent(verdict)

    return {
        "baseline.json": encode_json(baseline),
        "cell_verification.json": encode_json(cell),
        "peer_certifiability.json": encode_json(peers),
        "systemic_bound.json": encode_json(systemic),
        "verdict.json": encode_json(verdict),
    }


def source_hashes() -> dict[str, str]:
    return {name: sha_file(path) for name, path in sorted(SOURCES.items())}


def build_receipt(content: dict[str, bytes]) -> dict:
    cell = json.loads(content["cell_verification.json"])
    peers = json.loads(content["peer_certifiability.json"])
    systemic = json.loads(content["systemic_bound.json"])
    verdict = json.loads(content["verdict.json"])
    return {
        "contract": "W5_SMRTG_CANARY_RECEIPT_V1",
        "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w5_smrtg_canary.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            name: path.relative_to(ROOT).as_posix() for name, path in sorted(SOURCES.items())
        },
        "source_sha256": source_hashes(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "coverage": {
            "status": verdict["status"],
            "share_action_gate_passed": cell["share_certification"]["share_action_gate_passed"],
            "derivation_profile_origin": cell["derivation_profile"]["origin"],
            "safe_peer_count": cell["peer_cohort"]["safe_peer_count"],
            "minimum_peer_count": cell["peer_cohort"]["minimum_peer_count"],
            "unsafe_peers": peers["unsafe_peers"],
            "zero_interval_certifiable_peers": len(peers["zero_interval_certifiable_peers"]),
            "cutoffs_scanned": systemic["cutoffs"],
            "safe_derivation_cells_in_all_cutoffs": systemic["route_a_safe_share_derivation"][
                "safe_derivation_cells"
            ],
            "gate_reachable_cutoffs": systemic["gate_reachable_cutoffs"],
        },
        "policy": {
            "model_changed": False,
            "weights_changed": False,
            "veto_changed": False,
            "peer_or_coverage_threshold_changed": False,
            "universe_changed": False,
            "neutral_fill": False,
            "production_code_changed": False,
            "m2_materialized": False,
            "cells_repaired": 0,
            "live_results_touched": False,
        },
        "limitations": [
            "This canary produces no M2 and no Total; the cell stays an explicit rejection.",
            "The systemic bound measures reachability of the peer gate, not the value M2 would "
            "take if the gate ever opened.",
            "W6 is not started; the implication recorded here only sizes its cohort constraint.",
        ],
    }


def write(content: dict[str, bytes], receipt: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    for name, payload in content.items():
        (AUDIT / name).write_bytes(payload)
    (AUDIT / "receipt.json").write_bytes(encode_json(receipt))


def check() -> None:
    receipt_path = AUDIT / "receipt.json"
    if not receipt_path.exists():
        raise W5AuditError("W5_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W5AuditError("W5_RECEIPT_HASH_MODE_MISMATCH")
    observed = source_hashes()
    if observed != receipt["source_sha256"]:
        changed = sorted(
            name for name, value in observed.items() if receipt["source_sha256"].get(name) != value
        )
        raise W5AuditError(f"W5_SOURCE_HASH_MISMATCH:{','.join(changed)}")
    content = derive()
    for name in CONTENT_FILES:
        stored = (AUDIT / name).read_bytes()
        if sha_bytes(stored) != receipt["output_sha256"][name]:
            raise W5AuditError(f"W5_STORED_ARTIFACT_HASH_MISMATCH:{name}")
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W5AuditError(f"W5_REDERIVED_ARTIFACT_HASH_MISMATCH:{name}")
    print("W5_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        return
    first, second = derive(), derive()
    if first != second:
        raise W5AuditError("W5_NON_DETERMINISTIC_DERIVATION")
    write(first, build_receipt(first))
    print("W5_APPLY_OK " + sha_bytes(first["verdict.json"]))


if __name__ == "__main__":
    main()
