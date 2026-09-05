from __future__ import annotations

"""Fail-closed P2 historical valuation price evidence for 12 HOLDING/GYO gaps.

The official Borsa Istanbul THB rows prove exact pre-cutoff *raw closing prices*.
They do NOT by themselves prove the ADJUSTED_PRICE_SERIES_V1 share basis required
by the HOLDING/GYO valuation contracts.  This module therefore preserves the raw
evidence but refuses to emit ``current_price`` unless a point-in-time adjustment
proof, tied to the same raw archive and trade date, is supplied.

This is experimental historical replay support only.  It does not alter the
production price query or the canonical HOLDING/GYO NAV contracts.
"""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Mapping
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")
SOURCE = "BORSA_ISTANBUL_THB"
REQUIRED_SHARE_BASIS = "ADJUSTED_PRICE_SERIES_V1"
UNRESOLVED_REASON = "THB_RAW_CLOSE_AVAILABLE_BUT_ADJUSTED_BASIS_UNRESOLVED"
MAX_PRICE_AGE_DAYS = 7


class HistoricalValuationPriceSupplementError(ValueError):
    pass


_RAW_ROWS: tuple[Mapping[str, object], ...] = ({'ticker': 'INVES',
  'signal_date': '2022-07-01',
  'cutoff_local': '2022-06-30T18:10:00+03:00',
  'price_cap_date': '2022-06-29',
  'trade_date': '2022-06-29',
  'price_age_days': 1,
  'raw_close': 37.36,
  'archive_url': 'https://borsaistanbul.com/data/thb/2022/06/thb202206291.zip',
  'archive_sha256': 'b6e1aaf788041569fd117cf2eb1b04a7974476971358e838279d99bea78e992d',
  'member': 'thb202206291.csv',
  'member_sha256': '8f05fbe456c15cfe78ac2e63079f25baa092ea57073cf23be88141fdc3188cc7'},
 {'ticker': 'INVES',
  'signal_date': '2022-08-01',
  'cutoff_local': '2022-07-29T18:10:00+03:00',
  'price_cap_date': '2022-07-28',
  'trade_date': '2022-07-28',
  'price_age_days': 1,
  'raw_close': 40.82,
  'archive_url': 'https://borsaistanbul.com/data/thb/2022/07/thb202207281.zip',
  'archive_sha256': '50b824a92da98e59781d012256026f10ae658c458485a5630d3fd379a065427f',
  'member': 'thb202207281.csv',
  'member_sha256': 'af620393c07fcc2902706d108cfc20655862c8e3b39a8b7cf35abea6d0d8a8e5'},
 {'ticker': 'INVES',
  'signal_date': '2022-09-01',
  'cutoff_local': '2022-08-31T18:10:00+03:00',
  'price_cap_date': '2022-08-30',
  'trade_date': '2022-08-29',
  'price_age_days': 2,
  'raw_close': 41.08,
  'archive_url': 'https://borsaistanbul.com/data/thb/2022/08/thb202208291.zip',
  'archive_sha256': 'd6117bc61f4bd43afad4e3cdd92dd41d6d648f006248284512343c4135374d09',
  'member': 'thb202208291.csv',
  'member_sha256': 'a64641a5e513dba123a4d3965da00c2779840414ab33763ae7e97d0ec4947ac4'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-01-02',
  'cutoff_local': '2022-12-30T18:10:00+03:00',
  'price_cap_date': '2022-12-29',
  'trade_date': '2022-12-29',
  'price_age_days': 1,
  'raw_close': 26.8,
  'archive_url': 'https://borsaistanbul.com/data/thb/2022/12/thb202212291.zip',
  'archive_sha256': '841ba686e617a6e381e4a68e0f4947acdf1bf7bf5cc893c620d2d4403c8a0210',
  'member': 'thb202212291.csv',
  'member_sha256': '36f7e5759ce79acf24ce46caed2b46d30f44b0c5769ac9d2e1d6d009f4d23956'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-02-01',
  'cutoff_local': '2023-01-31T18:10:00+03:00',
  'price_cap_date': '2023-01-30',
  'trade_date': '2023-01-30',
  'price_age_days': 1,
  'raw_close': 26.2,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/01/thb202301301.zip',
  'archive_sha256': '7a381ec6c559fc27358e4ddac9342c54d19d0a226d38ce57c5943ec4f581fea5',
  'member': 'thb202301301.csv',
  'member_sha256': 'b16e3e41fe8f55c242c447163bd2c8d9bd1cfb7515f6f3a09bb26b32ae1a3f15'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-03-01',
  'cutoff_local': '2023-02-28T18:10:00+03:00',
  'price_cap_date': '2023-02-27',
  'trade_date': '2023-02-27',
  'price_age_days': 1,
  'raw_close': 21.18,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/02/thb202302271.zip',
  'archive_sha256': '8f8f35ca1da3297840031b053227d7b85f674d2afb32dc2a81a492458c39f01b',
  'member': 'thb202302271.csv',
  'member_sha256': '6b808a68403701b6a4e866f3cf2f017f265f2ba3e8d45389743df808de282d1f'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-04-03',
  'cutoff_local': '2023-03-31T18:10:00+03:00',
  'price_cap_date': '2023-03-30',
  'trade_date': '2023-03-30',
  'price_age_days': 1,
  'raw_close': 27.82,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/03/thb202303301.zip',
  'archive_sha256': 'e347c27fdea27fc1470764b7c5a23235644aa057f0eec986d313ce7b6484dc41',
  'member': 'thb202303301.csv',
  'member_sha256': '861e6b5bd0e965b04e9da6d3ea984b5f0fcd2c141f6581930d5ce8a65c6c971b'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-05-02',
  'cutoff_local': '2023-04-28T18:10:00+03:00',
  'price_cap_date': '2023-04-27',
  'trade_date': '2023-04-27',
  'price_age_days': 1,
  'raw_close': 25.6,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/04/thb202304271.zip',
  'archive_sha256': '881c45080ece57034a31dff1a85bed823bca3a2e14d21efa4143a2a2d490639e',
  'member': 'thb202304271.csv',
  'member_sha256': '9ca728a7054848f2281d1537785d629537ff609686ed61674c50e2f003445fe0'},
 {'ticker': 'KLRHO',
  'signal_date': '2023-06-01',
  'cutoff_local': '2023-05-31T18:10:00+03:00',
  'price_cap_date': '2023-05-30',
  'trade_date': '2023-05-30',
  'price_age_days': 1,
  'raw_close': 34.18,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/05/thb202305301.zip',
  'archive_sha256': '81fdc68a9473bdfa134b9275e5cad6ae77d36a0ddabb44c2b4f19de1c94118c0',
  'member': 'thb202305301.csv',
  'member_sha256': '60a986ce437417c93dbdc8ab971d0986790c86486025edb70fd779198fdb1057'},
 {'ticker': 'ASGYO',
  'signal_date': '2024-01-02',
  'cutoff_local': '2023-12-29T18:10:00+03:00',
  'price_cap_date': '2023-12-28',
  'trade_date': '2023-12-28',
  'price_age_days': 1,
  'raw_close': 13.76,
  'archive_url': 'https://borsaistanbul.com/data/thb/2023/12/thb202312281.zip',
  'archive_sha256': 'c11a3d91d361df4a4b0e2fe01b9b5066469a9995ea005fa10dd209635398c6a0',
  'member': 'thb202312281.csv',
  'member_sha256': '292e74e04e44fcb19ac7da1a401ea4f28123ab56b07898a4ea6ed0b8f9fb82dd'},
 {'ticker': 'ASGYO',
  'signal_date': '2024-02-01',
  'cutoff_local': '2024-01-31T18:10:00+03:00',
  'price_cap_date': '2024-01-30',
  'trade_date': '2024-01-30',
  'price_age_days': 1,
  'raw_close': 16.22,
  'archive_url': 'https://borsaistanbul.com/data/thb/2024/01/thb202401301.zip',
  'archive_sha256': 'e6482cee5147742c0bb2c485cc098d5e651eed302541c8867a40bbff98998b56',
  'member': 'thb202401301.csv',
  'member_sha256': '7793ae3cc962e04331bf36deffa197e6c49451cc2febcc69ba33316534427333'},
 {'ticker': 'ASGYO',
  'signal_date': '2024-03-01',
  'cutoff_local': '2024-02-29T18:10:00+03:00',
  'price_cap_date': '2024-02-28',
  'trade_date': '2024-02-28',
  'price_age_days': 1,
  'raw_close': 15.5,
  'archive_url': 'https://borsaistanbul.com/data/thb/2024/02/thb202402281.zip',
  'archive_sha256': 'dd6a236aa787d6ccf5005fa8bb42cd9f3436c1f049687a9b64a65b6e328fd722',
  'member': 'thb202402281.csv',
  'member_sha256': 'cdf005f8e3e53e885a1f6885cea1e327a08fb8af9d8d92b98e2db1fb00b47ab3'})


