#!/usr/bin/env python3
from __future__ import annotations

"""Build the CLOSED historical M3 route/index package from committed raw evidence."""

import argparse
import csv
import gzip
from html.parser import HTMLParser
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Iterable

import pandas as pd

from src.analytics.bist100_membership_export import build_bist100_membership_export
from src.analytics.historical_m3_source_package import (
    CONTRACT,
    INDEX_CLOSE_SCHEMA,
    ROUTE_SCHEMA,
)


PACKAGE_RELATIVE = Path("data/backtest_sources/m3_source_package")
COVERAGE_START = "2020-07-27"
COVERAGE_END = "2026-07-01"
ASSEMBLED_AT = "2026-08-24T16:50:00Z"
RETRIEVED_AT = "2026-08-24T16:48:00Z"
GRTHO_CHANGE_DATE = "2024-09-09"
GRTHO_NOTIFICATION_ID = "1331451"
GRTHO_LINEAGE_DATE = "2024-10-01"
GRTHO_LINEAGE_CSV = Path(
    "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv"
)
GRTHO_LINEAGE_PROVENANCE = Path(
    "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.provenance.json"
)
AUDIT_ARCHIVE_SHA256 = "45bdeb775a65fae31dcc6242f2c947040bdac6995a326f69fdca616d9a17c8ad"
KAP_INDEX_AUDIT_SHA256 = "1832428e16558272ea72f188ddf4412fe93c8fddc2bc9b9b015b18279ecb8409"

INDEX_CODES = ("XU100", "XUSIN", "XUHIZ", "XUMAL", "XUTEK")
BROAD_INDEX_BY_KAP_SECTOR = {
    "BİLGİ VE İLETİŞİM": "XUHIZ",
    "ELEKTRİK GAZ VE SU": "XUHIZ",
    "EĞİTİM SAĞLIK SPOR VE EĞLENCE HİZMETLERİ": "XUHIZ",
    "GAYRİMENKUL FAALİYETLERİ": "XUHIZ",
    "MADENCİLİK VE TAŞ OCAKÇILIĞI": "XUSIN",
    "MALİ KURULUŞLAR": "XUMAL",
    "MESLEKİ, BİLİMSEL VE TEKNİK FAALİYETLER": "XUHIZ",
    "OTELLER VE LOKANTALAR": "XUHIZ",
    "TARIM, ORMANCILIK VE BALIKÇILIK": "XUSIN",
    "TEKNOLOJİ": "XUTEK",
    "TOPTAN VE PERAKENDE TİCARET": "XUHIZ",
    "ULAŞTIRMA VE DEPOLAMA": "XUHIZ",
    "İDARİ VE DESTEK HİZMET FAALİYETLERİ": "XUHIZ",
    "İMALAT": "XUSIN",
    "İNŞAAT VE BAYINDIRLIK": "XUHIZ",
}

SECTOR_SNAPSHOT_SOURCE_ID = "KAP_SEKTORLER_2026_08_24"
GRTHO_CHANGE_SOURCE_ID = "KAP_BILDIRIM_1331451"
INDEX_SOURCE_ID = {code: f"BORSA_GRAPHIC_{code}_2026_08_24" for code in INDEX_CODES}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")


def _next_payload(path: Path, key: str) -> object:
    html = _read_text(path)
    chunks: list[str] = []
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)</script>')
    for match in pattern.finditer(html):
        chunks.append(json.loads('"' + match.group(1) + '"'))
    stream = "".join(chunks)
    marker = f'"{key}"'
    position = stream.find(marker)
    if position < 0:
        raise ValueError(f"{path}: Next payload key bulunamadi: {key}")
    position = stream.find(":", position) + 1
    return json.JSONDecoder().raw_decode(stream[position:])[0]


def _split_stock_codes(value: object) -> Iterable[str]:
    if not isinstance(value, str):
        return ()
    return (part.strip().upper() for part in value.split(",") if part.strip())


