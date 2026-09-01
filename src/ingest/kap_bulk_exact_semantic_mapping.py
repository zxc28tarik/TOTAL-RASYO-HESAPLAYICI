from __future__ import annotations

from typing import Iterable

from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.company_fact_materializer import CompanyDerivationConfig
from src.ingest.kap_bulk_semantic_adapter import exact_label_fact_code


PROFILE = "KAP_BULK_GENERAL_HOLDING_EXACT_LABEL_V1"
VERSION = 1
SUPPORTED_FAMILIES = frozenset({"NONFIN", "HOLDING"})


def _codes(items: Iterable[tuple[str, int, str]]) -> list[str]:
    return [exact_label_fact_code(f"{role}:{row}", label) for role, row, label in items]


def _rule(items, nature: str, *, sign: str = "AS_IS") -> dict[str, object]:
    rule: dict[str, object] = {
        "source_codes": _codes(items), "nature": nature, "currency": "TRY",
        "statement_scope_priority": ["CONSOLIDATED", "SOLO"],
        "period_start_policy": "REQUIRED" if nature == "YTD" else "FORBIDDEN",
    }
    if sign != "AS_IS":
        rule["sign"] = sign
    return rule


def build_bulk_exact_semantic_config(sector_family: str) -> SemanticMappingConfig:
    family = str(sector_family).strip().upper()
    if family not in SUPPORTED_FAMILIES:
        raise ValueError(f"bulk exact semantic family desteklenmiyor: {family}")
    if family == "NONFIN":
        b = "general_role_210015"
        income = ("general_role_310000", "general_role_310003")
        revenue = [(role, 3, "Hasılat") for role in income]
        cogs = [(role, 4, "Satışların Maliyeti") for role in income]
        gross = [(role, 31, "BRÜT KAR (ZARAR)") for role in income]
        operating = [(role, 38, "ESAS FAALİYET KARI (ZARARI)") for role in income]
        finance = [(role, 51, "Finansman Giderleri") for role in income]
        net = [(role, 62, "Ana Ortaklık Payları") for role in income] + [(role, 59, "DÖNEM KARI (ZARARI)") for role in income]
        cfo = [("general_role_520003", 2, "İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI"), ("general_role_510011", 2, "İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI")]
        capex = [("general_role_520003", 131, "Maddi ve Maddi Olmayan Duran Varlıkların Alımından Kaynaklanan Nakit Çıkışları"), ("general_role_520003", 133, "Maddi ve Maddi Olmayan Duran Varlıkların Alımından Kaynaklanan Nakit Çıkışları"), ("general_role_510011", 45, "Maddi ve Maddi Olmayan Duran Varlıkların Alımından Kaynaklanan Nakit Çıkışları")]
    else:
        b = "holding_role_210015"
        income = ("holding_role_310004", "holding_role_310030")
        revenue = [(role, 15, "TOPLAM HASILAT") for role in income] + [(role, 3, "Hasılat") for role in income]
        cogs = [(role, 16, "Satışların Maliyeti") for role in income]
        gross = [(role, 33, "BRÜT KAR (ZARAR)") for role in income]
        operating = [("holding_role_310004", 41, "ESAS FAALİYET KARI (ZARARI)"), ("holding_role_310030", 40, "ESAS FAALİYET KARI (ZARARI)")]
        finance = [(role, 53, "Finansman Giderleri") for role in income]
        net = [(role, 64, "Ana Ortaklık Payları") for role in income] + [(role, 61, "DÖNEM KARI (ZARARI)") for role in income]
        cfo = [("holding_role_520003", 2, "İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI")]
        capex = [("holding_role_520003", 133, "Maddi ve Maddi Olmayan Duran Varlıkların Alımından Kaynaklanan Nakit Çıkışları")]
    fields = {
        "REVENUE": _rule(revenue, "YTD"), "COST_OF_SALES": _rule(cogs, "YTD", sign="ABS"),
        "GROSS_PROFIT": _rule(gross, "YTD"), "OPERATING_PROFIT": _rule(operating, "YTD"),
        "NET_INCOME": _rule(net, "YTD"), "FINANCE_COSTS": _rule(finance, "YTD", sign="ABS"),
        "TOTAL_ASSETS": _rule([(b,129,"TOPLAM VARLIKLAR")], "INSTANT"),
        "TOTAL_EQUITY": _rule([(b,244,"Ana Ortaklığa Ait Özkaynaklar")], "INSTANT"),
        "CURRENT_ASSETS": _rule([(b,55,"TOPLAM DÖNEN VARLIKLAR")], "INSTANT"),
        "CURRENT_LIABILITIES": _rule([(b,191,"TOPLAM KISA VADELİ YÜKÜMLÜLÜKLER")], "INSTANT"),
        "CASH_AND_EQUIVALENTS": _rule([(b,4,"Nakit ve Nakit Benzerleri")], "INSTANT"),
        "SHORT_TERM_INVESTMENTS": _rule([(b,6,"Finansal Yatırımlar")], "INSTANT"),
        "TRADE_RECEIVABLES": _rule([(b,22,"Ticari Alacaklar")], "INSTANT"),
        "INVENTORY": _rule([(b,40,"Stoklar")], "INSTANT"),
        "SHORT_TERM_BORROWINGS": _rule([(b,132,"Kısa Vadeli Borçlanmalar")], "INSTANT"),
        "CURRENT_PORTION_LONG_TERM_BORROWINGS": _rule([(b,143,"Uzun Vadeli Borçlanmaların Kısa Vadeli Kısımları")], "INSTANT"),
        "LONG_TERM_BORROWINGS": _rule([(b,193,"Uzun Vadeli Borçlanmalar")], "INSTANT"),
        "CASH_FLOW_FROM_OPERATIONS": _rule(cfo, "YTD"), "CAPEX": _rule(capex, "YTD", sign="ABS"),
        "ISSUED_CAPITAL": _rule([(b,245,"Ödenmiş Sermaye")], "INSTANT"),
    }
    return SemanticMappingConfig.from_dict({"mapping_profile":PROFILE,"mapping_version":VERSION,"sector_family":family,"fields":fields})


