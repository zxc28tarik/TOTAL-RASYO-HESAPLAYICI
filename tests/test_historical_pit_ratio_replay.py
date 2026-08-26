from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.analytics.historical_pit_ratio_replay import (
    HistoricalPitRatioReplayError,
    run_historical_pit_ratio_foundation,
)


ANALYSIS=datetime(2023,1,2,19,0,tzinfo=timezone.utc)


def _frame(rows):
    return pd.DataFrame(rows,columns=[
        'ticker','period_end','version_tag','ratio_name','ratio_value','is_na'
    ])


def test_core_runs_before_val_with_same_analysis_at_and_ticker_set():
    calls=[]
    def core(conn,**kw):
        calls.append(('CORE',kw.copy()))
        return _frame([['AAA','2022-12-31','v1','ROE',.2,False]])
    def val(conn,**kw):
        calls.append(('VAL',kw.copy()))
        return _frame([['AAA','2022-12-31','v1','PB',1.5,False]])

    result=run_historical_pit_ratio_foundation(
        object(),analysis_at=ANALYSIS,routing={'BBB':'BANK','AAA':'NONFIN'},
        ratios_json_path='config/ratios.json',derivation_profile='P1',derivation_version=2,
        persist=True,core_runner=core,val_runner=val,
    )

    assert [x[0] for x in calls]==['CORE','VAL']
    assert calls[0][1]['analysis_at'] is ANALYSIS
    assert calls[1][1]['analysis_at'] is ANALYSIS
    assert calls[0][1]['tickers']==('AAA','BBB')
    assert calls[1][1]['tickers']==('AAA','BBB')
    assert calls[1][1]['derivation_profile']=='P1'
    assert calls[1][1]['derivation_version']==2
    assert result.tickers==('AAA','BBB')
    assert set(result.combined_ratios.ratio_name)=={'ROE','PB'}


def test_core_val_leak_fails_closed():
    def core(conn,**kw):
        return _frame([['AAA','2022-12-31','v1','PB',1.5,False]])
    def val(conn,**kw):
        return _frame([])
    with pytest.raises(HistoricalPitRatioReplayError,match='CORE pipeline VAL'):
        run_historical_pit_ratio_foundation(
            object(),analysis_at=ANALYSIS,routing={'AAA':'NONFIN'},
            ratios_json_path='config/ratios.json',derivation_profile='P1',derivation_version=1,
            core_runner=core,val_runner=val,
        )


def test_val_unknown_ratio_fails_closed():
    def core(conn,**kw): return _frame([])
    def val(conn,**kw): return _frame([['AAA','2022-12-31','v1','MAGIC',1,False]])
    with pytest.raises(HistoricalPitRatioReplayError,match='beklenmeyen oran'):
        run_historical_pit_ratio_foundation(
            object(),analysis_at=ANALYSIS,routing={'AAA':'NONFIN'},
            ratios_json_path='config/ratios.json',derivation_profile='P1',derivation_version=1,
            core_runner=core,val_runner=val,
        )


def test_foreign_ticker_and_duplicate_key_fail_closed():
    def empty_val(conn,**kw): return _frame([])
    def foreign(conn,**kw): return _frame([['ZZZ','2022-12-31','v1','ROE',.2,False]])
    with pytest.raises(HistoricalPitRatioReplayError,match='routing disi ticker'):
        run_historical_pit_ratio_foundation(
            object(),analysis_at=ANALYSIS,routing={'AAA':'NONFIN'},
            ratios_json_path='config/ratios.json',derivation_profile='P1',derivation_version=1,
            core_runner=foreign,val_runner=empty_val,
        )

    def duplicate(conn,**kw):
        return _frame([
            ['AAA','2022-12-31','v1','ROE',.2,False],
            ['AAA','2022-12-31','v1','ROE',.3,False],
        ])
    with pytest.raises(HistoricalPitRatioReplayError,match='duplicate ratio key'):
        run_historical_pit_ratio_foundation(
            object(),analysis_at=ANALYSIS,routing={'AAA':'NONFIN'},
            ratios_json_path='config/ratios.json',derivation_profile='P1',derivation_version=1,
            core_runner=duplicate,val_runner=empty_val,
        )


def test_naive_analysis_and_bad_profile_version_persist_rejected():
    empty=lambda conn,**kw:_frame([])
    base=dict(
        conn=object(),routing={'AAA':'NONFIN'},ratios_json_path='config/ratios.json',
        derivation_profile='P1',derivation_version=1,core_runner=empty,val_runner=empty,
    )
    with pytest.raises(HistoricalPitRatioReplayError,match='timezone-aware'):
        run_historical_pit_ratio_foundation(analysis_at=datetime(2023,1,2,19,0),**base)
    with pytest.raises(HistoricalPitRatioReplayError,match='derivation_profile'):
        run_historical_pit_ratio_foundation(analysis_at=ANALYSIS,**{**base,'derivation_profile':' '})
    with pytest.raises(HistoricalPitRatioReplayError,match='derivation_version'):
        run_historical_pit_ratio_foundation(analysis_at=ANALYSIS,**{**base,'derivation_version':0})
    with pytest.raises(HistoricalPitRatioReplayError,match='persist Python bool'):
        run_historical_pit_ratio_foundation(analysis_at=ANALYSIS,**{**base,'persist':1})
