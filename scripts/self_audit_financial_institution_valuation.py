"""
Banka disi finansal kurulus degerleme motoru — 15.000 senaryolu oz denetim.

Sigorta denetiminden farki: SAF motorun yaninda ORKESTRATOR (batch) ve
KALICILIK sozlesmesi katmanlari da taranir.
"""
from __future__ import annotations

try:
    from scripts._repo_bootstrap import ensure_repo_root
except ModuleNotFoundError:
    from _repo_bootstrap import ensure_repo_root

ensure_repo_root()

import json  # noqa: E402
import random  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from src.analytics.financial_institution_valuation import (  # noqa: E402
    FinancialInstitutionValuationConfig,
    FinancialInstitutionValuationError,
    build_financial_institution_snapshot,
    combine_financial_institution_m2,
    evaluate_financial_institution_batch,
    value_financial_institution_snapshot,
)
from src.ingest.financial_institution_metrics import (  # noqa: E402
    FinancialInstitutionMetricsIngestError,
    FinancialInstitutionMetricsRecord,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "f" * 64
RNG = random.Random(26072026)
TIPLER = ("FACTORING", "LEASING", "CONSUMER_FINANCE")


def config(**patch):
    data = {
        "valuation_profile": "FINANCIAL_INSTITUTION_PB_PE",
        "valuation_version": 1,
        "source_metrics_profile": "KAP_FINANCIAL_INSTITUTION_TTM",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS_LOCAL_STATUTORY",
        "accounting_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 6,
    }
    data.update(patch)
    return FinancialInstitutionValuationConfig.from_dict(data)


def snap(ticker, *, pb=None, pe=None, business="FACTORING", statement_age=36,
         price_age=1, accounting="TFRS_LOCAL_STATUTORY", eksik_opsiyonel=False,
         leverage=None):
    equity = RNG.uniform(5e8, 5e10)
    shares = RNG.uniform(1e7, 2e9)
    pb = RNG.uniform(.4, 3.0) if pb is None else pb
    income = equity / (RNG.uniform(6.0, 25.0) if pe is None else pe)
    if pe is not None and pe < 0:
        income = -abs(equity / abs(pe))
    kaldirac = RNG.uniform(3.0, 8.0) if leverage is None else leverage
    assets = equity * kaldirac
    receivables = assets * RNG.uniform(.65, .92)
    npl = receivables * RNG.uniform(.01, .09)
    price = (equity / shares) * pb
    kwargs = dict(
        ticker=ticker, analysis_at=ANALYSIS, business_type=business, currency="TRY",
        share_basis="ADJUSTED_PRICE_SERIES_V1", current_price=price,
        price_trade_date=ANALYSIS.date() - timedelta(days=price_age),
        period_end=date(2026, 6, 30) if statement_age < 100 else date(2025, 9, 30),
        published_at=ANALYSIS - timedelta(days=max(1, min(statement_age, 20))),
        total_equity=equity, average_equity=equity * RNG.uniform(.88, 1.0),
        net_income_ttm=income, total_assets=assets, finance_receivables=receivables,
        shares_out=shares, source_confidence=RNG.uniform(.65, 1.0),
        source_document_id=f"DOC-{ticker}", source_sha256=SHA,
        metrics_profile="KAP_FINANCIAL_INSTITUTION_TTM", metrics_version=1,
        accounting_profile=accounting, accounting_version=1,
    )
    if not eksik_opsiyonel:
        kwargs.update(
            npl_gross=npl, provisions=npl * RNG.uniform(.5, 1.1),
            net_finance_income_ttm=receivables * RNG.uniform(.05, .16),
            funding_cost_ttm=(assets - equity) * RNG.uniform(.04, .10),
            operating_expenses_ttm=equity * RNG.uniform(.05, .25),
            capital_adequacy_ratio=RNG.uniform(.10, .30),
        )
    return build_financial_institution_snapshot(**kwargs)


def peers(prefix="P", *, business="FACTORING"):
    return [snap(f"{prefix}{i}", pb=pb, pe=pe, business=business) for i, (pb, pe) in enumerate([
        (.6, 8), (.9, 10), (1.2, 12), (1.5, 15), (1.9, 20),
    ], 1)]


def run():
    stats = {
        "valid_valuation": 0,
        "controlled_insufficient": 0,
        "stale_or_profile_rejection": 0,
        "order_invariance": 0,
        "orchestration_isolation": 0,
        "persistence_contract": 0,
        "bypass_rejection": 0,
        "controlled_exception": 0,
        "uncontrolled_exception": 0,
        "silent_bad_acceptance": 0,
    }

    # 1) Gecerli degerleme (4000)
    for i in range(4000):
        try:
            business = TIPLER[i % 3]
            target = snap(f"V{i}", business=business, eksik_opsiyonel=(i % 7 == 0))
            out = value_financial_institution_snapshot(
                target, peers(f"V{i}_", business=business), config())
            if out["status"] != "OK" or not (0 < out["V_low"] <= out["V_mid"] <= out["V_high"]):
                stats["silent_bad_acceptance"] += 1
            elif not (0 <= out["valuation_score"] <= 1 and 0 <= out["v_conf"] <= 1):
                stats["silent_bad_acceptance"] += 1
            elif out["method_count"] < 1 or out["method_count"] > 2:
                stats["silent_bad_acceptance"] += 1
            else:
                stats["valid_valuation"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 2) Kontrollu yetersizlik (2500)
    for i in range(2500):
        try:
            if i % 3 == 0:
                target = snap(f"I{i}", pe=-10)
                cfg, peer_items = config(minimum_method_count=2), peers(f"I{i}_")
            elif i % 3 == 1:
                target = snap(f"I{i}")
                cfg, peer_items = config(minimum_peer_count=3), peers(f"I{i}_")[:2]
            else:
                target = snap(f"I{i}", leverage=40.0)      # ozkaynak tamponu yetersiz
                cfg, peer_items = config(), peers(f"I{i}_")
            out = value_financial_institution_snapshot(target, peer_items, cfg)
            if out["status"] == "YETERSIZ_VERI" and out["V_mid"] is None:
                stats["controlled_insufficient"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 3) Bayat / profil reddi (2500)
    for i in range(2500):
        try:
            if i % 3 == 0:
                target = snap(f"S{i}", price_age=30)
            elif i % 3 == 1:
                target = snap(f"S{i}", statement_age=300)
            else:
                target = snap(f"S{i}", accounting="OLD_PROFILE")
            out = value_financial_institution_snapshot(target, peers(f"S{i}_"), config())
            if out["status"] == "YETERSIZ_VERI" and out["reason"] in {
                "HEDEF_FIYAT_BAYAT", "HEDEF_FINANSAL_BILGI_BAYAT",
                "HEDEF_MUHASEBE_PROFILI_UYUSMUYOR",
            }:
                stats["stale_or_profile_rejection"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 4) Emsal sirasi degismezligi (2000)
    for i in range(2000):
        try:
            business = TIPLER[i % 3]
            target = snap(f"O{i}", business=business)
            peer_items = peers(f"O{i}_", business=business)
            duz = value_financial_institution_snapshot(target, peer_items, config())
            ters = value_financial_institution_snapshot(target, list(reversed(peer_items)), config())
            if (duz["V_mid"] == ters["V_mid"] and duz["v_conf"] == ters["v_conf"]
                    and duz["diagnostics"]["peer_tickers"] == ters["diagnostics"]["peer_tickers"]):
                stats["order_invariance"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 5) ORKESTRATOR: bozuk tek sirket digerlerini dusurmemeli (2000)
    for i in range(2000):
        try:
            business = TIPLER[i % 3]
            saglam = [snap(f"B{i}_{k}", business=business, pb=pb) for k, pb in enumerate([.8, 1.1, 1.4], 1)]
            bozuk = snap(f"B{i}_X", business=business, price_age=30)
            kayitlar = [*saglam, bozuk]
            rapor = evaluate_financial_institution_batch(
                kayitlar, config=config(), follow_contexts={})
            durum = {r["ticker"]: r["valuation"]["status"] for r in rapor["results"]}
            m2ler = {r["ticker"]: r["m2"]["m2"] for r in rapor["results"]}
            if (rapor["result_count"] == 4
                    and durum[f"B{i}_X"] == "YETERSIZ_VERI"
                    and sum(1 for t in durum if durum[t] == "OK") >= 2
                    and all(0 <= v <= 1 for v in m2ler.values())):
                stats["orchestration_isolation"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 6) KALICILIK SOZLESMESI: M2 iki eksen ve alan butunlugu (1500)
    zorunlu_alanlar = {
        "ticker", "analysis_at", "period_end", "business_type", "currency",
        "share_basis", "current_price", "total_equity", "average_equity",
        "total_assets", "finance_receivables", "shares_out", "roe_ttm",
        "equity_buffer", "valuation_score", "v_conf", "config_sha256",
        "valuation_profile", "valuation_version", "diagnostics",
    }
    for i in range(1500):
        try:
            business = TIPLER[i % 3]
            target = snap(f"P{i}", business=business)
            degerleme = value_financial_institution_snapshot(
                target, peers(f"P{i}_", business=business), config())
            takip_aktif = bool(i % 2)
            m2 = combine_financial_institution_m2(
                degerleme, follow_score=RNG.uniform(0, 1),
                follow_active=takip_aktif, config=config())
            eksik = zorunlu_alanlar - set(degerleme)
            skor_girdileri = m2["score_inputs"]
            json.dumps({"v": degerleme["diagnostics"], "m": skor_girdileri},
                       sort_keys=True, allow_nan=False)
            if (not eksik
                    and 0 <= m2["m2"] <= 1
                    and m2["m2_source"] == "FINANCIAL_INSTITUTION_PB_PE_TWO_AXIS_V1"
                    and isinstance(m2["valuation_usable"], bool)
                    and skor_girdileri["follow_active"] is takip_aktif):
                stats["persistence_contract"] += 1
            else:
                stats["silent_bad_acceptance"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    # 7) Dogrulama bypass denemesi (500)
    for i in range(500):
        try:
            if i % 2:
                # Dogrudan dataclass kurulumu dogrulamayi ATLAYAMAZ
                gecerli = config()
                bozuk = FinancialInstitutionValuationConfig(
                    **{**gecerli.__dict__, "minimum_peer_count": 0})
                value_financial_institution_snapshot(snap(f"X{i}"), peers(f"X{i}_"), bozuk)
            else:
                FinancialInstitutionMetricsRecord.from_mapping({
                    "ticker": f"X{i}", "period_end": "2026-06-30",
                    "published_at": "2026-07-20T10:00:00+03:00",
                    "business_type": "FACTORING",
                    "accounting_profile": "TFRS_LOCAL_STATUTORY", "accounting_version": 1,
                    "currency": "TRY", "shares_out": 100,
                    "share_basis": "ADJUSTED_PRICE_SERIES_V1",
                    "total_equity": 1000, "average_equity": 950, "net_income_ttm": 100,
                    "total_assets": 500,            # ozkaynak > aktif: gecersiz
                    "finance_receivables": 400,
                    "source_confidence": .9, "source_type": "KAP",
                    "source_document_id": "X", "source_sha256": SHA,
                    "metrics_profile": "KAP_FINANCIAL_INSTITUTION_TTM", "metrics_version": 1,
                })
            stats["silent_bad_acceptance"] += 1
        except (FinancialInstitutionValuationError, FinancialInstitutionMetricsIngestError):
            stats["bypass_rejection"] += 1
            stats["controlled_exception"] += 1
        except Exception:
            stats["uncontrolled_exception"] += 1

    scenario_count = sum(stats[key] for key in [
        "valid_valuation", "controlled_insufficient", "stale_or_profile_rejection",
        "order_invariance", "orchestration_isolation", "persistence_contract",
        "bypass_rejection",
    ])
    result = {
        "status": "PASS" if (scenario_count == 15000
                             and stats["uncontrolled_exception"] == 0
                             and stats["silent_bad_acceptance"] == 0) else "FAIL",
        "scenario_count": scenario_count,
        **stats,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
