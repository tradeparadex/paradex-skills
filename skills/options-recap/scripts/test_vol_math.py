#!/usr/bin/env python3
"""
Unit tests for vol_math.py — no network, no auth, no deps.

Run: python3 scripts/test_vol_math.py
These pin the formulas so the production CLI and the eval fixture generator
can't drift, and so a human can verify the math once by inspection.
"""

import math
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from vol_math import (
    compute_realized_vol,
    realized_vs_implied,
    black76_greeks,
    expiry_ms_from_instrument,
    compute_flow_greeks,
    cluster_blocks,
    HOURS_PER_YEAR,
)

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}  {detail}")


def approx(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ── Realized vol ───────────────────────────────────────────────────────────

def test_rv_flat_series_is_zero():
    rv = compute_realized_vol([100.0] * 10)
    check("flat series → 0 vol", approx(rv["annualized_vol"], 0.0, 1e-9),
          f"got {rv['annualized_vol']}")


def test_rv_too_few_points():
    rv = compute_realized_vol([100.0, 101.0])
    check("under 3 points → None", rv["annualized_vol"] is None, f"got {rv}")
    check("empty → None", compute_realized_vol([])["annualized_vol"] is None)


def test_rv_known_value():
    # Alternating +1%/-1% each hour: every log-return has equal magnitude.
    closes = [100.0]
    for i in range(1, 50):
        closes.append(closes[-1] * (1.01 if i % 2 else 1 / 1.01))
    rv = compute_realized_vol(closes)
    # Reconstruct expected: sample stdev of the log returns × √8760 × 100.
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
    expected = sd * math.sqrt(HOURS_PER_YEAR) * 100
    check("known series matches formula", approx(rv["annualized_vol"], round(expected, 1), 0.05),
          f"got {rv['annualized_vol']} vs {round(expected, 1)}")


def test_rv_annualization_factor():
    check("8760 hours/year (24/7)", HOURS_PER_YEAR == 8760)


def test_vrp_labels():
    # rv ~0 vs dvol 50 → implied very rich
    rich = realized_vs_implied([100.0] * 10, 50.0)
    check("VRP rich label", "rich" in (rich["vrp_label"] or ""), rich)
    # Build a high-realized series, low implied → cheap
    closes = [100.0]
    for i in range(1, 200):
        closes.append(closes[-1] * (1.02 if i % 2 else 1 / 1.02))
    cheap = realized_vs_implied(closes, 5.0)
    check("VRP cheap label", "cheap" in (cheap["vrp_label"] or ""), cheap)
    check("VRP sign = dvol - rv", approx(cheap["vrp"], round(5.0 - cheap["value"], 1), 0.11),
          f"vrp {cheap['vrp']} value {cheap['value']}")


# ── Expiry parsing ─────────────────────────────────────────────────────────

def test_expiry_parsing():
    from datetime import datetime, timezone
    one = expiry_ms_from_instrument("BTC-26JUN26-55000-P")
    expect = int(datetime(2026, 6, 26, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    check("26JUN26 parses to 08:00 UTC", one == expect, f"got {one} vs {expect}")
    # single-digit day
    short = expiry_ms_from_instrument("BTC-5JUN26-60000-C")
    expect2 = int(datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    check("5JUN26 (single-digit day) parses", short == expect2, f"got {short} vs {expect2}")
    check("garbage → None", expiry_ms_from_instrument("not-an-instrument") is None)


# ── Black-76 greeks ────────────────────────────────────────────────────────

def test_black76_positivity():
    g = black76_greeks(F=60000, K=60000, T_years=0.05, iv_pct=70)
    check("vega positive", g["vega"] > 0, g)
    check("dollar_gamma positive", g["dollar_gamma"] > 0, g)


def test_black76_degenerate():
    check("T=0 → zero greeks", black76_greeks(60000, 60000, 0, 70)["vega"] == 0)
    check("sigma=0 → zero greeks", black76_greeks(60000, 60000, 0.1, 0)["vega"] == 0)


def test_black76_vega_increases_with_tenor():
    near = black76_greeks(60000, 60000, 0.02, 70)["vega"]
    far = black76_greeks(60000, 60000, 0.50, 70)["vega"]
    check("longer tenor → more vega", far > near, f"near {near:.1f} far {far:.1f}")


def test_black76_atm_has_most_gamma():
    atm = black76_greeks(60000, 60000, 0.1, 70)["dollar_gamma"]
    otm = black76_greeks(60000, 80000, 0.1, 70)["dollar_gamma"]
    check("ATM gamma > far-OTM gamma", atm > otm, f"atm {atm:.0f} otm {otm:.0f}")


# ── Flow greeks / dealer positioning ───────────────────────────────────────

def _leg(inst, direction, amount, F=62000, iv=70.0, ts=1748000000000, bid="B1"):
    return {"instrument_name": inst, "index_price": F, "iv": iv,
            "timestamp": ts, "direction": direction, "amount": amount,
            "block_trade_id": bid}


def test_customer_buying_makes_dealers_short():
    # Customers buy a put → long vega/gamma → dealers short both.
    trades = [_leg("BTC-26JUN26-60000-P", "buy", 100)]
    fg = compute_flow_greeks(cluster_blocks(trades))
    check("net customer vega > 0 (bought)", fg["net_customer_vega"] > 0, fg)
    check("dealer vega < 0 (short)", fg["dealer_vega"] < 0, fg)
    check("dealer short gamma label", "short gamma" in fg["positioning_label"], fg)


def test_customer_selling_makes_dealers_long():
    trades = [_leg("BTC-26JUN26-60000-C", "sell", 100)]
    fg = compute_flow_greeks(cluster_blocks(trades))
    check("net customer vega < 0 (sold)", fg["net_customer_vega"] < 0, fg)
    check("dealer long gamma label", "long gamma" in fg["positioning_label"], fg)


def test_dealer_is_opposite_of_customer():
    trades = [_leg("BTC-26JUN26-60000-P", "buy", 100)]
    fg = compute_flow_greeks(cluster_blocks(trades))
    check("dealer vega = -customer vega",
          fg["dealer_vega"] == -fg["net_customer_vega"], fg)
    check("dealer gamma = -customer gamma",
          fg["dealer_dollar_gamma"] == -fg["net_customer_dollar_gamma"], fg)


def test_balanced_two_way():
    # Same instrument bought and sold in equal size → net ≈ 0 vs gross → balanced.
    trades = [_leg("BTC-26JUN26-60000-C", "buy", 100, bid="B1"),
              _leg("BTC-26JUN26-60000-C", "sell", 100, bid="B2")]
    fg = compute_flow_greeks(cluster_blocks(trades))
    check("offsetting flow → balanced", fg["balanced"], fg)
    check("balanced label", "two-way" in fg["positioning_label"], fg)


def test_cluster_blocks_filters_screen():
    trades = [
        _leg("BTC-26JUN26-60000-P", "buy", 100, bid="B1"),
        {"instrument_name": "BTC-26JUN26-60000-C", "direction": "buy", "amount": 1,
         "index_price": 62000, "iv": 70, "timestamp": 1748000000000},  # no block_trade_id
    ]
    clusters = cluster_blocks(trades)
    check("screen trade excluded from clusters", len(clusters) == 1, clusters)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"Running {len(tests)} test functions...")
    for t in tests:
        t()
    print(f"\n{_passed} checks passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
