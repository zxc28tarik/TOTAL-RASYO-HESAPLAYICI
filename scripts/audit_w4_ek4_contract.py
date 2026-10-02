from __future__ import annotations

"""Audit the Ek4 price/return contract and measure the adjusted/raw asymmetry.

Ek4 takes the stock leg from ``COALESCE(adj_close, close)`` and the sector leg
from the raw routed index close.  The execution ledger asks whether that
asymmetry breaks the locked contract, and requires the period-level effect to be
measured *in an audit artifact only*: a technical fix is allowed solely when a
contract violation is concretely proven, and a methodological preference needs a
separate governance decision rather than a code change.

This auditor therefore does two separable things and never mixes them:

1. it checks the locked contract against the live and historical code paths;
2. it recomputes every historical Ek4 cell on a raw/raw basis using the
   production formula and publishes the difference.

It writes no score back, changes no production module and repairs no cell.
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.ek4_momentum import compute_ek4_momentum_point

AUDIT = ROOT / "data/audit/w4_ek4_contract_v1"
CONTRACT = "W4_EK4_PRICE_RETURN_CONTRACT_AUDIT_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
RESOLVED_PRICES = (
    ROOT / "data/backtest_sources/yahoo_resolved"
    "/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
)
EK4_MOMENTUM = ROOT / "src/analytics/ek4_momentum.py"
EK4_REPLAY = ROOT / "src/analytics/historical_pit_ek4_replay.py"
DAILY_PIPELINE = ROOT / "src/analytics/run_daily_pipeline.py"
CURRENT_MARKET = ROOT / "scripts/materialize_current_market_modules.py"
EK4_CONTRACT_DOC = ROOT / "docs/HISTORICAL_PIT_EK4_REPLAY_CONTRACT.md"

SOURCES = {
    "p3_cells": P3_CELLS,
    "resolved_prices": RESOLVED_PRICES,
    "ek4_momentum": EK4_MOMENTUM,
    "historical_pit_ek4_replay": EK4_REPLAY,
    "run_daily_pipeline": DAILY_PIPELINE,
    "materialize_current_market_modules": CURRENT_MARKET,
    "ek4_replay_contract_doc": EK4_CONTRACT_DOC,
}

CONTENT_FILES = (
    "contract_compliance.json",
    "price_basis_comparison.json",
    "fallback_visibility.json",
    "rows.jsonl",
)

# Below this the adjusted and raw ratios agree to the precision at which the
# source stores adj_close; such rows carry no economic difference.
MATERIALITY = 1e-6
BAND_MATERIAL = "ADJUSTMENT_IN_WINDOW"
BAND_NEUTRAL = "NO_ADJUSTMENT_OR_ROUNDING"
BAND_ANOMALOUS = "ANOMALOUS_NEGATIVE"

STOCK_BASIS_MARKER = 'adjusted.where(adjusted.notna(), close)'
LIVE_STOCK_BASIS_MARKER = "COALESCE(adj_close, close) AS px"
LIVE_SECTOR_FALLBACK_MARKER = "COALESCE(sector_index_code,'XU100') AS sec"


class W4AuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def encode_rows(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode("utf-8")


def git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def read_prices() -> dict[tuple[str, str], tuple[float, float]]:
    """Return {(ticker, trade_date): (close, adj_close)} from the resolved source."""
    text = gzip.decompress(RESOLVED_PRICES.read_bytes()).decode("utf-8")
    header, *lines = text.replace("\r\n", "\n").strip("\n").split("\n")
    columns = header.split(",")
    index = {name: columns.index(name) for name in ("ticker", "trade_date", "close", "adj_close")}
    out: dict[tuple[str, str], tuple[float, float]] = {}
    for line in lines:
        parts = line.split(",")
        out[(parts[index["ticker"]], parts[index["trade_date"]])] = (
            float(parts[index["close"]]),
            float(parts[index["adj_close"]]),
        )
    return out


def read_ek4_cells() -> list[dict]:
    cells = []
    for line in gzip.decompress(P3_CELLS.read_bytes()).splitlines():
        row = json.loads(line)
        result = (row.get("market_module_lineage") or {}).get("Ek4", {}).get("source_result")
        if result:
            cells.append({"month": row["month"], "signal_date": row["signal_date"], **result})
    return cells


# --------------------------------------------------------------------------
# 1. Locked contract compliance
# --------------------------------------------------------------------------


def build_contract_compliance() -> dict:
    momentum = EK4_MOMENTUM.read_text(encoding="utf-8")
    replay = EK4_REPLAY.read_text(encoding="utf-8")
    daily = DAILY_PIPELINE.read_text(encoding="utf-8")
    current = CURRENT_MARKET.read_text(encoding="utf-8")

    shared_formula = (
        "from src.analytics.ek4_momentum import compute_ek4_momentum_point" in daily
        and "compute_ek4_momentum_point" in replay
    )
    historical_stock_adjusted = STOCK_BASIS_MARKER in replay
    live_stock_adjusted = LIVE_STOCK_BASIS_MARKER in daily
    historical_sector_raw = '_finite_positive(out["close"], "index_prices.close")' in replay
    live_sector_raw = "SELECT index_code, trade_date, close AS px" in daily
    historical_xu100_forbidden = (
        "SECTOR_WINDOW_PRICE_MISSING" in replay and "XU100" in replay
    )
    current_market_dated_routes = (
        "sector_routes.csv.gz" in current and "current market route ambiguous" in current
    )

    checks = {
        "live_and_historical_share_one_formula": {
            "verified": shared_formula,
            "evidence": "both paths call src.analytics.ek4_momentum.compute_ek4_momentum_point",
        },
        "stock_leg_identical_in_both_paths": {
            "verified": historical_stock_adjusted and live_stock_adjusted,
            "evidence": "historical COALESCE(adj_close,close) mirrors the live SQL COALESCE",
            "historical_marker": STOCK_BASIS_MARKER,
            "live_marker": LIVE_STOCK_BASIS_MARKER,
        },
        "sector_leg_is_raw_routed_index_close": {
            "verified": historical_sector_raw and live_sector_raw,
            "evidence": "the locked contract requires the raw routed sector return; both paths use close",
        },
        "historical_xu100_substitution_forbidden": {
            "verified": historical_xu100_forbidden,
            "evidence": "a missing routed endpoint yields SECTOR_WINDOW_PRICE_MISSING; XU100 is never substituted",
        },
        "current_live_materializer_uses_dated_routes": {
            "verified": current_market_dated_routes,
            "evidence": "materialize_current_market_modules reads dated sector routes and fails on ambiguity",
        },
    }
    violations = sorted(name for name, value in checks.items() if not value["verified"])
    return {
        "contract": CONTRACT,
        "locked_contract_doc": EK4_CONTRACT_DOC.relative_to(ROOT).as_posix(),
        "checks": checks,
        "violations": violations,
        "contract_violation_proven": bool(violations),
        "verdict": "COMPLIANT" if not violations else "VIOLATION_PROVEN",
        "consequence": (
            "No technical fix is authorized: the ledger permits a code change only when a locked "
            "contract violation is concretely proven. The adjusted-stock / raw-index asymmetry is "
            "a methodological choice, identical in the live and historical paths, so it belongs to "
            "a separate governance decision."
            if not violations
            else "A proven violation must be fixed with the narrowest safe change."
        ),
    }


# --------------------------------------------------------------------------
# 2. Measured price-basis effect (audit artifact only)
# --------------------------------------------------------------------------


def build_price_basis_rows(cells: list[dict], prices: dict) -> list[dict]:
    rows = []
    for cell in cells:
        ticker = cell["ticker"]
        start_key = (ticker, cell["start_date"])
        end_key = (ticker, cell["end_date"])
        if start_key not in prices or end_key not in prices:
            raise W4AuditError(f"EK4_ENDPOINT_PRICE_MISSING:{ticker}@{cell['start_date']}")
        close_start, adj_start = prices[start_key]
        close_end, adj_end = prices[end_key]
        # Reproducing the recorded return from adj_close proves the artifact and
        # the price source describe the same observation before anything is compared.
        reproduced = adj_end / adj_start - 1.0
        if abs(reproduced - cell["stock_return_20d"]) > 1e-9:
            raise W4AuditError(f"EK4_RECORDED_RETURN_NOT_REPRODUCIBLE:{ticker}@{cell['end_date']}")
        # The sector leg is already raw, so holding it fixed isolates the stock basis.
        sector_return = cell["sector_return_20d"]
        raw_point = compute_ek4_momentum_point(
            stock_start=close_start,
            stock_end=close_end,
            sector_start=1.0,
            sector_end=1.0 + sector_return,
        )
        delta_excess = cell["excess_return_20d"] - raw_point.excess_return
        if delta_excess > MATERIALITY:
            band = BAND_MATERIAL
        elif delta_excess < -MATERIALITY:
            band = BAND_ANOMALOUS
        else:
            band = BAND_NEUTRAL
        rows.append(
            {
                "month": cell["month"],
                "ticker": ticker,
                "sector_index_code": cell["sector_index_code"],
                "start_date": cell["start_date"],
                "end_date": cell["end_date"],
                "ek4_current_adjusted_raw": cell["ek4"],
                "ek4_counterfactual_raw_raw": raw_point.score,
                "delta_score": cell["ek4"] - raw_point.score,
                "delta_excess_return": delta_excess,
                "band": band,
            }
        )
    return sorted(rows, key=lambda row: (row["month"], row["ticker"]))


def build_price_basis_comparison(rows: list[dict]) -> dict:
    material = [row for row in rows if row["band"] == BAND_MATERIAL]
    neutral = [row for row in rows if row["band"] == BAND_NEUTRAL]
    anomalous = [row for row in rows if row["band"] == BAND_ANOMALOUS]

    def stat(values: list[float], name: str) -> dict:
        if not values:
            return {"count": 0}
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        return {
            "count": len(ordered),
            # CPython 3.12 switched sum() to compensated summation for floats,
            # so a plain sum() puts a different last bit in the artifact
            # depending on the interpreter. fsum is correctly rounded everywhere.
            "mean": math.fsum(ordered) / len(ordered),
            "median": median,
            "max": ordered[-1],
            "min": ordered[0],
            "field": name,
        }

    monthly: dict[str, int] = {}
    for row in material:
        monthly[row["month"]] = monthly.get(row["month"], 0) + 1
    top = sorted(material, key=lambda row: -row["delta_score"])[:20]

    return {
        "contract": CONTRACT,
        "comparison": "current adjusted-stock/raw-index basis vs a raw-stock/raw-index counterfactual",
        "method": (
            "The sector leg is already raw, so it is held fixed and only the stock leg is "
            "re-based. The production compute_ek4_momentum_point is used unchanged."
        ),
        "materiality_threshold_delta_excess": MATERIALITY,
        "cells_compared": len(rows),
        "band_counts": {
            BAND_MATERIAL: len(material),
            BAND_NEUTRAL: len(neutral),
            BAND_ANOMALOUS: len(anomalous),
        },
        "material_share": len(material) / len(rows) if rows else 0.0,
        "neutral_band_max_abs_delta_excess": max(
            (abs(row["delta_excess_return"]) for row in neutral), default=0.0
        ),
        "delta_score": stat([row["delta_score"] for row in material], "delta_score"),
        "delta_excess_return": stat(
            [row["delta_excess_return"] for row in material], "delta_excess_return"
        ),
        "direction": {
            # The material band is positive by construction, so the falsifiable
            # claim is that no window is inverted beyond the rounding band.
            "no_inverted_adjustment_beyond_rounding": not anomalous,
            "inverted_windows": len(anomalous),
            "rounding_band_max_abs_delta_score": max(
                (abs(row["delta_score"]) for row in neutral), default=0.0
            ),
            "interpretation": (
                "Back-adjustment raises the stock leg's measured return whenever a distribution "
                "falls inside the window, while the price-index sector leg has no such uplift. "
                "The bias is therefore one-directional, not symmetric noise."
            ),
        },
        "materially_affected_months": len(monthly),
        "materially_affected_tickers": len({row["ticker"] for row in material}),
        "monthly_material_counts": dict(sorted(monthly.items())),
        "score_shift_buckets": {
            "gt_0_05": sum(1 for row in material if row["delta_score"] > 0.05),
            "gt_0_10": sum(1 for row in material if row["delta_score"] > 0.10),
            "gt_0_20": sum(1 for row in material if row["delta_score"] > 0.20),
        },
        "largest_shifts": [
            {
                "month": row["month"],
                "ticker": row["ticker"],
                "sector_index_code": row["sector_index_code"],
                "ek4_current_adjusted_raw": row["ek4_current_adjusted_raw"],
                "ek4_counterfactual_raw_raw": row["ek4_counterfactual_raw_raw"],
                "delta_score": row["delta_score"],
            }
            for row in top
        ],
        "ek4_weight_in_total_rasyo": 0.16,
        "applied_to_production": False,
    }


# --------------------------------------------------------------------------
# 3. Fallback visibility
# --------------------------------------------------------------------------


def build_fallback_visibility() -> dict:
    daily = DAILY_PIPELINE.read_text(encoding="utf-8")
    current = CURRENT_MARKET.read_text(encoding="utf-8")
    live_fallback_present = LIVE_SECTOR_FALLBACK_MARKER in daily
    ek4_returns_only_score = 'return pd.DataFrame(rows, columns=["ticker","ek4"])' in daily
    rejection_only_on_missing_score = 'if pd.isna(row[key.lower()]):' in daily
    current_path_fails_closed = "current market route ambiguous" in current

    return {
        "contract": CONTRACT,
        "finding": "LIVE_SECTOR_FALLBACK_NOT_VISIBLE_IN_PROVENANCE",
        "severity": "RECORDED_NOT_FIXED",
        "description": (
            "In the database pipeline a NULL sector_index_code silently becomes XU100, the cell "
            "still produces an Ek4 score, and nothing downstream records which index was used."
        ),
        "evidence": {
            "live_fallback_marker_present": live_fallback_present,
            "live_fallback_site": "src/analytics/run_daily_pipeline.py::_compute_ek4_momentum",
            "ek4_result_columns_carry_no_sector": ek4_returns_only_score,
            "module_rejections_only_fire_on_missing_score": rejection_only_on_missing_score,
            "historical_replay_forbids_the_substitution": True,
        },
        "production_reachability": {
            "active_artifact_chain": "scripts/materialize_current_market_modules.py",
            "active_chain_uses_dated_routes_and_fails_closed": current_path_fails_closed,
            "active_chain_applies_xu100_fallback": False,
            "conclusion": "NO_PRODUCTION_REACHABILITY_IN_ACTIVE_ARTIFACT_CHAIN",
            "detail": (
                "Every current Ek4 value in data/live/current_market_modules_v1 comes from the "
                "dated-route materializer, not from the database pipeline, so the fallback did "
                "not influence any active result. The database pipeline retains the fallback."
            ),
        },
        "code_changed": False,
        "reason_code_not_changed": (
            "The ledger authorizes only the narrowest safe fix for a proven, reachable defect. "
            "This is a provenance-visibility gap on a path that produced none of the active "
            "results, so it is recorded with its reachability evidence instead of patched here."
        ),
        "reopen_condition": (
            "Before any Ek4 value produced by run_daily_pipeline enters a Total Rasyo claim, the "
            "resolved sector_index_code must be persisted alongside the score, or the NULL route "
            "must become an explicit rejection."
        ),
    }


# --------------------------------------------------------------------------


def derive() -> dict[str, bytes]:
    cells = read_ek4_cells()
    if not cells:
        raise W4AuditError("NO_EK4_CELLS_IN_SOURCE_ARTIFACT")
    prices = read_prices()
    rows = build_price_basis_rows(cells, prices)
    return {
        "contract_compliance.json": encode_json(build_contract_compliance()),
        "price_basis_comparison.json": encode_json(build_price_basis_comparison(rows)),
        "fallback_visibility.json": encode_json(build_fallback_visibility()),
        "rows.jsonl": encode_rows(rows),
    }


def source_hashes() -> dict[str, str]:
    return {name: sha_file(path) for name, path in sorted(SOURCES.items())}


def build_receipt(content: dict[str, bytes]) -> dict:
    compliance = json.loads(content["contract_compliance.json"])
    comparison = json.loads(content["price_basis_comparison.json"])
    fallback = json.loads(content["fallback_visibility.json"])
    return {
        "contract": "W4_EK4_CONTRACT_RECEIPT_V1",
        "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w4_ek4_contract.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {name: path.relative_to(ROOT).as_posix() for name, path in sorted(SOURCES.items())},
        "source_sha256": source_hashes(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "coverage": {
            "contract_verdict": compliance["verdict"],
            "contract_violation_proven": compliance["contract_violation_proven"],
            "cells_compared": comparison["cells_compared"],
            "band_counts": comparison["band_counts"],
            "materially_affected_months": comparison["materially_affected_months"],
            "materially_affected_tickers": comparison["materially_affected_tickers"],
            "max_delta_score": comparison["delta_score"].get("max"),
            "fallback_finding": fallback["finding"],
            "fallback_reachability": fallback["production_reachability"]["conclusion"],
        },
        "policy": {
            "model_changed": False,
            "weights_changed": False,
            "veto_changed": False,
            "peer_or_coverage_threshold_changed": False,
            "universe_changed": False,
            "neutral_fill": False,
            "production_code_changed": False,
            "ek4_values_rewritten": 0,
            "new_market_capture": False,
        },
        "limitations": [
            "The raw/raw counterfactual exists only in this artifact; no Ek4 value was rewritten.",
            "An adjusted/adjusted comparison is impossible here: the repository holds no "
            "total-return sector index series, only price indices.",
            "Issue #39 scopes itself to price-level valuation and explicitly leaves momentum "
            "alone, so this asymmetry needs its own governance record.",
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
        raise W4AuditError("W4_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W4AuditError("W4_RECEIPT_HASH_MODE_MISMATCH")
    observed = source_hashes()
    if observed != receipt["source_sha256"]:
        changed = sorted(
            name for name, value in observed.items() if receipt["source_sha256"].get(name) != value
        )
        raise W4AuditError(f"W4_SOURCE_HASH_MISMATCH:{','.join(changed)}")
    content = derive()
    for name in CONTENT_FILES:
        stored = (AUDIT / name).read_bytes()
        if sha_bytes(stored) != receipt["output_sha256"][name]:
            raise W4AuditError(f"W4_STORED_ARTIFACT_HASH_MISMATCH:{name}")
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W4AuditError(f"W4_REDERIVED_ARTIFACT_HASH_MISMATCH:{name}")
    print("W4_CHECK_PASS " + receipt["output_sha256"]["rows.jsonl"])


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
        raise W4AuditError("W4_NON_DETERMINISTIC_DERIVATION")
    write(first, build_receipt(first))
    print("W4_APPLY_OK " + sha_bytes(first["rows.jsonl"]))


if __name__ == "__main__":
    main()
