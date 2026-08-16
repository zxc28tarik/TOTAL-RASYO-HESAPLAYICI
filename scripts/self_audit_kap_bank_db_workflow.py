from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ROOT = ensure_repo_root()

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
from typing import Any

from src.analytics.kap_bank_batch_io import load_batch_contexts_json, load_disclosures_jsonl
from src.analytics.kap_bank_db_workflow import (
    KapBankDatabaseWorkflowError,
    fetch_bank_module_contexts,
    fetch_kap_bank_disclosures,
    run_kap_bank_database_batch,
)
from src.analytics.kap_bank_end_to_end import evaluate_kap_bank_batch_end_to_end
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig

FIXTURE = ROOT / "test_fixtures/kap_bank_batch_e2e"
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
ANCHOR = date(2026, 3, 31)
FACT = KapFinancialFactConfig.from_json_file(
    ROOT / "config/mkk_kap_financial_facts_mapping.example.json"
)
SEMANTIC = SemanticMappingConfig.from_json_file(
    ROOT / "config/kap_bank_semantic_mapping.official_v1.json"
)
DERIVATION = BankDerivationConfig.from_json_file(
    ROOT / "config/bank_fact_derivation.official_v1.json"
)
DISCLOSURES = load_disclosures_jsonl(FIXTURE / "disclosures.jsonl")
CONTEXTS = load_batch_contexts_json(FIXTURE / "contexts.json")


class Cursor:
    def __init__(self, rows, names):
        self.rows = rows
        self.description = [(name,) for name in names]

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def __init__(self, rows, names):
        self.cur = Cursor(rows, names)

    def cursor(self):
        return self.cur


def raw_row(envelope):
    return (
        envelope.source,
        envelope.disclosure_id,
        envelope.published_at,
        envelope.ticker,
        envelope.company_id,
        envelope.notification_type,
        envelope.subject,
        envelope.source_url,
        json.dumps(envelope.payload),
        envelope.payload_sha256,
        envelope.fetched_at,
    )


RAW_NAMES = [
    "source", "disclosure_id", "published_at", "ticker", "company_id",
    "notification_type", "subject", "source_url", "payload", "payload_sha256", "fetched_at",
]
MODULE_NAMES = [
    "ticker", "asof_date", "analysis_at", "m1", "m3", "ek4", "ek1", "ek9",
    "good_count_ge8",
]


def main(*, smoke: bool = False) -> None:
    rng = random.Random(20260804)
    valid_count = 2 if smoke else 500
    module_invalid_count = 20 if smoke else 5000
    raw_invalid_count = 20 if smoke else 4000
    missing_context_count = 5 if smoke else 1000
    counters = {
        "valid_order_mutations": 0,
        "controlled_module_rejections": 0,
        "controlled_raw_rejections": 0,
        "missing_context_runs": 0,
        "uncontrolled_exceptions": 0,
        "silent_invalid_accepts": 0,
    }

    expected_ranking = ["YKBNK", "AKBNK", "GARAN"]
    for _ in range(valid_count):
        rows = list(DISCLOSURES)
        rng.shuffle(rows)
        report = evaluate_kap_bank_batch_end_to_end(
            rows,
            analysis_at=ANALYSIS,
            anchor_period_end=ANCHOR,
            fact_config=FACT,
            semantic_config=SEMANTIC,
            derivation_config=DERIVATION,
            contexts=CONTEXTS,
            requested_tickers=["GARAN", "YKBNK", "AKBNK"],
        )
        if [row["ticker"] for row in report["ranking"]] != expected_ranking:
            raise AssertionError("order mutation changed BANK ranking")
        counters["valid_order_mutations"] += 1

    invalid_values: list[Any] = [
        True, False, float("nan"), float("inf"), float("-inf"),
        "bozuk", [], {}, 10 ** 10000, -0.1, 1.1,
    ]
    try:
        import numpy as np
        invalid_values.extend([np.bool_(False), np.bool_(True)])
    except ImportError:
        pass
    for idx in range(module_invalid_count):
        bad = invalid_values[idx % len(invalid_values)]
        position = idx % 6
        values = [0.6, 0.6, 0.5, 0.8, 0.6, 8]
        values[position] = bad
        rows = [(
            "AKBNK", date(2026, 5, 15), ANALYSIS,
            values[0], values[1], values[2], values[3], values[4], values[5],
        )]
        try:
            contexts, rejected = fetch_bank_module_contexts(
                Conn(rows, MODULE_NAMES),
                tickers=["AKBNK"], analysis_at=ANALYSIS, horizon_days=63,
            )
        except Exception:
            counters["uncontrolled_exceptions"] += 1
            continue
        if contexts or "AKBNK" not in rejected:
            counters["silent_invalid_accepts"] += 1
        else:
            counters["controlled_module_rejections"] += 1

    base = list(raw_row(DISCLOSURES[0]))
    mutations = [
        (0, ""), (1, ""), (2, "2026-01-01"), (3, "THYAO"),
        (8, "{bad"), (9, "x" * 64), (10, "2026-01-01"),
        (3, []), (1, {}), (0, ["MKK"]),
    ]
    for idx in range(raw_invalid_count):
        row = list(base)
        field, value = mutations[idx % len(mutations)]
        row[field] = value
        try:
            result = fetch_kap_bank_disclosures(
                Conn([tuple(row)], RAW_NAMES),
                tickers=["AKBNK"], analysis_at=ANALYSIS,
            )
        except KapBankDatabaseWorkflowError:
            counters["controlled_raw_rejections"] += 1
        except Exception:
            counters["uncontrolled_exceptions"] += 1
        else:
            if result:
                counters["silent_invalid_accepts"] += 1

    ticker_list = ["AKBNK", "GARAN", "YKBNK"]
    for idx in range(missing_context_count):
        contexts = dict(CONTEXTS)
        removed = ticker_list[idx % len(ticker_list)]
        contexts.pop(removed)
        try:
            report = evaluate_kap_bank_batch_end_to_end(
                DISCLOSURES,
                analysis_at=ANALYSIS,
                anchor_period_end=ANCHOR,
                fact_config=FACT,
                semantic_config=SEMANTIC,
                derivation_config=DERIVATION,
                contexts=contexts,
                requested_tickers=ticker_list,
            )
        except Exception:
            counters["uncontrolled_exceptions"] += 1
            continue
        if (
            report["prepared_count"] != 3
            or report["result_count"] != 2
            or report["rejected_count"] != 1
            or report["rejections"][0]["ticker"] != removed
            or any(row["valuation"]["sector_sample_size"] != 2 for row in report["results"])
        ):
            counters["silent_invalid_accepts"] += 1
        else:
            counters["missing_context_runs"] += 1

    total = sum(
        counters[key]
        for key in (
            "valid_order_mutations", "controlled_module_rejections",
            "controlled_raw_rejections", "missing_context_runs",
        )
    )
    output = {
        "scenario_count": valid_count + module_invalid_count + raw_invalid_count + missing_context_count,
        "validated_scenarios": total,
        **counters,
    }
    if counters["uncontrolled_exceptions"] or counters["silent_invalid_accepts"]:
        raise SystemExit(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KAP BANK DB workflow self-audit")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a small direct-execution regression audit instead of the full 10,500 scenarios.",
    )
    args = parser.parse_args()
    main(smoke=args.smoke)
