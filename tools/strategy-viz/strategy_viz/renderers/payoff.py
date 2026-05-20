"""Render a strategy payoff card as a PNG (matplotlib).

P&L-at-expiry curve on the left; entry/exit rules card stack on the right.
When `backtest` is supplied, overlays each historical cycle as a dot at
(exit_spot_normalised, pnl_per_unit) colored by exit reason.

Public API: `render(strat, out_path, backtest=None) -> None`.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from ..common import REASON_COLORS, cycles_from_trades, ensure_parent, hrs as _hrs, gate_label as _gate_label
from ..pricing import SPOT, ASSUMED_IV, R, leg_strike, leg_entry_premium
from ..specs import entry_lines as _entry_lines, exit_lines as _exit_lines


def leg_payoff_vec(leg: dict[str, Any], S: np.ndarray, K: float, prem: float) -> np.ndarray:
    """Vectorised leg payoff (numpy). The scalar version is `pricing.leg_payoff_at`."""
    sign = 1.0 if leg["side"] == "BUY" else -1.0
    size = leg.get("size", 1.0)
    if leg["type"] == "perp":
        return sign * size * (S - SPOT)
    opt = leg.get("optionType", "CALL")
    intrinsic = np.maximum(S - K, 0) if opt == "CALL" else np.maximum(K - S, 0)
    return sign * size * (intrinsic - prem)


def render(strat: dict[str, Any], out_path: Path, backtest: dict[str, Any] | None = None) -> None:
    legs = strat.get("legs", [])
    entry = strat.get("entry", {})
    exit_ = strat.get("exit", {})
    hedge = strat.get("deltaHedge", {})
    name = strat.get("name", "strategy")
    underlying = strat.get("underlying", "?")
    capital = strat.get("capital", 0)
    margin = strat.get("marginMode", "XM")

    # Compute strikes, premia, payoff
    S = np.linspace(SPOT * 0.5, SPOT * 1.5, 600)
    total = np.zeros_like(S)
    leg_info = []
    for leg in legs:
        K = leg_strike(leg)
        prem = leg_entry_premium(leg, K)
        pl = leg_payoff_vec(leg, S, K, prem)
        total += pl
        leg_info.append((leg, K, prem, pl))

    fig = plt.figure(figsize=(14, 8.5), facecolor="white")
    gs = fig.add_gridspec(3, 2, width_ratios=[2.2, 1.4], height_ratios=[1, 1, 1], hspace=0.45, wspace=0.18)

    # Payoff plot (left, spans all 3 rows)
    ax = fig.add_subplot(gs[:, 0])
    for (leg, K, prem, pl) in leg_info:
        lbl = leg["side"] + " " + (leg.get("optionType", "PERP") if leg["type"] == "option" else "PERP")
        ax.plot(S, pl, alpha=0.35, linewidth=1.1, linestyle="--", label=f"{lbl}  K≈{K:.1f}")
    ax.plot(S, total, color="#1f3a93", linewidth=2.6, label="net payoff")
    ax.fill_between(S, total, 0, where=total >= 0, color="#2ecc71", alpha=0.18)
    ax.fill_between(S, total, 0, where=total < 0, color="#e74c3c", alpha=0.18)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.axvline(SPOT, color="#888", linewidth=0.8, linestyle=":")
    ax.set_xlabel(f"{underlying} price at expiry (spot ≡ 100)")
    ax.set_ylabel("P&L per unit  (normalized to spot=100)")
    title_suffix = ""
    if backtest:
        m = backtest.get("metrics", {}) or {}
        title_suffix = (
            f"  ·  realized: {m.get('num_trades', 0)} cycles, "
            f"{m.get('win_rate', 0):.0f}% win, "
            f"PnL ${m.get('total_pnl', 0):,.0f}, "
            f"Sharpe {m.get('sharpe', 0):.2f}"
        )
    ax.set_title(
        f"{name}\npayoff at expiry  ·  assumed IV {int(ASSUMED_IV*100)}%, r {int(R*100)}%{title_suffix}",
        loc="left",
    )

    # Overlay realized cycles
    if backtest:
        trades = backtest.get("trades", []) or []
        cycles = cycles_from_trades(trades)
        if cycles:
            # Normalise exit_spot to the SPOT=100 baseline used by the payoff curve.
            # We assume the first observed entry corresponds to ~SPOT (close enough for viz).
            equity = backtest.get("equity") or []
            ref_spot = equity[0]["spot"] if equity else (cycles[0].get("exit_spot") or 1.0)
            ref_capital = strat.get("capital", 1.0)
            xs, ys, cs = [], [], []
            seen_reasons: set[str] = set()
            for c in cycles:
                es = c.get("exit_spot")
                if not es or not ref_spot:
                    continue
                xs.append(es / ref_spot * SPOT)
                # Normalise cycle PnL to "per unit" (divide by capital then scale to spot=100).
                ys.append(c["pnl"] / ref_capital * SPOT)
                primary = c["reason"].split("+")[0]
                cs.append(REASON_COLORS.get(primary, "#555"))
                seen_reasons.add(primary)
            ax.scatter(xs, ys, c=cs, s=44, edgecolors="white", linewidths=0.7,
                       alpha=0.85, zorder=5, label="_realized")
            # legend for reasons
            from matplotlib.lines import Line2D
            handles = [
                Line2D([0], [0], marker="o", color="w", markerfacecolor=REASON_COLORS[r],
                       markersize=7, label=r)
                for r in sorted(seen_reasons) if r in REASON_COLORS
            ]
            if handles:
                ax.add_artist(ax.legend(handles=handles, loc="upper right",
                                        title="exit reason", fontsize=8, frameon=True))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=8, frameon=False)
    ax.grid(alpha=0.25)

    # Header card
    ax0 = fig.add_subplot(gs[0, 1])
    ax0.axis("off")
    header = (
        f"$\\bf{{{underlying}}}$  ·  ${capital:,.0f}  ·  margin {margin}\n"
        f"cycle every {_hrs(entry.get('frequency', 168))}"
    )
    ax0.add_patch(mpatches.FancyBboxPatch((0.02, 0.10), 0.96, 0.85,
        boxstyle="round,pad=0.02", linewidth=1, edgecolor="#28a745", facecolor="#e9f7ef", transform=ax0.transAxes))
    ax0.text(0.5, 0.55, header, ha="center", va="center", fontsize=11, transform=ax0.transAxes)

    ax1 = fig.add_subplot(gs[1, 1])
    ax1.axis("off")
    e_rows = _entry_lines(entry)
    e_gate = _gate_label(entry.get("gateMode", "all"), entry.get("gateMin", 1), len(e_rows))
    body = f"Entry gate: {e_gate}\n" + ("\n".join(f"• {r}" for r in e_rows) if e_rows else "  (no conditions — always enter)")
    ax1.add_patch(mpatches.FancyBboxPatch((0.02, 0.05), 0.96, 0.90,
        boxstyle="round,pad=0.02", linewidth=1, edgecolor="#f0ad4e", facecolor="#fff3cd", transform=ax1.transAxes))
    ax1.text(0.05, 0.93, body, ha="left", va="top", fontsize=10, transform=ax1.transAxes, family="monospace")

    # Exit card
    ax2 = fig.add_subplot(gs[2, 1])
    ax2.axis("off")
    x_rows = _exit_lines(exit_)
    x_gate = _gate_label(exit_.get("gateMode", "any"), exit_.get("gateMin", 1), len(x_rows))
    hedge_line = ""
    if hedge.get("enabled"):
        hedge_line = f"Δ-hedge: band {hedge.get('band', 0.1):g}\n"
    body = hedge_line + f"Exit gate: {x_gate}\n" + ("\n".join(f"• {r}" for r in x_rows) if x_rows else "  (no conditions)")
    ax2.add_patch(mpatches.FancyBboxPatch((0.02, 0.05), 0.96, 0.90,
        boxstyle="round,pad=0.02", linewidth=1, edgecolor="#c0392b", facecolor="#fde2e2", transform=ax2.transAxes))
    ax2.text(0.05, 0.93, body, ha="left", va="top", fontsize=10, transform=ax2.transAxes, family="monospace")

    fig.suptitle("Strategy payoff & rules card", fontsize=13, y=0.995)
    fig.savefig(ensure_parent(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

