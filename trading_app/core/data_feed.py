"""
Real market data only -- no synthetic/demo data is used anywhere in this
app. SharkExchange public endpoints are tried first; Binance public klines
are the fallback for BTCUSDT history if SharkExchange is unreachable.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


def fetch_shark_ohlcv(pair: str = "BTCUSDT", interval: str = "15m", limit: int = 2000, price_type: str = "LAST_PRICE") -> pd.DataFrame:
    endpoint = "https://api.sharkexchange.in/v1/market/klines?priceType=" + price_type.upper()
    rows: list = []
    end_time = None
    while len(rows) < limit:
        payload = {"pair": pair.upper(), "interval": interval, "limit": min(limit - len(rows), 1000)}
        if end_time is not None:
            payload["endTime"] = end_time
        request = Request(
            endpoint, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "Mozilla/5.0", "Origin": "https://sharkexchange.in",
                     "Referer": "https://sharkexchange.in/"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            response_data = json.load(response)
        batch = response_data.get("data", response_data) if isinstance(response_data, dict) else response_data
        if not isinstance(batch, list) or not batch:
            break
        rows = batch + rows
        first_timestamp = int(rows[0].get("startTime", rows[0][0] if isinstance(rows[0], list) else 0))
        end_time = first_timestamp - 1
        if len(batch) < payload["limit"]:
            break
    rows = rows[-limit:]
    if not rows:
        raise ValueError("SharkExchange returned no kline data")
    frame = pd.DataFrame(rows)
    if isinstance(rows[0], list):
        frame = frame.iloc[:, :6]
        frame.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    else:
        frame = frame.rename(columns={"startTime": "timestamp"})[["timestamp", "open", "high", "low", "close", "volume"]]
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["timestamp"], errors="coerce"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna().set_index("timestamp").sort_index()


def fetch_binance_ohlcv(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 2000) -> pd.DataFrame:
    rows: list = []
    end_time = None
    while len(rows) < limit:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(limit - len(rows), 1000)}
        if end_time is not None:
            params["endTime"] = end_time
        query = urlencode(params)
        with urlopen(f"https://api.binance.com/api/v3/klines?{query}", timeout=15) as response:
            batch = json.load(response)
        if not batch:
            break
        rows = batch + rows
        end_time = batch[0][0] - 1
        if len(batch) < params["limit"]:
            break
    rows = rows[-limit:]
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time",
                                         "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
    frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["timestamp"], errors="coerce"), unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.set_index("timestamp").sort_index()


def fetch_ohlcv(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 2000) -> pd.DataFrame:
    """Try SharkExchange first, fall back to Binance public data if it fails."""
    try:
        return fetch_shark_ohlcv(pair=symbol, interval=interval, limit=limit)
    except Exception:
        return fetch_binance_ohlcv(symbol=symbol, interval=interval, limit=limit)


def fetch_shark_conversion_rate() -> float:
    request = Request(
        "https://api.sharkexchange.in/v1/exchange/exchangeInfo?market=USDT",
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    rate = payload.get("conversionRates", {}).get("INR_MARGIN_USDT")
    if rate is None or float(rate) <= 0:
        raise ValueError("SharkExchange did not return a valid INR/USDT conversion rate")
    return float(rate)
