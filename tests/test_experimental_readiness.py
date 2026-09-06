from copy import deepcopy

import pandas as pd
import pytest

from scripts.audit_experimental_readiness import ROOT, INDICES, run_audit, validate_p4_boundary
from scripts.build_historical_m3_source_package import _historical_membership
from src.analytics.historical_cutoff_execution_policy import build_authorized_cutoff_execution_schedule


@pytest.fixture(scope='module')
def boundary():
    members = _historical_membership(ROOT).sort_values(['signal_date','ticker'])
    indices = pd.read_csv(INDICES)
    indices['trade_date'] = pd.to_datetime(indices.trade_date)
    schedule = build_authorized_cutoff_execution_schedule(
        members[['month','signal_date','index_code']].drop_duplicates(),
        indices.loc[indices.index_code.eq('XU100'),['trade_date']].sort_values('trade_date'))
    cutoffs = {pd.Timestamp(r.signal_date).date().isoformat():r.cutoff_at.isoformat() for r in schedule.itertuples()}
    # These rejected cells exercise adapter behavior, not historical score evidence.
    cells = [dict(signal_date=str(r.signal_date),ticker=r.ticker,
                  knowledge_cutoff_at=cutoffs[str(r.signal_date)],status='EXPLICIT_REJECTION',
                  final_score=None,decision=None,reasons=['TEST_REJECTION']) for r in members.itertuples()]
    return cells,members,schedule


def test_production_auditor_does_not_accept_an_invented_registry(boundary):
    result = run_audit(boundary[0])
    assert result['status'] == 'BLOCKED' and not result['ready']
    assert result['checked_months'] == 60
    assert result['registry_rows_created'] == result['registry_rows'] == 0
    assert any(r['category']=='TOTAL_RASYO' and r['code']=='AUTHORITY_INVALID' for r in result['findings'])
    assert result['p4_status_counts'] == {'EXPLICIT_REJECTION':6000}


@pytest.mark.parametrize('mutation,reason',[
    ('member','P4_HISTORICAL_MEMBERSHIP'),('cutoff','P4_AUTHORIZED_CUTOFF'),
    ('score','P4_REJECTED_CELL_HAS_SCORE')])
def test_frame_boundary_preserves_source_identity_and_rejections(boundary,mutation,reason):
    cells=deepcopy(boundary[0])
    if mutation=='member': cells[0]['ticker']='FABRICATED'
    if mutation=='cutoff': cells[0]['knowledge_cutoff_at']=cells[0]['signal_date']+'T18:10:00+03:00'
    if mutation=='score': cells[0]['final_score']=0.95
    with pytest.raises(ValueError,match=reason):validate_p4_boundary(cells,*boundary[1:])
