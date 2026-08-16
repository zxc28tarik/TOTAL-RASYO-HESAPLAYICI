#!/usr/bin/env python3
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import hashlib
import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.analytics.ratios_calc import QuarterSeries
from src.ingest.api.semantic_facts import SemanticFinancialFact
from src.ingest.company_fact_materializer import (
    CompanyDerivationConfig,
    CompanyDerivationError,
    build_quarter_ends,
    derive_company_quarters,
)
from src.ingest.sector_routing import SectorRoutingError, infer_sector_family

RNG = random.Random(20260805)
UTC = timezone.utc
ANALYSIS = datetime(2026, 5, 20, 12, tzinfo=UTC)
ANCHOR = date(2026, 3, 31)

CONFIG = CompanyDerivationConfig.from_dict({
    "derivation_profile": "SELF_AUDIT_NONBANK_CORE",
    "derivation_version": 1,
    "semantic_profile": "SELF_AUDIT_SEMANTIC",
    "semantic_version": 1,
    "sector_families": ["NONFIN", "HOLDING", "GYO", "INSURANCE", "FINANCIAL"],
    "currency": "TRY",
    "target_periods": 8,
    "history_periods": 12,
    "field_map": {
        "revenue": "REVENUE",
        "cogs": "COST_OF_SALES",
        "net_income": "NET_INCOME",
        "total_assets": "TOTAL_ASSETS",
        "total_equity": "TOTAL_EQUITY",
    },
    "shares_out_field": "SHARES_OUT",
    "issued_capital_field": "ISSUED_CAPITAL",
    "share_nominal_value": 1,
    "required_fields": ["revenue", "net_income", "total_assets", "total_equity"],
    "minimum_present_fields": ["revenue", "net_income", "total_assets", "total_equity"],
    "minimum_present_count": 3,
    "derive_gross_profit": True,
})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fact(
    field: str,
    period_end: date,
    value: Decimal,
    *,
    nature: str,
    ticker: str = "TSTAA",
    sector_family: str = "NONFIN",
    period_start: date | None = None,
    published_at: datetime | None = None,
    disclosure_id: str | None = None,
    version_sequence: int = 1,
    salt: str = "",
) -> SemanticFinancialFact:
    pub = published_at or datetime.combine(period_end + timedelta(days=45), time(9), tzinfo=UTC)
    disclosure = disclosure_id or f"D-{period_end}-{version_sequence}-{salt}"
    lineage = _sha(
        f"{ticker}|{field}|{period_start}|{period_end}|{value}|{pub.isoformat()}|"
        f"{version_sequence}|{disclosure}|{salt}"
    )
    return SemanticFinancialFact(
        source="SELF_AUDIT",
        disclosure_id=disclosure,
        ticker=ticker,
        published_at=pub,
        version_tag="RESTATED" if version_sequence > 1 else "ORIGINAL",
        version_sequence=version_sequence,
        sector_family=sector_family,
        semantic_profile=CONFIG.semantic_profile,
        semantic_version=CONFIG.semantic_version,
        canonical_field=field,
        nature=nature,
        period_start=period_start,
        period_end=period_end,
        currency=None if field == "SHARES_OUT" else "TRY",
        statement_scope="CONSOLIDATED",
        value=value,
        source_fact_code=field,
        source_fact_key=lineage,
        source_mapping_profile="SELF_AUDIT_RAW",
        source_mapping_version=1,
        dimensions={},
        lineage_sha256=lineage,
        mapped_at=pub + timedelta(minutes=1),
    )


