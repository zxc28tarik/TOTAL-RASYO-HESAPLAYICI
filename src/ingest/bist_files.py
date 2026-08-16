from __future__ import annotations
import pandas as pd


def load_bist_pay_bulten_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def load_bist_endeks_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)
