from __future__ import annotations

"""W7-B -- can a genuinely period-contemporary archival source clear the
evidence-dating gate W7-A found, and how far does that actually get real M2?

W7-A proved the production gate (price_level_action_evidence.verify) rejects
any completeness source captured today, however accurate: its own publication
timestamp is always after every historical cutoff. That is unconditional --
no query of a present-day system can ever satisfy it for a past cutoff.

The one kind of source that is NOT a present-day query is a periodic,
official bulletin that was itself published, on paper (well, PDF), AT THE
TIME -- each issue is its own dated fact, not a retrospective aggregate. SPK
(Sermaye Piyasasi Kurulu, Turkey's capital-markets regulator) publishes
exactly such a bulletin weekly, and it has archived every issue back to 2005.
Each issue's own "Halka Acik Ortakliklarin Pay Ihraclari" table is the
authoritative record of every capital increase/decrease SPK approved that
week -- a company's absence from every issue between two dates is itself a
dated, verifiable, period-contemporary fact.

This audit captures 98 such issues (2022-06-09 through 2023-08-31, gap-free
in each year's own numbering), confirms hash-bound completeness, builds real
PRICE_LEVEL_ACTION_COVERAGE_V1 evidence for six NONFIN tickers whose only
prior blocker was this gate, and calls the unmodified production functions:
PriceLevelActionEvidence.verify(), materialize_price_level_market_cap(), and
run_historical_pit_nonfin_m2_replay(). Negative controls (an early cutoff, a
tampered future-dated source) confirm the gate still rejects what it should.

The honest result, recorded here rather than glossed over: the evidence-dating
gate is cleared for all six tickers -- six real, hash-verified market caps
materialize through the unmodified production path for the first time this
project has ever produced one for a non-zero interval. A second, independent
gate then rejects all six for a different reason (YETERSIZ_MULTIPLE_KAPSAMI):
with only six resolved tickers, each relative-valuation multiple has at most
five peers, one peer short of two of NonfinValuationConfig's four multiples
reaching minimum_peer_count=5 with valid (not just present) values. That is a
scale problem, solvable by resolving more tickers with the same method -- not
a new instance of the dating problem this audit exists to test.

It produces no M2 score and changes no production code.
"""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import gzip
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.historical_pit_nonfin_m2_replay import (
    HistoricalPitNonfinM2ReplayError,
    run_historical_pit_nonfin_m2_replay,
)
from src.analytics.nonfin_valuation import NonfinValuationConfig
from src.analytics.price_level_action_evidence import ActionEvidenceError, PriceLevelActionEvidence
from src.analytics.price_level_adapter import PriceLevelActionBundle
from src.analytics.price_level_valuation_basis import PRICE_LEVEL_BASIS, build_price_level_observation

AUDIT = ROOT / "data/audit/w7b_spk_bulletin_evidence_v1"
CONTRACT = "W7B_SPK_BULLETIN_EVIDENCE_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

ARCHIVE_DIR = ROOT / "data/backtest_sources/spk_bulletin_archive_v1"
RESOLVED_DIR = ROOT / "data/backtest_sources/spk_bulletin_resolved_shares_v1"
DIAGNOSTICS = ROOT / "data/audit/experimental_materialization_v3/core_diagnostics.jsonl.gz"
P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
EXACT_VALUATION_CONFIG = ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json"

CUTOFF = datetime(2023, 8, 31, 18, 10, tzinfo=ZoneInfo("Europe/Istanbul"))
PRICE_TRADE_DATE = date(2023, 8, 31)
SIGNAL_DATE = "2023-09-01"

TICKERS = ("ALFAS", "ENKAI", "GESAN", "KONTR", "SMRTG", "ZOREN")

NAME_SEARCH_KEY = {
    "ALFAS": "ALFA SOLAR ENERJİ",
    "ENKAI": "ENKA İNŞAAT",
    "GESAN": "GİRİŞİM ELEKTRİK",
    "KONTR": "KONTROLMATİK",
    "SMRTG": "SMART GÜNEŞ ENERJİSİ",
    "ZOREN": "ZORLU ENERJİ ELEKTRİK",
}