@dataclass(frozen=True)
class RawValuationPriceEvidence:
    ticker: str
    signal_date: date
    cutoff_local: datetime
    price_cap_date: date
    trade_date: date
    price_age_days: int
    raw_close: float
    archive_url: str
    archive_sha256: str
    member: str
    member_sha256: str
    source: str = SOURCE
    adjusted_price_status: str = UNRESOLVED_REASON


@dataclass(frozen=True)
class AdjustmentProof:
    """PIT proof that converts one official raw close to the required price basis."""

    ticker: str
    signal_date: date
    trade_date: date
    raw_archive_sha256: str
    adjustment_factor: float
    factor_known_at: datetime
    share_basis: str
    source_document_id: str
    source_sha256: str


@dataclass(frozen=True)
class ResolvedValuationPrice:
    ticker: str
    signal_date: date
    analysis_at: datetime
    price_trade_date: date
    current_price: float
    share_basis: str
    raw_close: float
    adjustment_factor: float
    raw_archive_sha256: str
    adjustment_source_document_id: str
    adjustment_source_sha256: str


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise HistoricalValuationPriceSupplementError(f"{field} 64 hex SHA256 olmali")
    text = value.strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HistoricalValuationPriceSupplementError(f"{field} 64 hex SHA256 olmali")
    return text


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalValuationPriceSupplementError("ticker dolu metin olmali")
    return value.strip().upper()


