import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from core.config import AccountConfig
from core.data_feed import fetch_ohlcv, fetch_shark_conversion_rate
from core.position_sizing import size_position
from core.reconciliation import reconcile_offline_position
from core.regime import classify_regime
from core.replay_engine import PendingOrder, start_random_replay, step_forward
from core.broker_sync import import_account_history
from ui.charts import candlestick_figure, render_journal_screenshot

try:
    from db import client as db
    DB_AVAILABLE = True
except Exception:
    DB_AVAILABLE = False

st.set_page_config(page_title="BTC Trade Replay & Paper Trading", layout="wide")


# ---------------------------------------------------------------- sidebar --
with st.sidebar:
    st.header("Account & risk (fixed, explicit)")
    account = AccountConfig(
        starting_capital_inr=st.number_input("Starting capital (INR)", value=50_000.0, step=1_000.0),
        fixed_risk_inr=st.number_input("Fixed risk per trade (INR)", value=500.0, step=50.0),
        leverage=st.number_input("Leverage", value=25.0, step=1.0),
        maker_fee_pct=st.number_input("Maker fee % (from SharkExchange fee page)", value=0.0, step=0.01, format="%.4f"),
        taker_fee_pct=st.number_input("Taker fee % (from SharkExchange fee page)", value=0.0, step=0.01, format="%.4f"),
        gst_pct=st.number_input("GST % on fees", value=18.0, step=1.0),
    )
    if account.maker_fee_pct == 0.0 and account.taker_fee_pct == 0.0:
        st.warning("Fee % is 0 -- enter SharkExchange's current maker/taker fees for accurate sizing & P&L.")

    st.divider()
    mode = st.radio("Mode", ["Replay", "Live paper", "Sync real account (read-only)"])
    st.caption("Live paper = simulated fills against real live prices. No real orders are ever sent.")

    st.divider()
    try:
        inr_per_usdt = fetch_shark_conversion_rate()
        st.success(f"INR/USDT: {inr_per_usdt:,.2f}")
    except Exception as error:
        inr_per_usdt = st.number_input("INR/USDT (manual fallback -- live fetch failed)", value=88.0)
        st.caption(f"Live rate fetch failed: {error}")

    if not DB_AVAILABLE:
        st.error("Database not configured -- trades will NOT persist. Add SUPABASE_URL/SUPABASE_KEY to secrets.")


def current_equity_inr() -> float:
    realized = sum(t.get("pnl_inr", 0) or 0 for t in st.session_state.get("closed_trades", []))
    return account.starting_capital_inr + realized


# ------------------------------------------------------------- REPLAY UI --
def render_replay_mode():
    st.subheader("Replay mode -- real historical candles, bar-by-bar, no lookahead")

    if "replay_state" not in st.session_state:
        col_a, col_b = st.columns([1, 3])
        with col_a:
            if st.button("Fetch history & start random replay", type="primary"):
                with st.spinner("Fetching real BTCUSDT candles..."):
                    candles = fetch_ohlcv("BTCUSDT", "15m", limit=3000)
                st.session_state.replay_15m = candles
                st.session_state.replay_state = start_random_replay(candles, warmup_bars=200)
                st.session_state.closed_trades = []
                st.rerun()
        st.info("Pick a random point in real BTCUSDT history and step forward candle by candle.")
        return

    state = st.session_state.replay_state
    visible = state.visible
    current_price = float(state.current_bar["close"])

    right, left = st.columns([3, 1])  # 75% / 25%

    with left:
        st.caption("Higher timeframe context")
        for label, rule in [("1D", "1D"), ("4H", "4h"), ("1H", "1h")]:
            resampled = visible.resample(rule).agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}
            ).dropna()
            st.plotly_chart(candlestick_figure(resampled.tail(80), label, height=220),
                             use_container_width=True, key=f"htf_{label}")

    with right:
        pos = state.position
        fig = candlestick_figure(
            visible.tail(150), "BTCUSDT 15m (replay)", height=430,
            entry=pos.entry_price if pos else None,
            stop=pos.stop_price if pos else None,
            target=pos.target_price if pos else None,
        )
        st.plotly_chart(fig, use_container_width=True, key="main_replay_chart")

        controls = st.columns(4)
        if controls[0].button("Step forward ▶", disabled=state.at_end):
            events = step_forward(state)
            _handle_events(events, mode="replay", visible_after=state.visible)
            st.rerun()
        if controls[1].button("Step x10 ⏩", disabled=state.at_end):
            for _ in range(10):
                if state.at_end:
                    break
                events = step_forward(state)
                _handle_events(events, mode="replay", visible_after=state.visible)
            st.rerun()
        if controls[2].button("New random replay 🎲"):
            del st.session_state["replay_state"]
            st.rerun()
        controls[3].metric("Price", f"${current_price:,.1f}")

        st.divider()
        _order_entry_form(current_price, inr_per_usdt, account, state=state)

        if pos:
            st.markdown(
                f"**Open position:** {pos.side.upper()} qty={pos.quantity:.5f} "
                f"entry=${pos.entry_price:,.1f} stop=${pos.stop_price:,.1f} "
                f"target={f'${pos.target_price:,.1f}' if pos.target_price else '—'} "
                f"| MAE={pos.mae:.2f}R MFE={pos.mfe:.2f}R | regime={pos.regime_tag}"
            )

    st.divider()
    _render_closed_trades()


