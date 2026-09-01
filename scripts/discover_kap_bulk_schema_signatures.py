from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from src.ingest.kap_bulk_financial_export import (
    KapBulkFinancialCell,
    parse_kap_bulk_export_report,
    parse_kap_bulk_financial_cells,
)


CONTRACT = "KAP_BULK_TECHNICAL_SCHEMA_DISCOVERY_V1"
MAX_EXAMPLES = 5


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def role_namespace(role: str) -> str:
    text = str(role).strip()
    if "_role_" not in text:
        return text
    return text.split("_role_", 1)[0]


def technical_schema_signature(roles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(role).strip() for role in roles if str(role).strip()}))
    if not normalized:
        raise ValueError("technical schema en az bir role icermeli")
    return normalized


def technical_schema_signature_sha256(roles: Iterable[str]) -> str:
    payload = json.dumps(
        technical_schema_signature(roles),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


@dataclass
class FactObservation:
    count: int = 0
    instant_count: int = 0
    ytd_count: int = 0
    context_examples: list[str] = field(default_factory=list)

    def add(self, cell: KapBulkFinancialCell) -> None:
        self.count += 1
        if cell.period_start is None:
            self.instant_count += 1
        else:
            self.ytd_count += 1
        if cell.context_label not in self.context_examples and len(self.context_examples) < MAX_EXAMPLES:
            self.context_examples.append(cell.context_label)


@dataclass
class SignatureObservation:
    report_count: int = 0
    source_entities: set[str] = field(default_factory=set)
    example_members: list[str] = field(default_factory=list)
    facts: dict[tuple[str, int, str], FactObservation] = field(default_factory=dict)

    def add_report(
        self,
        *,
        source_entity_code: str,
        member_name: str,
        cells: Iterable[KapBulkFinancialCell],
    ) -> None:
        self.report_count += 1
        self.source_entities.add(source_entity_code)
        if member_name not in self.example_members and len(self.example_members) < MAX_EXAMPLES:
            self.example_members.append(member_name)
        for cell in cells:
            key = (cell.table_role, cell.row_number, " ".join(cell.label_tr.split()))
            self.facts.setdefault(key, FactObservation()).add(cell)


def observe_reports(
    reports: Iterable[tuple[str, str, tuple[KapBulkFinancialCell, ...]]],
) -> dict[tuple[str, ...], SignatureObservation]:
    observations: dict[tuple[str, ...], SignatureObservation] = {}
    for source_entity_code, member_name, cells in reports:
        roles = technical_schema_signature(cell.table_role for cell in cells)
        bucket = observations.setdefault(roles, SignatureObservation())
        bucket.add_report(
            source_entity_code=source_entity_code,
            member_name=member_name,
            cells=cells,
        )
    return observations


def _serialize(
    observations: dict[tuple[str, ...], SignatureObservation],
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for roles, item in sorted(
        observations.items(),
        key=lambda pair: (-pair[1].report_count, pair[0]),
    ):
        facts = []
        for (role, row_number, label), obs in sorted(item.facts.items()):
            facts.append(
                {
                    "role": role,
                    "role_namespace": role_namespace(role),
                    "row_number": row_number,
                    "label_tr": label,
                    "label_sha256": _sha256_bytes(label.encode("utf-8")),
                    "observation_count": obs.count,
                    "instant_count": obs.instant_count,
                    "ytd_count": obs.ytd_count,
                    "context_examples": sorted(obs.context_examples),
                }
            )
        output.append(
            {
                "signature_sha256": technical_schema_signature_sha256(roles),
                "roles": list(roles),
                "role_namespaces": sorted({role_namespace(role) for role in roles}),
                "report_count": item.report_count,
                "source_entity_count": len(item.source_entities),
                "source_entities": sorted(item.source_entities),
                "example_members": sorted(item.example_members),
                "facts": facts,
            }
        )
    return output


def discover_archives(archives: Iterable[Path]) -> dict[str, object]:
    archive_rows: list[dict[str, object]] = []
    combined: list[tuple[str, str, tuple[KapBulkFinancialCell, ...]]] = []
    for archive in sorted((Path(p).resolve() for p in archives), key=lambda p: p.name):
        archive_hash = _sha256_path(archive)
        member_count = 0
        with ZipFile(archive) as bundle:
            members = sorted(name for name in bundle.namelist() if name.endswith(".xls"))
            for member_name in members:
                raw = bundle.read(member_name)
                report = parse_kap_bulk_export_report(
                    archive_name=archive.name,
                    archive_sha256=archive_hash,
                    member_name=member_name,
                    raw_html=raw,
                )
                cells = parse_kap_bulk_financial_cells(report, raw)
                combined.append((report.source_entity_code, member_name, cells))
                member_count += 1
        archive_rows.append(
            {
                "archive_name": archive.name,
                "archive_sha256": archive_hash,
                "member_count": member_count,
            }
        )
    observations = observe_reports(combined)
    return {
        "contract": CONTRACT,
        "archive_count": len(archive_rows),
        "archives": archive_rows,
        "report_count": sum(row["member_count"] for row in archive_rows),
        "technical_schema_count": len(observations),
        "technical_schemas": _serialize(observations),
        "semantic_mapping_authorized": False,
        "purpose": "DISCOVERY_ONLY_EXACT_ROLE_ROW_LABEL_EVIDENCE",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = discover_archives(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": _sha256_bytes(text.encode("utf-8")),
                "archive_count": result["archive_count"],
                "report_count": result["report_count"],
                "technical_schema_count": result["technical_schema_count"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