def complete_facts(seed: int) -> list[SemanticFinancialFact]:
    rows: list[SemanticFinancialFact] = []
    for idx, period in enumerate(build_quarter_ends(ANCHOR, 12)):
        pub = datetime.combine(period + timedelta(days=40 + seed % 5), time(9), tzinfo=UTC)
        disclosure = f"D-{seed}-{period}"
        revenue = Decimal(100 + idx * 7 + seed % 13)
        cogs = -(revenue * Decimal("0.62"))
        net_income = Decimal(8 + idx + seed % 5)
        assets = Decimal(1000 + idx * 50 + seed % 17)
        equity = Decimal(400 + idx * 20 + seed % 11)
        for field, value, nature in (
            ("REVENUE", revenue, "QUARTER"),
            ("COST_OF_SALES", cogs, "QUARTER"),
            ("NET_INCOME", net_income, "QUARTER"),
            ("TOTAL_ASSETS", assets, "INSTANT"),
            ("TOTAL_EQUITY", equity, "INSTANT"),
            ("SHARES_OUT", Decimal(100 + seed % 7), "INSTANT"),
        ):
            rows.append(fact(
                field, period, value, nature=nature, published_at=pub,
                disclosure_id=disclosure, salt=str(seed),
            ))
    return rows


def _assert_metrics(rows) -> None:
    if len(rows) != 8:
        raise AssertionError(f"expected 8 target quarters, got {len(rows)}")
    periods = [row.period_end for row in rows]
    if periods != sorted(periods) or len(periods) != len(set(periods)):
        raise AssertionError("target periods not unique chronological")
    for row in rows:
        if row.published_at > ANALYSIS:
            raise AssertionError("look-ahead publication")
        if not row.is_complete:
            raise AssertionError("complete source produced partial quarter")
        if row.values["gross_profit"] is None:
            raise AssertionError("gross profit not derived")
        for value in row.values.values():
            if value is not None and not math.isfinite(float(value)):
                raise AssertionError("non-finite derived value")
        if len(row.lineage_sha256) != 64:
            raise AssertionError("bad lineage hash")


