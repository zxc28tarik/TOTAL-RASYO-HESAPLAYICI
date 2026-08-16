from __future__ import annotations

import argparse
import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

from src.ingest.api.mkk_kap import (  # noqa: E402
    KapApiConfigError,
    KapApiProtocolError,
    KapDisclosureEnvelope,
    KapFetchResult,
    KapQuarantinedItem,
    MkkKapApiClient,
    MkkKapApiConfig,
)
from src.ingest.kap_raw import persist_kap_disclosures  # noqa: E402
from src.ingest.kap_sync import KapSyncCheckpoint, plan_kap_sync_window  # noqa: E402


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None):
        self.payload = payload
        self.status_code = status
        self.headers = {} if headers is None else dict(headers)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)

    def request(self, **_: Any) -> FakeResponse:
        return self.responses.pop(0)


class Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


class Conn:
    def __init__(self) -> None:
        self.cur = Cursor()

    def cursor(self) -> Cursor:
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> bool:
        return False


def base_config(**changes: Any) -> MkkKapApiConfig:
    raw: dict[str, Any] = {
        "base_url": "https://api.provider.example.org",
        "api_key_header": "X-Api-Key",
        "path": "/kap/disclosures",
        "method": "GET",
        "items_path": "data.items",
        "next_cursor_path": "data.next",
        "cursor_param": "cursor",
        "start_param": "startAt",
        "end_param": "endAt",
        "page_size_param": "limit",
        "page_size": 100,
        "fields": {
            "disclosure_id": "id",
            "published_at": "publishedAt",
            "ticker": "company.ticker",
        },
        "max_retries": 0,
    }
    raw.update(changes)
    return MkkKapApiConfig.from_dict(raw)


def item(identifier: Any, published: Any = "2026-08-04T10:00:00+00:00") -> dict[str, Any]:
    return {
        "id": identifier,
        "publishedAt": published,
        "company": {"ticker": "GARAN"},
    }


def disclosure(identifier: str = "D1", source: str = "MKK_KAP_API") -> KapDisclosureEnvelope:
    return KapDisclosureEnvelope(
        disclosure_id=identifier,
        published_at=NOW - timedelta(hours=1),
        ticker="GARAN",
        company_id="101",
        notification_type="FINANCIAL_STATEMENT",
        subject="Rapor",
        source_url="https://kap.org.tr/x",
        payload={"id": identifier},
        payload_sha256=("a" if identifier == "D1" else "b") * 64,
        fetched_at=NOW,
        source=source,
    )


def quarantine(index: int = 0, source: str = "MKK_KAP_API") -> KapQuarantinedItem:
    return KapQuarantinedItem(
        page_number=1,
        item_index=index,
        cursor_value=None,
        reason="gecersiz bildirim",
        payload={"id": f"BAD-{index}"},
        payload_sha256="c" * 64,
        fetched_at=NOW,
        source=source,
    )