def _date(value: object, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise HistoricalValuationPriceSupplementError(f"{field} date olmali")
    return value


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalValuationPriceSupplementError(f"{field} timezone-aware datetime olmali")
    return value


def _finite_positive(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise HistoricalValuationPriceSupplementError(f"{field} pozitif sonlu sayi olmali")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HistoricalValuationPriceSupplementError(f"{field} pozitif sonlu sayi olmali") from exc
    if not math.isfinite(result) or result <= 0:
        raise HistoricalValuationPriceSupplementError(f"{field} pozitif sonlu sayi olmali")
    return result


def _parse_row(row: Mapping[str, object]) -> RawValuationPriceEvidence:
    cutoff = datetime.fromisoformat(str(row["cutoff_local"]))
    return RawValuationPriceEvidence(
        ticker=_ticker(row["ticker"]),
        signal_date=date.fromisoformat(str(row["signal_date"])),
        cutoff_local=_aware(cutoff, "cutoff_local"),
        price_cap_date=date.fromisoformat(str(row["price_cap_date"])),
        trade_date=date.fromisoformat(str(row["trade_date"])),
        price_age_days=int(row["price_age_days"]),
        raw_close=_finite_positive(row["raw_close"], "raw_close"),
        archive_url=str(row["archive_url"]),
        archive_sha256=_sha256(row["archive_sha256"], "archive_sha256"),
        member=str(row["member"]),
        member_sha256=_sha256(row["member_sha256"], "member_sha256"),
    )


CATALOG: tuple[RawValuationPriceEvidence, ...] = tuple(_parse_row(row) for row in _RAW_ROWS)
_INDEX = {(row.ticker, row.signal_date): row for row in CATALOG}
if len(_INDEX) != 12 or len(CATALOG) != 12:
    raise RuntimeError("P2 valuation price catalog tam 12 benzersiz hucre olmali")


def get_raw_valuation_price_evidence(*, ticker: str, signal_date: date) -> RawValuationPriceEvidence:
    key = (_ticker(ticker), _date(signal_date, "signal_date"))
    try:
        return _INDEX[key]
    except KeyError as exc:
        raise HistoricalValuationPriceSupplementError(
            f"P2 valuation price exact key yok: {key[0]} {key[1].isoformat()}"
        ) from exc


def materialize_historical_valuation_price(
    *,
    evidence: RawValuationPriceEvidence,
    analysis_at: datetime,
    proof: AdjustmentProof | None,
) -> ResolvedValuationPrice:
    """Return valuation ``current_price`` only with an exact PIT adjustment proof."""

    if not isinstance(evidence, RawValuationPriceEvidence):
        raise HistoricalValuationPriceSupplementError("evidence RawValuationPriceEvidence olmali")
    analysis = _aware(analysis_at, "analysis_at")

    canonical = get_raw_valuation_price_evidence(ticker=evidence.ticker, signal_date=evidence.signal_date)
    if evidence != canonical:
        raise HistoricalValuationPriceSupplementError("raw evidence canonical exact kayitla birebir ayni olmali")
    if analysis != evidence.cutoff_local:
        raise HistoricalValuationPriceSupplementError("analysis_at exact historical cutoff ile ayni olmali")
    if evidence.trade_date > evidence.price_cap_date:
        raise HistoricalValuationPriceSupplementError("trade_date price cap sonrasinda olamaz")
    actual_age = (analysis.astimezone(ISTANBUL).date() - evidence.trade_date).days
    if actual_age != evidence.price_age_days:
        raise HistoricalValuationPriceSupplementError("price_age_days exact kayitla uyusmuyor")
    if actual_age < 0 or actual_age > MAX_PRICE_AGE_DAYS:
        raise HistoricalValuationPriceSupplementError("valuation price freshness siniri ihlal edildi")

    if proof is None:
        raise HistoricalValuationPriceSupplementError(UNRESOLVED_REASON)
    if not isinstance(proof, AdjustmentProof):
        raise HistoricalValuationPriceSupplementError("proof AdjustmentProof olmali")
    if _ticker(proof.ticker) != evidence.ticker:
        raise HistoricalValuationPriceSupplementError("adjustment proof ticker exact key ile uyusmuyor")
    if _date(proof.signal_date, "proof.signal_date") != evidence.signal_date:
        raise HistoricalValuationPriceSupplementError("adjustment proof signal_date exact key ile uyusmuyor")
    if _date(proof.trade_date, "proof.trade_date") != evidence.trade_date:
        raise HistoricalValuationPriceSupplementError("adjustment proof trade_date exact raw fiyatla uyusmuyor")
    if _sha256(proof.raw_archive_sha256, "proof.raw_archive_sha256") != evidence.archive_sha256:
        raise HistoricalValuationPriceSupplementError("adjustment proof raw archive SHA ile uyusmuyor")
    known_at = _aware(proof.factor_known_at, "proof.factor_known_at")
    if known_at > analysis:
        raise HistoricalValuationPriceSupplementError("analysis_at sonrasi adjustment proof sizdi")
    if proof.share_basis != REQUIRED_SHARE_BASIS:
        raise HistoricalValuationPriceSupplementError("adjustment proof share_basis ADJUSTED_PRICE_SERIES_V1 olmali")
    if not isinstance(proof.source_document_id, str) or not proof.source_document_id.strip():
        raise HistoricalValuationPriceSupplementError("adjustment proof source_document_id dolu olmali")
    proof_sha = _sha256(proof.source_sha256, "proof.source_sha256")
    factor = _finite_positive(proof.adjustment_factor, "proof.adjustment_factor")
    current_price = evidence.raw_close * factor
    if not math.isfinite(current_price) or current_price <= 0:
        raise HistoricalValuationPriceSupplementError("adjusted current_price pozitif sonlu olmali")

    return ResolvedValuationPrice(
        ticker=evidence.ticker,
        signal_date=evidence.signal_date,
        analysis_at=analysis,
        price_trade_date=evidence.trade_date,
        current_price=float(current_price),
        share_basis=REQUIRED_SHARE_BASIS,
        raw_close=evidence.raw_close,
        adjustment_factor=factor,
        raw_archive_sha256=evidence.archive_sha256,
        adjustment_source_document_id=proof.source_document_id.strip(),
        adjustment_source_sha256=proof_sha,
    )
