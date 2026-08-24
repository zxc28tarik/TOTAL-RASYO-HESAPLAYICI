from __future__ import annotations

"""Shared production arithmetic for Ek1's RSC quality-count score."""

import numpy as np


def compute_ek1_score_from_good_count(good_count_ge8: float) -> float:
    """Map the production good-count input to Ek1 without changing its source.

    Input validation and missing-value policy belong to the caller.  The live
    path historically coerces missing SQL values to zero before this formula;
    the PIT path instead requires a valid value from its closed M1/RSC lineage.
    """

    return float(np.clip(float(good_count_ge8) / 18.0, 0.0, 1.0))
