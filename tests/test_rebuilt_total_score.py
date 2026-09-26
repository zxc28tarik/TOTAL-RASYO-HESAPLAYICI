from __future__ import annotations

"""The rebuilt layer must stay parameter-free, and stay fail-closed."""

import pandas as pd
import pytest

from src.analytics.rebuilt_total_score import (
    BandPolicy, RebuiltScoreConfig, rebuilt_scores,
)


MODULES = ["M1", "M3", "Ek4", "Ek1", "Ek9"]


def _cohort(rows: int = 20, months: tuple[str, ...] = ("2024-01",)) -> pd.DataFrame:
    records = []
    for month in months:
        for index in range(rows):
            records.append({
                "month": month, "ticker": f"T{index:02d}",
                **{module: float(index + position) for position, module in enumerate(MODULES)},
            })
    return pd.DataFrame(records)


def test_weights_are_equal_and_there_is_no_way_to_set_them():
    config = RebuiltScoreConfig(modules=MODULES)
    assert config.weights == {module: 0.2 for module in MODULES}
    assert sum(config.weights.values()) == pytest.approx(1.0)
    # The dataclass is frozen and weights is a derived property, so no vector
    # chosen by hand can be injected.
    with pytest.raises((AttributeError, TypeError)):
        config.weights = {module: 1 / 5 for module in MODULES}  # type: ignore[misc]
    with pytest.raises(Exception):
        config.modules = ["M1"]  # type: ignore[misc]


def test_weights_stay_equal_for_any_module_count():
    for count in (2, 3, 5, 6):
        config = RebuiltScoreConfig(modules=MODULES[:count] if count <= 5 else MODULES + ["M2"])
        values = set(config.weights.values())
        assert len(values) == 1
        assert sum(config.weights.values()) == pytest.approx(1.0)


def test_the_score_uses_the_whole_range_rather_than_a_band_in_the_middle():
    scored = rebuilt_scores(_cohort(), config=RebuiltScoreConfig(modules=MODULES))
    values = scored.total_rasyo_100.dropna().astype(float)
    assert values.max() == pytest.approx(100.0)
    assert values.min() < 10.0


def test_bands_deliver_the_share_they_promise_not_a_level():
    config = RebuiltScoreConfig(modules=MODULES, bands=BandPolicy(al_share=0.10, izle_share=0.30))
    scored = rebuilt_scores(_cohort(rows=100), config=config)
    counts = scored.decision.value_counts()
    assert counts["AL"] == 10
    assert counts["IZLE"] == 20
    assert counts["UZAK"] == 70


def test_a_nonsensical_band_policy_is_refused():
    for al, izle in ((0.0, 0.3), (0.4, 0.2), (0.3, 1.0), (-0.1, 0.5)):
        with pytest.raises(ValueError, match="al_share"):
            BandPolicy(al_share=al, izle_share=izle)


def test_a_row_missing_any_module_gets_no_score_and_is_never_filled():
    frame = _cohort()
    frame.loc[3, "Ek9"] = None
    scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=MODULES))
    assert pd.isna(scored.loc[3, "total_rasyo_100"])
    # A frame column normalises None to NA, so the assertion is "no decision was
    # emitted" rather than the identity of the empty value.
    assert pd.isna(scored.loc[3, "decision"])
    # The rest of the cohort still scores.
    assert scored.total_rasyo_100.notna().sum() == len(frame) - 1


def test_an_absent_module_column_is_an_error_not_a_silent_reweighting():
    frame = _cohort().drop(columns=["Ek4"])
    with pytest.raises(ValueError, match="REBUILT_SCORE_MODULE_COLUMN_ABSENT"):
        rebuilt_scores(frame, config=RebuiltScoreConfig(modules=MODULES))


def test_each_month_is_scored_against_itself_only():
    frame = _cohort(months=("2024-01", "2024-02"))
    # Inflate February's inputs; ranks must be unchanged.
    february = frame.month == "2024-02"
    for module in MODULES:
        frame.loc[february, module] = frame.loc[february, module] * 1000 + 5000
    scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=MODULES))
    january = scored.loc[~february, "total_rasyo_100"].astype(float).tolist()
    later = scored.loc[february, "total_rasyo_100"].astype(float).tolist()
    assert january == later


def test_a_cohort_too_small_to_rank_produces_no_scores():
    frame = _cohort(rows=3)
    scored = rebuilt_scores(frame, config=RebuiltScoreConfig(modules=MODULES))
    assert scored.total_rasyo_100.isna().all()
    assert scored.decision.isna().all()


def test_config_rejects_an_empty_or_duplicated_module_list():
    with pytest.raises(ValueError, match="bos olamaz"):
        RebuiltScoreConfig(modules=[])
    with pytest.raises(ValueError, match="yinelenen"):
        RebuiltScoreConfig(modules=["M1", "M1"])


def test_there_is_no_veto_anywhere_in_the_rebuilt_output():
    scored = rebuilt_scores(_cohort(), config=RebuiltScoreConfig(modules=MODULES))
    assert not [column for column in scored.columns if "veto" in column.lower()]
