"""Create the exact, cell-scoped M2 external-data procurement manifest."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIORITY_DIR = ROOT / "data/audit/m2_priority_matrix_v1"
BASELINE_DIR = ROOT / "data/audit/experimental_materialization_v2"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path):
    return [json.loads(line) for line in gzip.decompress(Path(path).read_bytes()).splitlines()]


SCHEMA = [
    "ISIN", "Payın Kodu", "Özellik", "Ticaret Ünvanı", "Bülten Adı",
    "Kayıtlı Sermaye Tavanı (TL)", "Önceki Sermaye (TL)",
    "Sermaye Azaltımı (TL)", "Sermaye Azaltımı (%)",
    "Birleşme/Devralma Nedeniyle Sermaye Azaltımı (TL)",
    "Birleşme/Devralma Nedeniyle Sermaye Azaltımı (%)",
    "Rüçhan Hakkı Kullandırılarak (TL)", "Rüçhan Hakkı Kullandırılarak (%)",
    "Pay Satış Fiyatı (TL)", "Rüçhan Hakkı Kullandırılmadan (TL)",
    "Pay Satış Fiyatı (TL)", "Birleşme ve Devralma (TL)",
    "İç Kaynaklardan (TL)", "İç Kaynaklardan (%)", "Kar Payından (TL)",
    "Kar Payından (%)", "Cari Sermaye (TL)", "Hak Kullanım Başlangıç Tarihi",
    "Hak Kullanım Bitiş Tarihi", "Pay Dağıtım Tarihi",
    "Verilecek Menkul Grubu", "Pazar", "Not",
]


def build(output: Path):
    priority_path = PRIORITY_DIR / "m2_priority_cells.jsonl.gz"
    priority_summary_path = PRIORITY_DIR / "summary.json"
    priority_summary = json.loads(priority_summary_path.read_bytes())
    priority = rows(priority_path)
    if len(priority) != priority_summary["target_cells"] or len(priority) != 3017:
        raise ValueError("PRIORITY_MATRIX_COUNT_MISMATCH")

    by_year = defaultdict(list)
    standalone = defaultdict(list)
    for cell in priority:
        for year in cell["required_serart_years"]:
            by_year[year].append(cell)
        if len(cell["required_serart_years"]) == 1:
            standalone[cell["required_serart_years"][0]].append(cell)
    packages = []
    for year in range(2021, 2027):
        cells = by_year[year]
        packages.append({
            "year": year,
            "required_product": f"serart{year}.zip",
            "expected_inner_file": f"sermaye_{year}.xls",
            "official_product_family": "Sermaye Artırımları (Haftalık)",
            "why_required": "At least one priority cell's share-basis-to-price interval intersects this calendar year.",
            "dependent_priority_cell_count": len(cells),
            "dependent_priority_ticker_count": len({cell["ticker"] for cell in cells}),
            "dependent_priority_tickers": sorted({cell["ticker"] for cell in cells}),
            "standalone_single_year_priority_cell_count": len(standalone[year]),
            "m2_prerequisites_addressed": [
                "dated prior/current capital state", "share-class/ISIN identity",
                "share-changing event inventory", "effective/start/distribution dates",
            ],
            "expected_schema": SCHEMA,
            "resolves_dated_shares": "CONDITIONAL_ON_1_TL_NOMINAL_AND_ALL_SHARE_CLASS_RECONCILIATION",
            "resolves_corporate_action_completeness": "CONDITIONAL_ON_DELIVERY_CONTAINING_EACH_REQUIRED_HISTORICAL_AS_OF_SNAPSHOT",
            "does_not_resolve": ["BANK coe/macro assumptions", "financial-statement gaps", "sector-family gaps"],
        })

    cutoffs = sorted({cell["knowledge_cutoff_at"][:7].replace("-", "") for cell in priority})
    monthly_fallback = [{
        "required_product": f"serart{yyyymm}.zip",
        "expected_inner_file": f"SERM{yyyymm}.xls",
        "required_as_of_month": yyyymm,
    } for yyyymm in cutoffs]

    baseline = rows(BASELINE_DIR / "p3_cells.jsonl.gz")
    banks = [cell for cell in baseline if cell.get("historical_family") == "BANK"
             and "BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING" in cell.get("reasons", [])]
    if len(banks) != 509:
        raise ValueError(f"BANK_BLOCKER_COUNT_MISMATCH:{len(banks)}")

    manifest = {
        "contract": "M2_EXTERNAL_DATA_PROCUREMENT_MANIFEST_V1",
        "generated_at": "2026-09-06T00:00:00+03:00",
        "scope": "3017 cells having all modules except M2",
        "access_status": "AUTHENTICATED_PAID_PURCHASE_REQUIRED",
        "unauthorized_access_attempted": False,
        "package_search": {
            "worktree_and_local_desktop": "NO_SERART_OR_SERMAYE_PACKAGE_FOUND",
            "all_local_git_refs_and_objects": "NO_SERART_OR_SERMAYE_PATH_FOUND",
            "github_public_releases_checked": 1,
            "github_public_actions_artifact_names_checked": 95,
            "github_result": "NO_SERART_OR_SERMAYE_RELEASE_ASSET_OR_ACTION_ARTIFACT_FOUND",
        },
        "official_references": [
            "https://www.borsaistanbul.com/veriler/gecmise-donuk-veri-satisi",
            "https://datastore.borsaistanbul.com/",
            "https://datastore.borsaistanbul.com/assets/files/DataStore_Veri_Bildirim_ve_Kabul_Formatlar%C4%B1.pdf",
        ],
        "minimum_annual_package_request": {
            "packages": [package["required_product"] for package in packages],
            "package_count": 6,
            "condition": "Vendor must confirm the delivery preserves dated weekly snapshots/revisions sufficient for every cutoff from 2021-07 through 2026-06; a latest/final annual roll-up alone is not PIT evidence.",
            "best_case_priority_total_score_upper_bound": 3017,
        },
        "annual_packages": packages,
        "point_in_time_safe_fallback_if_annual_archives_lack_revisions": {
            "package_count": len(monthly_fallback),
            "packages": monthly_fallback,
            "instruction": "Request only after DataStore confirms annual products do not contain historical as-of snapshots. Each file must be the month-end snapshot available no later than the corresponding valuation cutoff.",
        },
        "purchase_before_confirmation_recommended": False,
        "vendor_questions_before_purchase": [
            "Does each serartYYYY delivery contain every historical weekly snapshot or only the latest/final yearly roll-up?",
            "Does each delivered member carry an as-of/publication timestamp that can be compared with a backtest cutoff?",
            "Can 2021-2026 be supplied with unchanged original bytes and SHA-256/checksum metadata?",
            "Are all listed and non-listed share classes plus company-total rows included?",
        ],
        "raw_price_product_required": False,
        "raw_price_reason": "All 3017 priority cells are already verified against hash-pinned Yahoo Close acquired with auto_adjust=False; Adj Close is retained only as diagnostics.",
        "share_derivation_warning": "Cari/Önceki Sermaye in TL is not automatically a share count. Derive shares only after the delivered pay/ISIN rows prove 1 TL nominal units and all classes reconcile to the company total.",
        "corporate_action_warning": "A search with no result is not completeness. Accept only event inventory plus pre/post-state reconciliation and dated source coverage through the price date.",
        "bank_requirement": {
            "affected_cells": len(banks),
            "affected_tickers": sorted({cell["ticker"] for cell in banks}),
            "separate_from_serart": True,
            "historical_or_cutoff_bound": {
                "coe": "DATE_VARYING_MODEL_ASSUMPTION_DERIVED_FROM_HISTORICAL_OBSERVABLES",
                "macro_cap": "DATE_VARYING_MACRO_ASSUMPTION",
                "risk_free_rate": "OPTIONAL_HISTORICAL_OBSERVABLE_DIAGNOSTIC",
            },
            "versioned_model_policy": {
                "tier_cap": 0.80,
                "payout_missing_factor": 0.70,
                "band_width_shadow_mode": True,
                "max_halfwidth": 0.80,
            },
            "gate_assessment": "NOT_TOO_STRICT_OVERALL: policy constants need version identity, not external historical observations; dated coe and macro_cap remain genuinely missing and are sufficient to reject all 509 cells.",
        },
        "remaining_after_best_case_serart_purchase": [
            "Validate nominal unit and aggregate every share class per ticker.",
            "Reconcile pre-state + every effective share-changing event = independently observed post-state.",
            "Materialize the NONFIN/HOLDING/GYO M2 engines with the verified basis; no hard-coded score.",
            "Obtain separate cutoff-dated BANK coe and macro_cap assumptions for 9 tickers/509 cells.",
        ],
        "best_case_target_coverage": {
            "priority_cells": 3017,
            "raw_close_ready": 3017,
            "potential_total_score_upper_bound_after_all_nonbank_validation_and_engine_work": 3017,
            "guaranteed_unlock_from_purchase_alone": 0,
        },
        "source_hashes": {
            "priority_cells": sha(priority_path),
            "priority_summary": sha(priority_summary_path),
            "baseline_p3": sha(BASELINE_DIR / "p3_cells.jsonl.gz"),
            "producer": sha(__file__),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded(manifest))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
