"""
V22-A uretici tarafi — saf fan-out testleri.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.analytics.module_producer_lineage import (
    MODULE_COLUMN_MAP,
    ModuleRow,
    ProducerLineageError,
    fanout_lineage_rows,
)

TZ = timezone(timedelta(hours=3))
ANALIZ = datetime(2026, 3, 2, 20, 0, tzinfo=TZ)
URETIM = datetime(2026, 3, 2, 20, 5, tzinfo=TZ)


def test_bes_modul_fan_out_edilir():
    """M1,M3,Ek1,Ek4,Ek9 -- BES modul, M2 HARIC."""
    satirlar = fanout_lineage_rows(
        [ModuleRow("GARFA", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="RUN-1")
    moduller = {s[1] for s in satirlar}
    assert moduller == {"M1", "M3", "Ek1", "Ek4", "Ek9"}
    assert "M2" not in moduller


def test_bes_modul_ayni_analysis_at_tasir():
    satirlar = fanout_lineage_rows(
        [ModuleRow("GARFA", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="RUN-1")
    assert all(s[2] == ANALIZ for s in satirlar)
    assert all(s[4] == "RUN-1" for s in satirlar)


def test_coklu_ticker_carpimsal_uretir():
    satirlar = fanout_lineage_rows(
        [ModuleRow("AAA", date(2025, 12, 31)), ModuleRow("BBB", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="RUN-1")
    assert len(satirlar) == 10  # 2 ticker x 5 modul
    assert {s[0] for s in satirlar} == {"AAA", "BBB"}


def test_naive_analysis_at_reddedilir():
    with pytest.raises(ProducerLineageError):
        fanout_lineage_rows(
            [ModuleRow("AAA", date(2025, 12, 31))],
            analysis_at=datetime(2026, 3, 2, 20, 0), produced_at=URETIM,
            source_run_key="RUN-1")


def test_bos_ticker_reddedilir():
    with pytest.raises(ProducerLineageError):
        fanout_lineage_rows(
            [ModuleRow("", date(2025, 12, 31))],
            analysis_at=ANALIZ, produced_at=URETIM, source_run_key="RUN-1")


def test_ticker_normalize_edilir():
    satirlar = fanout_lineage_rows(
        [ModuleRow(" garfa ", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key="RUN-1")
    assert all(s[0] == "GARFA" for s in satirlar)


def test_source_run_key_none_olabilir():
    """Eski cagrilarda source_run_key olmayabilir; None gecerli."""
    satirlar = fanout_lineage_rows(
        [ModuleRow("AAA", date(2025, 12, 31))],
        analysis_at=ANALIZ, produced_at=URETIM, source_run_key=None)
    assert all(s[4] is None for s in satirlar)


def test_bos_liste_bos_sonuc():
    assert fanout_lineage_rows([], analysis_at=ANALIZ, produced_at=URETIM,
                               source_run_key="RUN-1") == []


def test_module_column_map_bes_eleman():
    assert len(MODULE_COLUMN_MAP) == 5
    assert dict(MODULE_COLUMN_MAP) == {"m1": "M1", "m3": "M3", "ek1": "Ek1",
                                       "ek4": "Ek4", "ek9": "Ek9"}
