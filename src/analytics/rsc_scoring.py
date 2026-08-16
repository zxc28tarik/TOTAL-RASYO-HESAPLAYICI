from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.ingest.sector_routing import SUPPORTED_SECTOR_FAMILIES


@dataclass(frozen=True)
class RatioMeta:
    pillar: str
    core_or_val: str
    ratio_type: str
    band: Optional[dict] = None


def load_ratio_meta(ratios_json_path: str) -> Dict[str, RatioMeta]:
    cfg = json.loads(Path(ratios_json_path).read_text(encoding="utf-8"))
    return {rn: RatioMeta(s["pillar"], s["core_or_val"], s["type"], s.get("band")) for rn, s in cfg["ratios"].items()}


def load_sector_config(sectors_json_path: Optional[str]) -> Tuple[Dict[str, str], Dict[str, dict]]:
    """Load index_to_group mapping and sector policies from sectors.json."""
    if not sectors_json_path:
        return {}, {}
    cfg = json.loads(Path(sectors_json_path).read_text(encoding="utf-8"))
    return dict(cfg.get("index_to_group", {})), dict(cfg.get("sector_policies", {}))


def build_sector_group_map(universe_df: pd.DataFrame, index_to_group: Dict[str, str]) -> Dict[str, str]:
    """Map ticker -> sector group (BANK / HOLDING / NONFIN ...).

    Priority: explicit supported sector_code, then sector_index_code mapping,
    then the '*' default, then 'NONFIN'.
    """
    default_group = index_to_group.get("*", "NONFIN")
    out: Dict[str, str] = {}
    if universe_df is None or universe_df.empty:
        return out
    has_sec_code = "sector_code" in universe_df.columns
    for r in universe_df.itertuples(index=False):
        ticker = str(r.ticker)
        idx_code = str(getattr(r, "sector_index_code", "") or "").upper().strip()
        sec_code = (
            str(getattr(r, "sector_code", "") or "").upper().strip()
            if has_sec_code else ""
        )
        if sec_code in SUPPORTED_SECTOR_FAMILIES:
            out[ticker] = sec_code
            continue
        if idx_code and idx_code in index_to_group:
            out[ticker] = str(index_to_group[idx_code])
            continue
        out[ticker] = str(default_group)
    return out


def winsorize_series(x: pd.Series, p: float = 0.01) -> pd.Series:
    if x.dropna().empty:
        return x
    return x.clip(x.quantile(p), x.quantile(1.0 - p))


def safe_zscore(x: pd.Series) -> pd.Series:
    mu, sd = x.mean(), x.std(ddof=1)
    if sd is None or sd == 0 or not np.isfinite(sd):
        return pd.Series([0.0] * len(x), index=x.index)
    return (x - mu) / sd


def pct_rank(x: pd.Series) -> pd.Series:
    return x.rank(pct=True, method="average")


def sigmoid(z: pd.Series) -> pd.Series:
    return 1.0 / (1.0 + np.exp(-z))


def to_score_1_10(p: pd.Series) -> pd.Series:
    return 1.0 + 9.0 * p


def trend_bonus(values_last8: List[Optional[float]]) -> float:
    xs = [v for v in values_last8 if v is not None and np.isfinite(v)]
    if len(xs) < 4:
        return 0.0
    y = np.array(xs, dtype=float)
    x = np.arange(len(y), dtype=float)
    x = x - x.mean()
    denom = float((x * x).sum())
    if denom == 0:
        return 0.0
    slope = float((x * (y - y.mean())).sum() / denom)
    return 0.0 if not np.isfinite(slope) else float(np.tanh(slope) * 0.5)


def apply_ratio_direction(meta: RatioMeta, v: pd.Series) -> pd.Series:
    t = meta.ratio_type.upper()
    if t == "LOWER_BETTER":
        return -v
    if t == "BAND":
        if not meta.band:
            return -abs(v)
        center = (float(meta.band.get("low", 0)) + float(meta.band.get("high", 0))) / 2.0
        return -(v - center).abs()
    return v


