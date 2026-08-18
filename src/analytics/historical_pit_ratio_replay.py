from __future__ import annotations

"""PIT-safe ratio foundation for historical Total Rasyo replay.

The legacy ``ratios_calc.run_ratios_calc`` path is intentionally not used here.
For every historical ``analysis_at`` this bridge executes two explicit siblings:

1. ``company_ratio_pipeline`` -> CORE ratios only, from company_metrics_quarterly
   versions visible at analysis_at;
2. ``val_ratios_pit`` -> the six VAL ratios only, with its own publication/t0/
   market-cutoff contract.

This module does not calculate RSC/module_scores or run sector M2 engines.  Its
purpose is narrower: make it impossible for a historical replay caller to prepare
CORE while silently forgetting the PIT valuation wing, or to fall back to the
legacy non-PIT valuation path.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

import pandas as pd

from src.analytics.company_ratio_pipeline import run_company_core_ratios_asof
from src.analytics.val_ratios_pit import VAL_RATIO_NAMES, run_val_ratios_asof


class HistoricalPitRatioReplayError(ValueError):
    pass


@dataclass(frozen=True)
class HistoricalPitRatioReplayResult:
    analysis_at: datetime
    tickers: tuple[str, ...]
    core_ratios: pd.DataFrame
    val_ratios: pd.DataFrame

    @property
    def combined_ratios(self) -> pd.DataFrame:
        frames = [x for x in (self.core_ratios, self.val_ratios) if x is not None and not x.empty]
        if not frames:
            return pd.DataFrame(columns=[
                "ticker", "period_end", "version_tag", "ratio_name", "ratio_value", "is_na"
            ])
        return pd.concat(frames, ignore_index=True).sort_values(
            ["ticker", "period_end", "version_tag", "ratio_name"]
        ).reset_index(drop=True)


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalPitRatioReplayError("analysis_at timezone-aware datetime olmali")
    return value


def _tickers_from_routing(routing: Mapping[str, str]) -> tuple[str, ...]:
    if not isinstance(routing, Mapping) or not routing:
        raise HistoricalPitRatioReplayError("routing bos olmayan mapping olmali")
    out=[]
    for raw in routing:
        if not isinstance(raw, str) or not raw.strip():
            raise HistoricalPitRatioReplayError("routing bos ticker iceriyor")
        ticker=raw.strip().upper()
        if ticker in out:
            raise HistoricalPitRatioReplayError(f"duplicate normalized ticker: {ticker}")
        out.append(ticker)
    return tuple(sorted(out))


def _profile(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPitRatioReplayError("derivation_profile dolu metin olmali")
    return value.strip()


def _version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HistoricalPitRatioReplayError("derivation_version pozitif int olmali")
    return value


def _validate_frame(frame: pd.DataFrame, *, name: str, tickers: tuple[str, ...]) -> pd.DataFrame:
    required={"ticker","period_end","version_tag","ratio_name","ratio_value","is_na"}
    if frame is None:
        raise HistoricalPitRatioReplayError(f"{name} None donemez")
    missing=required-set(frame.columns)
    if missing:
        raise HistoricalPitRatioReplayError(f"{name} missing columns: {sorted(missing)}")
    out=frame.copy()
    if out.empty:
        return out
    out["ticker"]=out["ticker"].astype(str).str.strip().str.upper()
    foreign=sorted(set(out["ticker"])-set(tickers))
    if foreign:
        raise HistoricalPitRatioReplayError(f"{name} routing disi ticker uretti: {foreign}")
    if out.duplicated(["ticker","period_end","version_tag","ratio_name"]).any():
        raise HistoricalPitRatioReplayError(f"{name} duplicate ratio key uretti")
    return out


def run_historical_pit_ratio_foundation(
    conn: Any,
    *,
    analysis_at: datetime,
    routing: Mapping[str, str],
    ratios_json_path: str,
    derivation_profile: str,
    derivation_version: int,
    since_period_end: Optional[object] = None,
    persist: bool = True,
    core_runner: Callable[..., pd.DataFrame] = run_company_core_ratios_asof,
    val_runner: Callable[..., pd.DataFrame] = run_val_ratios_asof,
) -> HistoricalPitRatioReplayResult:
    analysis=_aware(analysis_at)
    tickers=_tickers_from_routing(routing)
    profile=_profile(derivation_profile)
    version=_version(derivation_version)
    if type(persist) is not bool:
        raise HistoricalPitRatioReplayError("persist Python bool olmali")
    if not isinstance(ratios_json_path, str) or not ratios_json_path.strip():
        raise HistoricalPitRatioReplayError("ratios_json_path dolu metin olmali")

    core=core_runner(
        conn,
        analysis_at=analysis,
        ratios_json_path=ratios_json_path,
        tickers=tickers,
        since_period_end=since_period_end,
        persist=persist,
    )
    core=_validate_frame(core,name="CORE",tickers=tickers)
    leaked=sorted(set(core.get("ratio_name",pd.Series(dtype=str)).astype(str)) & set(VAL_RATIO_NAMES))
    if leaked:
        raise HistoricalPitRatioReplayError(f"CORE pipeline VAL orani sizdirdi: {leaked}")

    val=val_runner(
        conn,
        analysis_at=analysis,
        tickers=tickers,
        derivation_profile=profile,
        derivation_version=version,
        persist=persist,
    )
    val=_validate_frame(val,name="VAL",tickers=tickers)
    names=set(val.get("ratio_name",pd.Series(dtype=str)).astype(str))
    unexpected=sorted(names-set(VAL_RATIO_NAMES))
    if unexpected:
        raise HistoricalPitRatioReplayError(f"VAL pipeline beklenmeyen oran uretti: {unexpected}")

    return HistoricalPitRatioReplayResult(
        analysis_at=analysis,
        tickers=tickers,
        core_ratios=core.reset_index(drop=True),
        val_ratios=val.reset_index(drop=True),
    )
