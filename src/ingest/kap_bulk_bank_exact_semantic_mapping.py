from __future__ import annotations

from typing import Iterable

from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.kap_bulk_semantic_adapter import exact_label_fact_code


PROFILE = "KAP_BULK_BANK_EXACT_LABEL_V1"
VERSION = 1
SECTOR_FAMILY = "BANK"


def _codes(items: Iterable[tuple[str, int, str]]) -> list[str]:
    return [exact_label_fact_code(f"{role}:{row}", label) for role, row, label in items]


def _rule(
    items: Iterable[tuple[str, int, str]],
    nature: str,
    *,
    dimensions_equals: dict[str, str],
    sign: str = "AS_IS",
) -> dict[str, object]:
    rule: dict[str, object] = {
        "source_codes": _codes(items),
        "nature": nature,
        "currency": "TRY",
        "statement_scope_priority": ["CONSOLIDATED", "SOLO"],
        "dimensions_equals": dimensions_equals,
        "period_start_policy": "REQUIRED" if nature == "YTD" else "FORBIDDEN",
    }
    if sign != "AS_IS":
        rule["sign"] = sign
    return rule


def build_bulk_exact_bank_semantic_config() -> SemanticMappingConfig:
    """Build the fail-closed BANK + participation-bank bulk mapping.

    Participation banks are routed through the production BANK family.  Every
    source identity below is bound to exact role + row + Turkish label, and the
    structured context filters prevent current/previous-period or dimensional
    columns from being silently interchanged.
    """
    fields = {
        "TOTAL_EQUITY": _rule(
            [
                ("banks_role_210010", 83, "ÖZKAYNAKLAR"),
                ("banks_role_210011", 82, "ÖZKAYNAKLAR"),
                ("par-banks_role_210013", 74, "ÖZKAYNAKLAR"),
                ("par-banks_role_210013", 77, "ÖZKAYNAKLAR"),
            ],
            "INSTANT",
            dimensions_equals={"context_period_side": "CURRENT", "context_member": "Toplam"},
        ),
        "ISSUED_CAPITAL": _rule(
            [
                ("banks_role_210010", 84, "Ödenmiş Sermaye"),
                ("banks_role_210011", 83, "Ödenmiş Sermaye"),
                ("par-banks_role_210013", 75, "Ödenmiş Sermaye"),
                ("par-banks_role_210013", 78, "Ödenmiş Sermaye"),
            ],
            "INSTANT",
            dimensions_equals={"context_period_side": "CURRENT", "context_member": "Toplam"},
        ),
        "NET_INCOME": _rule(
            [
                ("banks_role_310016", 63, "DÖNEM NET KARI VEYA ZARARI"),
                ("banks_role_310017", 64, "DÖNEM NET KARI VEYA ZARARI"),
                ("par-banks_role_310019", 64, "DÖNEM NET KARI VEYA ZARARI"),
            ],
            "YTD",
            dimensions_equals={"context_period_side": "CURRENT", "context_period_kind": "YTD"},
        ),
        "DIVIDENDS_PAID": _rule(
            [
                ("banks_role_610002", 39, "Dağıtılan Temettü"),
                ("banks_role_610003", 39, "Dağıtılan Temettü"),
                ("par-banks_role_610005", 39, "Dağıtılan Temettü"),
            ],
            "YTD",
            sign="ABS",
            dimensions_equals={
                "context_period_side": "CURRENT",
                "context_period_kind": "YTD",
                "context_member": "Toplam Özkaynak",
            },
        ),
    }
    return SemanticMappingConfig.from_dict(
        {
            "mapping_profile": PROFILE,
            "mapping_version": VERSION,
            "sector_family": SECTOR_FAMILY,
            "fields": fields,
        }
    )