def _kap_top_sector_by_ticker(path: Path) -> dict[str, str]:
    payload = _next_payload(path, "data")
    if not isinstance(payload, list):
        raise ValueError("KAP Sektorler data list olmali")
    result: dict[str, str] = {}

    def walk(node: object, top_sector: str) -> None:
        if not isinstance(node, dict):
            return
        content = node.get("content")
        if isinstance(content, list):
            for row in content:
                if not isinstance(row, dict):
                    continue
                for ticker in _split_stock_codes(row.get("stockCode")):
                    previous = result.setdefault(ticker, top_sector)
                    if previous != top_sector:
                        raise ValueError(f"{ticker}: birden fazla KAP ana sektoru")
        children = node.get("children")
        if isinstance(children, dict):
            for child in children.values():
                walk(child, top_sector)

    for root in payload:
        if not isinstance(root, dict) or not isinstance(root.get("title"), str):
            raise ValueError("KAP Sektorler kok kaydi gecersiz")
        walk(root, str(root["title"]).strip())
    if not result:
        raise ValueError("KAP Sektorler ticker kapsami bos")
    return result


class _AnnouncementTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.cells: list[tuple[str, str]] = []
        self.buffer: list[str] = []
        self.href = ""
        self.rows: list[list[tuple[str, str]]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self.in_row = True
            self.cells = []
        elif self.in_row and tag == "td":
            self.in_cell = True
            self.buffer = []
            self.href = ""
        elif self.in_cell and tag == "a":
            self.href = dict(attrs).get("href", "")

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_cell:
            text = " ".join(" ".join(self.buffer).split())
            self.cells.append((text, self.href))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if len(self.cells) >= 2:
                self.rows.append(self.cells)
            self.in_row = False


def _verify_sector_change_audit(path: Path, membership_tickers: set[str]) -> None:
    if _sha256(path) != AUDIT_ARCHIVE_SHA256:
        raise ValueError("Borsa Istanbul sector-change audit arsivi hash ile eslesmiyor")
    parser = _AnnouncementTableParser()
    parser.feed(_read_text(path))
    sector_changes = [
        row for row in parser.rows if "sector change" in row[1][0].casefold()
    ]
    expected_titles = {
        "changes to be made within the scope of bist stock indices due to the sector change of arsan tekstil",
        "changes to be made within the scope of bist stock indices due to the sector change of selcuk gida",
        "changes to be made within the scope of bist stock indices due to the sector change of metemtur yatirim",
        "changes to be made within the scope of bist stock indices due to the sector change of konfrut tarim",
        "changes to be made within the scope of bist stock indices due to the sector change of lydia yesil enerji",
        "changes to be made within the scope of bist stock indices due to the sector change of grainturk holding",
        "changes to be made within the scope of bist stock indices due to the sector change of lydia holding",
        "changes to be made within the scope of bist stock indices due to the sector change of pera yatirim holding",
    }
    observed_titles = {row[1][0].casefold() for row in sector_changes}
    if observed_titles != expected_titles:
        raise ValueError("Borsa Istanbul sector-change ilan seti beklenen arsivle eslesmiyor")
    grtho = [row for row in sector_changes if "grainturk holding" in row[1][0].casefold()]
    if len(grtho) != 1 or not grtho[0][1][1].endswith(f"/Bildirim/{GRTHO_NOTIFICATION_ID}"):
        raise ValueError("GRAINTURK sector-change ilani KAP 1331451'e bagli olmali")
    non_member_legacy_codes = {
        "ARSAN",
        "SELGD",
        "DUNYH",
        "METUR",
        "BLUME",
        "KNFRT",
        "TKURU",
        "TETMT",
        "LYDYE",
        "MIPAZ",
        "LYDHO",
        "PEGYO",
        "PEHOL",
        "TEHOL",
    }
    overlap = membership_tickers & non_member_legacy_codes
    if overlap:
        raise ValueError(f"Ek sector-change rotasi gerekli: {sorted(overlap)}")
    if "GRTHO" not in membership_tickers:
        raise ValueError("GRTHO historical membership birlesiminde olmali")


def _verify_grtho_notification(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    required_tokens = ("GRAINTURK", "09.09.2024", "XHOLD", "XUMAL", "XUHIZ", "XTCRT")
    missing = [token for token in required_tokens if token not in text]
    if missing:
        raise ValueError(f"KAP 1331451 zorunlu alanlari eksik: {missing}")


def _verify_grtho_identity_lineage(repo_root: Path) -> None:
    csv_path = repo_root / GRTHO_LINEAGE_CSV
    provenance_path = repo_root / GRTHO_LINEAGE_PROVENANCE
    frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    event = frame.loc[
        (frame["effective_date"] == GRTHO_LINEAGE_DATE)
        & (frame["old_ticker"].str.upper() == "GRTRK")
        & (frame["new_ticker"].str.upper() == "GRTHO")
    ]
    if len(event) != 1:
        raise ValueError("GRTRK->GRTHO resmi ticker-lineage olayi tekil olmali")

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    expected = {
        "effective_date": GRTHO_LINEAGE_DATE,
        "old_ticker": "GRTRK",
        "new_ticker": "GRTHO",
    }
    if provenance.get("publisher") != "Borsa Istanbul A.S.":
        raise ValueError("GRTRK->GRTHO lineage yayincisi Borsa Istanbul olmali")
    if expected not in provenance.get("notable_bist100_relevant_changes", []):
        raise ValueError("GRTRK->GRTHO lineage provenance olayini acikca icermeli")
    workbook_sha = str(provenance.get("workbook_sha256", ""))
    if event.iloc[0]["source_workbook_sha256"] != workbook_sha:
        raise ValueError("GRTRK->GRTHO lineage workbook hash'i provenance ile eslesmiyor")


def _grtho_identity_artifact(repo_root: Path) -> str:
    _verify_grtho_identity_lineage(repo_root)
    return (
        "KAP notification 1331451 (relatedStocks=GRTRK), effective 2024-09-09; "
        "canonical GRTHO identity via official Borsa Istanbul ticker-code change "
        f"GRTRK->GRTHO effective {GRTHO_LINEAGE_DATE}; "
        f"lineage_csv={GRTHO_LINEAGE_CSV} sha256={_sha256(repo_root / GRTHO_LINEAGE_CSV)}; "
        f"lineage_provenance={GRTHO_LINEAGE_PROVENANCE} "
        f"sha256={_sha256(repo_root / GRTHO_LINEAGE_PROVENANCE)}"
    )


def _verify_broad_index_mapping(
    path: Path,
    *,
    historical_tickers: set[str],
    successors: dict[str, str],
    top_sector: dict[str, str],
) -> None:
    if _sha256(path) != KAP_INDEX_AUDIT_SHA256:
        raise ValueError("KAP Endeksler audit snapshot hash ile eslesmiyor")
    payload = _next_payload(path, "initialData")
    if not isinstance(payload, list):
        raise ValueError("KAP Endeksler initialData list olmali")
    observed: dict[str, str] = {}
    for index in payload:
        if not isinstance(index, dict) or index.get("code") not in INDEX_CODES[1:]:
            continue
        code = str(index["code"])
        content = index.get("content")
        if not isinstance(content, list):
            raise ValueError(f"KAP Endeksler {code} content list olmali")
        for row in content:
            if not isinstance(row, dict):
                continue
            for ticker in _split_stock_codes(row.get("stockCode")):
                previous = observed.setdefault(ticker, code)
                if previous != code:
                    raise ValueError(f"{ticker}: birden fazla genis KAP endeksi")
    current_codes = {_current_ticker(ticker, successors) for ticker in historical_tickers}
    missing = current_codes - set(observed)
    if missing != {"KONTR", "TRILC"}:
        raise ValueError(f"KAP genis endeks audit bosluklari beklenmiyor: {sorted(missing)}")
    for ticker in sorted(current_codes - missing):
        sector = top_sector.get(ticker)
        expected = BROAD_INDEX_BY_KAP_SECTOR.get(str(sector))
        if observed[ticker] != expected:
            raise ValueError(
                f"{ticker}: KAP ana sektor/genis endeks uyusmazligi "
                f"{sector}->{expected}, observed={observed[ticker]}"
            )


def _historical_membership(repo_root: Path) -> pd.DataFrame:
    data = repo_root / "data/backtest_sources"
    result = build_bist100_membership_export(
        snapshot_csv=data / "bist100_snapshot_2026-08-17.csv",
        periodic_json=data / "bist100_periodic_events_2021Q3_2026Q3.json",
        nonperiodic_json=data / "bist100_nonperiodic_events_2021-08_2026-08.json",
        ticker_lineage_csv=data / "bist_ticker_code_changes_2021-08_2026-08.csv",
        signal_dates_csv=data / "xu100_signal_dates_yahoo_2021-08_2026-07.csv",
        expected_months=60,
        expected_member_count=100,
        index_code="XU100",
    )
    return result.frame


def _ticker_successors(repo_root: Path) -> dict[str, str]:
    path = repo_root / "data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(frame["old_ticker"].str.upper(), frame["new_ticker"].str.upper()))


def _current_ticker(ticker: str, successors: dict[str, str]) -> str:
    seen: set[str] = set()
    current = ticker
    while current in successors:
        if current in seen:
            raise ValueError(f"Ticker lineage dongusu: {ticker}")
        seen.add(current)
        current = successors[current]
    return current


def _route_frame(repo_root: Path, package_dir: Path) -> pd.DataFrame:
    membership = _historical_membership(repo_root)
    tickers = set(membership["ticker"].astype(str).str.upper())
    _verify_sector_change_audit(
        package_dir / "audit/borsa_index_announcements_2026-08-24.html", tickers
    )
    _verify_grtho_notification(package_dir / "raw/kap_bildirim_1331451.html")
    _verify_grtho_identity_lineage(repo_root)
    top_sector = _kap_top_sector_by_ticker(
        package_dir / "raw/kap_sektorler_2026-08-24.html.gz"
    )
    successors = _ticker_successors(repo_root)
    _verify_broad_index_mapping(
        package_dir / "audit/kap_endeksler_2026-08-24.html.gz",
        historical_tickers=tickers,
        successors=successors,
        top_sector=top_sector,
    )
    rows: list[dict[str, str]] = []
    for ticker in sorted(tickers):
        current = _current_ticker(ticker, successors)
        sector = top_sector.get(current)
        if sector not in BROAD_INDEX_BY_KAP_SECTOR:
            raise ValueError(f"{ticker}->{current}: KAP ana sektoru genis endekse baglanamadi: {sector}")
        code = BROAD_INDEX_BY_KAP_SECTOR[sector]
        if ticker == "GRTHO":
            if code != "XUMAL":
                raise ValueError("GRTHO current KAP sektoru XUMAL'a baglanmali")
            rows.extend(
                [
                    {
                        "ticker": ticker,
                        "valid_from": COVERAGE_START,
                        "valid_to": GRTHO_CHANGE_DATE,
                        "sector_index_code": "XUHIZ",
                        "source_id": GRTHO_CHANGE_SOURCE_ID,
                    },
                    {
                        "ticker": ticker,
                        "valid_from": GRTHO_CHANGE_DATE,
                        "valid_to": "",
                        "sector_index_code": "XUMAL",
                        "source_id": GRTHO_CHANGE_SOURCE_ID,
                    },
                ]
            )
        else:
            rows.append(
                {
                    "ticker": ticker,
                    "valid_from": COVERAGE_START,
                    "valid_to": "",
                    "sector_index_code": code,
                    "source_id": SECTOR_SNAPSHOT_SOURCE_ID,
                }
            )
    frame = pd.DataFrame(rows, columns=ROUTE_SCHEMA).sort_values(
        ["ticker", "valid_from"], kind="stable"
    )
    if set(frame["ticker"]) != tickers or len(frame) != len(tickers) + 1:
        raise ValueError("Route ticker/satir kapsami beklenen 209/210 degil")
    return frame.reset_index(drop=True)


def _index_close_frame(package_dir: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    expected_dates: tuple[str, ...] | None = None
    for code in INDEX_CODES:
        path = package_dir / f"raw/borsa_graphic_{code}_2026-08-24.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success" or not isinstance(payload.get("data"), list):
            raise ValueError(f"{code}: Borsa graphic payload basarisiz")
        rows = payload["data"]
        selected = [
            row
            for row in rows
            if isinstance(row, dict)
            and COVERAGE_START <= str(row.get("hisTs", "")) <= COVERAGE_END
        ]
        dates = tuple(sorted(str(row["hisTs"]) for row in selected))
        if len(dates) != len(set(dates)) or len(dates) != 1483:
            raise ValueError(f"{code}: zorunlu takvim 1483 benzersiz gun olmali")
        if expected_dates is None:
            expected_dates = dates
        elif dates != expected_dates:
            raise ValueError(f"{code}: XU100 takvimiyle birebir eslesmiyor")
        canonical = []
        for row in selected:
            if row.get("indexName") != code:
                raise ValueError(f"{code}: indexName uyusmazligi")
            close = float(row["clval"])
            if not close > 0:
                raise ValueError(f"{code}: pozitif olmayan kapanis")
            canonical.append(
                {
                    "index_code": code,
                    "trade_date": str(row["hisTs"]),
                    "close": format(close, ".15g"),
                    "source_id": INDEX_SOURCE_ID[code],
                }
            )
        frames.append(pd.DataFrame(canonical, columns=INDEX_CLOSE_SCHEMA))
    out = pd.concat(frames, ignore_index=True).sort_values(
        ["index_code", "trade_date"], kind="stable"
    )
    if len(out) != 7415:
        raise ValueError("Index close satir sayisi 5x1483 olmali")
    return out.reset_index(drop=True)


def _csv_gzip_bytes(frame: pd.DataFrame) -> bytes:
    text = frame.to_csv(index=False, lineterminator="\n")
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()


def build_canonical_bytes(repo_root: str | Path) -> dict[str, bytes]:
    root = Path(repo_root).resolve()
    package_dir = root / PACKAGE_RELATIVE
    routes = _route_frame(root, package_dir)
    closes = _index_close_frame(package_dir)
    return {
        "sector_routes.csv.gz": _csv_gzip_bytes(routes),
        "index_closes.csv.gz": _csv_gzip_bytes(closes),
    }


def _raw_sources(package_dir: Path) -> list[dict[str, object]]:
    repo_root = package_dir.parent.parent.parent
    descriptors = [
        {
            "source_id": SECTOR_SNAPSHOT_SOURCE_ID,
            "publisher": "Kamuyu Aydinlatma Platformu (KAP)",
            "source_url": "https://www.kap.org.tr/tr/Sektorler",
            "artifact_identity": "KAP Sektorler Next.js snapshot 2026-08-24, deterministic gzip",
            "raw_path": f"{PACKAGE_RELATIVE}/raw/kap_sektorler_2026-08-24.html.gz",
        },
        {
            "source_id": GRTHO_CHANGE_SOURCE_ID,
            "publisher": "Kamuyu Aydinlatma Platformu (KAP) / Borsa Istanbul",
            "source_url": "https://www.kap.org.tr/tr/Bildirim/1331451",
            "artifact_identity": _grtho_identity_artifact(repo_root),
            "raw_path": f"{PACKAGE_RELATIVE}/raw/kap_bildirim_1331451.html",
        },
    ]
    for code in INDEX_CODES:
        descriptors.append(
            {
                "source_id": INDEX_SOURCE_ID[code],
                "publisher": "Borsa Istanbul A.S.",
                "source_url": (
                    "https://www.borsaistanbul.com/graphic.php?"
                    f"veriTuru=endeks-graphic&indexCode={code}"
                ),
                "artifact_identity": f"Official Borsa Istanbul index graphic payload {code} 2026-08-24",
                "raw_path": f"{PACKAGE_RELATIVE}/raw/borsa_graphic_{code}_2026-08-24.json",
            }
        )
    for descriptor in descriptors:
        raw_path = Path(str(descriptor["raw_path"]))
        descriptor["retrieved_at"] = RETRIEVED_AT
        descriptor["raw_sha256"] = _sha256(repo_root / raw_path)
    return descriptors


def write_package(repo_root: str | Path) -> None:
    root = Path(repo_root).resolve()
    package_dir = root / PACKAGE_RELATIVE
    canonical = build_canonical_bytes(root)
    for name, payload in canonical.items():
        (package_dir / name).write_bytes(payload)

    entrypoint = root / "scripts/build_historical_m3_source_package.py"
    determinism_test = root / "tests/test_real_historical_m3_source_package.py"
    canonical_paths = {
        "sector_routes": package_dir / "sector_routes.csv.gz",
        "index_closes": package_dir / "index_closes.csv.gz",
    }
    manifest: dict[str, object] = {
        "contract": CONTRACT,
        "package_status": "CLOSED",
        "assembled_at": ASSEMBLED_AT,
        "market_index": "XU100",
        "coverage": {
            "start_date": COVERAGE_START,
            "end_date": COVERAGE_END,
            "beta_lookback_trading_days": 252,
            "alpha_window_trading_days": 63,
        },
        "canonical_files": {
            "sector_routes": {
                "path": f"{PACKAGE_RELATIVE}/sector_routes.csv.gz",
                "sha256": _sha256(canonical_paths["sector_routes"]),
                "row_count": 210,
                "schema": list(ROUTE_SCHEMA),
            },
            "index_closes": {
                "path": f"{PACKAGE_RELATIVE}/index_closes.csv.gz",
                "sha256": _sha256(canonical_paths["index_closes"]),
                "row_count": 7415,
                "schema": list(INDEX_CLOSE_SCHEMA),
            },
        },
        "raw_sources": _raw_sources(package_dir),
        "transformation": {
            "entrypoint_path": "scripts/build_historical_m3_source_package.py",
            "entrypoint_sha256": _sha256(entrypoint),
            "determinism_test_path": "tests/test_real_historical_m3_source_package.py",
            "determinism_test_sha256": _sha256(determinism_test),
            "reproduction_command": "python -m pytest -q tests/test_real_historical_m3_source_package.py",
        },
        "sha256sums_path": f"{PACKAGE_RELATIVE}/SHA256SUMS",
        "sha256sums_sha256": "0" * 64,
    }
    expected: dict[str, str] = {}
    for descriptor in manifest["canonical_files"].values():
        expected[str(descriptor["path"])] = str(descriptor["sha256"])
    for descriptor in manifest["raw_sources"]:
        expected[str(descriptor["raw_path"])] = str(descriptor["raw_sha256"])
    transformation = manifest["transformation"]
    expected[str(transformation["entrypoint_path"])] = str(transformation["entrypoint_sha256"])
    expected[str(transformation["determinism_test_path"])] = str(
        transformation["determinism_test_sha256"]
    )
    sums_path = package_dir / "SHA256SUMS"
    sums_path.write_text(
        "".join(f"{digest}  {path}\n" for path, digest in sorted(expected.items())),
        encoding="utf-8",
    )
    manifest["sha256sums_sha256"] = _sha256(sums_path)
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    write_package(args.repo_root)
    print(f"CLOSED historical M3 package written under {PACKAGE_RELATIVE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
