from __future__ import annotations

"""Reconcile the three historical price-gap populations under separate keys.

The execution ledger records 163 ``PRICE_MISSING``, 174
``EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING`` and 402
``STOCK_WINDOW_PRICE_MISSING`` cells and forbids using them interchangeably or
adding them together before an intersection report exists.  This auditor
rebuilds each population from its own producer artifact under its own key,
publishes the intersection/difference structure, and assigns every execution
cell exactly one reason code from the ledger's closed taxonomy.

The auditor is read-only with respect to production code and existing
artifacts.  It creates no score, repairs no cell and relaxes no threshold: a
cell that cannot be explained from repo-internal or free official evidence
stays an explicit rejection.
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

AUDIT = ROOT / "data/audit/w3_price_populations_v1"
CONTRACT = "W3_PRICE_POPULATION_RECONCILIATION_V1"
HASH_MODE = "LF_CANONICAL_SHA256_V1"

P3_CELLS = ROOT / "data/audit/experimental_materialization_v3/p3_cells.jsonl.gz"
P4_CELLS = ROOT / "data/audit/experimental_materialization_v3/p4_cells.jsonl.gz"
READINESS = ROOT / "data/audit/experimental_readiness_v1/readiness_report.json"
RESOLVED_PRICES = (
    ROOT / "data/backtest_sources/yahoo_resolved"
    "/historical_member_prices_resolved_2020-07_2026-08.csv.gz"
)
LINEAGE = ROOT / "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv"
SIGNAL_DATES = ROOT / "data/backtest_sources/xu100_signal_dates_yahoo_2021-08_2026-07.csv"
ACTIONS = (
    ROOT / "data/backtest_sources/yahoo_discovery"
    "/historical_member_actions_yahoo_2020-07_2026-08.csv"
)
SHARE_CLASSES = (
    ROOT / "data/backtest_sources/kap_share_class_history_v1"
    "/share_class_observations.jsonl.gz"
)

SOURCES = {
    "p3_cells": P3_CELLS,
    "p4_cells": P4_CELLS,
    "readiness_report": READINESS,
    "resolved_prices": RESOLVED_PRICES,
    "bist_ticker_code_changes": LINEAGE,
    "xu100_signal_dates": SIGNAL_DATES,
    "yahoo_member_actions": ACTIONS,
    "kap_share_class_observations": SHARE_CLASSES,
}

CONTENT_FILES = (
    "populations.json",
    "intersections.json",
    "execution_reason_codes.json",
    "lineage_admissibility.json",
    "rows.jsonl",
)

# Closed taxonomy from ACTIVE_EXECUTION_LEDGER.md section W3.  Every execution
# cell is assigned exactly one of these; no cell is dropped silently.
EXECUTION_REASON_CODES = (
    "TICKER_LINEAGE",
    "NO_TRADING_SESSION_OR_NO_TRADE",
    "SOURCE_SYMBOL_GAP",
    "CALENDAR_OR_SESSION_GAP",
    "INGESTION_OR_PARSER_GAP",
    "OTHER_EVIDENCED",
    "UNRESOLVED_BLOCKED",
)

ALIAS_RESOLUTION = "BORSA_LINEAGE_YAHOO_ALIAS"


class W3AuditError(RuntimeError):
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


def read_csv(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
    header, *lines = text.split("\n")
    columns = header.split(",")
    return [dict(zip(columns, line.split(","))) for line in lines]


def read_csv_gz(path: Path, keep: tuple[str, ...]) -> list[dict]:
    text = gzip.decompress(path.read_bytes()).decode("utf-8").replace("\r\n", "\n").strip("\n")
    header, *lines = text.split("\n")
    columns = header.split(",")
    index = {name: columns.index(name) for name in keep}
    out = []
    for line in lines:
        parts = line.split(",")
        out.append({name: parts[position] for name, position in index.items()})
    return out


def git_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "UNKNOWN"


def build_populations(p3: list[dict], p4: list[dict], readiness: dict) -> dict:
    """Rebuild each population from its own producer under its own key."""
    price_missing = sorted(
        (row["signal_date"], row["ticker"]) for row in p4 if "PRICE_MISSING" in row["reasons"]
    )
    execution_missing = sorted(
        (cell["signal_date"], cell["ticker"]) for cell in readiness["missing_execution_cells"]
    )
    # The flat p4 reason set collapses three different analysis windows into one
    # label.  The window owner is only visible in the p3 module_reasons map.
    per_module: dict[str, list[tuple[str, str]]] = {}
    for row in p3:
        for module, reason in (row.get("module_reasons") or {}).items():
            if reason and "STOCK_WINDOW_PRICE_MISSING" in reason:
                per_module.setdefault(module, []).append((row["signal_date"], row["ticker"]))
    stock_window = sorted({key for keys in per_module.values() for key in keys})
    flat_stock_window = sorted(
        (row["signal_date"], row["ticker"])
        for row in p4
        if "STOCK_WINDOW_PRICE_MISSING" in row["reasons"]
    )
    if stock_window != flat_stock_window:
        raise W3AuditError("STOCK_WINDOW_MODULE_UNION_DOES_NOT_MATCH_P4_FLAT_SET")

    def ticker_counts(keys: list[tuple[str, str]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, ticker in keys:
            counts[ticker] = counts.get(ticker, 0) + 1
        return dict(sorted(counts.items()))

    return {
        "contract": CONTRACT,
        "populations": {
            "PRICE_MISSING": {
                "count": len(price_missing),
                "key": "ticker + cutoff/month",
                "producer": "scripts/materialize_experimental_p3_p4.py::PRICE_MISSING",
                "meaning": "no pre-cutoff bounded price row for the valuation basis",
                "gate": "the M2 cell of that month",
                "ticker_counts": ticker_counts(price_missing),
                "month_count": len({date[:7] for date, _ in price_missing}),
            },
            "EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING": {
                "count": len(execution_missing),
                "key": "ticker + signal/execution date",
                "producer": "scripts/audit_experimental_readiness.py::run_audit",
                "meaning": "no signal-day price row whose price_source_ticker equals the member ticker",
                "gate": "selected trade execution and full V24-G",
                "ticker_counts": ticker_counts(execution_missing),
                "month_count": len({date[:7] for date, _ in execution_missing}),
            },
            "STOCK_WINDOW_PRICE_MISSING": {
                "count": len(stock_window),
                "key": "ticker + analysis window",
                "producer": "src/analytics/historical_pit_{ek9,ek4,m3}_replay.py",
                "meaning": "the module lookback window has too few stock price observations",
                "gate": "historical Ek9 (and the Ek4/M3 windows nested inside it)",
                "ticker_counts": ticker_counts(stock_window),
                "month_count": len({date[:7] for date, _ in stock_window}),
                "per_module_counts": {
                    module: len(keys) for module, keys in sorted(per_module.items())
                },
                "per_module_containment": {
                    module: {
                        "subset_of_Ek9": set(keys) <= set(per_module.get("Ek9", [])),
                        "count": len(keys),
                    }
                    for module, keys in sorted(per_module.items())
                },
            },
        },
    }


def build_intersections(populations_keys: dict[str, list[tuple[str, str]]]) -> dict:
    price_missing = set(populations_keys["PRICE_MISSING"])
    execution = set(populations_keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"])
    stock_window = set(populations_keys["STOCK_WINDOW_PRICE_MISSING"])
    union = price_missing | execution | stock_window
    naive_sum = len(price_missing) + len(execution) + len(stock_window)

    def keys(values: set[tuple[str, str]]) -> list[list[str]]:
        return [[date, ticker] for date, ticker in sorted(values)]

    return {
        "contract": CONTRACT,
        "join_key": "signal_date + ticker (the common cell identity of all three producers)",
        "note": (
            "The three populations answer different questions and keep different native keys. "
            "This report exists so they are never summed or substituted for one another."
        ),
        "sizes": {
            "PRICE_MISSING": len(price_missing),
            "EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING": len(execution),
            "STOCK_WINDOW_PRICE_MISSING": len(stock_window),
            "union": len(union),
            "naive_sum": naive_sum,
            "double_counted_by_naive_sum": naive_sum - len(union),
        },
        "pairwise_intersections": {
            "PRICE_MISSING&EXECUTION": len(price_missing & execution),
            "PRICE_MISSING&STOCK_WINDOW": len(price_missing & stock_window),
            "EXECUTION&STOCK_WINDOW": len(execution & stock_window),
            "all_three": len(price_missing & execution & stock_window),
        },
        "containment": {
            "PRICE_MISSING_subset_of_STOCK_WINDOW": price_missing <= stock_window,
            "EXECUTION_subset_of_STOCK_WINDOW": execution <= stock_window,
            "PRICE_MISSING_subset_of_EXECUTION": price_missing <= execution,
            "union_equals_STOCK_WINDOW": union == stock_window,
        },
        "differences": {
            "PRICE_MISSING_not_EXECUTION": keys(price_missing - execution),
            "EXECUTION_not_PRICE_MISSING_count": len(execution - price_missing),
            "STOCK_WINDOW_not_EXECUTION_count": len(stock_window - execution),
            "EXECUTION_not_STOCK_WINDOW": keys(execution - stock_window),
        },
    }


def classify_execution_cells(
    execution_keys: list[tuple[str, str]],
    price_rows: list[dict],
    lineage_rows: list[dict],
    signal_dates: set[str],
) -> tuple[list[dict], dict]:
    """Assign exactly one closed-taxonomy reason code to every execution cell."""
    by_cell: dict[tuple[str, str], dict] = {}
    series_bounds: dict[str, dict] = {}
    for row in price_rows:
        ticker, trade_date = row["ticker"], row["trade_date"]
        by_cell[(trade_date, ticker)] = row
        bounds = series_bounds.setdefault(
            ticker, {"first": trade_date, "last": trade_date, "rows": 0}
        )
        bounds["rows"] += 1
        if trade_date < bounds["first"]:
            bounds["first"] = trade_date
        if trade_date > bounds["last"]:
            bounds["last"] = trade_date

    lineage_by_old = {row["old_ticker"]: row for row in lineage_rows}

    assignments: list[dict] = []
    for signal_date, ticker in execution_keys:
        row = by_cell.get((signal_date, ticker))
        bounds = series_bounds.get(ticker)
        if row is not None and row["price_resolution"] == ALIAS_RESOLUTION:
            lineage = lineage_by_old.get(ticker)
            if lineage is None:
                reason, evidence = "UNRESOLVED_BLOCKED", {
                    "detail": "alias-resolved price row without an official lineage row"
                }
            else:
                reason = "TICKER_LINEAGE"
                evidence = {
                    "price_source_ticker": row["price_source_ticker"],
                    "yahoo_symbol": row["yahoo_symbol"],
                    "alias_effective_date": row["alias_effective_date"],
                    "lineage_effective_date": lineage["effective_date"],
                    "lineage_new_ticker": lineage["new_ticker"],
                    "source_workbook_sha256": lineage["source_workbook_sha256"],
                    "event_sha256": lineage["event_sha256"],
                    "price_row_present_under_alias": True,
                }
        elif row is not None:
            reason, evidence = "OTHER_EVIDENCED", {
                "detail": "exact-ticker price row exists; producer gate must be re-examined",
                "price_resolution": row["price_resolution"],
            }
        elif signal_date not in signal_dates:
            reason, evidence = "CALENDAR_OR_SESSION_GAP", {
                "detail": "signal date is absent from the XU100 signal calendar"
            }
        elif bounds is None:
            reason, evidence = "SOURCE_SYMBOL_GAP", {
                "detail": "the resolved price source contains no series for this ticker"
            }
        elif signal_date < bounds["first"] or signal_date > bounds["last"]:
            reason, evidence = "SOURCE_SYMBOL_GAP", {
                "detail": "the resolved series does not cover the signal date",
                "series_first_trade_date": bounds["first"],
                "series_last_trade_date": bounds["last"],
                "series_rows": bounds["rows"],
            }
        else:
            reason, evidence = "NO_TRADING_SESSION_OR_NO_TRADE", {
                "detail": "the series covers the date range but has no row on the signal date",
                "series_first_trade_date": bounds["first"],
                "series_last_trade_date": bounds["last"],
            }
        assignments.append(
            {
                "signal_date": signal_date,
                "ticker": ticker,
                "reason_code": reason,
                "evidence": evidence,
            }
        )

    counts: dict[str, int] = {code: 0 for code in EXECUTION_REASON_CODES}
    for item in assignments:
        counts[item["reason_code"]] += 1
    per_ticker: dict[str, dict[str, int]] = {}
    for item in assignments:
        bucket = per_ticker.setdefault(item["ticker"], {})
        bucket[item["reason_code"]] = bucket.get(item["reason_code"], 0) + 1

    summary = {
        "contract": CONTRACT,
        "taxonomy": list(EXECUTION_REASON_CODES),
        "assigned_cells": len(assignments),
        "exhaustive": len(assignments) == len(execution_keys),
        "single_reason_per_cell": True,
        "counts": counts,
        "per_ticker": {ticker: dict(sorted(v.items())) for ticker, v in sorted(per_ticker.items())},
    }
    return assignments, summary


def validate_execution_assignments(
    execution_keys: list[tuple[str, str]], assignments: list[dict]
) -> None:
    """Fail closed unless every execution cell carries one evidenced, known reason.

    ``len(assignments) == len(keys)`` is not exhaustiveness: a duplicated cell
    would hide a dropped one, and an unknown or evidence-free code would let a
    cell leave the closed taxonomy silently.
    """
    expected = set(execution_keys)
    if len(expected) != len(execution_keys):
        raise W3AuditError("EXECUTION_INPUT_KEYS_NOT_UNIQUE")
    seen: set[tuple[str, str]] = set()
    for item in assignments:
        key = (item["signal_date"], item["ticker"])
        if key in seen:
            raise W3AuditError(f"EXECUTION_CELL_ASSIGNED_TWICE:{key[1]}@{key[0]}")
        seen.add(key)
        if item["reason_code"] not in EXECUTION_REASON_CODES:
            raise W3AuditError(f"EXECUTION_REASON_CODE_OUTSIDE_TAXONOMY:{item['reason_code']}")
        if not item.get("evidence"):
            raise W3AuditError(f"EXECUTION_REASON_WITHOUT_EVIDENCE:{key[1]}@{key[0]}")
    if seen != expected:
        missing = sorted(expected - seen)
        extra = sorted(seen - expected)
        raise W3AuditError(
            f"EXECUTION_REASON_CODE_ASSIGNMENT_NOT_EXHAUSTIVE:missing={len(missing)},extra={len(extra)}"
        )


def build_lineage_admissibility(
    assignments: list[dict],
    lineage_rows: list[dict],
    action_rows: list[dict],
    share_class_rows: list[dict],
) -> dict:
    """Score each alias against the four admissibility conditions in the ledger.

    The ledger allows a lineage mapping only when company/share-class identity,
    the event date, corporate-action continuity and the source symbol are all
    verified.  A ticker change on its own is never permission to carry a price
    across the boundary.
    """
    actions_by_ticker: dict[str, int] = {}
    for row in action_rows:
        actions_by_ticker[row["ticker"]] = actions_by_ticker.get(row["ticker"], 0) + 1
    share_classes_by_ticker: dict[str, int] = {}
    for row in share_class_rows:
        ticker = row.get("ticker")
        if ticker:
            share_classes_by_ticker[ticker] = share_classes_by_ticker.get(ticker, 0) + 1

    lineage_by_old = {row["old_ticker"]: row for row in lineage_rows}
    affected: dict[str, int] = {}
    for item in assignments:
        if item["reason_code"] == "TICKER_LINEAGE":
            affected[item["ticker"]] = affected.get(item["ticker"], 0) + 1

    aliases = []
    for old_ticker in sorted(affected):
        lineage = lineage_by_old[old_ticker]
        new_ticker = lineage["new_ticker"]
        identity_rows = share_classes_by_ticker.get(new_ticker, 0)
        old_actions = actions_by_ticker.get(old_ticker, 0)
        new_actions = actions_by_ticker.get(new_ticker, 0)
        conditions = {
            "same_company_or_share_class_identity": {
                "verified": identity_rows > 0,
                "evidence": "kap_share_class_history_v1 observations under the successor code",
                "successor_share_class_observations": identity_rows,
                "predecessor_share_class_observations": share_classes_by_ticker.get(old_ticker, 0),
            },
            "event_date": {
                "verified": True,
                "evidence": "official Borsa Istanbul ticker-change workbook row",
                "effective_date": lineage["effective_date"],
                "source_workbook_sha256": lineage["source_workbook_sha256"],
                "event_sha256": lineage["event_sha256"],
            },
            "corporate_action_continuity": {
                # An empty action inventory is not proof that no action occurred.
                "verified": False,
                "evidence": "yahoo_member_actions inventory is not continuous across the boundary",
                "predecessor_action_rows": old_actions,
                "successor_action_rows": new_actions,
                "blocker": "ACTION_CONTINUITY_UNPROVEN",
            },
            "source_symbol": {
                "verified": True,
                "evidence": "historical_price_aliases fail-closed resolver bound to the lineage row",
                "successor_symbol": new_ticker,
            },
        }
        verified = [name for name, value in conditions.items() if value["verified"]]
        aliases.append(
            {
                "old_ticker": old_ticker,
                "new_ticker": new_ticker,
                "effective_date": lineage["effective_date"],
                "affected_execution_cells": affected[old_ticker],
                "conditions": conditions,
                "conditions_verified": sorted(verified),
                "conditions_unverified": sorted(set(conditions) - set(verified)),
                "admissible_as_exact_execution_price": len(verified) == len(conditions),
                "status": "BLOCKED",
                "reopen_condition": (
                    "a dated, source-hashed corporate-action inventory that is continuous across "
                    f"{old_ticker}->{new_ticker} on {lineage['effective_date']}"
                ),
            }
        )

    return {
        "contract": CONTRACT,
        "rule": (
            "A ticker change is not permission to carry a price. All four conditions must be "
            "verified before an alias-resolved row may serve as an exact historical execution price."
        ),
        "koza_cluster_note": (
            "KERVT is a separate 2025-06-02 KERVT->BESLR event and is never folded into the "
            "2025-11-24 Koza cluster. EFORC->EFOR on 2025-11-03 is likewise its own event."
        ),
        "aliases": aliases,
        "admissible_alias_count": sum(1 for a in aliases if a["admissible_as_exact_execution_price"]),
        "blocked_execution_cells": sum(
            a["affected_execution_cells"]
            for a in aliases
            if not a["admissible_as_exact_execution_price"]
        ),
    }


def build_rows(
    populations_keys: dict[str, list[tuple[str, str]]], assignments: list[dict]
) -> list[dict]:
    price_missing = set(populations_keys["PRICE_MISSING"])
    execution = set(populations_keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"])
    stock_window = set(populations_keys["STOCK_WINDOW_PRICE_MISSING"])
    reason_by_cell = {(a["signal_date"], a["ticker"]): a["reason_code"] for a in assignments}
    rows = []
    for signal_date, ticker in sorted(price_missing | execution | stock_window):
        key = (signal_date, ticker)
        rows.append(
            {
                "signal_date": signal_date,
                "ticker": ticker,
                "month": signal_date[:7],
                "in_price_missing": key in price_missing,
                "in_execution_missing": key in execution,
                "in_stock_window_missing": key in stock_window,
                "execution_reason_code": reason_by_cell.get(key),
            }
        )
    return rows


def derive() -> dict[str, bytes]:
    p3 = read_jsonl_gz(P3_CELLS)
    p4 = read_jsonl_gz(P4_CELLS)
    readiness = json.loads(READINESS.read_text(encoding="utf-8"))
    price_rows = read_csv_gz(
        RESOLVED_PRICES,
        ("ticker", "yahoo_symbol", "trade_date", "price_source_ticker", "price_resolution",
         "alias_effective_date"),
    )
    lineage_rows = read_csv(LINEAGE)
    signal_rows = read_csv(SIGNAL_DATES)
    signal_column = "signal_date" if "signal_date" in signal_rows[0] else next(iter(signal_rows[0]))
    signal_dates = {row[signal_column] for row in signal_rows}
    action_rows = read_csv(ACTIONS)
    share_class_rows = read_jsonl_gz(SHARE_CLASSES)

    populations = build_populations(p3, p4, readiness)
    populations_keys = {
        "PRICE_MISSING": sorted(
            (row["signal_date"], row["ticker"]) for row in p4 if "PRICE_MISSING" in row["reasons"]
        ),
        "EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING": sorted(
            (cell["signal_date"], cell["ticker"]) for cell in readiness["missing_execution_cells"]
        ),
        "STOCK_WINDOW_PRICE_MISSING": sorted(
            (row["signal_date"], row["ticker"])
            for row in p4
            if "STOCK_WINDOW_PRICE_MISSING" in row["reasons"]
        ),
    }
    intersections = build_intersections(populations_keys)
    assignments, execution_summary = classify_execution_cells(
        populations_keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"],
        price_rows,
        lineage_rows,
        signal_dates,
    )
    validate_execution_assignments(
        populations_keys["EXACT_HISTORICAL_TICKER_EXECUTION_PRICE_MISSING"], assignments
    )
    execution_summary["cells"] = assignments
    admissibility = build_lineage_admissibility(
        assignments, lineage_rows, action_rows, share_class_rows
    )
    rows = build_rows(populations_keys, assignments)

    return {
        "populations.json": encode_json(populations),
        "intersections.json": encode_json(intersections),
        "execution_reason_codes.json": encode_json(execution_summary),
        "lineage_admissibility.json": encode_json(admissibility),
        "rows.jsonl": encode_rows(rows),
    }


def source_hashes() -> dict[str, str]:
    return {
        name: sha_file(path)
        for name, path in sorted(SOURCES.items())
    }


def build_receipt(content: dict[str, bytes]) -> dict:
    populations = json.loads(content["populations.json"])["populations"]
    intersections = json.loads(content["intersections.json"])
    execution = json.loads(content["execution_reason_codes.json"])
    admissibility = json.loads(content["lineage_admissibility.json"])
    return {
        "contract": "W3_PRICE_POPULATION_RECEIPT_V1",
        "profile": "EXPERIMENTAL_RISK_ACCEPTED_5Y",
        "hash_mode": HASH_MODE,
        "command": "python scripts/audit_w3_price_populations.py --apply",
        "commit": git_head(),
        "generator_sha256": sha_file(Path(__file__)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_paths": {
            name: str(path.relative_to(ROOT)) for name, path in sorted(SOURCES.items())
        },
        "source_sha256": source_hashes(),
        "output_sha256": {name: sha_bytes(payload) for name, payload in sorted(content.items())},
        # Bound separately: the mutation suite runs against this generator, so it
        # is produced before the receipt rather than by it.
        "mutations_sha256": (
            sha_file(AUDIT / "mutations.json") if (AUDIT / "mutations.json").exists() else None
        ),
        "coverage": {
            "population_counts": {
                name: value["count"] for name, value in sorted(populations.items())
            },
            "union_cells": intersections["sizes"]["union"],
            "double_counted_by_naive_sum": intersections["sizes"]["double_counted_by_naive_sum"],
            "execution_cells_classified": execution["assigned_cells"],
            "execution_reason_counts": execution["counts"],
            "blocked_execution_cells": admissibility["blocked_execution_cells"],
            "admissible_alias_count": admissibility["admissible_alias_count"],
        },
        "policy": {
            "model_changed": False,
            "weights_changed": False,
            "veto_changed": False,
            "peer_or_coverage_threshold_changed": False,
            "universe_changed": False,
            "neutral_fill": False,
            "production_code_changed": False,
            "cells_repaired": 0,
            "new_market_capture": False,
        },
        "limitations": [
            "This is a reconciliation audit. It resolves no cell and produces no score.",
            "Alias-resolved price rows stay inadmissible as exact historical execution prices "
            "until corporate-action continuity is proven across the lineage boundary.",
            "An empty corporate-action inventory is recorded as unproven continuity, never as "
            "evidence that no action occurred.",
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
        raise W3AuditError("W3_RECEIPT_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("hash_mode") != HASH_MODE:
        raise W3AuditError("W3_RECEIPT_HASH_MODE_MISMATCH")
    observed_sources = source_hashes()
    if observed_sources != receipt["source_sha256"]:
        changed = sorted(
            name
            for name, value in observed_sources.items()
            if receipt["source_sha256"].get(name) != value
        )
        raise W3AuditError(f"W3_SOURCE_HASH_MISMATCH:{','.join(changed)}")
    content = derive()
    for name in CONTENT_FILES:
        stored = (AUDIT / name).read_bytes()
        if sha_bytes(stored) != receipt["output_sha256"][name]:
            raise W3AuditError(f"W3_STORED_ARTIFACT_HASH_MISMATCH:{name}")
        if sha_bytes(content[name]) != receipt["output_sha256"][name]:
            raise W3AuditError(f"W3_REDERIVED_ARTIFACT_HASH_MISMATCH:{name}")
    print("W3_CHECK_PASS " + receipt["output_sha256"]["rows.jsonl"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true", help="derive and write the audit artifacts")
    group.add_argument("--check", action="store_true", help="re-derive and verify every hash")
    args = parser.parse_args()
    if args.check:
        check()
        return
    first, second = derive(), derive()
    if first != second:
        raise W3AuditError("W3_NON_DETERMINISTIC_DERIVATION")
    write(first, build_receipt(first))
    print("W3_APPLY_OK " + sha_bytes(first["rows.jsonl"]))


if __name__ == "__main__":
    main()
