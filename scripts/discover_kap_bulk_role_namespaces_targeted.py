from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping
from zipfile import ZipFile

from scripts.discover_kap_bulk_current_snapshot import build_current_identity_manifest
from scripts.discover_kap_bulk_schema_signatures import validate_archive_inputs


CONTRACT = "KAP_BULK_ROLE_NAMESPACE_TARGETED_DISCOVERY_V1"
STREAM_SCAN_CHUNK_BYTES = 1024 * 1024
# Both boundaries are deliberate. A role number split at a chunk boundary must not
# be accepted until its terminating non-digit arrives; adjacent text must not be
# absorbed into the namespace.
ROLE_PATTERN = re.compile(
    rb"(?<![a-z0-9-])([a-z0-9-]+)_role_([0-9]+)(?=[^0-9])",
    re.IGNORECASE,
)
MAX_EXAMPLES = 8


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _collect_role_markers(probe: bytes, found: set[tuple[str, str]]) -> None:
    for match in ROLE_PATTERN.finditer(probe):
        namespace = match.group(1).decode("ascii")
        role = f"{namespace}_role_{match.group(2).decode('ascii')}"
        found.add((namespace, role))


def _stream_role_markers(
    handle: BinaryIO,
    *,
    chunk_size: int = STREAM_SCAN_CHUNK_BYTES,
) -> set[tuple[str, str]]:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size pozitif Python int olmali")
    # More than enough overlap for any realistic KAP taxonomy role marker.
    overlap = 256
    carry = b""
    found: set[tuple[str, str]] = set()
    while True:
        block = handle.read(chunk_size)
        if not block:
            # A marker can legally end at EOF. Add a synthetic non-digit solely to
            # finalize that trailing candidate; it never becomes source evidence.
            if carry:
                _collect_role_markers(carry + b" ", found)
            return found
        probe = carry + bytes(block).lower()
        _collect_role_markers(probe, found)
        carry = probe[-overlap:]


def discover_role_namespaces(
    archives: Iterable[Path],
    *,
    public_evidence: Mapping[str, object],
    public_evidence_sha256: str,
    scan_archive_name: str,
) -> dict[str, object]:
    current_manifest = build_current_identity_manifest(public_evidence)
    verified = validate_archive_inputs(archives, current_manifest)
    selected = [item for item in verified if item.path.name == scan_archive_name]
    if len(selected) != 1:
        raise ValueError("scan archive exact bir verified archive ile eslesmeli")

    namespace_members: Counter[str] = Counter()
    role_members: Counter[str] = Counter()
    namespace_examples: dict[str, list[str]] = {}
    matched_member_count = 0
    selected_archive = selected[0]
    with ZipFile(selected_archive.path) as bundle:
        members = sorted(name for name in bundle.namelist() if name.endswith(".xls"))
        for member_name in members:
            with bundle.open(member_name, "r") as handle:
                markers = _stream_role_markers(handle)
            if not markers:
                continue
            matched_member_count += 1
            namespaces_in_member = {namespace for namespace, _ in markers}
            roles_in_member = {role for _, role in markers}
            for namespace in namespaces_in_member:
                namespace_members[namespace] += 1
                examples = namespace_examples.setdefault(namespace, [])
                if member_name not in examples and len(examples) < MAX_EXAMPLES:
                    examples.append(member_name)
            for role in roles_in_member:
                role_members[role] += 1

    if not namespace_members:
        raise ValueError("verified archive role namespace uretmedi")
    namespace_rows = [
        {
            "namespace": namespace,
            "member_count": namespace_members[namespace],
            "example_members": sorted(namespace_examples.get(namespace, [])),
            "roles": sorted(role for role in role_members if role.startswith(f"{namespace}_role_")),
        }
        for namespace in sorted(namespace_members)
    ]
    return {
        "contract": CONTRACT,
        "purpose": "DISCOVERY_ONLY_FAST_ROLE_NAMESPACE_INVENTORY",
        "archive_count": len(verified),
        "scan_archive_name": selected_archive.path.name,
        "scan_archive_sha256": selected_archive.sha256,
        "scan_archive_member_count": selected_archive.member_count,
        "matched_member_count": matched_member_count,
        "namespace_count": len(namespace_rows),
        "namespaces": namespace_rows,
        "current_snapshot_public_evidence_verified": True,
        "source_public_evidence_sha256": public_evidence_sha256,
        "semantic_mapping_authorized": False,
        "pit_materialization_authorized": False,
        "real_60_cutoff_scoring_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--scan-archive-name", required=True)
    parser.add_argument("--public-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence_bytes = args.public_evidence.read_bytes()
    public_evidence = json.loads(evidence_bytes.decode("utf-8"))
    if not isinstance(public_evidence, dict):
        raise ValueError("public evidence JSON root must be an object")
    result = discover_role_namespaces(
        args.archive,
        public_evidence=public_evidence,
        public_evidence_sha256=_sha256_bytes(evidence_bytes),
        scan_archive_name=args.scan_archive_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sha256": _sha256_bytes(text.encode("utf-8")),
        "namespace_count": result["namespace_count"],
        "namespaces": [row["namespace"] for row in result["namespaces"]],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
