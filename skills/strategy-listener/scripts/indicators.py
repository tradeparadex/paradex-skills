"""
indicators.py — rolling indicator state for the live listener.

Mirrors the math used by skills/strategy-backtester/scripts/paradex_backtest_engine.py
(compute_rsi line 126, compute_sma line 151, compute_realized_vol line 164,
compute_iv_percentile line 178). Kept self-contained per skill convention —
if you fix a bug here, fix it there too.

Backtester computes indicators over a fixed historical array; the listener
maintains a rolling deque seeded by a startup REST backfill and advanced one
bar at a time as bar_close events arrive.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional


HOURS_PER_YEAR = 8760  # used for annualising realized vol


# ── Rolling state per market ──────────────────────────────────────────────────


class IndicatorState:
    """
    Holds rolling close prices + funding rates for one market and exposes
    the four indicators the gate evaluator can ask about.

    Buffer size is the max window any condition asks for, plus a small head-
    room. Older bars are dropped on append.
    """

    def __init__(self, market: str, max_window: int):
        self.market = market
        # +50 headroom so RSI/SMA still have warmup after rotation
        self.max_window = max_window + 50
        self.closes: deque[float] = deque(maxlen=self.max_window)
        # Funding rate (8h, decimal) sampled at funding events
        self.last_funding: Optional[float] = None

    # ── Buffer maintenance ────────────────────────────────────────────────────

    def seed_closes(self, closes: list[float]) -> None:
        """Replace the buffer with `closes` (oldest → newest)."""
        self.closes.clear()
        for c in closes[-self.max_window:]:
            self.closes.append(float(c))

    def append_close(self, close: float) -> None:
        self.closes.append(float(close))

    def update_funding(self, funding: float) -> None:
        self.last_funding = float(funding)

    # ── Indicator readouts ────────────────────────────────────────────────────

    def rsi(self, period: int = 14) -> Optional[float]:
        """Wilder's smoothed RSI on the rolling close buffer."""
        n = len(self.closes)
        if n < period + 1:
            return None
        closes = list(self.closes)
        avg_gain = avg_loss = 0.0
        for i in range(1, period + 1):
            d = closes[i] - closes[i - 1]
            if d > 0:
                avg_gain += d
            else:
                avg_loss -= d
        avg_gain /= period
        avg_loss /= period
        for i in range(period + 1, n):
            d = closes[i] - closes[i - 1]
            gain = d if d > 0 else 0.0
            loss = -d if d < 0 else 0.0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    def sma(self, period: int) -> Optional[float]:
        n = len(self.closes)
        if n < period:
            return None
        recent = list(self.closes)[-period:]
        return sum(recent) / period

    def realized_vol_pctile(self, value: float, window: int) -> Optional[float]:
        """
        Realized-vol percentile: rank current annualised RV against the
        last `window` rolling RV samples. Returns 0–100.
        """
        n = len(self.closes)
        if n < window + 2:
            return None
        closes = list(self.closes)
        # Build per-step RV history matching the backtester's algorithm
        rv_window = min(24, max(5, window // 7))  # short rolling window for RV itself
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n) if closes[i - 1] > 0]
        if len(log_rets) < rv_window + 2:
            return None
        rv_series: list[float] = []
        for i in range(rv_window, len(log_rets)):
            sl = log_rets[i - rv_window:i]
            mean = sum(sl) / rv_window
            var = sum((x - mean) ** 2 for x in sl) / max(1, rv_window - 1)
            rv_series.append(math.sqrt(var) * math.sqrt(HOURS_PER_YEAR))
        if len(rv_series) < 2:
            return None
        history = rv_series[-window:-1] if len(rv_series) > window else rv_series[:-1]
        return percentile_rank(rv_series[-1], history)

    def funding_8h_pct(self) -> Optional[float]:
        """Last seen 8h funding rate as a percentage (e.g. 0.01 = 1%)."""
        if self.last_funding is None:
            return None
        return self.last_funding * 100.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def percentile_rank(current: float, history: list[float]) -> float:
    """Rank `current` against `history`. Returns 0–100. Empty history → 50."""
    if not history:
        return 50.0
    below = sum(1 for v in history if v < current)
    return below / len(history) * 100.0


def required_window(conditions: dict) -> int:
    """
    Look at a conditions dict and return the largest window any indicator
    asks for, in bars. Used by the runner to size the backfill.
    """
    windows: list[int] = []

    def w(key: str, default: int) -> None:
        cfg = conditions.get(key) or {}
        if cfg.get("enabled"):
            windows.append(int(cfg.get("window") or cfg.get("period") or default))

    w("rsi", 14)
    w("sma", 24)
    w("rvPctile", 168)
    w("ivPctile", 720)
    return max(windows) if windows else 0
