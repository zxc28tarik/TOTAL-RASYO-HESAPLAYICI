from __future__ import annotations

import json
from pathlib import Path


PATH = Path("data/backtest_sources/historical_universe_borsaistanbul_manifest.json")


def _payload():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_historical_universe_manifest_locks_all_three_acquired_official_source_families():
    payload = _payload()
    assert payload["manifest_version"] == 2
    assert payload["publisher"] == "Borsa Istanbul A.S."
    assert payload["official_landing_page"].startswith("https://borsaistanbul.com/")
    assert payload["window"] == {"start_month": "2021-08", "end_month": "2026-07"}

    assert payload["official_path_registry"]["raw_sha256"] == "0674425ad01f20b30ed0576a2b147d5999f468e4a239291180648929e500277b"
    assert payload["raw_acquisition"]["artifact_id"] == 9272988898
    assert payload["schema_inspection"]["artifact_id"] == 9273009059

    datasets = {row["dataset_key"]: row for row in payload["datasets"]}
    assert set(datasets) == {
        "FIRST_TRADING_DATE_AND_PRICE",
        "PERMANENT_DELISTINGS",
        "EQUITY_CODE_CHANGES",
    }
    assert all(row["required"] is True for row in datasets.values())
    assert all(row["acquisition_status"] == "ACQUIRED_HASH_LOCKED_INSPECTED" for row in datasets.values())

    assert datasets["FIRST_TRADING_DATE_AND_PRICE"]["official_path_from_registry"] == "/datum/ilkislem.zip"
    assert datasets["FIRST_TRADING_DATE_AND_PRICE"]["raw_sha256"] == "71666f0e981cb647a9c223811768b0478397d356ccae7608c91a34633b353e14"
    assert datasets["FIRST_TRADING_DATE_AND_PRICE"]["extracted_files"][0]["sha256"] == "34b6b5a9f5142a47214761961b76e911597528e5998dc07e82780a97fea793bd"

    assert datasets["PERMANENT_DELISTINGS"]["official_path_from_registry"] == "/datum/IslemSirasiKapananSirketler.xls"
    assert datasets["PERMANENT_DELISTINGS"]["raw_sha256"] == "26e3a7fb0152acdc8f548901ee0edbb44ddd2ecedbc1eae1527f030bd336c8e3"

    assert datasets["EQUITY_CODE_CHANGES"]["official_path_from_registry"] == "/datum/payadvekoddegisiklikleri.zip"
    assert datasets["EQUITY_CODE_CHANGES"]["raw_sha256"] == "c8068dab5643940e6efe23b7920d2dc0b454f3878f365150111754cb631f2076"
    extracted = {row["name"]: row["sha256"] for row in datasets["EQUITY_CODE_CHANGES"]["extracted_files"]}
    assert extracted == {
        "PayAdiDegisiklikleri.xls": "753951c8f2a82ff7b9f7ab6f010c62f96c7d6be54482ddeef9c635520c640ac0",
        "PayKoduDegisiklikleri.xls": "cb5e2fc5ed8bd69b75db7707f0078facc3b900ae14cb51e8f3a286e13cf239b5",
    }


def test_manifest_locks_observed_schema_without_pretending_delisting_boundary_is_resolved():
    payload = _payload()
    datasets = {row["dataset_key"]: row for row in payload["datasets"]}

    first = datasets["FIRST_TRADING_DATE_AND_PRICE"]
    assert first["primary_sheet"] == "İlk İşlem Tarihleri&Fiyatları"
    assert first["observed_nonempty_rows"] == 933
    assert "İLK İŞLEM GÜNÜ / FIRST TRADING DAY" in first["observed_columns"]
    assert first["canonicalization_status"] == "FIRST_TRADING_DAY_VALID_FROM_EVIDENCE_AVAILABLE"

    delisted = datasets["PERMANENT_DELISTINGS"]
    assert delisted["primary_sheet"] == "ISLEM_SIRASI_KAPANAN_SIRKETLER"
    assert delisted["observed_nonempty_rows"] == 186
    assert "KAPANMA TARİHİ / DELISTING DATE" in delisted["observed_columns"]
    assert delisted["canonicalization_status"] == "DELISTING_EVENT_DATE_AVAILABLE_VALID_TO_SEMANTICS_UNRESOLVED"

    code_changes = datasets["EQUITY_CODE_CHANGES"]
    assert code_changes["code_change_sheet"] == "KOD_DEGISIKLIGI"
    assert code_changes["observed_code_change_rows"] == 170
    assert code_changes["observed_code_change_columns"] == [
        "ESKİ PAY KODU / CODE BEFORE CHANGE",
        "YENİ PAY KODU / CODE AFTER CHANGE",
        "TARİH / DATE",
    ]
    assert code_changes["canonicalization_status"] == "DATED_OLD_TO_NEW_TICKER_EVENTS_AVAILABLE"

    open_semantics = "\n".join(payload["open_semantics"])
    assert "supported historical company/instrument scope" in open_semantics
    assert "Delisting Date" in open_semantics
    assert "old/new ticker price continuity" in open_semantics


def test_manifest_explicitly_forbids_current_snapshot_survivorship_inference():
    payload = _payload()
    rules = "\n".join(payload["rules"])
    assert "Do not derive historical membership from core.universe_stocks" in rules
    assert "Do not invent raw file URLs" in rules
    assert "Hash each downloaded raw file" in rules
    assert "Temporary suspensions" in rules
