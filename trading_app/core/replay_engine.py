"""
Bar-by-bar replay over REAL historical candles (fetched via data.py from
SharkExchange/Binance -- never synthetic). You jump to a random point in
history and step forward one bar at a time; pending limit/stop orders and
open positions are checked against each new bar exactly the same way the
offline-reconciliation engine checks them, so replay and "what actually
happened while you were away" use one shared, consistent rule set.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import pandas as pd

from core.regime import classify_regime


@dataclass
class PendingOrder:
    id: str
    side: str            # 'long' | 'short'
    order_type: str      # 'limit' | 'stop'
    trigger_price: float
    quantity: float
    stop_price: float
    target_price: float | None
    reason: str = ""


@dataclass
class OpenPosition:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    quantity: float
    stop_price: float
    target_price: float | None
    reason: str
    regime_tag: str
    mae: float = 0.0
    mfe: float = 0.0


@dataclass
class ReplayState:
    candles: pd.DataFrame          # full fetched history, real data
    cursor: int                    # index of the "current" (last revealed) bar
    pending_orders: list[PendingOrder] = field(default_factory=list)
    position: OpenPosition | None = None
    closed_trades: list[dict] = field(default_factory=list)

    @property
    def visible(self) -> pd.DataFrame:
        """Only bars up to and including the cursor -- no lookahead."""
        return self.candles.iloc[: self.cursor + 1]

    @property
    def current_bar(self) -> pd.Series:
        return self.candles.iloc[self.cursor]

    @property
    def at_end(self) -> bool:
        return self.cursor >= len(self.candles) - 1


def start_random_replay(candles: pd.DataFrame, warmup_bars: int = 200, seed: int | None = None) -> ReplayState:
    """Jump to a random point in real history, leaving `warmup_bars` of
    context before it so indicators/regime classification have data, and
    at least a few hundred bars of runway after it."""
    rng = random.Random(seed)
    if len(candles) <= warmup_bars + 50:
        raise ValueError("Not enough candles to start a replay with this warmup")
    start_index = rng.randint(warmup_bars, len(candles) - 50)
    return ReplayState(candles=candles, cursor=start_index)


def step_forward(state: ReplayState) -> list[dict]:
    """Advance one bar, checking pending orders and any open position
    against the newly revealed bar. Returns a list of events (fills/exits)
    that happened on this bar."""
    if state.at_end:
        return []
    state.cursor += 1
    bar = state.current_bar
    events: list[dict] = []

    # 1. Check pending orders for fills against this bar's range.
    still_pending = []
    for order in state.pending_orders:
        filled = False
        if order.order_type == "limit":
            if order.side == "long" and bar["low"] <= order.trigger_price:
                filled = True
            elif order.side == "short" and bar["high"] >= order.trigger_price:
                filled = True
        else:  # stop order (breakout-style entry)
            if order.side == "long" and bar["high"] >= order.trigger_price:
                filled = True
            elif order.side == "short" and bar["low"] <= order.trigger_price:
                filled = True

        if filled and state.position is None:
            regime = classify_regime(state.visible["close"])
            state.position = OpenPosition(
                side=order.side, entry_time=bar.name, entry_price=order.trigger_price,
                quantity=order.quantity, stop_price=order.stop_price,
                target_price=order.target_price, reason=order.reason, regime_tag=regime,
            )
            events.append({"type": "fill", "order_id": order.id, "price": order.trigger_price})
        else:
            still_pending.append(order)
    state.pending_orders = still_pending

    # 2. Check open position against this bar (conservative stop-first-on-overlap).
    if state.position is not None:
        pos = state.position
        is_long = pos.side == "long"
        distance = abs(pos.entry_price - pos.stop_price)
        adverse = max(0.0, pos.entry_price - bar["low"]) if is_long else max(0.0, bar["high"] - pos.entry_price)
        favorable = max(0.0, bar["high"] - pos.entry_price) if is_long else max(0.0, pos.entry_price - bar["low"])
        if distance > 0:
            pos.mae = max(pos.mae, adverse / distance)
            pos.mfe = max(pos.mfe, favorable / distance)

        stop_hit = (bar["low"] <= pos.stop_price) if is_long else (bar["high"] >= pos.stop_price)
        target_hit = pos.target_price is not None and (
            (bar["high"] >= pos.target_price) if is_long else (bar["low"] <= pos.target_price)
        )
        exit_price, exit_reason = None, None
        if stop_hit:
            exit_price, exit_reason = pos.stop_price, "stop"
        elif target_hit:
            exit_price, exit_reason = pos.target_price, "target"

        if exit_price is not None:
            pnl_per_unit = (exit_price - pos.entry_price) if is_long else (pos.entry_price - exit_price)
            trade = {
                "side": pos.side, "entry_time": pos.entry_time, "entry_price": pos.entry_price,
                "exit_time": bar.name, "exit_price": exit_price, "quantity": pos.quantity,
                "stop_price": pos.stop_price, "target_price": pos.target_price,
                "pnl_gross_usd": pnl_per_unit * pos.quantity, "exit_reason": exit_reason,
                "mae": pos.mae, "mfe": pos.mfe, "reason": pos.reason, "regime_tag": pos.regime_tag,
            }
            state.closed_trades.append(trade)
            events.append({"type": "exit", **trade})
            state.position = None

    return events
