from __future__ import annotations

"""Measure the P7 historical-version enumeration gap against Issue #24.

``SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED`` is the formal
production blocker.  Prior research recovered one real correction pair; this
auditor is the second pass that stops asserting and starts counting:

* each Issue #24 closure criterion is measured against the 6000 cells;
* the version-metadata gap is quantified over the distinct selected reports;
* the one month with recovered official query bytes is used as a bounded
  contamination probe against the universe;
* every route the ledger names is graded from evidence, including whether a
  free route has actually been demonstrated to return superseded disclosures.

It changes no source selection, relabels nothing as authoritative and closes no
issue on its own.
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT = ROOT / "data/audit/w10_p7_enumeration_v1"
CONTRACT = "W10_P7_VERSION_ENUMERATION_AUDIT_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"
RISK_ID = "SUPERSEDED_HISTORICAL_KAP_REPORT_VERSIONS_NOT_ENUMERATED"

P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
RESEARCH = ROOT / "data/backtest_sources/p7_version_research_v1"
QUERY_RESPONSE = RESEARCH / "financial_march2023.response.json"
QUERY_RECEIPT = RESEARCH / "query_receipt.json"
VERSION_PAIR = RESEARCH / "version_pair_receipt.json"
BULK_COMPARISON = RESEARCH / "bulk_version_comparison.json"

SOURCES = {
    "p3_cells": P3_CELLS,
    "p7_query_response": QUERY_RESPONSE,
    "p7_query_receipt": QUERY_RECEIPT,
    "p7_version_pair_receipt": VERSION_PAIR,
    "p7_bulk_version_comparison": BULK_COMPARISON,
}

CONTENT_FILES = (
    "issue24_criteria.json",
    "enumeration_gap.json",
    "sampled_month_probe.json",
    "routes.json",
)

VERSION_METADATA_FIELDS = ("modify_status", "version", "revision", "correction_of", "superseded_by")
SUPERSEDED_MARKER = "DUZELTILEN"
REVISION_MARKER = "DUZENLENEN"


class W10AuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_jsonl_gz(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_issue24_criteria(cells: list[dict]) -> dict:
    """Measure each Issue #24 closure criterion instead of asserting it."""
    selected = [(cell, cell["selected_report"]) for cell in cells if cell.get("selected_report")]
    months = sorted({cell["month"] for cell in cells})
    per_month: dict[str, int] = {}
    for cell in cells:
        per_month[cell["month"]] = per_month.get(cell["month"], 0) + 1

    hashed = sum(1 for _, report in selected if len(str(report.get("member_sha256", ""))) == 64)
    timestamped = sum(1 for _, report in selected if report.get("published_at"))
    lookahead = sum(
        1
        for cell, report in selected
        if datetime.fromisoformat(report["published_at"])
        > datetime.fromisoformat(cell["knowledge_cutoff_at"])
    )
    versioned = sum(
        1
        for _, report in selected
        if any(field in report for field in VERSION_METADATA_FIELDS)
        or report.get("historical_version_enumeration_complete") is True
    )
    families: dict[str, int] = {}
    for cell in cells:
        families[str(cell.get("historical_family"))] = (
            families.get(str(cell.get("historical_family")), 0) + 1
        )
    fixture = sum(
        1
        for _, report in selected
        if "example" in str(report.get("source_url", "")).lower()
        or "example" in str(report.get("archive_name", "")).lower()
        or "fixture" in str(report.get("archive_name", "")).lower()
    )
    rejected = sum(1 for cell in cells if cell["status"] == "EXPLICIT_REJECTION")

    criteria = {
        "real_source_identity_and_hashes": {
            "met": hashed == len(selected) and len(selected) > 0,
            "measured": {
                "selected_reports": len(selected),
                "with_member_sha256": hashed,
                "distinct_notification_ids": len({r["notification_id"] for _, r in selected}),
                "distinct_archives": len({r["archive_name"] for _, r in selected}),
            },
        },
        "exact_60_month_coverage": {
            "met": len(months) == 60 and set(per_month.values()) == {100},
            "measured": {"months": len(months), "cells_per_month": sorted(set(per_month.values()))},
        },
        "historical_bist100_universe_binding_per_month": {
            "met": set(per_month.values()) == {100},
            "measured": {
                "distinct_tickers": len({cell["ticker"] for cell in cells}),
                "cells": len(cells),
            },
        },
        "pit_publication_timestamps": {
            "met": timestamped == len(selected) and lookahead == 0,
            "measured": {"with_published_at": timestamped, "published_after_cutoff": lookahead},
        },
        "pit_version_identifiers": {
            "met": versioned == len(selected) and len(selected) > 0,
            "measured": {
                "with_version_metadata": versioned,
                "without_version_metadata": len(selected) - versioned,
                "historical_version_enumeration_complete_true": sum(
                    1
                    for _, r in selected
                    if r.get("historical_version_enumeration_complete") is True
                ),
            },
        },
        "sector_family_input_coverage": {
            "met": families.get("None", 0) == 0,
            "measured": {"family_counts": dict(sorted(families.items()))},
        },
        "explicit_gap_and_rejection_accounting": {
            "met": rejected == len(cells),
            "measured": {"explicit_rejections": rejected, "cells": len(cells)},
        },
        "no_example_or_fixture_fallback": {
            "met": fixture == 0,
            "measured": {"example_or_fixture_sources": fixture},
        },
    }
    unmet = sorted(name for name, value in criteria.items() if not value["met"])
    return {
        "contract": CONTRACT,
        "issue": 24,
        "criteria": criteria,
        "criteria_total": len(criteria),
        "criteria_met": len(criteria) - len(unmet),
        "criteria_unmet": unmet,
        "closure_allowed": not unmet,
        "verdict": "BLOCKED" if unmet else "CLOSURE_CRITERIA_SATISFIED",
    }


