"""Retrospective capital reconciliation is evidence, never an action completeness oracle.

This audit deliberately cannot create a runtime action bundle. Equal endpoint
balances do not exclude offsetting intermediate actions or changes of nominal value.
"""
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile

from bs4 import BeautifulSoup
from scripts.audit_p2_action_research_v1 import audit as dated_audit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/backtest_sources/p2_share_continuity_v1'


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def capital_point(raw, member):
    soup = BeautifulSoup(raw, 'html.parser')
    text = ' '.join(soup.get_text(' ', strip=True).split())
    node = soup.find(string=lambda value: value and value.strip() == 'Ödenmiş Sermaye').parent
    for _ in range(6):
        node = node.parent
    capital = int(node.find(attrs={'title': True})['title'])
    unit = 1000 if 'Sunum Para Birimi 1.000 TL' in text else 1
    period = re.search(r'Cari Dönem (\d{2}\.\d{2}\.\d{4})', text).group(1)
    published = re.search(r'Gönderim Tarihi:(.*?) Bildirim Tipi', text).group(1)
    return dict(ticker=member.split('_')[0], disclosure_id=member.split('_')[1],
                balance_date=datetime.strptime(period, '%d.%m.%Y').date().isoformat(),
                published_at=datetime.strptime(published, '%d.%m.%Y %H:%M:%S').isoformat() + '+03:00',
                paid_in_capital_try=capital * unit, table_value=capital, table_unit_multiplier=unit,
                source_member=member, source_sha256=sha256(raw).hexdigest(),
                source_url='https://kap.org.tr/tr/Bildirim/' + member.split('_')[1],
                independently_reported_nominal_value=False)


def action_observation(raw, idx):
    text = ' '.join(BeautifulSoup(raw, 'html.parser').get_text(' ', strip=True).split())
    published = re.search(r'Gönderim Tarihi (\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})', text).group(1)
    old = re.search(r'Mevcut Sermaye \(TL\) ([\d.]+) Ulaşılacak Sermaye \(TL\) ([\d.]+)', text)
    effective = re.search(r'Bedelsiz Pay Alma Hakkı Kullanım Başlangıç Tarihi (\d{2}\.\d{2}\.\d{4})', text)
    return dict(ticker='KLRHO', disclosure_id=idx,
                published_at=datetime.strptime(published, '%d.%m.%Y %H:%M:%S').isoformat() + '+03:00',
                effective_date=datetime.strptime(effective.group(1), '%d.%m.%Y').date().isoformat() if effective else None,
                event_type='BONUS_ISSUE', old_capital_try=int(old.group(1).replace('.', '')),
                new_capital_try=int(old.group(2).replace('.', '')),
                source_sha256=sha256(raw).hexdigest(), source_url=f'https://kap.org.tr/tr/Bildirim/{idx}',
                same_economic_event_key='KLRHO_BONUS_2023_06_06',
                source_role='CONFIRMED_EX_DATE' if effective else 'ANNOUNCEMENT_WITHOUT_EX_DATE')


def eligible_action(event, start, end, cutoff):
    """A decision date never substitutes for the ex-date or publication cutoff."""
    return (event['effective_date'] is not None
            and start < event['effective_date'] <= end
            and datetime.fromisoformat(event['published_at']) <= datetime.fromisoformat(cutoff))


def reconcile(start_capital, next_capital, events):
    seen = set()
    expected = Decimal(start_capital)
    for event in events:
        key = event['same_economic_event_key']
        if key in seen:
            raise ValueError('DUPLICATE_ECONOMIC_ACTION')
        seen.add(key)
        if expected != Decimal(event['old_capital_try']):
            raise ValueError('ACTION_OLD_CAPITAL_MISMATCH')
        expected *= Decimal(event['new_capital_try']) / Decimal(event['old_capital_try'])
    return dict(expected_capital_try=int(expected), observed_capital_try=next_capital,
                capital_reconciles=expected == Decimal(next_capital),
                action_completeness_proven=False, runtime_materialization_allowed=False)


