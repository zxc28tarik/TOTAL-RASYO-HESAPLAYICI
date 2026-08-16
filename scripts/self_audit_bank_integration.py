from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json
import math
import random
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd

from src.analytics.bank_valuation_pipeline import (
    BankValuationInputs,
    CanonicalizationError,
    build_quarter_slots,
    compute_v_conf,
    run_bank_valuation,
    to_canonical_row,
)

TZ = ZoneInfo("Europe/Istanbul")
ANCHOR = date(2025, 12, 31)
ANALYSIS = datetime(2026, 3, 1, 12, 0, tzinfo=TZ)
BNK1 = [0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]


def _base_rows():
    rows = []
    values = [None, None] + BNK1
    for i, (slot, value) in enumerate(zip(build_quarter_slots(ANCHOR), values)):
        if value is None:
            rows.append({
                "period_end": slot,
                "record_id": None,
                "selected_version_tag": None,
                "selected_version_sequence": None,
                "selected_published_at": None,
                "roe_ttm": None,
                "bvps": None,
                "payout_sus": None,
            })
        else:
            rows.append({
                "period_end": slot,
                "record_id": i + 1,
                "selected_version_tag": "ORIGINAL",
                "selected_version_sequence": 1,
                "selected_published_at": datetime(2025, 1, 1, 10, 0, tzinfo=TZ),
                "roe_ttm": value,
                "bvps": 21.24 if i == 7 else None,
                "payout_sus": 0.25 if i == 7 else None,
            })
    return rows


def _canonical(rows):
    return to_canonical_row(
        rows,
        ticker="BNK1",
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
    )


def _sql_contract(sql: str) -> None:
    normalized = " ".join(sql.lower().split())
    required = [
        "generate_series(7, 0, -1)",
        "m.published_at <= p.analysis_at",
        "m.published_at desc, m.version_sequence desc, m.record_id desc",
        "left join selected",
        "order by s.period_end;",
    ]
    for item in required:
        if item not in normalized:
            raise AssertionError(f"SQL contract missing: {item}")
    forbidden = ["m.published_at::date", "order by s.period_end desc"]
    for item in forbidden:
        if item in normalized:
            raise AssertionError(f"SQL contract forbidden: {item}")


def main() -> None:
    rng = random.Random(20260804)
    canonical_valid = canonical_rejected = 0
    mutations = ["reverse", "future", "bool", "hidden", "wrong_count", "wrong_period"]
    for i in range(20000):
        rows = _base_rows()
        if i % 4 == 0:
            # Geçerli varyant: NumPy/Decimal/pandas eksiklerini kanonikleştir.
            rows[0]["roe_ttm"] = pd.NA
            rows[2]["roe_ttm"] = np.float64(rows[2]["roe_ttm"])
            try:
                c = _canonical(rows)
                assert all(v is None or type(v) is float for v in c.roe_series)
                canonical_valid += 1
            except Exception as exc:
                raise AssertionError(f"valid canonical case failed at {i}: {exc}") from exc
            continue

        mutation = mutations[i % len(mutations)]
        if mutation == "reverse":
            rows.reverse()
        elif mutation == "future":
            rows[-1]["selected_published_at"] = datetime(2027, 1, 1, tzinfo=TZ)
        elif mutation == "bool":
            rows[-1]["record_id"] = np.bool_(True)
        elif mutation == "hidden":
            rows[0]["roe_ttm"] = 0.2
        elif mutation == "wrong_count":
            rows.pop()
        elif mutation == "wrong_period":
            rows[0]["period_end"] = date(2023, 12, 31)
        try:
            _canonical(rows)
        except CanonicalizationError:
            canonical_rejected += 1
        except Exception as exc:
            raise AssertionError(
                f"uncontrolled canonical exception {mutation}: {type(exc).__name__}: {exc}"
            ) from exc
        else:
            raise AssertionError(f"invalid mutation accepted: {mutation}")

    vconf_valid = vconf_rejected = 0
    weird = [None, pd.NA, float("nan"), float("inf"), True, np.bool_(False), "oops", -1, 0, 0.7, 1]
    for _ in range(20000):
        args = dict(
            tier_cap=rng.choice(weird),
            payout_defaulted=rng.choice([True, False, np.bool_(True), "false"]),
            payout_missing_factor=rng.choice(weird),
            outlier_conf_penalty=rng.choice(weird),
            corner_conf_penalty=rng.choice(weird),
        )
        try:
            value, factors = compute_v_conf(**args)
            assert math.isfinite(value) and 0 <= value <= 1
            assert all(math.isfinite(x) and 0 <= x <= 1 for x in factors.values())
            vconf_valid += 1
        except CanonicalizationError:
            vconf_rejected += 1
        except Exception as exc:
            raise AssertionError(f"uncontrolled v_conf exception: {exc}") from exc

    c = _canonical(_base_rows())
    result = run_bank_valuation(
        c,
        BankValuationInputs(
            coe=0.3705,
            macro_cap=0.140135,
            band_width_shadow_mode=False,
        ),
    )
    assert abs(result["valuation"]["V_mid"] - 6.8934456) <= 1e-7
    assert abs(result["v_conf"] - 0.8) <= 1e-12

    sql_path = Path(__file__).resolve().parents[1] / "sql" / "011_bank_valuation_integration.sql"
    sql = sql_path.read_text(encoding="utf-8")
    _sql_contract(sql)
    mutation_results = {}
    mutations_sql = {
        "seven_slots": sql.replace("generate_series(7, 0, -1)", "generate_series(6, 0, -1)"),
        "date_leak": sql.replace("m.published_at <= p.analysis_at", "m.published_at::date <= p.analysis_at::date"),
        "tie_break_removed": sql.replace("m.version_sequence DESC,\n        m.record_id DESC", "m.record_id DESC"),
        "reverse_output": sql.replace("ORDER BY s.period_end;", "ORDER BY s.period_end DESC;"),
    }
    for name, mutated in mutations_sql.items():
        try:
            _sql_contract(mutated)
        except AssertionError:
            mutation_results[name] = "caught"
        else:
            raise AssertionError(f"SQL mutation not caught: {name}")

    print(json.dumps({
        "canonical_valid": canonical_valid,
        "canonical_controlled_reject": canonical_rejected,
        "vconf_valid": vconf_valid,
        "vconf_controlled_reject": vconf_rejected,
        "sql_mutations": mutation_results,
        "uncontrolled_exceptions": 0,
        "bnk1_v_mid": result["valuation"]["V_mid"],
        "bnk1_v_conf": result["v_conf"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