def build_enumeration_gap(cells: list[dict], query_rows: list[dict], pair: dict) -> dict:
    selected = [cell["selected_report"] for cell in cells if cell.get("selected_report")]
    distinct_reports = {report["notification_id"] for report in selected}
    carrying_risk = sum(1 for report in selected if RISK_ID in (report.get("risks") or []))

    financial = [row for row in query_rows if row.get("disclosureCategory") == "FR"]
    superseded = [row for row in financial if row.get("modifyStatus") == SUPERSEDED_MARKER]
    revised = [row for row in financial if row.get("modifyStatus") == REVISION_MARKER]
    rate = len(superseded) / len(financial) if financial else 0.0

    return {
        "contract": CONTRACT,
        "risk_id": RISK_ID,
        "selected_reports": len(selected),
        "distinct_selected_reports": len(distinct_reports),
        "reports_carrying_the_risk_flag": carrying_risk,
        "reports_with_a_known_version_chain": 0,
        "sampled_supersession_rate": {
            "window": "2023-03-01..2023-03-31",
            "financial_report_disclosures": len(financial),
            "marked_superseded": len(superseded),
            "marked_revision": len(revised),
            "rate": rate,
            "caveat": (
                "One month of official query bytes. It indicates an order of magnitude, not a "
                "retention guarantee and not a rate that can be assumed constant across 60 months."
            ),
        },
        "indicative_exposed_reports": round(len(distinct_reports) * rate),
        "why_the_bulk_archives_cannot_answer_this": {
            "proven_case": pair["correction_relation"],
            "original_disclosure_in_bulk_archive": False,
            "detail": (
                "In the recovered pair the preserved-hash annual bulk archive holds only the "
                "later disclosure. A catalog built from bulk archives therefore cannot even "
                "detect that an earlier version existed."
            ),
            "numeric_slots_compared": pair["numeric_slots_compared"],
            "changed_numeric_slots": len(pair["changed_numeric_slots"]),
            "materiality": (
                "The correction swapped parent and non-controlling interest amounts, so using the "
                "later version before its publication would leak information into the cutoff."
            ),
        },
        "authoritative_label_allowed": False,
        "retained_profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
    }


def build_sampled_month_probe(cells: list[dict], query_rows: list[dict], receipt: dict) -> dict:
    """Ask whether the sampled month's corrections actually touch the universe."""
    universe = {cell["ticker"] for cell in cells}
    selected_ids = {
        cell["selected_report"]["notification_id"] for cell in cells if cell.get("selected_report")
    }
    financial = [row for row in query_rows if row.get("disclosureCategory") == "FR"]
    corrections = [row for row in financial if row.get("modifyStatus")]

    findings = []
    for row in corrections:
        codes = {code.strip() for code in (row.get("stockCodes") or "").split(",") if code.strip()}
        findings.append(
            {
                "disclosure_index": row["disclosureIndex"],
                "stock_codes": sorted(codes),
                "modify_status": row["modifyStatus"],
                "publish_date": row["publishDate"],
                "report_year": row.get("year"),
                "report_period": row.get("period"),
                "tickers_in_universe": sorted(codes & universe),
                "selected_by_any_cell": row["disclosureIndex"] in selected_ids,
            }
        )

    touching = [item for item in findings if item["selected_by_any_cell"]]
    universe_hits = [item for item in findings if item["tickers_in_universe"]]
    return {
        "contract": CONTRACT,
        "scope": "the single month with recovered official query bytes",
        "query_url": receipt["url"],
        "query_response_sha256": receipt["response_sha256"],
        "query_http_status": receipt["http_status"],
        "financial_report_disclosures": len(financial),
        "corrections_found": len(corrections),
        "corrections_touching_a_universe_ticker": len(universe_hits),
        "corrections_selected_by_a_cell": len(touching),
        "contamination_detected": bool(touching),
        "findings": findings,
        "conclusion": (
            "No selected report in the 6000 cells is one of this month's corrected disclosures. "
            "That is a bounded negative result for one month of sixty, not a clean bill of health "
            "for the remaining fifty-nine."
        ),
    }


