from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.analytics.bank_valuation_pipeline import BankValuationInputs
from src.analytics.kap_bank_end_to_end import (
    KapBankEndToEndError,
    evaluate_kap_bank_end_to_end,
)
from src.ingest.api.kap_financial_facts import KapFinancialFactConfig
from src.ingest.api.mkk_kap import KapDisclosureEnvelope
from src.ingest.api.semantic_facts import SemanticMappingConfig
from src.ingest.bank_fact_materializer import BankDerivationConfig, build_quarter_ends

ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = SemanticMappingConfig.from_json_file(
    str(ROOT / "config" / "kap_bank_semantic_mapping.official_v1.json")
)
DERIVATION = BankDerivationConfig.from_json_file(
    str(ROOT / "config" / "bank_fact_derivation.official_v1.json")
)
FACT_CONFIG = KapFinancialFactConfig.from_dict({
    "mapping_profile": "MKK_KAP_FINANCIAL_FACTS",
    "mapping_version": 1,
    "facts_path": "financialStatement.facts",
    "version_tag_path": "financialStatement.versionTag",
    "version_sequence_path": "financialStatement.versionSequence",
    "default_unit_scale": 1000,
    "default_currency": "TRY",
    "default_statement_scope": "CONSOLIDATED",
    "fields": {
        "fact_code": "code",
        "value": "value",
        "period_start": "periodStart",
        "period_end": "periodEnd",
        "currency": "currency",
        "unit_scale": "unitScale",
        "statement_scope": "statementScope"
    }
})
ANCHOR = date(2026, 3, 31)
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))


