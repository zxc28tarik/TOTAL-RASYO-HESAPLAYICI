from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_semantic_config
from src.ingest.kap_bulk_financial_export import (
    KapBulkExportError,
    parse_kap_bulk_export_report,
    parse_kap_bulk_financial_cells,
)
from src.ingest.kap_bulk_semantic_adapter import (
    bulk_cells_to_financial_facts,
    exact_label_fact_code,
)


FIELDS = (
    "archive_name", "archive_sha256", "member_name", "member_sha256",
    "notification_id", "source_entity_code", "company_name", "published_at",
    "report_year", "report_period", "statement_scope", "technical_schema_family",
    "numeric_cell_count", "semantic_fact_count", "semantic_fields", "status", "reason",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _family(roles: set[str]) -> str | None:
    general = any(role.startswith("general_role_") for role in roles)
    holding = any(role.startswith("holding_role_") for role in roles)
    if general and holding:
        raise KapBulkExportError("general ve holding rolleri ayni raporda")
    if holding:
        return "HOLDING"
    if general:
        return "NONFIN"
    return None


def _completed(path: Path, *, archive_name: str, archive_hash: str) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("partial checkpoint kolonlari degisti")
        rows = list(reader)
    names = [row["member_name"] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("partial checkpoint duplicate member iceriyor")
    if any(
        row["archive_name"] != archive_name or row["archive_sha256"] != archive_hash
        for row in rows
    ):
        raise ValueError("partial checkpoint baska archive kimligi iceriyor")
    return set(names)


def build(archive: Path, output_dir: Path, *, captured_at: datetime) -> dict[str, object]:
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at timezone icermeli")
    archive = archive.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_hash = _sha(archive)
    stem = archive.stem
    partial = output_dir / f"{stem}.reports.partial.csv"
    final = output_dir / f"{stem}.reports.csv.gz"
    completed = _completed(
        partial, archive_name=archive.name, archive_hash=archive_hash
    )
    write_header = not partial.exists()
    with ZipFile(archive) as bundle, partial.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if write_header:
            writer.writeheader()
            handle.flush()
        members = sorted(name for name in bundle.namelist() if name.endswith(".xls"))
        for member_name in members:
            if member_name in completed:
                continue
            row = {field: "" for field in FIELDS}
            row.update({"archive_name": archive.name, "archive_sha256": archive_hash, "member_name": member_name})
            try:
                raw = bundle.read(member_name)
                report = parse_kap_bulk_export_report(
                    archive_name=archive.name, archive_sha256=archive_hash,
                    member_name=member_name, raw_html=raw,
                )
                cells = parse_kap_bulk_financial_cells(report, raw)
                family = _family({cell.table_role for cell in cells})
                row.update({
                    "member_sha256": report.member_sha256,
                    "notification_id": report.notification_id,
                    "source_entity_code": report.source_entity_code,
                    "company_name": report.company_name,
                    "published_at": report.published_at.isoformat(),
                    "report_year": report.report_year,
                    "report_period": report.report_period,
                    "statement_scope": report.statement_scope,
                    "technical_schema_family": family or "UNSUPPORTED",
                    "numeric_cell_count": len(cells),
                })
                if family is None:
                    row.update({"status": "UNSUPPORTED_SCHEMA", "reason": "NO_GENERAL_OR_HOLDING_ROLE"})
                else:
                    config = build_bulk_exact_semantic_config(family)
                    allowed_codes = {
                        code for rule in config.fields for code in rule.source_codes
                    }
                    selected_cells = tuple(
                        cell for cell in cells
                        if exact_label_fact_code(cell.fact_code, cell.label_tr).upper() in allowed_codes
                    )
                    if not selected_cells:
                        raise KapBulkExportError("supported schema hic hedef semantik hucre icermiyor")
                    facts = bulk_cells_to_financial_facts(
                        report, selected_cells, ticker=report.source_entity_code.replace("-", "")[:12],
                        extracted_at=captured_at,
                    )
                    semantic = SemanticFactMapper(config).map_facts(
                        facts, mapped_at=captured_at
                    )
                    row.update({
                        "semantic_fact_count": len(semantic),
                        "semantic_fields": ";".join(sorted({item.canonical_field for item in semantic})),
                        "status": "MAPPED",
                    })
            except (KapBulkExportError, ValueError, TypeError) as exc:
                row.update({"status": "REJECTED", "reason": f"{type(exc).__name__}:{exc}"})
            writer.writerow(row)
            handle.flush()

    with partial.open("r", encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != len(members) or {row["member_name"] for row in rows} != set(members):
        raise ValueError("checkpoint tum archive uyelerini siniflandirmadi")
    text = partial.read_text(encoding="utf-8")
    with final.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            zipped.write(text.encode("utf-8"))
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    summary = {
        "contract": "KAP_ARCHIVE_SEMANTIC_CHECKPOINT_V1",
        "archive_name": archive.name,
        "archive_sha256": archive_hash,
        "captured_at": captured_at.isoformat(),
        "member_count": len(rows),
        "status_counts": status_counts,
        "report_inventory": final.name,
        "report_inventory_sha256": _sha(final),
        "memory_policy": "ONE_REPORT_AT_A_TIME_FLUSH_AFTER_EACH_REPORT",
        "authoritative_sector_routing": False,
    }
    summary_path = output_dir / f"{stem}.summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--captured-at", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    captured_at = datetime.fromisoformat(args.captured_at)
    print(json.dumps(build(args.archive, args.output_dir, captured_at=captured_at), ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
