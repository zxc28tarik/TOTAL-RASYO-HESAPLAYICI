from datetime import date, datetime
import hashlib

from scripts.materialize_zero_interval_share_basis import build_manifest
from src.analytics.price_level_action_evidence import PriceLevelActionEvidence


def test_zero_length_interval_manifest_passes_existing_fail_closed_contract():
    raw = b"official-kap-row"
    digest = hashlib.sha256(raw).hexdigest()
    manifest = build_manifest(source_sha256=digest, shares_out=605880000)
    evidence = PriceLevelActionEvidence(
        manifest_bytes=manifest, expected_sha256=hashlib.sha256(manifest).hexdigest(),
        source_bytes={"KAP_SMRTG_SHARE_HISTORY": raw},
    )
    evidence.verify(
        ticker="SMRTG", shares_basis_date=date(2023, 7, 31),
        price_trade_date=date(2023, 7, 31),
        cutoff=datetime.fromisoformat("2023-07-31T18:10:00+03:00"),
        events=(), shares_out=605880000,
    )
