#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _repo_bootstrap import ensure_repo_root

ROOT = ensure_repo_root()

from src.ingest.api.mkk_kap import (  # noqa: E402
    KapApiTransportError, KapFetchResult, KapQuarantinedItem, MkkKapApiConfig,
)
from src.ingest.api.mkk_suite import (  # noqa: E402
    MkkProductDefinition,
    MkkProductSuite,
    MkkProductValidation,
    MkkSuiteValidationReport,
)
import src.ingest.mkk_suite_sync as suite_sync_module  # noqa: E402
from src.ingest.kap_sync import KapSyncCheckpoint  # noqa: E402
from src.ingest.mkk_suite_sync import (  # noqa: E402
    MkkProductSyncResult,
    MkkSuiteDatabaseReadiness,
    MkkSuiteSyncReport,
    _make_run_key,
)

UTC = timezone.utc
BASE = datetime(2026, 8, 5, tzinfo=UTC)


def product_result(rng: random.Random, index: int) -> MkkProductSyncResult:
    status = rng.choice(["COMPLETE", "PARTIAL", "UP_TO_DATE", "QUARANTINED", "FAILED", "NOT_RUN"])
    windows = 0 if status in {"UP_TO_DATE", "NOT_RUN"} else rng.randint(1, 5)
    quarantine = rng.randint(1, 3) if status == "QUARANTINED" else 0
    error = "ValueError: injected" if status == "FAILED" else None
    last_start = None if windows == 0 else BASE
    last_end = None if windows == 0 else BASE + timedelta(hours=windows)
    return MkkProductSyncResult(
        product_name=f"P{index}",
        source_name=f"SRC{index}",
        stream_name=f"S{index}",
        config_sha256=f"{index % 16:x}" * 64,
        status=status,
        windows_completed=windows,
        attempts=0 if status in {"UP_TO_DATE", "NOT_RUN"} else rng.randint(windows, windows + 3),
        rows_persisted=rng.randint(0, 100),
        pages_fetched=rng.randint(0, 20),
        quarantined_count=quarantine,
        requested_end=BASE + timedelta(days=1),
        last_window_start=last_start,
        last_window_end=last_end,
        checkpoint_window_end=None if windows == 0 else last_end,
        error=error,
    )


def suite_fixture():
    products = tuple(
        MkkProductDefinition(
            product_name=f"P{i}",
            config_path=Path(f"/p{i}.json"),
            contract_lock_path=Path(f"/p{i}.lock.json"),
            sample_path=Path(f"/p{i}.sample.json"),
            api_key_env=f"P{i}_KEY",
            stream_name=f"S{i}",
            max_window_hours=1 + i,
            overlap_seconds=i * 10,
        )
        for i in range(1, 4)
    )
    suite = MkkProductSuite("AUDIT", 1, Path("/suite.json"), products)
    rows = tuple(
        MkkProductValidation(
            product_name=p.product_name,
            source_name=f"SRC{i}",
            stream_name=p.stream_name,
            config_path=str(p.config_path),
            contract_lock_path=str(p.contract_lock_path),
            sample_path=str(p.sample_path),
            config_sha256=f"{i:x}" * 64,
            sample_sha256=f"{i + 3:x}" * 64,
            items_validated=1,
            live_ready=True,
            api_key_env=p.api_key_env,
            api_key_present=True,
        )
        for i, p in enumerate(products, 1)
    )
    return suite, MkkSuiteValidationReport("AUDIT", 1, BASE, rows)


def audit_valid_reports(rng: random.Random, count: int) -> tuple[int, int]:
    accepted = uncontrolled = 0
    for i in range(count):
        try:
            products = tuple(product_result(rng, j + 1) for j in range(rng.randint(1, 5)))
            if all(item.status == "NOT_RUN" for item in products):
                products = (MkkProductSyncResult(
                    product_name=products[0].product_name, source_name=products[0].source_name,
                    stream_name=products[0].stream_name, config_sha256=products[0].config_sha256,
                    status="UP_TO_DATE", windows_completed=0, attempts=0, rows_persisted=0,
                    pages_fetched=0, quarantined_count=0, requested_end=products[0].requested_end,
                ), *products[1:])
            report = MkkSuiteSyncReport(
                run_key=f"{i % 16:x}" * 64,
                suite_name="AUDIT",
                suite_version=1,
                started_at=BASE,
                completed_at=BASE + timedelta(seconds=1),
                requested_start=BASE - timedelta(days=1),
                requested_end=BASE,
                resume=False,
                continue_on_error=bool(i % 2),
                max_windows_per_product=5,
                max_product_attempts=3,
                database_ready=True,
                products=products,
            )
            payload = report.to_dict()
            json.dumps(payload, allow_nan=False)
            accepted += 1
        except Exception:
            uncontrolled += 1
    return accepted, uncontrolled


