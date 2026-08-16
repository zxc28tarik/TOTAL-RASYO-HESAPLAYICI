#!/usr/bin/env python3
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json
from datetime import datetime, timedelta, timezone

from src.ingest.api.kap_financial_facts import (
    KapFinancialFactConfig,
    KapFinancialFactExtractor,
)
from src.ingest.api.mkk_kap import (
    KapApiProtocolError,
    KapDisclosureEnvelope,
    MkkKapApiClient,
    MkkKapApiConfig,
)
from src.ingest.api.kap_public_universe import KapPublicUniverseClient, KapUniverseError

NOW = datetime(2026, 8, 4, 16, 0, tzinfo=timezone.utc)

API_CONFIG = MkkKapApiConfig.from_dict({
    "base_url": "https://api.example.invalid",
    "api_key_header": "X-Api-Key",
    "path": "/kap/disclosures",
    "method": "GET",
    "items_path": "data.items",
    "start_param": "startAt",
    "end_param": "endAt",
    "fields": {
        "disclosure_id": "id",
        "published_at": "publishedAt",
        "ticker": "ticker",
        "company_id": "companyId",
        "notification_type": "type",
        "subject": "subject",
        "source_url": "url",
    },
})

FACT_CONFIG = KapFinancialFactConfig.from_dict({
    "mapping_profile": "SELF_AUDIT",
    "mapping_version": 1,
    "facts_path": "statement.facts",
    "version_tag_path": "statement.versionTag",
    "version_sequence_path": "statement.versionSequence",
    "dimensions_path": "dimensions",
    "default_unit_scale": 1,
    "default_currency": "TRY",
    "fields": {
        "fact_code": "code",
        "value": "value",
        "period_start": "periodStart",
        "period_end": "periodEnd",
        "unit_scale": "unitScale",
        "currency": "currency",
        "statement_scope": "scope",
    },
})


def api_item(i: int) -> dict:
    return {
        "id": i if i % 2 else f"D-{i}",
        "publishedAt": (NOW - timedelta(minutes=i % 1000)).isoformat(),
        "ticker": f"T{i % 997:04d}",
        "companyId": i,
        "type": "FINANCIAL_STATEMENT",
        "subject": f"Rapor {i}",
        "url": f"https://kap.org.tr/tr/Bildirim/{i}",
    }


def envelope(i: int, facts: list[dict]) -> KapDisclosureEnvelope:
    return KapDisclosureEnvelope(
        source="MKK_KAP_API",
        disclosure_id=f"D-{i}",
        published_at=NOW - timedelta(minutes=10),
        ticker=f"T{i % 997:04d}",
        company_id=str(i),
        notification_type="FINANCIAL_STATEMENT",
        subject="Rapor",
        source_url=f"https://kap.org.tr/tr/Bildirim/{i}",
        payload={
            "statement": {
                "versionTag": "ORIGINAL" if i % 2 else "RESTATED",
                "versionSequence": i % 5,
                "facts": facts,
            }
        },
        payload_sha256="a" * 64,
        fetched_at=NOW,
    )


def valid_fact(i: int) -> dict:
    return {
        "code": f"FACT_{i % 101}",
        "value": str((i % 100000) / 100),
        "periodStart": "2026-01-01",
        "periodEnd": "2026-03-31",
        "unitScale": [1, 1000, 1_000_000][i % 3],
        "currency": "TRY",
        "scope": "CONSOLIDATED",
        "dimensions": {"axis": f"A{i % 7}"},
    }


def run() -> dict:
    client = MkkKapApiClient(API_CONFIG, "secret")
    extractor = KapFinancialFactExtractor(FACT_CONFIG)
    valid_api = valid_facts = controlled_rejects = uncontrolled = 0
    unexpected: list[str] = []

    for i in range(5000):
        try:
            out = client._normalize_item(api_item(i), NOW)
            if out.disclosure_id not in {str(i), f"D-{i}"}:
                raise AssertionError("disclosure id drift")
            valid_api += 1
            facts = [valid_fact(i), valid_fact(i + 1)]
            result = extractor.extract(envelope(i, facts), extracted_at=NOW)
            if not result:
                raise AssertionError("valid facts empty")
            valid_facts += 1
        except Exception as exc:  # Valid domain must never fail.
            uncontrolled += 1
            unexpected.append(f"valid[{i}] {type(exc).__name__}: {exc}")

    invalid_builders = [
        lambda i: ({**api_item(i), "id": [i]}, "api"),
        lambda i: ({**api_item(i), "publishedAt": "2026-08-04T10:00:00"}, "api"),
        lambda i: ({**api_item(i), "publishedAt": "2026-08-05T10:00:00+00:00"}, "api"),
        lambda i: ({**api_item(i), "ticker": {"code": "GARAN"}}, "api"),
        lambda i: ([{**valid_fact(i), "periodEnd": "2026-03-31BOZUK"}], "fact"),
        lambda i: ([{**valid_fact(i), "value": True}], "fact"),
        lambda i: ([{**valid_fact(i), "value": "1e101"}], "fact"),
        lambda i: ([], "fact"),
        lambda i: ([{**valid_fact(i), "code": ["BAD"]}], "fact"),
        lambda i: ([{**valid_fact(i), "dimensions": {"x": "z" * 70000}}], "fact"),
    ]

    for i in range(15000):
        payload, kind = invalid_builders[i % len(invalid_builders)](i)
        try:
            if kind == "api":
                client._normalize_item(payload, NOW)
            else:
                extractor.extract(envelope(i, payload), extracted_at=NOW)
        except KapApiProtocolError:
            controlled_rejects += 1
        except Exception as exc:
            uncontrolled += 1
            unexpected.append(f"invalid[{i}] {type(exc).__name__}: {exc}")
        else:
            uncontrolled += 1
            unexpected.append(f"invalid[{i}] silently accepted")

    # Parser-level mutation checks: duplicate ticker conflict and suspicious empty page.
    for i in range(200):
        ticker = f"A{i:03d}"
        html = (
            "<html><table>"
            f'<tr><td>{ticker}</td><td><a href="/tr/sirket-bilgileri/ozet/{i}-x">SIRKET {i}</a></td></tr>'
            f'<tr><td>{ticker}</td><td><a href="/tr/sirket-bilgileri/ozet/{i+1}-y">BASKA {i}</a></td></tr>'
            "</table></html>"
        )
        try:
            KapPublicUniverseClient.parse_html(html)
        except KapUniverseError:
            controlled_rejects += 1
        except Exception as exc:
            uncontrolled += 1
            unexpected.append(f"universe[{i}] {type(exc).__name__}: {exc}")
        else:
            uncontrolled += 1
            unexpected.append(f"universe[{i}] conflict silently accepted")

    report = {
        "valid_api_items": valid_api,
        "valid_fact_batches": valid_facts,
        "controlled_rejects": controlled_rejects,
        "uncontrolled_or_silent_failures": uncontrolled,
        "unexpected_examples": unexpected[:10],
        "total_scenarios": 5000 + 5000 + 15000 + 200,
    }
    if uncontrolled:
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