def canonical_payload_hash(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def envelope(period: date, idx: int, *, published_at=None, disclosure_id=None, equity=None):
    quarter_no = (period.month - 1) // 3 + 1
    published_at = published_at or datetime.combine(
        period + timedelta(days=40), datetime.min.time(), tzinfo=timezone.utc
    )
    disclosure_id = disclosure_id or f"D-{period.isoformat()}"
    equity = equity if equity is not None else 100_000_000 + idx * 2_000_000
    payload = {
        "financialStatement": {
            "versionTag": "ORIGINAL",
            "versionSequence": 1,
            "facts": [
                {
                    "code": "ifrs-full_Equity", "value": str(equity),
                    "periodEnd": period.isoformat(), "periodStart": None,
                    "currency": "TRY", "unitScale": 1000,
                    "statementScope": "CONSOLIDATED"
                },
                {
                    "code": "ifrs-full_IssuedCapital", "value": "10000000",
                    "periodEnd": period.isoformat(), "periodStart": None,
                    "currency": "TRY", "unitScale": 1000,
                    "statementScope": "CONSOLIDATED"
                },
                {
                    "code": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
                    "value": str(quarter_no * 5_000_000),
                    "periodStart": f"{period.year}-01-01",
                    "periodEnd": period.isoformat(), "currency": "TRY",
                    "unitScale": 1000, "statementScope": "CONSOLIDATED"
                },
                {
                    "code": "ifrs-full_DividendsPaid",
                    "value": str(-(quarter_no * 1_000_000)),
                    "periodStart": f"{period.year}-01-01",
                    "periodEnd": period.isoformat(), "currency": "TRY",
                    "unitScale": 1000, "statementScope": "CONSOLIDATED"
                }
            ]
        }
    }
    return KapDisclosureEnvelope(
        disclosure_id=disclosure_id,
        published_at=published_at,
        ticker="GARAN",
        company_id="C1",
        notification_type="FR",
        subject="Financial Report",
        source_url=f"https://kap.org.tr/tr/Bildirim/{disclosure_id}",
        payload=payload,
        payload_sha256=canonical_payload_hash(payload),
        fetched_at=max(ANALYSIS, published_at + timedelta(minutes=1)),
    )


def base_envelopes():
    return [envelope(period, idx) for idx, period in enumerate(build_quarter_ends(ANCHOR, 12))]


def evaluate(rows):
    return evaluate_kap_bank_end_to_end(
        rows,
        ticker="GARAN",
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC,
        derivation_config=DERIVATION,
        valuation_inputs=BankValuationInputs(
            coe=0.15,
            macro_cap=0.08,
            tier_cap=0.80,
            payout_missing_factor=0.70,
            band_width_shadow_mode=True,
            max_halfwidth=0.80,
        ),
        current_price=8.0,
        price_trade_date=ANALYSIS.date(),
        other_module_scores={"M1": 0.70, "M3": 0.60, "Ek4": 0.55, "Ek1": 0.80, "Ek9": 0.65},
        good_count_ge8=8,
        sector_residual_scales=[0.01] * 20,
    )


def test_raw_kap_to_total_rasyo_end_to_end_is_complete_and_deterministic():
    rows = base_envelopes()
    first = evaluate(rows)
    second = evaluate(list(reversed(rows)))
    assert first == second
    assert first["disclosures_used"] == 12
    assert first["raw_facts_extracted"] == 48
    assert len(first["disclosure_lineage"]) == 12
    assert all(len(row["payload_sha256"]) == 64 for row in first["disclosure_lineage"])
    assert first["config_lineage"] == {
        "fact_mapping_profile": "MKK_KAP_FINANCIAL_FACTS",
        "fact_mapping_version": 1,
        "semantic_profile": "KAP_TFRS_BANK_V1",
        "semantic_version": 1,
        "derivation_profile": "BANK_METRICS_KAP_TFRS_V1",
        "derivation_version": 1,
    }
    assert first["semantic_facts_mapped"] == 48
    assert first["bank_metrics_derived"] == 8
    assert first["canonical"]["roe_series"].count(None) == 0
    assert first["valuation"]["status"] == "OK"
    assert 0.0 <= first["m2"]["m2_score"] <= 1.0
    assert first["total_rasyo"]["module_scores"]["M2"] == first["m2"]["m2_score"]
    assert 0.0 <= first["total_rasyo"]["total_rasyo_100"] <= 100.0
    assert first["total_rasyo"]["decision"] in {"AL", "IZLE", "UZAK"}


def test_future_restatement_and_exact_duplicate_do_not_change_historical_result():
    rows = base_envelopes()
    baseline = evaluate(rows)
    future = envelope(
        ANCHOR,
        99,
        published_at=ANALYSIS + timedelta(hours=1),
        disclosure_id="FUTURE-RESTATED",
        equity=999_999_999,
    )
    result = evaluate(rows + [rows[0], future])
    assert result == baseline


def test_same_disclosure_id_with_different_payload_is_rejected():
    rows = base_envelopes()
    # Construct a valid envelope with the same id but changed payload.
    period = build_quarter_ends(ANCHOR, 12)[0]
    conflict = envelope(period, 0, disclosure_id=rows[0].disclosure_id, equity=123_456_789)
    with pytest.raises(KapBankEndToEndError, match="farkli payload"):
        evaluate(rows + [conflict])


def test_mixed_ticker_and_profile_mismatch_are_fail_closed():
    rows = base_envelopes()
    bad = rows[0]
    mixed = KapDisclosureEnvelope(**{**bad.__dict__, "ticker": "AKBNK"})
    with pytest.raises(KapBankEndToEndError, match="farkli ticker"):
        evaluate(rows + [mixed])

    wrong_derivation = BankDerivationConfig.from_dict({
        "derivation_profile": "X", "derivation_version": 1,
        "semantic_profile": "OTHER", "semantic_version": 1,
        "total_equity_field": "TOTAL_EQUITY", "shares_out_field": None,
        "issued_capital_field": "ISSUED_CAPITAL", "share_nominal_value": "1",
        "net_income_field": "NET_INCOME", "target_periods": 8, "history_periods": 12,
    })
    with pytest.raises(KapBankEndToEndError, match="profilleri eslesmiyor"):
        evaluate_kap_bank_end_to_end(
            rows, ticker="GARAN", analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            fact_config=FACT_CONFIG, semantic_config=SEMANTIC,
            derivation_config=wrong_derivation,
            valuation_inputs=BankValuationInputs(coe=0.15, macro_cap=0.08),
            current_price=8.0, price_trade_date=ANALYSIS.date(),
            other_module_scores={"M1": 0.7, "M3": 0.6, "Ek4": 0.55, "Ek1": 0.8, "Ek9": 0.65},
            good_count_ge8=8,
        )


def test_payload_hash_and_duplicate_identity_are_fail_closed():
    rows = base_envelopes()
    first = rows[0]
    bad_hash = KapDisclosureEnvelope(**{**first.__dict__, "payload_sha256": "0" * 64})
    with pytest.raises(KapBankEndToEndError, match="SHA256"):
        evaluate([bad_hash, *rows[1:]])

    changed_time = KapDisclosureEnvelope(**{
        **first.__dict__,
        "published_at": first.published_at + timedelta(seconds=1),
        "fetched_at": first.fetched_at + timedelta(seconds=1),
    })
    with pytest.raises(KapBankEndToEndError, match="farkli kimlik"):
        evaluate(rows + [changed_time])


def test_publication_cannot_be_materially_after_fetch_time():
    rows = base_envelopes()
    first = rows[0]
    impossible = KapDisclosureEnvelope(**{
        **first.__dict__,
        "published_at": first.fetched_at + timedelta(minutes=6),
    })
    with pytest.raises(KapBankEndToEndError, match="fetched_at sonrasinda"):
        evaluate([impossible, *rows[1:]])
