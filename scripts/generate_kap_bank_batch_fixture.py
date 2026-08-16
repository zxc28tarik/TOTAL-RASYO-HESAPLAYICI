from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test_fixtures" / "kap_bank_batch_e2e"
ANCHOR = date(2026, 3, 31)
ANALYSIS = datetime(2026, 5, 15, 20, 0, tzinfo=timezone(timedelta(hours=3)))
TICKERS = ("AKBNK", "GARAN", "YKBNK")


def quarter_ends(anchor: date, count: int) -> list[date]:
    result = []
    year, month = anchor.year, anchor.month
    for offset in range(count - 1, -1, -1):
        index = year * 4 + ((month - 1) // 3) - offset
        y, q = divmod(index, 4)
        m = (q + 1) * 3
        if m == 3:
            day = 31
        elif m == 6:
            day = 30
        elif m == 9:
            day = 30
        else:
            day = 31
        result.append(date(y, m, day))
    return result


def digest(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def disclosure(ticker: str, period: date, idx: int, *, restated: bool = False):
    settings = {
        "AKBNK": {"offset": 1, "scope": "CONSOLIDATED", "profit": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "dividend": True},
        "GARAN": {"offset": 2, "scope": "SOLO", "profit": "ifrs-full_ProfitLoss", "dividend": False},
        "YKBNK": {"offset": 3, "scope": "CONSOLIDATED", "profit": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "dividend": True},
    }[ticker]
    quarter_no = (period.month - 1) // 3 + 1
    published = datetime.combine(period + timedelta(days=40), datetime.min.time(), tzinfo=timezone.utc)
    version_tag = "RESTATED" if restated else "ORIGINAL"
    version_sequence = 2 if restated else 1
    if restated:
        published += timedelta(days=90)
    equity = 90_000_000 + settings["offset"] * 6_000_000 + idx * 2_100_000
    profit = quarter_no * (4_000_000 + settings["offset"] * 600_000)
    if restated:
        equity += 4_500_000
        profit += 1_250_000
    facts = [
        {"code": "ifrs-full_Equity", "value": str(equity), "periodEnd": period.isoformat(), "periodStart": None, "currency": "TRY", "unitScale": 1000, "statementScope": settings["scope"]},
        {"code": "ifrs-full_IssuedCapital", "value": "10000000", "periodEnd": period.isoformat(), "periodStart": None, "currency": "TRY", "unitScale": 1000, "statementScope": settings["scope"]},
        {"code": settings["profit"], "value": str(profit), "periodStart": f"{period.year}-01-01", "periodEnd": period.isoformat(), "currency": "TRY", "unitScale": 1000, "statementScope": settings["scope"]},
    ]
    if settings["dividend"]:
        facts.append({"code": "ifrs-full_DividendsPaid", "value": str(-(quarter_no * 800_000)), "periodStart": f"{period.year}-01-01", "periodEnd": period.isoformat(), "currency": "TRY", "unitScale": 1000, "statementScope": settings["scope"]})
    payload = {"financialStatement": {"versionTag": version_tag, "versionSequence": version_sequence, "facts": facts}}
    disclosure_id = f"{ticker}-{period.isoformat()}-{version_tag}"
    return {
        "disclosure_id": disclosure_id,
        "published_at": published.isoformat(),
        "ticker": ticker,
        "company_id": f"C-{ticker}",
        "notification_type": "FINANCIAL_STATEMENT",
        "subject": "Financial Report",
        "source_url": f"https://kap.org.tr/tr/Bildirim/{disclosure_id}",
        "payload": payload,
        "payload_sha256": digest(payload),
        "fetched_at": max(ANALYSIS, published + timedelta(minutes=1)).isoformat(),
        "source": "FIXTURE_KAP",
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    periods = quarter_ends(ANCHOR, 12)
    rows = []
    for ticker in TICKERS:
        for idx, period in enumerate(periods):
            rows.append(disclosure(ticker, period, idx))
    # One historical restatement known by analysis time.
    rows.append(disclosure("YKBNK", date(2025, 6, 30), periods.index(date(2025, 6, 30)), restated=True))
    rows.sort(key=lambda row: (row["published_at"], row["ticker"], row["disclosure_id"]))
    (OUT / "disclosures.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    contexts = {}
    for ticker, price, m1 in (("AKBNK", 7.0, 0.62), ("GARAN", 8.0, 0.72), ("YKBNK", 9.0, 0.82)):
        contexts[ticker] = {
            "valuation_inputs": {
                "coe": 0.15,
                "macro_cap": 0.08,
                "tier_cap": 0.80,
                "payout_missing_factor": 0.70,
                "band_width_shadow_mode": True,
                "max_halfwidth": 0.80,
            },
            "current_price": price,
            "price_trade_date": ANALYSIS.date().isoformat(),
            "other_module_scores": {"M1": m1, "M3": 0.60, "Ek4": 0.55, "Ek1": 0.80, "Ek9": 0.65},
            "good_count_ge8": 8,
        }
    (OUT / "contexts.json").write_text(json.dumps(contexts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "analysis_at": ANALYSIS.isoformat(),
        "anchor_period_end": ANCHOR.isoformat(),
        "tickers": list(TICKERS),
        "disclosure_count": len(rows),
        "variants": {
            "AKBNK": "CONSOLIDATED + preferred parent-owner profit + dividends",
            "GARAN": "SOLO + fallback ProfitLoss + payout missing",
            "YKBNK": "CONSOLIDATED + one historical RESTATED period",
        },
    }
    (OUT / "fixture_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
