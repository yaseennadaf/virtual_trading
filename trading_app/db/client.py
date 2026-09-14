"""
Thin wrapper around the Supabase Python client.

Setup:
  1. Create a free project at https://supabase.com
  2. Run db/schema.sql in the Supabase SQL editor
  3. In Streamlit Cloud -> App settings -> Secrets, add:

     SUPABASE_URL = "https://xxxx.supabase.co"
     SUPABASE_KEY = "your-service-role-or-anon-key"

  4. Locally, put the same two keys in .streamlit/secrets.toml (gitignored)
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import streamlit as st
from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


def insert_order(order: dict[str, Any]) -> dict[str, Any]:
    return get_client().table("orders").insert(order).execute().data[0]


def update_order(order_id: int, fields: dict[str, Any]) -> None:
    get_client().table("orders").update(fields).eq("id", order_id).execute()


def insert_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return get_client().table("trades").insert(trade).execute().data[0]


def insert_trade_if_new(trade: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """For imported real trades: uses the unique external_ref index to
    avoid double-importing the same fill pair on repeated syncs. Returns
    (row, was_inserted)."""
    external_ref = trade.get("external_ref")
    if external_ref:
        existing = (
            get_client().table("trades").select("id").eq("external_ref", external_ref).limit(1).execute()
        )
        if existing.data:
            return existing.data[0], False
    return get_client().table("trades").insert(trade).execute().data[0], True


def update_trade(trade_id: int, fields: dict[str, Any]) -> None:
    get_client().table("trades").update(fields).eq("id", trade_id).execute()


def get_open_trade(mode: str) -> dict[str, Any] | None:
    result = (
        get_client()
        .table("trades")
        .select("*")
        .eq("mode", mode)
        .eq("status", "open")
        .order("entry_time", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def insert_journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return get_client().table("journal_entries").insert(entry).execute().data[0]


def save_equity_point(mode: str, equity_inr: float) -> None:
    get_client().table("equity_snapshots").insert({"mode": mode, "equity_inr": equity_inr}).execute()


def get_equity_curve(mode: str, limit: int = 5000):
    result = (
        get_client()
        .table("equity_snapshots")
        .select("*")
        .eq("mode", mode)
        .order("ts", desc=False)
        .limit(limit)
        .execute()
    )
    return result.data


def get_app_state(mode: str) -> dict[str, Any] | None:
    result = get_client().table("app_state").select("*").eq("mode", mode).limit(1).execute()
    return result.data[0] if result.data else None


def upsert_app_state(mode: str, last_seen_time: str, open_trade_id: int | None) -> None:
    get_client().table("app_state").upsert(
        {"mode": mode, "last_seen_time": last_seen_time, "open_trade_id": open_trade_id}
    ).execute()


def get_closed_trades(mode: str | None = None, limit: int = 10_000):
    query = get_client().table("trades").select("*").eq("status", "closed")
    if mode:
        query = query.eq("mode", mode)
    result = query.order("exit_time", desc=True).limit(limit).execute()
    return result.data