def _sector_neutral_percentiles(
    x: pd.Series,
    group_of: Dict[str, str],
    min_group_size: int,
    winsor_p: float,
    meta: RatioMeta,
) -> pd.Series:
    """Percentile rank within sector group; small groups fall back to the full universe.

    Winsorization and direction handling are applied inside each ranking pool so
    that a bank's leverage is compared against other banks, not against industrials.
    """
    def _pool_pct(pool: pd.Series) -> pd.Series:
        w = winsorize_series(pool, p=winsor_p)
        w = apply_ratio_direction(meta, w)
        return pct_rank(w)

    groups = pd.Series({t: group_of.get(str(t), "NONFIN") for t in x.index})
    counts = groups.value_counts()
    big_groups = set(counts[counts >= min_group_size].index)

    global_pct = _pool_pct(x)
    out = global_pct.copy()
    for g in big_groups:
        members = groups[groups == g].index
        out.loc[members] = _pool_pct(x.loc[members])
    return out


def _policy_weight(rn: str, meta: RatioMeta, policy: Optional[dict]) -> float:
    """Aggregation weight of a CORE ratio for a given sector policy."""
    if not policy:
        return 1.0
    pw = policy.get("pillar_weights_override", {}) or {}
    rw = policy.get("ratio_weights_override", {}) or {}
    pillar_w = float(pw.get(meta.pillar, 1.0)) if pw else 1.0
    ratio_w = float(rw.get(rn, 1.0)) if rw else 1.0
    return max(pillar_w * ratio_w, 0.0)


