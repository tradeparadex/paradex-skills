"""Strategy-JSON → human-readable label helpers. No numeric deps."""
from __future__ import annotations

from typing import Any

from .common import hrs


def entry_lines(entry: dict[str, Any]) -> list[str]:
    out: list[str] = []
    rv = entry.get("rvPctile") or {}
    if rv.get("enabled"):
        out.append(f"RV pctile {rv.get('op')} {rv.get('value')} · {hrs(rv.get('window', 168))}")
    iv = entry.get("ivPctile") or {}
    if iv.get("enabled"):
        out.append(f"IV pctile {iv.get('op')} {iv.get('value')} · {hrs(iv.get('window', 720))}")
    rsi = entry.get("rsi") or {}
    if rsi.get("enabled"):
        out.append(f"RSI(14) {rsi.get('op')} {rsi.get('value')}")
    sma = entry.get("sma") or {}
    if sma.get("enabled"):
        out.append(f"spot {sma.get('op')} SMA({hrs(sma.get('period', 168))})")
    fr = entry.get("fundingRate") or {}
    if fr.get("enabled"):
        out.append(f"funding {fr.get('op')} {fr.get('value')}/8h")
    return out


def thesis(strat: dict[str, Any]) -> str:
    """One-line auto-generated thesis from the strategy structure."""
    legs = strat.get("legs", [])
    n_sell = sum(1 for l in legs if l.get("side") == "SELL")
    n_buy = sum(1 for l in legs if l.get("side") == "BUY")
    n_perp = sum(1 for l in legs if l.get("type") == "perp")
    entry = strat.get("entry") or {}
    triggers: list[str] = []
    if (entry.get("ivPctile") or {}).get("enabled"):
        triggers.append("elevated IV" if entry["ivPctile"].get("op") == ">" else "low IV")
    if (entry.get("rvPctile") or {}).get("enabled"):
        triggers.append("compressed RV" if entry["rvPctile"].get("op") == "<" else "expanded RV")
    if (entry.get("rsi") or {}).get("enabled"):
        triggers.append("RSI oversold" if entry["rsi"].get("op") == "<" else "RSI overbought")
    if (entry.get("sma") or {}).get("enabled"):
        triggers.append("trend filter")
    trigger_str = ", ".join(triggers) if triggers else "no signal filter"
    shape: list[str] = []
    if n_sell:
        shape.append(f"sell {n_sell} option" + ("s" if n_sell != 1 else ""))
    if n_buy:
        shape.append(f"buy {n_buy} option" + ("s" if n_buy != 1 else ""))
    if n_perp:
        shape.append(f"{n_perp} perp leg" + ("s" if n_perp != 1 else ""))
    return f"{', '.join(shape)} · entry on {trigger_str}"


def expectancy(cycles: list[dict[str, Any]]) -> float:
    if not cycles:
        return 0.0
    return sum(c["pnl"] for c in cycles) / len(cycles)


def exit_lines(exit_: dict[str, Any]) -> list[str]:
    out: list[str] = []
    pt = exit_.get("profitTarget") or {}
    if pt.get("enabled"):
        out.append(f"profit ≥ {pt.get('value')}% of premium")
    sl = exit_.get("stopLoss") or {}
    if sl.get("enabled"):
        out.append(f"loss ≥ {sl.get('value')}% of premium")
    iv = exit_.get("ivPctile") or {}
    if iv.get("enabled"):
        out.append(f"IV pctile {iv.get('op')} {iv.get('value')} · {hrs(iv.get('window', 720))}")
    df = exit_.get("dteFloor") or {}
    if df.get("enabled"):
        out.append(f"any leg DTE ≤ {df.get('value')}d")
    mh = exit_.get("maxHold") or {}
    if mh.get("enabled"):
        out.append(f"held ≥ {hrs(mh.get('value'))}")
    dl = exit_.get("distToLiq") or {}
    if dl.get("enabled"):
        out.append(f"liq within {dl.get('value')}% of spot")
    return out
