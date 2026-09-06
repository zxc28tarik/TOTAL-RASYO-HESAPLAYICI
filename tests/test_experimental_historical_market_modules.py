import numpy as np
import pandas as pd
import pytest

from scripts.experimental_historical_market_modules import dated_route, build_market_modules, SOURCE


def test_dated_route_enforces_publication_effective_and_ticker_identity():
    assert dated_route('2024-09-05T12:00:00+03:00', 'GRTRK')[0] is None
    assert dated_route('2024-09-07T12:00:00+03:00', 'GRTRK')[0] is None
    assert dated_route('2024-09-10T12:00:00+03:00', 'GRTRK')[0] == 'XUMAL'
    assert dated_route('2024-09-10T12:00:00+03:00', 'GRTHO')[0] is None
    assert dated_route('2025-01-03T10:00:00+03:00', 'GRTHO')[0] == 'XUMAL'
    assert dated_route('2025-01-03T10:00:00+03:00', 'AAA')[0] is None


def test_invalid_source_is_rejected_even_when_tokens_survive(tmp_path):
    path = tmp_path/'changed.html'; path.write_bytes(SOURCE.read_bytes()+b' ')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        dated_route('2025-01-03T10:00:00+03:00', 'GRTHO', source_path=path)


def frames():
    days = pd.bdate_range(end='2025-01-02', periods=320)
    step = np.arange(len(days))
    calendar = pd.DataFrame({'trade_date': days})
    prices = pd.concat([pd.DataFrame(dict(ticker=t, trade_date=days,
        close=50*np.exp(step*.0016), adj_close=50*np.exp(step*.0016)))
        for t in ('GRTHO', 'AAA')], ignore_index=True)
    indices = pd.concat([pd.DataFrame(dict(index_code=c,trade_date=days,
        close=100*np.exp(step*r))) for c,r in [('XU100',.001),('XUMAL',.0013)]], ignore_index=True)
    return calendar, prices, indices


def test_dated_subset_runs_market_math_while_other_ticker_keeps_independent_ek9():
    result = build_market_modules('2025-01-03T10:00:00+03:00', ['AAA','GRTHO'], *frames())
    grtho = result['per_ticker']['GRTHO']; aaa = result['per_ticker']['AAA']
    for name in ['M3','Ek4','Ek9']:
        assert grtho[name]['value'] is not None, grtho[name]['reasons']
    assert aaa['M3']['value'] is None
    assert aaa['Ek4']['value'] is None
    assert aaa['Ek9']['value'] is not None


def test_post_cutoff_prices_do_not_leak():
    calendar, prices, indices = frames()
    prices.loc[0,'trade_date'] = pd.Timestamp('2025-01-03')
    result = build_market_modules('2025-01-03T10:00:00+03:00', ['GRTHO'], calendar,prices,indices)
    assert all(m['value'] is None and m['reasons'] == ['POST_CUTOFF_MARKET_DATA']
        for m in result['per_ticker']['GRTHO'].values())
