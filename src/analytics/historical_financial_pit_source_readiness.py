from __future__ import annotations

"""Fail-closed readiness contract for real historical financial PIT sources.

The closed replay adapters prove that production math can be replayed safely
from explicit PIT inputs.  They do not prove that the required 2021-2026 real
financial input corpus exists.  This module keeps those two claims separate.
"""

from dataclasses import dataclass
from typing import Mapping


class HistoricalFinancialPitSourceReadinessError(ValueError):
    pass


REQUIRED_SOURCE_CLASSES: tuple[str, ...] = (
    "KAP_FINANCIAL_REPORTS",
    "CORE_SEMANTIC_METRICS",
    "NONFIN_FINANCIALS",
    "HOLDING_NAV",
    "GYO_NAV",
    "INSURANCE_METRICS",
    "FINANCIAL_METRICS",
    "BANK_QUARTER_SLOTS",
    "BANK_ASSUMPTIONS",
    "M2_FOLLOW_CONTEXTS",
)

ALLOWED_STATUSES = frozenset({"OPEN", "ACQUISITION_PROVEN", "CLOSED"})


@dataclass(frozen=True)
class HistoricalFinancialPitSourceReadiness:
    contract: str
    statuses: Mapping[str, str]
    closed_requirements: tuple[str, ...]
    open_requirements: tuple[str, ...]
    ready: bool
    result: str


def evaluate_historical_financial_pit_source_readiness(
    statuses: Mapping[str, str],
) -> HistoricalFinancialPitSourceReadiness:
    if not isinstance(statuses, Mapping):
        raise HistoricalFinancialPitSourceReadinessError("statuses mapping olmali")

    required = set(REQUIRED_SOURCE_CLASSES)
    actual = set(statuses)
    missing = sorted(required - actual)
    foreign = sorted(actual - required)
    if missing or foreign:
        raise HistoricalFinancialPitSourceReadinessError(
            f"source class set exact olmali; missing={missing}, foreign={foreign}"
        )

    normalized: dict[str, str] = {}
    for key in REQUIRED_SOURCE_CLASSES:
        value = statuses[key]
        if not isinstance(value, str) or value.strip().upper() not in ALLOWED_STATUSES:
            raise HistoricalFinancialPitSourceReadinessError(
                f"{key} status OPEN/ACQUISITION_PROVEN/CLOSED olmali"
            )
        normalized[key] = value.strip().upper()

    closed = tuple(key for key in REQUIRED_SOURCE_CLASSES if normalized[key] == "CLOSED")
    open_requirements = tuple(key for key in REQUIRED_SOURCE_CLASSES if normalized[key] != "CLOSED")
    ready = not open_requirements
    return HistoricalFinancialPitSourceReadiness(
        contract="HISTORICAL_FINANCIAL_PIT_SOURCE_READINESS_V1",
        statuses=normalized,
        closed_requirements=closed,
        open_requirements=open_requirements,
        ready=ready,
        result="READY" if ready else "NOT_READY",
    )


def current_financial_pit_source_readiness() -> HistoricalFinancialPitSourceReadiness:
    """Current repository truth; update only when each source class is audited.

    Public KAP acquisition has live-CI proof, but the full 2021-2026 financial
    report corpus and semantic/sector-specific source classes are not closed.
    Therefore even KAP_FINANCIAL_REPORTS is intentionally not marked CLOSED.
    """

    return evaluate_historical_financial_pit_source_readiness(
        {
            "KAP_FINANCIAL_REPORTS": "ACQUISITION_PROVEN",
            "CORE_SEMANTIC_METRICS": "OPEN",
            "NONFIN_FINANCIALS": "OPEN",
            "HOLDING_NAV": "OPEN",
            "GYO_NAV": "OPEN",
            "INSURANCE_METRICS": "OPEN",
            "FINANCIAL_METRICS": "OPEN",
            "BANK_QUARTER_SLOTS": "OPEN",
            "BANK_ASSUMPTIONS": "OPEN",
            "M2_FOLLOW_CONTEXTS": "OPEN",
        }
    )
