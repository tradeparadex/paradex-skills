"""Shared helpers for the strategy-viz renderers."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def ensure_parent(path: Path) -> Path:
    """Make sure path.parent exists; return path unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# Exit-reason metadata used by every renderer that touches backtest trades.
REASON_COLORS: dict[str, str] = {
    "TP": "#2ecc71",        # profit target
    "SL": "#e74c3c",        # stop loss
    "DTE": "#3498db",       # DTE floor
    "EXPIRY": "#8e44ad",    # expired worthless
    "MAX": "#f39c12",       # max hold
    "DTL": "#c0392b",       # liq distance
    "IVP": "#16a085",       # IV percentile
    "REHEDGE": "#7f8c8d",   # delta rehedge
}


def hrs(h: int | float) -> str:
    if h % 24 == 0 and h >= 24:
        return f"{int(h // 24)}d"
    return f"{h}h"


def gate_label(mode: str, gate_min: int, n: int) -> str:
    mode = (mode or "all").lower()
    if mode == "all":
        return f"ALL of {n}"
    if mode == "any":
        return f"ANY of {n}"
    if mode == "min":
        return f"≥{gate_min} of {n}"
    return mode


def cycles_from_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group per-leg trade rows into cycles keyed by entry_time, matching
    the backtester engine's metric grouping. Hedge legs are excluded."""
    out: dict[int, dict[str, Any]] = {}
    for t in trades:
        if t.get("is_hedge"):
            continue
        k = t["entry_time"]
        c = out.setdefault(k, {
            "entry_time": k,
            "exit_time": t["exit_time"],
            "exit_spot": t.get("exit_spot"),
            "pnl": 0.0,
            "reason": t["reason"],
            "bars_held": t.get("bars_held", 0),
            "n_legs": 0,
        })
        c["pnl"] += t.get("pnl", 0.0)
        c["n_legs"] += 1
        c["exit_time"] = max(c["exit_time"], t["exit_time"])
    return sorted(out.values(), key=lambda c: c["entry_time"])
