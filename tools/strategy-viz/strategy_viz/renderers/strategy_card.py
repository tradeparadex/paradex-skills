"""Strategy tear-sheet card: one-page summary combining strategy spec
with optional backtest results. Layout follows pyfolio / quantstats /
options-platform "command center" conventions.

Layout (14x10in, designed to be skimmed in ~30 seconds):

  ┌─ HEADER (name · asset · capital · period · margin · 1-line thesis) ──┐
  ├─ KPI STRIP (6 tiles: total return · Sharpe · max DD · win % · # trades · expectancy) ──┤
  ├─ EQUITY CURVE + DRAWDOWN BAND ───┬─ PAYOFF AT EXPIRY ─────────────────┤
  │                                  ├─ LEGS · ENTRY · EXIT (rules card)─┤
  ├─ MONTHLY RETURNS HEATMAP ────────┴─ EXIT-REASON BREAKDOWN ────────────┤
  └──────────────────────────────────────────────────────────────────────┘

Public API: `render(strat, backtest, out_path) -> None`. Pass `backtest=None`
for the pre-trade preview (omits equity/heatmap/exit-reasons).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np

from ..common import REASON_COLORS, cycles_from_trades as _cycles, ensure_parent, gate_label as _gate_label, hrs as _hrs
from ..pricing import SPOT, ASSUMED_IV, R, leg_entry_premium, leg_greeks_at_entry, leg_strike, portfolio_greeks
from ..specs import entry_lines as _entry_lines, exit_lines as _exit_lines, expectancy as _expectancy, thesis as _thesis
from .payoff import leg_payoff_vec


def _monthly_returns(equity: list[dict]) -> dict[tuple[int, int], float]:
    """Compute month-end / month-start equity returns. Returns {(year, month): pct}."""
    by_month: dict[tuple[int, int], list[float]] = defaultdict(list)
    for e in equity:
        d = datetime.fromtimestamp(e["t"] / 1000, tz=timezone.utc)
        by_month[(d.year, d.month)].append(e["equity"])
    out: dict[tuple[int, int], float] = {}
    for k in sorted(by_month):
        vals = by_month[k]
        if vals[0]:
            out[k] = (vals[-1] - vals[0]) / vals[0] * 100
    return out


# ─────────────────────────────────────────────────────────────────────
# Render sections
# ─────────────────────────────────────────────────────────────────────


def _draw_kpi_tile(ax, label: str, value: str, accent: str, hint: str | None = None) -> None:
    ax.axis("off")
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.04, 0.05), 0.92, 0.90, transform=ax.transAxes,
        boxstyle="round,pad=0.02", linewidth=0, facecolor="#f7f9fb"))
    # accent stripe on the left edge
    ax.add_patch(mpatches.Rectangle(
        (0.04, 0.05), 0.045, 0.90, transform=ax.transAxes,
        linewidth=0, facecolor=accent))
    ax.text(0.13, 0.74, label.upper(), transform=ax.transAxes,
            fontsize=9, color="#6b7280", weight="bold")
    ax.text(0.13, 0.40, value, transform=ax.transAxes,
            fontsize=18, color="#111827", weight="bold")
    if hint:
        ax.text(0.13, 0.16, hint, transform=ax.transAxes,
                fontsize=8, color="#6b7280")


def _draw_header(fig, strat: dict[str, Any], backtest: dict[str, Any] | None) -> None:
    ax = fig.add_axes([0.03, 0.91, 0.94, 0.07])
    ax.axis("off")
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.0), 1.0, 1.0, transform=ax.transAxes,
        boxstyle="round,pad=0.005", linewidth=0, facecolor="#111827"))
    name = strat.get("name", "strategy")
    underlying = strat.get("underlying", "?")
    capital = strat.get("capital", 0)
    margin = strat.get("marginMode", "XM")
    bt_window = strat.get("backtest", {}) or {}
    period = f"{bt_window.get('startDate', '?')} → {bt_window.get('endDate', '?')}"
    ax.text(0.012, 0.66, name, transform=ax.transAxes,
            fontsize=18, color="white", weight="bold")
    ax.text(0.012, 0.20, _thesis(strat), transform=ax.transAxes,
            fontsize=10, color="#9ca3af", style="italic")
    meta = f"{underlying}  ·  ${capital:,.0f}  ·  margin {margin}  ·  {period}"
    ax.text(0.988, 0.50, meta, transform=ax.transAxes,
            ha="right", va="center", fontsize=10, color="#d1d5db")
    if backtest and backtest.get("_synthetic"):
        ax.text(0.988, 0.10, "synthetic fixture", transform=ax.transAxes,
                ha="right", va="bottom", fontsize=8, color="#fbbf24", style="italic")


def _draw_kpi_strip(fig, strat: dict[str, Any], backtest: dict[str, Any] | None) -> None:
    metrics = (backtest or {}).get("metrics") or {}
    cycles = _cycles((backtest or {}).get("trades", []) or [])
    capital = strat.get("capital", 1)
    expectancy = _expectancy(cycles)

    total_return = metrics.get("total_return", 0.0)
    sharpe = metrics.get("sharpe", 0.0)
    max_dd = metrics.get("max_dd", 0.0)
    win_rate = metrics.get("win_rate", 0.0)
    num_trades = metrics.get("num_trades", 0)

    # Color cues: green if good, red if bad. Threshold-based, conservative defaults.
    g, r, n = "#16a34a", "#dc2626", "#3b82f6"
    tiles = [
        ("Total return", f"{total_return:+.1f}%",
         g if total_return > 0 else r,
         f"${metrics.get('total_pnl', 0):,.0f} pnl"),
        ("Sharpe", f"{sharpe:.2f}",
         g if sharpe > 1.0 else (r if sharpe < 0 else "#a16207"),
         "annualized · hourly"),
        ("Max drawdown", f"-{max_dd:.1f}%",
         g if max_dd < 10 else (r if max_dd > 25 else "#a16207"),
         "peak to trough"),
        ("Win rate", f"{win_rate:.0f}%",
         g if win_rate > 60 else (r if win_rate < 40 else "#a16207"),
         f"{num_trades} cycles"),
        ("Expectancy", f"${expectancy:,.0f}",
         g if expectancy > 0 else r,
         "avg per cycle"),
        ("Capital deployed", f"{metrics.get('holding_pct', 0):.0f}%",
         n, "of all hours"),
    ]
    n_tiles = len(tiles)
    left, right, top, bottom = 0.03, 0.97, 0.89, 0.81
    width = (right - left) / n_tiles
    for i, (label, value, accent, hint) in enumerate(tiles):
        ax = fig.add_axes([left + i * width, bottom, width * 0.97, top - bottom])
        _draw_kpi_tile(ax, label, value, accent, hint)


def _draw_equity_and_drawdown(fig, backtest: dict[str, Any]) -> None:
    equity = backtest.get("equity") or []
    trades = backtest.get("trades") or []
    if not equity:
        return
    ts = [datetime.fromtimestamp(e["t"] / 1000, tz=timezone.utc) for e in equity]
    eq = np.array([e["equity"] for e in equity], dtype=float)
    peak = np.maximum.accumulate(eq)
    dd_pct = (eq - peak) / peak * 100.0

    # Equity (top)
    ax = fig.add_axes([0.04, 0.50, 0.55, 0.27])
    ax.plot(ts, eq, color="#1e3a8a", linewidth=1.4)
    ax.fill_between(ts, eq, peak, where=eq < peak, color="#fca5a5", alpha=0.35)
    ax.fill_between(ts, eq, eq[0], where=eq >= eq[0], color="#86efac", alpha=0.18)
    ax.axhline(eq[0], color="#9ca3af", linewidth=0.6, linestyle=":")
    # cycle exits as dots
    cycles = _cycles(trades)
    eq_times = [e["t"] for e in equity]
    for c in cycles:
        et = datetime.fromtimestamp(c["exit_time"] / 1000, tz=timezone.utc)
        y = float(np.interp(c["exit_time"], eq_times, eq))
        ax.scatter([et], [y], s=24,
                   color=REASON_COLORS.get(c["reason"].split("+")[0], "#555"),
                   edgecolors="white", linewidths=0.5, zorder=3)
    ax.set_title("Equity curve", loc="left", fontsize=10, color="#374151", weight="bold")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.set_ylabel("equity ($)", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.2)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    # Drawdown (bottom)
    ax2 = fig.add_axes([0.04, 0.38, 0.55, 0.10])
    ax2.fill_between(ts, dd_pct, 0, color="#dc2626", alpha=0.30)
    ax2.plot(ts, dd_pct, color="#991b1b", linewidth=0.9)
    ax2.set_title("Drawdown", loc="left", fontsize=10, color="#374151", weight="bold")
    ax2.set_ylabel("dd (%)", fontsize=9)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax2.tick_params(labelsize=8)
    ax2.grid(alpha=0.2)
    for spine in ("top", "right"):
        ax2.spines[spine].set_visible(False)


def _draw_payoff(fig, strat: dict[str, Any], backtest: dict[str, Any] | None) -> None:
    legs = strat.get("legs", [])
    if not legs:
        return
    S = np.linspace(SPOT * 0.55, SPOT * 1.45, 400)
    total = np.zeros_like(S)
    for leg in legs:
        K = leg_strike(leg)
        prem = leg_entry_premium(leg, K)
        total += leg_payoff_vec(leg, S, K, prem)

    ax = fig.add_axes([0.62, 0.62, 0.35, 0.18])
    ax.plot(S, total, color="#1e3a8a", linewidth=2.0)
    ax.fill_between(S, total, 0, where=total >= 0, color="#86efac", alpha=0.40)
    ax.fill_between(S, total, 0, where=total < 0, color="#fca5a5", alpha=0.40)
    ax.axhline(0, color="#9ca3af", linewidth=0.6)
    ax.axvline(SPOT, color="#9ca3af", linewidth=0.6, linestyle=":")
    # Breakeven markers
    sign = np.sign(total)
    zero_x = []
    for i in range(1, len(S)):
        if sign[i - 1] * sign[i] < 0:
            zero_x.append(S[i])
    for zx in zero_x[:2]:
        ax.axvline(zx, color="#a16207", linewidth=0.6, linestyle="--", alpha=0.7)
        ax.text(zx, ax.get_ylim()[1] * 0.92, f"BE {zx:.0f}", fontsize=7,
                color="#a16207", ha="center")
    ax.set_title(f"Payoff @ expiry  ·  IV {int(ASSUMED_IV*100)}% (assumed)",
                 loc="left", fontsize=10, color="#374151", weight="bold")
    ax.set_xlabel(f"{strat.get('underlying','?')} (normalised, spot ≡ 100)", fontsize=8)
    ax.set_ylabel("P&L per unit", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.2)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)


def _draw_greeks_strip(fig, strat: dict[str, Any]) -> None:
    legs = strat.get("legs", [])
    if not legs:
        return
    ax = fig.add_axes([0.62, 0.595, 0.35, 0.022])
    ax.axis("off")
    pg = portfolio_greeks(legs)
    txt = (f"Δ {pg['delta']:+.2f}    "
           f"Γ {pg['gamma']:+.3f}    "
           f"Vega/1% {pg['vega']:+.2f}    "
           f"Θ/day {pg['theta']:+.2f}")
    ax.text(0.5, 0.5, txt, transform=ax.transAxes, ha="center", va="center",
            fontsize=9, family="monospace", color="#374151",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#eef2ff",
                      edgecolor="#c7d2fe"))


def _draw_rules_card(fig, strat: dict[str, Any]) -> None:
    ax = fig.add_axes([0.62, 0.38, 0.35, 0.21])
    ax.axis("off")
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.0, 0.0), 1.0, 1.0, transform=ax.transAxes,
        boxstyle="round,pad=0.01", linewidth=1, edgecolor="#e5e7eb",
        facecolor="#fafafa"))

    legs = strat.get("legs", [])
    entry = strat.get("entry", {})
    exit_ = strat.get("exit", {})
    hedge = strat.get("deltaHedge", {}) or {}
    e_rows = _entry_lines(entry)
    x_rows = _exit_lines(exit_)
    e_gate = _gate_label(entry.get("gateMode", "all"), entry.get("gateMin", 1), len(e_rows))
    x_gate = _gate_label(exit_.get("gateMode", "any"), exit_.get("gateMin", 1), len(x_rows))

    y = 0.93
    ax.text(0.03, y, f"LEGS ({len(legs)})", transform=ax.transAxes,
            fontsize=9, color="#374151", weight="bold")
    y -= 0.06
    for leg in legs[:6]:
        side = leg.get("side", "?")
        kind = leg.get("optionType") if leg.get("type") == "option" else "PERP"
        sm = leg.get("strikeMode", "delta"); p = leg.get("strikeParam", 0)
        if sm == "delta":
            strike = f"{p}Δ"
        elif sm == "otm_pct":
            strike = f"{p:.0%} OTM"
        else:
            strike = "ATM"
        dte = leg.get("dteTarget", "?")
        size_unit = "ctx" if leg.get("sizeMode", "contracts") == "contracts" else "% cap"
        sz = leg.get("size", 1.0)
        sz_str = f"{sz}" if size_unit == "ctx" else f"{int(sz*100)}%"
        line = f"  • {side:<4} {kind:<5} {strike:<8} {dte}d   size {sz_str} {size_unit}"
        ax.text(0.03, y, line, transform=ax.transAxes,
                fontsize=8.5, color="#1f2937", family="monospace")
        y -= 0.05

    y -= 0.02
    ax.text(0.03, y, f"ENTRY · gate {e_gate}", transform=ax.transAxes,
            fontsize=9, color="#a16207", weight="bold")
    y -= 0.05
    if not e_rows:
        ax.text(0.03, y, "  (always pass)", transform=ax.transAxes,
                fontsize=8.5, color="#6b7280", family="monospace"); y -= 0.05
    for row in e_rows[:4]:
        ax.text(0.03, y, f"  • {row}", transform=ax.transAxes,
                fontsize=8.5, color="#1f2937", family="monospace"); y -= 0.045

    y -= 0.01
    ax.text(0.03, y, f"EXIT · gate {x_gate}" + (
                f"   ·   Δ-hedge band {hedge.get('band', 0.1):g}" if hedge.get("enabled") else ""),
            transform=ax.transAxes, fontsize=9, color="#dc2626", weight="bold")
    y -= 0.05
    if not x_rows:
        ax.text(0.03, y, "  (no exit conditions)", transform=ax.transAxes,
                fontsize=8.5, color="#6b7280", family="monospace"); y -= 0.05
    for row in x_rows[:4]:
        ax.text(0.03, y, f"  • {row}", transform=ax.transAxes,
                fontsize=8.5, color="#1f2937", family="monospace"); y -= 0.045


def _draw_monthly_heatmap(fig, backtest: dict[str, Any]) -> None:
    equity = backtest.get("equity") or []
    if not equity:
        return
    monthly = _monthly_returns(equity)
    if not monthly:
        return
    years = sorted({y for y, _ in monthly.keys()})
    months = list(range(1, 13))
    grid = np.full((len(years), 12), np.nan)
    for (y, m), v in monthly.items():
        grid[years.index(y), m - 1] = v

    ax = fig.add_axes([0.04, 0.07, 0.40, 0.26])
    vmax = max(1.0, np.nanmax(np.abs(grid)))
    im = ax.imshow(grid, cmap="RdYlGn", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(12), ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], fontsize=8)
    ax.set_yticks(range(len(years)), [str(y) for y in years], fontsize=8)
    for i in range(len(years)):
        for j in range(12):
            v = grid[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                        fontsize=8, color="#111827" if abs(v) < vmax * 0.6 else "white",
                        weight="bold")
    ax.set_title("Monthly returns (%)", loc="left", fontsize=10, color="#374151", weight="bold")
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _draw_reason_breakdown(fig, backtest: dict[str, Any]) -> None:
    cycles = _cycles((backtest or {}).get("trades", []) or [])
    if not cycles:
        return
    counts: Counter[str] = Counter()
    pnls: dict[str, float] = defaultdict(float)
    for c in cycles:
        r = c["reason"].split("+")[0]
        counts[r] += 1
        pnls[r] += c["pnl"]
    reasons = sorted(counts.keys(), key=lambda r: -counts[r])

    ax = fig.add_axes([0.50, 0.07, 0.47, 0.26])
    y_pos = np.arange(len(reasons))
    counts_arr = [counts[r] for r in reasons]
    pnl_arr = [pnls[r] for r in reasons]
    colors = [REASON_COLORS.get(r, "#9ca3af") for r in reasons]
    bars = ax.barh(y_pos, counts_arr, color=colors, alpha=0.85, edgecolor="white", linewidth=1)
    for i, (bar, c, p) in enumerate(zip(bars, counts_arr, pnl_arr)):
        ax.text(bar.get_width() + max(counts_arr) * 0.02, bar.get_y() + bar.get_height() / 2,
                f"{c} cycles · ${p:,.0f}", va="center", fontsize=8.5, color="#374151")
    ax.set_yticks(y_pos)
    ax.set_yticklabels([_reason_full(r) for r in reasons], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("cycle count", fontsize=8)
    ax.set_title("Exit reasons", loc="left", fontsize=10, color="#374151", weight="bold")
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.2, axis="x")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.set_xlim(0, max(counts_arr) * 1.35)


def _reason_full(r: str) -> str:
    return {
        "TP": "TP  · profit target",
        "SL": "SL  · stop loss",
        "DTE": "DTE · DTE floor",
        "EXPIRY": "EXP · expired",
        "MAX": "MAX · max hold",
        "DTL": "DTL · liq distance",
        "IVP": "IVP · IV percentile",
        "REHEDGE": "HDG · rehedge",
    }.get(r, r)


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────


def render(strat: dict[str, Any], backtest: dict[str, Any] | None, out_path: Path) -> None:
    fig = plt.figure(figsize=(14, 10), facecolor="#ffffff")
    _draw_header(fig, strat, backtest)
    _draw_kpi_strip(fig, strat, backtest)
    if backtest:
        _draw_equity_and_drawdown(fig, backtest)
    _draw_payoff(fig, strat, backtest)
    _draw_greeks_strip(fig, strat)
    _draw_rules_card(fig, strat)
    if backtest:
        _draw_monthly_heatmap(fig, backtest)
        _draw_reason_breakdown(fig, backtest)
    fig.savefig(ensure_parent(out_path), dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


