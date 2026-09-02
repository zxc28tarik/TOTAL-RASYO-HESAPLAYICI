from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping
from zipfile import ZipFile

from scripts.discover_kap_bulk_current_snapshot import build_current_identity_manifest
from scripts.discover_kap_bulk_schema_signatures import role_namespace, validate_archive_inputs
from src.ingest.kap_bulk_financial_export import (
    KapBulkFinancialCell,
    parse_kap_bulk_export_report,
    parse_kap_bulk_financial_cells,
)


CONTRACT = "KAP_BULK_BANK_SCHEMA_TARGETED_DISCOVERY_CURRENT_SNAPSHOT_V1"
TARGET_ROLE_NAMESPACES = frozenset({"banks", "par-banks"})
RAW_MARKERS = (b"banks_role_", b"par-banks_role_")
STREAM_SCAN_CHUNK_BYTES = 1024 * 1024
MAX_EXAMPLES = 5


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_target_raw(raw: bytes) -> bool:
    lowered = bytes(raw).lower()
    return any(marker in lowered for marker in RAW_MARKERS)


def _stream_contains_target_role(
    handle: BinaryIO,
    *,
    chunk_size: int = STREAM_SCAN_CHUNK_BYTES,
) -> bool:
    """Search target role markers without materializing every XLS member.

    Non-target members are scanned incrementally and discarded. Only members that
    expose an exact BANK / participation-bank technical role are subsequently read
    in full and parsed. ``carry`` preserves markers split across chunk boundaries.
    """
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size pozitif Python int olmali")
    overlap = max(len(marker) for marker in RAW_MARKERS) - 1
    carry = b""
    while True:
        block = handle.read(chunk_size)
        if not block:
            return False
        probe = carry + bytes(block).lower()
        if any(marker in probe for marker in RAW_MARKERS):
            return True
        carry = probe[-overlap:] if overlap else b""


def _target_cells(cells: Iterable[KapBulkFinancialCell]) -> tuple[KapBulkFinancialCell, ...]:
    return tuple(cell for cell in cells if role_namespace(cell.table_role) in TARGET_ROLE_NAMESPACES)


@dataclass
class Observation:
    observation_count: int = 0
    instant_count: int = 0
    ytd_count: int = 0
    source_entities: set[str] = field(default_factory=set)
    archive_names: set[str] = field(default_factory=set)
    example_members: list[str] = field(default_factory=list)
    context_examples: list[str] = field(default_factory=list)

    def add(
        self,
        *,
        cell: KapBulkFinancialCell,
        source_entity: str,
        archive_name: str,
        member_name: str,
    ) -> None:
        self.observation_count += 1
        if cell.period_start is None:
            self.instant_count += 1
        else:
            self.ytd_count += 1
        self.source_entities.add(source_entity)
        self.archive_names.add(archive_name)
        if member_name not in self.example_members and len(self.example_members) < MAX_EXAMPLES:
            self.example_members.append(member_name)
        if cell.context_label not in self.context_examples and len(self.context_examples) < MAX_EXAMPLES:
            self.context_examples.append(cell.context_label)


