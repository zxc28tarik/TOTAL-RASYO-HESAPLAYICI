from __future__ import annotations

"""W7-A — does the production NONFIN M2 replay accept a present-day-captured
corporate-action completeness source for a historical, non-zero interval?

W6 measured that a real, gap-free KAP corporate-action inventory makes the
*peer-cohort-size* gate reachable at 60/60 cutoffs: for SMRTG's own 2023-07-31
cutoff, 8 same-sector NONFIN peers have a cross-validated, dated share basis
and no matching-subject disclosure inside their action window.

That is necessary but this audit shows it is not sufficient. The production
replay (src/analytics/historical_pit_nonfin_m2_replay.py) does not take a
peer's share count on trust: it must pass through
price_level_action_evidence.PriceLevelActionEvidence.verify(), which requires
every evidentiary source's own publication timestamp to be at or before the
historical analysis cutoff. A source captured today (querying KAP now) always
carries today's publication timestamp, regardless of how old the disclosures
it reports on are -- so it is unconditionally rejected as "future source
publication" for any cutoff in the past.

The only route SMRTG's own canary could ever pass is a *zero-length* action
interval, where the target's own dated disclosure serves as both the share
source and the completeness source (nothing needs to be enumerated between a
timestamp and itself). W5's systemic bound already showed at most one such
zero-length peer exists at any given cutoff, never enough to reach
minimum_peer_count=5. This audit re-derives that same conclusion by calling
the real evidence-verification function with real, hash-bound inputs, so the
finding is demonstrated rather than argued.

It produces no score, computes no M2, and changes no production code.
"""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.price_level_action_evidence import PriceLevelActionEvidence, ActionEvidenceError

AUDIT = ROOT / "data/audit/w7a_evidence_dating_gate_v1"
CONTRACT = "W7A_EVIDENCE_DATING_GATE_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

INVENTORY_DIR = ROOT / "data/backtest_sources/kap_monthly_ca_inventory_v1"
COVERING_WINDOW = "2021-10-31_2021-11-06"  # covers AKSEN's 2021-11-04 share-basis date
TARGET_TICKER = "AKSEN"
SHARES_BASIS_DATE = date(2021, 11, 4)
PRICE_TRADE_DATE = date(2023, 7, 31)
CUTOFF = datetime(2023, 7, 31, 18, 10, tzinfo=ZoneInfo("Europe/Istanbul"))
CERTIFIED_SHARES_OUT = 1226338236.0

CONTENT_FILES = ("attempt.json", "verdict.json")


class W7AAuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_manifest(published_at: str, source_ref: str, source_sha256: str) -> dict:
    return {
        "contract": "PRICE_LEVEL_ACTION_COVERAGE_V1",
        "ticker": TARGET_TICKER,
        "source_share_basis": "DATED_UNADJUSTED_SHARES_V1",
        "source_shares_out": CERTIFIED_SHARES_OUT,
        "shares_basis_date": SHARES_BASIS_DATE.isoformat(),
        "complete_through": PRICE_TRADE_DATE.isoformat(),
        "enumeration_complete": True,
        "events": [],
        "completeness_source_ref": source_ref,
        "share_source_ref": source_ref,
        "sources": [{"source_ref": source_ref, "source_sha256": source_sha256, "published_at": published_at}],
    }


def attempt_with_source_dated(published_at: str, source_ref: str, response_path: Path) -> dict:
    """Try to verify a bundle whose one source carries the given publication time."""
    manifest = build_manifest(published_at, source_ref, sha_file(response_path))
    manifest_bytes = encode_json(manifest)
    evidence = PriceLevelActionEvidence(
        manifest_bytes, sha_bytes(manifest_bytes), {source_ref: response_path.read_bytes()}
    )
    try:
        evidence.verify(
            ticker=TARGET_TICKER,
            shares_basis_date=SHARES_BASIS_DATE,
            price_trade_date=PRICE_TRADE_DATE,
            cutoff=CUTOFF,
            events=(),
            shares_out=CERTIFIED_SHARES_OUT,
        )
        return {"published_at": published_at, "accepted": True, "error": None}
    except ActionEvidenceError as exc:
        return {"published_at": published_at, "accepted": False, "error": str(exc)}


