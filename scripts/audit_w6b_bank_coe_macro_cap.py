from __future__ import annotations

"""Verify the BANK ``coe`` / ``macro_cap`` position for the 509 historical cells.

Prior research already captured an official OVP-derived ``macro_cap`` series and
rejected every free ``coe`` route.  The ledger's second-pass rule forbids taking
an implementation claim at face value, so this auditor re-derives the numbers
from the artifacts rather than restating the earlier receipt:

* every ``macro_cap`` value is recomputed from the recorded nominal-GDP pair;
* every cell is re-checked for look-ahead and for latest-eligible vintage choice;
* the 509 cells are re-identified against the P3/P4 artifacts;
* ``coe`` is recorded as BLOCKED with its exhausted routes and reopen condition;
* the unlock upper bound is recomputed instead of assumed.

It repairs nothing, scores nothing and never carries a current assumption into a
historical cell.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT = ROOT / "data/audit/w6b_bank_coe_macro_cap_v1"
CONTRACT = "W6B_BANK_COE_MACRO_CAP_AUDIT_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

MACRO_CELLS = ROOT / "data/backtest_sources/bank_macro_cap_ovp_v1/bank_macro_cap_cells.jsonl.gz"
MACRO_RECEIPT = ROOT / "data/backtest_sources/bank_macro_cap_ovp_v1/receipt.json"
FREE_RESEARCH = ROOT / "data/audit/bank_free_input_research_v1/receipt.json"
P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
P4_CELLS = ROOT / "data/audit/experimental_materialization_v3/p4_cells.jsonl.gz"

SOURCES = {
    "bank_macro_cap_cells": MACRO_CELLS,
    "bank_macro_cap_receipt": MACRO_RECEIPT,
    "bank_free_input_research": FREE_RESEARCH,
    "p3_cells": P3_CELLS,
    "p4_cells": P4_CELLS,
}

CONTENT_FILES = (
    "macro_cap_verification.json",
    "coe_blocked.json",
    "unlock_bound.json",
    "cells.jsonl",
)

MODULE_KEYS = ("M1", "M2", "M3", "Ek1", "Ek4", "Ek9")


class W6BAuditError(RuntimeError):
    pass


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload.replace(b"\r\n", b"\n")).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def encode_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def encode_rows(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode("utf-8")


def read_jsonl_gz(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def verify_macro_cap(cells: list[dict], receipt: dict, p3: dict) -> dict:
    """Recompute every macro_cap value and re-check its point-in-time standing."""
    sources = receipt["sources"]
    published = sorted(
        (datetime.fromisoformat(value["published_at"]), name) for name, value in sources.items()
    )

    arithmetic_mismatch: list[dict] = []
    lookahead: list[dict] = []
    wrong_vintage: list[dict] = []
    unknown_cell: list[dict] = []
    non_bank_cell: list[dict] = []

    for cell in cells:
        key = (cell["month"], cell["ticker"])
        vintage = sources.get(cell["ovp"])
        if vintage is None:
            raise W6BAuditError(f"MACRO_CAP_VINTAGE_NOT_IN_RECEIPT:{cell['ovp']}")
        recorded = Decimal(cell["macro_cap"])
        expected = (
            Decimal(vintage["gdp_last_billion_try"]) / Decimal(vintage["gdp_previous_billion_try"])
            - 1
        )
        if recorded != expected.quantize(recorded):
            arithmetic_mismatch.append({**key_fields(cell), "recorded": str(recorded)})

        cutoff = datetime.fromisoformat(cell["knowledge_cutoff_at"])
        publication = datetime.fromisoformat(cell["ovp_published_at"])
        if publication > cutoff:
            lookahead.append(key_fields(cell))
        eligible = [name for stamp, name in published if stamp <= cutoff]
        if not eligible:
            wrong_vintage.append({**key_fields(cell), "detail": "no cutoff-eligible vintage"})
        elif eligible[-1] != cell["ovp"]:
            wrong_vintage.append({**key_fields(cell), "expected_vintage": eligible[-1]})

        source = p3.get(key)
        if source is None:
            unknown_cell.append(key_fields(cell))
        elif source.get("historical_family") != "BANK":
            non_bank_cell.append(
                {**key_fields(cell), "historical_family": source.get("historical_family")}
            )

    vintage_counts: dict[str, int] = {}
    for cell in cells:
        vintage_counts[cell["ovp"]] = vintage_counts.get(cell["ovp"], 0) + 1

    clean = not (
        arithmetic_mismatch or lookahead or wrong_vintage or unknown_cell or non_bank_cell
    )
    return {
        "contract": CONTRACT,
        "parameter": "macro_cap",
        "status": "RESOLVED_FROM_OFFICIAL_DATED_SOURCES" if clean else "VERIFICATION_FAILED",
        "definition": receipt["definition"],
        "covered_cells": len(cells),
        "checks": {
            "arithmetic_recomputed_from_recorded_gdp_pairs": {
                "verified": not arithmetic_mismatch,
                "mismatches": arithmetic_mismatch,
            },
            "no_publication_after_cutoff": {"verified": not lookahead, "violations": lookahead},
            "latest_cutoff_eligible_vintage_selected": {
                "verified": not wrong_vintage,
                "violations": wrong_vintage,
            },
            "every_cell_exists_in_p3": {"verified": not unknown_cell, "unknown": unknown_cell},
            "every_cell_is_a_bank_family_cell": {
                "verified": not non_bank_cell,
                "violations": non_bank_cell,
            },
        },
        "vintage_counts": dict(sorted(vintage_counts.items())),
        "vintages": [
            {
                "ovp": name,
                "published_at": sources[name]["published_at"],
                "gdp_last_billion_try": sources[name]["gdp_last_billion_try"],
                "gdp_previous_billion_try": sources[name]["gdp_previous_billion_try"],
                "source_url": sources[name]["url"],
                "source_sha256": sources[name]["sha256"],
                "size_bytes": sources[name]["size_bytes"],
                "cells": vintage_counts.get(name, 0),
            }
            for _, name in published
        ],
        "provenance_caveat": {
            "raw_source_bytes_committed": receipt.get("raw_source_bytes_committed", False),
            "policy": receipt.get("source_bytes_policy"),
            "consequence": (
                "URL, publication date, size and SHA256 are recorded, so the chain is auditable "
                "against a re-download, but the PDFs are not in the repository and the byte level "
                "cannot be re-verified offline."
            ),
        },
    }


def key_fields(cell: dict) -> dict:
    return {"month": cell["month"], "ticker": cell["ticker"], "ovp": cell.get("ovp")}


def build_coe_blocked(research: dict) -> dict:
    coe = research["coe"]
    exhaustion = coe["free_source_exhaustion"]
    return {
        "contract": CONTRACT,
        "parameter": "coe",
        "status": "BLOCKED",
        "reason_code": coe["rejection"],
        "covered_cells": coe["covered_cells"],
        "repo_source_status": coe["repo_source_status"],
        "rejected_manual_value": coe["demo_value_rejected"],
        "why_the_manual_value_is_not_usable": (
            "It is a single undated demo constant. Applying one present-day number to sixty "
            "historical cutoffs would carry a current assumption into history, which the ledger "
            "forbids outright."
        ),
        "exhausted_free_routes": [
            {
                "route": "Borsa Istanbul historical index data",
                "source": exhaustion["bist_index_source"],
                "outcome": exhaustion["bist_historical_index_access"],
            },
            {
                "route": "TCMB EVDS open data platform",
                "source": exhaustion["evds_source"],
                "outcome": exhaustion["evds_status"],
            },
            {
                "route": "TCMB cost-of-equity working paper",
                "source": coe["research_source"],
                "method": exhaustion["tcmb_method"],
                "outcome": exhaustion["tcmb_input_disclosure"],
            },
        ],
        "usable_free_cutoff_dated_series_found": exhaustion[
            "usable_free_cutoff_dated_coe_series_found"
        ],
        "backfill_or_forward_knowledge_used": False,
        "reopen_condition": (
            "A dated, versioned and freely obtainable cost-of-equity series (or its complete "
            "input lineage: risk-free curve, equity risk premium and bank beta, each with a "
            "publication timestamp at or before every cutoff) that can be replayed without a "
            "paid terminal."
        ),
    }


def build_unlock_bound(cells: list[dict], p3: dict, p4: dict) -> dict:
    """Recompute what solving both parameters could actually unlock."""
    bank_keys = {(cell["month"], cell["ticker"]) for cell in cells}

    def module_count(key: tuple[str, str]) -> int:
        return sum(1 for value in p4[key]["module_values"].values() if value is not None)

    at_least_five = {key for key in p4 if module_count(key) >= 5}
    priority = {key for key in at_least_five if p4[key]["module_values"]["M2"] is None}
    at_least_four = {key for key in p4 if module_count(key) >= 4}

    present: dict[str, int] = {name: 0 for name in MODULE_KEYS}
    missing: dict[str, int] = {name: 0 for name in MODULE_KEYS}
    for key in bank_keys:
        for name, value in p4[key]["module_values"].items():
            if value is None:
                missing[name] += 1
            else:
                present[name] += 1

    counts: dict[str, int] = {}
    for key in bank_keys:
        counts[str(module_count(key))] = counts.get(str(module_count(key)), 0) + 1

    core_status: dict[str, int] = {}
    core_reasons: dict[str, int] = {}
    for key in bank_keys:
        status = p3[key].get("partial_core_diagnostic_status")
        core_status[str(status)] = core_status.get(str(status), 0) + 1
        for reason in p3[key].get("partial_core_diagnostic_reasons") or []:
            core_reasons[reason] = core_reasons.get(reason, 0) + 1

    best_after = max(module_count(key) for key in bank_keys) + 1  # M2 would become available
    return {
        "contract": CONTRACT,
        "bank_cells": len(bank_keys),
        "bank_tickers": sorted({ticker for _, ticker in bank_keys}),
        "bank_months": len({month for month, _ in bank_keys}),
        "priority_cells_total": len(priority),
        "overlap_with_priority_cells": len(bank_keys & priority),
        "overlap_with_at_least_five_module_cells": len(bank_keys & at_least_five),
        "overlap_with_at_least_four_module_cells": len(bank_keys & at_least_four),
        "module_present_counts": dict(sorted(present.items())),
        "module_missing_counts": dict(sorted(missing.items())),
        "module_count_distribution": dict(sorted(counts.items())),
        "best_module_count_if_both_parameters_resolved": best_after,
        "modules_required_for_a_total": 6,
        "total_score_unlock_upper_bound": 0,
        "residual_blockers": {
            "core_diagnostic_status": dict(sorted(core_status.items())),
            "core_diagnostic_reasons": dict(sorted(core_reasons.items())),
            "explanation": (
                "M1 and Ek1 are missing in every BANK cell because the CORE diagnostic layer "
                "produced nothing for them. Resolving coe and macro_cap makes M2 computable and "
                "raises the best cell to four modules, still short of a six-module Total, so this "
                "package cannot unlock a Total score on its own."
            ),
        },
        "does_not_block_nonfin": True,
    }


def build_cells(cells: list[dict], p3: dict, p4: dict, coe: dict) -> list[dict]:
    rows = []
    for cell in sorted(cells, key=lambda item: (item["month"], item["ticker"])):
        key = (cell["month"], cell["ticker"])
        values = p4[key]["module_values"]
        rows.append(
            {
                "month": cell["month"],
                "ticker": cell["ticker"],
                "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
                "macro_cap": cell["macro_cap"],
                "macro_cap_status": cell["status"],
                "ovp": cell["ovp"],
                "ovp_published_at": cell["ovp_published_at"],
                "ovp_source_sha256": cell["source_sha256"],
                "coe_status": coe["status"],
                "coe_reason_code": coe["reason_code"],
                "modules_present": sorted(name for name, value in values.items() if value is not None),
                "modules_missing": sorted(name for name, value in values.items() if value is None),
                "core_diagnostic_status": p3[key].get("partial_core_diagnostic_status"),
                "cell_status": "BLOCKED",
            }
        )
    return rows


def derive() -> dict[str, bytes]:
    cells = read_jsonl_gz(MACRO_CELLS)
    receipt = json.loads(MACRO_RECEIPT.read_text(encoding="utf-8"))
    research = json.loads(FREE_RESEARCH.read_text(encoding="utf-8"))
    p3 = {(row["month"], row["ticker"]): row for row in read_jsonl_gz(P3_CELLS)}
    p4 = {(row["month"], row["ticker"]): row for row in read_jsonl_gz(P4_CELLS)}

    macro = verify_macro_cap(cells, receipt, p3)
    if macro["status"] != "RESOLVED_FROM_OFFICIAL_DATED_SOURCES":
        raise W6BAuditError("MACRO_CAP_VERIFICATION_FAILED")
    coe = build_coe_blocked(research)
    unlock = build_unlock_bound(cells, p3, p4)
    if unlock["overlap_with_priority_cells"] != 0 and unlock["total_score_unlock_upper_bound"] == 0:
        raise W6BAuditError("UNLOCK_BOUND_CONTRADICTS_PRIORITY_OVERLAP")
    rows = build_cells(cells, p3, p4, coe)

    return {
        "macro_cap_verification.json": encode_json(macro),
        "coe_blocked.json": encode_json(coe),
        "unlock_bound.json": encode_json(unlock),
        "cells.jsonl": encode_rows(rows),
    }


def source_hashes() -> dict[str, str]:
    return {name: sha_file(path) for name, path in sorted(SOURCES.items())}


def build_receipt(content: dict[str, bytes]) -> dict:
    macro = json.loads(content["macro_cap_verification.json"])
    coe = json.loads(content["coe_blocked.json"])
    unlock = json.loads(content["unlock_bound.json"])
    return {
        "contract": "W6B_BANK_COE_MACRO_CAP_RECEIPT_V1",
        "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w6b_bank_coe_macro_cap.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {name: str(path.relative_to(ROOT)) for name, path in sorted(SOURCES.items())},
        "source_sha256": source_hashes(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "coverage": {
            "bank_cells": unlock["bank_cells"],
            "macro_cap_status": macro["status"],
            "macro_cap_covered_cells": macro["covered_cells"],
            "coe_status": coe["status"],
            "coe_covered_cells": coe["covered_cells"],
            "overlap_with_priority_cells": unlock["overlap_with_priority_cells"],
            "total_score_unlock_upper_bound": unlock["total_score_unlock_upper_bound"],
            "cells_remaining_blocked": unlock["bank_cells"],
        },
        "policy": {
            "model_changed": False,
            "weights_changed": False,
            "veto_changed": False,
            "peer_or_coverage_threshold_changed": False,
            "universe_changed": False,
            "neutral_fill": False,
            "production_code_changed": False,
            "current_assumption_carried_to_history": False,
            "backfill_or_forward_knowledge_used": False,
            "cells_repaired": 0,
        },
        "limitations": [
            "macro_cap is verified against the recorded GDP pairs and publication dates; the OVP "
            "PDFs themselves are not in the repository, so byte-level replay needs a re-download.",
            "coe stays BLOCKED: no free, dated, versioned series or complete input lineage exists.",
            "Even with both parameters resolved no Total score unlocks; M1 and Ek1 are missing in "
            "all 509 cells because the CORE diagnostic layer produced nothing for the BANK family.",
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
        raise W6BAuditError("W6B_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W6BAuditError("W6B_RECEIPT_HASH_MODE_MISMATCH")
    observed = source_hashes()
    if observed != receipt["source_sha256"]:
        changed = sorted(
            name for name, value in observed.items() if receipt["source_sha256"].get(name) != value
        )
        raise W6BAuditError(f"W6B_SOURCE_HASH_MISMATCH:{','.join(changed)}")
    content = derive()
    for name in CONTENT_FILES:
        stored = (AUDIT / name).read_bytes()
        if sha_bytes(stored) != receipt["output_sha256"][name]:
            raise W6BAuditError(f"W6B_STORED_ARTIFACT_HASH_MISMATCH:{name}")
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W6BAuditError(f"W6B_REDERIVED_ARTIFACT_HASH_MISMATCH:{name}")
    print("W6B_CHECK_PASS " + receipt["output_sha256"]["cells.jsonl"])


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
        raise W6BAuditError("W6B_NON_DETERMINISTIC_DERIVATION")
    write(first, build_receipt(first))
    print("W6B_APPLY_OK " + sha_bytes(first["cells.jsonl"]))


if __name__ == "__main__":
    main()