def discover_targeted_bank_schema(
    archives: Iterable[Path],
    *,
    public_evidence: Mapping[str, object],
    public_evidence_sha256: str,
) -> dict[str, object]:
    current_manifest = build_current_identity_manifest(public_evidence)
    verified_archives = validate_archive_inputs(archives, current_manifest)

    observations: dict[tuple[str, int, str], Observation] = {}
    archive_rows: list[dict[str, object]] = []
    target_reports = 0
    target_source_entities: set[str] = set()
    target_roles: set[str] = set()

    for verified in verified_archives:
        matched_reports = 0
        matched_members: list[str] = []
        with ZipFile(verified.path) as bundle:
            members = sorted(name for name in bundle.namelist() if name.endswith(".xls"))
            for member_name in members:
                with bundle.open(member_name, "r") as member_stream:
                    if not _stream_contains_target_role(member_stream):
                        continue
                # Target members are rare. Read only those in full for the strict
                # report/cell parser; all other members were never materialized.
                raw = bundle.read(member_name)
                if not _is_target_raw(raw):
                    raise ValueError("stream BANK role gate ile full-byte gate celisti")
                report = parse_kap_bulk_export_report(
                    archive_name=verified.path.name,
                    archive_sha256=verified.sha256,
                    member_name=member_name,
                    raw_html=raw,
                )
                cells = _target_cells(parse_kap_bulk_financial_cells(report, raw))
                if not cells:
                    raise ValueError("BANK role marker bulundu ancak hedef hucre uretilmedi")
                matched_reports += 1
                target_reports += 1
                target_source_entities.add(report.source_entity_code)
                if len(matched_members) < MAX_EXAMPLES:
                    matched_members.append(member_name)
                for cell in cells:
                    namespace = role_namespace(cell.table_role)
                    if namespace not in TARGET_ROLE_NAMESPACES:
                        raise ValueError(f"hedef disi BANK namespace sizdi: {namespace}")
                    target_roles.add(cell.table_role)
                    key = (cell.table_role, cell.row_number, " ".join(cell.label_tr.split()))
                    observations.setdefault(key, Observation()).add(
                        cell=cell,
                        source_entity=report.source_entity_code,
                        archive_name=verified.path.name,
                        member_name=member_name,
                    )
        archive_rows.append(
            {
                "archive_name": verified.path.name,
                "archive_sha256": verified.sha256,
                "member_count": verified.member_count,
                "matched_bank_report_count": matched_reports,
                "matched_member_examples": matched_members,
            }
        )

    facts: list[dict[str, object]] = []
    for (role, row_number, label), observation in sorted(observations.items()):
        facts.append(
            {
                "role": role,
                "role_namespace": role_namespace(role),
                "row_number": row_number,
                "label_tr": label,
                "label_sha256": _sha256_bytes(label.encode("utf-8")),
                "observation_count": observation.observation_count,
                "instant_count": observation.instant_count,
                "ytd_count": observation.ytd_count,
                "source_entity_count": len(observation.source_entities),
                "source_entities": sorted(observation.source_entities),
                "archive_names": sorted(observation.archive_names),
                "example_members": sorted(observation.example_members),
                "context_examples": sorted(observation.context_examples),
            }
        )

    drift_names = sorted(
        str(row["filename"])
        for row in public_evidence.get("drifts", [])
        if isinstance(row, Mapping) and isinstance(row.get("filename"), str)
    )
    return {
        "contract": CONTRACT,
        "purpose": "DISCOVERY_ONLY_CURRENT_OFFICIAL_SNAPSHOT_BANK_AND_PARTICIPATION_BANK_ROLE_ROW_LABEL_EVIDENCE",
        "archive_count": len(archive_rows),
        "archives": archive_rows,
        "matched_bank_report_count": target_reports,
        "source_entity_count": len(target_source_entities),
        "source_entities": sorted(target_source_entities),
        "role_count": len(target_roles),
        "roles": sorted(target_roles),
        "role_namespaces": sorted({role_namespace(role) for role in target_roles}),
        "fact_identity_count": len(facts),
        "facts": facts,
        "current_snapshot_public_evidence_verified": True,
        "source_public_evidence_sha256": public_evidence_sha256,
        "source_workflow_run_id": public_evidence.get("workflow_run_id"),
        "source_acquisition_head_sha": public_evidence.get("acquisition_head_sha"),
        "source_acquisition_receipt_sha256": public_evidence.get("acquisition_receipt_sha256"),
        "preserved_manifest_sha256": public_evidence.get("manifest_file_sha256"),
        "preserved_manifest_exact_match_count": public_evidence.get("manifest_exact_match_count"),
        "preserved_manifest_drift_count": public_evidence.get("manifest_drift_count"),
        "preserved_manifest_drift_filenames": drift_names,
        "semantic_mapping_authorized": False,
        "pit_materialization_authorized": False,
        "real_60_cutoff_scoring_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--public-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence_bytes = args.public_evidence.read_bytes()
    public_evidence = json.loads(evidence_bytes.decode("utf-8"))
    if not isinstance(public_evidence, dict):
        raise ValueError("public evidence JSON root must be an object")
    result = discover_targeted_bank_schema(
        args.archive,
        public_evidence=public_evidence,
        public_evidence_sha256=_sha256_bytes(evidence_bytes),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": _sha256_bytes(text.encode("utf-8")),
                "archive_count": result["archive_count"],
                "matched_bank_report_count": result["matched_bank_report_count"],
                "source_entity_count": result["source_entity_count"],
                "role_count": result["role_count"],
                "fact_identity_count": result["fact_identity_count"],
                "role_namespaces": result["role_namespaces"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
