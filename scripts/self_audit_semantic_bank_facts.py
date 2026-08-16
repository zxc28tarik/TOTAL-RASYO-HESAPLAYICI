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
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from src.ingest.api.kap_financial_facts import KapFinancialFact
from src.ingest.api.mkk_kap import KapApiProtocolError
from src.ingest.api.semantic_facts import (
    SemanticFactMapper,
    SemanticFinancialFact,
    SemanticMappingConfig,
)
from src.ingest.bank_fact_materializer import (
    BankDerivationConfig,
    BankDerivationError,
    build_quarter_ends,
    derive_bank_metrics,
)

RNG = random.Random(20260804)
ANALYSIS = datetime(2026, 5, 15, 12, tzinfo=timezone.utc)
ANCHOR = date(2026, 3, 31)

SEMANTIC_CONFIG = SemanticMappingConfig.from_dict({
    "mapping_profile": "SELF_AUDIT_BANK_SEMANTIC",
    "mapping_version": 1,
    "sector_family": "BANK",
    "fields": {
        "TOTAL_EQUITY": {"source_codes": ["EQ"], "nature": "INSTANT"},
        "SHARES_OUT": {"source_codes": ["SH"], "nature": "INSTANT"},
        "NET_INCOME": {
            "source_codes": ["NI"], "nature": "YTD",
            "period_start_policy": "REQUIRED",
        },
        "PAYOUT_RATIO": {"source_codes": ["PAY"], "nature": "RATIO"},
        "TOTAL_LOANS": {
            "source_codes": ["LOANS"], "nature": "INSTANT",
            "dimensions_equals": {"member": "TOTAL"},
        },
        "NPL_LOANS": {
            "source_codes": ["LOANS"], "nature": "INSTANT",
            "dimensions_equals": {"member": "NPL"},
        },
    },
})
MAPPER = SemanticFactMapper(SEMANTIC_CONFIG)

BANK_CONFIG = BankDerivationConfig.from_dict({
    "derivation_profile": "SELF_AUDIT_BANK_METRICS",
    "derivation_version": 1,
    "semantic_profile": SEMANTIC_CONFIG.mapping_profile,
    "semantic_version": SEMANTIC_CONFIG.mapping_version,
    "total_equity_field": "TOTAL_EQUITY",
    "shares_out_field": "SHARES_OUT",
    "net_income_field": "NET_INCOME",
    "payout_ratio_field": "PAYOUT_RATIO",
    "currency": "TRY",
    "target_periods": 8,
    "history_periods": 12,
})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_fact(
    code: str,
    value: Decimal,
    *,
    disclosure_id: str,
    period_end: date,
    published_at: datetime,
    period_start: date | None = None,
    dimensions: dict | None = None,
    fact_key_suffix: str = "",
) -> KapFinancialFact:
    key = _sha(
        f"{disclosure_id}|{code}|{period_start}|{period_end}|{dimensions}|{fact_key_suffix}"
    )
    return KapFinancialFact(
        source="MKK_KAP_API",
        disclosure_id=disclosure_id,
        mapping_profile="SELF_AUDIT_RAW",
        mapping_version=1,
        fact_key=key,
        ticker="GARAN",
        published_at=published_at,
        version_tag="ORIGINAL",
        version_sequence=1,
        fact_code=code,
        period_start=period_start,
        period_end=period_end,
        currency="TRY",
        unit_scale=1,
        raw_value_text=str(value),
        normalized_value=value,
        scaled_value=value,
        statement_scope="CONSOLIDATED",
        dimensions={} if dimensions is None else dimensions,
        extracted_at=published_at + timedelta(minutes=1),
    )