def score_quarter(
    df_ratios: pd.DataFrame,
    ratios_json_path: str,
    allowed_ratios: Optional[List[str]] = None,
    winsor_p: float = 0.01,
    sector_group_map: Optional[Dict[str, str]] = None,
    sector_policies: Optional[Dict[str, dict]] = None,
    min_group_size: int = 5,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Score ratios cross-sectionally per (period_end, version_tag).

    Sector-neutral mode (sector_group_map given):
    - percentile ranks are computed within sector groups (>= min_group_size members),
    - sector policies restrict which CORE ratios count for each group,
    - pillar/ratio weight overrides shape the CORE aggregation,
    - VAL ratios are always scored for every ticker (valuation axis is policy-free).
    Without sector inputs the function behaves like the legacy pooled version.
    """
    meta = load_ratio_meta(ratios_json_path)
    group_of = sector_group_map or {}
    policies = sector_policies or {}
    use_sectors = bool(group_of)

    d = df_ratios.copy()
    if allowed_ratios is not None:
        d = d[d["ratio_name"].isin(allowed_ratios)].copy()
    d["ratio_value"] = pd.to_numeric(d["ratio_value"], errors="coerce")
    d["is_na"] = d["is_na"].fillna(False).astype(bool)

    scores_rows, summary_rows = [], []
    for (pe, vt), gq in d.groupby(["period_end", "version_tag"], sort=False):
        scored_ratio_frames = []
        for rn, gr in gq.groupby("ratio_name", sort=False):
            m = meta.get(rn)
            if m is None:
                continue
            x = gr.set_index("ticker")["ratio_value"]

            # Policy filter: CORE ratios not allowed for a group are dropped for
            # that group's tickers. VAL ratios are never filtered by policy.
            if use_sectors and policies and m.core_or_val == "CORE":
                keep_idx = []
                for t in x.index:
                    pol = policies.get(group_of.get(str(t), "NONFIN"))
                    if pol is None:
                        keep_idx.append(t)
                        continue
                    allowed = pol.get("allowed_ratios")
                    if not allowed or rn in allowed:
                        keep_idx.append(t)
                x = x.loc[keep_idx]
                gr = gr[gr["ticker"].isin(keep_idx)]
            if x.empty:
                continue

            if use_sectors:
                pcts = _sector_neutral_percentiles(x, group_of, min_group_size, winsor_p, m)
            else:
                xw = winsorize_series(x, p=winsor_p)
                xw = apply_ratio_direction(m, xw)
                pcts = pct_rank(xw)

            s10 = to_score_1_10(pcts)
            tmp = pd.DataFrame({
                "ticker": pcts.index,
                "period_end": pe,
                "version_tag": vt,
                "ratio_name": rn,
                "pillar": m.pillar,
                "score_1_10": s10.values,
                "level_percentile": pcts.values,
                "trend_bonus": 0.0,
                "is_na": gr.set_index("ticker")["is_na"].reindex(pcts.index).fillna(False).values
            })
            scored_ratio_frames.append(tmp)
        if not scored_ratio_frames:
            continue
        scored = pd.concat(scored_ratio_frames, axis=0, ignore_index=True)
        scores_rows.append(scored)

        core = scored[scored["ratio_name"].map(lambda x: meta.get(x).core_or_val if meta.get(x) else None) == "CORE"].copy()
        if core.empty:
            continue

        # Weighted CORE aggregation using sector policies.
        if use_sectors and policies:
            core["agg_w"] = [
                _policy_weight(rn_, meta[rn_], policies.get(group_of.get(str(t_), "NONFIN")))
                for rn_, t_ in zip(core["ratio_name"], core["ticker"])
            ]
        else:
            core["agg_w"] = 1.0
        core = core[core["agg_w"] > 0]
        if core.empty:
            continue

        def _wmean(g: pd.DataFrame) -> float:
            w = g["agg_w"].to_numpy(dtype=float)
            s = g["score_1_10"].to_numpy(dtype=float)
            sw = float(w.sum())
            return float((w * s).sum() / sw) if sw > 0 else float(np.mean(s))

        agg_mean = core.groupby("ticker").apply(_wmean)
        agg_std = core.groupby("ticker")["score_1_10"].std(ddof=1)
        good = core[core["score_1_10"] >= 8.0].groupby("ticker")["score_1_10"].count()
        rsc_core_norm = ((agg_mean - 1.0) / 9.0).clip(0.0, 1.0)

        # Valuation axis: cheapness from LOWER_BETTER VAL ratios (PE, PB, PS, EV/EBIT).
        # Size-style VAL proxies (HIGHER_BETTER) are excluded on purpose.
        val = scored[scored["ratio_name"].map(
            lambda x: meta.get(x) is not None and meta[x].core_or_val == "VAL" and meta[x].ratio_type.upper() == "LOWER_BETTER"
        )].copy()
        if not val.empty:
            val_mean = val.groupby("ticker")["score_1_10"].mean()
            rsc_val_norm = ((val_mean - 1.0) / 9.0).clip(0.0, 1.0)
        else:
            rsc_val_norm = pd.Series(dtype=float)

        out = pd.DataFrame({
            "ticker": agg_mean.index,
            "period_end": pe,
            "version_tag": vt,
            "rsc_core_norm": rsc_core_norm.values,
            "rsc_val_norm": rsc_val_norm.reindex(agg_mean.index).values,
            "good_count_ge8": good.reindex(agg_mean.index).fillna(0).astype(int).values,
            "score_mean": agg_mean.values,
            "score_std": agg_std.reindex(agg_mean.index).fillna(0.0).values,
        })
        summary_rows.append(out)

    df_scores = pd.concat(scores_rows, axis=0, ignore_index=True) if scores_rows else pd.DataFrame(columns=["ticker", "period_end", "version_tag", "ratio_name", "pillar", "score_1_10", "level_percentile", "trend_bonus", "is_na"])
    df_sum = pd.concat(summary_rows, axis=0, ignore_index=True) if summary_rows else pd.DataFrame(columns=["ticker", "period_end", "version_tag", "rsc_core_norm", "rsc_val_norm", "good_count_ge8", "score_mean", "score_std"])
    return df_scores, df_sum