def run(seed: int = 9042026, *, smoke: bool = False) -> dict[str, Any]:
    rng = random.Random(seed)
    counts = {
        "sync_plan_valid": 0,
        "sync_plan_controlled_reject": 0,
        "api_valid": 0,
        "api_quarantined": 0,
        "config_valid": 0,
        "config_controlled_reject": 0,
        "persistence_complete": 0,
        "persistence_quarantined": 0,
        "uncontrolled": 0,
        "silent_accept": 0,
    }
    errors: list[str] = []

    plan_valid_n = 10 if smoke else 5_000
    invalid_plan_repeat = 2 if smoke else 400
    api_n = 12 if smoke else 5_000
    config_n = 14 if smoke else 3_500
    persistence_n = 10 if smoke else 2_000

    for _ in range(plan_valid_n):
        cp_end = NOW - timedelta(hours=rng.randint(0, 72))
        cp = KapSyncCheckpoint(
            source="MKK_KAP_API",
            stream_name="disclosures",
            cursor_value=None,
            window_start=cp_end - timedelta(hours=24),
            window_end=cp_end,
            last_success_at=cp_end + timedelta(minutes=1),
            rows_seen=rng.randint(0, 1000),
            pages_fetched=rng.randint(0, 20),
        )
        requested_end = cp_end + timedelta(hours=rng.uniform(0.1, 96))
        overlap = rng.randint(0, 3600)
        max_hours = rng.uniform(0.25, 24)
        try:
            plan = plan_kap_sync_window(
                requested_start=None,
                requested_end=requested_end,
                checkpoint=cp,
                resume=True,
                overlap_seconds=overlap,
                max_window_hours=max_hours,
            )
            if not (plan.start_at <= plan.end_at <= requested_end):
                counts["silent_accept"] += 1
                errors.append("sync plan zaman sirasi bozuk")
            else:
                counts["sync_plan_valid"] += 1
        except Exception as exc:  # unexpected for generated valid cases
            counts["uncontrolled"] += 1
            errors.append(f"valid sync plan: {type(exc).__name__}: {exc}")

    invalid_plan_values = [True, -1, 86_401, "300", None]
    for value in invalid_plan_values * invalid_plan_repeat:
        try:
            plan_kap_sync_window(
                requested_start=NOW,
                requested_end=NOW + timedelta(hours=1),
                overlap_seconds=value,  # type: ignore[arg-type]
            )
            counts["silent_accept"] += 1
            errors.append(f"invalid overlap accepted: {value!r}")
        except (TypeError, ValueError):
            counts["sync_plan_controlled_reject"] += 1
        except Exception as exc:
            counts["uncontrolled"] += 1
            errors.append(f"invalid sync plan: {type(exc).__name__}: {exc}")

    for index in range(api_n):
        mode = index % 4
        items: list[Any]
        if mode == 0:
            items = [item(f"D{index}")]
        elif mode == 1:
            items = [item(f"D{index}", "bad-time")]
        elif mode == 2:
            items = [item({"bad": index})]
        else:
            first = item(f"D{index}")
            changed = dict(first)
            changed["extra"] = index
            items = [first, changed]
        client = MkkKapApiClient(
            base_config(),
            "secret",
            session=FakeSession([FakeResponse({"data": {"items": items, "next": None}})]),
            clock=lambda: NOW,
            sleeper=lambda _: None,
        )
        try:
            result = client.fetch_disclosures(
                start_at=NOW - timedelta(days=1),
                end_at=NOW,
                quarantine_invalid_items=True,
            )
            if mode == 0:
                if not result.complete or len(result.disclosures) != 1:
                    counts["silent_accept"] += 1
                    errors.append("valid API page wrong status")
                else:
                    counts["api_valid"] += 1
            else:
                if result.complete or len(result.quarantined_items) != 1:
                    counts["silent_accept"] += 1
                    errors.append(f"invalid API item not quarantined mode={mode}")
                else:
                    counts["api_quarantined"] += 1
        except (KapApiConfigError, KapApiProtocolError, ValueError, TypeError) as exc:
            counts["uncontrolled"] += 1
            errors.append(f"quarantine API mode={mode}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            counts["uncontrolled"] += 1
            errors.append(f"API uncontrolled mode={mode}: {type(exc).__name__}: {exc}")

    invalid_configs = [
        {"api_key_header": "Accept"},
        {"static_params": {1: "x"}},
        {"static_params": {"x": float("nan")}},
        {"static_params": {"startAt": "x"}},
        {"end_param": "startAt"},
        {"source_name": []},
        {"base_url": "https://user:pass@api.example.org"},
    ]
    for index in range(config_n):
        if index % 2 == 0:
            try:
                config = base_config(
                    min_request_interval_seconds=rng.uniform(0, 2),
                    max_retry_after_seconds=rng.uniform(0, 30),
                    source_name=f"MKK_STREAM_{index}",
                )
                config.validate_live_ready()
                counts["config_valid"] += 1
            except Exception as exc:
                counts["uncontrolled"] += 1
                errors.append(f"valid config: {type(exc).__name__}: {exc}")
        else:
            raw = {
                "base_url": "https://api.provider.example.org",
                "api_key_header": "X-Api-Key",
                "path": "/kap",
                "method": "GET",
                "items_path": "items",
                "start_param": "startAt",
                "end_param": "endAt",
                "fields": {"disclosure_id": "id", "published_at": "publishedAt"},
            }
            raw.update(invalid_configs[index % len(invalid_configs)])
            try:
                MkkKapApiConfig.from_dict(raw)
                counts["silent_accept"] += 1
                errors.append(f"invalid config accepted: {invalid_configs[index % len(invalid_configs)]!r}")
            except KapApiConfigError:
                counts["config_controlled_reject"] += 1
            except Exception as exc:
                counts["uncontrolled"] += 1
                errors.append(f"config uncontrolled: {type(exc).__name__}: {exc}")

    for index in range(persistence_n):
        partial = index % 2 == 1
        result = KapFetchResult(
            disclosures=(disclosure(),),
            next_cursor=None,
            pages_fetched=1,
            start_at=NOW - timedelta(hours=1),
            end_at=NOW,
            completed_at=NOW,
            quarantined_items=(quarantine(),) if partial else (),
            complete=not partial,
        )
        conn = Conn()
        try:
            persist_kap_disclosures(conn, result)
            sqls = [sql for sql, _ in conn.cur.executed]
            checkpoint_written = any("INSERT INTO raw.kap_sync_state" in sql for sql in sqls)
            if checkpoint_written == partial:
                counts["silent_accept"] += 1
                errors.append("checkpoint quarantine sozlesmesi bozuk")
            elif partial:
                counts["persistence_quarantined"] += 1
            else:
                counts["persistence_complete"] += 1
        except Exception as exc:
            counts["uncontrolled"] += 1
            errors.append(f"persistence: {type(exc).__name__}: {exc}")

    counts["total_scenarios"] = sum(
        counts[key]
        for key in (
            "sync_plan_valid", "sync_plan_controlled_reject", "api_valid",
            "api_quarantined", "config_valid", "config_controlled_reject",
            "persistence_complete", "persistence_quarantined",
        )
    )
    return {
        "status": "PASS" if counts["uncontrolled"] == 0 and counts["silent_accept"] == 0 else "FAIL",
        "seed": seed,
        "counts": counts,
        "errors": errors[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed", type=int, default=9042026)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = run(seed=args.seed, smoke=args.smoke)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
