"""Source-bound partial market modules without a current sector snapshot."""
from datetime import datetime, date, timedelta
from functools import lru_cache
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import pandas as pd

from scripts.experimental_core_module_materializer import _json, _time
from src.analytics.historical_cutoff_execution_policy import TOTAL_RASYO_MONTHLY_OPEN_V1
from src.analytics.historical_pit_m3_replay import run_historical_pit_m3_replay
from src.analytics.historical_pit_ek4_replay import run_historical_pit_ek4_replay
from src.analytics.historical_pit_ek9_replay import run_historical_pit_ek9_replay

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'data/backtest_sources/m3_source_package/raw/kap_bildirim_1331451.html'
LINEAGE = ROOT/'data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv'
SOURCE_SHA = 'e9564b36bafd4712316209c2be7a9c585cb392aad151d3aff8343c661b6bde11'
LINEAGE_SHA = '8d71a1d442a1389fd0f0d65414d21fd10b6f95403aa29bda147c638a3e98fa4e'
PUBLICATION = datetime(2024, 9, 6, 16, 34, 9, tzinfo=ZoneInfo('Europe/Istanbul'))
EFFECTIVE = date(2024, 9, 9)
RENAME = date(2024, 10, 1)


@lru_cache(maxsize=1)
def _verify_bytes(raw, lineage):
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('HISTORICAL_ROUTE_SOURCE_HASH_MISMATCH')
    if hashlib.sha256(lineage).hexdigest() != LINEAGE_SHA:
        raise ValueError('HISTORICAL_TICKER_LINEAGE_HASH_MISMATCH')
    soup = BeautifulSoup(raw, 'html.parser')
    text = soup.get_text(' ', strip=True)
    if '06.09.2024 16:34:09' not in text or '[GRTRK]' not in text:
        raise ValueError('HISTORICAL_ROUTE_PUBLICATION_IDENTITY_MISSING')
    rows = [[td.get_text(' ', strip=True) for td in tr.find_all(['td', 'th'], recursive=False)]
            for tr in soup.find_all('tr')]
    if not any(r[:4] == ['GRAINTURK HOLDING', 'XUMAL', '', '09/09/2024'] for r in rows):
        raise ValueError('HISTORICAL_ROUTE_INCLUSION_UNPROVEN')
    if not any(r[:4] == ['GRAINTURK HOLDING', '', 'XUHIZ', '09/09/2024'] for r in rows):
        raise ValueError('HISTORICAL_ROUTE_EXCLUSION_UNPROVEN')
    if '2024-10-01,GRTRK,GRTHO,' not in lineage.decode():
        raise ValueError('HISTORICAL_TICKER_LINEAGE_EVENT_MISSING')


def dated_route(cutoff, ticker, *, source_path=SOURCE, lineage_path=LINEAGE):
    """Latest explicitly dated event; subsequent-change enumeration is a risk."""
    analysis = _time(cutoff)
    _verify_bytes(Path(source_path).read_bytes(), Path(lineage_path).read_bytes())
    local_day = analysis.astimezone(ZoneInfo('Europe/Istanbul')).date()
    if ticker not in {'GRTRK', 'GRTHO'}:
        return None, 'HISTORICAL_SECTOR_INDEX_EVIDENCE_MISSING'
    if analysis < PUBLICATION:
        return None, 'HISTORICAL_ROUTE_ANNOUNCEMENT_NOT_YET_PUBLISHED'
    if local_day < EFFECTIVE:
        return None, 'HISTORICAL_ROUTE_EVENT_NOT_YET_EFFECTIVE'
    if (ticker == 'GRTRK' and local_day >= RENAME) or (ticker == 'GRTHO' and local_day < RENAME):
        return None, 'HISTORICAL_ROUTE_TICKER_EFFECTIVE_DATE_MISMATCH'
    return 'XUMAL', None


