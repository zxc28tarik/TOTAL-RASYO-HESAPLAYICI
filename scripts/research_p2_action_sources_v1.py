"""Capture bounded official KAP action research; never assert PIT completeness."""
from pathlib import Path
from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import argparse
import calendar
import json
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/backtest_sources/p2_action_research_v1'
URL='https://kap.org.tr/tr/api/disclosure/members/byCriteria'
SCOPES={
'INVES':('8acae2c47d3bee50017f0bbbbbfa3c0e','2022-03-31','2022-08-31'),
'KLRHO':('4028e4a1415f4d990141600b4b5331e2','2022-09-30','2023-06-30'),
'ASGYO':('8acae2c562329bd1016464c3796e08a0','2023-09-30','2024-02-29')}
def encode(v): return (json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf8')
def query(item):
 ticker,start,end,suffix=item
 payload=dict(fromDate=start,toDate=end,memberType='',mkkMemberOidList=[SCOPES[ticker][0]],inactiveMkkMemberOidList=[],disclosureClass='',subjectList=[],isLate='',mainSector='',sector='',subSector='',marketOid='',index='',bdkReview='',bdkMemberOidList=[],year='',term='',ruleType='',period='',fromSrc=False,srcCategory='',disclosureIndexList=[])
 prefix=f'{ticker}_{start}_{end}_{suffix}'
 request_bytes=encode(payload)
 (OUT/(prefix+'.request.json')).write_bytes(request_bytes)
 response=requests.post(URL,data=request_bytes,headers={'Content-Type':'application/json','Accept-Language':'tr'},timeout=45)
 response.raise_for_status()
 raw=response.content
 (OUT/(prefix+'.response.json')).write_bytes(raw)
 rows=response.json()
 if not isinstance(rows,list): raise ValueError('Expected complete-array API response')
 return dict(ticker=ticker,start=start,end=end,suffix=suffix,request_file=prefix+'.request.json',response_file=prefix+'.response.json',request_sha256=sha256(request_bytes).hexdigest(),response_sha256=sha256(raw).hexdigest(),row_count=len(rows),ids=sorted(r['disclosureIndex'] for r in rows),response_http_date=response.headers.get('Date'),rows=rows)
def main():
 global OUT
 parser=argparse.ArgumentParser()
 parser.add_argument('--output-dir',type=Path,required=True,help='New directory; preserved captures are never overwritten')
 args=parser.parse_args();OUT=args.output_dir.resolve()
 OUT.mkdir(parents=True,exist_ok=False)
 tasks=[]
 for ticker,(_,start,end) in SCOPES.items():
  tasks.extend([(ticker,start,end,'full_a'),(ticker,start,end,'full_b')])
  current=date.fromisoformat(start);last=date.fromisoformat(end)
  while current<=last:
   stop=min(date(current.year,current.month,calendar.monthrange(current.year,current.month)[1]),last)
   tasks.append((ticker,current.isoformat(),stop.isoformat(),'month'))
   current=date(stop.year+(stop.month==12),stop.month%12+1,1)
 results=list(ThreadPoolExecutor(max_workers=4).map(query,tasks))
 comparisons=[]
 for ticker in SCOPES:
  group=[r for r in results if r['ticker']==ticker]
  a=next(r for r in group if r['suffix']=='full_a');b=next(r for r in group if r['suffix']=='full_b')
  union=sorted(set(i for r in group if r['suffix']=='month' for i in r['ids']))
  comparisons.append(dict(ticker=ticker,full_repeat_bytes_identical=a['response_sha256']==b['response_sha256'],full_repeat_ids_identical=a['ids']==b['ids'],partition_union_equals_full=union==a['ids'],full_count=a['row_count'],partition_unique_count=len(union),action_rows=[r for r in a['rows'] if r.get('disclosureType')=='CA'],historical_pit_completeness_proven=False))
 receipt=dict(contract='P2_BOUNDED_OFFICIAL_ACTION_QUERY_RESEARCH_V1',fetched_at=datetime.now(timezone.utc).isoformat(),source_url=URL,request_method='POST',client_body_source='15nftaedmwt2t.js',client_route_source='0.71jgxax-7yc.js',queries=[{k:v for k,v in r.items() if k!='rows'} for r in results],comparisons=comparisons,notes=['Fetched_at is acquisition time, never historical publication time.','Partition equality checks response consistency, not historical version completeness.','Original candidate993 catalog is unavailable; interval candidates are separately reconstructed from preserved raw reports.'])
 (OUT/'query_research_receipt.json').write_bytes(encode(receipt))
 print(json.dumps(comparisons,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
