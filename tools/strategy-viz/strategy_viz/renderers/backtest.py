"""Render an equity curve + drawdown band + per-cycle trade table.

Input: a backtester results dict (`{equity, trades, metrics}`).

Public API: `render(bt, out_path, name="") -> None`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from ..common import REASON_COLORS, cycles_from_trades as _cycles, ensure_parent


def render(bt: dict[str, Any], out_path: Path, name: str = "") -> None:
    equity = bt.get("equity") or []
    trades = bt.get("trades") or []
    metrics = bt.get("metrics") or {}

    if not equity:
        raise ValueError("backtest has no equity series")

    ts = [datetime.fromtimestamp(e["t"] / 1000, tz=timezone.utc) for e in equity]
    eq = np.array([e["equity"] for e in equity], dtype=float)
    spot = np.array([e["spot"] for e in equity], dtype=float)
    in_pos = np.array([1 if e.get("has_positions") else 0 for e in equity], dtype=int)

    # Running peak + drawdown
    peak = np.maximum.accumulate(eq)
    dd_pct = (eq - peak) / peak * 100.0

    fig = plt.figure(figsize=(14, 9), facecolor="white")
    gs = fig.add_gridspec(3, 2, height_ratios=[1.7, 0.9, 1.5], width_ratios=[2.4, 1.4],
                          hspace=0.45, wspace=0.18)

    # Equity curve
    ax = fig.add_subplot(gs[0, :])
    ax.plot(ts, eq, color="#1f3a93", linewidth=1.6, label="equity")
    ax.fill_between(ts, eq, peak, where=eq < peak, color="#e74c3c", alpha=0.12)
    # mark in-position spans
    starts = np.where(np.diff(np.r_[0, in_pos, 0]) == 1)[0]
    ends = np.where(np.diff(np.r_[0, in_pos, 0]) == -1)[0]
    for s, e in zip(starts, ends):
        if e > len(ts) - 1:
            e = len(ts) - 1
        ax.axvspan(ts[s], ts[min(e, len(ts) - 1)], color="#3498db", alpha=0.04)
    # cycle exit markers
    for c in _cycles(trades):
        et = datetime.fromtimestamp(c["exit_time"] / 1000, tz=timezone.utc)
        ax.scatter([et], [np.interp(c["exit_time"], [e["t"] for e in equity], eq)],
                   s=30, color=REASON_COLORS.get(c["reason"].split("+")[0], "#555"),
                   edgecolors="white", linewidths=0.6, zorder=4)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_ylabel("equity ($)")
    ax.set_title(f"{name}  ·  equity curve" if name else "Equity curve", loc="left")
    ax.grid(alpha=0.25)

    # Drawdown
    ax2 = fig.add_subplot(gs[1, :], sharex=ax)
    ax2.fill_between(ts, dd_pct, 0, color="#e74c3c", alpha=0.35)
    ax2.set_ylabel("drawdown (%)")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax2.grid(alpha=0.25)

    # Trade table (bottom-left)
    ax3 = fig.add_subplot(gs[2, 0])
    ax3.axis("off")
    cycles = _cycles(trades)
    rows = []
    for c in cycles[:18]:
        et = datetime.fromtimestamp(c["entry_time"] / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        rows.append([et, c["reason"], f"{c['n_legs']}", f"{c['bars_held']}", f"${c['pnl']:,.0f}"])
    if not rows:
        rows = [["—", "—", "—", "—", "—"]]
    table = ax3.table(
        cellText=rows,
        colLabels=["entry (UTC)", "reason", "legs", "bars held", "PnL"],
        cellLoc="left", colLoc="left", loc="upper left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1, 1.2)
    # color reason cells
    for i, c in enumerate(cycles[:18]):
        cell = table[i + 1, 1]
        cell.set_facecolor(REASON_COLORS.get(c["reason"].split("+")[0], "#eeeeee"))
        cell.set_alpha(0.5)
    suffix = f"  (showing first 18 of {len(cycles)})" if len(cycles) > 18 else ""
    ax3.set_title(f"Cycles{suffix}", loc="left", fontsize=10)

    # Metrics card (bottom-right)
    ax4 = fig.add_subplot(gs[2, 1])
    ax4.axis("off")
    def fmt_pnl(v): return f"${v:,.0f}"
    text_lines = [
        f"trades         {metrics.get('num_trades', 0)}",
        f"win rate       {metrics.get('win_rate', 0):.1f}%",
        f"total PnL      {fmt_pnl(metrics.get('total_pnl', 0))}",
        f"total return   {metrics.get('total_return', 0):.1f}%",
        f"sharpe         {metrics.get('sharpe', 0):.2f}",
        f"max drawdown   {metrics.get('max_dd', 0):.1f}%",
        f"avg win        {fmt_pnl(metrics.get('avg_win', 0))}",
        f"avg loss       {fmt_pnl(metrics.get('avg_loss', 0))}",
        f"holding pct    {metrics.get('holding_pct', 0):.0f}%",
    ]
    if metrics.get("min_dtl") is not None:
        text_lines.append(f"min dist→liq   {metrics['min_dtl']:.1f}%")
    body = "\n".join(text_lines)
    ax4.add_patch(mpatches.FancyBboxPatch((0.02, 0.05), 0.96, 0.90,
        boxstyle="round,pad=0.02", linewidth=1, edgecolor="#28a745", facecolor="#e9f7ef",
        transform=ax4.transAxes))
    ax4.text(0.06, 0.93, body, ha="left", va="top", fontsize=10, transform=ax4.transAxes,
             family="monospace")
    if bt.get("_synthetic"):
        ax4.text(0.06, 0.04, "synthetic fixture — illustrative only", ha="left", va="bottom",
                 fontsize=8, color="#888", transform=ax4.transAxes, style="italic")

    fig.suptitle("Backtest results", fontsize=13, y=0.995)
    fig.savefig(ensure_parent(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

