from __future__ import annotations

"""The current full-BIST valuation config may differ from the frozen historical
one in exactly one parameter, and only in the admitted direction."""

import json
from pathlib import Path

from src.analytics.nonfin_valuation import NonfinValuationConfig
from scripts.materialize_current_nonfin_valuation import CONFIG, HISTORICAL_CONFIG


ROOT = Path(__file__).resolve().parents[1]
COVERAGE_KEY = "minimum_coverage_weight"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_current_config_differs_from_the_historical_one_in_coverage_weight_only():
    current, historical = _load(CONFIG), _load(HISTORICAL_CONFIG)
    assert set(current) == set(historical)
    differing = {key for key in current if current[key] != historical[key]}
    assert differing == {COVERAGE_KEY}, (
        "the current line may only relax the coverage gate; every other "
        f"valuation parameter must match the frozen historical config: {differing}"
    )


def test_coverage_gate_admits_two_independent_multiples_but_never_one():
    current = _load(CONFIG)
    weights = current["multiple_weights"]
    gate = current[COVERAGE_KEY]
    # PB+PS is the thinnest pair a loss-making issuer can still present.
    assert weights["PB"] + weights["PS"] == gate
    # A single multiple must still fail closed, whichever one it is.
    assert max(weights.values()) < gate


def test_the_relaxation_leaves_every_other_gate_untouched():
    current = NonfinValuationConfig.from_json_file(CONFIG)
    historical = NonfinValuationConfig.from_json_file(HISTORICAL_CONFIG)
    assert current.multiple_weights == historical.multiple_weights
    assert current.minimum_peer_count == historical.minimum_peer_count
    assert current.full_confidence_peer_count == historical.full_confidence_peer_count
    assert current.lower_quantile == historical.lower_quantile
    assert current.upper_quantile == historical.upper_quantile
    assert current.max_halfwidth == historical.max_halfwidth
    assert current.valuation_axis_weight == historical.valuation_axis_weight
    assert current.follow_axis_weight == historical.follow_axis_weight
    assert current.minimum_coverage_weight < historical.minimum_coverage_weight


def test_historical_audits_still_read_the_frozen_config():
    """A regression guard: the audits must not be repointed at the current config."""
    for script in ("audit_w7c_real_m2_score.py", "audit_w7b_spk_bulletin_evidence.py",
                   "audit_w5_smrtg_canary.py", "audit_w2_current_provenance.py"):
        source = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "nonfin_valuation.kap_bulk_exact_v1.json" in source
        assert "nonfin_valuation.current_full_bist_v1.json" not in source
