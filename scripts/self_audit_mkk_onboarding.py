from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

from src.ingest.api.mkk_contract import (  # noqa: E402
    validate_mkk_contract_sample,
    verify_mkk_contract_lock,
    write_mkk_contract_lock,
)
from src.ingest.api.mkk_kap import (  # noqa: E402
    KapApiConfigError,
    KapApiProtocolError,
    MkkKapApiConfig,
)
from src.ingest.kap_sync import (  # noqa: E402
    acquire_kap_sync_lock,
    plan_kap_backfill_windows,
    release_kap_sync_lock,
)

NOW = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


def config() -> MkkKapApiConfig:
    return MkkKapApiConfig.from_dict({
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": "/registered/product/path",
        "method": "GET",
        "items_path": "data.items",
        "next_cursor_path": "data.nextCursor",
        "cursor_param": "cursor",
        "start_param": "startAt",
        "end_param": "endAt",
        "page_size_param": "pageSize",
        "page_size": 100,
        "fields": {
            "disclosure_id": "id",
            "published_at": "publishedAt",
            "ticker": "company.ticker",
            "company_id": "company.id",
            "notification_type": "type",
            "subject": "subject",
            "source_url": "url",
        },
        "source_name": "MKK_KAP_FINANCIALS",
    })


def sample(identifier: str = "D1") -> dict[str, Any]:
    return {"data": {"items": [{
        "id": identifier,
        "publishedAt": "2026-08-04T10:00:00+03:00",
        "company": {"ticker": "GARAN", "id": "101"},
        "type": "FINANCIAL_STATEMENT",
        "subject": "Finansal rapor",
        "url": f"https://kap.org.tr/tr/Bildirim/{identifier}",
    }], "nextCursor": None}}


class LockCursor:
    def __init__(self, answers: list[tuple[bool]]) -> None:
        self.answers = list(answers)
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.answers.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


class LockConn:
    def __init__(self, answers: list[tuple[bool]]) -> None:
        self.cur = LockCursor(answers)

    def cursor(self) -> LockCursor:
        return self.cur


def run(*, smoke: bool = False) -> dict[str, Any]:
    rng = random.Random(20260805)
    factor = 20 if smoke else 1
    counts = {
        "valid_backfill": 0,
        "controlled_backfill_reject": 0,
        "valid_contract": 0,
        "controlled_contract_reject": 0,
        "lock_checks": 0,
        "uncontrolled": 0,
        "silent_accept": 0,
    }

    for _ in range(5000 // factor):
        hours = rng.uniform(1.0, 24.0 * 20.0)
        window = rng.uniform(0.25, min(48.0, max(0.25, hours + 0.25)))
        overlap = rng.randint(0, max(0, min(300, int(window * 900))))
        start = NOW - timedelta(days=30)
        end = start + timedelta(hours=hours)
        try:
            windows = plan_kap_backfill_windows(
                start_at=start,
                end_at=end,
                max_window_hours=window,
                overlap_seconds=overlap,
            )
            assert windows[0].start_at == start
            assert windows[-1].end_at == end
            for left, right in zip(windows, windows[1:]):
                assert right.start_at == left.end_at - timedelta(seconds=overlap)
                assert right.end_at > right.start_at
            counts["valid_backfill"] += 1
        except Exception:
            counts["uncontrolled"] += 1

    invalid_cases = [
        {"max_window_hours": 0, "overlap_seconds": 0},
        {"max_window_hours": 1, "overlap_seconds": 3600},
        {"max_window_hours": float("inf"), "overlap_seconds": 0},
        {"max_window_hours": True, "overlap_seconds": 0},
        {"max_window_hours": 1, "overlap_seconds": True},
    ]
    for i in range(3000 // factor):
        case = invalid_cases[i % len(invalid_cases)]
        try:
            plan_kap_backfill_windows(
                start_at=NOW,
                end_at=NOW + timedelta(days=1),
                **case,
            )
        except (TypeError, ValueError):
            counts["controlled_backfill_reject"] += 1
        except Exception:
            counts["uncontrolled"] += 1
        else:
            counts["silent_accept"] += 1

    root = Path("/tmp/total_rasyo_mkk_contract_audit")
    root.mkdir(parents=True, exist_ok=True)
    for i in range(2500 // factor):
        cfg = config()
        payload = sample(f"D{i}")
        try:
            report = validate_mkk_contract_sample(cfg, payload, checked_at=NOW)
            lock_path = write_mkk_contract_lock(root / f"lock-{i}.json", cfg, report)
            verify_mkk_contract_lock(lock_path, cfg)
            counts["valid_contract"] += 1
        except Exception:
            counts["uncontrolled"] += 1

    for i in range(2500 // factor):
        cfg = config()
        payload = sample(f"B{i}")
        mode = i % 4
        if mode == 0:
            payload["data"]["items"][0]["publishedAt"] = "bad-date"
        elif mode == 1:
            payload["data"]["items"][0]["id"] = []
        elif mode == 2:
            payload["data"]["items"].append(dict(payload["data"]["items"][0], subject="mutated"))
        else:
            payload = {"data": {"wrong": []}}
        try:
            validate_mkk_contract_sample(cfg, payload, checked_at=NOW)
        except (KapApiConfigError, KapApiProtocolError, TypeError, ValueError):
            counts["controlled_contract_reject"] += 1
        except Exception:
            counts["uncontrolled"] += 1
        else:
            counts["silent_accept"] += 1

    for i in range(1000 // factor):
        acquired = (i % 2 == 0)
        conn = LockConn([(acquired,)] + ([(True,)] if acquired else []))
        try:
            key = acquire_kap_sync_lock(
                conn, source="MKK_KAP_FINANCIALS", stream_name="disclosures"
            )
            if not acquired:
                counts["silent_accept"] += 1
                continue
            release_kap_sync_lock(conn, key)
            counts["lock_checks"] += 1
        except RuntimeError:
            if acquired:
                counts["uncontrolled"] += 1
            else:
                counts["lock_checks"] += 1
        except Exception:
            counts["uncontrolled"] += 1

    status = "PASS" if counts["uncontrolled"] == 0 and counts["silent_accept"] == 0 else "FAIL"
    return {"status": status, "counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = run(smoke=args.smoke)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