def build_bulk_exact_company_derivation_config(sector_family: str) -> CompanyDerivationConfig:
    family = str(sector_family).strip().upper()
    if family not in SUPPORTED_FAMILIES:
        raise ValueError(f"bulk exact derivation family desteklenmiyor: {family}")
    return CompanyDerivationConfig.from_dict({
        "derivation_profile":"KAP_BULK_GENERAL_HOLDING_CORE_EXACT_V1","derivation_version":1,
        "semantic_profile":PROFILE,"semantic_version":VERSION,"sector_families":[family],"currency":"TRY",
        "target_periods":8,"history_periods":12,
        "field_map":{"revenue":"REVENUE","cogs":"COST_OF_SALES","gross_profit":"GROSS_PROFIT","ebit":"OPERATING_PROFIT","net_income":"NET_INCOME","interest_exp":"FINANCE_COSTS","total_assets":"TOTAL_ASSETS","total_equity":"TOTAL_EQUITY","current_assets":"CURRENT_ASSETS","current_liabilities":"CURRENT_LIABILITIES","cash_and_eq":"CASH_AND_EQUIVALENTS","st_investments":"SHORT_TERM_INVESTMENTS","receivables":"TRADE_RECEIVABLES","inventory":"INVENTORY","debt_st":"SHORT_TERM_BORROWINGS","debt_lt":"LONG_TERM_BORROWINGS","cfo":"CASH_FLOW_FROM_OPERATIONS","capex":"CAPEX"},
        "issued_capital_field":"ISSUED_CAPITAL","share_nominal_value":1,
        "required_fields":["revenue","net_income","total_assets","total_equity"],
        "minimum_present_fields":["revenue","net_income","total_assets","total_equity"],"minimum_present_count":2,
        "derive_gross_profit":True,
    })
