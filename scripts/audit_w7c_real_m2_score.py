from __future__ import annotations

"""W7-C -- close the bottleneck W7-B left standing and materialize a real,
non-zero NONFIN M2 score through the unmodified production pipeline.

W7-B proved SPK's own archived weekly bulletin clears the evidence-dating
gate (price_level_action_evidence.verify) for six tickers, then hit a second,
independent gate: NonfinValuationConfig.minimum_peer_count=5 rejected all six
with YETERSIZ_MULTIPLE_KAPSAMI, because field_completeness_census.json showed
no NONFIN sector's own CORE data carried even five same-quarter tickers with
revenue/ebit/net_income all populated at that one cutoff/signal_date.

This audit finds a cutoff/sector/multiple combination where that ceiling is
cleared with genuinely verifiable evidence, not by lowering any threshold:

  - signal_date 2023-08-01 (price_trade_date 2023-07-31), sector XUSIN,
    multiple PE (net_income_ttm: the last four quarters, ending
    period_end=2023-03-31, all individually non-null for the ticker's own
    reporting).
  - Seven tickers independently satisfy that TTM completeness at this exact
    cutoff: BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA.
  - Each ticker's own KAP share-count certification (the latest
    EXPLICIT_CLASS_NOMINALS_RECONCILED/usable observation in
    kap_share_class_history_v1 published before the cutoff) is used only to
    pick which disclosure (which creationDate) to certify from; the actual
    share count is always recomputed from that disclosure's own raw
    nominalValueOfShares/nominalValuePerShare strings, never taken from the
    artifact's precomputed derived_shares/classes fields. That recomputation
    is what catches CCOLA's 2019-05-14 entry: the artifact's own
    derived_shares figure for it carries the same decimal-separator mis-parse
    this project's earlier forensic work found elsewhere (two of its three
    class rows have kurus-level precision and get their decimal point
    effectively dropped, inflating the total ~686x); the raw strings
    recompute to 25,437,078,200 shares, exactly matching CCOLA's own
    2016-06-23 observation -- zero real capital change in between, a coherent
    story, not a guess. The other six tickers' raw recomputation matches their
    stored derived_shares exactly.
  - Every SPK bulletin strictly after each ticker's anchor date through the
    2023-07-31 price date (drawn from the 461-bulletin archive this work
    package extended from W7-B's original 98, spanning 2016-06-24 through
    2023-08-31) is scanned for that company's own legal name. Two of the
    seven (KONYA, VESTL) have multi-year anchor gaps and turn up mentions in
    every one of the archive's recurring annual bulletins -- each reviewed by
    hand and confirmed to be the same numbered "bagimsiz denetime tabi
    ortakliklar" registry list already established as a harmless false
    positive in W7-B (a generic company roster, not a capital-action table
    row). CCOLA's gap additionally turns up two mentions that are neither a
    capital-action table row nor that registry list: a wholly-owned
    subsidiary's merger approval ("D. DIGER BASVURU SONUCLARI", 63-2021 --
    does not touch CCOLA's own share count) and a bond/note issuance
    ("2. BORCLANMA ARACLARI", 56-2022 -- debt, not equity). Both were read
    directly from the bulletin text before being accepted here.
  - With all seven evidenced this way, PriceLevelActionEvidence.verify() and
    materialize_price_level_market_cap() -- the unmodified production
    functions -- pass for every ticker, and run_historical_pit_nonfin_m2_replay
    (also unmodified, same nonfin_valuation.kap_bulk_exact_v1.json config
    W7-B used) computes PE with peer_count=6 (every ticker sees the other six)
    and PB with peer_count=6, together clearing minimum_coverage_weight=0.5
    (0.3 + 0.2) with zero rejections: seven real M2 scores, the first this
    project has ever produced from the NONFIN relative-valuation path.

Scope, stated plainly: this is a closed seven-ticker sample, not a
full-universe run. It proves the valuation path works end-to-end given
genuine, sufficiently-evidenced peer coverage -- it does not claim every
NONFIN cell across all 60 historical cutoffs now clears minimum_peer_count;
that would need the same evidence-materialization effort repeated at scale.

It changes no production code, no model weight, no veto, and no threshold.
"""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import gzip
import json
from pathlib import Path
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