def audit(output_dir=OUT):
    archive_meta = json.loads((output_dir / 'raw_archive.json').read_bytes())
    rawzip = (output_dir / archive_meta['path']).read_bytes()
    if sha256(rawzip).hexdigest() != archive_meta['sha256']:
        raise ValueError('RAW_ARCHIVE_HASH_MISMATCH')
    manifests = [json.loads((output_dir / name).read_bytes()) for name in
                 ['financial_source_manifest.json', 'network_source_manifest.json']]
    immutable = json.loads((ROOT / 'data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json').read_bytes())
    originals = {r['filename']: r['sha256'] for r in immutable['archives']}
    with zipfile.ZipFile(output_dir / archive_meta['path']) as z:
        if sorted(z.namelist()) != sorted(archive_meta['members']):
            raise ValueError('RAW_ARCHIVE_MEMBER_SET_MISMATCH')
        for manifest in manifests:
            for row in manifest:
                raw = z.read(row['path'])
                if sha256(raw).hexdigest() != row['sha256'] or len(raw) != row['size_bytes']:
                    raise ValueError('SOURCE_MEMBER_HASH_MISMATCH')
                if 'original_archive' in row:
                    if originals[row['original_archive']] != row['original_archive_sha256']:
                        raise ValueError('ORIGINAL_ARCHIVE_IDENTITY_MISMATCH')
        points = [capital_point(z.read(r['path']), r['path']) | {
            'original_archive': r['original_archive'], 'original_archive_sha256': r['original_archive_sha256']
        } for r in manifests[0]]
        actions = [action_observation(z.read(r['path']), r['path'].split('_')[1].split('.')[0]) for r in manifests[1]]
    previous = dated_audit()
    old_points = [dict(ticker=s['ticker'], balance_date=s['source_date'], published_at=s['published_at'],
                       paid_in_capital_try=s['source_shares_out'], disclosure_id=s['source_disclosure_id'],
                       source_sha256=s['raw_xls_sha256'], independently_reported_nominal_value=True)
                  for s in previous['dated_share_sources']]
    all_points = sorted(old_points + points, key=lambda p: (p['ticker'], p['balance_date']))
    confirmed = next(e for e in actions if e['effective_date'])
    links = []
    for ticker in ['INVES', 'KLRHO', 'ASGYO']:
        group = [p for p in all_points if p['ticker'] == ticker]
        for before, after in zip(group, group[1:]):
            events = [confirmed] if ticker == 'KLRHO' and before['balance_date'] < confirmed['effective_date'] <= after['balance_date'] else []
            links.append(dict(ticker=ticker, start_disclosure_id=before['disclosure_id'], next_disclosure_id=after['disclosure_id'],
                              start_date=before['balance_date'], next_date=after['balance_date'],
                              proven_events=[e['disclosure_id'] for e in events],
                              **reconcile(before['paid_in_capital_try'], after['paid_in_capital_try'], events)))
    cells = []
    for c in previous['cells']:
        next_point = min((p for p in all_points if p['ticker'] == c['ticker'] and p['balance_date'] > c['valuation_price_trade_date']),
                         key=lambda p: p['balance_date'])
        events = [e for e in [confirmed] if e['ticker'] == c['ticker'] and eligible_action(e, c['share_source_date'], c['valuation_price_trade_date'], c['cutoff_local'])]
        cells.append(dict(ticker=c['ticker'], signal_date=c['signal_date'], source_disclosure_id=c['source_disclosure_id'],
                          source_share_date=c['share_source_date'], valuation_date=c['valuation_price_trade_date'],
                          source_shares_out=c['shares_out'], proven_effective_actions_by_cutoff=[e['disclosure_id'] for e in events],
                          next_retrospective_balance=next_point,
                          next_balance_is_not_pit_input=datetime.fromisoformat(next_point['published_at']) > datetime.fromisoformat(c['cutoff_local']),
                          action_completeness_proven=False, status='EXPLICIT_REJECTION', reason='ACTION_COMPLETENESS_EVIDENCE_MISSING'))
    return dict(contract='P2_SHARE_CONTINUITY_RETROSPECTIVE_AUDIT_V1', profile='EXPERIMENTAL_RISK_ACCEPTED_5Y',
                raw_archive_sha256=archive_meta['sha256'], independently_captured_next_capital_points=points,
                actions=actions, balance_links=links, cells=cells, cell_count=len(cells),
                rejection_counts={'ACTION_COMPLETENESS_EVIDENCE_MISSING': len(cells)},
                negative_evidence_contract=dict(
                    repeated_query_partition_consistency=True,
                    capital_endpoint_reconciliation=all(link['capital_reconciles'] for link in links),
                    historical_complete_enumeration=False, nominal_value_continuity_independently_proven=False,
                    offsetting_intermediate_actions_excluded=False,
                    explanation='Equal paid-in-capital endpoints and a reconciled known bonus strengthen retrospective evidence but cannot prove all intermediate share changes. Current query consistency does not establish historical disclosure retention or as-of version coverage.'),
                runtime_action_bundles_created=0, authoritative_claim_allowed=False)


def main():
    first, second = encode(audit()), encode(audit())
    assert first == second
    (OUT / 'continuity_receipt.json').write_bytes(first)
    print(sha256(first).hexdigest(), '12 exact rejections; 8 retrospective balance links; no completeness promotion')


if __name__ == '__main__':
    main()
