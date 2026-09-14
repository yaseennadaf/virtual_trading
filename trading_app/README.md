# BTC Trade Replay & Paper Trading Lab

Two modes:
- **Replay**: jump to a random point in real historical BTCUSDT candles and step forward bar by bar (no lookahead) to practice entries/exits.
- **Live paper**: simulated fills against real, currently-live BTCUSDT prices. **No real orders are ever sent** to SharkExchange.

All data (historical and live) comes from SharkExchange's public endpoints, falling back to Binance public klines if SharkExchange is unreachable. No synthetic data is used.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate     # .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
```

### Database (required for persistence)
1. Create a free project at https://supabase.com
2. Open the SQL editor and run `db/schema.sql`
3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in your `SUPABASE_URL` / `SUPABASE_KEY`
4. On Streamlit Community Cloud: App settings → Secrets → paste the same two keys

### Run locally
```bash
streamlit run app.py
```

### Deploy
Push this repo to GitHub, then deploy it on https://share.streamlit.io pointing at `app.py`.

## Risk & sizing model (exactly as specified, nothing assumed)
- Fixed risk of ₹500 per trade (configurable in the sidebar), converted to USD live via SharkExchange's INR/USDT conversion rate.
- 25x leverage cap on notional (configurable).
- Starting capital ₹50,000 (configurable).
- Maker/taker fee % and GST are **blank by default and must be entered by you** in the sidebar — I did not hardcode SharkExchange's current fee schedule since fee schedules change and a stale guess would quietly corrupt every P&L number. Check SharkExchange's current fee page and enter the numbers each session, or bake them into `.streamlit/secrets.toml`/`core/config.py` defaults once you've confirmed them.

## What's implemented vs. simplified for this first pass

**Implemented and tested:**
- Real-data replay engine with random start point, bar-by-bar stepping, no lookahead
- Position sizing from a fixed INR risk → BTC quantity, leverage-capped, fee-aware
- Pending limit/stop order simulation and fill logic
- Offline reconciliation: replays real 1-minute candles across any gap to determine if SL/TP was hit while the app was closed (conservative "stop wins on same-bar overlap" assumption, since exact intra-candle sequencing isn't knowable without tick data)
- Auto-journal on trade close: entry chart screenshot (PNG), MAE/MFE in R-multiples, trend/range regime tag (Kaufman efficiency ratio), entry reason, saved to Postgres
- 75/25 layout: main 15m chart + stacked 1D/4H/1H context charts

**Simplified — flagged, not silently faked:**
- **Order modification is via form fields, not literal chart drag-and-drop.** True drag-to-modify SL/TP lines on the chart needs a custom JS component (e.g. wrapping TradingView's `lightweight-charts`); Streamlit's native Plotly charts don't support draggable annotations. This is a solid follow-up if you want it — it's a separate, scoped piece of frontend work.
- **Live mode polls on interaction, it does not run 24/7.** Streamlit Community Cloud has no persistent background process — the app only runs while a browser tab has it open (and even then, checks happen on rerun, not a continuous websocket tick stream). Real "SL/TP can trigger even with the tab closed" requires a small always-on worker (a cheap VPS, a scheduled GitHub Action hitting SharkExchange's API every few minutes, or a serverless cron) that writes results to the same Supabase database — the reconciliation engine here is what that worker would call. Let me know if you want that worker built next; it's a self-contained add-on.
- **Fee numbers are not hardcoded** — you enter SharkExchange's current maker/taker % each session (see above) since I don't have a verified live source for their current schedule to hardcode without risking staleness.

This is analysis/practice software. Nothing here places real orders, and it isn't financial advice.