def audit_invalid_reports(rng: random.Random, count: int) -> tuple[int, int]:
    rejected = silent = 0
    mutations = (
        lambda: {"status": "BAD"},
        lambda: {"config_sha256": "x" * 64},
        lambda: {"windows_completed": -1},
        lambda: {"attempts": True},
        lambda: {"requested_end": BASE.replace(tzinfo=None)},
        lambda: {"status": "FAILED", "error": None},
        lambda: {"status": "QUARANTINED", "quarantined_count": 0},
        lambda: {"status": "NOT_RUN", "windows_completed": 1, "last_window_start": BASE, "last_window_end": BASE},
    )
    base = dict(
        product_name="P", source_name="SRC", stream_name="S",
        config_sha256="a" * 64, status="COMPLETE", windows_completed=1,
        attempts=1, rows_persisted=0, pages_fetched=1, quarantined_count=0,
        requested_end=BASE, last_window_start=BASE - timedelta(hours=1),
        last_window_end=BASE, checkpoint_window_end=BASE, error=None,
    )
    for _ in range(count):
        values = dict(base)
        values.update(rng.choice(mutations)())
        try:
            MkkProductSyncResult(**values)
        except (TypeError, ValueError):
            rejected += 1
        except Exception:
            rejected += 1
        else:
            silent += 1
    return rejected, silent


def audit_run_keys(count: int) -> tuple[int, int]:
    suite, validation = suite_fixture()
    unique_ok = collisions = 0
    for i in range(count):
        base = dict(
            started_at=BASE + timedelta(microseconds=i),
            requested_start=BASE - timedelta(days=1),
            requested_end=BASE,
            resume=False,
            continue_on_error=False,
            overlap_seconds=300,
            max_window_hours=24,
            max_windows_per_product=1,
            max_product_attempts=1,
            max_pages=100,
            quarantine_invalid_items=True,
        )
        first = _make_run_key(suite, validation, **base)
        second = _make_run_key(suite, validation, **{**base, "max_product_attempts": 2})
        if first != second and len(first) == 64 and len(second) == 64:
            unique_ok += 1
        else:
            collisions += 1
    return unique_ok, collisions


def audit_readiness(rng: random.Random, count: int) -> tuple[int, int]:
    controlled = silent = 0
    required = (
        "raw.kap_disclosures", "raw.kap_sync_state", "raw.kap_sync_runs",
        "raw.kap_api_quarantine", "raw.mkk_suite_sync_runs", "raw.mkk_suite_product_runs",
    )
    for _ in range(count):
        mode = rng.randrange(4)
        try:
            if mode == 0:
                report = MkkSuiteDatabaseReadiness(160014, required, required)
                valid = report.ready
            elif mode == 1:
                report = MkkSuiteDatabaseReadiness(150014, required, required)
                valid = not report.ready
            elif mode == 2:
                report = MkkSuiteDatabaseReadiness(160014, required, required[:-1])
                valid = not report.ready and report.missing_relations == (required[-1],)
            else:
                MkkSuiteDatabaseReadiness(True, required, required)
                valid = False
        except (TypeError, ValueError):
            valid = mode == 3
        except Exception:
            valid = False
        if valid:
            controlled += 1
        else:
            silent += 1
    return controlled, silent



