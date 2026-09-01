from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin

import requests


CONTRACT = "KAP_BULK_DOWNLOAD_ROUTE_PROBE_V1"
DEFAULT_SOURCE_PAGE = "https://kap.org.tr/tr"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_SCRIPT_BYTES = 8 * 1024 * 1024
INTEREST_TOKENS = (
    "Finansal Tablolar",
    "financial",
    "download",
    "period",
    "year",
    "report",
)
_ROUTE_INTEREST_TOKENS = ("download", "financial", "report", "statement", "file")
_ROUTE_PATTERNS = (
    re.compile(r"https?://(?:www\.)?kap\.org\.tr/[A-Za-z0-9_./?=&%{}:+-]+", re.IGNORECASE),
    re.compile(r"/(?:tr|en)/api/[A-Za-z0-9_./?=&%{}:+-]+", re.IGNORECASE),
    re.compile(r"/api/[A-Za-z0-9_./?=&%{}:+-]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_/])api/[A-Za-z0-9_./?=&%{}:+-]+", re.IGNORECASE),
)


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        values = dict(attrs)
        src = values.get("src")
        if src:
            self.srcs.append(src)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def extract_script_urls(html: str, *, base_url: str) -> tuple[str, ...]:
    parser = _ScriptParser()
    parser.feed(html)
    return tuple(sorted({urljoin(base_url, src) for src in parser.srcs}))


def _normalize_text(text: str) -> str:
    return text.replace("\\/", "/").replace("\\u002F", "/")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in occupied)


def extract_candidate_routes(text: str) -> tuple[str, ...]:
    """Return canonical route candidates without suffix duplicates."""
    normalized = _normalize_text(text)
    occupied: list[tuple[int, int]] = []
    candidates: set[str] = set()

    for pattern in _ROUTE_PATTERNS:
        for match in pattern.finditer(normalized):
            span = match.span()
            if _overlaps(span, occupied):
                continue
            route = match.group(0).rstrip(".,;:)]}'\"")
            if not any(token in route.lower() for token in _ROUTE_INTEREST_TOKENS):
                continue
            candidates.add(route)
            occupied.append(span)

    return tuple(sorted(candidates))


def extract_interesting_snippets(text: str, *, max_per_token: int = 3, radius: int = 180) -> dict[str, list[str]]:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    out: dict[str, list[str]] = {}
    for token in INTEREST_TOKENS:
        needle = token.lower()
        start = 0
        rows: list[str] = []
        while len(rows) < max_per_token:
            idx = lowered.find(needle, start)
            if idx < 0:
                break
            left = max(0, idx - radius)
            right = min(len(normalized), idx + len(token) + radius)
            snippet = " ".join(normalized[left:right].split())
            if snippet not in rows:
                rows.append(snippet)
            start = idx + len(token)
        if rows:
            out[token] = rows
    return out


def _get_text(session: requests.Session, url: str, *, timeout: int, max_bytes: int) -> tuple[int, bytes, str]:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    raw = response.content
    if len(raw) > max_bytes:
        raise ValueError(f"probe response too large: {url} bytes={len(raw)} max={max_bytes}")
    return response.status_code, raw, response.encoding or "utf-8"


def build_probe(
    *,
    source_page: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_script_bytes: int = DEFAULT_MAX_SCRIPT_BYTES,
) -> dict[str, object]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/javascript;q=0.9,*/*;q=0.8",
        }
    )

    page_status, page_raw, page_encoding = _get_text(
        session,
        source_page,
        timeout=timeout_seconds,
        max_bytes=max_script_bytes,
    )
    page_text = page_raw.decode(page_encoding, errors="replace")
    script_urls = extract_script_urls(page_text, base_url=source_page)

    all_candidates = set(extract_candidate_routes(page_text))
    snippets = extract_interesting_snippets(page_text)
    scripts: list[dict[str, object]] = []

    for url in script_urls:
        row: dict[str, object] = {"url": url}
        try:
            status, raw, encoding = _get_text(
                session,
                url,
                timeout=timeout_seconds,
                max_bytes=max_script_bytes,
            )
            text = raw.decode(encoding, errors="replace")
            candidates = extract_candidate_routes(text)
            for candidate in candidates:
                all_candidates.add(candidate)
            row.update(
                {
                    "status": status,
                    "size_bytes": len(raw),
                    "sha256": sha256_bytes(raw),
                    "candidate_routes": list(candidates),
                    "interesting_snippets": extract_interesting_snippets(text),
                }
            )
        except Exception as exc:
            row.update({"error": f"{type(exc).__name__}:{exc}"})
        scripts.append(row)

    return {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_page": source_page,
        "page_status": page_status,
        "page_size_bytes": len(page_raw),
        "page_sha256": sha256_bytes(page_raw),
        "script_count": len(script_urls),
        "scripts": scripts,
        "candidate_routes": sorted(all_candidates),
        "page_interesting_snippets": snippets,
        "raw_archive_download_authorized": False,
        "semantic_mapping_authorized": False,
        "note": "Discovery-only probe. It identifies official KAP client routes; it does not download or authorize archive bytes.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-page", default=DEFAULT_SOURCE_PAGE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-script-bytes", type=int, default=DEFAULT_MAX_SCRIPT_BYTES)
    parser.add_argument("--require-candidates", action="store_true")
    args = parser.parse_args()

    result = build_probe(
        source_page=args.source_page,
        timeout_seconds=args.timeout_seconds,
        max_script_bytes=args.max_script_bytes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if args.require_candidates and not result["candidate_routes"]:
        raise SystemExit("no candidate KAP download/API routes discovered")


if __name__ == "__main__":
    main()
