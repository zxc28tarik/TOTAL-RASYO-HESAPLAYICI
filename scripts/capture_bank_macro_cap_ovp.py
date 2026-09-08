"""Bind every blocked BANK cell to the latest cutoff-eligible official SBB OVP."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
P3 = ROOT / "data/audit/experimental_materialization_v2/p3_cells.jsonl.gz"
OUT = ROOT / "data/backtest_sources/bank_macro_cap_ovp_v1"

VINTAGES = (
    {"ovp": "2021-2023", "published_at": "2020-09-29T23:59:59+03:00",
     "previous": 6310, "last": 7021,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2021/08/YeniEkonomiProgrami_OVP_2021-2023.pdf"},
    {"ovp": "2022-2024", "published_at": "2021-09-05T23:59:59+03:00",
     "previous": 9041, "last": 10287,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2021/09/Orta-Vadeli-Program-2022-2024.pdf"},
    {"ovp": "2023-2025", "published_at": "2022-09-04T23:59:59+03:00",
     "previous": 23438, "last": 27440,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2022/09/Orta-Vadeli-Program-2023-2025.pdf"},
    {"ovp": "2024-2026", "published_at": "2023-09-06T23:59:59+03:00",
     "previous": 52942, "last": 62997,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2023/09/Orta-Vadeli-Program_2024-2026.pdf"},
    {"ovp": "2025-2027", "published_at": "2024-09-05T23:59:59+03:00",
     "previous": 72915, "last": 83132,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2024/09/Orta-Vadeli-Program_2025-2027.pdf"},
    {"ovp": "2026-2028", "published_at": "2025-09-07T23:59:59+03:00",
     "previous": 89406, "last": 101397,
     "url": "https://www.sbb.gov.tr/wp-content/uploads/2025/09/Orta-Vadeli-Program-2026-2028.pdf"},
)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def _encode(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=None if indent else (",", ":"), indent=indent,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def select_vintage(cutoff: str) -> dict:
    point = datetime.fromisoformat(cutoff)
    eligible = [row for row in VINTAGES if datetime.fromisoformat(row["published_at"]) <= point]
    if not eligible:
        raise ValueError("NO_CUTOFF_ELIGIBLE_OVP")
    return max(eligible, key=lambda row: row["published_at"])


def capture(output_dir: Path = OUT, *, retain_source_pdfs: bool = False) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    for vintage in VINTAGES:
        filename = f"ovp_{vintage['ovp'].replace('-', '_')}.pdf"
        path = output_dir / filename
        if path.exists():
            raw = path.read_bytes()
        else:
            response = requests.get(vintage["url"], timeout=90)
            response.raise_for_status()
            raw = response.content
            path.write_bytes(raw)
        if not raw.startswith(b"%PDF-"):
            raise ValueError(f"OFFICIAL_OVP_NOT_PDF:{vintage['ovp']}")
        sources[vintage["ovp"]] = {
            "filename": filename, "url": vintage["url"], "sha256": _sha(raw),
            "size_bytes": len(raw), "published_at": vintage["published_at"],
            "gdp_previous_billion_try": vintage["previous"],
            "gdp_last_billion_try": vintage["last"],
        }

    banks = [row for row in _rows(P3) if row.get("historical_family") == "BANK"
             and "BANK_PIT_ASSUMPTIONS_EVIDENCE_MISSING" in row.get("reasons", [])]
    if len(banks) != 509:
        raise ValueError(f"BANK_CELL_COUNT_MISMATCH:{len(banks)}")
    observations = []
    for cell in banks:
        vintage = select_vintage(cell["knowledge_cutoff_at"])
        ratio = Decimal(vintage["last"]) / Decimal(vintage["previous"]) - Decimal(1)
        observations.append({
            "ticker": cell["ticker"], "month": cell["month"],
            "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
            "ovp": vintage["ovp"], "ovp_published_at": vintage["published_at"],
            "macro_cap": str(ratio),
            "definition": "LAST_FORECAST_NOMINAL_GDP/PREVIOUS_FORECAST_NOMINAL_GDP-1",
            "source_file": sources[vintage["ovp"]]["filename"],
            "source_sha256": sources[vintage["ovp"]]["sha256"],
            "status": "OFFICIAL_CUTOFF_ELIGIBLE_MACRO_CAP",
        })
    observations.sort(key=lambda row: (row["month"], row["ticker"]))
    obs_path = output_dir / "bank_macro_cap_cells.jsonl.gz"
    obs_path.write_bytes(gzip.compress(b"".join(_encode(row) for row in observations), mtime=0))
    counts = Counter(row["ovp"] for row in observations)
    receipt = {
        "contract": "BANK_OFFICIAL_OVP_MACRO_CAP_V1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "bank_cells": len(observations), "covered_cells": len(observations),
        "vintage_cell_counts": dict(sorted(counts.items())),
        "definition": "latest cutoff-eligible OVP last forecast nominal GDP / previous forecast nominal GDP - 1",
        "sources": sources,
        "raw_source_bytes_committed": retain_source_pdfs,
        "source_bytes_policy": (
            "RAW_PDFS_RETAINED" if retain_source_pdfs
            else "URL_AND_CAPTURE_SHA256_RECORDED;RAW_PDFS_REMOVED_TO_AVOID_92MB_GIT_BLOAT"
        ),
        "coe_status": "EXPLICIT_REJECTION:COE_METHODOLOGY_UNVERSIONED_AND_INPUT_LINEAGE_INCOMPLETE",
        "bank_m2_materialized": 0,
        "priority_3017_overlap_cells": 0,
        "total_score_unlock_upper_bound_from_macro_cap_only": 0,
        "outputs": {obs_path.name: _sha(obs_path.read_bytes())},
    }
    (output_dir / "receipt.json").write_bytes(_encode(receipt, indent=2))
    if not retain_source_pdfs:
        for source in sources.values():
            (output_dir / source["filename"]).unlink()
    return receipt


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
