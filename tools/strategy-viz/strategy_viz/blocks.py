"""Composable building blocks for strategy summaries.

Each block is a small, self-contained function that emits a list of
`webchat-ui-renderer` primitives (the canonical, composable backend).
A "card" is just an ordered list of block IDs.

Two reasons to factor this out:

1. *Simple.* Each block reads in <30 lines. You can grep for "header" and
   see exactly what shows up under that label, in one place.
2. *Reusable.* You can ship "just the legs" or "just the Greeks" by
   listing one block ID — no copy-pasting between files. New layouts
   (preview / full / KPI-only) are one-line `LAYOUTS` entries.

Each block must:
- have a unique `id`
- accept `(strat, bt)` where `bt` may be `None`
- return `list[dict]` of primitive specs (possibly empty if not applicable)

Blocks must NEVER throw on missing fields. Skip with `[]` instead.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .common import cycles_from_trades, gate_label, hrs
from .pricing import ASSUMED_IV, leg_greeks_at_entry, payoff_curve, portfolio_greeks
from .specs import entry_lines, exit_lines, thesis


# ───────────────────────────────────────────────────────────────────────
# Small helpers used by multiple blocks
# ───────────────────────────────────────────────────────────────────────


def _leg_strike_label(leg: dict[str, Any]) -> str:
    sm = leg.get("strikeMode", "delta")
    p = leg.get("strikeParam", 0)
    if sm == "delta":
        return f"{p}Δ"
    if sm == "otm_pct":
        return f"{int(p * 100)}% OTM"
    if sm == "atm":
        return "ATM"
    return f"{sm}({p})"


def _short_leg_label(leg: dict[str, Any]) -> str:
    if leg.get("type") == "perp":
        return f"{leg.get('side','?')} PERP"
    return (f"{leg.get('side','?')} {leg.get('optionType','?')} "
            f"{_leg_strike_label(leg)} {leg.get('dteTarget','?')}d")


# ───────────────────────────────────────────────────────────────────────
# Block functions — one per visual concept
# ───────────────────────────────────────────────────────────────────────


def header_block(strat: dict[str, Any], _bt) -> list[dict]:
    name = strat.get("name", "strategy")
    capital = strat.get("capital", 0)
    freq = (strat.get("entry") or {}).get("frequency", 168)
    return [
        {"component": "markdown",
         "props": {"content": f"### {name}\n*{thesis(strat)}*"}},
        {"component": "labeled_output",
         "props": {"label": "Underlying", "value": strat.get("underlying", "?")}},
        {"component": "labeled_output",
         "props": {"label": "Capital", "value": f"${capital:,.0f}"}},
        {"component": "labeled_output",
         "props": {"label": "Margin", "value": strat.get("marginMode", "XM")}},
        {"component": "labeled_output",
         "props": {"label": "Cycle", "value": hrs(freq)}},
    ]


def legs_block(strat: dict[str, Any], _bt) -> list[dict]:
    legs = strat.get("legs", [])
    if not legs:
        return []
    rows = []
    for leg in legs:
        size_unit = "ctx" if leg.get("sizeMode", "contracts") == "contracts" else "% cap"
        sz = leg.get("size", 1.0)
        size = f"{sz}" if size_unit == "ctx" else f"{int(sz*100)}%"
        rows.append({
            "side": leg.get("side", "?"),
            "type": leg.get("optionType") if leg.get("type") == "option" else "PERP",
            "strike": _leg_strike_label(leg) if leg.get("type") == "option" else "—",
            "dte": f"{leg.get('dteTarget', '?')}d" if leg.get("type") == "option" else "—",
            "size": f"{size} {size_unit}",
        })
    return [{
        "component": "data_table",
        "props": {
            "columns": [
                {"key": "side", "header": "Side", "align": "left"},
                {"key": "type", "header": "Type", "align": "left"},
                {"key": "strike", "header": "Strike", "align": "left"},
                {"key": "dte", "header": "DTE", "align": "right"},
                {"key": "size", "header": "Size", "align": "right"},
            ],
            "rows": rows,
        },
    }]


def entry_block(strat: dict[str, Any], _bt) -> list[dict]:
    entry = strat.get("entry", {}) or {}
    rows_text = entry_lines(entry)
    n = len(rows_text)
    gate = gate_label(entry.get("gateMode", "all"), entry.get("gateMin", 1), n)
    freq = hrs(entry.get("frequency", 168))
    rows = ([{"signal": r, "status": "✓ enabled"} for r in rows_text]
            or [{"signal": "(no signal filters — enter every cycle)", "status": ""}])
    return [{
        "component": "data_table",
        "props": {
            "columns": [
                {"key": "signal", "header": f"Entry · gate {gate} · every {freq}", "align": "left"},
                {"key": "status", "header": "", "align": "right"},
            ],
            "rows": rows,
        },
    }]


def exit_block(strat: dict[str, Any], _bt) -> list[dict]:
    exit_ = strat.get("exit", {}) or {}
    rows_text = exit_lines(exit_)
    rows = [{"trigger": r, "status": "✓ enabled"} for r in rows_text]
    has_option = any(l.get("type") == "option" for l in strat.get("legs", []))
    if has_option:
        rows.append({"trigger": "EXPIRY override · any leg DTE=0 closes all", "status": "always"})
    if not rows:
        rows = [{"trigger": "(no exit conditions)", "status": ""}]
    n_real = sum(1 for r in rows if r["status"] == "✓ enabled")
    gate = gate_label(exit_.get("gateMode", "any"), exit_.get("gateMin", 1), n_real)
    return [{
        "component": "data_table",
        "props": {
            "columns": [
                {"key": "trigger", "header": f"Exit · gate {gate}", "align": "left"},
                {"key": "status", "header": "", "align": "right"},
            ],
            "rows": rows,
        },
    }]


def risk_banner_block(strat: dict[str, Any], bt) -> list[dict]:
    msgs: list[str] = []
    hedge = strat.get("deltaHedge", {}) or {}
    if hedge.get("enabled"):
        msgs.append(f"Delta hedge ON · band {hedge.get('band', 0.1):g}")
    if (strat.get("maxImrPctEntry") or 0) > 70:
        msgs.append(f"High IMR ceiling: {strat['maxImrPctEntry']}%")
    variant = "info"
    if bt:
        m = bt.get("metrics") or {}
        dtl = m.get("min_dtl")
        if dtl is not None and dtl < 5:
            msgs.append(f"Historical min distance-to-liquidation: {dtl:.1f}%")
            variant = "warning"
        if (m.get("max_dd") or 0) > 25:
            msgs.append(f"Historical max drawdown: {m['max_dd']:.1f}%")
            variant = "warning"
    if not msgs:
        return []
    return [{"component": "alert_banner",
             "props": {"variant": variant, "message": " · ".join(msgs)}}]


def greeks_block(strat: dict[str, Any], _bt) -> list[dict]:
    legs = strat.get("legs", [])
    if not legs:
        return []
    rows = []
    for leg in legs:
        g = leg_greeks_at_entry(leg)
        rows.append({
            "leg": _short_leg_label(leg),
            "delta": f"{g['delta']:+.3f}",
            "gamma": f"{g['gamma']:+.4f}",
            "vega": f"{g['vega']:+.3f}",
            "theta": f"{g['theta']:+.3f}",
        })
    pg = portfolio_greeks(legs)
    rows.append({"leg": "Σ portfolio",
                 "delta": f"{pg['delta']:+.3f}", "gamma": f"{pg['gamma']:+.4f}",
                 "vega": f"{pg['vega']:+.3f}", "theta": f"{pg['theta']:+.3f}"})
    return [{
        "component": "data_table",
        "props": {
            "columns": [
                {"key": "leg",
                 "header": f"Greeks at entry (spot ≡ 100, IV {int(ASSUMED_IV*100)}%)",
                 "align": "left"},
                {"key": "delta", "header": "Δ", "align": "right"},
                {"key": "gamma", "header": "Γ", "align": "right"},
                {"key": "vega", "header": "Vega/1%", "align": "right"},
                {"key": "theta", "header": "Θ/day", "align": "right"},
            ],
            "rows": rows,
        },
    }]


def payoff_block(strat: dict[str, Any], _bt) -> list[dict]:
    spots, total = payoff_curve(strat.get("legs", []), n_points=60)
    if not spots:
        return []
    values = [{"name": f"{int(s)}", "value": round(v, 3)} for s, v in zip(spots, total)]
    return [
        {"component": "markdown",
         "props": {"content": "### Theoretical payoff at expiry"}},
        {"component": "performance_chart",
         "props": {
             "label": f"Payoff @ expiry (assumed IV {int(ASSUMED_IV*100)}%, spot ≡ 100)",
             "values": values,
             "tooltip": True, "grid": True, "yAxis": True,
         }},
    ]


def bt_heading_block(_strat, bt) -> list[dict]:
    if not bt:
        return []
    suffix = "  *(synthetic fixture)*" if bt.get("_synthetic") else ""
    return [{"component": "markdown",
             "props": {"content": f"### Backtest results{suffix}"}}]


def bt_kpis_block(_strat, bt) -> list[dict]:
    if not bt:
        return []
    m = bt.get("metrics") or {}
    return [
        {"component": "metric_card",
         "props": {"label": "Total return",
                   "value": f"{m.get('total_return', 0):+.1f}%",
                   "direction": "up" if (m.get("total_return") or 0) > 0 else "down"}},
        {"component": "metric_card",
         "props": {"label": "Sharpe", "value": f"{m.get('sharpe', 0):.2f}"}},
        {"component": "metric_card",
         "props": {"label": "Max DD",
                   "value": f"-{m.get('max_dd', 0):.1f}%", "direction": "down"}},
        {"component": "metric_card",
         "props": {"label": "Win %",
                   "value": f"{m.get('win_rate', 0):.0f}% · {m.get('num_trades', 0)}",
                   "direction": "up" if (m.get("win_rate") or 0) > 50 else "down"}},
    ]


def bt_equity_block(_strat, bt) -> list[dict]:
    equity = (bt or {}).get("equity") or []
    if not equity:
        return []
    step = max(1, len(equity) // 120)
    pts = equity[::step]
    if pts[-1] is not equity[-1]:
        pts.append(equity[-1])
    values = []
    for e in pts:
        d = datetime.fromtimestamp(e["t"] / 1000, tz=timezone.utc)
        values.append({"name": d.strftime("%m-%d"), "value": round(float(e["equity"]), 2)})
    return [{"component": "performance_chart",
             "props": {"label": "Equity curve", "values": values,
                       "tooltip": True, "grid": True, "yAxis": True}}]


def bt_trades_block(_strat, bt) -> list[dict]:
    cycles = cycles_from_trades((bt or {}).get("trades") or [])
    if not cycles:
        return []
    rows = []
    for c in cycles[:30]:
        et = datetime.fromtimestamp(c["entry_time"] / 1000, tz=timezone.utc)
        xt = datetime.fromtimestamp(c["exit_time"] / 1000, tz=timezone.utc)
        rows.append({
            "entry": et.strftime("%m-%d %H:%M"),
            "exit": xt.strftime("%m-%d %H:%M"),
            "reason": c["reason"],
            "pnl": f"${c['pnl']:,.0f}",
        })
    return [{
        "component": "data_table",
        "props": {
            "columns": [
                {"key": "entry", "header": "Entry (UTC)", "align": "left"},
                {"key": "exit", "header": "Exit (UTC)", "align": "left"},
                {"key": "reason", "header": "Reason", "align": "left"},
                {"key": "pnl", "header": "PnL", "align": "right"},
            ],
            "rows": rows,
        },
    }]


# ───────────────────────────────────────────────────────────────────────
# Catalog + layouts
# ───────────────────────────────────────────────────────────────────────


CATALOG: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], list[dict]]] = {
    "header":       header_block,
    "legs":         legs_block,
    "entry":        entry_block,
    "exit":         exit_block,
    "risk_banner":  risk_banner_block,
    "greeks":       greeks_block,
    "payoff":       payoff_block,
    "bt_heading":   bt_heading_block,
    "bt_kpis":      bt_kpis_block,
    "bt_equity":    bt_equity_block,
    "bt_trades":    bt_trades_block,
}


LAYOUTS: dict[str, list[str]] = {
    # The strategy summary card you'd send before placing the trade.
    "preview": [
        "header", "legs", "entry", "exit", "risk_banner",
        "greeks", "payoff",
    ],
    # Same but with backtest results spliced in.
    "full": [
        "header", "legs", "entry", "exit", "risk_banner",
        "bt_heading", "bt_kpis", "bt_equity", "bt_trades",
        "greeks", "payoff",
    ],
    # Just the position structure (used by tests/docs).
    "legs_only": ["header", "legs"],
}
# For ad-hoc compositions, pass a list of block IDs directly to render().


def render(strat: dict[str, Any], bt: dict[str, Any] | None,
           layout: str | list[str] = "preview",
           spec_id: str | None = None) -> dict[str, Any]:
    """Render a strategy summary as one webchat-renderer stack spec.

    `layout` is either a layout name from LAYOUTS or a list of block ids.
    Unknown ids raise; missing-data blocks self-skip via empty returns.
    """
    block_ids = LAYOUTS[layout] if isinstance(layout, str) else list(layout)
    unknown = [b for b in block_ids if b not in CATALOG]
    if unknown:
        raise KeyError(f"unknown block ids: {unknown}")
    children: list[dict] = []
    for bid in block_ids:
        children.extend(CATALOG[bid](strat, bt))
    spec_id = spec_id or f"strategy-{strat.get('name', 'x')}"
    return {"id": spec_id, "layout": "stack", "children": children}
