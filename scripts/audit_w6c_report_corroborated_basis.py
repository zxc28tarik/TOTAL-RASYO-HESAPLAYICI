from __future__ import annotations

"""W6-C — does the issuer's own dated report chain shrink the residual gap?

W5/W6/W7-A established: real M2 needs a share basis whose evidentiary
sources are all dated at or before the historical cutoff, and the only
gap-free route found (W6's KAP query) fails that test because it was
captured today. This audit asks a narrower, more tractable question: for
every NONFIN candidate cell (60 cutoffs x every ticker), can the ticker's
OWN sequence of already-dated quarterly financial disclosures -- each an
authentic historical KAP filing, already sitting in this repo's own
core_diagnostics artifact with a genuine published_at -- extend an
externally-certified share count forward in time, closer to the cutoff,
without needing anything fetched today?

The logic mirrors what was done by hand for SMRTG's 8 peers in this
session: start from an externally-certified share count (the safe
EXPLICIT_CLASS_NOMINAL_SUM KAP disclosure), then walk the ticker's own
quarterly reports forward in dated order. As long as every report up to
some point keeps repeating the identical share count, that repetition is
itself period-contemporary corroboration -- each report is its own
separately-filed, separately-dated document, not a retrospective query.
The first report that shows a different value (or the end of the series)
stops the chain.

Two honestly different readings of "how close does this get us" are
reported side by side, not silently collapsed into one:

  - report_published_at: the date the corroborating report was filed
    (optimistic -- assumes a company discloses a capital change promptly,
    independent of the next quarterly filing).
  - report_period_end: the fiscal period-end the report's balance sheet
    describes (conservative -- the report only actually vouches for its
    own period-end, not for anything after it).

Choosing between them is a modelling decision this audit does not make.
It measures both and leaves the choice open. It produces no M2, computes
no score, and changes no production code -- it only measures how far a
chain of already-dated documents can narrow the open, unverifiable
interval before *any* external check is needed.
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

from scripts.audit_w5_smrtg_canary import (
    CORE_DIAGNOSTICS,
    RECONCILED,
    nonfin_candidates,
    parse_kap_stamp,
    read_share_class_observations,
)

SHARE_CLASSES = ROOT / "data/backtest_sources/kap_share_class_history_v1/share_class_observations.jsonl.gz"

AUDIT = ROOT / "data/audit/w6c_report_corroborated_basis_v1"
CONTRACT = "W6C_REPORT_CORROBORATED_BASIS_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

CONTENT_FILES = ("rows.json", "summary.json", "verdict.json")


class W6CAuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def parse_published_at(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def read_share_class_derived_values() -> dict[str, dict[datetime, float]]:
    """ticker -> {observation timestamp: derived_shares}, same filter as W5/W6."""
    out: dict[str, dict[datetime, float]] = {}
    for line in gzip.open(SHARE_CLASSES, "rt", encoding="utf-8"):
        row = json.loads(line)
        if not row.get("usable") or row.get("status") != RECONCILED:
            continue
        stamp = parse_kap_stamp(row.get("published_at_local"))
        ticker = row.get("ticker")
        derived = row.get("derived_shares")
        if stamp is None or not ticker or derived is None:
            continue
        out.setdefault(str(ticker), {})[stamp] = float(derived)
    return out


def build_report_series(records: list[dict]) -> dict[str, list[tuple[date, date, float]]]:
    """ticker -> sorted [(period_end, published_at_date, shares_out), ...]."""
    series: dict[str, list[tuple[date, date, float]]] = {}
    for record in records:
        cutoff = datetime.fromisoformat(record["analysis_at"])
        for ticker in nonfin_candidates(record.get("per_ticker") or {}):
            payload = record["per_ticker"][ticker]
            for quarter in payload.get("quarters") or []:
                pub = parse_published_at(quarter.get("published_at"))
                if pub is None or pub > cutoff:
                    continue
                shares = quarter.get("values", {}).get("shares_out")
                if shares is None:
                    continue
                try:
                    period_end = date.fromisoformat(str(quarter["period_end"]))
                except (KeyError, ValueError):
                    continue
                entry = (period_end, pub.date(), float(shares))
                bucket = series.setdefault(ticker, [])
                if entry not in bucket:
                    bucket.append(entry)
    for ticker in series:
        series[ticker] = sorted(set(series[ticker]))
    return series


def chain_extend(
    anchor_date: date,
    anchor_shares: float,
    reports: list[tuple[date, date, float]],
    cutoff_date: date,
) -> dict:
    """Walk reports (period_end, published_at, shares_out) dated after the anchor,
    in published-at order, for as long as each one repeats the anchor's value."""
    candidates = sorted(
        (pub, pe, shares) for pe, pub, shares in reports if anchor_date < pub <= cutoff_date
    )
    matched: list[tuple[date, date, float]] = []
    for pub, pe, shares in candidates:
        if abs(shares - anchor_shares) > max(1.0, abs(anchor_shares) * 1e-9):
            break
        matched.append((pub, pe, shares))
    if not matched:
        return {
            "corroborating_quarters": 0,
            "basis_date_published_at": anchor_date.isoformat(),
            "basis_date_period_end": anchor_date.isoformat(),
        }
    last_pub, last_pe, _ = matched[-1]
    return {
        "corroborating_quarters": len(matched),
        "basis_date_published_at": last_pub.isoformat(),
        "basis_date_period_end": last_pe.isoformat(),
    }