def _order_entry_form(current_price: float, inr_per_usdt: float, account: AccountConfig, state):
    with st.form("order_form", clear_on_submit=True):
        cols = st.columns(5)
        side = cols[0].selectbox("Side", ["long", "short"])
        order_type = cols[1].selectbox("Type", ["limit", "stop", "market"])
        trigger = cols[2].number_input("Entry/trigger price", value=float(current_price))
        stop = cols[3].number_input("Stop price", value=float(current_price * (0.99 if side == "long" else 1.01)))
        target = cols[4].number_input("Target price (0 = none)", value=0.0)
        reason = st.text_input("Reason for this trade (required for the journal)")
        submitted = st.form_submit_button("Place order")
        if submitted:
            if not reason.strip():
                st.error("A reason is required so the auto-journal has context.")
                return
            try:
                sizing = size_position(
                    entry_price_usd=trigger, stop_price_usd=stop,
                    equity_inr=current_equity_inr(), inr_per_usdt=inr_per_usdt, account=account,
                )
            except ValueError as error:
                st.error(str(error))
                return
            order = PendingOrder(
                id=str(time.time()), side=side, order_type=order_type if order_type != "market" else "limit",
                trigger_price=trigger, quantity=sizing.quantity_btc, stop_price=stop,
                target_price=target if target > 0 else None, reason=reason,
            )
            if order_type == "market":
                # Fill immediately against current price rather than waiting for a touch.
                order.trigger_price = current_price
            state.pending_orders.append(order)
            st.success(
                f"Order queued: qty={sizing.quantity_btc:.5f} BTC "
                f"(risk ≈ ₹{sizing.risk_inr:.0f} / ${sizing.risk_usd:.2f}"
                f"{', capped by leverage' if sizing.capped_by_leverage else ''})"
            )


def _handle_events(events: list[dict], mode: str, visible_after: pd.DataFrame):
    for event in events:
        if event["type"] == "exit":
            trade = dict(event)
            trade["pnl_inr"] = trade["pnl_gross_usd"] * st.session_state.get("inr_per_usdt_cached", 88.0)
            st.session_state.setdefault("closed_trades", []).append(trade)
            if DB_AVAILABLE:
                try:
                    saved = db.insert_trade({
                        "mode": mode, "side": trade["side"],
                        "entry_time": str(trade["entry_time"]), "entry_price": trade["entry_price"],
                        "exit_time": str(trade["exit_time"]), "exit_price": trade["exit_price"],
                        "quantity": trade["quantity"], "stop_price": trade["stop_price"],
                        "target_price": trade["target_price"], "pnl_usd": trade["pnl_gross_usd"],
                        "exit_reason": trade["exit_reason"], "mae": trade["mae"], "mfe": trade["mfe"],
                        "reason": trade["reason"], "regime_tag": trade["regime_tag"], "status": "closed",
                    })
                    try:
                        png = render_journal_screenshot(
                            visible_after.tail(150), "Entry snapshot",
                            trade["entry_price"], trade["stop_price"], trade["target_price"],
                        )
                        db.insert_journal_entry({
                            "trade_id": saved["id"],
                            "notes": trade["reason"],
                            "strategy_rules": "",
                            "accuracy_context": "",
                        })
                        st.session_state.setdefault("last_screenshot", png)
                    except Exception as shot_error:
                        st.caption(f"Screenshot generation skipped: {shot_error}")
                except Exception as db_error:
                    st.warning(f"Trade closed but DB save failed: {db_error}")


