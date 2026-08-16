from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import random
from typing import Any

from src.analytics.kap_bank_batch_io import run_batch_preview_from_files
from src.analytics.kap_bank_batch_persistence import (
    KapBankBatchPersistenceError,
    persist_kap_bank_batch_report,
)

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
ANCHOR = date(2026, 3, 31)
SEED = 20260804


class Cursor:
    def __init__(self, fail_pattern: str | None = None):
        self.calls: list[tuple[str, Any]] = []
        self.fail_pattern = fail_pattern

    def execute(self, sql: str, params: Any = None) -> None:
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, params))
        if self.fail_pattern and self.fail_pattern in normalized:
            raise RuntimeError("injected database failure")

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


class Conn:
    def __init__(self, fail_pattern: str | None = None):
        self.cur = Cursor(fail_pattern)
        self.enter_count = 0
        self.exit_errors: list[Any] = []

    def cursor(self):
        return self.cur

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> bool:
        self.exit_errors.append(exc_type)
        return False


def base_report() -> dict[str, Any]:
    return run_batch_preview_from_files(
        disclosures_path=ROOT / "test_fixtures/kap_bank_batch_e2e/disclosures.jsonl",
        contexts_path=ROOT / "test_fixtures/kap_bank_batch_e2e/contexts.json",
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config_path=ROOT / "config/mkk_kap_financial_facts_mapping.example.json",
        semantic_config_path=ROOT / "config/kap_bank_semantic_mapping.official_v1.json",
        derivation_config_path=ROOT / "config/bank_fact_derivation.official_v1.json",
    )


def expect_rejection(report: dict[str, Any]) -> None:
    conn = Conn()
    try:
        persist_kap_bank_batch_report(conn, report)
    except KapBankBatchPersistenceError:
        if conn.enter_count != 0 or conn.cur.calls:
            raise AssertionError("invalid report touched database before rejection")
        return
    except Exception as exc:  # pragma: no cover - audit assertion
        raise AssertionError(f"uncontrolled invalid-report exception: {type(exc).__name__}: {exc}") from exc
    raise AssertionError("invalid report was silently accepted")


def delete_path(report: dict[str, Any], kind: str, key: str) -> None:
    if kind == "result":
        del report["results"][0][key]
    elif kind == "valuation":
        del report["results"][0]["valuation"][key]
    elif kind == "m2":
        del report["results"][0]["m2"][key]
    elif kind == "total":
        del report["results"][0]["total_rasyo"][key]
    else:  # pragma: no cover
        raise AssertionError(kind)


