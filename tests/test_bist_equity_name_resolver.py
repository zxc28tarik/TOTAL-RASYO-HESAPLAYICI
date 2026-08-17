from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.bist_equity_name_resolver import (
    BistEquityNameResolutionError,
    BistEquityNameResolver,
    load_bist_equity_name_aliases_csv,
    normalize_bulletin_name,
)
from src.analytics.ticker_lineage import TickerCodeChange, TickerLineageResolver


SHA="a"*64
EVENT_SHA="b"*64
SNAP="2026-08-17"


def _aliases(tmp_path):
    path=tmp_path/"aliases.csv"
    pd.DataFrame([
        {"ticker_alias":"BERA","bulletin_name":"BERA HOLDING","normalized_name":"BERA HOLDING","validity_hint":"CURRENT_REPORT","event_date":"17/08/2026","source_type":"CURRENT_EQUITY_INDEX_REPORT","source_sha256":SHA},
        {"ticker_alias":"KONTR","bulletin_name":"KONTROLMATIK TEKNOLOJI","normalized_name":"KONTROLMATIK TEKNOLOJI","validity_hint":"CURRENT_REPORT","event_date":"17/08/2026","source_type":"CURRENT_EQUITY_INDEX_REPORT","source_sha256":SHA},
        {"ticker_alias":"EFOR","bulletin_name":"EFOR YATIRIM","normalized_name":"EFOR YATIRIM","validity_hint":"CURRENT_REPORT","event_date":"17/08/2026","source_type":"CURRENT_EQUITY_INDEX_REPORT","source_sha256":SHA},
        {"ticker_alias":"OLDX","bulletin_name":"ESKI SIRKET","normalized_name":"ESKI SIRKET","validity_hint":"BEFORE_CHANGE","event_date":"2024-03-01","source_type":"OFFICIAL_EQUITY_NAME_CHANGE","source_sha256":SHA},
    ]).to_csv(path,index=False)
    return path


def _lineage():
    return TickerLineageResolver([
        TickerCodeChange.build(effective_date="2025-11-03",old_ticker="EFORC",new_ticker="EFOR",source_sha256=SHA,event_sha256=EVENT_SHA),
        TickerCodeChange.build(effective_date="2025-01-10",old_ticker="OLDX",new_ticker="NEWX",source_sha256=SHA,event_sha256="c"*64),
    ])


def test_normalization_matches_kap_bulletin_style():
    assert normalize_bulletin_name("Bera Holdıng") == "BERA HOLDING"
    assert normalize_bulletin_name("Borusan Yat. Paz.") == "BORUSAN YAT PAZ"


def test_resolves_without_using_related_stocks_order(tmp_path):
    aliases=load_bist_equity_name_aliases_csv(_aliases(tmp_path),snapshot_date=SNAP)
    resolver=BistEquityNameResolver(aliases,ticker_lineage=_lineage(),snapshot_date=SNAP)
    assert resolver.resolve_related_stock(
        "KONTROLMATİK TEKNOLOJİ",
        related_tickers=["BERA","KONTR","AKSA"],
        event_date="2026-06-18",
    ) == "KONTR"
    assert resolver.resolve_related_stock(
        "BERA HOLDİNG",
        related_tickers=["KONTR","BERA"],
        event_date="2026-06-18",
    ) == "BERA"


def test_current_name_resolves_historical_ticker_through_identity_lineage(tmp_path):
    aliases=load_bist_equity_name_aliases_csv(_aliases(tmp_path),snapshot_date=SNAP)
    resolver=BistEquityNameResolver(aliases,ticker_lineage=_lineage(),snapshot_date=SNAP)
    assert resolver.resolve_related_stock(
        "EFOR YATIRIM",
        related_tickers=["EFORC","OTHER"],
        event_date="2025-06-01",
    ) == "EFORC"


def test_historical_name_alias_resolves_to_related_identity(tmp_path):
    aliases=load_bist_equity_name_aliases_csv(_aliases(tmp_path),snapshot_date=SNAP)
    resolver=BistEquityNameResolver(aliases,ticker_lineage=_lineage(),snapshot_date=SNAP)
    assert resolver.resolve_related_stock(
        "ESKİ ŞİRKET",
        related_tickers=["OLDX","OTHER"],
        event_date="2024-06-01",
    ) == "OLDX"


def test_unknown_or_ambiguous_match_fails_closed(tmp_path):
    aliases=load_bist_equity_name_aliases_csv(_aliases(tmp_path),snapshot_date=SNAP)
    resolver=BistEquityNameResolver(aliases,ticker_lineage=_lineage(),snapshot_date=SNAP)
    with pytest.raises(BistEquityNameResolutionError,match="bulunamadi"):
        resolver.resolve_related_stock("BILINMEYEN",related_tickers=["BERA"],event_date="2026-06-18")
    with pytest.raises(BistEquityNameResolutionError,match="tekil"):
        resolver.resolve_related_stock("BERA HOLDING",related_tickers=["KONTR"],event_date="2026-06-18")
