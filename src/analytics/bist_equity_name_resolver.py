from __future__ import annotations

"""Resolve KAP/Borsa bulletin names to disclosure-related ticker codes.

Resolution is exact after Turkish/ASCII normalization and is constrained by the
KAP disclosure's own relatedStocks set. Ticker-code changes are compared in a
common snapshot-date identity space; relatedStocks ordering is never used.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import unicodedata
from typing import Iterable

import pandas as pd

from src.analytics.ticker_lineage import TickerLineageResolver


class BistEquityNameResolutionError(ValueError):
    pass


def normalize_bulletin_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper().replace("İ", "I").replace("İ", "I")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _date(value: object, field: str) -> date:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise BistEquityNameResolutionError(f"{field} gecerli tarih olmali") from exc
    if pd.isna(ts):
        raise BistEquityNameResolutionError(f"{field} gecerli tarih olmali")
    return ts.date()


def _ticker(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text or text in {"NAN", "NONE", "<NA>"}:
        raise BistEquityNameResolutionError("ticker bos/gecersiz olamaz")
    return text


@dataclass(frozen=True)
class BulletinNameAlias:
    ticker_alias: str
    bulletin_name: str
    normalized_name: str
    validity_hint: str
    event_date: date
    source_type: str
    source_sha256: str


def _alias_date(raw: object, validity_hint: str, snapshot_date: date) -> date:
    text = str(raw or "").strip()
    if validity_hint == "CURRENT_REPORT":
        # Current report uses DD/MM/YYYY.
        try:
            return datetime.strptime(text, "%d/%m/%Y").date()
        except ValueError as exc:
            raise BistEquityNameResolutionError("CURRENT_REPORT event_date DD/MM/YYYY olmali") from exc
    return _date(text, "alias event_date")


def load_bist_equity_name_aliases_csv(
    path: str | Path,
    *,
    snapshot_date: object,
) -> tuple[BulletinNameAlias, ...]:
    snap = _date(snapshot_date, "snapshot_date")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "ticker_alias", "bulletin_name", "normalized_name", "validity_hint",
        "event_date", "source_type", "source_sha256",
    }
    missing = required - set(frame.columns)
    if missing:
        raise BistEquityNameResolutionError(f"alias CSV kolonlari eksik: {sorted(missing)}")
    out: list[BulletinNameAlias] = []
    exact_seen: set[tuple[str, str, str, str, str]] = set()
    for row in frame.itertuples(index=False):
        ticker = _ticker(row.ticker_alias)
        bulletin = str(row.bulletin_name or "").strip()
        normalized = normalize_bulletin_name(bulletin)
        if not normalized or normalized != str(row.normalized_name or "").strip():
            raise BistEquityNameResolutionError("alias normalized_name kaynak adindan deterministik uretilmeli")
        hint = str(row.validity_hint or "").strip().upper()
        if hint not in {"CURRENT_REPORT", "BEFORE_CHANGE", "AFTER_CHANGE"}:
            raise BistEquityNameResolutionError(f"validity_hint gecersiz: {hint!r}")
        day = _alias_date(row.event_date, hint, snap)
        if hint == "CURRENT_REPORT" and day != snap:
            raise BistEquityNameResolutionError("CURRENT_REPORT tarihi snapshot_date ile eslesmeli")
        source_type = str(row.source_type or "").strip()
        source_sha = str(row.source_sha256 or "").strip().lower()
        if not source_type or len(source_sha) != 64 or any(ch not in "0123456789abcdef" for ch in source_sha):
            raise BistEquityNameResolutionError("alias source_type ve 64-hex source_sha256 zorunlu")
        key=(ticker,normalized,hint,day.isoformat(),source_sha)
        if key in exact_seen:
            raise BistEquityNameResolutionError("duplicate exact alias evidence")
        exact_seen.add(key)
        out.append(BulletinNameAlias(ticker,bulletin,normalized,hint,day,source_type,source_sha))
    return tuple(sorted(out,key=lambda a:(a.normalized_name,a.ticker_alias,a.event_date,a.validity_hint,a.source_sha256)))


class BistEquityNameResolver:
    def __init__(
        self,
        aliases: Iterable[BulletinNameAlias],
        *,
        ticker_lineage: TickerLineageResolver,
        snapshot_date: object,
    ):
        self.aliases = tuple(aliases)
        self.ticker_lineage = ticker_lineage
        self.snapshot_date = _date(snapshot_date, "snapshot_date")
        self._by_name: dict[str, list[BulletinNameAlias]] = {}
        for alias in self.aliases:
            self._by_name.setdefault(alias.normalized_name, []).append(alias)

    def _alias_identity_at_snapshot(self, alias: BulletinNameAlias) -> str:
        if alias.validity_hint == "CURRENT_REPORT":
            return alias.ticker_alias
        if alias.event_date > self.snapshot_date:
            raise BistEquityNameResolutionError("historical alias snapshot_date sonrasinda")
        return self.ticker_lineage.forward(
            alias.ticker_alias,
            from_date=alias.event_date,
            to_date=self.snapshot_date,
        )

    def resolve_related_stock(
        self,
        bulletin_name: object,
        *,
        related_tickers: Iterable[object],
        event_date: object,
    ) -> str:
        name = normalize_bulletin_name(bulletin_name)
        if not name:
            raise BistEquityNameResolutionError("bulletin_name bos olamaz")
        day = _date(event_date, "event_date")
        if day > self.snapshot_date:
            raise BistEquityNameResolutionError("event_date snapshot_date sonrasinda")
        related = tuple(sorted({_ticker(x) for x in related_tickers}))
        if not related:
            raise BistEquityNameResolutionError("related_tickers bos olamaz")
        candidates = self._by_name.get(name, [])
        if not candidates:
            raise BistEquityNameResolutionError(f"Borsa alias kanitinda pay adi bulunamadi: {name}")

        related_identity: dict[str, str] = {}
        for ticker in related:
            related_identity[ticker] = self.ticker_lineage.forward(
                ticker,
                from_date=day,
                to_date=self.snapshot_date,
            )

        matched: set[str] = set()
        for alias in candidates:
            try:
                identity = self._alias_identity_at_snapshot(alias)
            except Exception:
                continue
            for ticker, candidate_identity in related_identity.items():
                if identity == candidate_identity:
                    matched.add(ticker)
        if len(matched) != 1:
            raise BistEquityNameResolutionError(
                f"pay adi relatedStocks icinde tekil cozumlenemedi: {name}; matched={sorted(matched)}"
            )
        return next(iter(matched))
