from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

SUPPORTED_SECTOR_FAMILIES = frozenset({
    "BANK",
    "HOLDING",
    "GYO",
    "INSURANCE",
    "FINANCIAL",
    "NONFIN",
})

# These defaults are deliberately conservative. XUMAL is a broad financial index;
# it must never imply that every constituent is a deposit bank.
DEFAULT_INDEX_TO_FAMILY: Mapping[str, str] = {
    "XBANK": "BANK",
    "XHOLD": "HOLDING",
    "XGMYO": "GYO",
    "XUMAL": "FINANCIAL",
}

# Alt tur kodu -> aile. Sirketin `sector_code` alani alt turu tasiyabilir
# (ornegin FACTORING); bu tur AILE degildir ve dogrudan yonlendirilemez.
# Eslesme olmadan bu sirketler genis XUMAL endeksine veya NONFIN'e duserdi.
DEFAULT_SECTOR_CODE_TO_FAMILY: Mapping[str, str] = {
    "FACTORING": "FINANCIAL",
    "LEASING": "FINANCIAL",
    "CONSUMER_FINANCE": "FINANCIAL",
    "NON_LIFE": "INSURANCE",
    "LIFE_PENSION": "INSURANCE",
}


class SectorRoutingError(ValueError):
    pass


def is_missing_like(value: Any) -> bool:
    """
    EKSIK deger tespiti: None, pandas.NA (NAType) ve gercek NaN.

    PostgreSQL NULL -> pandas donusumunde deger `None` KALMAZ. pandas 3.x'te
    metin sutunlarinda `None` -> `nan` (float), nullable sutunlarda `pd.NA`
    olur. Rota kodu yalnizca `is not None` kontrol ettigi icin bos sector_code
    "dolu metin olmali" hatasi veriyordu ve GYO/HOLDING/NONFIN evren sorgulari
    kiriliyordu (bkz. tests/test_sector_routing_missing_values.py).

    pandas ZORUNLU bagimlilik olmasin diye NAType tip ADI uzerinden tespit edilir.
    """
    if value is None:
        return True
    value_type = type(value)
    if (value_type.__name__ == "NAType"
            and value_type.__module__.split(".")[0] == "pandas"):
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _strict_text(name: str, value: Any, *, uppercase: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SectorRoutingError(f"{name} dolu metin olmali")
    text = value.strip()
    return text.upper() if uppercase else text


def _family(name: str, value: Any) -> str:
    family = _strict_text(name, value, uppercase=True)
    if family not in SUPPORTED_SECTOR_FAMILIES:
        raise SectorRoutingError(
            f"{name} desteklenmeyen sektor ailesi: {family}"
        )
    return family


@dataclass(frozen=True)
class SectorRoutingConfig:
    routing_profile: str
    routing_version: int
    default_family: str
    index_to_family: Mapping[str, str]
    ticker_overrides: Mapping[str, str]
    sector_code_to_family: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "SectorRoutingConfig":
        return cls(
            routing_profile="TOTAL_RASYO_SECTOR_ROUTING",
            routing_version=1,
            default_family="NONFIN",
            index_to_family=dict(DEFAULT_INDEX_TO_FAMILY),
            ticker_overrides={},
            sector_code_to_family=dict(DEFAULT_SECTOR_CODE_TO_FAMILY),
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SectorRoutingConfig":
        if not isinstance(data, Mapping):
            raise SectorRoutingError("sector routing config nesne olmali")
        allowed = {
            "routing_profile",
            "routing_version",
            "default_family",
            "index_to_family",
            "ticker_overrides",
            "sector_code_to_family",
        }
        unknown = set(data) - allowed
        if unknown:
            raise SectorRoutingError(
                "sector routing bilinmeyen alanlar: "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        profile = _strict_text("routing_profile", data.get("routing_profile"))
        version = data.get("routing_version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise SectorRoutingError("routing_version pozitif Python int olmali")
        default_family = _family("default_family", data.get("default_family", "NONFIN"))

        raw_index = data.get("index_to_family", {})
        raw_ticker = data.get("ticker_overrides", {})
        raw_subtype = data.get("sector_code_to_family", dict(DEFAULT_SECTOR_CODE_TO_FAMILY))
        if not isinstance(raw_subtype, Mapping):
            raise SectorRoutingError("sector_code_to_family nesne olmali")
        if not isinstance(raw_index, Mapping):
            raise SectorRoutingError("index_to_family nesne olmali")
        if not isinstance(raw_ticker, Mapping):
            raise SectorRoutingError("ticker_overrides nesne olmali")

        index_to_family: dict[str, str] = {}
        for key, value in raw_index.items():
            code = _strict_text("index_to_family anahtari", key, uppercase=True)
            if code == "*":
                raise SectorRoutingError("index_to_family '*' kullanmamalı; default_family kullanin")
            index_to_family[code] = _family(f"index_to_family.{code}", value)

        ticker_overrides: dict[str, str] = {}
        for key, value in raw_ticker.items():
            ticker = _strict_text("ticker_overrides anahtari", key, uppercase=True)
            ticker_overrides[ticker] = _family(f"ticker_overrides.{ticker}", value)

        sector_code_to_family: dict[str, str] = {}
        for key, value in raw_subtype.items():
            code = _strict_text("sector_code_to_family anahtari", key, uppercase=True)
            if code in SUPPORTED_SECTOR_FAMILIES:
                raise SectorRoutingError(
                    f"sector_code_to_family anahtari aile adi olamaz: {code}"
                )
            sector_code_to_family[code] = _family(f"sector_code_to_family.{code}", value)

        return cls(
            routing_profile=profile,
            routing_version=version,
            default_family=default_family,
            index_to_family=index_to_family,
            ticker_overrides=ticker_overrides,
            sector_code_to_family=sector_code_to_family,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "SectorRoutingConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    def route(
        self,
        *,
        ticker: Any,
        sector_index_code: Any = None,
        sector_code: Any = None,
    ) -> str:
        ticker_text = _strict_text("ticker", ticker, uppercase=True)
        override = self.ticker_overrides.get(ticker_text)
        if override is not None:
            return override

        # An explicit supported sector_code is more precise than a broad index.
        # Eksik deger (None/NaN/pd.NA) hata degil, "bilgi yok" demektir; endekse dusulur.
        if not is_missing_like(sector_code):
            explicit = _strict_text("sector_code", sector_code, uppercase=True)
            if explicit in SUPPORTED_SECTOR_FAMILIES:
                return explicit
            # Alt tur kodu (FACTORING, LEASING, CONSUMER_FINANCE, NON_LIFE, ...)
            # aile adi degildir; eslemesi varsa AILEYE cevrilir.
            mapped_subtype = self.sector_code_to_family.get(explicit)
            if mapped_subtype is not None:
                return mapped_subtype

        if not is_missing_like(sector_index_code):
            index_code = _strict_text(
                "sector_index_code", sector_index_code, uppercase=True
            )
            mapped = self.index_to_family.get(index_code)
            if mapped is not None:
                return mapped
        return self.default_family


def infer_sector_family(
    sector_index_code: Any,
    *,
    sector_code: Any = None,
    ticker: str = "UNKNOWN",
) -> str:
    return SectorRoutingConfig.default().route(
        ticker=ticker,
        sector_index_code=sector_index_code,
        sector_code=sector_code,
    )
