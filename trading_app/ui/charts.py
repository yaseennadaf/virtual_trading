import plotly.graph_objects as go
import pandas as pd


def candlestick_figure(candles: pd.DataFrame, title: str, height: int = 350,
                        entry: float | None = None, stop: float | None = None,
                        target: float | None = None) -> go.Figure:
    fig = go.Figure(data=[go.Candlestick(
        x=candles.index, open=candles["open"], high=candles["high"],
        low=candles["low"], close=candles["close"], name=title,
    )])
    if entry is not None:
        fig.add_hline(y=entry, line_color="#3b82f6", line_dash="dot", annotation_text="Entry")
    if stop is not None:
        fig.add_hline(y=stop, line_color="#ef4444", line_dash="dot", annotation_text="Stop")
    if target is not None:
        fig.add_hline(y=target, line_color="#22c55e", line_dash="dot", annotation_text="Target")
    fig.update_layout(
        title=title, height=height, margin=dict(l=10, r=10, t=30, b=10),
        xaxis_rangeslider_visible=False, showlegend=False,
    )
    return fig


def render_journal_screenshot(candles: pd.DataFrame, title: str, entry: float,
                               stop: float, target: float | None) -> bytes:
    """Renders the chart at time of entry to PNG bytes for the auto-journal.
    Requires the 'kaleido' package (see requirements.txt)."""
    fig = candlestick_figure(candles, title, height=500, entry=entry, stop=stop, target=target)
    return fig.to_image(format="png", width=1000, height=500)