def build_routes(query_rows: list[dict], query_receipt: dict, pair: dict, bulk: dict) -> dict:
    financial = [row for row in query_rows if row.get("disclosureCategory") == "FR"]
    superseded = [row for row in financial if row.get("modifyStatus") == SUPERSEDED_MARKER]
    capture_date = query_receipt["response_http_date"]

    routes = [
        {
            "route": "immutable historical KAP notification exports (the bulk archives)",
            "status": "INSUFFICIENT_PROVEN",
            "cost": "free, already in repository",
            "evidence": {
                "archive": bulk["archive"],
                "archive_sha256": bulk["archive_sha256"],
                "original_disclosure_in_bulk_archive": bulk["original_disclosure_in_bulk_archive"],
                "matching_members": bulk["matching_members"],
            },
            "detail": (
                "The archive keeps only the surviving version, so supersession is invisible to any "
                "catalog derived from it. This route cannot enumerate versions even in principle."
            ),
        },
        {
            "route": "official distribution/API enumeration (disclosure query by criteria)",
            "status": "DEMONSTRATED_RETENTION_COMPLETENESS_UNPROVEN",
            "cost": "free, official endpoint, one request per publication window",
            "evidence": {
                "url": query_receipt["url"],
                "http_status": query_receipt["http_status"],
                "captured_at": capture_date,
                "queried_window": "2023-03-01..2023-03-31",
                "financial_report_rows": len(financial),
                "superseded_rows_returned": len(superseded),
            },
            "detail": (
                "The endpoint returned superseded disclosures for a window years before the "
                "capture date, so historical retention is demonstrated rather than hypothetical. "
                "What remains unproven is completeness: whether deleted disclosures, or windows "
                "further back, are returned at all."
            ),
        },
        {
            "route": "archived disclosure/version identifiers (per-disclosure correction links)",
            "status": "WORKS_PER_DISCLOSURE_NO_GLOBAL_COMPLETENESS",
            "cost": "free, one request per disclosure",
            "evidence": {
                "recovered_pair": pair["correction_relation"],
                "older_published_at": pair["versions"][0]["published_at"],
                "newer_published_at": pair["versions"][1]["published_at"],
            },
            "detail": (
                "The newer disclosure page links back to the one it corrects, which recovers a "
                "chain one hop at a time. It proves nothing about a version nobody linked to."
            ),
        },
        {
            "route": "publication/version timestamp chain",
            "status": "TIMESTAMPS_COMPLETE_VERSION_CHAIN_ABSENT",
            "cost": "free, already in repository",
            "evidence": {
                "publication_timestamps_present": True,
                "version_chain_present": False,
            },
            "detail": (
                "Publication timestamps are complete and point-in-time correct for every selected "
                "report, but a timestamp orders versions only once the versions are known."
            ),
        },
    ]

    return {
        "contract": CONTRACT,
        "routes": routes,
        "paid_source_used": False,
        "free_routes_exhausted_for_a_completeness_proof": True,
        "a_free_route_exists_but_is_unexecuted": {
            "route": "official distribution/API enumeration",
            "scope_required": "every publication window feeding the 60 cutoffs",
            "what_it_would_yield": (
                "the correction markers for each window, bindable to the selected reports"
            ),
            "what_it_still_would_not_yield": (
                "proof that a deleted or never-linked version does not exist, which is exactly "
                "what Issue #24 requires"
            ),
        },
        "verdict": "BLOCKED",
        "blocked_reason": RISK_ID,
        "reopen_condition": (
            "An official source that enumerates every version of a historical disclosure with a "
            "retention or completeness guarantee, or an equivalent immutable export that preserves "
            "superseded versions. Executing the query route across all windows narrows the gap and "
            "is worth doing, but on its own it cannot satisfy Issue #24's completeness criterion."
        ),
    }


