from __future__ import annotations

import argparse
import json
import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

from src.ingest.api.mkk_contract import (  # noqa: E402
    validate_mkk_contract_sample,
    write_mkk_contract_capture,
    write_mkk_contract_lock,
)
from src.ingest.api.mkk_kap import (  # noqa: E402
    KapApiConfigError,
    KapApiProtocolError,
    MkkKapApiClient,
    MkkKapApiConfig,
)
from src.ingest.api.mkk_suite import (  # noqa: E402
    MkkProductSuite,
    plan_mkk_suite_backfill,
    validate_mkk_product_suite,
)

NOW = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


class Response:
    status_code = 200
    headers: dict[str, str] = {}
    content = b""

    def __init__(self, payload: Any):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


class Session:
    def __init__(self, payload: Any):
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        return Response(self.payload)


def config(source: str, product_path: str) -> dict[str, Any]:
    return {
        "base_url": "https://apiportal.mkk.com.tr",
        "api_key_header": "X-API-Key",
        "path": product_path,
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
            "ticker": "ticker",
        },
        "source_name": source,
    }


def sample(identifier: str) -> dict[str, Any]:
    return {"data": {"items": [{
        "id": identifier,
        "publishedAt": "2026-08-04T23:00:00+03:00",
        "ticker": "GARAN",
    }], "nextCursor": None}}


def build_suite(root: Path) -> MkkProductSuite:
    products = []
    for index in range(3):
        name = f"product_{index}"
        cfg_raw = config(f"MKK_SOURCE_{index}", f"/registered/product/{index}")
        cfg = MkkKapApiConfig.from_dict(cfg_raw)
        payload = sample(f"D{index}")
        cfg_path = root / f"{name}.config.json"
        sample_path = root / f"{name}.sample.json"
        lock_path = root / f"{name}.lock.json"
        cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
        sample_path.write_text(json.dumps(payload), encoding="utf-8")
        report = validate_mkk_contract_sample(cfg, payload, checked_at=NOW)
        write_mkk_contract_lock(lock_path, cfg, report)
        products.append({
            "product_name": name,
            "config": cfg_path.name,
            "sample": sample_path.name,
            "contract_lock": lock_path.name,
            "api_key_env": "MKK_SHARED_KEY",
            "stream_name": f"stream_{index}",
            "max_window_hours": 6 + index,
            "overlap_seconds": index * 30,
        })
    manifest = root / "suite.json"
    manifest.write_text(json.dumps({
        "suite_name": "AUDIT_SUITE",
        "suite_version": 1,
        "products": products,
    }), encoding="utf-8")
    return MkkProductSuite.from_json_file(manifest)


