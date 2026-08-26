from __future__ import annotations

"""Build a frozen monthly BIST100 membership table from audited source files."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.analytics.bist100_membership_pipeline import (
    reconstruct_bist100_memberships_with_ticker_lineage,
)
from src.analytics.bist100_nonperiodic_events import load_bist100_nonperiodic_events
from src.analytics.bist100_periodic_events import load_bist100_periodic_events_json
from src.analytics.ticker_lineage import TickerLineageResolver, load_ticker_code_changes_csv


class Bist100MembershipExportError(ValueError):
    pass


@dataclass(frozen=True)
class Bist100MembershipExport:
    frame: pd.DataFrame
    snapshot_date: str
    month_count: int
    expected_member_count: int
    periodic_event_count: int
    nonperiodic_event_count: int
    ticker_change_count: int


def _read_snapshot(path: str | Path, expected_count: int) -> tuple[tuple[str, ...], str]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required={"ticker","snapshot_date"}
    missing=required-set(frame.columns)
    if missing:
        raise Bist100MembershipExportError(f"snapshot kolonlari eksik: {sorted(missing)}")
    tickers=tuple(sorted(str(x).strip().upper() for x in frame["ticker"]))
    if any(not x for x in tickers) or len(tickers) != len(set(tickers)):
        raise Bist100MembershipExportError("snapshot bos/duplicate ticker iceriyor")
    if len(tickers) != int(expected_count):
        raise Bist100MembershipExportError(
            f"snapshot uye sayisi {len(tickers)}, beklenen {int(expected_count)}"
        )
    dates=sorted({str(x).strip() for x in frame["snapshot_date"] if str(x).strip()})
    if len(dates) != 1:
        raise Bist100MembershipExportError("snapshot_date tek ve dolu olmali")
    try:
        snap=pd.Timestamp(dates[0]).date().isoformat()
    except Exception as exc:
        raise Bist100MembershipExportError("snapshot_date gecerli tarih olmali") from exc
    return tickers,snap


def _read_signal_dates(path: str | Path, expected_months: int, index_code: str) -> pd.DataFrame:
    frame=pd.read_csv(path,dtype=str,keep_default_na=False)
    required={"month","signal_date","index_code"}
    missing=required-set(frame.columns)
    if missing:
        raise Bist100MembershipExportError(f"signal-date kolonlari eksik: {sorted(missing)}")
    frame=frame.loc[:,["month","signal_date","index_code"]].copy()
    frame["index_code"]=frame["index_code"].str.strip().str.upper()
    expected_index=str(index_code).strip().upper()
    if set(frame["index_code"]) != {expected_index}:
        raise Bist100MembershipExportError("signal-date index_code tek ve beklenen kod olmali")
    if len(frame) != int(expected_months):
        raise Bist100MembershipExportError(
            f"signal-date satir sayisi {len(frame)}, beklenen {int(expected_months)}"
        )
    if frame["month"].duplicated().any() or frame["signal_date"].duplicated().any():
        raise Bist100MembershipExportError("signal-date month/date duplicate olamaz")
    parsed=pd.to_datetime(frame["signal_date"],errors="raise")
    normalized=parsed.dt.strftime("%Y-%m-%d")
    if list(frame["month"]) != list(parsed.dt.strftime("%Y-%m")):
        raise Bist100MembershipExportError("month signal_date ayi ile eslesmiyor")
    frame["signal_date"]=normalized
    return frame.sort_values("signal_date").reset_index(drop=True)


def build_bist100_membership_export(
    *,
    snapshot_csv: str | Path,
    periodic_json: str | Path,
    nonperiodic_json: str | Path,
    ticker_lineage_csv: str | Path,
    signal_dates_csv: str | Path,
    expected_months: int = 60,
    expected_member_count: int = 100,
    index_code: str = "XU100",
) -> Bist100MembershipExport:
    members,snapshot_date=_read_snapshot(snapshot_csv,expected_member_count)
    signal_dates=_read_signal_dates(signal_dates_csv,expected_months,index_code)
    periodic=load_bist100_periodic_events_json(periodic_json)
    nonperiodic=load_bist100_nonperiodic_events(nonperiodic_json)
    ticker_changes=load_ticker_code_changes_csv(ticker_lineage_csv)
    lineage=TickerLineageResolver(ticker_changes)
    events=tuple(sorted((*periodic,*nonperiodic),key=lambda e:(e.effective_date,e.event_type,e.source_id)))
    history=reconstruct_bist100_memberships_with_ticker_lineage(
        current_members=members,
        snapshot_date=snapshot_date,
        constituent_events=events,
        ticker_lineage=lineage,
        target_dates=signal_dates["signal_date"],
        expected_count=expected_member_count,
    )

    month_by_day=dict(zip(signal_dates["signal_date"],signal_dates["month"]))
    rows=[]
    for day,members_on_day in history.memberships.items():
        day_text=day.isoformat()
        month=month_by_day.get(day_text)
        if month is None:
            raise Bist100MembershipExportError(f"history beklenmeyen target_date uretti: {day_text}")
        if len(members_on_day) != int(expected_member_count):
            raise Bist100MembershipExportError(
                f"{day_text} uye sayisi {len(members_on_day)}, beklenen {int(expected_member_count)}"
            )
        rows.extend(
            {"month":month,"signal_date":day_text,"index_code":str(index_code).upper(),"ticker":ticker}
            for ticker in members_on_day
        )
    out=pd.DataFrame(rows,columns=["month","signal_date","index_code","ticker"])
    expected_rows=int(expected_months)*int(expected_member_count)
    if len(out) != expected_rows:
        raise Bist100MembershipExportError(
            f"membership export {len(out)} satir, beklenen {expected_rows}"
        )
    if out.duplicated(["signal_date","ticker"]).any():
        raise Bist100MembershipExportError("membership export duplicate signal_date+ticker iceriyor")
    out=out.sort_values(["signal_date","ticker"]).reset_index(drop=True)
    return Bist100MembershipExport(
        frame=out,
        snapshot_date=snapshot_date,
        month_count=int(expected_months),
        expected_member_count=int(expected_member_count),
        periodic_event_count=len(periodic),
        nonperiodic_event_count=len(nonperiodic),
        ticker_change_count=len(ticker_changes),
    )
