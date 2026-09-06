"""Verify real P2 source recovery and replay explicit missing-action rejections."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,date
import json,re,zipfile
from bs4 import BeautifulSoup
from src.analytics.historical_valuation_price_supplement import CATALOG,materialize_historical_price_level_v2
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/backtest_sources/p2_action_research_v1'
def encode(v):return (json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def read(name):
 p=OUT/name
 if p.exists():return p.read_bytes()
 with zipfile.ZipFile(OUT/'raw_sources.zip') as z:return z.read(name)
SOURCES=[('INVES',1027019,'2022-03-31',187500000,25),('INVES',1052635,'2022-06-30',187500000,26),('KLRHO',1079063,'2022-09-30',650000000,75),('KLRHO',1124175,'2022-12-31',650000000,83),('KLRHO',1150263,'2023-03-31',650000000,76),('ASGYO',1210998,'2023-09-30',659000000,34)]
def audit():
 metadata=json.loads((OUT/'raw_source_manifest.json').read_bytes());assert sha256((OUT/metadata['archive_file']).read_bytes()).hexdigest()==metadata['archive_sha256']
 nominal=json.loads((OUT/'nominal_share_extraction.json').read_bytes());nominal_by_name={r['source']:r for r in nominal}
 byname={r['path']:r for r in metadata['members']}
 for r in metadata['members']:
  raw=read(r['path']);assert sha256(raw).hexdigest()==r['sha256'];assert len(raw)==r['size_bytes']
 sources=[]
 for ticker,idx,period,shares,pdfpage in SOURCES:
  name=next(n for n in byname if n.endswith('.xls') and f'_{idx}_' in n)
  raw=read(name);node=BeautifulSoup(raw,'html.parser');text=' '.join(node.get_text(' ',strip=True).split());published=datetime.strptime(re.search(r'Gönderim Tarihi:(.*?) Bildirim Tipi',text).group(1),'%d.%m.%Y %H:%M:%S').isoformat()+'+03:00'
  capitalnode=node.find(string=lambda value: value and value.strip()=="Ödenmiş Sermaye").parent;ancestor=capitalnode
  for _ in range(6):ancestor=ancestor.parent
  values=[n["title"] for n in ancestor.find_all(attrs={"title":True})];amount=int(values[0]);scale=1000 if 'Sunum Para Birimi 1.000 TL' in text else 1
  assert amount*scale==shares
  assert 'Cari Dönem '+datetime.strptime(period,'%Y-%m-%d').strftime('%d.%m.%Y') in text
  pdfname=f'kap_{idx}_attachment_0.pdf';pdf=read(pdfname)
  assert pdf[:23]==bytes.fromhex('aced0005757200025b42acf317f8060854e00200007870')
  assert int.from_bytes(pdf[23:27],'big')==len(pdf)-27 and pdf[27:32]==b'%PDF-'
  extracted=nominal_by_name[pdfname];assert extracted['raw_source_sha256']==sha256(pdf).hexdigest()
  assert any(candidate['page']==pdfpage for candidate in extracted['nominal_share_candidates'])
  sources.append(dict(ticker=ticker,source_disclosure_id=str(idx),source_url=f'https://kap.org.tr/tr/Bildirim/{idx}',source_date=period,published_at=published,source_shares_out=shares,nominal_value_try=1,capital_table_raw_value=amount,capital_table_unit_multiplier=scale,raw_xls=name,raw_xls_sha256=sha256(raw).hexdigest(),nominal_definition_raw_pdf=pdfname,nominal_definition_pdf_page_one_based=pdfpage,nominal_definition_raw_sha256=sha256(pdf).hexdigest(),nominal_definition_decoded_pdf_sha256=sha256(pdf[27:]).hexdigest(),version_policy='EXPERIMENTAL_VISIBLE_VERSION_ONLY'))
 rows=[]
 for price in CATALOG:
  candidates=[s for s in sources if s['ticker']==price.ticker and datetime.fromisoformat(s['published_at'])<=price.cutoff_local]
  source=max(candidates,key=lambda s:(s['source_date'],s['published_at']))
  row=dict(ticker=price.ticker,signal_date=str(price.signal_date),cutoff_local=price.cutoff_local.isoformat(),share_source_date=source['source_date'],valuation_price_trade_date=str(price.trade_date),source_disclosure_id=source['source_disclosure_id'],shares_out=source['source_shares_out'],source_published_at=source['published_at'],original_993_source_selection_identity_proven=False,action_completeness_proven=False)
  try:
   materialize_historical_price_level_v2(evidence=price,analysis_at=price.cutoff_local,raw_archive_bytes=(ROOT/'data/backtest_sources/p2_raw_close_pit_v2'/Path(price.archive_url).name).read_bytes(),shares_out=source['source_shares_out'],shares_basis_date=date.fromisoformat(source['source_date']),action_bundle=None)
   raise AssertionError('Missing proof must reject')
  except ValueError as exc:row.update(status='REJECTED',reason=str(exc));assert row['reason']=='ACTION_COMPLETENESS_EVIDENCE_MISSING'
  rows.append(row)
 query=json.loads((OUT/'query_research_receipt.json').read_bytes())
 for item in query['queries']:
  assert sha256(read(item['request_file'])).hexdigest()==item['request_sha256']
  response=read(item['response_file']);assert sha256(response).hexdigest()==item['response_sha256']
  observed=json.loads(response);assert len(observed)==item['row_count']
  assert sorted(r['disclosureIndex'] for r in observed)==item['ids']
 return dict(contract='P2_REAL_DATED_SHARE_RESEARCH_AND_REPLAY_V1',status='BLOCKED_ACTION_COMPLETENESS',dated_share_sources=sources,cells=rows,row_count=len(rows),raw_sources_verified=len(metadata['members']),nominal_extraction_sha256=sha256((OUT/'nominal_share_extraction.json').read_bytes()).hexdigest(),parser_mapping_version='P2_DATED_SHARES_HTML_CAPITAL_AND_REVIEWED_PDF_V1',query_consistency=[{k:r[k] for k in ['ticker','full_count','partition_unique_count','full_repeat_bytes_identical','partition_union_equals_full']} for r in query['comparisons']],rejection_counts={'ACTION_COMPLETENESS_EVIDENCE_MISSING':len(rows)},blockers=['Official monthly CA calendar returns empty for June2023 despite known KLRHO event #1158799; empty calendars cannot establish completeness.','Detailed byCriteria query repeat bytes and monthly union agree, but no historical version/deletion or historical as-of enumeration guarantee was obtained.','Query captures acquired in2026 have no historical publication timestamp; cannot backdate them to cutoff.','Announcements before the source share date may become effective later; long-range lookback returnedHTTP500 and prior announcement coverage is not established.','Original993 candidate source rows and exact original catalog bytes remain unavailable.'],runtime_action_bundles_created=0,authoritative_claim_allowed=False)
def main():
 a=encode(audit());b=encode(audit());assert a==b
 (OUT/'dated_share_replay_receipt.json').write_bytes(a)
 print('12 real dated-share candidates;12 explicit missing-proof rejections;repeat bytes equal',sha256(a).hexdigest())
if __name__=='__main__':main()
