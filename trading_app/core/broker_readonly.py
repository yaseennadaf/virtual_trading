"""
Read-only SharkExchange execution history client.

There is deliberately no order placement, edit, cancel, or close method on
this class. It only ever reads `/v1/user-data/trade-history` and
`/v1/user-data/transaction-history`. API credentials are entered by you at
runtime (Streamlit password inputs) and are never written to disk, the
database, or the repo.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


class SharkExchangeReadOnlyClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.sharkexchange.in"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url

    def _get(self, endpoint: str, params: dict) -> object:
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        request = Request(
            f"{self.base_url.rstrip('/')}{endpoint}?{query}",
            headers={"api-key": self.api_key, "signature": signature,
                     "Accept": "application/json", "User-Agent": "TradeJournalLab/0.1"},
        )
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def trade_history(self, symbol: str | None = None, page_size: int = 100,
                       start_timestamp: int | None = None, end_timestamp: int | None = None) -> pd.DataFrame:
        """Fields returned per SharkExchange: time, symbol, side, price,
        quantity, fee, realizedProfit -- exactly the execution-level data
        the exchange exposes; nothing here is inferred or fabricated."""
        params = {"pageSize": str(min(page_size, 100)), "sortOrder": "asc",
                  "timestamp": str(int(time.time() * 1000))}
        if symbol is not None:
            params["symbol"] = symbol.upper()
        if start_timestamp is not None:
            params["startTimestamp"] = str(start_timestamp)
        if end_timestamp is not None:
            params["endTimestamp"] = str(end_timestamp)
        payload = self._get("/v1/user-data/trade-history", params)
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        frame = pd.DataFrame(rows or [])
        if frame.empty:
            return frame
        if "time" in frame:
            frame["time"] = pd.to_datetime(frame["time"], utc=True)
        for column in ["price", "quantity", "fee", "realizedProfit"]:
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_values("time") if "time" in frame else frame