def _render_closed_trades():
    trades = st.session_state.get("closed_trades", [])
    if not trades:
        st.caption("No closed trades yet this session.")
        return
    frame = pd.DataFrame(trades)
    wins = frame[frame["pnl_gross_usd"] > 0]
    losses = frame[frame["pnl_gross_usd"] < 0]
    cols = st.columns(5)
    cols[0].metric("Trades", len(frame))
    cols[1].metric("Win rate", f"{(len(wins) / len(frame) * 100):.0f}%")
    cols[2].metric("Net P&L (USD)", f"${frame['pnl_gross_usd'].sum():,.2f}")
    cols[3].metric("Avg MAE (R)", f"{frame['mae'].mean():.2f}")
    cols[4].metric("Avg MFE (R)", f"{frame['mfe'].mean():.2f}")
    st.dataframe(frame, use_container_width=True, hide_index=True)
    if "last_screenshot" in st.session_state:
        st.image(st.session_state["last_screenshot"], caption="Last trade entry snapshot (auto-journal)")


# ---------------------------------------------------------- LIVE PAPER UI --
def render_live_paper_mode():
    st.subheader("Live paper mode -- simulated fills against real live BTCUSDT price")
    st.caption("No real orders are ever sent to SharkExchange in this mode.")

    if DB_AVAILABLE:
        _reconcile_on_load()

    if st.button("Refresh live price"):
        st.rerun()

    with st.spinner("Fetching latest real candles..."):
        candles = fetch_ohlcv("BTCUSDT", "15m", limit=300)
    current_price = float(candles["close"].iloc[-1])
    st.metric("BTCUSDT live", f"${current_price:,.1f}", help=f"as of {candles.index[-1]}")

    right, left = st.columns([3, 1])
    with left:
        for label, rule in [("1D", "1D"), ("4H", "4h"), ("1H", "1h")]:
            resampled = candles.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            st.plotly_chart(candlestick_figure(resampled.tail(80), label, height=220), use_container_width=True, key=f"live_htf_{label}")
    with right:
        st.plotly_chart(candlestick_figure(candles.tail(150), "BTCUSDT 15m (live)", height=430),
                         use_container_width=True, key="live_main_chart")
        st.info(
            "This MVP polls on each manual refresh / page interaction. For true always-on "
            "monitoring (so a position can hit SL/TP even with the tab closed) you need the "
            "separate always-on worker discussed earlier -- Streamlit Cloud alone can't do this."
        )
        _order_entry_form(current_price, inr_per_usdt, account, state=_ensure_live_state())

    st.divider()
    _render_closed_trades()

    if DB_AVAILABLE:
        db.upsert_app_state("live_paper", datetime.now(timezone.utc).isoformat(),
                             open_trade_id=None)


def _ensure_live_state():
    if "live_state" not in st.session_state:
        class _LiveState:
            pending_orders: list = []
            position = None
        st.session_state.live_state = _LiveState()
    return st.session_state.live_state