AUDIT = ROOT / "data/audit/w7c_real_m2_score_v1"
CONTRACT = "W7C_REAL_M2_SCORE_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

ARCHIVE_DIR = ROOT / "data/backtest_sources/spk_bulletin_archive_v1"
SHARE_HISTORY_DIR = ROOT / "data/backtest_sources/kap_share_class_history_v1"
DIAGNOSTICS = ROOT / "data/audit/experimental_materialization_v3/core_diagnostics.jsonl.gz"
P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
EXACT_VALUATION_CONFIG = ROOT / "config/nonfin_valuation.kap_bulk_exact_v1.json"

CUTOFF = datetime(2023, 7, 31, 18, 10, tzinfo=ZoneInfo("Europe/Istanbul"))
PRICE_TRADE_DATE = date(2023, 7, 31)
SIGNAL_DATE = "2023-08-01"

TICKERS = ("BRSAN", "CEMTS", "QUAGR", "TUKAS", "KONYA", "VESTL", "CCOLA")

NAME_SEARCH_KEY = {
    "BRSAN": "BORUSAN MANNESMANN",
    "CEMTS": "ÇEMTAŞ ÇELİK",
    "QUAGR": "QUA GRANITE",
    "TUKAS": "TUKAŞ GIDA",
    "KONYA": "KONYA ÇİMENTO",
    "VESTL": "VESTEL ELEKTRONİK",
    "CCOLA": "COCA-COLA İÇECEK",
}

# Every mention this audit's own scan of the archive finds for these seven
# tickers within their own (anchor_date, price_trade_date] window, reviewed
# by hand. KONYA/VESTL/CCOLA's multi-year gaps mean they hit the recurring
# annual "bagimsiz denetime tabi ortakliklar" numbered registry list (the same
# false-positive class W7-B already established for ALFAS/ENKAI/KONTR at
# bulletin 2-2023) once per year in the window; the 4-2020, 4-2021 and 2-2022
# editions were each inspected directly this work package and match the
# identical numbered-list format (a generic company roster, not a capital
# increase/decrease table row). CCOLA additionally hits two mentions that are
# neither the registry list nor a capital-action row: 63-2021 is a wholly-
# owned subsidiary's merger approval under "D. DIGER BASVURU SONUCLARI" (does
# not touch CCOLA's own share count), and 56-2022 is a bond/note issuance
# under "2. BORCLANMA ARACLARI" (debt, not equity) -- both read directly from
# the bulletin text before being accepted here. Any mention this audit's
# rescan finds that is NOT in this set aborts derive() rather than silently
# asserting completeness -- see verify_capital_action_absence().
KNOWN_NON_ACTION_MENTIONS = {
    ("KONYA", 4, 2021), ("KONYA", 2, 2022), ("KONYA", 2, 2023),
    ("VESTL", 4, 2020), ("VESTL", 4, 2021), ("VESTL", 2, 2022), ("VESTL", 2, 2023),
    ("CCOLA", 4, 2020), ("CCOLA", 4, 2021), ("CCOLA", 63, 2021),
    ("CCOLA", 2, 2022), ("CCOLA", 56, 2022), ("CCOLA", 2, 2023),
}

SHARE_ITEM_KEY = "kpy41_acc5_sermayeyi_temsil_eden"

