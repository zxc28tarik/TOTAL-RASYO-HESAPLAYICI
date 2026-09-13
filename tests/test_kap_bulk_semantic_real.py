from datetime import datetime
import hashlib
import os
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import pytest

from src.ingest.api.semantic_facts import SemanticFactMapper
from src.ingest.kap_bulk_exact_semantic_mapping import build_bulk_exact_semantic_config
from src.ingest.kap_bulk_financial_export import parse_kap_bulk_export_report, parse_kap_bulk_financial_cells
from src.ingest.kap_bulk_semantic_adapter import bulk_cells_to_financial_facts, exact_label_fact_code


ARCHIVE = Path(os.environ.get("KAP_BULK_REAL_ARCHIVE", "__missing_kap_archive__"))
CAPTURED = datetime(2026, 8, 31, tzinfo=ZoneInfo("Europe/Istanbul"))
REAL_ARCHIVE = pytest.mark.skipif(
    not ARCHIVE.exists(), reason="real KAP archive is an external validation artifact"
)


def _mapped(member: str, ticker: str, family: str):
    archive_hash = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    with ZipFile(ARCHIVE) as bundle:
        raw = bundle.read(member)
    report = parse_kap_bulk_export_report(archive_name=ARCHIVE.name, archive_sha256=archive_hash, member_name=member, raw_html=raw)
    cells = parse_kap_bulk_financial_cells(report, raw)
    facts = bulk_cells_to_financial_facts(report, cells, ticker=ticker, extracted_at=CAPTURED)
    return SemanticFactMapper(build_bulk_exact_semantic_config(family)).map_facts(facts, mapped_at=CAPTURED)


def test_label_bound_code_rejects_same_row_with_other_meaning() -> None:
    assert exact_label_fact_code("general_role_210015:129", "TOPLAM VARLIKLAR") != exact_label_fact_code("general_role_210015:129", "Ödenmiş Sermaye")


def test_label_bound_code_accepts_hyphenated_bank_role() -> None:
    code = exact_label_fact_code("par-banks_role_210013:50", "VARLIKLAR TOPLAMI")

    assert code.startswith("PAR-BANKS_ROLE_210013:50|LABEL_SHA256=")


def test_label_bound_codes_match_normalized_mapping_source_codes() -> None:
    config = build_bulk_exact_semantic_config("NONFIN")
    allowed_codes = {code for rule in config.fields for code in rule.source_codes}
    generated = exact_label_fact_code(
        "general_role_210015:129", "TOPLAM VARLIKLAR"
    ).upper()

    assert generated in allowed_codes


@REAL_ARCHIVE
def test_real_aefes_general_semantics_choose_parent_income() -> None:
    rows = _mapped("AEFES_934813_2021_1.xls", "AEFES", "NONFIN")
    current = {(row.canonical_field, row.period_end): row.value for row in rows}
    assert current[("TOTAL_ASSETS", datetime(2021,3,31).date())] == 54884888000
    assert current[("TOTAL_EQUITY", datetime(2021,3,31).date())] == 13672883000
    assert current[("NET_INCOME", datetime(2021,3,31).date())] == 295163000


@REAL_ARCHIVE
def test_real_aghol_holding_semantics_choose_parent_income() -> None:
    rows = _mapped("AGHOL_935411_2021_1.xls", "AGHOL", "HOLDING")
    current = {(row.canonical_field, row.period_end): row.value for row in rows}
    assert current[("TOTAL_ASSETS", datetime(2021,3,31).date())] == 76325970000
    assert current[("TOTAL_EQUITY", datetime(2021,3,31).date())] == 6338471000
    assert current[("NET_INCOME", datetime(2021,3,31).date())] == 684337000