def _reconcile_on_load():
    open_trade = db.get_open_trade("live_paper")
    if not open_trade:
        return
    app_state = db.get_app_state("live_paper")
    last_seen = app_state["last_seen_time"] if app_state else open_trade["entry_time"]
    st.warning(f"Found an open position from before the app was last closed (last seen {last_seen}). Reconciling against real market data...")
    gap_candles = fetch_ohlcv("BTCUSDT", "1m", limit=1500)
    gap_candles = gap_candles.loc[gap_candles.index > pd.Timestamp(last_seen)]
    result = reconcile_offline_position(
        side=open_trade["side"], entry_price=open_trade["entry_price"],
        stop_price=open_trade["stop_price"], target_price=open_trade.get("target_price"),
        candles=gap_candles,
    )
    if result.closed:
        db.update_trade(open_trade["id"], {
            "status": "closed", "exit_time": str(result.exit_time), "exit_price": result.exit_price,
            "exit_reason": "reconciled_offline", "mae": result.mae, "mfe": result.mfe,
            "regime_tag": result.regime_tag,
        })
        st.success(f"Reconciled: position was closed by {result.exit_reason} at ${result.exit_price:,.1f} while offline.")
    else:
        st.info("Position was still open at every checked candle -- resuming live monitoring.")


# ---------------------------------------------------------- REAL SYNC UI --
def render_real_sync_mode():
    st.subheader("Sync real account history (read-only)")
    st.caption(
        "Pulls your actual fills from SharkExchange's trade-history endpoint (time, symbol, "
        "side, price, quantity, fee, realizedProfit), pairs them into round trips, and stores "
        "them alongside your paper/replay trades for unified analytics. This client has no "
        "order placement, edit, cancel, or close method -- it only reads."
    )
    col1, col2 = st.columns(2)
    api_key = col1.text_input("SharkExchange API key", type="password")
    api_secret = col2.text_input("SharkExchange API secret", type="password")
    st.caption("Credentials are used only for this request and never written to disk, the database, or the repo.")

    if st.button("Fetch & import real trade history", type="primary", disabled=not (api_key and api_secret)):
        if not DB_AVAILABLE:
            st.error("Database not configured -- can't import without SUPABASE_URL/SUPABASE_KEY in secrets.")
            return
        with st.spinner("Fetching fills and matching real candles for MAE/MFE/regime..."):
            try:
                round_trips = import_account_history(api_key, api_secret, with_candle_enrichment=True)
            except Exception as error:
                st.error(f"Import failed: {error}")
                return
        inserted, skipped = 0, 0
        for trip in round_trips:
            row = {
                "mode": "live_real", "symbol": trip["symbol"], "side": trip["side"],
                "entry_time": str(trip["entry_time"]), "entry_price": trip["entry_price"],
                "exit_time": str(trip["exit_time"]), "exit_price": trip["exit_price"],
                "quantity": trip["quantity"], "fees_usd": trip["fees_usd"],
                "pnl_usd": trip["pnl_usd"], "status": "closed",
                "exit_reason": "actual", "external_ref": trip["external_ref"],
                "mae": trip.get("mae"), "mfe": trip.get("mfe"), "regime_tag": trip.get("regime_tag"),
            }
            _, was_new = db.insert_trade_if_new(row)
            inserted += int(was_new)
            skipped += int(not was_new)
        st.success(f"Imported {inserted} new round-trip trade(s), skipped {skipped} already-imported.")
        if round_trips:
            st.caption("MAE/MFE for real trades are shown as % price excursion from entry, not R-multiples -- "
                       "SharkExchange's fill feed has no planned stop, so R has no defined basis here.")
            st.dataframe(pd.DataFrame(round_trips), use_container_width=True, hide_index=True)

    if DB_AVAILABLE:
        st.divider()
        st.caption("Previously imported real trades")
        real_trades = db.get_closed_trades(mode="live_real")
        if real_trades:
            st.dataframe(pd.DataFrame(real_trades), use_container_width=True, hide_index=True)
        else:
            st.caption("None imported yet.")


# ------------------------------------------------------------------ main --
st.title("BTC Trade Replay & Paper Trading Lab")
st.caption("Real data only. Replay has no lookahead. Live mode places no real orders.")

if mode == "Replay":
    render_replay_mode()
elif mode == "Live paper":
    render_live_paper_mode()
else:
    render_real_sync_mode()
