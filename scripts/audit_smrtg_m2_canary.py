from __future__ import annotations

import argparse
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.historical_pit_nonfin_m2_replay import (
    HistoricalPitNonfinM2ReplayError,
    run_historical_pit_nonfin_m2_replay,
)
from src.analytics.m2_canary_audit import audit_derivation_contract, audit_real_peer_cohort
from src.analytics.nonfin_valuation import NonfinValuationConfig
from src.analytics.price_level_action_evidence import PriceLevelActionEvidence
from src.analytics.price_level_adapter import PriceLevelActionBundle
from src.analytics.price_level_valuation_basis import PRICE_LEVEL_BASIS


CONTRACT = "SMRTG_HISTORICAL_M2_CANARY_AUDIT_V1"
DIAGNOSTICS = ROOT / "data/audit/experimental_materialization_v3/core_diagnostics.jsonl.gz"
SHARE_RECEIPT = ROOT / "data/backtest_sources/zero_interval_share_basis_v1/receipt.json"
VALUATION_CONFIG = ROOT / "config/nonfin_valuation.relative_v1.json"
EXACT_VALUATION_CONFIG = ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json"
ACTION_MANIFEST = ROOT / "data/backtest_sources/zero_interval_share_basis_v1/action_coverage_manifest.json"
ACTION_SOURCE = ROOT / "data/backtest_sources/zero_interval_share_basis_v1/smrtg_kap_share_history.json"
DEFAULT_OUTPUT = ROOT / "data/audit/smrtg_m2_canary_v1/receipt.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_month() -> dict:
    with gzip.open(DIAGNOSTICS, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("signal_date") == "2023-08-01":
                return row
    raise ValueError("SMRTG_CANARY_MONTH_NOT_FOUND")


def _production_replay(target: dict, share: dict, config: NonfinValuationConfig):
    quarters = []
    for raw in target["quarters"]:
        values = dict(raw["values"])
        values["shares_out"] = None
        values["shares_basis_date"] = raw["period_end"]
        if raw is target["quarters"][-1]:
            values["shares_out"] = share["shares_out"]
            values["shares_basis_date"] = share["price_trade_date"]
        quarters.append({
            "ticker": "SMRTG",
            "period_end": raw["period_end"],
            "published_at": raw["published_at"],
            "derivation_profile": raw["derivation_profile"],
            "derivation_version": raw["derivation_version"],
            **values,
        })
    manifest = ACTION_MANIFEST.read_bytes()
    source = ACTION_SOURCE.read_bytes()
    source_ref = "KAP_SMRTG_SHARE_HISTORY"
    bundle = PriceLevelActionBundle(
        PriceLevelActionEvidence(manifest, _sha(ACTION_MANIFEST), {source_ref: source}), ()
    )
    return run_historical_pit_nonfin_m2_replay(
        analysis_at=datetime.fromisoformat(share["knowledge_cutoff_at"]),
        universe=pd.DataFrame([{
            "ticker": "SMRTG", "peer_group": "UNVERIFIED", "sector_family": "NONFIN",
        }]),
        financials=pd.DataFrame(quarters),
        prices=pd.DataFrame([{
            "ticker": "SMRTG", "price_trade_date": share["price_trade_date"],
            "current_price": share["raw_close"], "price_basis": PRICE_LEVEL_BASIS,
            "action_bundle": bundle,
        }]),
        config=config,
    )


def audit(output: Path = DEFAULT_OUTPUT) -> dict:
    month = _load_month()
    target = month["per_ticker"]["SMRTG"]
    share = json.loads(SHARE_RECEIPT.read_text(encoding="utf-8"))
    config_raw = json.loads(VALUATION_CONFIG.read_text(encoding="utf-8"))
    config = NonfinValuationConfig.from_json_file(VALUATION_CONFIG)
    exact_config = NonfinValuationConfig.from_json_file(EXACT_VALUATION_CONFIG)
    contract_audit = audit_derivation_contract(target["quarters"][-1], config_raw)
    cohort = audit_real_peer_cohort(
        month["per_ticker"], target_ticker="SMRTG",
        externally_certified_share_tickers={"SMRTG"},
        minimum_peer_count=config.minimum_peer_count,
    )
    try:
        _production_replay(target, share, config)
    except HistoricalPitNonfinM2ReplayError as exc:
        replay_profile_error = str(exc)
    else:
        raise AssertionError("production replay accepted mismatched derivation profile")
    exact_replay = _production_replay(target, share, exact_config)
    observed_source_risks = list(contract_audit["blockers"])
    blockers: list[str] = []
    if not cohort["peer_gate_passed"]:
        blockers.append(
            f"VERIFIED_SHARE_PEER_COHORT_INSUFFICIENT:"
            f"safe_peers={cohort['safe_peer_count']};required={cohort['minimum_peer_count']}"
        )
    result = {
        "contract": CONTRACT,
        "ticker": "SMRTG",
        "month": "2023-08",
        "knowledge_cutoff_at": share["knowledge_cutoff_at"],
        "share_action_gate_passed": True,
        "shares_out": share["shares_out"],
        "nominal_value_per_share_assumed": False,
        "raw_close": share["raw_close"],
        "market_cap": share["market_cap"],
        "derivation_contract_audit": contract_audit,
        "peer_cohort_audit": cohort,
        "production_replay_invoked": True,
        "production_profile_gate_error": replay_profile_error,
        "versioned_exact_profile_config": str(EXACT_VALUATION_CONFIG.relative_to(ROOT)).replace("\\", "/"),
        "profile_mismatch_resolved_by_explicit_config": True,
        "profile_equivalence_claimed": False,
        "unsafe_financial_share_field_scrubbed": True,
        "certified_share_basis_injected_separately": True,
        "exact_profile_replay_m2_count": len(exact_replay.m2_scores),
        "exact_profile_replay_rejections": exact_replay.rejections.to_dict("records"),
        "m2_materialized": False,
        "neutral_m2_materialized": False,
        "total_materialized": False,
        "p4_materialized": False,
        "observed_source_risks": observed_source_risks,
        "active_blockers": blockers,
        "source_hashes": {
            str(DIAGNOSTICS.relative_to(ROOT)).replace("\\", "/"): _sha(DIAGNOSTICS),
            str(SHARE_RECEIPT.relative_to(ROOT)).replace("\\", "/"): _sha(SHARE_RECEIPT),
            str(VALUATION_CONFIG.relative_to(ROOT)).replace("\\", "/"): _sha(VALUATION_CONFIG),
            str(EXACT_VALUATION_CONFIG.relative_to(ROOT)).replace("\\", "/"): _sha(EXACT_VALUATION_CONFIG),
            str(ACTION_MANIFEST.relative_to(ROOT)).replace("\\", "/"): _sha(ACTION_MANIFEST),
            str(ACTION_SOURCE.relative_to(ROOT)).replace("\\", "/"): _sha(ACTION_SOURCE),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(audit(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
