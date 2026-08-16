from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


KAP_BIST_COMPANIES_URL = "https://kap.org.tr/tr/bist-sirketler"
_TICKER_RE = re.compile(r"\b[A-Z0-9]{3,10}\b")
MAX_UNIVERSE_RETRIES = 10
MAX_UNIVERSE_TIMEOUT_SECONDS = 300.0
MAX_UNIVERSE_ROWS = 10_000
REQUIRED_UNIVERSE_COLUMNS = {
    "ticker", "company_name", "sector_index_code", "sector_code", "is_active",
    "kap_company_id", "source_url", "universe_source",
}


class KapUniverseError(RuntimeError):
    pass


@dataclass(frozen=True)
class KapUniverseSnapshot:
    frame: pd.DataFrame
    fetched_at: datetime
    source_url: str
    html_sha256: str


class KapPublicUniverseClient:
    """Bootstrap the active BIST company universe from KAP's official page.

    This is intentionally limited to company/ticker discovery. Financial
    statements and point-in-time disclosures must come from the official MKK API
    Portal products, not from the delayed summary pages.
    """

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        url: str = KAP_BIST_COMPANIES_URL,
        session: Optional[requests.Session] = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        minimum_rows: int = 100,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url dolu HTTP(S) adresi olmali")
        parsed_url = urlparse(url.strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("url dolu HTTP(S) adresi olmali")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds pozitif sonlu sayi olmali")
        timeout_seconds = float(timeout_seconds)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds pozitif sonlu sayi olmali")
        if timeout_seconds > MAX_UNIVERSE_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds guvenli siniri asiyor")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries negatif olmayan Python int olmali")
        if max_retries > MAX_UNIVERSE_RETRIES:
            raise ValueError("max_retries guvenli siniri asiyor")
        if isinstance(minimum_rows, bool) or not isinstance(minimum_rows, int) or minimum_rows < 1:
            raise ValueError("minimum_rows pozitif Python int olmali")
        if minimum_rows > MAX_UNIVERSE_ROWS:
            raise ValueError("minimum_rows guvenli siniri asiyor")
        self.url = url.strip()
        self.session = session or requests.Session()
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max_retries
        self.minimum_rows = minimum_rows
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper

    def _download_html(self) -> str:
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
            "User-Agent": "total-rasyo-hesaplayici/universe-sync",
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    self.url, headers=headers, timeout=self.timeout_seconds
                )
                if response.status_code in self.RETRY_STATUS and attempt < self.max_retries:
                    self.sleeper(min(8.0, 0.5 * (2**attempt)))
                    continue
                response.raise_for_status()
                text = response.text
                if not isinstance(text, str) or "<html" not in text.lower():
                    raise KapUniverseError("KAP BIST sirketleri yaniti HTML degil")
                return text
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleeper(min(8.0, 0.5 * (2**attempt)))
        raise KapUniverseError(
            f"KAP BIST sirketleri sayfasi {self.max_retries + 1} denemede alinamadi"
        ) from last_error

    @staticmethod
    def _company_ref_from_href(href: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not href or "/sirket-bilgileri/ozet/" not in href:
            return None, None
        full = urljoin("https://kap.org.tr", href)
        path = urlparse(full).path
        segment = path.split("/sirket-bilgileri/ozet/", 1)[1].split("/", 1)[0]
        if not segment:
            return None, full
        first = segment.split("-", 1)[0]
        if first.isdigit() or re.fullmatch(r"[0-9a-fA-F]{24,64}", first):
            company_id = first
        else:
            company_id = segment
        return company_id, full

    @classmethod
    def parse_html(cls, html: str, *, source_url: str = KAP_BIST_COMPANIES_URL) -> pd.DataFrame:
        if not isinstance(html, str) or not html.strip():
            raise KapUniverseError("HTML bos olamaz")
        soup = BeautifulSoup(html, "html.parser")
        by_ticker: dict[str, dict[str, object]] = {}

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            ticker_text = cells[0].get_text(" ", strip=True).upper()
            tickers = []
            for token in _TICKER_RE.findall(ticker_text):
                if not any(ch.isalpha() for ch in token):
                    continue
                if token not in tickers:
                    tickers.append(token)
            if not tickers:
                continue

            company_name = cells[1].get_text(" ", strip=True)
            if not company_name:
                continue
            company_link = None
            for anchor in row.find_all("a", href=True):
                if "/sirket-bilgileri/ozet/" in anchor.get("href", ""):
                    company_link = anchor.get("href")
                    # Prefer the link from the company-name cell if available.
                    if anchor in cells[1].find_all("a", href=True):
                        break
            company_id, company_url = cls._company_ref_from_href(company_link)

            for ticker in tickers:
                current = {
                    "ticker": ticker,
                    "company_name": company_name,
                    "sector_index_code": None,
                    "sector_code": None,
                    "is_active": True,
                    "kap_company_id": company_id,
                    "source_url": company_url or source_url,
                    "universe_source": "KAP_PUBLIC_BIST_COMPANIES",
                }
                previous = by_ticker.get(ticker)
                if previous is not None:
                    comparable = (previous["company_name"], previous["kap_company_id"])
                    incoming = (current["company_name"], current["kap_company_id"])
                    if comparable != incoming:
                        raise KapUniverseError(
                            f"ayni ticker birden fazla sirketle eslesti: {ticker}: {comparable} != {incoming}"
                        )
                    continue
                by_ticker[ticker] = current

        if not by_ticker:
            raise KapUniverseError("KAP HTML icinden hic BIST sirketi ayrıştırılamadi")
        return pd.DataFrame(sorted(by_ticker.values(), key=lambda row: str(row["ticker"])))

    def fetch(self) -> KapUniverseSnapshot:
        html = self._download_html()
        frame = self.parse_html(html, source_url=self.url)
        if len(frame) < self.minimum_rows:
            raise KapUniverseError(
                f"KAP evreni beklenenden kucuk: {len(frame)} < {self.minimum_rows}; hata sayfasi olabilir"
            )
        fetched_at = self.clock()
        if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise KapUniverseError("clock timezone iceren datetime olmali")
        return KapUniverseSnapshot(
            frame=frame,
            fetched_at=fetched_at,
            source_url=self.url,
            html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
        )


def write_universe_snapshot(snapshot: KapUniverseSnapshot, output_path: str | Path) -> tuple[Path, Path]:
    if not isinstance(snapshot, KapUniverseSnapshot):
        raise TypeError("snapshot KapUniverseSnapshot olmali")
    if snapshot.fetched_at.tzinfo is None or snapshot.fetched_at.utcoffset() is None:
        raise ValueError("snapshot.fetched_at timezone icermeli")
    missing = REQUIRED_UNIVERSE_COLUMNS - set(snapshot.frame.columns)
    if missing:
        raise ValueError(f"snapshot frame eksik kolonlar: {sorted(missing)}")
    if snapshot.frame.empty:
        raise ValueError("snapshot frame bos olamaz")
    tickers = snapshot.frame["ticker"].astype(str).str.strip().str.upper()
    if tickers.eq("").any() or tickers.duplicated().any():
        raise ValueError("snapshot ticker degerleri dolu ve benzersiz olmali")
    if not isinstance(snapshot.html_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot.html_sha256):
        raise ValueError("snapshot.html_sha256 gecersiz")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = output.with_suffix(output.suffix + ".meta.json")

    # Atomic replacement: a half-written universe must never replace the last
    # usable snapshot.
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=output.parent, newline="") as tmp:
        tmp_path = Path(tmp.name)
        snapshot.frame.to_csv(tmp, index=False)
    csv_sha256 = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    os.replace(tmp_path, output)

    metadata = {
        "source_url": snapshot.source_url,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "row_count": int(len(snapshot.frame)),
        "html_sha256": snapshot.html_sha256,
        "csv_sha256": csv_sha256,
        "tickers_sha256": hashlib.sha256(
            "\n".join(snapshot.frame["ticker"].astype(str).tolist()).encode("utf-8")
        ).hexdigest(),
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=output.parent) as tmp:
        meta_tmp_path = Path(tmp.name)
        json.dump(metadata, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.write("\n")
    os.replace(meta_tmp_path, metadata_path)
    return output, metadata_path