def run(*, smoke: bool = False) -> dict[str, Any]:
    factor = 20 if smoke else 1
    rng = random.Random(20260805)
    counts = {
        "valid_suite_plan": 0,
        "controlled_plan_reject": 0,
        "valid_capture": 0,
        "controlled_capture_reject": 0,
        "controlled_suite_reject": 0,
        "uncontrolled": 0,
        "silent_accept": 0,
    }

    with tempfile.TemporaryDirectory(prefix="total_rasyo_mkk_suite_audit_") as tmp:
        root = Path(tmp)
        suite = build_suite(root)
        validation = validate_mkk_product_suite(
            suite,
            checked_at=NOW,
            environment={"MKK_SHARED_KEY": "secret"},
            require_api_keys=True,
            require_live_ready=True,
        )

        for _ in range(4000 // factor):
            start = NOW - timedelta(days=rng.randint(1, 365))
            end = start + timedelta(hours=rng.uniform(1.0, 72.0))
            try:
                plans = plan_mkk_suite_backfill(
                    suite, validation, start_at=start, end_at=end,
                    max_window_hours=24.0, overlap_seconds=300,
                )
                assert len(plans) == 3
                for plan in plans:
                    assert plan.windows[0].start_at == start
                    assert plan.windows[-1].end_at == end
                    for left, right in zip(plan.windows, plan.windows[1:]):
                        assert right.start_at <= left.end_at
                        assert right.end_at > right.start_at
                counts["valid_suite_plan"] += 1
            except Exception:
                counts["uncontrolled"] += 1

        invalid_plan_cases = [
            {"max_window_hours": 0.0, "overlap_seconds": 0},
            {"max_window_hours": float("inf"), "overlap_seconds": 0},
            {"max_window_hours": 1.0, "overlap_seconds": 3600},
            {"max_window_hours": True, "overlap_seconds": 0},
        ]
        for index in range(2000 // factor):
            case = invalid_plan_cases[index % len(invalid_plan_cases)]
            try:
                plan_mkk_suite_backfill(
                    suite, validation, start_at=NOW, end_at=NOW + timedelta(days=1),
                    **case,
                )
            except (TypeError, ValueError, KapApiConfigError):
                counts["controlled_plan_reject"] += 1
            except Exception:
                counts["uncontrolled"] += 1
            else:
                counts["silent_accept"] += 1

        cfg = MkkKapApiConfig.from_dict(config("MKK_CAPTURE", "/registered/capture"))
        for index in range(1500 // factor):
            payload = sample(f"CAP{index}")
            session = Session(payload)
            client = MkkKapApiClient(
                cfg, "never-persist-this", session=session,
                clock=lambda: NOW,
            )
            try:
                capture = client.capture_contract_sample(
                    start_at=NOW - timedelta(hours=1), end_at=NOW,
                )
                sample_path = root / "captures" / f"sample-{index}.json"
                meta_path = root / "captures" / f"sample-{index}.meta.json"
                write_mkk_contract_capture(
                    sample_path=sample_path, metadata_path=meta_path,
                    config=cfg, capture=capture,
                )
                assert "never-persist-this" not in sample_path.read_text(encoding="utf-8")
                assert "never-persist-this" not in meta_path.read_text(encoding="utf-8")
                counts["valid_capture"] += 1
            except Exception:
                counts["uncontrolled"] += 1

        for index in range(1500 // factor):
            mode = index % 4
            payload = sample(f"BAD{index}")
            if mode == 0:
                payload["data"]["items"] = []
            elif mode == 1:
                payload["data"]["items"][0]["id"] = []
            elif mode == 2:
                payload["data"]["items"][0]["publishedAt"] = "bad-date"
            else:
                payload = {"data": {"wrong": []}}
            client = MkkKapApiClient(
                cfg, "secret", session=Session(payload), clock=lambda: NOW,
            )
            try:
                client.capture_contract_sample(
                    start_at=NOW - timedelta(hours=1), end_at=NOW,
                )
            except (KapApiConfigError, KapApiProtocolError, TypeError, ValueError):
                counts["controlled_capture_reject"] += 1
            except Exception:
                counts["uncontrolled"] += 1
            else:
                counts["silent_accept"] += 1

        for index in range(1000 // factor):
            raw = json.loads((root / "suite.json").read_text(encoding="utf-8"))
            mode = index % 4
            if mode == 0:
                raw["products"][1]["product_name"] = raw["products"][0]["product_name"]
            elif mode == 1:
                raw["products"][1]["stream_name"] = raw["products"][0]["stream_name"]
                config_path = root / raw["products"][1]["config"]
                config_raw = json.loads(config_path.read_text(encoding="utf-8"))
                config_raw["source_name"] = "MKK_SOURCE_0"
                config_path.write_text(json.dumps(config_raw), encoding="utf-8")
            elif mode == 2:
                raw["products"][0]["max_window_hours"] = float("inf")
            else:
                raw["products"][0]["enabled"] = "yes"
            mutated = root / f"mutated-{index}.json"
            mutated.write_text(json.dumps(raw), encoding="utf-8")
            try:
                candidate = MkkProductSuite.from_json_file(mutated)
                validate_mkk_product_suite(candidate, checked_at=NOW, environment={})
            except (KapApiConfigError, KapApiProtocolError, TypeError, ValueError, OSError):
                counts["controlled_suite_reject"] += 1
            except Exception:
                counts["uncontrolled"] += 1
            else:
                counts["silent_accept"] += 1

    status = "PASS" if counts["uncontrolled"] == 0 and counts["silent_accept"] == 0 else "FAIL"
    return {"status": status, "counts": counts, "scenario_count": sum(
        value for key, value in counts.items() if key not in {"uncontrolled", "silent_accept"}
    )}


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
