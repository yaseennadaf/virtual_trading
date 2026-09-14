"""
Classifies the market as 'trending' or 'ranging' at a point in time, for
auto-tagging journal entries. This is a simple, explainable heuristic --
not a prediction model -- so you can see exactly why a trade got tagged
one way or the other and override it in the journal notes if you disagree.

Method: over the lookback window, compare net directional displacement to
total path length traveled (efficiency ratio). High efficiency = trending,
low efficiency = choppy/ranging. This is the classic Kaufman Efficiency
Ratio, applied to closes.
"""
import numpy as np
import pandas as pd


def efficiency_ratio(closes: pd.Series, lookback: int = 20) -> float:
    window = closes.tail(lookback)
    if len(window) < 2:
        return float("nan")
    net_change = abs(window.iloc[-1] - window.iloc[0])
    path_length = window.diff().abs().sum()
    return float(net_change / path_length) if path_length else 0.0


def classify_regime(closes: pd.Series, lookback: int = 20, trend_threshold: float = 0.35) -> str:
    ratio = efficiency_ratio(closes, lookback)
    if np.isnan(ratio):
        return "unknown"
    return "trending" if ratio >= trend_threshold else "ranging"