def build_market_modules(cutoff, tickers, calendar, prices, indices):
    analysis = _time(cutoff)
    signal_day = analysis.astimezone(ZoneInfo('Europe/Istanbul')).date()
    # The monthly policy explicitly authorizes the three half-day closes at
    # 12:40. The generic bank helper assumes 18:10 and would reject every valid
    # cutoff-day observation for those months as future data.
    local_analysis = analysis.astimezone(ZoneInfo('Europe/Istanbul'))
    session_end = TOTAL_RASYO_MONTHLY_OPEN_V1.session_end_for(pd.Timestamp(signal_day))
    market_day = (signal_day if local_analysis.timetz().replace(tzinfo=None) >= session_end
                  else signal_day - timedelta(days=1))
    wanted = tuple(sorted(set(tickers)))
    result = {'contract': 'EXPERIMENTAL_PARTIAL_MARKET_MODULES_V1',
        'analysis_at': analysis.isoformat(), 'current_sector_fallback': False,
        'market_asof_date': market_day.isoformat(),
        'provenance': {'route_source_path': str(SOURCE.relative_to(ROOT)),
            'route_source_sha256': SOURCE_SHA, 'lineage_sha256': LINEAGE_SHA,
            'publication': PUBLICATION.isoformat(), 'effective_date': EFFECTIVE.isoformat(),
            'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'cutoff_policy_sha256': TOTAL_RASYO_MONTHLY_OPEN_V1.descriptor_sha256,
            'risk_ids': ['SUBSEQUENT_SECTOR_CHANGE_ENUMERATION_UNPROVEN'],
            'authoritative_pit_claim_allowed': False}, 'per_ticker': {}}
    for ticker in wanted:
        result['per_ticker'][ticker] = {m: {'value': None, 'reasons': []} for m in ('M3','Ek4','Ek9')}
    if not wanted:
        return result
    # Caller must supply its actual bounded market slice; a stale/future slice
    # cannot be repaired invisibly inside a provenance-sensitive helper.
    for frame in (calendar, prices, indices):
        if not frame.empty and (pd.to_datetime(frame.trade_date).dt.date > market_day).any():
            for d in result['per_ticker'].values():
                for module in d.values():
                    module['reasons'].append('POST_CUTOFF_MARKET_DATA')
            return result
    base = dict(analysis_at=analysis, asof_date=signal_day, market_asof_date=market_day,
                trading_calendar=calendar, stock_prices=prices)

    def run(name, runner, subset, **kwargs):
        try:
            args = dict(base, stock_prices=prices[prices.ticker.isin(subset)].copy())
            replay = runner(**args, **kwargs)
            frame = getattr(replay, name.lower() + '_scores')
            for ticker in subset:
                target = result['per_ticker'][ticker][name]
                hit = frame[frame.ticker == ticker]
                if not hit.empty:
                    row = hit.iloc[0].to_dict()
                    target.update(value=row[name.lower()], source_result=row)
                reject = replay.rejections[replay.rejections.ticker == ticker]
                target['reasons'].extend(reject.reason.astype(str).tolist())
                if hit.empty and reject.empty:
                    target['reasons'].append('MODULE_NO_RESULT')
        except (ValueError, TypeError, KeyError) as exc:
            for ticker in subset:
                result['per_ticker'][ticker][name]['reasons'].append(
                    'MODULE_INPUT_REJECTED:' + type(exc).__name__ + ':' + str(exc))

    run('Ek9', run_historical_pit_ek9_replay, wanted, universe=pd.DataFrame({'ticker': wanted}))
    routes = []
    for ticker in wanted:
        try:
            code, reason = dated_route(analysis, ticker)
        except (ValueError, OSError) as exc:
            code, reason = None, str(exc)
        if code:
            routes.append({'ticker': ticker, 'sector_index_code': code})
            result['per_ticker'][ticker]['route'] = {'sector_index_code': code,
                'source_id': 'KAP_BILDIRIM_1331451',
                'risk_id': 'SUBSEQUENT_SECTOR_CHANGE_ENUMERATION_UNPROVEN'}
        else:
            for name in ('M3', 'Ek4'):
                result['per_ticker'][ticker][name]['reasons'].append(reason)
    if routes:
        universe = pd.DataFrame(routes)
        subset = tuple(universe.ticker)
        for name, runner in [('M3', run_historical_pit_m3_replay), ('Ek4', run_historical_pit_ek4_replay)]:
            run(name, runner, subset, universe=universe, index_prices=indices)
    return _json(result)