def derive() -> dict[str, bytes]:
    observations = read_share_class_observations()
    derived_values = read_share_class_derived_values()
    records = [
        json.loads(line) for line in gzip.open(CORE_DIAGNOSTICS, "rt", encoding="utf-8")
    ]
    report_series = build_report_series(records)

    rows: list[dict] = []
    for record in records:
        cutoff_dt = datetime.fromisoformat(record["analysis_at"])
        cutoff_date = cutoff_dt.date()
        for ticker in nonfin_candidates(record.get("per_ticker") or {}):
            stamps = [s for s in observations.get(ticker, []) if s.date() <= cutoff_date]
            if not stamps:
                rows.append({
                    "cutoff": cutoff_dt.isoformat(), "ticker": ticker,
                    "has_external_anchor": False,
                })
                continue
            anchor_stamp = max(stamps)
            anchor_date = anchor_stamp.date()
            # Re-derive the anchor's certified share count from the same source
            # the W5/W6 auditors use, rather than trusting a second copy of it.
            anchor_shares = derived_values.get(ticker, {}).get(anchor_stamp)
            if anchor_shares is None:
                raise W6CAuditError(f"ANCHOR_SHARES_LOOKUP_FAILED:{ticker}@{anchor_stamp}")
            reports = report_series.get(ticker, [])
            extension = chain_extend(anchor_date, anchor_shares, reports, cutoff_date)
            gap_before = (cutoff_date - anchor_date).days
            gap_after_published = (
                cutoff_date - date.fromisoformat(extension["basis_date_published_at"])
            ).days
            gap_after_period_end = (
                cutoff_date - date.fromisoformat(extension["basis_date_period_end"])
            ).days
            rows.append({
                "cutoff": cutoff_dt.isoformat(),
                "ticker": ticker,
                "has_external_anchor": True,
                "anchor_date": anchor_date.isoformat(),
                "anchor_shares_out": anchor_shares,
                "corroborating_quarters": extension["corroborating_quarters"],
                "gap_before_days": gap_before,
                "gap_after_published_at_days": gap_after_published,
                "gap_after_period_end_days": gap_after_period_end,
                "shrunk": gap_after_published < gap_before,
            })

    rows.sort(key=lambda r: (r["cutoff"], r["ticker"]))
    summary = build_summary(rows)
    verdict = build_verdict(summary)
    return {
        "rows.json": encode_json({"contract": CONTRACT, "rows": rows}),
        "summary.json": encode_json(summary),
        "verdict.json": encode_json(verdict),
    }