# CCOLA's 2019-05-14 KAP share observation is the same decimal-separator
# mis-parse class this project's earlier forensic work found for other
# tickers: two of its three nominalValueOfShares entries carry three decimal
# digits (kurus-level precision), and the artifact's own precomputed
# derived_shares/classes fields effectively drop the decimal point on those
# two (e.g. "51114298.631" -> 51114298631), inflating the total ~686x. The
# raw nominalValueOfShares/nominalValuePerShare strings recompute cleanly to
# 25,437,078,200 shares -- exactly matching CCOLA's own 2016-06-23 KAP
# observation, i.e. zero real capital change between the two, a coherent
# story. resolve_share_anchor() always recomputes from the raw strings and
# uses that value regardless; this set only pins which ticker(s) that
# recomputation is EXPECTED to disagree with the artifact's stored figure for,
# so an unexpected future disagreement (or this one disappearing) aborts
# derive() rather than passing silently.
EXPECTED_DECIMAL_BUG_CORRECTED = {"CCOLA"}

CONTENT_FILES = (
    "archive.json", "anchors.json", "evidence.json", "gate_results.json",
    "negative_controls.json", "batch_replay.json", "verdict.json",
)


class W7CAuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    """For this audit's own generated JSON content only (LF line endings by
    construction). Never use on committed binary/PDF source bytes -- the CRLF
    normalization below is wrong for arbitrary binary content."""
    return sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_raw(payload: bytes) -> str:
    """Plain byte hash for binary/arbitrary content (a PDF, a source blob
    handed to PriceLevelActionEvidence). Matches exactly what verify() itself
    computes (sha256 of the exact bytes, no normalization)."""
    return sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    return sha_raw(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n").encode("utf-8")


def load_bulletin_archive() -> dict:
    """Verify the committed SPK bulletin archive: hashes match, and each
    year's own bulletin numbering is gap-free from its minimum through its
    maximum captured number."""
    manifest = json.loads((ARCHIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("contract") != "SPK_BULLETIN_ARCHIVE_V1":
        raise W7CAuditError("BULLETIN_ARCHIVE_CONTRACT_MISMATCH")
    by_year: dict[int, set[int]] = {}
    by_date: dict[str, list[dict]] = {}
    seen_files: set[tuple[int, int]] = set()
    for entry in manifest["entries"]:
        path = ARCHIVE_DIR / entry["filename"]
        if sha_file(path) != entry["sha256"]:
            raise W7CAuditError(f"BULLETIN_HASH_MISMATCH:{entry['filename']}")
        key = (entry["bulletin_year"], entry["bulletin_num"])
        if key in seen_files:
            raise W7CAuditError(f"DUPLICATE_BULLETIN_NUMBER:{key}")
        seen_files.add(key)
        by_year.setdefault(entry["bulletin_year"], set()).add(entry["bulletin_num"])
        by_date.setdefault(entry["date"], []).append(entry)
    for year, nums in by_year.items():
        span = range(min(nums), max(nums) + 1)
        missing = sorted(set(span) - nums)
        if missing:
            raise W7CAuditError(f"BULLETIN_NUMBERING_GAP:{year}:{missing}")
    return {"manifest": manifest, "by_date": by_date}


def extract_bulletin_text(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "".join((page.extract_text() or "") for page in reader.pages).upper()


def verify_capital_action_absence(ticker: str, anchor_date: str, archive: dict) -> list[dict]:
    """Scan every archived bulletin strictly after anchor_date through the
    price trade date for this ticker's company name. Any mention not in
    KNOWN_NON_ACTION_MENTIONS aborts the audit."""
    needle = NAME_SEARCH_KEY[ticker]
    window_dates = sorted(
        d for d in archive["by_date"] if anchor_date < d <= PRICE_TRADE_DATE.isoformat()
    )
    if not window_dates:
        raise W7CAuditError(f"EMPTY_BULLETIN_WINDOW:{ticker}")
    sources = []
    for d in window_dates:
        for entry in sorted(archive["by_date"][d], key=lambda e: e["bulletin_num"]):
            text = extract_bulletin_text(ARCHIVE_DIR / entry["filename"])
            if needle in text:
                key = (ticker, entry["bulletin_num"], entry["bulletin_year"])
                if key not in KNOWN_NON_ACTION_MENTIONS:
                    raise W7CAuditError(
                        f"UNEXPLAINED_MENTION:{ticker} appears in bulletin "
                        f"{entry['bulletin_num']}-{entry['bulletin_year']} ({d}) outside the "
                        f"reviewed false-positive set -- refusing to assert completeness"
                    )
            sources.append({
                "date": d, "bulletin_num": entry["bulletin_num"], "bulletin_year": entry["bulletin_year"],
                "filename": entry["filename"], "sha256": entry["sha256"],
            })
    return sources


def resolve_share_anchor(ticker: str) -> dict:
    """Pick the latest EXPLICIT_CLASS_NOMINALS_RECONCILED/usable KAP share
    observation strictly before CUTOFF (that classification, from
    share_class_observations.jsonl.gz, only screens which creationDate to
    use), then derive shares_out ITSELF straight from the raw
    nominalValueOfShares/nominalValuePerShare strings in raw_responses.jsonl.gz
    -- never from the artifact's own precomputed derived_shares/classes
    fields. This project's forensic work this session found a real
    decimal-separator mis-parse in that precomputed field for several tickers
    (a bug in the one-time derivation, not in the raw KAP API response text
    itself); re-deriving directly from the raw strings every time is what
    actually guards against it, rather than a same-artifact self-consistency
    check that would only catch a bug that disagrees with itself. A ticker
    whose stored derived_shares happens to disagree with this recomputation
    is not rejected -- it is flagged in the receipt and the recomputed value
    (the one with direct textual provenance to the disclosure) is used."""
    creation = None
    with gzip.open(SHARE_HISTORY_DIR / "share_class_observations.jsonl.gz", "rt", encoding="utf-8") as stream:
        best_dt = None
        for line in stream:
            row = json.loads(line)
            if row.get("ticker") != ticker:
                continue
            if row.get("status") != "EXPLICIT_CLASS_NOMINALS_RECONCILED" or not row.get("usable"):
                continue
            candidate_creation = row["published_at_local"]
            dt = datetime.strptime(candidate_creation, "%d/%m/%Y %H:%M:%S")
            if dt.replace(tzinfo=ZoneInfo("Europe/Istanbul")) >= CUTOFF:
                continue
            if best_dt is None or dt > best_dt:
                best_dt, creation, stored_derived_shares = dt, candidate_creation, row["derived_shares"]
    if creation is None:
        raise W7CAuditError(f"NO_USABLE_SHARE_ANCHOR_BEFORE_CUTOFF:{ticker}")

    raw_value = None
    with gzip.open(SHARE_HISTORY_DIR / "raw_responses.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("ticker") != ticker:
                continue
            for entry in row.get("response", []):
                if entry.get("itemKey") != SHARE_ITEM_KEY:
                    continue
                if entry.get("creationDate") == creation:
                    raw_value = entry["value"]
                    break
            if raw_value is not None:
                break
    if raw_value is None:
        raise W7CAuditError(f"RAW_SHARE_RESPONSE_NOT_FOUND:{ticker}:{creation}")

    def to_float(text):
        return float(str(text).replace(",", "."))

    recomputed = 0.0
    for cls in raw_value:
        nominal_total = to_float(cls["nominalValueOfShares"])
        per_share = to_float(cls["nominalValuePerShare"])
        recomputed += nominal_total / per_share

    stored = float(stored_derived_shares)
    decimal_bug_corrected = abs(recomputed - stored) > max(1.0, stored * 1e-6)

    return {
        "anchor_date": best_dt.date(), "creation": creation,
        "shares_out": recomputed, "stored_derived_shares": stored,
        "decimal_bug_corrected": decimal_bug_corrected, "raw_value": raw_value,
    }


def build_evidence_bundle(ticker: str, anchor: dict, sources: list[dict]) -> tuple[PriceLevelActionBundle, dict]:
    share_entry = {
        "itemName": "Sermayeyi Temsil Eden Paylara İlişkin Bilgi", "value": anchor["raw_value"],
        "mkkMemberOid": None, "itemKey": SHARE_ITEM_KEY, "creationDate": anchor["creation"],
    }
    share_bytes = json.dumps([share_entry], ensure_ascii=False).encode("utf-8")
    share_ref = f"KAP_{ticker}_SHARE_HISTORY"
    creation_dt = datetime.strptime(anchor["creation"], "%d/%m/%Y %H:%M:%S")
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
        "source_share_basis": "DATED_UNADJUSTED_SHARES_V1", "source_shares_out": anchor["shares_out"],
        "shares_basis_date": anchor["anchor_date"].isoformat(), "complete_through": PRICE_TRADE_DATE.isoformat(),
        "enumeration_complete": True, "sources": manifest_sources,
        "completeness_source_ref": completeness_ref, "share_source_ref": share_ref, "events": [],
        "completeness_scope": (
            f"SPK_WEEKLY_BULLETIN_CONTINUOUS_COVERAGE_V1:"
            f"({anchor['anchor_date'].isoformat()},{PRICE_TRADE_DATE.isoformat()}]"
        ),
    }
    manifest_bytes = encode_json(manifest)
    evidence = PriceLevelActionEvidence(manifest_bytes, sha_bytes(manifest_bytes), source_bytes)
    receipt = {
        "ticker": ticker, "anchor_date": anchor["anchor_date"].isoformat(), "shares_out": anchor["shares_out"],
        "stored_derived_shares": anchor["stored_derived_shares"],
        "decimal_bug_corrected": anchor["decimal_bug_corrected"], "source_count": len(manifest_sources),
        "manifest_sha256": sha_bytes(manifest_bytes),
    }
    return PriceLevelActionBundle(evidence, ()), receipt


def run_gate(ticker: str, bundle: PriceLevelActionBundle, price_close: float, anchor: dict) -> dict:
    from src.analytics.price_level_valuation_basis import materialize_price_level_market_cap
    price_obs = build_price_level_observation(
        ticker=ticker, trade_date=PRICE_TRADE_DATE, close=price_close, adjusted_close=price_close,
    )
    try:
        result = materialize_price_level_market_cap(
            price=price_obs, shares_out=anchor["shares_out"], shares_basis_date=anchor["anchor_date"],
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


def run_negative_controls(ticker: str, bundle: PriceLevelActionBundle, anchor: dict) -> dict:
    """Prove the gate still rejects bad evidence for these seven tickers too:
    an early cutoff (some listed sources not yet published), and a tampered
    future-dated source."""
    basis_date = anchor["anchor_date"]

    early_cutoff = datetime(2022, 12, 1, 18, 10, tzinfo=ZoneInfo("Europe/Istanbul"))
    case_a = {"case": "cutoff_before_some_sources"}
    try:
        bundle.evidence.verify(
            ticker=ticker, shares_basis_date=basis_date, price_trade_date=PRICE_TRADE_DATE,
            cutoff=early_cutoff, events=(), shares_out=anchor["shares_out"],
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
            cutoff=CUTOFF, events=(), shares_out=anchor["shares_out"],
        )
        case_c.update(rejected=False, error=None)
    except ActionEvidenceError as exc:
        case_c.update(rejected=True, error=str(exc))

    if not case_a["rejected"] or case_a["error"] != "future source publication":
        raise W7CAuditError(f"NEGATIVE_CONTROL_A_FAILED:{ticker}:{case_a}")
    if not case_c["rejected"] or case_c["error"] != "future source publication":
        raise W7CAuditError(f"NEGATIVE_CONTROL_C_FAILED:{ticker}:{case_c}")
    return {"ticker": ticker, "case_a": case_a, "case_c": case_c}


def load_month_diagnostics() -> dict:
    with gzip.open(DIAGNOSTICS, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("signal_date") == SIGNAL_DATE:
                return row.get("per_ticker", {})
    raise W7CAuditError("SIGNAL_DATE_MONTH_NOT_FOUND")


def load_prices_and_sectors() -> tuple[dict, dict]:
    prices, sectors = {}, {}
    with gzip.open(P3_CELLS, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            ticker = row.get("ticker")
            if (ticker in TICKERS and row.get("signal_date") == SIGNAL_DATE
                    and (row.get("price") or {}).get("trade_date") == PRICE_TRADE_DATE.isoformat()):
                prices[ticker] = row["price"]["raw_close"]
                sectors[ticker] = row["historical_family_lineage"]["sector_index_code"]
    missing = sorted(set(TICKERS) - set(prices))
    if missing:
        raise W7CAuditError(f"PRICE_MISSING_FOR_TICKERS:{missing}")
    off_sector = {t: s for t, s in sectors.items() if s != "XUSIN"}
    if off_sector:
        raise W7CAuditError(f"TICKER_NOT_IN_EXPECTED_SECTOR:{off_sector}")
    return prices, sectors


def run_batch_replay(bundles: dict[str, PriceLevelActionBundle], anchors: dict) -> dict:
    per_ticker = load_month_diagnostics()
    prices, sectors = load_prices_and_sectors()
    missing = sorted(set(TICKERS) - set(per_ticker))
    if missing:
        raise W7CAuditError(f"DIAGNOSTICS_MISSING_FOR_TICKERS:{missing}")

    universe_rows, financial_rows, price_rows = [], [], []
    for ticker in TICKERS:
        anchor = anchors[ticker]
        universe_rows.append({"ticker": ticker, "peer_group": sectors[ticker], "sector_family": "NONFIN"})
        quarters = sorted(per_ticker[ticker]["quarters"], key=lambda q: q["period_end"])
        for i, raw in enumerate(quarters):
            values = dict(raw["values"])
            values["shares_out"] = None
            values["shares_basis_date"] = raw["period_end"]
            if i == len(quarters) - 1:
                values["shares_out"] = anchor["shares_out"]
                values["shares_basis_date"] = anchor["anchor_date"].isoformat()
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
        raise W7CAuditError(f"BATCH_REPLAY_RAISED_UNEXPECTEDLY:{exc}") from exc

    rejections = result.rejections.to_dict("records")
    m2_scores = result.m2_scores.to_dict("records")
    diagnostics_by_ticker = {}
    for item in result.report.get("results", []):
        ticker = item.get("ticker")
        if ticker in TICKERS:
            v = item.get("valuation", {})
            diag = v.get("diagnostics", {})
            diagnostics_by_ticker[ticker] = {
                "status": v.get("status"), "reason": v.get("reason"),
                "target_multiples": diag.get("target_multiples"),
                "multiple_details": {
                    mult: {"peer_count": d["peer_count"], "usable": d["usable"]}
                    for mult, d in (diag.get("multiple_details") or {}).items()
                },
                "market_cap": (diag.get("price_level_basis") or {}).get("market_cap"),
            }
    return {
        "m2_score_count": len(m2_scores), "rejection_count": len(rejections),
        "rejection_reasons": {row["ticker"]: row["reason"] for row in rejections},
        "m2_scores": m2_scores, "diagnostics_by_ticker": diagnostics_by_ticker,
    }


def derive() -> dict[str, bytes]:
    archive = load_bulletin_archive()

    anchors = {ticker: resolve_share_anchor(ticker) for ticker in TICKERS}
    if set(anchors) != set(TICKERS):
        raise W7CAuditError("ANCHOR_TICKER_SET_MISMATCH")

    corrected = {t for t, a in anchors.items() if a["decimal_bug_corrected"]}
    if corrected != EXPECTED_DECIMAL_BUG_CORRECTED:
        raise W7CAuditError(
            f"DECIMAL_BUG_CORRECTION_SET_CHANGED:{sorted(corrected)} != "
            f"{sorted(EXPECTED_DECIMAL_BUG_CORRECTED)} -- either a previously-clean ticker's "
            "raw-vs-stored figures now disagree (investigate before trusting its anchor) or a "
            "previously-corrected one no longer does (re-derive this file's docstring/verdict "
            "instead of silently keeping a stale explanation)"
        )

    bundles: dict[str, PriceLevelActionBundle] = {}
    evidence_receipts = []
    for ticker in TICKERS:
        sources = verify_capital_action_absence(ticker, anchors[ticker]["anchor_date"].isoformat(), archive)
        bundle, receipt = build_evidence_bundle(ticker, anchors[ticker], sources)
        bundles[ticker] = bundle
        evidence_receipts.append(receipt)

    prices, _sectors = load_prices_and_sectors()
    gate_results = [run_gate(ticker, bundles[ticker], prices[ticker], anchors[ticker]) for ticker in TICKERS]
    if not all(row["gate_passed"] for row in gate_results):
        failed = [row["ticker"] for row in gate_results if not row["gate_passed"]]
        raise W7CAuditError(f"EVIDENCE_DATING_GATE_UNEXPECTEDLY_REJECTS:{failed}")

    negative_controls = [run_negative_controls(ticker, bundles[ticker], anchors[ticker]) for ticker in TICKERS]

    batch_replay = run_batch_replay(bundles, anchors)
    if batch_replay["rejection_count"] != 0:
        raise W7CAuditError(
            f"UNEXPECTED_REJECTIONS:{batch_replay['rejection_reasons']} -- this audit's fixed "
            "seven-ticker sample was measured to clear minimum_coverage_weight on PE+PB for "
            "every ticker; any rejection here means the fixture data or config changed and "
            "this audit's own narrative needs re-deriving, not silent acceptance"
        )
    if batch_replay["m2_score_count"] != len(TICKERS):
        raise W7CAuditError(
            f"M2_SCORE_COUNT_MISMATCH:{batch_replay['m2_score_count']} != {len(TICKERS)}"
        )
    for ticker, diag in batch_replay["diagnostics_by_ticker"].items():
        details = diag["multiple_details"]
        for multiple in ("PE", "PB"):
            if not details.get(multiple, {}).get("usable"):
                raise W7CAuditError(f"EXPECTED_MULTIPLE_NOT_USABLE:{ticker}:{multiple}")
            if details[multiple]["peer_count"] != len(TICKERS) - 1:
                raise W7CAuditError(
                    f"UNEXPECTED_PEER_COUNT:{ticker}:{multiple}:{details[multiple]['peer_count']}"
                )

    archive_out = {
        "contract": CONTRACT, "bulletin_count": len(archive["manifest"]["entries"]),
        "span_start": min(archive["by_date"]), "span_end": max(archive["by_date"]),
        "archive_manifest_sha256": sha_file(ARCHIVE_DIR / "manifest.json"),
    }
    anchors_out = {"contract": CONTRACT, "anchors": {
        ticker: {
            "anchor_date": info["anchor_date"].isoformat(), "creation": info["creation"],
            "shares_out": info["shares_out"], "stored_derived_shares": info["stored_derived_shares"],
            "decimal_bug_corrected": info["decimal_bug_corrected"],
            "gap_days": (PRICE_TRADE_DATE - info["anchor_date"]).days,
        }
        for ticker, info in anchors.items()
    }}
    evidence_out = {
        "contract": CONTRACT, "cutoff": CUTOFF.isoformat(), "price_trade_date": PRICE_TRADE_DATE.isoformat(),
        "tickers": evidence_receipts,
    }
    gate_out = {"contract": CONTRACT, "results": gate_results}
    negative_out = {"contract": CONTRACT, "results": negative_controls}
    batch_out = {"contract": CONTRACT, **batch_replay}

    verdict = {
        "contract": CONTRACT,
        "status": "M2_MATERIALIZED",
        "finding": (
            "The bottleneck W7-B left standing -- no NONFIN sector's own CORE data carried "
            "minimum_peer_count=5 same-quarter tickers for any relative-valuation multiple at "
            "its one measured cutoff -- is cleared at a different cutoff/sector/multiple "
            "combination (signal_date 2023-08-01, sector XUSIN, multiple PE) with seven "
            "genuinely TTM-complete tickers (BRSAN, CEMTS, QUAGR, TUKAS, KONYA, VESTL, CCOLA). "
            "Each one's KAP share-count anchor and SPK-bulletin evidence of no intervening "
            "capital action pass the unmodified production evidence-dating gate; "
            "run_historical_pit_nonfin_m2_replay, called unmodified with the same "
            "nonfin_valuation.kap_bulk_exact_v1.json config W7-B used, computes PE and PB each "
            "with peer_count=6 for every ticker, clears minimum_coverage_weight=0.5, and "
            "produces seven real, non-zero M2 scores with zero rejections -- the first this "
            "project has ever produced from the NONFIN relative-valuation path."
        ),
        "consequence": (
            "This is a closed seven-ticker sample at one cutoff, not a full-universe result: "
            "it proves the valuation path works end-to-end given genuine, sufficiently-evidenced "
            "peer coverage, not that every NONFIN cell across all 60 historical cutoffs now "
            "clears minimum_peer_count. Extending this to more cells needs the same "
            "evidence-materialization effort (TTM-completeness screening, KAP share-anchor "
            "resolution and internal consistency check, SPK-bulletin capital-action-absence "
            "scan) repeated per cutoff/sector/multiple combination -- most of which this work "
            "package also found genuinely blocked (missing share anchors, non-TRY par values, "
            "stale anchors with an intervening real capital action, or financial-statement "
            "fields the CORE pipeline correctly leaves null) rather than simply unattempted."
        ),
        "reopen_condition": (
            "If a future change to CORE derivation, the KAP share-class history artifact, or "
            "the SPK bulletin archive changes any of: the seven tickers' TTM completeness, "
            "their resolved share anchors' recomputed-vs-stored consistency, the capital-action "
            "scan's result, or the batch replay's peer counts/rejection count -- this audit's "
            "own guards in derive() already raise rather than silently reporting a stale result; "
            "re-run with --apply and update this file's docstring/verdict from the new evidence."
        ),
        "policy": {
            "model_changed": False, "weights_changed": False, "veto_changed": False,
            "peer_or_coverage_threshold_changed": False, "universe_changed": False,
            "neutral_fill": False, "production_code_changed": False,
            "m2_materialized": True, "market_cap_materialized_non_zero_interval": True,
            "scope": "SEVEN_TICKER_CLOSED_SAMPLE_ONE_CUTOFF",
        },
    }
    return {
        "archive.json": encode_json(archive_out), "anchors.json": encode_json(anchors_out),
        "evidence.json": encode_json(evidence_out), "gate_results.json": encode_json(gate_out),
        "negative_controls.json": encode_json(negative_out), "batch_replay.json": encode_json(batch_out),
        "verdict.json": encode_json(verdict),
    }


def git_head() -> str:
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_receipt(content: dict[str, bytes]) -> dict:
    return {
        "contract": "W7C_REAL_M2_SCORE_RECEIPT_V1",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w7c_real_m2_score.py --apply",
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
        raise W7CAuditError("HASH_MODE_MISMATCH")
    content = derive()
    for name in CONTENT_FILES:
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W7CAuditError(f"REDERIVED_HASH_MISMATCH:{name}")
    print("W7C_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


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
    print("W7C_APPLY_OK")


if __name__ == "__main__":
    main()