def semantic_fact(
    field: str,
    period_end: date,
    value: Decimal,
    *,
    nature: str,
    period_start: date | None = None,
    published_at: datetime | None = None,
    version_sequence: int = 1,
    salt: str = "",
) -> SemanticFinancialFact:
    pub = published_at or datetime.combine(
        period_end + timedelta(days=45), time(9), tzinfo=timezone.utc
    )
    lineage = _sha(f"{field}|{period_end}|{value}|{pub.isoformat()}|{version_sequence}|{salt}")
    return SemanticFinancialFact(
        source="MKK_KAP_API",
        disclosure_id="D-" + lineage[:16],
        ticker="GARAN",
        published_at=pub,
        version_tag="RESTATED" if version_sequence > 1 else "ORIGINAL",
        version_sequence=version_sequence,
        sector_family="BANK",
        semantic_profile=SEMANTIC_CONFIG.mapping_profile,
        semantic_version=SEMANTIC_CONFIG.mapping_version,
        canonical_field=field,
        nature=nature,
        period_start=period_start,
        period_end=period_end,
        currency="TRY",
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


def complete_semantic_facts(seed: int) -> list[SemanticFinancialFact]:
    slots = build_quarter_ends(ANCHOR, 12)
    rows: list[SemanticFinancialFact] = []
    ytd: dict[int, Decimal] = {}
    for idx, period in enumerate(slots):
        equity = Decimal(1000 + idx * 50 + seed % 17)
        shares = Decimal(100 + seed % 5)
        q = (period.month - 1) // 3 + 1
        profit = Decimal(q * 10 + seed % 7)
        ytd[period.year] = ytd.get(period.year, Decimal(0)) + profit
        rows.extend([
            semantic_fact("TOTAL_EQUITY", period, equity, nature="INSTANT", salt=str(seed)),
            semantic_fact("SHARES_OUT", period, shares, nature="INSTANT", salt=str(seed)),
            semantic_fact(
                "NET_INCOME", period, ytd[period.year], nature="YTD",
                period_start=date(period.year, 1, 1), salt=str(seed),
            ),
        ])
    rows.append(semantic_fact("PAYOUT_RATIO", slots[-1], Decimal("0.25"), nature="RATIO", salt=str(seed)))
    return rows


def _assert_metric_invariants(metrics) -> None:
    if len(metrics) != 8:
        raise AssertionError(f"expected 8 metrics, got {len(metrics)}")
    periods = [row.period_end for row in metrics]
    if periods != sorted(periods) or len(set(periods)) != len(periods):
        raise AssertionError("derived periods not unique chronological")
    for row in metrics:
        if len(row.lineage_sha256) != 64:
            raise AssertionError("bad metric lineage")
        if row.source_disclosure_id != "SEMANTIC:" + row.lineage_sha256:
            raise AssertionError("source id drift")
        if row.published_at > ANALYSIS:
            raise AssertionError("look-ahead lineage")
        if row.bvps is not None and (not math.isfinite(row.bvps) or row.bvps <= 0):
            raise AssertionError("bad bvps")
        if row.roe_ttm is not None and not math.isfinite(row.roe_ttm):
            raise AssertionError("bad roe")
        if row.payout_sus is not None and not (0 <= row.payout_sus <= 1):
            raise AssertionError("bad payout")
        for source in row.source_lineage:
            if datetime.fromisoformat(source["published_at"]) > ANALYSIS:
                raise AssertionError("future source in lineage")


def run() -> dict:
    valid_mapping = valid_derivation = controlled_rejects = 0
    uncontrolled_or_silent = 0
    unexpected: list[str] = []

    # Valid semantic mapping, including one source code split by dimensions.
    for i in range(3000):
        period = date(2026, 3, 31)
        pub = datetime(2026, 5, 10, 9, tzinfo=timezone.utc)
        disclosure = f"D-{i}"
        rows = [
            raw_fact("EQ", Decimal(1000 + i), disclosure_id=disclosure, period_end=period, published_at=pub),
            raw_fact("SH", Decimal(100), disclosure_id=disclosure, period_end=period, published_at=pub),
            raw_fact(
                "NI", Decimal(100 + i % 11), disclosure_id=disclosure,
                period_start=date(2026, 1, 1), period_end=period, published_at=pub,
            ),
            raw_fact("PAY", Decimal("0.25"), disclosure_id=disclosure, period_end=period, published_at=pub),
            raw_fact(
                "LOANS", Decimal(5000), disclosure_id=disclosure, period_end=period,
                published_at=pub, dimensions={"member": "TOTAL"}, fact_key_suffix="total",
            ),
            raw_fact(
                "LOANS", Decimal(250), disclosure_id=disclosure, period_end=period,
                published_at=pub, dimensions={"member": "NPL"}, fact_key_suffix="npl",
            ),
        ]
        RNG.shuffle(rows)
        try:
            first = MAPPER.map_facts(rows, mapped_at=pub + timedelta(minutes=2))
            second = MAPPER.map_facts(reversed(rows), mapped_at=pub + timedelta(minutes=2))
            if first != second:
                raise AssertionError("mapping input-order drift")
            values = {row.canonical_field: row.value for row in first}
            if values["TOTAL_LOANS"] != Decimal(5000) or values["NPL_LOANS"] != Decimal(250):
                raise AssertionError("dimension selector drift")
            valid_mapping += 1
        except Exception as exc:  # valid domain must never fail
            uncontrolled_or_silent += 1
            unexpected.append(f"valid-map[{i}] {type(exc).__name__}: {exc}")

    # Valid BANK derivation with order mutation and deterministic lineage.
    for i in range(3000):
        rows = complete_semantic_facts(i)
        try:
            first = derive_bank_metrics(
                rows, config=BANK_CONFIG, ticker="GARAN",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            RNG.shuffle(rows)
            second = derive_bank_metrics(
                rows, config=BANK_CONFIG, ticker="GARAN",
                analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            )
            if first != second:
                raise AssertionError("derivation input-order drift")
            _assert_metric_invariants(first)
            valid_derivation += 1
        except Exception as exc:
            uncontrolled_or_silent += 1
            unexpected.append(f"valid-derive[{i}] {type(exc).__name__}: {exc}")

    invalid_cases = []
    base_raw = raw_fact(
        "EQ", Decimal(1000), disclosure_id="BAD", period_end=date(2026, 3, 31),
        published_at=datetime(2026, 5, 10, 9, tzinfo=timezone.utc),
    )
    invalid_cases.extend([
        lambda i: MAPPER.map_facts(123, mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([{"fact": i}], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([replace(base_raw, fact_key="bad")], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([replace(base_raw, scaled_value=Decimal("NaN"))], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([replace(base_raw, period_end=date(2027, 3, 31))], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([replace(base_raw, dimensions=[])], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts([replace(base_raw, fact_code="UNKNOWN")], mapped_at=ANALYSIS),
        lambda i: MAPPER.map_facts(
            [base_raw, replace(base_raw, disclosure_id="OTHER", fact_key=_sha(f"other-{i}"))],
            mapped_at=ANALYSIS,
        ),
        lambda i: derive_bank_metrics(
            [replace(complete_semantic_facts(i)[0], lineage_sha256="bad")],
            config=BANK_CONFIG, ticker="GARAN", analysis_at=ANALYSIS,
            anchor_period_end=ANCHOR,
        ),
        lambda i: derive_bank_metrics(
            complete_semantic_facts(i), config=BANK_CONFIG, ticker="GARAN",
            analysis_at=datetime(2026, 5, 15, 12), anchor_period_end=ANCHOR,
        ),
    ])

    for i in range(12000):
        case = invalid_cases[i % len(invalid_cases)]
        try:
            case(i)
        except (KapApiProtocolError, BankDerivationError, ValueError, ArithmeticError):
            controlled_rejects += 1
        except Exception as exc:
            uncontrolled_or_silent += 1
            unexpected.append(f"invalid[{i}] {type(exc).__name__}: {exc}")
        else:
            uncontrolled_or_silent += 1
            unexpected.append(f"invalid[{i}] silently accepted")

    # Mutation checks whose correct outcome is a controlled diagnostic, not exception.
    mutation_checks = 0
    for i in range(1000):
        rows = complete_semantic_facts(i)
        missing_period = date(2025, 9, 30)
        rows = [
            row for row in rows
            if not (row.canonical_field == "NET_INCOME" and row.period_end == missing_period)
        ]
        result = derive_bank_metrics(
            rows, config=BANK_CONFIG, ticker="GARAN",
            analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
        )
        anchor_row = next(row for row in result if row.period_end == ANCHOR)
        if anchor_row.roe_ttm is not None:
            uncontrolled_or_silent += 1
            unexpected.append(f"missing-quarter[{i}] silently compressed")
        else:
            mutation_checks += 1

    report = {
        "valid_semantic_mappings": valid_mapping,
        "valid_bank_derivations": valid_derivation,
        "controlled_invalid_rejections": controlled_rejects,
        "missing_quarter_mutation_checks": mutation_checks,
        "total_scenarios": 3000 + 3000 + 12000 + 1000,
        "uncontrolled_or_silent_failures": uncontrolled_or_silent,
        "unexpected_examples": unexpected[:10],
    }
    if uncontrolled_or_silent:
        raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
