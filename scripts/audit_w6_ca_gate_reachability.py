from __future__ import annotations

"""W6 — does the real KAP corporate-action inventory open the peer gate?

W5 measured that the peer-certification gate was reachable at 0/60 cutoffs
because no non-empty action interval could be proven empty. This audit
consumes the market-wide, gap-free KAP disclosure inventory captured by
scripts/capture_kap_monthly_ca_inventory.py and re-measures that bound with
real, dated evidence instead of the placeholder "zero interval only" route.

Fail-closed by construction: a ticker's window certifies as empty only if
every day in it falls inside a captured, complete window AND no disclosure
whose subject indicates a share-count change (capital increase/decrease,
merger, demerger) was published for that ticker inside the window. Any gap
in capture coverage, or any ambiguous disclosure, blocks certification for
that ticker rather than silently passing it.

It produces no score and changes no production code. It only asks: with this
new evidence, how many of the 60 cutoffs can now reach the peer-cohort gate
that W5 found closed everywhere?
"""

import argparse
from datetime import date, datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analytics.m2_canary_audit import SAFE_SHARE_DERIVATIONS
from scripts.audit_w5_smrtg_canary import (
    nonfin_candidates,
    read_share_class_observations,
    CORE_DIAGNOSTICS,
)

AUDIT = ROOT / "data/audit/w6_ca_gate_reachability_v1"
CONTRACT = "W6_CA_GATE_REACHABILITY_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

INVENTORY_DIR = ROOT / "data/backtest_sources/kap_monthly_ca_inventory_v1"
MANIFEST = INVENTORY_DIR / "capture_manifest.json"

# Fail-closed: any disclosure subject containing one of these fragments is
# treated as a possible share-count change and blocks certification, even
# where it might turn out (on closer reading) to be purely informational
# (e.g. a registered-capital-ceiling notice, which raises a *future* ceiling
# rather than issuing shares today). Being over-inclusive here can only
# refuse a certification that a narrower rule would have allowed -- it can
# never wrongly certify a window that had a real change.
ACTION_SUBJECT_MARKERS = (
    "sermaye artır", "sermaye azalt", "sermaye artırımı", "sermaye azaltımı",
    "birleşme", "bölünme", "pay grubu",
)

CONTENT_FILES = ("coverage.json", "certifications.json", "systemic_bound.json", "verdict.json")


class W6AuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def parse_kap_stamp(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError):
        return None


def is_action_subject(subject: object) -> bool:
    s = str(subject or "").lower()
    return any(marker in s for marker in ACTION_SUBJECT_MARKERS)


def load_manifest() -> dict:
    if not MANIFEST.exists():
        raise W6AuditError("KAP_CA_INVENTORY_NOT_CAPTURED")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def load_inventory(manifest: dict) -> tuple[dict[str, list[tuple[date, date, int]]], dict]:
    """Per-ticker sorted list of (event_date, disclosureIndex) for action rows,
    plus the set of fully-covered day ranges (as sorted, non-overlapping pairs)."""
    windows = manifest["windows"]
    ok = [w for w in windows.values() if w["status"] == "OK"]
    covered = sorted((date.fromisoformat(w["start"]), date.fromisoformat(w["end"])) for w in ok)

    events: dict[str, list[tuple[date, int, str]]] = {}
    source_hashes = {}
    for key, w in windows.items():
        if w["status"] != "OK":
            continue
        response_path = INVENTORY_DIR / f"{key}.response.json"
        rows = json.loads(response_path.read_bytes())
        source_hashes[key] = sha_file(response_path)
        for row in rows:
            if not is_action_subject(row.get("subject")):
                continue
            stamp = parse_kap_stamp(row.get("publishDate"))
            if stamp is None:
                continue
            codes = str(row.get("stockCodes") or "").split(",")
            for code in codes:
                code = code.strip()
                if not code:
                    continue
                events.setdefault(code, []).append(
                    (stamp.date(), row.get("disclosureIndex"), row.get("subject"))
                )
    for code in events:
        events[code].sort(key=lambda item: item[0])
    return events, {"covered_ranges": covered, "source_sha256": source_hashes}