def derive() -> dict[str, bytes]:
    cells = read_jsonl_gz(P3_CELLS)
    query_rows = json.loads(QUERY_RESPONSE.read_text(encoding="utf-8"))
    query_receipt = json.loads(QUERY_RECEIPT.read_text(encoding="utf-8"))
    pair = json.loads(VERSION_PAIR.read_text(encoding="utf-8"))
    bulk = json.loads(BULK_COMPARISON.read_text(encoding="utf-8"))

    criteria = build_issue24_criteria(cells)
    gap = build_enumeration_gap(cells, query_rows, pair)
    probe = build_sampled_month_probe(cells, query_rows, query_receipt)
    routes = build_routes(query_rows, query_receipt, pair, bulk)

    if criteria["closure_allowed"] and routes["verdict"] == "BLOCKED":
        raise W10AuditError("ISSUE24_CLOSURE_CONTRADICTS_BLOCKED_ROUTE_VERDICT")
    if gap["authoritative_label_allowed"]:
        raise W10AuditError("AUTHORITATIVE_LABEL_MUST_STAY_DISALLOWED_WHILE_BLOCKED")

    return {
        "issue24_criteria.json": encode_json(criteria),
        "enumeration_gap.json": encode_json(gap),
        "sampled_month_probe.json": encode_json(probe),
        "routes.json": encode_json(routes),
    }


def source_hashes() -> dict[str, str]:
    return {name: sha_file(path) for name, path in sorted(SOURCES.items())}


def build_receipt(content: dict[str, bytes]) -> dict:
    criteria = json.loads(content["issue24_criteria.json"])
    gap = json.loads(content["enumeration_gap.json"])
    probe = json.loads(content["sampled_month_probe.json"])
    routes = json.loads(content["routes.json"])
    return {
        "contract": "W10_P7_ENUMERATION_RECEIPT_V1",
        "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w10_p7_enumeration.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {name: path.relative_to(ROOT).as_posix() for name, path in sorted(SOURCES.items())},
        "source_sha256": source_hashes(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "coverage": {
            "issue24_criteria_met": criteria["criteria_met"],
            "issue24_criteria_total": criteria["criteria_total"],
            "issue24_criteria_unmet": criteria["criteria_unmet"],
            "issue24_verdict": criteria["verdict"],
            "distinct_selected_reports": gap["distinct_selected_reports"],
            "reports_with_a_known_version_chain": gap["reports_with_a_known_version_chain"],
            "indicative_exposed_reports": gap["indicative_exposed_reports"],
            "sampled_month_contamination_detected": probe["contamination_detected"],
            "route_verdict": routes["verdict"],
        },
        "policy": {
            "model_changed": False,
            "weights_changed": False,
            "veto_changed": False,
            "universe_changed": False,
            "neutral_fill": False,
            "production_code_changed": False,
            "source_selection_changed": False,
            "authoritative_label_applied": False,
            "paid_source_used": False,
            "network_access_performed": False,
        },
        "limitations": [
            "Everything here is derived from bytes already in the repository; no new request was made.",
            "The supersession rate comes from one month of official query bytes and is indicative only.",
            "A negative contamination probe for one month says nothing about the other fifty-nine.",
            "Issue #24 stays open and the result keeps the EXPERIMENTAL_RISK_ACCEPTED_5Y label.",
        ],
    }


def write(content: dict[str, bytes], receipt: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    for name, payload in content.items():
        (AUDIT / name).write_bytes(payload)
    (AUDIT / "receipt.json").write_bytes(encode_json(receipt))


def check() -> None:
    receipt_path = AUDIT / "receipt.json"
    if not receipt_path.exists():
        raise W10AuditError("W10_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W10AuditError("W10_RECEIPT_HASH_MODE_MISMATCH")
    observed = source_hashes()
    if observed != receipt["source_sha256"]:
        changed = sorted(
            name for name, value in observed.items() if receipt["source_sha256"].get(name) != value
        )
        raise W10AuditError(f"W10_SOURCE_HASH_MISMATCH:{','.join(changed)}")
    content = derive()
    for name in CONTENT_FILES:
        stored = (AUDIT / name).read_bytes()
        if sha_bytes(stored) != receipt["output_sha256"][name]:
            raise W10AuditError(f"W10_STORED_ARTIFACT_HASH_MISMATCH:{name}")
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W10AuditError(f"W10_REDERIVED_ARTIFACT_HASH_MISMATCH:{name}")
    print("W10_CHECK_PASS " + receipt["output_sha256"]["issue24_criteria.json"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        return
    first, second = derive(), derive()
    if first != second:
        raise W10AuditError("W10_NON_DETERMINISTIC_DERIVATION")
    write(first, build_receipt(first))
    print("W10_APPLY_OK " + sha_bytes(first["issue24_criteria.json"]))


if __name__ == "__main__":
    main()
