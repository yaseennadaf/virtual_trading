"""
Position sizing for a fixed INR risk-per-trade on a USD-quoted instrument (BTCUSDT).

Design:
  - You risk a FIXED amount of INR per trade (e.g. 500 INR), regardless of instrument.
  - BTC trades in USD, so the INR risk is converted to USD using the current
    SharkExchange INR/USDT conversion rate (data.fetch_shark_conversion_rate()).
  - Quantity is sized so that if price moves from entry to stop, the loss
    (including round-trip fees + GST) equals the USD risk budget.
  - Quantity is capped by the leverage-based notional limit on your equity.

Nothing here assumes the fee numbers -- they come from AccountConfig, which
you must set explicitly (see config.py).
"""
from dataclasses import dataclass

from core.config import AccountConfig


@dataclass
class SizingResult:
    quantity_btc: float
    risk_usd: float
    risk_inr: float
    notional_usd: float
    capped_by_leverage: bool
    fee_usd_estimate: float


def size_position(
    entry_price_usd: float,
    stop_price_usd: float,
    equity_inr: float,
    inr_per_usdt: float,
    account: AccountConfig,
) -> SizingResult:
    if entry_price_usd <= 0 or stop_price_usd <= 0:
        raise ValueError("Entry and stop prices must be positive")
    if inr_per_usdt <= 0:
        raise ValueError("INR/USDT conversion rate must be positive")
    if entry_price_usd == stop_price_usd:
        raise ValueError("Stop price cannot equal entry price")

    distance = abs(entry_price_usd - stop_price_usd)
    risk_usd = account.fixed_risk_inr / inr_per_usdt

    # Assume worst case round trip: taker fill on entry, taker fill on stop-out.
    entry_fee_rate = account.taker_fee_rate * (1 + account.gst_rate)
    exit_fee_rate = account.taker_fee_rate * (1 + account.gst_rate)
    risk_per_unit = distance + entry_price_usd * entry_fee_rate + stop_price_usd * exit_fee_rate
    if risk_per_unit <= 0:
        raise ValueError("Computed risk-per-unit is non-positive; check inputs")

    quantity_by_risk = risk_usd / risk_per_unit

    equity_usd = equity_inr / inr_per_usdt
    max_notional_usd = equity_usd * account.leverage
    quantity_by_leverage = max_notional_usd / entry_price_usd

    quantity = min(quantity_by_risk, quantity_by_leverage)
    capped = quantity_by_leverage < quantity_by_risk

    fee_estimate = quantity * (entry_price_usd * entry_fee_rate + stop_price_usd * exit_fee_rate)

    return SizingResult(
        quantity_btc=quantity,
        risk_usd=risk_usd,
        risk_inr=account.fixed_risk_inr,
        notional_usd=quantity * entry_price_usd,
        capped_by_leverage=capped,
        fee_usd_estimate=fee_estimate,
    )
