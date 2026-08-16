#!/usr/bin/env python3
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import hashlib
import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.analytics.bank_valuation_pipeline import BankValuationInputs
from src.analytics.kap_bank_end_to_end import (
    KapBankEndToEndError,
    evaluate_kap_bank_end_to_end,
)
from src.analytics.total_rasyo_score import (
    MODULE_KEYS,
    TotalRasyoScoreError,
    compute_total_rasyo,
)
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.mkk_kap import KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig, build_quarter_ends

ROOT = Path(__file__).resolve().parents[1]
RNG = random.Random(20260804)
ANCHOR = date(2026, 3, 31)
IST = timezone(timedelta(hours=3))
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=IST)

SEMANTIC = SemanticMappingConfig.from_json_file(
    str(ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json")
)
DERIVATION = BankDerivationConfig.from_json_file(
    str(ROOT / "config" / "bank_fact_derivation.official_v1.json")
)
FACT_CONFIG = KapFinancialFactConfig.from_dict({
    "mapping_profile": "MKK_KAP_FINANCIAL_FACTS",
    "mapping_version": 1,
    "facts_path": "financialStatement.facts",
    "version_tag_path": "financialStatement.versionTag",
    "version_sequence_path": "financialStatement.versionSequence",
    "default_unit_scale": 1000,
    "default_currency": "TRY",
    "default_statement_scope": "CONSOLIDATED",
    "fields": {
        "fact_code": "code",
        "value": "value",
        "period_start": "periodStart",
        "period_end": "periodEnd",
        "currency": "currency",
        "unit_scale": "unitScale",
        "statement_scope": "statementScope",
    },
})


def _payload_hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _envelope(
    period: date,
    idx: int,
    *,
    seed: int,
    disclosure_id: str | None = None,
    published_at: datetime | None = None,
    ticker: str = "GARAN",
    equity_override: int | None = None,
) -> KapDisclosureEnvelope:
    q = (period.month - 1) // 3 + 1
    pub = published_at or datetime.combine(
        period + timedelta(days=40), datetime.min.time(), tzinfo=timezone.utc
    )
    equity = equity_override or (100_000_000 + idx * (1_000_000 + seed % 500_000))
    capital = 10_000_000 + seed % 1_000_000
    q_profit = 4_000_000 + seed % 2_000_000
    q_dividend = 500_000 + seed % 300_000
    payload = {
        "financialStatement": {
            "versionTag": "ORIGINAL",
            "versionSequence": 1,
            "facts": [
                {
                    "code": "ifrs-full_Equity",
                    "value": str(equity),
                    "periodEnd": period.isoformat(),
                    "periodStart": None,
                    "currency": "TRY",
                    "unitScale": 1000,
                    "statementScope": "CONSOLIDATED",
                },
                {
                    "code": "ifrs-full_IssuedCapital",
                    "value": str(capital),
                    "periodEnd": period.isoformat(),
                    "periodStart": None,
                    "currency": "TRY",
                    "unitScale": 1000,
                    "statementScope": "CONSOLIDATED",
                },
                {
                    "code": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                    "value": str(q * q_profit),
                    "periodStart": f"{period.year}-01-01",
                    "periodEnd": period.isoformat(),
                    "currency": "TRY",
                    "unitScale": 1000,
                    "statementScope": "CONSOLIDATED",
                },
                {
                    "code": "ifrs-full_DividendsPaid",
                    "value": str(-(q * q_dividend)),
                    "periodStart": f"{period.year}-01-01",
                    "periodEnd": period.isoformat(),
                    "currency": "TRY",
                    "unitScale": 1000,
                    "statementScope": "CONSOLIDATED",
                },
            ],
        }
    }
    did = disclosure_id or f"D-{seed}-{period.isoformat()}"
    fetched = max(ANALYSIS, pub + timedelta(minutes=1))
    return KapDisclosureEnvelope(
        disclosure_id=did,
        published_at=pub,
        ticker=ticker,
        company_id=f"C-{seed}",
        notification_type="FR",
        subject="Financial Report",
        source_url=f"https://kap.org.tr/tr/Bildirim/{did}",
        payload=payload,
        payload_sha256=_payload_hash(payload),
        fetched_at=fetched,
    )


def _rows(seed: int) -> list[KapDisclosureEnvelope]:
    return [
        _envelope(period, idx, seed=seed)
        for idx, period in enumerate(build_quarter_ends(ANCHOR, 12))
    ]


def _evaluate(rows: list[KapDisclosureEnvelope], *, seed: int) -> dict[str, Any]:
    score_rng = random.Random(10_000_003 + seed)
    scores = {
        "M1": score_rng.random(),
        "M3": score_rng.random(),
        "Ek4": score_rng.random(),
        "Ek1": score_rng.random(),
        "Ek9": score_rng.random(),
    }
    return evaluate_kap_bank_end_to_end(
        rows,
        ticker="GARAN",
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC,
        derivation_config=DERIVATION,
        valuation_inputs=BankValuationInputs(
            coe=0.13 + (seed % 6) * 0.01,
            macro_cap=0.06 + (seed % 3) * 0.01,
            tier_cap=0.80,
            payout_missing_factor=0.70,
            band_width_shadow_mode=True,
            max_halfwidth=0.80,
        ),
        current_price=5.0 + (seed % 30) * 0.5,
        price_trade_date=ANALYSIS.date(),
        other_module_scores=scores,
        good_count_ge8=seed % 12,
        sector_residual_scales=[0.005 + (i % 7) * 0.001 for i in range(20)],
    )


def _assert_e2e(result: dict[str, Any]) -> None:
    if result["disclosures_used"] != 12:
        raise AssertionError("disclosure count drift")
    if result["bank_metrics_derived"] != 8:
        raise AssertionError("metric count drift")
    if sum(value is None for value in result["canonical"]["roe_series"]) != 0:
        raise AssertionError("unexpected missing ROE")
    m2 = result["m2"]["m2_score"]
    total = result["total_rasyo"]
    if not (0.0 <= m2 <= 1.0):
        raise AssertionError("M2 contract")
    if total["module_scores"]["M2"] != m2:
        raise AssertionError("M2 bridge drift")
    if not (0.0 <= total["final_score"] <= 1.0):
        raise AssertionError("Total contract")
    if total["decision"] not in {"AL", "IZLE", "UZAK"}:
        raise AssertionError("decision contract")


def run() -> dict[str, Any]:
    valid_total = valid_e2e = controlled_rejects = mutation_checks = 0
    failures = 0
    unexpected: list[str] = []

    # Shared Total Rasyo formula: broad valid numeric domain.
    for i in range(10_000):
        module_scores = {key: RNG.random() for key in MODULE_KEYS}
        try:
            result = compute_total_rasyo(
                module_scores,
                good_count_ge8=RNG.randrange(0, 20),
            )
            expected = math.fsum(
                result["module_scores"][key] * result["weights"][key]
                for key in MODULE_KEYS
            )
            if not math.isclose(result["base_score"], expected, abs_tol=1e-12):
                raise AssertionError("contribution sum drift")
            if not (0 <= result["final_score"] <= 1):
                raise AssertionError("score out of range")
            valid_total += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"valid-total[{i}] {type(exc).__name__}: {exc}")

    # End-to-end raw KAP -> Total Rasyo, with order mutation.
    for i in range(500):
        rows = _rows(i)
        try:
            first = _evaluate(rows, seed=i)
            second = _evaluate(list(reversed(rows)), seed=i)
            if first != second:
                raise AssertionError("input order changed KAP-to-Total result")
            _assert_e2e(first)
            _assert_e2e(second)
            valid_e2e += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"valid-e2e[{i}] {type(exc).__name__}: {exc}")

    # Invalid Total Rasyo domains must be controlled rejections.
    invalid_total = [
        lambda i: compute_total_rasyo({**{k: 0.5 for k in MODULE_KEYS}, "M2": True}, good_count_ge8=8),
        lambda i: compute_total_rasyo({**{k: 0.5 for k in MODULE_KEYS}, "M2": float("nan")}, good_count_ge8=8),
        lambda i: compute_total_rasyo({**{k: 0.5 for k in MODULE_KEYS}, 1: 0.1, (1,): 0.1}, good_count_ge8=8),
        lambda i: compute_total_rasyo({k: 0.5 for k in MODULE_KEYS}, good_count_ge8=True),
        lambda i: compute_total_rasyo({k: 0.5 for k in MODULE_KEYS}, good_count_ge8=-1),
        lambda i: compute_total_rasyo({k: 0.5 for k in MODULE_KEYS}, good_count_ge8=8, weights={k: 0.1 for k in MODULE_KEYS}),
    ]
    try:
        import numpy as np
        invalid_total.append(
            lambda i: compute_total_rasyo(
                {**{k: 0.5 for k in MODULE_KEYS}, "M2": np.bool_(False)},
                good_count_ge8=8,
            )
        )
    except ImportError:
        pass

    for i in range(7_000):
        case = invalid_total[i % len(invalid_total)]
        try:
            case(i)
        except TotalRasyoScoreError:
            controlled_rejects += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"invalid-total[{i}] {type(exc).__name__}: {exc}")
        else:
            failures += 1
            unexpected.append(f"invalid-total[{i}] silently accepted")

    # End-to-end identity/look-ahead/input mutations.
    for i in range(2_000):
        rows = _rows(i)
        first = rows[0]
        mutation = i % 8
        if mutation == 0:
            bad = replace(first, payload_sha256="0" * 64)
            mutated = [bad, *rows[1:]]
        elif mutation == 1:
            bad = replace(first, published_at=first.published_at + timedelta(seconds=1))
            mutated = rows + [bad]
        elif mutation == 2:
            bad_payload = json.loads(json.dumps(first.payload))
            bad_payload["financialStatement"]["facts"][0]["value"] = "999999999"
            bad = replace(first, payload=bad_payload, payload_sha256=_payload_hash(bad_payload))
            mutated = rows + [bad]
        elif mutation == 3:
            mutated = [replace(first, ticker="AKBNK"), *rows[1:]]
        elif mutation == 4:
            mutated = [replace(first, fetched_at=first.published_at - timedelta(minutes=10)), *rows[1:]]
        elif mutation == 5:
            bad_payload = json.loads(json.dumps(first.payload))
            bad_payload["financialStatement"]["facts"] = []
            mutated = [replace(first, payload=bad_payload, payload_sha256=_payload_hash(bad_payload)), *rows[1:]]
        elif mutation == 6:
            mutated = []
        else:
            mutated = [object()]  # type: ignore[list-item]
        try:
            _evaluate(mutated, seed=i)
        except (KapBankEndToEndError, ValueError, TypeError, ArithmeticError):
            controlled_rejects += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"invalid-e2e[{i}] {type(exc).__name__}: {exc}")
        else:
            failures += 1
            unexpected.append(f"invalid-e2e[{i}] silently accepted")

    # Correct diagnostic mutations: future restatement ignored, missing quarter visible.
    for i in range(500):
        rows = _rows(i)
        baseline = _evaluate(rows, seed=i)
        future = _envelope(
            ANCHOR,
            99,
            seed=i,
            disclosure_id=f"FUTURE-{i}",
            published_at=ANALYSIS + timedelta(hours=1),
            equity_override=999_999_999,
        )
        with_future = _evaluate(rows + [future], seed=i)
        if baseline["canonical"] != with_future["canonical"] or baseline["valuation"] != with_future["valuation"]:
            failures += 1
            unexpected.append(f"future-restatement[{i}] changed historical result")
        else:
            mutation_checks += 1

        missing_period = build_quarter_ends(ANCHOR, 12)[-3]
        missing_rows = [
            row for row in rows
            if not (
                any(
                    fact.get("periodEnd") == missing_period.isoformat()
                    for fact in row.payload["financialStatement"]["facts"]
                )
            )
        ]
        try:
            missing_result = _evaluate(missing_rows, seed=i)
            if sum(value is None for value in missing_result["canonical"]["roe_series"]) <= 0:
                raise AssertionError("missing quarter silently compressed")
            mutation_checks += 1
        except (KapBankEndToEndError, ValueError):
            # A fail-closed insufficient-history result is also safe.
            mutation_checks += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"missing-quarter[{i}] {type(exc).__name__}: {exc}")

    report = {
        "valid_total_rasyo_cases": valid_total,
        "valid_kap_bank_end_to_end_cases": valid_e2e,
        "controlled_invalid_rejections": controlled_rejects,
        "point_in_time_and_missing_quarter_checks": mutation_checks,
        "total_scenarios": 10_000 + 500 + 7_000 + 2_000 + 1_000,
        "uncontrolled_or_silent_failures": failures,
        "unexpected_examples": unexpected[:20],
    }
    if failures:
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