# Every mention this audit's own re-scan of the archive finds for these six
# tickers within their own window, reviewed by hand: each is bulletin 2-2023's
# generic numbered "independent audit subject companies" list, not a capital
# increase/decrease table row (that table is a separate, distinctly-headed
# section; see docs/W7B_SPK_BULLETIN_EVIDENCE.md). Any mention this audit's
# rescan finds that is NOT in this set aborts derive() rather than silently
# asserting completeness -- see verify_capital_action_absence().
KNOWN_NON_ACTION_MENTIONS = {
    ("ALFAS", 2, 2023),
    ("ENKAI", 2, 2023),
    ("KONTR", 2, 2023),
}

CONTENT_FILES = ("archive.json", "evidence.json", "gate_results.json", "negative_controls.json",
                  "batch_replay.json", "verdict.json")


class W7BAuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    """For this audit's own generated JSON content only (LF line endings by
    construction). Never use on committed binary/PDF source bytes -- the CRLF
    normalization below is wrong for arbitrary binary content."""
    return sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_raw(payload: bytes) -> str:
    """Plain byte hash for binary/arbitrary content (a PDF, a source blob
    handed to PriceLevelActionEvidence). Matches exactly what verify() itself
    computes (sha256 of the exact bytes, no normalization) -- never route
    binary content through sha_bytes' CRLF stripping."""
    return sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    return sha_raw(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def load_bulletin_archive() -> dict:
    """Verify the committed SPK bulletin archive: hashes match, and each
    year's own bulletin numbering is gap-free from 1 through its max. SPK
    has, at least once (52-2022 and 53-2022), issued two distinct bulletins
    on the same calendar date -- by_date holds a list per date, not one
    entry, so that real case is preserved rather than silently dropped."""
    manifest = json.loads((ARCHIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("contract") != "SPK_BULLETIN_ARCHIVE_V1":
        raise W7BAuditError("BULLETIN_ARCHIVE_CONTRACT_MISMATCH")
    by_year: dict[int, set[int]] = {}
    by_date: dict[str, list[dict]] = {}
    seen_files: set[str] = set()
    for entry in manifest["entries"]:
        path = ARCHIVE_DIR / entry["filename"]
        if sha_file(path) != entry["sha256"]:
            raise W7BAuditError(f"BULLETIN_HASH_MISMATCH:{entry['filename']}")
        key = (entry["bulletin_year"], entry["bulletin_num"])
        if key in seen_files:
            raise W7BAuditError(f"DUPLICATE_BULLETIN_NUMBER:{key}")
        seen_files.add(key)
        by_year.setdefault(entry["bulletin_year"], set()).add(entry["bulletin_num"])
        by_date.setdefault(entry["date"], []).append(entry)
    # Numbering completeness is checked only across the numbers this archive
    # actually spans per year (a partial-year capture is legitimate; a hole
    # inside the captured span is not).
    for year, nums in by_year.items():
        span = range(min(nums), max(nums) + 1)
        missing = sorted(set(span) - nums)
        if missing:
            raise W7BAuditError(f"BULLETIN_NUMBERING_GAP:{year}:{missing}")
    return {"manifest": manifest, "by_date": by_date}


def extract_bulletin_text(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "".join((page.extract_text() or "") for page in reader.pages).upper()


def verify_capital_action_absence(ticker: str, anchor_date: str, archive: dict) -> list[dict]:
    """Scan every archived bulletin strictly after anchor_date through the
    cutoff price date for this ticker's company name. Any mention not in
    KNOWN_NON_ACTION_MENTIONS aborts the audit. Returns the exact source list
    (date-ordered, then bulletin-number-ordered within a shared date) used to
    build this ticker's completeness evidence."""
    needle = NAME_SEARCH_KEY[ticker]
    window_dates = sorted(
        d for d in archive["by_date"] if anchor_date < d <= PRICE_TRADE_DATE.isoformat()
    )
    if not window_dates:
        raise W7BAuditError(f"EMPTY_BULLETIN_WINDOW:{ticker}")
    sources = []
    for d in window_dates:
        for entry in sorted(archive["by_date"][d], key=lambda e: e["bulletin_num"]):
            text = extract_bulletin_text(ARCHIVE_DIR / entry["filename"])
            if needle in text:
                key = (ticker, entry["bulletin_num"], entry["bulletin_year"])
                if key not in KNOWN_NON_ACTION_MENTIONS:
                    raise W7BAuditError(
                        f"UNEXPLAINED_MENTION:{ticker} appears in bulletin "
                        f"{entry['bulletin_num']}-{entry['bulletin_year']} ({d}) outside the "
                        f"reviewed false-positive set -- refusing to assert completeness"
                    )
            sources.append({
                "date": d, "bulletin_num": entry["bulletin_num"], "bulletin_year": entry["bulletin_year"],
                "filename": entry["filename"], "sha256": entry["sha256"],
            })
    return sources


def build_evidence_bundle(ticker: str, anchors: dict, sources: list[dict]) -> tuple[PriceLevelActionBundle, dict]:
    info = anchors[ticker]
    share_path = RESOLVED_DIR / ticker / "share_certification_source.json"
    share_bytes = share_path.read_bytes()
    share_ref = f"KAP_{ticker}_SHARE_HISTORY"
    creation_dt = datetime.strptime(info["creation"], "%d/%m/%Y %H:%M:%S")
    share_published = creation_dt.strftime("%Y-%m-%dT%H:%M:%S+03:00")

    manifest_sources = [{
        "source_ref": share_ref, "source_sha256": sha_bytes(share_bytes), "published_at": share_published,
    }]
    source_bytes = {share_ref: share_bytes}
    for source in sources:
        raw = (ARCHIVE_DIR / source["filename"]).read_bytes()
        ref = f"SPK_BULLETIN_{source['bulletin_year']}_{source['bulletin_num']:02d}"
        source_bytes[ref] = raw
        manifest_sources.append({
            "source_ref": ref, "source_sha256": sha_raw(raw),
            "published_at": f"{source['date']}T09:00:00+03:00",
        })
    completeness_ref = manifest_sources[-1]["source_ref"]

    manifest = {
        "contract": "PRICE_LEVEL_ACTION_COVERAGE_V1", "ticker": ticker,
        "source_share_basis": "DATED_UNADJUSTED_SHARES_V1", "source_shares_out": info["shares_out"],
        "shares_basis_date": info["anchor_date"], "complete_through": PRICE_TRADE_DATE.isoformat(),
        "enumeration_complete": True, "sources": manifest_sources,
        "completeness_source_ref": completeness_ref, "share_source_ref": share_ref, "events": [],
        "completeness_scope": (
            f"SPK_WEEKLY_BULLETIN_CONTINUOUS_COVERAGE_V1:"
            f"({info['anchor_date']},{PRICE_TRADE_DATE.isoformat()}]"
        ),
    }
    manifest_bytes = encode_json(manifest)
    evidence = PriceLevelActionEvidence(manifest_bytes, sha_bytes(manifest_bytes), source_bytes)
    receipt = {
        "ticker": ticker, "anchor_date": info["anchor_date"], "shares_out": info["shares_out"],
        "source_count": len(manifest_sources), "manifest_sha256": sha_bytes(manifest_bytes),
    }
    return PriceLevelActionBundle(evidence, ()), receipt


def run_gate(ticker: str, bundle: PriceLevelActionBundle, price_close: float) -> dict:
    from src.analytics.price_level_valuation_basis import materialize_price_level_market_cap
    info_anchor = json.loads((RESOLVED_DIR / "anchors.json").read_text(encoding="utf-8"))[ticker]
    price_obs = build_price_level_observation(
        ticker=ticker, trade_date=PRICE_TRADE_DATE, close=price_close, adjusted_close=price_close,
    )
    try:
        result = materialize_price_level_market_cap(
            price=price_obs, shares_out=info_anchor["shares_out"],
            shares_basis_date=date.fromisoformat(info_anchor["anchor_date"]),
            corporate_actions=bundle.events, events_complete_through=PRICE_TRADE_DATE,
            evidence=bundle.evidence, cutoff=CUTOFF,
        )
    except Exception as exc:
        return {"ticker": ticker, "gate_passed": False, "error": str(exc)}
    return {
        "ticker": ticker, "gate_passed": True, "error": None,
        "market_cap": result.market_cap, "normalized_shares_out": result.normalized_shares_out,
        "raw_close": result.raw_close, "action_evidence_sha256": result.action_evidence_sha256,
    }


def run_negative_controls(ticker: str, bundle: PriceLevelActionBundle, anchors: dict) -> dict:
    """Prove the gate still rejects bad evidence: an early cutoff (some listed
    sources not yet published), and a tampered future-dated source."""
    info = anchors[ticker]
    basis_date = date.fromisoformat(info["anchor_date"])

    early_cutoff = datetime(2022, 12, 1, 18, 10, tzinfo=ZoneInfo("Europe/Istanbul"))
    case_a = {"case": "cutoff_before_some_sources"}
    try:
        bundle.evidence.verify(
            ticker=ticker, shares_basis_date=basis_date, price_trade_date=PRICE_TRADE_DATE,
            cutoff=early_cutoff, events=(), shares_out=info["shares_out"],
        )
        case_a.update(rejected=False, error=None)
    except ActionEvidenceError as exc:
        case_a.update(rejected=True, error=str(exc))

    manifest = json.loads(bundle.evidence.manifest_bytes)
    tampered_sources = [dict(s) for s in manifest["sources"]]
    tampered_sources[-1]["published_at"] = "2026-09-08T00:00:00+03:00"
    manifest["sources"] = tampered_sources
    tampered_bytes = encode_json(manifest)
    tampered_evidence = PriceLevelActionEvidence(
        tampered_bytes, sha_bytes(tampered_bytes), bundle.evidence.source_bytes
    )
    case_c = {"case": "tampered_future_dated_source"}
    try:
        tampered_evidence.verify(
            ticker=ticker, shares_basis_date=basis_date, price_trade_date=PRICE_TRADE_DATE,
            cutoff=CUTOFF, events=(), shares_out=info["shares_out"],
        )
        case_c.update(rejected=False, error=None)
    except ActionEvidenceError as exc:
        case_c.update(rejected=True, error=str(exc))

    if not case_a["rejected"] or case_a["error"] != "future source publication":
        raise W7BAuditError(f"NEGATIVE_CONTROL_A_FAILED:{ticker}:{case_a}")
    if not case_c["rejected"] or case_c["error"] != "future source publication":
        raise W7BAuditError(f"NEGATIVE_CONTROL_C_FAILED:{ticker}:{case_c}")
    return {"ticker": ticker, "case_a": case_a, "case_c": case_c}


def load_month_diagnostics() -> dict:
    with gzip.open(DIAGNOSTICS, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("signal_date") == SIGNAL_DATE:
                return row.get("per_ticker", {})
    raise W7BAuditError("SIGNAL_DATE_MONTH_NOT_FOUND")


def load_prices_and_sectors() -> tuple[dict, dict]:
    prices, sectors = {}, {}
    with gzip.open(P3_CELLS, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            ticker = row.get("ticker")
            if ticker in TICKERS and (row.get("price") or {}).get("trade_date") == PRICE_TRADE_DATE.isoformat():
                prices[ticker] = row["price"]["raw_close"]
                sectors[ticker] = row["historical_family_lineage"]["sector_index_code"]
    missing = sorted(set(TICKERS) - set(prices))
    if missing:
        raise W7BAuditError(f"PRICE_MISSING_FOR_TICKERS:{missing}")
    return prices, sectors


def run_batch_replay(bundles: dict[str, PriceLevelActionBundle], anchors: dict) -> dict:
    per_ticker = load_month_diagnostics()
    prices, sectors = load_prices_and_sectors()
    missing = sorted(set(TICKERS) - set(per_ticker))
    if missing:
        raise W7BAuditError(f"DIAGNOSTICS_MISSING_FOR_TICKERS:{missing}")

    universe_rows, financial_rows, price_rows = [], [], []
    for ticker in TICKERS:
        info = anchors[ticker]
        universe_rows.append({"ticker": ticker, "peer_group": sectors[ticker], "sector_family": "NONFIN"})
        quarters = per_ticker[ticker]["quarters"]
        for i, raw in enumerate(quarters):
            values = dict(raw["values"])
            values["shares_out"] = None
            values["shares_basis_date"] = raw["period_end"]
            if i == len(quarters) - 1:
                values["shares_out"] = info["shares_out"]
                values["shares_basis_date"] = info["anchor_date"]
            financial_rows.append({
                "ticker": ticker, "period_end": raw["period_end"], "published_at": raw["published_at"],
                "derivation_profile": raw["derivation_profile"], "derivation_version": raw["derivation_version"],
                **values,
            })
        price_rows.append({
            "ticker": ticker, "price_trade_date": PRICE_TRADE_DATE.isoformat(),
            "current_price": prices[ticker], "price_basis": PRICE_LEVEL_BASIS,
            "action_bundle": bundles[ticker],
        })

    config = NonfinValuationConfig.from_json_file(EXACT_VALUATION_CONFIG)
    try:
        result = run_historical_pit_nonfin_m2_replay(
            analysis_at=CUTOFF, universe=pd.DataFrame(universe_rows),
            financials=pd.DataFrame(financial_rows), prices=pd.DataFrame(price_rows), config=config,
        )
    except HistoricalPitNonfinM2ReplayError as exc:
        raise W7BAuditError(f"BATCH_REPLAY_RAISED_UNEXPECTEDLY:{exc}") from exc

    rejections = result.rejections.to_dict("records")
    reasons = {row["ticker"]: row["reason"] for row in rejections}
    diagnostics_by_ticker = {}
    for item in result.report.get("results", []):
        ticker = item.get("ticker")
        if ticker in TICKERS:
            v = item.get("valuation", {})
            diag = v.get("diagnostics", {})
            diagnostics_by_ticker[ticker] = {
                "status": v.get("status"), "reason": v.get("reason"),
                "multiple_details": diag.get("multiple_details"),
                "peer_tickers": diag.get("peer_tickers"),
                "market_cap": (diag.get("price_level_basis") or {}).get("market_cap"),
            }
    return {
        "m2_score_count": len(result.m2_scores), "rejection_count": len(rejections),
        "rejection_reasons": reasons, "diagnostics_by_ticker": diagnostics_by_ticker,
    }


def derive() -> dict[str, bytes]:
    archive = load_bulletin_archive()
    anchors = json.loads((RESOLVED_DIR / "anchors.json").read_text(encoding="utf-8"))
    if set(anchors) != set(TICKERS):
        raise W7BAuditError("RESOLVED_ANCHORS_TICKER_SET_MISMATCH")

    bundles: dict[str, PriceLevelActionBundle] = {}
    evidence_receipts = []
    for ticker in TICKERS:
        sources = verify_capital_action_absence(ticker, anchors[ticker]["anchor_date"], archive)
        bundle, receipt = build_evidence_bundle(ticker, anchors, sources)
        bundles[ticker] = bundle
        evidence_receipts.append(receipt)

    prices, _sectors = load_prices_and_sectors()
    gate_results = [run_gate(ticker, bundles[ticker], prices[ticker]) for ticker in TICKERS]
    if not all(row["gate_passed"] for row in gate_results):
        failed = [row["ticker"] for row in gate_results if not row["gate_passed"]]
        raise W7BAuditError(f"EVIDENCE_DATING_GATE_STILL_REJECTS:{failed}")

    negative_controls = [run_negative_controls(ticker, bundles[ticker], anchors) for ticker in TICKERS]

    batch_replay = run_batch_replay(bundles, anchors)
    if batch_replay["m2_score_count"] != 0:
        raise W7BAuditError(
            f"UNEXPECTED_M2_SCORE_PRODUCED:{batch_replay['m2_score_count']} -- "
            "this audit's fixed six-ticker sample was measured to fall short of "
            "minimum_peer_count=5 on two of four multiples; a nonzero score here "
            "means the fixture data or config changed and this audit's own "
            "narrative needs re-deriving, not silent acceptance"
        )
    expected_reason = "VALUATION_NOT_USABLE:YETERSIZ_MULTIPLE_KAPSAMI"
    if any(reason != expected_reason for reason in batch_replay["rejection_reasons"].values()):
        raise W7BAuditError(f"UNEXPECTED_REJECTION_REASON:{batch_replay['rejection_reasons']}")

    archive_out = {
        "contract": CONTRACT, "bulletin_count": len(archive["manifest"]["entries"]),
        "span_start": min(archive["by_date"]), "span_end": max(archive["by_date"]),
        "archive_manifest_sha256": sha_file(ARCHIVE_DIR / "manifest.json"),
    }
    evidence_out = {
        "contract": CONTRACT, "cutoff": CUTOFF.isoformat(), "price_trade_date": PRICE_TRADE_DATE.isoformat(),
        "tickers": evidence_receipts,
    }
    gate_out = {"contract": CONTRACT, "results": gate_results}
    negative_out = {"contract": CONTRACT, "results": negative_controls}
    batch_out = {"contract": CONTRACT, **batch_replay}

    verdict = {
        "contract": CONTRACT,
        "status": "PARTIAL_PROGRESS",
        "finding": (
            "A genuinely period-contemporary archival source -- SPK's own weekly "
            "regulatory bulletin, each issue independently dated at its original "
            "publication -- clears the evidence-dating gate W7-A found unconditional "
            "against present-day KAP queries. All six sample tickers' real market caps "
            "materialize through the unmodified production path "
            "(materialize_price_level_market_cap), the first time this project has ever "
            "produced a non-zero-interval, production-admissible historical share basis. "
            "Negative controls (an early cutoff; a tampered future-dated source) confirm "
            "the gate still rejects what it should -- this is a genuine pass, not a "
            "weakened check."
        ),
        "consequence": (
            "W7-A's specific finding (a present-day query is unconditionally rejected) "
            "is unchanged and still correct -- this audit did not touch that code path or "
            "weaken it; it supplied a source of a qualitatively different kind. A second, "
            "independent gate (NonfinValuationConfig.minimum_peer_count=5 per multiple) "
            "then rejects all six with YETERSIZ_MULTIPLE_KAPSAMI: with only six resolved "
            "tickers, every multiple has at most five peers, and two of the four "
            "multiples (EV_EBIT, PS) have far fewer than five peers with a valid, usable "
            "value. That is a scale problem -- resolve more tickers with the identical, "
            "now-proven method -- not a recurrence of the dating problem this audit tests."
        ),
        "reopen_condition": (
            "Resolve additional same-sector NONFIN tickers' share basis via the same "
            "SPK-bulletin method (short raw-KAP-anchor gap preferred, to bound the "
            "bulletin count) until each of PE, EV_EBIT, PS and PB independently reaches "
            "minimum_peer_count=5 *usable* peer values -- not just five peers present. "
            "Re-run the batch replay; a nonzero m2_score_count is the reopen signal, and "
            "this audit's own derive() already refuses to pass silently if that happens "
            "without the narrative being updated."
        ),
        "policy": {
            "model_changed": False, "weights_changed": False, "veto_changed": False,
            "peer_or_coverage_threshold_changed": False, "universe_changed": False,
            "neutral_fill": False, "production_code_changed": False,
            "m2_materialized": False, "market_cap_materialized_non_zero_interval": True,
        },
    }
    return {
        "archive.json": encode_json(archive_out), "evidence.json": encode_json(evidence_out),
        "gate_results.json": encode_json(gate_out), "negative_controls.json": encode_json(negative_out),
        "batch_replay.json": encode_json(batch_out), "verdict.json": encode_json(verdict),
    }


def git_head() -> str:
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_receipt(content: dict[str, bytes]) -> dict:
    return {
        "contract": "W7B_SPK_BULLETIN_EVIDENCE_RECEIPT_V1",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w7b_spk_bulletin_evidence.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "policy": json.loads(content["verdict.json"])["policy"],
    }


def write(content: dict[str, bytes], receipt: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    for name, payload in content.items():
        (AUDIT / name).write_bytes(payload)
    (AUDIT / "receipt.json").write_bytes(encode_json(receipt))


def check() -> None:
    receipt = json.loads((AUDIT / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W7BAuditError("HASH_MODE_MISMATCH")
    content = derive()
    for name in CONTENT_FILES:
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W7BAuditError(f"REDERIVED_HASH_MISMATCH:{name}")
    print("W7B_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        return
    content = derive()
    write(content, build_receipt(content))
    print("W7B_APPLY_OK")


if __name__ == "__main__":
    main()
