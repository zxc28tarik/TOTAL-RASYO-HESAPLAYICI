from __future__ import annotations
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.ingest.sector_routing import infer_sector_family


@dataclass(frozen=True)
class UniverseRow:
    ticker: str
    company_name: str
    sector_index_code: str
    sector_code: str
    is_active: bool = True


def _infer_sector_code(sector_index_code: str) -> str:
    return infer_sector_family(sector_index_code, ticker="UNIVERSE_BUILD")


def read_tickers(path: str) -> List[str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix.lower() == ".txt":
        return sorted(set([ln.strip().upper() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]))
    import pandas as pd
    df = pd.read_csv(p)
    vals = df["ticker"].astype(str).tolist() if "ticker" in df.columns else df.iloc[:, 0].astype(str).tolist()
    return sorted(set([v.strip().upper() for v in vals if v and str(v).strip()]))


def load_mapping(mapping_json_path: Optional[str]) -> Dict[str, dict]:
    if not mapping_json_path:
        return {}
    data = json.loads(Path(mapping_json_path).read_text(encoding="utf-8"))
    out: Dict[str, dict] = {}
    for k, v in data.items():
        tk = str(k).upper().strip()
        out[tk] = {"sector_index_code": v} if isinstance(v, str) else v
    return out


def build_universe_rows(tickers: Iterable[str], mapping: Dict[str, dict], default_sector_index_code: str = "XU100") -> List[UniverseRow]:
    rows: List[UniverseRow] = []
    for t in tickers:
        t = str(t).upper().strip()
        meta = mapping.get(t, {})
        sic = (meta.get("sector_index_code") or default_sector_index_code).upper().strip()
        sc = (meta.get("sector_code") or _infer_sector_code(sic)).upper().strip()
        cn = str(meta.get("company_name") or "")
        ia = bool(meta.get("is_active", True))
        rows.append(UniverseRow(t, cn, sic, sc, ia))
    return rows


def write_universe_csv(rows: List[UniverseRow], out_path: str) -> None:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "company_name", "sector_index_code", "sector_code", "is_active"])
        for r in rows:
            w.writerow([r.ticker, r.company_name, r.sector_index_code, r.sector_code, "true" if r.is_active else "false"])


def build_universe_csv(tickers_file: str, out_csv: str, mapping_json_path: Optional[str] = None, default_sector_index_code: str = "XU100") -> int:
    tickers = read_tickers(tickers_file)
    mapping = load_mapping(mapping_json_path)
    rows = build_universe_rows(tickers, mapping, default_sector_index_code)
    write_universe_csv(rows, out_csv)
    return len(rows)
