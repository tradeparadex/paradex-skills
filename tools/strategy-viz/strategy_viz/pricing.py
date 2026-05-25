"""Pure-Python pricing helpers used by every renderer.

Kept dependency-free (math only, no numpy/matplotlib) so that webchat
composition and other text-only tools don't pull plotting libraries.
"""
from __future__ import annotations

import math
from typing import Any


SPOT = 100.0
ASSUMED_IV = 0.60
R = 0.05


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, sigma: float, opt: str) -> float:
    if T <= 0:
        return max(S - K, 0) if opt == "CALL" else max(K - S, 0)
    d1 = (math.log(S / K) + (R + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "CALL":
        return S * norm_cdf(d1) - K * math.exp(-R * T) * norm_cdf(d2)
    return K * math.exp(-R * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def strike_from_delta(target_delta: float, T: float, sigma: float, opt: str) -> float:
    lo, hi = SPOT * 0.2, SPOT * 5.0
    mid = SPOT
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if T <= 0:
            d = 1.0 if (opt == "CALL" and SPOT > mid) else 0.0
        else:
            d1 = (math.log(SPOT / mid) + (R + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
            d = norm_cdf(d1) if opt == "CALL" else norm_cdf(-d1)
        if d > target_delta:
            if opt == "CALL":
                lo = mid
            else:
                hi = mid
        else:
            if opt == "CALL":
                hi = mid
            else:
                lo = mid
    return mid


def leg_strike(leg: dict[str, Any]) -> float:
    if leg["type"] == "perp":
        return SPOT
    mode = leg.get("strikeMode", "delta")
    p = leg.get("strikeParam", 0)
    dte = leg.get("dteTarget", 14)
    T = max(dte, 1) / 365.0
    opt = leg.get("optionType", "CALL")
    if mode == "atm":
        return SPOT
    if mode == "otm_pct":
        return SPOT * (1 + p) if opt == "CALL" else SPOT * (1 - p)
    return strike_from_delta(p, T, ASSUMED_IV, opt)


def leg_entry_premium(leg: dict[str, Any], K: float) -> float:
    if leg["type"] == "perp":
        return SPOT
    dte = leg.get("dteTarget", 14)
    T = max(dte, 1) / 365.0
    return bs_price(SPOT, K, T, ASSUMED_IV, leg.get("optionType", "CALL"))


def leg_payoff_at(leg: dict[str, Any], s: float, K: float, prem: float) -> float:
    """P&L of a single leg at one underlying price, in spot-normalized units."""
    sign = 1.0 if leg["side"] == "BUY" else -1.0
    size = leg.get("size", 1.0)
    if leg["type"] == "perp":
        return sign * size * (s - SPOT)
    opt = leg.get("optionType", "CALL")
    intrinsic = max(s - K, 0) if opt == "CALL" else max(K - s, 0)
    return sign * size * (intrinsic - prem)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(S: float, K: float, T: float, sigma: float, opt: str) -> dict[str, float]:
    """Returns delta, gamma, vega (per 1% IV move), theta (per day) at (S,K,T,σ).
    Pure-Python; same parameterisation as bs_price."""
    if T <= 0 or sigma <= 0:
        # at expiry, delta is a step; gamma/vega/theta are 0
        if opt == "CALL":
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    phi = norm_pdf(d1)
    if opt == "CALL":
        delta = norm_cdf(d1)
        theta_annual = -(S * phi * sigma) / (2 * sqrtT) - R * K * math.exp(-R * T) * norm_cdf(d2)
    else:
        delta = norm_cdf(d1) - 1.0
        theta_annual = -(S * phi * sigma) / (2 * sqrtT) + R * K * math.exp(-R * T) * norm_cdf(-d2)
    gamma = phi / (S * sigma * sqrtT)
    vega_per_1pct = S * phi * sqrtT / 100.0
    return {"delta": delta, "gamma": gamma, "vega": vega_per_1pct, "theta": theta_annual / 365.0}


def leg_greeks_at_entry(leg: dict[str, Any]) -> dict[str, float]:
    """Per-leg signed Greeks at entry, multiplied by size. Perp legs contribute
    delta = ±1, all other Greeks = 0."""
    sign = 1.0 if leg.get("side") == "BUY" else -1.0
    size = leg.get("size", 1.0)
    if leg.get("type") == "perp":
        return {"delta": sign * size, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    K = leg_strike(leg)
    dte = leg.get("dteTarget", 14)
    T = max(dte, 1) / 365.0
    g = bs_greeks(SPOT, K, T, ASSUMED_IV, leg.get("optionType", "CALL"))
    return {k: sign * size * v for k, v in g.items()}


def portfolio_greeks(legs: list[dict[str, Any]]) -> dict[str, float]:
    out = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for leg in legs:
        g = leg_greeks_at_entry(leg)
        for k in out:
            out[k] += g[k]
    return out


def per_leg_curves(legs: list[dict[str, Any]], n_points: int = 80,
                   lo: float = 0.55, hi: float = 1.45) -> dict[str, Any]:
    """Returns {spots, per_leg: [{label, strike, pnl}], net}. Pure Python."""
    if n_points < 2:
        n_points = 2
    if not legs:
        return {"spots": [], "per_leg": [], "net": []}
    step = (hi - lo) / (n_points - 1)
    spots = [SPOT * (lo + i * step) for i in range(n_points)]
    per_leg: list[dict[str, Any]] = []
    net = [0.0] * n_points
    for leg in legs:
        K = leg_strike(leg)
        prem = leg_entry_premium(leg, K)
        pnl = [leg_payoff_at(leg, s, K, prem) for s in spots]
        for i, v in enumerate(pnl):
            net[i] += v
        per_leg.append({
            "label": _leg_short_label(leg),
            "type": leg.get("type"),
            "side": leg.get("side"),
            "optionType": leg.get("optionType"),
            "strike": K,
            "entry_premium": prem,
            "pnl": pnl,
        })
    return {"spots": spots, "per_leg": per_leg, "net": net}


def payoff_curve(legs: list[dict[str, Any]], n_points: int = 60,
                 lo: float = 0.55, hi: float = 1.45) -> tuple[list[float], list[float]]:
    """Net payoff at expiry sampled on the spot axis."""
    c = per_leg_curves(legs, n_points=n_points, lo=lo, hi=hi)
    return c["spots"], c["net"]


def _leg_short_label(leg: dict[str, Any]) -> str:
    side = leg.get("side", "?")
    if leg.get("type") == "perp":
        return f"{side} PERP"
    sm = leg.get("strikeMode", "delta")
    p = leg.get("strikeParam", 0)
    if sm == "delta":
        strike = f"{p}Δ"
    elif sm == "otm_pct":
        strike = f"{int(p * 100)}% OTM"
    else:
        strike = "ATM"
    return f"{side} {leg.get('optionType', '?')} · {strike} · {leg.get('dteTarget', '?')}d"
