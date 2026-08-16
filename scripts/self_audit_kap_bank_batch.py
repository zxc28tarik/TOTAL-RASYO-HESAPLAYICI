#!/usr/bin/env python3
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

from dataclasses import replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from src.analytics.kap_bank_batch_io import load_batch_contexts_json, load_disclosures_jsonl
from src.analytics.kap_bank_end_to_end import (
    KapBankEndToEndError,
    KapBankEvaluationContext,
    evaluate_kap_bank_batch_end_to_end,
)
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.mkk_kap import KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test_fixtures" / "kap_bank_batch_e2e"
RNG = random.Random(20260804)
ANALYSIS = datetime.fromisoformat("2026-05-15T20:00:00+03:00")
ANCHOR = date.fromisoformat("2026-03-31")
FACT = KapFinancialFactConfig.from_json_file(
    str(ROOT / "config" / "mkk_kap_financial_facts_mapping.example.json")
)
SEMANTIC = SemanticMappingConfig.from_json_file(
    str(ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json")
)
DERIVATION = BankDerivationConfig.from_json_file(
    str(ROOT / "config" / "bank_fact_derivation.official_v1.json")
)


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _run(rows, contexts, *, continue_on_error=True):
    return evaluate_kap_bank_batch_end_to_end(
        rows,
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config=FACT,
        semantic_config=SEMANTIC,
        derivation_config=DERIVATION,
        contexts=contexts,
        continue_on_error=continue_on_error,
    )


def _assert_report(report: dict[str, Any]) -> None:
    if report["status"] not in {"COMPLETE", "PARTIAL"}:
        raise AssertionError(f"unexpected status: {report['status']}")
    if report["result_count"] + report["rejected_count"] != report["requested_count"]:
        raise AssertionError("result/rejection count drift")
    ranking = report["ranking"]
    scores = [float(row["total_rasyo_100"]) for row in ranking]
    if scores != sorted(scores, reverse=True):
        raise AssertionError("ranking is not descending")
    if [row["rank"] for row in ranking] != list(range(1, len(ranking) + 1)):
        raise AssertionError("rank sequence drift")
    for result in report["results"]:
        if not 0.0 <= float(result["m2"]["m2_score"]) <= 1.0:
            raise AssertionError("M2 out of range")
        if not 0.0 <= float(result["total_rasyo"]["total_rasyo_100"]) <= 100.0:
            raise AssertionError("Total Rasyo out of range")
        if result["total_rasyo"]["decision"] not in {"AL", "IZLE", "UZAK"}:
            raise AssertionError("decision drift")


def run() -> dict[str, Any]:
    base_rows = list(load_disclosures_jsonl(FIXTURE / "disclosures.jsonl"))
    base_contexts = load_batch_contexts_json(FIXTURE / "contexts.json")
    baseline = _run(base_rows, base_contexts)
    _assert_report(baseline)

    valid_permutations = valid_contexts = partial_isolation = controlled_rejects = 0
    failures = 0
    unexpected: list[str] = []

    for i in range(200):
        rows = list(base_rows)
        RNG.shuffle(rows)
        items = list(base_contexts.items())
        RNG.shuffle(items)
        try:
            report = _run(rows, dict(items))
            if report != baseline:
                raise AssertionError("order mutation changed batch result")
            valid_permutations += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"permutation[{i}] {type(exc).__name__}: {exc}")

    for i in range(200):
        contexts: dict[str, KapBankEvaluationContext] = {}
        for ticker, context in base_contexts.items():
            scores = dict(context.other_module_scores)
            scores["M1"] = RNG.random()
            contexts[ticker] = replace(
                context,
                current_price=1.0 + RNG.random() * 20.0,
                other_module_scores=scores,
                good_count_ge8=RNG.randrange(0, 12),
            )
        try:
            report = _run(base_rows, contexts)
            _assert_report(report)
            if report["status"] != "COMPLETE" or report["result_count"] != 3:
                raise AssertionError("valid context did not complete")
            valid_contexts += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"context[{i}] {type(exc).__name__}: {exc}")

    ticker_cycle = ("AKBNK", "GARAN", "YKBNK")
    for i in range(200):
        target = ticker_cycle[i % 3]
        rows = list(base_rows)
        index = next(j for j, row in enumerate(rows) if row.ticker == target)
        original = rows[index]
        payload = json.loads(json.dumps(original.payload))
        payload["financialStatement"]["facts"][0]["value"] = "BOZUK"
        rows[index] = replace(original, payload=payload, payload_sha256=_hash(payload))
        try:
            report = _run(rows, base_contexts)
            _assert_report(report)
            if report["status"] != "PARTIAL" or report["rejected_count"] != 1:
                raise AssertionError("bad ticker was not isolated")
            if report["rejections"][0]["ticker"] != target:
                raise AssertionError("wrong ticker rejected")
            partial_isolation += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"partial[{i}] {type(exc).__name__}: {exc}")

    for i in range(1000):
        try:
            _run(base_rows, base_contexts, continue_on_error=1)
        except KapBankEndToEndError:
            controlled_rejects += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"bool-boundary[{i}] {type(exc).__name__}: {exc}")
        else:
            failures += 1
            unexpected.append(f"bool-boundary[{i}] silently accepted")

    for i in range(1000):
        bad = replace(base_rows[0], disclosure_id=["bad", i])
        try:
            _run([bad, *base_rows[1:]], base_contexts)
        except KapBankEndToEndError:
            controlled_rejects += 1
        except Exception as exc:
            failures += 1
            unexpected.append(f"id-boundary[{i}] {type(exc).__name__}: {exc}")
        else:
            failures += 1
            unexpected.append(f"id-boundary[{i}] silently accepted")

    return {
        "status": "PASS" if failures == 0 else "FAIL",
        "valid_permutations": valid_permutations,
        "valid_contexts": valid_contexts,
        "partial_isolation": partial_isolation,
        "controlled_rejects": controlled_rejects,
        "total_scenarios": 2600,
        "failures": failures,
        "unexpected": unexpected[:20],
        "baseline_ranking": baseline["ranking"],
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
