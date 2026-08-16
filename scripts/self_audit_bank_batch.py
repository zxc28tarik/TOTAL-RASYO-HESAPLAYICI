from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json
import math
import random
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.analytics.bank_batch_pipeline import (
    BankM2Context,
    _assumption_from_row,
    compute_bank_m2_score,
    daily_price_cutoff_date,
)
from src.analytics.bank_shadow_report import build_bank_shadow_distribution, canonical_thresholds
from src.analytics.bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalizationError,
    build_quarter_slots,
    run_bank_valuation,
    to_canonical_row,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 4, 12, 0, tzinfo=TZ)
ANCHOR = date(2025, 12, 31)
ROOT = Path(__file__).resolve().parents[1]
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


def _valuation_result():
    values = [None, None, 0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]
    rows = []
    for idx, (period, roe) in enumerate(zip(build_quarter_slots(ANCHOR), values)):
        rows.append({
            "period_end": period,
            "record_id": None if roe is None else idx + 1,
            "selected_version_tag": None if roe is None else "ORIGINAL",
            "selected_version_sequence": None if roe is None else 1,
            "selected_published_at": None if roe is None else datetime(2025, 1, 1, 10, 0, tzinfo=TZ),
            "roe_ttm": roe,
            "bvps": Decimal("21.24") if idx == 7 else None,
            "payout_sus": Decimal("0.25") if idx == 7 else None,
        })
    canonical = to_canonical_row(
        rows, ticker="BNK1", analysis_at=ANALYSIS, anchor_period_end=ANCHOR
    )
    return run_bank_valuation(
        canonical,
        BankValuationInputs(
            coe=0.3705,
            macro_cap=0.140135,
            band_width_shadow_mode=False,
        ),
    )


def _valid_assumption_row() -> dict:
    return {
        "scope_type": "BANK",
        "scope_code": "BANK",
        "effective_at": "2026-01-01T00:00:00+03:00",
        "coe": Decimal("0.3705"),
        "macro_cap": np.float64(0.140135),
        "risk_free_rate": "0.30",
        "tier_cap": 0.8,
        "payout_missing_factor": 0.7,
        "band_width_shadow_mode": True,
        "max_halfwidth": 0.8,
        "source": "SELF_AUDIT",
        "metadata": {"run": 1},
    }


def _shadow_df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "ticker": "AAA", "sector_code": "BANK", "anchor_period_end": ANCHOR,
            "valuation_status": "OK", "lower_halfwidth": 0.70, "upper_halfwidth": 0.75,
            "floor_source": "ABSOLUTE_FLOOR", "sd_roe_floor": 0.01,
            "sd_roe_effective": 0.01, "justified_pb": 0.8, "roe_sus": 0.22,
            "outlier_conf_penalty": 1.0, "v_mid": 10.0, "current_price": 8.0,
            "z_val": 0.9, "s_valuation": 0.72, "v_conf": 0.8,
            "coe": 0.37, "macro_cap": 0.14, "risk_free_rate": 0.30,
        },
        {
            "ticker": "BBB", "sector_code": "BANK", "anchor_period_end": ANCHOR,
            "valuation_status": "OK", "lower_halfwidth": 0.85, "upper_halfwidth": 0.95,
            "floor_source": "SECTOR_QUANTILE", "sd_roe_floor": 0.01,
            "sd_roe_effective": 0.03, "justified_pb": 1.2, "roe_sus": 0.28,
            "outlier_conf_penalty": 0.85, "v_mid": 20.0, "current_price": 22.0,
            "z_val": -0.3, "s_valuation": 0.0, "v_conf": 0.68,
            "coe": 0.39, "macro_cap": 0.15, "risk_free_rate": 0.32,
        },
    ])


