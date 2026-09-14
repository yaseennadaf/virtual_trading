"""
Turns SharkExchange's real execution-level history (time, symbol, side,
price, quantity, fee, realizedProfit -- exactly what the exchange exposes,
nothing inferred) into the same `trades` table used by replay and paper
mode, tagged mode='live_real', so all three live side by side for
analytics.

Two things are honestly NOT available from this endpoint and are NOT
faked here:
  - stop_price / target_price: SharkExchange's fill feed doesn't carry your
    planned risk levels, so these are left NULL for imported trades. If you
    want real trades to show planned-vs-actual, journal the stop/target
    yourself in journal_entries after import.
  - MAE/MFE in R-multiples: R requires a defined risk (stop distance),
    which doesn't exist for these rows. Instead we store MAE/MFE here as
    a *percentage price excursion* against entry price (still computed
    from real candle data, just a different unit) -- see `mae`/`mfe`
    below, and this is explicitly labelled in the journal note.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from core.broker_readonly import SharkExchangeReadOnlyClient
from core.data_feed import fetch_ohlcv
from core.regime import classify_regime

SIDE_TO_DIRECTION = {"BUY": "long", "LONG": "long", "SELL": "short", "SHORT": "short"}


def _external_ref(entry_leg: pd.Series, exit_leg: pd.Series) -> str:
    """Best-effort de-dup key from available fields. If SharkExchange's
    payload includes a genuine execution/order id, prefer that instead --
    swap this out once you confirm the field name in a live response."""
    raw = f"{entry_leg['time']}|{exit_leg['time']}|{entry_leg.get('symbol')}|{entry_leg.get('price')}|{exit_leg.get('price')}|{entry_leg.get('quantity')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def consolidate_real_round_trips(fills: pd.DataFrame) -> list[dict]:
    if fills.empty or "side" not in fills:
        return []
    fills = fills.copy()
    fills["side"] = fills["side"].astype(str).str.upper()
    open_legs: dict[str, pd.Series] = {}
    round_trips = []
    for _, leg in fills.sort_values("time").iterrows():
        symbol = str(leg.get("symbol", "UNKNOWN"))
        direction = SIDE_TO_DIRECTION.get(leg["side"])
        if direction is None:
            continue
        if symbol not in open_legs:
            open_legs[symbol] = leg
            continue
        first = open_legs[symbol]
        first_direction = SIDE_TO_DIRECTION.get(first["side"])
        if direction == first_direction:
            continue  # same side again -- not a closing fill, treat first as still open
        pnl = float(first.get("realizedProfit", 0) or 0) + float(leg.get("realizedProfit", 0) or 0)
        fees = float(first.get("fee", 0) or 0) + float(leg.get("fee", 0) or 0)
        round_trips.append({
            "symbol": symbol, "side": first_direction,
            "entry_time": first["time"], "entry_price": float(first["price"]),
            "exit_time": leg["time"], "exit_price": float(leg["price"]),
            "quantity": float(first.get("quantity", 0) or 0),
            "pnl_usd": pnl, "fees_usd": fees,
            "external_ref": _external_ref(first, leg),
        })
        del open_legs[symbol]
    return round_trips


def enrich_with_candles(round_trip: dict, candle_interval: str = "1m") -> dict:
    """Adds regime tag and % price excursion using real candles between
    entry and exit. Silently skips enrichment (leaves fields None) if
    candles can't be fetched or matched -- it never fabricates values."""
    enriched = dict(round_trip)
    try:
        candles = fetch_ohlcv(round_trip["symbol"], candle_interval, limit=1500)
        window = candles.loc[
            pd.Timestamp(round_trip["entry_time"]):pd.Timestamp(round_trip["exit_time"])
        ]
        if window.empty:
            return enriched
        is_long = round_trip["side"] == "long"
        entry_price = round_trip["entry_price"]
        adverse = (entry_price - window["low"].min()) if is_long else (window["high"].max() - entry_price)
        favorable = (window["high"].max() - entry_price) if is_long else (entry_price - window["low"].min())
        enriched["mae"] = max(0.0, adverse) / entry_price * 100    # % excursion, NOT R (no stop exists)
        enriched["mfe"] = max(0.0, favorable) / entry_price * 100
        enriched["regime_tag"] = classify_regime(candles.loc[:round_trip["entry_time"], "close"])
    except Exception:
        pass
    return enriched


def import_account_history(api_key: str, api_secret: str, symbol: str | None = None,
                            with_candle_enrichment: bool = True) -> list[dict]:
    client = SharkExchangeReadOnlyClient(api_key, api_secret)
    fills = client.trade_history(symbol=symbol, page_size=100)
    round_trips = consolidate_real_round_trips(fills)
    if with_candle_enrichment:
        round_trips = [enrich_with_candles(trip) for trip in round_trips]
    return round_trips
