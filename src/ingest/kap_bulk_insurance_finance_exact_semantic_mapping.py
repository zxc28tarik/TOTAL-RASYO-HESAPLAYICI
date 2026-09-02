from __future__ import annotations

from typing import Iterable

from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.kap_bulk_semantic_adapter import exact_label_fact_code


INSURANCE_PROFILE = "KAP_BULK_INSURANCE_EXACT_LABEL_V1"
FINANCIAL_PROFILE = "KAP_BULK_FINANCIAL_EXACT_LABEL_V1"
VERSION = 1


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


def build_bulk_exact_insurance_semantic_config() -> SemanticMappingConfig:
    """Exact KAP insurance mapping observed across all 28 verified snapshot archives.

    The target insurance role/row/label identities below remain stable from the
    2019Q3 archive through 2026Q2 in the full discovery evidence. Duration facts
    stay YTD here: TTM construction belongs to the downstream point-in-time
    metrics materializer and must not be inferred from one report.
    """
    current_instant = {
        "context_period_side": "CURRENT",
        "context_period_kind": "INSTANT",
    }
    current_ytd = {
        "context_period_side": "CURRENT",
        "context_period_kind": "YTD",
    }
    role_b = "insurance_role_210004"
    role_i = "insurance_role_310010"
    fields = {
        "TOTAL_ASSETS": _rule(
            [(role_b, 138, "TOPLAM VARLIKLAR")], "INSTANT",
            dimensions_equals=current_instant,
        ),
        "TOTAL_EQUITY": _rule(
            [(role_b, 275, "ÖZSERMAYE TOPLAMI")], "INSTANT",
            dimensions_equals=current_instant,
        ),
        "ISSUED_CAPITAL": _rule(
            [(role_b, 249, "ÖDENMİŞ SERMAYE")], "INSTANT",
            dimensions_equals=current_instant,
        ),
        "NET_INCOME": _rule(
            [(role_i, 133, "DÖNEM NET KARI VEYA ZARARI")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "WRITTEN_PREMIUMS_NON_LIFE": _rule(
            [(role_i, 5, "Yazılan Primler (Reasürör Payı Düşülmüş Olarak)")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "EARNED_PREMIUMS_NON_LIFE": _rule(
            [(role_i, 4, "Kazanılmış Primler (Reasürör Payı Düşülmüş Olarak)")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "NET_CLAIMS_NON_LIFE": _rule(
            [(role_i, 22, "Gerçekleşen Tazminatlar (Reasürör Payı Düşülmüş Olarak) (+/-)")],
            "YTD", dimensions_equals=current_ytd, sign="ABS",
        ),
        "OPERATING_EXPENSES_NON_LIFE": _rule(
            [(role_i, 33, "Faaliyet Giderleri (-)")], "YTD",
            dimensions_equals=current_ytd, sign="ABS",
        ),
        "TECHNICAL_RESULT_NON_LIFE": _rule(
            [(role_i, 40, "TEKNİK BÖLÜM DENGESİ - HAYAT DIŞI")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "WRITTEN_PREMIUMS_LIFE": _rule(
            [(role_i, 43, "Yazılan Primler (Reasürör Payı Düşülmüş Olarak)")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "EARNED_PREMIUMS_LIFE": _rule(
            [(role_i, 42, "Kazanılmış Primler (Reasürör Payı Düşülmüş Olarak)")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "NET_CLAIMS_LIFE": _rule(
            [(role_i, 59, "Gerçekleşen Tazminatlar (Reasürör Payı Düşülmüş Olarak) (+/-)")],
            "YTD", dimensions_equals=current_ytd, sign="ABS",
        ),
        "OPERATING_EXPENSES_LIFE": _rule(
            [(role_i, 77, "Faaliyet Giderleri (-)")], "YTD",
            dimensions_equals=current_ytd, sign="ABS",
        ),
        "TECHNICAL_RESULT_LIFE": _rule(
            [(role_i, 81, "TEKNİK BÖLÜM DENGESİ - HAYAT")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "TECHNICAL_RESULT_PENSION": _rule(
            [(role_i, 96, "TEKNİK BÖLÜM DENGESİ - EMEKLİLİK")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "TECHNICAL_RESULT_TOTAL": _rule(
            [(role_i, 101, "GENEL TEKNİK BÖLÜM DENGESİ")], "YTD",
            dimensions_equals=current_ytd,
        ),
        "INVESTMENT_INCOME": _rule(
            [(role_i, 102, "YATIRIM GELİRLERİ")], "YTD",
            dimensions_equals=current_ytd,
        ),
    }
    return SemanticMappingConfig.from_dict({
        "mapping_profile": INSURANCE_PROFILE,
        "mapping_version": VERSION,
        "sector_family": "INSURANCE",
        "fields": fields,
    })


def build_bulk_exact_financial_semantic_config() -> SemanticMappingConfig:
    """Exact KAP non-bank financial mapping for both observed taxonomy row epochs.

    Full 28-archive discovery proves a coordinated row shift beginning with
    2022Q1 while the role names and Turkish labels remain stable. Only those
    actually observed pre-2022 and 2022+ identities are accepted. The three
    receivable bases remain separate so FACTORING, LEASING and CONSUMER_FINANCE
    materializers can select the correct base. Average equity and TTM metrics are
    downstream PIT derivations.
    """
    current_total = {
        "context_period_side": "CURRENT",
        "context_period_kind": "INSTANT",
        "context_member": "Toplam",
    }
    current_ytd = {
        "context_period_side": "CURRENT",
        "context_period_kind": "YTD",
    }
    role_b = "finance_role_210014"
    role_i = "finance_role_310020"
    fields = {
        "TOTAL_ASSETS": _rule(
            [
                (role_b, 37, "VARLIKLAR TOPLAMI"),
                (role_b, 40, "VARLIKLAR TOPLAMI"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "TOTAL_EQUITY": _rule(
            [
                (role_b, 58, "ÖZKAYNAKLAR"),
                (role_b, 62, "ÖZKAYNAKLAR"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "ISSUED_CAPITAL": _rule(
            [
                (role_b, 59, "Ödenmiş Sermaye"),
                (role_b, 63, "Ödenmiş Sermaye"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "FACTORING_RECEIVABLES": _rule(
            [(role_b, 9, "Faktoring Alacakları")], "INSTANT",
            dimensions_equals=current_total,
        ),
        "CONSUMER_FINANCE_RECEIVABLES": _rule(
            [
                (role_b, 12, "Finansman Kredileri"),
                (role_b, 15, "Finansman Kredileri"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "LEASING_RECEIVABLES_NET": _rule(
            [
                (role_b, 16, "Kiralama İşlemleri (Net)"),
                (role_b, 19, "Kiralama İşlemleri (Net)"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "NPL_GROSS": _rule(
            [
                (role_b, 21, "Takipteki Alacaklar"),
                (role_b, 24, "Takipteki Alacaklar"),
            ],
            "INSTANT", dimensions_equals=current_total,
        ),
        "PROVISIONS": _rule(
            [
                (role_b, 22, "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)"),
                (role_b, 25, "Beklenen Zarar Karşılıkları / Özel Karşılıklar (-)"),
            ],
            "INSTANT", dimensions_equals=current_total, sign="ABS",
        ),
        "NET_INCOME": _rule(
            [
                (role_i, 77, "DÖNEM NET KARI VEYA ZARARI"),
                (role_i, 81, "DÖNEM NET KARI VEYA ZARARI"),
            ],
            "YTD", dimensions_equals=current_ytd,
        ),
        "NET_FINANCE_INCOME": _rule(
            [
                (role_i, 25, "BRÜT KAR (ZARAR)"),
                (role_i, 29, "BRÜT KAR (ZARAR)"),
            ],
            "YTD", dimensions_equals=current_ytd,
        ),
        "FUNDING_COSTS": _rule(
            [
                (role_i, 18, "FİNANSMAN GİDERLERİ (-)"),
                (role_i, 21, "FİNANSMAN GİDERLERİ (-)"),
            ],
            "YTD", dimensions_equals=current_ytd, sign="ABS",
        ),
        "OPERATING_EXPENSES": _rule(
            [
                (role_i, 26, "ESAS FAALİYET GİDERLERİ (-)"),
                (role_i, 30, "ESAS FAALİYET GİDERLERİ (-)"),
            ],
            "YTD", dimensions_equals=current_ytd, sign="ABS",
        ),
    }
    return SemanticMappingConfig.from_dict({
        "mapping_profile": FINANCIAL_PROFILE,
        "mapping_version": VERSION,
        "sector_family": "FINANCIAL",
        "fields": fields,
    })