def build_summary(rows: list[dict]) -> dict:
    with_anchor = [r for r in rows if r["has_external_anchor"]]
    shrunk = [r for r in with_anchor if r["shrunk"]]

    def stats(values: list[int]) -> dict:
        if not values:
            return {"count": 0, "median": None, "mean": None, "min": None, "max": None}
        ordered = sorted(values)
        n = len(ordered)
        median = (
            ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
        )
        return {
            "count": n, "median": median, "mean": round(sum(ordered) / n, 2),
            "min": ordered[0], "max": ordered[-1],
        }

    return {
        "contract": CONTRACT,
        "total_cells": len(rows),
        "cells_with_external_anchor": len(with_anchor),
        "cells_without_external_anchor": len(rows) - len(with_anchor),
        "cells_with_any_corroboration": sum(
            1 for r in with_anchor if r["corroborating_quarters"] > 0
        ),
        "cells_shrunk": len(shrunk),
        "gap_before_days": stats([r["gap_before_days"] for r in with_anchor]),
        "gap_after_published_at_days": stats([r["gap_after_published_at_days"] for r in with_anchor]),
        "gap_after_period_end_days": stats([r["gap_after_period_end_days"] for r in with_anchor]),
        "distinct_tickers_shrunk": sorted({r["ticker"] for r in shrunk}),
    }


def build_verdict(summary: dict) -> dict:
    return {
        "contract": CONTRACT,
        "status": "GAP_NARROWED_NOT_CLOSED",
        "finding": (
            "The issuer's own dated report chain narrows the anchor-to-cutoff gap for "
            f"{summary['cells_shrunk']}/{summary['cells_with_external_anchor']} certified "
            "cells, but the gap after narrowing (median "
            f"{summary['gap_after_published_at_days']['median']} days, optimistic reading) "
            "is still non-zero for essentially every cell. It has not reached zero at any "
            "cell measured here, so it does not by itself resolve W7-A's finding."
        ),
        "unresolved_methodology_choice": (
            "Two readings of how far this narrows the gap are reported and neither is "
            "adopted here: using a corroborating report's own filing date "
            "(report_published_at) is optimistic -- it assumes a capital change would be "
            "disclosed promptly and separately rather than waiting for the next quarterly "
            "filing; using the report's period-end (report_period_end) is conservative -- "
            "the report only actually vouches for its own balance-sheet date. Picking "
            "between them is a modelling decision, not a data question, and is left open."
        ),
        "policy": {
            "model_changed": False, "weights_changed": False, "veto_changed": False,
            "peer_or_coverage_threshold_changed": False, "universe_changed": False,
            "neutral_fill": False, "production_code_changed": False,
            "m2_materialized": False,
        },
    }


def git_head() -> str:
    import subprocess
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_receipt(content: dict[str, bytes]) -> dict:
    summary = json.loads(content["summary.json"])
    return {
        "contract": "W6C_REPORT_CORROBORATED_BASIS_RECEIPT_V1",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w6c_report_corroborated_basis.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "core_diagnostics_sha256": sha_file(CORE_DIAGNOSTICS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "summary": {
            "total_cells": summary["total_cells"],
            "cells_with_external_anchor": summary["cells_with_external_anchor"],
            "cells_shrunk": summary["cells_shrunk"],
            "gap_before_median": summary["gap_before_days"]["median"],
            "gap_after_published_median": summary["gap_after_published_at_days"]["median"],
        },
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
        raise W6CAuditError("HASH_MODE_MISMATCH")
    if sha_file(CORE_DIAGNOSTICS) != receipt["core_diagnostics_sha256"]:
        raise W6CAuditError("CORE_DIAGNOSTICS_HASH_MISMATCH")
    content = derive()
    for name in CONTENT_FILES:
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W6CAuditError(f"REDERIVED_HASH_MISMATCH:{name}")
    print("W6C_CHECK_PASS " + receipt["output_sha256"]["verdict.json"])


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
    print("W6C_APPLY_OK")


if __name__ == "__main__":
    main()
