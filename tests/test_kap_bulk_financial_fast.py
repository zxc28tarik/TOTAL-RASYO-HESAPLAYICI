from datetime import datetime
import hashlib
import importlib.util
import pytest
from src.ingest.kap_bulk_financial_export import KapBulkExportReport, KapBulkExportError, parse_kap_bulk_financial_cells
from scripts.kap_bulk_financial_fast import parse_kap_bulk_financial_cells_fast
from scripts.kap_bulk_financial_native import parse_kap_bulk_financial_cells_native


def report(raw):
    return KapBulkExportReport('fixture.zip','TEST_1_2021_1.xls','a'*64,hashlib.sha256(raw).hexdigest(),len(raw),1,'TEST','TEST',datetime.fromisoformat('2021-04-30T18:00:00+03:00'),2021,'Q1','SOLO','TRY',1000,'https://kap.org.tr/tr/Bildirim/1')


def context_fixture():
    return b'''<html><body><table class="financial-table tbl_general_role_1"><tbody>
    <tr><td colspan="2"></td><td class="context-header" colspan="2">01.01.2021 | 31.03.2021</td></tr>
    <tr><td colspan="2"></td><td class="taxonomy-dimensional-header-cell">Current</td><td class="taxonomy-dimensional-header-cell">Previous 31.12.2020</td></tr>
    <tr class="general_role_1-row-1 data-input-row"><td class="taxonomy-context-cell" rowspan="2">Shared context</td><td class="taxonomy-field-title">Cash</td><td class="taxonomy-context-value"><span title="1.234,50"></span></td><td class="taxonomy-context-value"><span title="(2.000)"></span></td></tr>
    <tr class="general_role_1-row-2 data-input-row"><td class="taxonomy-field-title">Receivables</td><td class="taxonomy-context-value"><span title="12"></span></td><td class="taxonomy-context-value"><span title="13"></span></td></tr>
    </tbody></table></body></html>'''


@pytest.mark.parametrize('backend',['html.parser','lxml','native-lxml'])
def test_indexed_grid_preserves_rowspan_colspan_and_context_order(backend):
    if backend in {'lxml','native-lxml'} and importlib.util.find_spec('lxml') is None:
        pytest.skip('optional experimental lxml backend not installed')
    raw=context_fixture(); metadata=report(raw)
    original=parse_kap_bulk_financial_cells(metadata,raw)
    actual=parse_kap_bulk_financial_cells_native(metadata,raw) if backend=='native-lxml' else parse_kap_bulk_financial_cells_fast(metadata,raw,parser_backend=backend)
    assert original==actual and len(actual)==4
    assert all(row.context_label.startswith('Shared context | 01.01.2021 | 31.03.2021 | ') for row in actual)
    assert {row.column_index for row in actual}=={2,3}


def test_fast_parser_rejects_changed_raw_member_before_parsing():
    raw=context_fixture()
    with pytest.raises(KapBulkExportError,match='member_sha256'):
        parse_kap_bulk_financial_cells_fast(report(raw),raw+b' ')


def test_fast_parser_preserves_missing_context_failure():
    raw=context_fixture().replace(b'01.01.2021 | 31.03.2021',b'No dates').replace(b'Previous 31.12.2020',b'Previous')
    for parser in (parse_kap_bulk_financial_cells,parse_kap_bulk_financial_cells_fast):
        with pytest.raises(KapBulkExportError,match='context tarihi'):
            parser(report(raw),raw)
