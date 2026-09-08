from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.borsaistanbul.com/files/pay-piyasasi-proseduru.pdf"
OUTPUT = ROOT / "data/live/current_quote_unit_v1"
CONTRACT = "BORSA_PAY_PRICE_PER_1_TRY_NOMINAL_V1"


def capture(output_dir: Path = OUTPUT) -> dict:
    from pypdf import PdfReader

    response = requests.get(URL, timeout=90, headers={"User-Agent": "TOTAL-RASYO evidence capture/1"})
    response.raise_for_status()
    raw = response.content
    if not raw.startswith(b"%PDF"):
        raise ValueError("Borsa quote-unit source is not PDF")
    pages = PdfReader(io.BytesIO(raw)).pages
    matched_page = None
    for number, page in enumerate(pages, start=1):
        text = " ".join((page.extract_text() or "").split())
        if "ilan edilen fiyat" in text and "1 TL nominal değerli payın" in text and "piyasa fiyatıdır" in text:
            matched_page = number
            break
    if matched_page is None:
        raise ValueError("Borsa 1 TRY nominal quote-unit semantic not found")
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / "pay_piyasasi_proseduru.pdf.gz"
    source_path.write_bytes(gzip.compress(raw, mtime=0))
    receipt = {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_url": URL,
        "source_http_date": response.headers.get("Date"),
        "source_pdf_sha256": hashlib.sha256(raw).hexdigest(),
        "source_gzip_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "semantic_page_number": matched_page,
        "quoted_nominal_unit_try": 1,
        "legal_share_count_equated_to_nominal_try": False,
        "market_cap_formula": "raw_close * total_explicit_class_nominal_value_try / 1 TRY",
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