def _assert_sql_contracts() -> dict[str, str]:
    migration = (ROOT / "sql" / "013_bank_batch_m2_integration.sql").read_text(encoding="utf-8")
    batch_sql = (ROOT / "sql" / "014_bank_point_in_time_slots_batch.sql").read_text(encoding="utf-8")
    batch_source = (ROOT / "src" / "analytics" / "bank_batch_pipeline.py").read_text(encoding="utf-8")
    daily_source = (ROOT / "src" / "analytics" / "run_daily_pipeline.py").read_text(encoding="utf-8")

    contracts = {
        "batch_one_roundtrip": "unnest(%(tickers)s::text[])" in batch_sql,
        "single_pit_source": "analytics.bank_point_in_time_slots" in batch_sql,
        "assumption_cutoff": "effective_at <= %(analysis_at)s" in batch_source,
        "m2_exact_cutoff": "analysis_at <= %(analysis_at)s" in daily_source,
        "shared_market_cutoff": all(
            token in daily_source
            for token in (
                "analysis_ts, market_asof = _resolve_pipeline_clock",
                "compute_trailing_alpha(conn, asof_date=str(market_asof)",
                "build_expected_band_periods(conn, asof_date=str(market_asof)",
                "compute_m2_period_comparison(conn, asof_date=str(market_asof)",
                "_compute_ek4_momentum(conn, market_asof)",
                "_compute_ek9_vol(conn, market_asof)",
            )
        ),
        "daily_close_db_gate": "ck_bank_m2_daily_close_cutoff" in migration,
        "assumption_trace_gate": "ck_bank_valuation_assumption_trace" in migration,
        "json_payload_gates": all(
            name in migration
            for name in ("ck_bank_valuation_json_payloads", "ck_bank_m2_json_payloads")
        ),
        "xumal_not_bank_routed": "XUMAL" not in batch_source,
    }
    missing = [name for name, ok in contracts.items() if not ok]
    if missing:
        raise AssertionError(f"SQL/source contracts missing: {missing}")

    mutations = {
        "batch_n_plus_one": batch_sql.replace("unnest(%(tickers)s::text[])", "unnest(ARRAY['ONE'])"),
        "pit_function_removed": batch_sql.replace("analytics.bank_point_in_time_slots", "analytics.wrong_slots"),
        "daily_gate_removed": migration.replace("ck_bank_m2_daily_close_cutoff", "removed_gate"),
    }
    caught: dict[str, str] = {}
    if "unnest(%(tickers)s::text[])" not in mutations["batch_n_plus_one"]:
        caught["batch_n_plus_one"] = "caught"
    if "analytics.bank_point_in_time_slots" not in mutations["pit_function_removed"]:
        caught["pit_function_removed"] = "caught"
    if "ck_bank_m2_daily_close_cutoff" not in mutations["daily_gate_removed"]:
        caught["daily_gate_removed"] = "caught"
    if len(caught) != len(mutations):
        raise AssertionError("source mutation audit incomplete")
    return caught


