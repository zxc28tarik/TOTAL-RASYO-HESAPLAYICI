from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.analytics.bank_valuation_pipeline import BankValuationInputs
from src.analytics.kap_bank_end_to_end import (
    KapBankEndToEndError,
    KapBankEvaluationContext,
    evaluate_kap_bank_batch_end_to_end,
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
        "statement_scope": "statementScope",
    },
})
ANCHOR = date(2026, 3, 31)
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
TICKERS = ("AKBNK", "GARAN", "YKBNK")


def _hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _envelope(ticker: str, period: date, idx: int, *, broken: bool = False):
    quarter_no = (period.month - 1) // 3 + 1
    ticker_offset = {"AKBNK": 1, "GARAN": 2, "YKBNK": 3}[ticker]
    published_at = datetime.combine(
        period + timedelta(days=40), datetime.min.time(), tzinfo=timezone.utc
    )
    facts = [
        {
            "code": "ifrs-full_Equity",
            "value": str(90_000_000 + ticker_offset * 5_000_000 + idx * 2_000_000),
            "periodEnd": period.isoformat(), "periodStart": None,
            "currency": "TRY", "unitScale": 1000,
            "statementScope": "CONSOLIDATED",
        },
        {
            "code": "ifrs-full_IssuedCapital", "value": "10000000",
            "periodEnd": period.isoformat(), "periodStart": None,
            "currency": "TRY", "unitScale": 1000,
            "statementScope": "CONSOLIDATED",
        },
        {
            "code": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "value": str(quarter_no * (4_000_000 + ticker_offset * 500_000)),
            "periodStart": f"{period.year}-01-01", "periodEnd": period.isoformat(),
            "currency": "TRY", "unitScale": 1000,
            "statementScope": "CONSOLIDATED",
        },
        {
            "code": "ifrs-full_DividendsPaid",
            "value": str(-(quarter_no * 800_000)),
            "periodStart": f"{period.year}-01-01", "periodEnd": period.isoformat(),
            "currency": "TRY", "unitScale": 1000,
            "statementScope": "CONSOLIDATED",
        },
    ]
    if broken:
        facts[0]["value"] = "BOZUK"
    payload = {
        "financialStatement": {
            "versionTag": "ORIGINAL",
            "versionSequence": 1,
            "facts": facts,
        }
    }
    disclosure_id = f"{ticker}-{period.isoformat()}"
    return KapDisclosureEnvelope(
        disclosure_id=disclosure_id,
        published_at=published_at,
        ticker=ticker,
        company_id=f"C-{ticker}",
        notification_type="FR",
        subject="Financial Report",
        source_url=f"https://kap.org.tr/tr/Bildirim/{disclosure_id}",
        payload=payload,
        payload_sha256=_hash(payload),
        fetched_at=max(ANALYSIS, published_at + timedelta(minutes=1)),
    )


def _rows(*, broken_ticker: str | None = None):
    rows = []
    periods = build_quarter_ends(ANCHOR, 12)
    for ticker in TICKERS:
        for idx, period in enumerate(periods):
            rows.append(_envelope(ticker, period, idx, broken=broken_ticker == ticker))
    return rows


def _contexts():
    values = {
        "AKBNK": (7.0, 0.62),
        "GARAN": (8.0, 0.72),
        "YKBNK": (9.0, 0.82),
    }
    return {
        ticker: KapBankEvaluationContext(
            valuation_inputs=BankValuationInputs(
                coe=0.15,
                macro_cap=0.08,
                tier_cap=0.80,
                payout_missing_factor=0.70,
                band_width_shadow_mode=True,
                max_halfwidth=0.80,
            ),
            current_price=price,
            price_trade_date=ANALYSIS.date(),
            other_module_scores={
                "M1": quality,
                "M3": 0.60,
                "Ek4": 0.55,
                "Ek1": 0.80,
                "Ek9": 0.65,
            },
            good_count_ge8=8,
        )
        for ticker, (price, quality) in values.items()
    }


def _evaluate(rows, contexts=None, **kwargs):
    return evaluate_kap_bank_batch_end_to_end(
        rows,
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        fact_config=FACT_CONFIG,
        semantic_config=SEMANTIC,
        derivation_config=DERIVATION,
        contexts=_contexts() if contexts is None else contexts,
        **kwargs,
    )


def test_all_banks_run_in_one_call_with_leave_one_out_and_stable_ranking():
    rows = _rows()
    first = _evaluate(rows)
    second = _evaluate(list(reversed(rows)), contexts=dict(reversed(list(_contexts().items()))))
    assert first == second
    assert first["status"] == "COMPLETE"
    assert first["requested_count"] == 3
    assert first["result_count"] == 3
    assert first["rejected_count"] == 0
    assert first["sector_scale_eligible_count"] == 3
    assert [row["ticker"] for row in first["results"]] == sorted(TICKERS)
    assert all(row["valuation"]["sector_sample_size"] == 2 for row in first["results"])
    assert [row["rank"] for row in first["ranking"]] == [1, 2, 3]
    scores = [row["total_rasyo_100"] for row in first["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_one_bad_bank_is_isolated_without_poisoning_other_results():
    result = _evaluate(_rows(broken_ticker="YKBNK"))
    assert result["status"] == "PARTIAL"
    assert result["prepared_count"] == 2
    assert result["result_count"] == 2
    assert result["rejected_count"] == 1
    assert result["rejections"][0]["ticker"] == "YKBNK"
    assert {row["ticker"] for row in result["results"]} == {"AKBNK", "GARAN"}
    assert all(row["valuation"]["sector_sample_size"] == 1 for row in result["results"])


def test_strict_mode_stops_on_the_first_invalid_bank():
    with pytest.raises((KapBankEndToEndError, ValueError)):
        _evaluate(_rows(broken_ticker="YKBNK"), continue_on_error=False)


def test_missing_disclosures_are_reported_as_a_ticker_rejection():
    rows = [row for row in _rows() if row.ticker != "YKBNK"]
    result = _evaluate(rows)
    assert result["status"] == "PARTIAL"
    assert result["rejections"] == [
        {"ticker": "YKBNK", "reason": "en az bir KAP bildirimi gerekli"}
    ]


def test_context_outside_the_disclosure_universe_and_duplicate_global_id_fail_closed():
    rows = _rows()
    extra = _envelope("GARAN", ANCHOR, 999)
    extra = KapDisclosureEnvelope(**{**extra.__dict__, "ticker": "HALKB"})
    with pytest.raises(KapBankEndToEndError, match="context disi ticker"):
        _evaluate([*rows, extra])

    first = rows[0]
    other = rows[-1]
    conflict = KapDisclosureEnvelope(**{**other.__dict__, "disclosure_id": first.disclosure_id})
    with pytest.raises(KapBankEndToEndError, match="farkli kimlik"):
        _evaluate([*rows, conflict])


def test_structured_disclosure_id_is_rejected_before_hash_table_use():
    rows = _rows()
    bad = KapDisclosureEnvelope(**{**rows[0].__dict__, "disclosure_id": ["bad"]})
    with pytest.raises(KapBankEndToEndError, match="disclosure_id"):
        _evaluate([bad, *rows[1:]])


def test_batch_contract_rejects_non_bool_mode_and_non_context_values():
    with pytest.raises(KapBankEndToEndError, match="Python bool"):
        _evaluate(_rows(), continue_on_error=1)
    bad = _contexts()
    bad["GARAN"] = {}  # type: ignore[assignment]
    with pytest.raises(KapBankEndToEndError, match="context gecersiz"):
        _evaluate(_rows(), contexts=bad)
