"""Generate a plausible synthetic backtest results file for a strategy.

The real backtester (skills/strategy-backtester/scripts/paradex_backtest_engine.py)
needs live Paradex/Deribit data. For viz development we just need a fixture in
the same shape: {equity, trades, metrics, error}.

Usage: python3 gen_synthetic_backtest.py samples/iron_condor_btc.json out.bt.json
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path
from typing import Any


REASONS = ["TP", "SL", "DTE", "EXPIRY", "MAX", "DTL"]


def _seeded(name: str) -> random.Random:
    return random.Random(hash(name) & 0xFFFFFFFF)


def _spot_path(rng: random.Random, n_bars: int, s0: float = 60000.0,
               sigma_hourly: float = 0.012, drift: float = 0.0) -> list[float]:
    spots = [s0]
    for _ in range(n_bars - 1):
        z = rng.gauss(0, 1)
        spots.append(spots[-1] * math.exp(drift + sigma_hourly * z))
    return spots


def generate(strat: dict[str, Any]) -> dict[str, Any]:
    name = strat.get("name", "strategy")
    capital = float(strat.get("capital", 100000))
    underlying = strat.get("underlying", "BTC")
    rng = _seeded(name)

    bt = strat.get("backtest", {}) or {}
    # Hourly bars for ~117 days (Jan 1 -> Apr 27) ≈ 2808 bars
    n_bars = 2808
    base_t = 1735689600 * 1000  # 2025-01-01 UTC in ms; close enough to sample windows

    s0 = {"BTC": 60000.0, "ETH": 3000.0, "SOL": 150.0}.get(underlying, 100.0)
    spots = _spot_path(rng, n_bars, s0=s0, sigma_hourly=0.012, drift=0.00001 * rng.uniform(-1, 1))

    # Backtest tone: bias profitability by gateMode strictness (loose proxy)
    gate_mode = (strat.get("entry") or {}).get("gateMode", "all")
    has_hedge = bool((strat.get("deltaHedge") or {}).get("enabled"))
    bias = 0.0
    if gate_mode == "all": bias += 0.005
    if gate_mode == "any": bias -= 0.003
    if has_hedge: bias += 0.002

    # Hold mask + trade events
    freq = (strat.get("entry") or {}).get("frequency", 168)
    holding = [False] * n_bars
    trades: list[dict] = []
    equity_vals = [capital]
    cash = capital
    open_pnl_mtm = 0.0
    cycle_pnl = 0.0
    in_position = False
    bars_held = 0
    entry_bar = 0
    entry_spot = spots[0]
    leg_count = max(1, len(strat.get("legs", [])))

    for i in range(1, n_bars):
        s = spots[i]
        if not in_position:
            # Maybe enter on a cycle bar
            if i % freq == 0 and rng.random() < 0.65:
                in_position = True
                entry_bar = i
                entry_spot = s
                bars_held = 0
                cycle_pnl = 0.0
                # premium collected (positive cash for shorts; this is a synthetic proxy)
                cycle_premium = capital * 0.012 * leg_count * (1 + 0.5 * rng.random())
                cycle_pnl = 0.0  # will accrue
                # Stash on the position-state implicitly
                cycle_premium_state = cycle_premium
            else:
                holding[i] = False
                equity_vals.append(cash + open_pnl_mtm)
                continue

        # In a cycle: MTM drift + exit checks
        bars_held += 1
        spot_move = (s - entry_spot) / entry_spot
        # Synthetic MTM: short-vol/range strategies pay slowly, get hit on big moves
        decay = 0.6 * (bars_held / max(1, (strat.get("legs") or [{}])[0].get("dteTarget", 14) * 24))
        adverse = max(abs(spot_move) - 0.05, 0)
        cycle_pnl = cycle_premium_state * (decay - 8 * adverse) + capital * bias * 5
        open_pnl_mtm = cycle_pnl
        holding[i] = True

        # Exit check
        exit_reason = None
        pnl_pct_of_prem = (cycle_pnl / cycle_premium_state) * 100 if cycle_premium_state else 0
        ex = strat.get("exit", {}) or {}
        if (ex.get("profitTarget") or {}).get("enabled") and pnl_pct_of_prem >= ex["profitTarget"]["value"]:
            exit_reason = "TP"
        elif (ex.get("stopLoss") or {}).get("enabled") and pnl_pct_of_prem <= -ex["stopLoss"]["value"]:
            exit_reason = "SL"
        elif (ex.get("maxHold") or {}).get("enabled") and bars_held >= ex["maxHold"]["value"]:
            exit_reason = "MAX"
        else:
            dte_target = (strat.get("legs") or [{}])[0].get("dteTarget", 14)
            if bars_held >= dte_target * 24 - 24 and (ex.get("dteFloor") or {}).get("enabled"):
                exit_reason = "DTE"
            elif bars_held >= dte_target * 24:
                exit_reason = "EXPIRY"

        if exit_reason:
            cash += cycle_pnl
            open_pnl_mtm = 0.0
            for li, leg in enumerate(strat.get("legs", [])):
                trades.append({
                    "entry_time": base_t + entry_bar * 3600 * 1000,
                    "exit_time": base_t + i * 3600 * 1000,
                    "exit_spot": s,
                    "leg_type": leg.get("type", "option"),
                    "side": leg.get("side"),
                    "option_type": leg.get("optionType"),
                    "strike": entry_spot * (1 + ((-1 if leg.get("optionType") == "PUT" else 1) * 0.08)),
                    "dte_at_entry": leg.get("dteTarget"),
                    "entry_price": cycle_premium_state * 0.25 if leg.get("side") == "SELL" else cycle_premium_state * 0.15,
                    "exit_price": cycle_premium_state * 0.05,
                    "size": leg.get("size", 1.0),
                    "pnl": cycle_pnl / leg_count,
                    "funding": 0.0,
                    "reason": exit_reason,
                    "bars_held": bars_held,
                    "is_hedge": False,
                })
            in_position = False
            cycle_pnl = 0.0

        equity_vals.append(cash + open_pnl_mtm)

    # Build equity array with margin-ish fields
    equity = []
    for i, v in enumerate(equity_vals):
        equity.append({
            "t": base_t + i * 3600 * 1000,
            "equity": v,
            "spot": spots[i],
            "cash": v - (0 if not holding[i] else open_pnl_mtm),
            "has_positions": holding[i],
            "imr": 0.35 * v if holding[i] else 0.0,
            "mmr": 0.20 * v if holding[i] else 0.0,
            "dist_to_liq": (8 + 6 * rng.random()) if holding[i] else None,
            "liq_down": spots[i] * 0.85 if holding[i] else None,
            "liq_up": spots[i] * 1.18 if holding[i] else None,
        })

    # Metrics
    eqs = [e["equity"] for e in equity]
    final_eq = eqs[-1]
    total_pnl = final_eq - capital
    total_return = total_pnl / capital * 100
    rets = [(eqs[i] - eqs[i - 1]) / eqs[i - 1] for i in range(1, len(eqs)) if eqs[i - 1]]
    mean_r = sum(rets) / len(rets) if rets else 0
    var_r = sum((x - mean_r) ** 2 for x in rets) / len(rets) if rets else 0
    std_r = math.sqrt(var_r)
    sharpe = (mean_r / std_r * math.sqrt(8760)) if std_r > 0 else 0.0
    peak, max_dd = eqs[0], 0.0
    for v in eqs:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak else 0
        max_dd = max(max_dd, dd)
    # Win rate by entry_time grouping (matches engine logic)
    cycles: dict[int, dict] = {}
    for t in trades:
        cycles.setdefault(t["entry_time"], {"pnl": 0, "reason": t["reason"]})
        cycles[t["entry_time"]]["pnl"] += t["pnl"]
    wins = [c for c in cycles.values() if c["pnl"] > 0]
    losses = [c for c in cycles.values() if c["pnl"] <= 0]
    avg_win = sum(c["pnl"] for c in wins) / len(wins) if wins else 0.0
    avg_loss = sum(c["pnl"] for c in losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(cycles) * 100 if cycles else 0.0
    dtls = [e["dist_to_liq"] for e in equity if e.get("dist_to_liq") is not None]
    holding_bars = sum(1 for e in equity if e["has_positions"])

    metrics = {
        "total_pnl": total_pnl,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_dd": max_dd * 100,
        "win_rate": win_rate,
        "num_trades": len(cycles),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "final_equity": final_eq,
        "min_dtl": min(dtls) if dtls else None,
        "avg_dtl": sum(dtls) / len(dtls) if dtls else None,
        "holding_bars": holding_bars,
        "holding_pct": holding_bars / len(equity) * 100,
    }
    return {"equity": equity, "trades": trades, "metrics": metrics, "error": None,
            "_synthetic": True, "_strategy_name": name}


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: gen_synthetic_backtest.py <strategy.json> <out.bt.json>", file=sys.stderr)
        sys.exit(2)
    strat = json.loads(Path(sys.argv[1]).read_text())
    if "evaluators" in strat:
        print("listener form has no backtest; skipping", file=sys.stderr)
        sys.exit(0)
    bt = generate(strat)
    from strategy_viz.common import ensure_parent
    ensure_parent(Path(sys.argv[2])).write_text(json.dumps(bt, indent=2))


if __name__ == "__main__":
    main()