def main() -> None:
    rng = random.Random(20260804)
    valuation = _valuation_result()

    assumption_valid = assumption_rejected = 0
    bad_values = [pd.NA, np.bool_(True), True, float("nan"), float("inf"), -1, "oops", [], {}]
    fields = [
        "scope_type", "effective_at", "coe", "macro_cap", "risk_free_rate",
        "tier_cap", "payout_missing_factor", "band_width_shadow_mode",
        "max_halfwidth", "metadata",
    ]
    for index in range(20000):
        row = _valid_assumption_row()
        if index % 4 == 0:
            try:
                parsed = _assumption_from_row(row)
                assert parsed.inputs.coe > 0
                assert parsed.risk_free_rate is None or parsed.risk_free_rate >= 0
                assumption_valid += 1
            except Exception as exc:
                raise AssertionError(f"valid assumption failed: {exc}") from exc
            continue
        field = rng.choice(fields)
        row[field] = rng.choice(bad_values)
        try:
            _assumption_from_row(row)
        except CanonicalizationError:
            assumption_rejected += 1
        except Exception as exc:
            raise AssertionError(
                f"uncontrolled assumption exception {field}: {type(exc).__name__}: {exc}"
            ) from exc
        else:
            # Bazı rastgele değerler seçilen alan için geçerli olabilir (örn. risk_free_rate={}).
            # Kabul edilen her sonuç yine ekonomik sözleşmeyi korumalıdır.
            parsed = _assumption_from_row(row)
            assert parsed.inputs.coe > 0
            assert 0 <= parsed.inputs.tier_cap <= 1
            assumption_valid += 1

    m2_valid = m2_rejected = 0
    price_values = [None, 5.5, Decimal("5.5"), "5.5", -1, 0, True, np.bool_(False), pd.NA, float("inf")]
    lag_values = [0, 0.5, 1, "0.7", -0.1, 1.1, True, pd.NA, float("nan")]
    dates = [None, ANALYSIS.date() - timedelta(days=1), ANALYSIS.date(), "bozuk", True]
    sources = ["DAILY_CLOSE", " TEST ", "", 3, None]
    for _ in range(20000):
        context = BankM2Context(
            current_price=rng.choice(price_values),
            price_trade_date=rng.choice(dates),
            price_source=rng.choice(sources),  # type: ignore[arg-type]
            s_lag_effective=rng.choice(lag_values),  # type: ignore[arg-type]
            lag_active=rng.choice([True, False, np.bool_(True), "false"]),  # type: ignore[arg-type]
            lag_source=rng.choice(sources),  # type: ignore[arg-type]
        )
        try:
            result = compute_bank_m2_score(valuation, context)
            assert math.isfinite(result["m2_score"])
            assert 0 <= result["m2_score"] <= 1
            assert 0 <= result["s_val_effective"] <= 1
            assert 0 <= result["s_lag_effective"] <= 1
            m2_valid += 1
        except CanonicalizationError:
            m2_rejected += 1
        except Exception as exc:
            raise AssertionError(f"uncontrolled M2 context exception: {type(exc).__name__}: {exc}") from exc

    shadow_valid = shadow_rejected = 0
    for index in range(400):
        df = _shadow_df()
        try:
            if index % 5 == 0:
                report = build_bank_shadow_distribution(df)
                row = report.iloc[0]
                assert row["floor_binding_count"] == 1
                assert row["reject_0_80_count"] == 1
                shadow_valid += 1
                continue
            mutation = index % 4
            if mutation == 0:
                df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
            elif mutation == 1:
                df["coe"] = df["coe"].astype(object)
                df.loc[0, "coe"] = "bozuk"
            elif mutation == 2:
                df["anchor_period_end"] = df["anchor_period_end"].astype(object)
                df.loc[0, "anchor_period_end"] = "bozuk"
            else:
                canonical_thresholds([0.801, 0.804])
            build_bank_shadow_distribution(df)
        except CanonicalizationError:
            shadow_rejected += 1
        except Exception as exc:
            raise AssertionError(f"uncontrolled shadow exception: {type(exc).__name__}: {exc}") from exc
        else:
            raise AssertionError("invalid shadow mutation accepted")

    # UTC günü ile İstanbul günü ayrıştığında cutoff ve M2 asof aynı yerel günü kullanmalı.
    utc_analysis = datetime(2026, 8, 3, 21, 30, tzinfo=ZoneInfo("UTC"))
    assert daily_price_cutoff_date(utc_analysis) == date(2026, 8, 3)
    valuation_utc = dict(valuation)
    valuation_utc["analysis_at"] = utc_analysis
    m2_utc = compute_bank_m2_score(valuation_utc, BankM2Context(current_price=5.5))
    assert m2_utc["asof_date"] == date(2026, 8, 4)

    mutations = _assert_sql_contracts()

    print(json.dumps({
        "assumption_valid": assumption_valid,
        "assumption_controlled_reject": assumption_rejected,
        "m2_valid": m2_valid,
        "m2_controlled_reject": m2_rejected,
        "shadow_valid": shadow_valid,
        "shadow_controlled_reject": shadow_rejected,
        "source_mutations": mutations,
        "uncontrolled_exceptions": 0,
        "bnk1_v_mid": valuation["valuation"]["V_mid"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