def run() -> dict:
    counts = {
        "valid_derivation": 0,
        "gap_preservation": 0,
        "controlled_reject": 0,
        "sector_routing": 0,
        "calendar_ratio": 0,
        "runtime_config_reject": 0,
    }
    unexpected: list[str] = []

    for i in range(3000):
        rows = complete_facts(i)
        try:
            first = derive_company_quarters(
                rows, config=CONFIG, ticker="TSTAA",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            RNG.shuffle(rows)
            second = derive_company_quarters(
                rows, config=CONFIG, ticker="TSTAA",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            if first != second:
                raise AssertionError("input order changed derived output")
            _assert_metrics(first)
            counts["valid_derivation"] += 1
        except Exception as exc:
            unexpected.append(f"valid[{i}] {type(exc).__name__}: {exc}")

    # Missing Q2 YTD must not be compressed into a fake Q3 quarter.
    q1, q2, q3 = date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)
    for i in range(2000):
        pub1 = datetime(2025, 5, 10, 9, tzinfo=UTC)
        pub3 = datetime(2025, 11, 10, 9, tzinfo=UTC)
        rows = [
            fact("REVENUE", q1, Decimal(100 + i % 7), nature="YTD", period_start=date(2025, 1, 1), published_at=pub1, disclosure_id=f"Q1-{i}"),
            fact("NET_INCOME", q1, Decimal(10), nature="YTD", period_start=date(2025, 1, 1), published_at=pub1, disclosure_id=f"Q1-{i}"),
            fact("TOTAL_ASSETS", q1, Decimal(1000), nature="INSTANT", published_at=pub1, disclosure_id=f"Q1-{i}"),
            fact("TOTAL_EQUITY", q1, Decimal(400), nature="INSTANT", published_at=pub1, disclosure_id=f"Q1-{i}"),
            fact("SHARES_OUT", q1, Decimal(100), nature="INSTANT", published_at=pub1, disclosure_id=f"Q1-{i}"),
            fact("REVENUE", q3, Decimal(360 + i % 9), nature="YTD", period_start=date(2025, 1, 1), published_at=pub3, disclosure_id=f"Q3-{i}"),
            fact("NET_INCOME", q3, Decimal(36), nature="YTD", period_start=date(2025, 1, 1), published_at=pub3, disclosure_id=f"Q3-{i}"),
            fact("TOTAL_ASSETS", q3, Decimal(1200), nature="INSTANT", published_at=pub3, disclosure_id=f"Q3-{i}"),
            fact("TOTAL_EQUITY", q3, Decimal(450), nature="INSTANT", published_at=pub3, disclosure_id=f"Q3-{i}"),
            fact("SHARES_OUT", q3, Decimal(100), nature="INSTANT", published_at=pub3, disclosure_id=f"Q3-{i}"),
        ]
        try:
            result = derive_company_quarters(
                rows, config=CONFIG, ticker="TSTAA",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            q3_rows = [row for row in result if row.period_end == q3]
            if q3_rows and q3_rows[0].values["revenue"] is not None:
                raise AssertionError("missing Q2 YTD was compressed")
            if any(row.period_end == q2 for row in result):
                raise AssertionError("missing quarter materialized as real data")
            counts["gap_preservation"] += 1
        except Exception as exc:
            unexpected.append(f"gap[{i}] {type(exc).__name__}: {exc}")

    bad_mutators = (
        lambda rows: [dict(bad=True)],
        lambda rows: [replace(rows[0], sector_family="BANK")],
        lambda rows: [replace(rows[0], lineage_sha256="bad")],
        lambda rows: [replace(rows[0], value=Decimal("Infinity"))],
    )
    for i in range(2000):
        rows = complete_facts(i)
        broken = bad_mutators[i % len(bad_mutators)](rows)
        try:
            derive_company_quarters(
                broken, config=CONFIG, ticker="TSTAA",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            unexpected.append(f"reject[{i}] silently accepted")
        except (CompanyDerivationError, ValueError, ArithmeticError):
            counts["controlled_reject"] += 1
        except Exception as exc:
            unexpected.append(f"reject[{i}] uncontrolled {type(exc).__name__}: {exc}")

    expected_routes = (
        ({"sector_index_code": "XBANK"}, "BANK"),
        ({"sector_index_code": "XUMAL"}, "FINANCIAL"),
        ({"sector_index_code": "XHOLD"}, "HOLDING"),
        ({"sector_index_code": "XGMYO"}, "GYO"),
        ({"sector_index_code": "XUSIN"}, "NONFIN"),
    )
    for i in range(2000):
        kwargs, expected = expected_routes[i % len(expected_routes)]
        try:
            actual = infer_sector_family(ticker=f"T{i:04d}", **kwargs)
            if actual != expected:
                raise AssertionError(f"route {actual} != {expected}")
            counts["sector_routing"] += 1
        except (SectorRoutingError, AssertionError) as exc:
            unexpected.append(f"route[{i}] {type(exc).__name__}: {exc}")

    for i in range(2000):
        rows = [
            {"period_end": date(2024, 6, 30), "revenue": 10 + i},
            {"period_end": date(2024, 9, 30), "revenue": 20 + i},
            # Q4 deliberately missing.
            {"period_end": date(2025, 3, 31), "revenue": 30 + i},
            {"period_end": date(2025, 6, 30), "revenue": 40 + i},
        ]
        try:
            qs = QuarterSeries(rows)
            if qs.sum4q(date(2025, 6, 30), "revenue") is not None:
                raise AssertionError("sum4q bridged missing quarter")
            if qs.lag(date(2025, 6, 30), "revenue", 4) != 10 + i:
                raise AssertionError("lag4q did not use calendar quarter")
            counts["calendar_ratio"] += 1
        except Exception as exc:
            unexpected.append(f"calendar[{i}] {type(exc).__name__}: {exc}")

    for i in range(1000):
        unsafe = replace(CONFIG, target_periods=0 if i % 2 == 0 else 99)
        try:
            derive_company_quarters(
                (), config=unsafe, ticker="TSTAA",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            unexpected.append(f"runtime-config[{i}] silently accepted")
        except CompanyDerivationError:
            counts["runtime_config_reject"] += 1
        except Exception as exc:
            unexpected.append(f"runtime-config[{i}] uncontrolled {type(exc).__name__}: {exc}")

    total = sum(counts.values())
    result = {
        "seed": 20260805,
        "scenario_count": total,
        **counts,
        "uncontrolled_or_silent": len(unexpected),
        "unexpected_examples": unexpected[:20],
        "status": "PASS" if not unexpected else "FAIL",
    }
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