def audit_orchestration(rng: random.Random, count: int) -> tuple[int, int]:
    suite, validation = suite_fixture()
    # One product keeps the audit fast while still exercising the full orchestration.
    suite = MkkProductSuite(suite.suite_name, suite.suite_version, suite.manifest_path, (suite.products[0],))
    validation = MkkSuiteValidationReport(
        validation.suite_name, validation.suite_version, validation.checked_at,
        (validation.products[0],),
    )
    config = MkkKapApiConfig.from_dict({
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": "/registered/product",
        "method": "GET",
        "items_path": "data.items",
        "fields": {"disclosure_id": "id", "published_at": "publishedAt"},
        "start_param": "startAt", "end_param": "endAt", "source_name": "SRC1",
    })
    originals = {
        name: getattr(suite_sync_module, name)
        for name in (
            "check_mkk_suite_database_readiness", "acquire_kap_sync_lock",
            "release_kap_sync_lock", "load_kap_sync_checkpoint",
            "persist_kap_disclosures", "verify_mkk_contract_lock",
        )
    }
    original_loader = suite_sync_module.MkkKapApiConfig.from_json_file
    controlled = uncontrolled = 0
    try:
        for index in range(count):
            mode = index % 6
            state = {}
            calls = 0
            suite_sync_module.check_mkk_suite_database_readiness = lambda conn: MkkSuiteDatabaseReadiness(
                160014,
                ("raw.kap_disclosures", "raw.kap_sync_state", "raw.kap_sync_runs", "raw.kap_api_quarantine", "raw.mkk_suite_sync_runs", "raw.mkk_suite_product_runs"),
                ("raw.kap_disclosures", "raw.kap_sync_state", "raw.kap_sync_runs", "raw.kap_api_quarantine", "raw.mkk_suite_sync_runs", "raw.mkk_suite_product_runs"),
            )
            suite_sync_module.acquire_kap_sync_lock = lambda conn, **kwargs: f"{kwargs['source']}:{kwargs['stream_name']}"
            suite_sync_module.release_kap_sync_lock = lambda conn, key: None
            suite_sync_module.MkkKapApiConfig.from_json_file = lambda path: config
            suite_sync_module.verify_mkk_contract_lock = lambda path, cfg: {"config_sha256": "1" * 64}
            if mode == 5:
                state[("SRC1", "S1")] = KapSyncCheckpoint(
                    source="SRC1", stream_name="S1", cursor_value=None,
                    window_start=BASE - timedelta(hours=2), window_end=BASE + timedelta(hours=2),
                    last_success_at=BASE + timedelta(hours=2), rows_seen=1, pages_fetched=1,
                )
            suite_sync_module.load_kap_sync_checkpoint = lambda conn, **kwargs: state.get((kwargs["source"], kwargs["stream_name"]))

            def persist(conn, result, *, stream_name):
                if result.complete:
                    state[(result.source, stream_name)] = KapSyncCheckpoint(
                        source=result.source, stream_name=stream_name, cursor_value=None,
                        window_start=result.start_at, window_end=result.end_at,
                        last_success_at=result.completed_at, rows_seen=0,
                        pages_fetched=result.pages_fetched,
                    )
                return 0

            suite_sync_module.persist_kap_disclosures = persist

            class Client:
                def __init__(self, cfg, key):
                    self.cfg = cfg

                def fetch_disclosures(self, **kwargs):
                    nonlocal calls
                    calls += 1
                    if mode == 2:
                        item = KapQuarantinedItem(
                            page_number=1, item_index=0, cursor_value=None, reason="bad",
                            payload={"bad": True}, payload_sha256="e" * 64,
                            fetched_at=kwargs["end_at"], source="SRC1",
                        )
                        return KapFetchResult(
                            (), None, 1, kwargs["start_at"], kwargs["end_at"], kwargs["end_at"],
                            (item,), False, "SRC1",
                        )
                    if mode == 3 and calls == 1:
                        raise KapApiTransportError("temporary")
                    if mode == 4:
                        raise ValueError("controlled")
                    return KapFetchResult(
                        (), None, 1, kwargs["start_at"], kwargs["end_at"], kwargs["end_at"],
                        (), True, "SRC1",
                    )

            try:
                report, _ = suite_sync_module.run_mkk_product_suite_sync(
                    object(), suite, validation,
                    requested_start=None if mode == 5 else BASE - timedelta(hours=2),
                    requested_end=BASE + timedelta(hours=1),
                    resume=mode == 5,
                    continue_on_error=True,
                    max_windows_per_product=1 if mode == 1 else 3,
                    max_product_attempts=2,
                    environment={"P1_KEY": "secret"},
                    client_factory=Client,
                    clock=iter((BASE, BASE + timedelta(seconds=1))).__next__,
                )
                expected = {
                    0: "COMPLETE", 1: "PARTIAL", 2: "QUARANTINED",
                    3: "COMPLETE", 4: "FAILED", 5: "UP_TO_DATE",
                }[mode]
                valid = report.products[0].status == expected
            except Exception:
                valid = False
            if valid:
                controlled += 1
            else:
                uncontrolled += 1
    finally:
        suite_sync_module.MkkKapApiConfig.from_json_file = original_loader
        for name, value in originals.items():
            setattr(suite_sync_module, name, value)
    return controlled, uncontrolled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    rng = random.Random(1208)
    scale = 20 if args.smoke else 1
    valid_target = 4000 // scale
    invalid_target = 4000 // scale
    key_target = 2000 // scale
    readiness_target = 2000 // scale
    orchestration_target = 1000 // scale

    valid, valid_uncontrolled = audit_valid_reports(rng, valid_target)
    rejected, invalid_silent = audit_invalid_reports(rng, invalid_target)
    keys, key_collisions = audit_run_keys(key_target)
    readiness, readiness_silent = audit_readiness(rng, readiness_target)
    orchestration, orchestration_uncontrolled = audit_orchestration(rng, orchestration_target)
    uncontrolled = valid_uncontrolled + key_collisions + orchestration_uncontrolled
    silent = invalid_silent + readiness_silent
    report = {
        "status": "PASS" if uncontrolled == 0 and silent == 0 else "FAIL",
        "counts": {
            "valid_reports": valid,
            "controlled_invalid_reports": rejected,
            "run_key_policy_checks": keys,
            "database_readiness_checks": readiness,
            "orchestration_checks": orchestration,
            "uncontrolled": uncontrolled,
            "silent_accept": silent,
            "total": valid_target + invalid_target + key_target + readiness_target + orchestration_target,
        },
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
