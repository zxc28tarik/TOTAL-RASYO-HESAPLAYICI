from __future__ import annotations

"""The current M2 derivation must read a holding's own technical family.

A holding files under the HOLDING technical family. Selecting only NONFIN facts
left the fact list empty for the largest issuers on the exchange and then died
on max() of an empty sequence, which surfaced as a stack-trace string in the
receipt instead of a reason code. These pin both halves of the fix.
"""

import json
from pathlib import Path

from scripts.materialize_current_nonfin_valuation import TECHNICAL_FAMILIES


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data/live/current_nonfin_valuation_v1/receipt.json"
# Holdings whose statements the exchange's biggest issuers actually file.
RECOVERED_HOLDINGS = ("KCHOL", "SAHOL", "SISE", "TCELL", "DOHOL", "POLHO")


def test_technical_families_match_what_the_exact_derivation_supports():
    from src.ingest.kap_bulk_exact_semantic_mapping import SUPPORTED_FAMILIES
    assert TECHNICAL_FAMILIES == SUPPORTED_FAMILIES


def test_derivation_rejections_carry_reason_codes_not_stack_traces():
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    for row in receipt["derivation_rejections"]:
        reason = row["reason"]
        assert ":" not in reason, f"{row['ticker']} still reports a raw exception: {reason}"
        assert reason in {"TECHNICAL_FAMILY_FACTS_ABSENT", "TECHNICAL_FAMILY_CONFLICTING"}, reason


def test_the_biggest_holdings_reach_a_real_m2_score():
    scored = {
        json.loads(line)["ticker"]
        for line in (ROOT / "data/live/current_nonfin_valuation_v1/m2.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    }
    missing = [ticker for ticker in RECOVERED_HOLDINGS if ticker not in scored]
    assert not missing, f"holdings dropped out of M2 again: {missing}"