def day_fully_covered(day: date, covered: list[tuple[date, date]]) -> bool:
    # covered is sorted and non-overlapping by construction (adjacent chunks).
    lo, hi = 0, len(covered) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        s, e = covered[mid]
        if day < s:
            hi = mid - 1
        elif day > e:
            lo = mid + 1
        else:
            return True
    return False


def window_fully_covered(start: date, end: date, covered: list[tuple[date, date]]) -> bool:
    cursor = start
    for s, e in covered:
        if e < cursor:
            continue
        if s > cursor:
            return False
        cursor = max(cursor, e) + __import__("datetime").timedelta(days=1)
        if cursor > end:
            return True
    return cursor > end


def certify_ticker_window(
    ticker: str, window_start: date, cutoff: date, events: dict, covered: list
) -> dict:
    """Open-closed interval (window_start, cutoff]: prove no action inside it."""
    if window_start >= cutoff:
        return {"ticker": ticker, "certifiable": True, "reason": "ZERO_OR_NEGATIVE_LENGTH_INTERVAL"}
    if not window_fully_covered(window_start, cutoff, covered):
        return {"ticker": ticker, "certifiable": False, "reason": "INVENTORY_COVERAGE_GAP"}
    hits = [
        e for e in events.get(ticker, [])
        if window_start < e[0] <= cutoff
    ]
    if hits:
        return {
            "ticker": ticker, "certifiable": False, "reason": "ACTION_DETECTED_IN_WINDOW",
            "events": [{"date": e[0].isoformat(), "disclosureIndex": e[1], "subject": e[2]} for e in hits],
        }
    return {"ticker": ticker, "certifiable": True, "reason": "NO_ACTION_IN_COVERED_WINDOW"}


def build_certifications(events: dict, covered: list, observations: dict) -> dict:
    """For every NONFIN candidate at every cutoff, is its window now certifiable?"""
    rows = []
    for line in gzip.open(CORE_DIAGNOSTICS, "rt", encoding="utf-8"):
        record = json.loads(line)
        cutoff = datetime.fromisoformat(record["analysis_at"]).date()
        candidates = nonfin_candidates(record.get("per_ticker") or {})
        for ticker in candidates:
            stamps = [s.date() for s in observations.get(ticker, []) if s.date() <= cutoff]
            if not stamps:
                rows.append({
                    "cutoff": cutoff.isoformat(), "ticker": ticker,
                    "certifiable": False, "reason": "NO_USABLE_PRE_CUTOFF_SHARE_CLASS_OBSERVATION",
                })
                continue
            latest = max(stamps)
            result = certify_ticker_window(ticker, latest, cutoff, events, covered)
            rows.append({
                "cutoff": cutoff.isoformat(), "ticker": ticker,
                "window_start": latest.isoformat(), "window_days": (cutoff - latest).days,
                **result,
            })
    return {"contract": CONTRACT, "rows": rows}


def build_systemic_bound(certifications: dict, required: int) -> dict:
    by_cutoff: dict[str, list[dict]] = {}
    for row in certifications["rows"]:
        by_cutoff.setdefault(row["cutoff"], []).append(row)

    cutoff_rows = []
    for cutoff, rows in sorted(by_cutoff.items()):
        certifiable = sorted(r["ticker"] for r in rows if r["certifiable"])
        cutoff_rows.append({
            "cutoff_date": cutoff,
            "nonfin_candidates": len(rows),
            "newly_certifiable": len(certifiable),
            "certifiable_tickers": certifiable,
            # one of the certifiable tickers can be the target itself
            "gate_reachable": len(certifiable) >= required + 1,
        })

    reason_counts: dict[str, int] = {}
    for row in certifications["rows"]:
        reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1

    return {
        "contract": CONTRACT,
        "cutoffs": len(cutoff_rows),
        "required_certifiable_per_cutoff": required + 1,
        "gate_reachable_cutoffs": sum(1 for r in cutoff_rows if r["gate_reachable"]),
        "reason_counts": dict(sorted(reason_counts.items())),
        "distribution": dict(sorted(
            __import__("collections").Counter(r["newly_certifiable"] for r in cutoff_rows).items()
        )),
        "cutoff_rows": cutoff_rows,
        "compared_to_w5": {
            "w5_gate_reachable_cutoffs": 0,
            "w5_source": "data/audit/w5_smrtg_canary_v1/systemic_bound.json",
        },
    }


