"""Verify one recovered real financial correction pair, without global completeness."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/backtest_sources/p7_version_research_v1'


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def select_within_recovered_pair(versions, cutoff):
    selected = [v for v in versions if datetime.fromisoformat(v['published_at']) <= datetime.fromisoformat(cutoff)]
    if not selected:
        raise ValueError('NO_PUBLISHED_VERSION_WITHIN_RECOVERED_PAIR')
    return max(selected, key=lambda v: v['published_at'])


def audit(output_dir=OUT):
    meta = json.loads((output_dir/'raw_archive.json').read_bytes())
    if sha256((output_dir/meta['path']).read_bytes()).hexdigest() != meta['sha256']:
        raise ValueError('DETAIL_ARCHIVE_HASH_MISMATCH')
    query = json.loads((output_dir/'query_receipt.json').read_bytes())
    for kind in ['request', 'response']:
        if sha256((output_dir/f'financial_march2023.{kind}.json').read_bytes()).hexdigest() != query[f'{kind}_sha256']:
            raise ValueError('QUERY_HASH_MISMATCH')
    rows = json.loads((output_dir/'financial_march2023.response.json').read_bytes())
    byid = {str(row['disclosureIndex']): row for row in rows}
    sources = json.loads((output_dir/'detail_sources.json').read_bytes())
    versions = []
    facts = {}
    with zipfile.ZipFile(output_dir/meta['path']) as z:
        if sorted(z.namelist()) != sorted(meta['members']):
            raise ValueError('DETAIL_MEMBER_SET_MISMATCH')
        for source in sources:
            if sha256(z.read(source['path'])).hexdigest() != source['sha256']:
                raise ValueError('DETAIL_MEMBER_HASH_MISMATCH')
        newer = BeautifulSoup(z.read('kap_1126845.html'), 'html.parser')
        link = newer.find('a', href='https://kap.org.tr/tr/Bildirim/1122417')
        if link is None or link.get_text(' ', strip=True) != 'Düzeltilmiş Bildirim':
            raise ValueError('OFFICIAL_CORRECTION_RELATION_MISSING')
        for idx in ['1122417', '1126845']:
            raw = z.read(f'kap_{idx}.xls')
            soup = BeautifulSoup(raw, 'html.parser')
            text = ' '.join(soup.get_text(' ', strip=True).split())
            stamp = re.search(r'Gönderim Tarihi:(.*?) Bildirim Tipi', text).group(1)
            if stamp != byid[idx]['publishDate']:
                raise ValueError('QUERY_DETAIL_PUBLICATION_MISMATCH')
            if 'Yıl:2022 Periyot:4' not in text or byid[idx]['stockCodes'] != 'KORTS':
                raise ValueError('FINANCIAL_PERIOD_IDENTITY_MISMATCH')
            if 'Sunum Para Birimi 1.000 TL' not in text:
                raise ValueError('FINANCIAL_UNIT_MISMATCH')
            nodes = soup.find_all(attrs={'title': True})
            facts[idx] = [(n['title'], ' '.join(n.find_parent('tr').get_text(' ', strip=True).split())) for n in nodes]
            versions.append(dict(disclosure_id=idx, ticker='KORTS', financial_period='2022-12-31',
                                 published_at=datetime.strptime(stamp, '%d.%m.%Y %H:%M:%S').isoformat()+'+03:00',
                                 query_modify_status=byid[idx]['modifyStatus'], table_unit='1000_TRY', source_sha256=sha256(raw).hexdigest(),
                                 source_url=f'https://kap.org.tr/tr/api/notification/export/excel/{idx}'))
    if len(facts['1122417']) != len(facts['1126845']):
        raise ValueError('PAIR_FACT_SHAPE_MISMATCH')
    differences = [dict(numeric_slot_zero_based=i, original_value=a[0], revised_value=b[0],
                        original_row_text=a[1], revised_row_text=b[1])
                   for i, (a, b) in enumerate(zip(facts['1122417'], facts['1126845'], strict=True)) if a[0] != b[0]]
    return dict(contract='P7_RECOVERED_FINANCIAL_CORRECTION_PAIR_V1', status='POSITIVE_PAIR_RECOVERY_GLOBAL_ENUMERATION_STILL_BLOCKED',
                raw_details_sha256=meta['sha256'], query_response_sha256=query['response_sha256'],
                query_scope='2023-03-01 through 2023-03-31, FR, all members', query_rows=len(rows),
                query_rows_with_modify_status=sum(bool(r.get('modifyStatus')) for r in rows),
                versions=versions, correction_relation=dict(newer='1126845', older='1122417', label='Düzeltilmiş Bildirim'),
                numeric_slots_compared=len(facts['1122417']), changed_numeric_slots=differences,
                demonstrated_cutoffs=[dict(cutoff=cutoff, selected_disclosure_id=select_within_recovered_pair(versions, cutoff)['disclosure_id'])
                                      for cutoff in ['2023-03-10T18:10:00+03:00', '2023-03-22T18:10:00+03:00']],
                historical_global_version_enumeration_proven=False, authoritative_performance_allowed=False,
                limitations=['A current bounded query with correction metadata is not a retention or exhaustive historical-version guarantee.',
                             'One recoverable explicit correction pair does not enumerate versions for every selected report across 6000 cells.',
                             'Numeric evidence proves economically material differences; replacing the earlier parent-profit fact with the later revision would leak information.',
                             'The selector demonstrates ordering within this recovered pair only; it cannot certify an undiscovered earlier or intermediate version does not exist.'])


def main():
    one, two = encode(audit()), encode(audit())
    assert one == two
    (OUT/'version_pair_receipt.json').write_bytes(one)
    print(sha256(one).hexdigest())


if __name__ == '__main__':
    main()