def derive() -> dict[str, bytes]:
    manifest_path = INVENTORY_DIR / "capture_manifest.json"
    if not manifest_path.exists():
        raise W7AAuditError("KAP_CA_INVENTORY_NOT_CAPTURED")
    capture_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    window = capture_manifest["windows"].get(COVERING_WINDOW)
    if window is None or window["status"] != "OK":
        raise W7AAuditError(f"COVERING_WINDOW_MISSING_OR_NOT_OK:{COVERING_WINDOW}")
    response_path = INVENTORY_DIR / f"{COVERING_WINDOW}.response.json"
    if sha_file(response_path) != window["response_sha256"]:
        raise W7AAuditError("COVERING_WINDOW_RESPONSE_HASH_MISMATCH")

    # Case A: the source's real, honest publication timestamp -- when this
    # session actually queried KAP. This is the only truthful timestamp
    # available for a present-day capture.
    real_capture_time = window["captured_at"]
    case_a = attempt_with_source_dated(real_capture_time, "KAP_WINDOW_HONEST_TIMESTAMP", response_path)

    # Case B (control): a source genuinely dated at-or-before the cutoff
    # passes the same check, proving the rejection above is about the date,
    # not about the manifest shape or the sha256 plumbing.
    case_b = attempt_with_source_dated(
        CUTOFF.isoformat(), "KAP_WINDOW_BACKDATED_CONTROL", response_path
    )

    attempt = {
        "contract": CONTRACT,
        "target_ticker": TARGET_TICKER,
        "shares_basis_date": SHARES_BASIS_DATE.isoformat(),
        "price_trade_date": PRICE_TRADE_DATE.isoformat(),
        "cutoff": CUTOFF.isoformat(),
        "covering_window": COVERING_WINDOW,
        "covering_window_response_sha256": window["response_sha256"],
        "case_a_honest_capture_timestamp": case_a,
        "case_b_backdated_control": case_b,
    }
    if case_a["accepted"]:
        raise W7AAuditError("HONEST_CAPTURE_TIMESTAMP_UNEXPECTEDLY_ACCEPTED")
    if not case_b["accepted"]:
        raise W7AAuditError("BACKDATED_CONTROL_UNEXPECTEDLY_REJECTED:" + str(case_b["error"]))
    if case_a["error"] != "future source publication":
        raise W7AAuditError(f"UNEXPECTED_REJECTION_REASON:{case_a['error']}")

    verdict = {
        "contract": CONTRACT,
        "status": "BLOCKED",
        "finding": (
            "The production replay's own evidence-verification function rejects any "
            "corporate-action completeness source whose publication timestamp is after "
            "the historical analysis cutoff -- unconditionally, regardless of how "
            "comprehensive or accurate the source's content is. A source captured today "
            "always carries today's timestamp, so it can never certify a non-zero "
            "historical interval, no matter which cutoff or ticker."
        ),
        "consequence": (
            "W6's 60/60 peer-cohort-size finding is real and stands, but it measures a "
            "necessary, not sufficient, condition. The deeper, code-enforced gate found "
            "here means the production-admissible peer count for real M2 remains "
            "governed by W5's zero-interval-only route: at most one ticker per cutoff, "
            "never enough to reach minimum_peer_count=5. Real historical NONFIN M2 via "
            "peer relative valuation is still BLOCKED for every cutoff."
        ),
        "reopen_condition": (
            "A completeness source whose own publication timestamp genuinely predates "
            "or equals the historical cutoff -- e.g. an archived, period-contemporary "
            "official bulletin -- not a retrospective query, however comprehensive. "
            "P2's research already found the one candidate for this (the KAP "
            "corporate-action calendar) returns no historical archive for past months."
        ),
        "policy": {
            "model_changed": False, "weights_changed": False, "veto_changed": False,
            "peer_or_coverage_threshold_changed": False, "universe_changed": False,
            "neutral_fill": False, "production_code_changed": False,
            "m2_materialized": False,
        },
    }
    return {"attempt.json": encode_json(attempt), "verdict.json": encode_json(verdict)}


def git_head() -> str:
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_receipt(content: dict[str, bytes]) -> dict:
    return {
        "contract": "W7A_EVIDENCE_DATING_GATE_RECEIPT_V1",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w7a_evidence_dating_gate.py --apply",
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
        raise W7AAuditError("HASH_MODE_MISMATCH")
    content = derive()
    for name in CONTENT_FILES:
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W7AAuditError(f"REDERIVED_HASH_MISMATCH:{name}")
    print("W7A_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


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
    print("W7A_APPLY_OK")


if __name__ == "__main__":
    main()