def build_verdict(coverage: dict, systemic: dict) -> dict:
    return {
        "contract": CONTRACT,
        "status": "GATE_OPENED" if systemic["gate_reachable_cutoffs"] > 0 else "STILL_BLOCKED",
        "gate_reachable_cutoffs": systemic["gate_reachable_cutoffs"],
        "total_cutoffs": systemic["cutoffs"],
        "coverage_complete": coverage["complete"],
        "coverage_gaps": coverage.get("gaps", []),
        "policy": {
            "model_changed": False, "weights_changed": False, "veto_changed": False,
            "peer_or_coverage_threshold_changed": False, "universe_changed": False,
            "neutral_fill": False, "production_code_changed": False,
            "m2_materialized": False,
        },
        "note": (
            "This audit only re-measures peer-gate reachability with real corporate-action "
            "evidence. Materializing an actual historical M2 value for any newly-opened cell "
            "is a separate, subsequent step that still must call the production valuation "
            "combiner and satisfy every other locked contract."
        ),
    }


def derive() -> dict[str, bytes]:
    manifest = load_manifest()
    coverage = manifest.get("coverage", {})
    if not coverage.get("complete"):
        raise W6AuditError(f"KAP_CA_INVENTORY_INCOMPLETE:gaps={coverage.get('gaps')}")

    events, inv_meta = load_inventory(manifest)
    observations = read_share_class_observations()

    certifications = build_certifications(events, inv_meta["covered_ranges"], observations)
    systemic = build_systemic_bound(certifications, required=5)
    verdict = build_verdict(coverage, systemic)

    coverage_out = dict(coverage)
    coverage_out["contract"] = CONTRACT
    coverage_out["manifest_sha256"] = sha_file(MANIFEST)
    coverage_out["distinct_action_tickers"] = len(events)
    coverage_out["total_action_events"] = sum(len(v) for v in events.values())

    return {
        "coverage.json": encode_json(coverage_out),
        "certifications.json": encode_json(certifications),
        "systemic_bound.json": encode_json(systemic),
        "verdict.json": encode_json(verdict),
    }


def git_head() -> str:
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_receipt(content: dict[str, bytes]) -> dict:
    verdict = json.loads(content["verdict.json"])
    systemic = json.loads(content["systemic_bound.json"])
    coverage = json.loads(content["coverage.json"])
    return {
        "contract": "W6_CA_GATE_REACHABILITY_RECEIPT_V1",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w6_ca_gate_reachability.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": coverage["manifest_sha256"],
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "coverage": {
            "status": verdict["status"],
            "gate_reachable_cutoffs": systemic["gate_reachable_cutoffs"],
            "total_cutoffs": systemic["cutoffs"],
            "distinct_action_tickers": coverage["distinct_action_tickers"],
            "total_action_events": coverage["total_action_events"],
        },
        "policy": verdict["policy"],
    }


def write(content: dict[str, bytes], receipt: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    for name, payload in content.items():
        (AUDIT / name).write_bytes(payload)
    (AUDIT / "receipt.json").write_bytes(encode_json(receipt))


def check() -> None:
    receipt = json.loads((AUDIT / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W6AuditError("HASH_MODE_MISMATCH")
    if sha_file(MANIFEST) != receipt["manifest_sha256"]:
        raise W6AuditError("MANIFEST_HASH_MISMATCH")
    content = derive()
    for name in CONTENT_FILES:
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W6AuditError(f"REDERIVED_HASH_MISMATCH:{name}")
    print("W6_CA_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


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
    print("W6_CA_APPLY_OK")


if __name__ == "__main__":
    main()
