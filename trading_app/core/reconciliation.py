"""
When the app (re)starts and finds an open position, this replays REAL
candles covering the gap between `last_seen_time` and now to determine
whether stop or target was touched while nobody was watching.

Caller is responsible for fetching candles for that time range at a fine
enough interval (1m recommended; 15m is a fallback if 1m history isn't
available) -- this module is pure logic so it's testable without network
calls.

CONSERVATIVE ASSUMPTION: if a single candle's range touches BOTH the stop
and the target, we cannot know which happened first without tick data, so
we assume the STOP was hit first. This is the standard conservative
backtesting convention -- it may understate a winning trade, but it will
never overstate your real performance.
"""
from dataclasses import dataclass

import pandas as pd

from core.regime import classify_regime


@dataclass
class ReconciliationResult:
    closed: bool
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None       # 'target' | 'stop' | None (still open)
    mae: float | None = None
    mfe: float | None = None
    regime_tag: str | None = None


def reconcile_offline_position(
    side: str,
    entry_price: float,
    stop_price: float,
    target_price: float | None,
    candles: pd.DataFrame,
) -> ReconciliationResult:
    if candles.empty:
        return ReconciliationResult(closed=False)

    is_long = side == "long"
    risk_distance = abs(entry_price - stop_price)
    mae = 0.0
    mfe = 0.0

    for timestamp, bar in candles.iterrows():
        high, low = float(bar["high"]), float(bar["low"])

        adverse = max(0.0, entry_price - low) if is_long else max(0.0, high - entry_price)
        favorable = max(0.0, high - entry_price) if is_long else max(0.0, entry_price - low)
        if risk_distance > 0:
            mae = max(mae, adverse / risk_distance)
            mfe = max(mfe, favorable / risk_distance)

        stop_hit = (low <= stop_price) if is_long else (high >= stop_price)
        target_hit = target_price is not None and (
            (high >= target_price) if is_long else (low <= target_price)
        )

        if stop_hit and target_hit:
            # Ambiguous within-bar ordering -- conservatively assume stop first.
            return ReconciliationResult(
                closed=True, exit_time=timestamp, exit_price=stop_price,
                exit_reason="stop", mae=mae, mfe=mfe,
                regime_tag=classify_regime(candles.loc[:timestamp, "close"]),
            )
        if stop_hit:
            return ReconciliationResult(
                closed=True, exit_time=timestamp, exit_price=stop_price,
                exit_reason="stop", mae=mae, mfe=mfe,
                regime_tag=classify_regime(candles.loc[:timestamp, "close"]),
            )
        if target_hit:
            return ReconciliationResult(
                closed=True, exit_time=timestamp, exit_price=target_price,
                exit_reason="target", mae=mae, mfe=mfe,
                regime_tag=classify_regime(candles.loc[:timestamp, "close"]),
            )

    return ReconciliationResult(closed=False, mae=mae, mfe=mfe)
