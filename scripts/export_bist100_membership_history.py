#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.analytics.bist100_membership_export import build_bist100_membership_export


def _sha256(path: str | Path) -> str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    p=argparse.ArgumentParser(description='Export audited monthly BIST100 membership history')
    p.add_argument('--snapshot-csv',required=True)
    p.add_argument('--periodic-json',required=True)
    p.add_argument('--nonperiodic-json',required=True)
    p.add_argument('--ticker-lineage-csv',required=True)
    p.add_argument('--signal-dates-csv',required=True)
    p.add_argument('--out-csv',required=True)
    p.add_argument('--manifest-json',required=True)
    p.add_argument('--expected-months',type=int,default=60)
    p.add_argument('--expected-member-count',type=int,default=100)
    p.add_argument('--index-code',default='XU100')
    args=p.parse_args()

    result=build_bist100_membership_export(
        snapshot_csv=args.snapshot_csv,
        periodic_json=args.periodic_json,
        nonperiodic_json=args.nonperiodic_json,
        ticker_lineage_csv=args.ticker_lineage_csv,
        signal_dates_csv=args.signal_dates_csv,
        expected_months=args.expected_months,
        expected_member_count=args.expected_member_count,
        index_code=args.index_code,
    )
    out=Path(args.out_csv)
    out.parent.mkdir(parents=True,exist_ok=True)
    result.frame.to_csv(out,index=False,lineterminator='\n')
    manifest={
        'contract':'V24_BIST100_MONTHLY_MEMBERSHIP_EXPORT_V1',
        'snapshot_date':result.snapshot_date,
        'month_count':result.month_count,
        'expected_member_count':result.expected_member_count,
        'row_count':len(result.frame),
        'periodic_event_count':result.periodic_event_count,
        'nonperiodic_event_count':result.nonperiodic_event_count,
        'ticker_change_count':result.ticker_change_count,
        'index_code':str(args.index_code).upper(),
        'inputs':{
            'snapshot_csv':{'path':args.snapshot_csv,'sha256':_sha256(args.snapshot_csv)},
            'periodic_json':{'path':args.periodic_json,'sha256':_sha256(args.periodic_json)},
            'nonperiodic_json':{'path':args.nonperiodic_json,'sha256':_sha256(args.nonperiodic_json)},
            'ticker_lineage_csv':{'path':args.ticker_lineage_csv,'sha256':_sha256(args.ticker_lineage_csv)},
            'signal_dates_csv':{'path':args.signal_dates_csv,'sha256':_sha256(args.signal_dates_csv)},
        },
        'output_csv_sha256':_sha256(out),
    }
    manifest_path=Path(args.manifest_json)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