def main() -> None:
    rng = random.Random(SEED)
    original = base_report()
    valid_count = 0
    controlled_rejections = 0
    injected_db_failures = 0

    # Valid order/timezone permutations must preserve one deterministic run identity.
    expected_key = persist_kap_bank_batch_report(Conn(), original).run_key
    valid_count += 1
    for _ in range(1000):
        report = deepcopy(original)
        rng.shuffle(report["results"])
        if rng.random() < 0.5:
            utc = report["analysis_at"].astimezone(timezone.utc)
            report["analysis_at"] = utc
            for row in report["results"]:
                row["analysis_at"] = utc
                row["canonical"]["analysis_at"] = utc
                row["valuation"]["analysis_at"] = utc
                row["m2"]["analysis_at"] = utc
        saved = persist_kap_bank_batch_report(Conn(), report)
        if saved.run_key != expected_key:
            raise AssertionError("equivalent analysis instant produced a different run key")
        valid_count += 1

    required_paths: list[tuple[str, str]] = []
    for key in original["results"][0]:
        required_paths.append(("result", key))
    for key in original["results"][0]["valuation"]:
        required_paths.append(("valuation", key))
    for key in original["results"][0]["m2"]:
        required_paths.append(("m2", key))
    for key in original["results"][0]["total_rasyo"]:
        required_paths.append(("total", key))

    # Every deletion of an emitted contract field must be controlled. Fields that are
    # conditionally optional are skipped only when the current OK result does not require them.
    for kind, key in required_paths:
        report = deepcopy(original)
        delete_path(report, kind, key)
        try:
            expect_rejection(report)
        except AssertionError:
            # Only valuation.reason is nullable but its key is still mandatory; no exemptions.
            raise
        controlled_rejections += 1

    bad_scalars = [None, True, False, [], {}, object(), float("nan"), float("inf"), 10**10000]
    for _ in range(6000):
        report = deepcopy(original)
        choice = rng.randrange(12)
        bad = rng.choice(bad_scalars)
        if choice == 0:
            report["requested_count"] = bad
        elif choice == 1:
            report["status"] = bad
        elif choice == 2:
            report["ranking"][0]["rank"] = bad
        elif choice == 3:
            report["ranking"][0]["total_rasyo_100"] = bad
        elif choice == 4:
            report["results"][0]["total_rasyo"]["module_scores"]["M1"] = bad
        elif choice == 5:
            report["results"][0]["m2"]["score_inputs"] = bad
        elif choice == 6:
            report["results"][0]["valuation"]["quarter_slots"] = bad
        elif choice == 7:
            report["results"][0]["canonical"]["roe_series"] = bad
        elif choice == 8:
            report["results"][0]["disclosure_lineage"][0]["payload_sha256"] = bad
        elif choice == 9:
            original_flag = report["results"][0]["total_rasyo"]["veto_flag"]
            report["results"][0]["total_rasyo"]["veto_flag"] = not original_flag
        elif choice == 10:
            report["rejections"] = "not-a-list"
        else:
            report["results"][0]["ticker"] = bad
        expect_rejection(report)
        controlled_rejections += 1

    for _ in range(3000):
        report = deepcopy(original)
        choice = rng.randrange(8)
        if choice == 0:
            report["result_count"] += 1
        elif choice == 1:
            report["valuation_ok_count"] -= 1
        elif choice == 2:
            report["ranking"][0]["ticker"] = report["ranking"][1]["ticker"]
        elif choice == 3:
            report["ranking"][0]["m2_score"] = 0.0
        elif choice == 4:
            report["results"][0]["m2"]["asof_date"] += timedelta(days=1)
        elif choice == 5:
            report["results"][0]["valuation"]["roe_missing_count"] += 1
        elif choice == 6:
            report["results"][0]["disclosure_lineage"][0]["published_at"] = ANALYSIS + timedelta(seconds=1)
        else:
            report["results"][0]["total_rasyo"]["total_rasyo_100"] += 1.0
        expect_rejection(report)
        controlled_rejections += 1

    # Database failures must escape and mark the one outer transaction as failed.
    for pattern in (
        "INSERT INTO analytics.kap_bank_batch_runs",
        "INSERT INTO analytics.bank_valuation_periods",
        "INSERT INTO analytics.bank_m2_scores",
        "INSERT INTO analytics.module_scores",
        "INSERT INTO analytics.kap_bank_batch_rankings",
    ):
        for _ in range(20):
            conn = Conn(pattern)
            try:
                persist_kap_bank_batch_report(conn, original)
            except RuntimeError:
                if conn.enter_count != 1 or conn.exit_errors != [RuntimeError]:
                    raise AssertionError("database failure did not fail one atomic transaction")
                injected_db_failures += 1
            else:  # pragma: no cover
                raise AssertionError(f"database failure was not propagated: {pattern}")

    output = {
        "seed": SEED,
        "valid_reports": valid_count,
        "controlled_invalid_reports": controlled_rejections,
        "injected_database_failures": injected_db_failures,
        "total_scenarios": valid_count + controlled_rejections + injected_db_failures,
        "uncontrolled_exceptions": 0,
        "silent_invalid_accepts": 0,
        "run_key": expected_key,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
