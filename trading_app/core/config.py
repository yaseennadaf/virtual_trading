"""
Account & risk configuration.

IMPORTANT: SharkExchange's maker/taker/GST fee percentages are NOT hardcoded
here. Fee schedules change over time and guessing a stale number would
silently corrupt every P&L and position-size calculation. Enter the current
values (visible on SharkExchange's fee page) in the sidebar each session, or
set them once in .streamlit/secrets.toml (see README).
"""
from dataclasses import dataclass


@dataclass
class AccountConfig:
    starting_capital_inr: float = 50_000.0
    fixed_risk_inr: float = 500.0           # risk budget per trade, in INR
    leverage: float = 25.0                  # max notional multiple of equity
    maker_fee_pct: float = 0.0              # e.g. 0.02 for 0.02% -- SET THIS
    taker_fee_pct: float = 0.0              # e.g. 0.05 for 0.05% -- SET THIS
    gst_pct: float = 18.0                   # GST on fees, India default; confirm current rate
    max_daily_loss_pct: float = 0.03        # stop trading for the day beyond this equity drawdown
    max_entries_per_day: int = 10

    @property
    def maker_fee_rate(self) -> float:
        return self.maker_fee_pct / 100.0

    @property
    def taker_fee_rate(self) -> float:
        return self.taker_fee_pct / 100.0

    @property
    def gst_rate(self) -> float:
        return self.gst_pct / 100.0
