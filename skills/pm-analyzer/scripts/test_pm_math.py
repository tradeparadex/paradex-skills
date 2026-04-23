"""
test_pm_math.py — Unit tests for pm_math.py

Run with:  python3 -m pytest scripts/test_pm_math.py -v
       or: python3 scripts/test_pm_math.py  (no pytest needed)

All tests use fixed input dicts — no auth, no network calls.
"""

import math
import sys
import os
from datetime import datetime, timezone

# Allow running from the skill root or the scripts dir
sys.path.insert(0, os.path.dirname(__file__))
from pm_math import (
    norm_cdf, bs_price, parse_market, parse_expiry,
    xm_position, spot_balance_margin, compute_xm,
    compute_pm, compute, delta_hedge_size,
    SCENARIOS, N_SC, WEIGHTS,
)

# ── Fixtures ───────────────────────────────────────────────────────────────

# Market data for the two live positions (values as of 2026-04-22)
MARKET_DATA = {
    "BTC-USD-PERP": {
        "mark_price": 77888.84,
        "delta": 0.99934658,
        "mark_iv": None,
        "underlying_price": 77939.768,
        "funding_rate": -0.00017403,
    },
    "BTC-USD-8MAY26-78000-C": {
        "mark_price": 2455.661,
        "delta": 0.47715119,
        "mark_iv": 0.42746687,
        "underlying_price": 77939.768,
        "funding_rate": None,
    },
}

MARKET_SPECS = {
    "BTC-USD-PERP": {
        "asset_kind": "PERP",
        "delta1_cross_margin_params": {
            "imf_base": "0.02",
            "imf_factor": "0",
            "imf_shift": "0",
            "mmf_factor": "0.5",
        },
        "option_cross_margin_params": None,
        "order_size_increment": "0.00001",
    },
    "BTC-USD-8MAY26-78000-C": {
        "asset_kind": "OPTION",
        "option_type": "CALL",
        "strike_price": "78000",
        "delta1_cross_margin_params": None,
        "option_cross_margin_params": {
            "imf": {
                "long_itm": "0.2",
                "premium_multiplier": "1",
                "short_itm": "0.15",
                "short_otm": "0.1",
                "short_put_cap": "0.5",
            },
            "mmf": {
                "long_itm": "0.1",
                "premium_multiplier": "0.5",
                "short_itm": "0.075",
                "short_otm": "0.05",
                "short_put_cap": "0.5",
            },
        },
        "order_size_increment": "0.001",
    },
}

POSITIONS = [
    {"market": "BTC-USD-PERP",           "side": "SELL", "size": 0.0005},
    {"market": "BTC-USD-8MAY26-78000-C", "side": "BUY",  "size": 0.001},
]

BALANCES = [{"token": "USDC", "size": 10.25}]


# ── Helpers ────────────────────────────────────────────────────────────────

def assert_close(a, b, tol=0.01, label=""):
    assert abs(a - b) <= tol, f"{label}: expected ~{b:.6f}, got {a:.6f} (diff {a-b:+.6f})"

def assert_equal(a, b, label=""):
    assert a == b, f"{label}: expected {b!r}, got {a!r}"


# ── Black-Scholes ──────────────────────────────────────────────────────────

def test_norm_cdf_symmetry():
    """norm_cdf(0) == 0.5, norm_cdf(-x) == 1 - norm_cdf(x)"""
    assert_close(norm_cdf(0), 0.5, tol=1e-9, label="norm_cdf(0)")
    for x in [0.5, 1.0, 1.96, 2.5]:
        assert_close(norm_cdf(-x), 1 - norm_cdf(x), tol=1e-9, label=f"symmetry({x})")

def test_bs_price_intrinsic_at_expiry():
    """At T=0, BS = intrinsic value."""
    assert_close(bs_price(100, 90, 0, 0, 0.3, True),  10.0, tol=1e-9, label="call ITM")
    assert_close(bs_price(100, 110, 0, 0, 0.3, True),  0.0, tol=1e-9, label="call OTM")
    assert_close(bs_price(100, 110, 0, 0, 0.3, False), 10.0, tol=1e-9, label="put ITM")
    assert_close(bs_price(100, 90,  0, 0, 0.3, False),  0.0, tol=1e-9, label="put OTM")

def test_bs_price_atm_positive():
    """ATM option with time value is always positive."""
    price = bs_price(100, 100, 1.0, 0, 0.5, True)
    assert price > 0, f"ATM call should be positive, got {price}"

def test_bs_price_known_value():
    """Cross-check against a known Black-Scholes result (S=100, K=100, T=1, r=0, σ=0.2)."""
    price = bs_price(100, 100, 1.0, 0, 0.2, True)
    assert_close(price, 7.9656, tol=0.01, label="known BS call price")

def test_bs_put_call_parity():
    """Put-call parity: C - P = S - K*exp(-rT)"""
    S, K, T, r, sigma = 100, 95, 0.5, 0.05, 0.3
    call = bs_price(S, K, T, r, sigma, True)
    put  = bs_price(S, K, T, r, sigma, False)
    lhs  = call - put
    rhs  = S - K * math.exp(-r * T)
    assert_close(lhs, rhs, tol=1e-6, label="put-call parity")


# ── Market parsing ─────────────────────────────────────────────────────────

def test_parse_market_perp():
    r = parse_market("BTC-USD-PERP")
    assert r["type"] == "perp"

def test_parse_market_dated_call():
    r = parse_market("BTC-USD-8MAY26-78000-C")
    assert r["type"] == "dated_option"
    assert r["is_call"] is True
    assert r["strike"] == 78000.0
    assert r["expiry"] == datetime(2026, 5, 8, 8, tzinfo=timezone.utc)

def test_parse_market_dated_put():
    r = parse_market("BTC-USD-8MAY26-78000-P")
    assert r["type"] == "dated_option"
    assert r["is_call"] is False

def test_parse_expiry():
    assert parse_expiry("8MAY26")  == datetime(2026, 5, 8, 8, tzinfo=timezone.utc)
    assert parse_expiry("31DEC25") == datetime(2025, 12, 31, 8, tzinfo=timezone.utc)
    assert parse_expiry("invalid") is None


# ── XM margin ─────────────────────────────────────────────────────────────

def test_xm_perp_short():
    """SHORT 0.0005 BTC perp @ $77,888.84 mark, imf=2% → IMR=$0.7789, MMR=$0.3894"""
    pos = {"market": "BTC-USD-PERP", "side": "SELL", "size": 0.0005}
    r = xm_position(pos, MARKET_DATA, MARKET_SPECS)
    assert_close(r["imr"],         0.7789, tol=0.001,  label="perp short IMR")
    assert_close(r["mmr"],         0.3894, tol=0.001,  label="perp short MMR")
    assert_close(r["delta_contrib"], -0.000500, tol=1e-5, label="perp short delta")

def test_xm_long_call():
    """LONG 0.001 BTC call @ $2455.66 mark → IMR=mark×size=$2.456, MMR=$1.228"""
    pos = {"market": "BTC-USD-8MAY26-78000-C", "side": "BUY", "size": 0.001}
    r = xm_position(pos, MARKET_DATA, MARKET_SPECS)
    assert_close(r["imr"], 2.455661, tol=0.001, label="long call IMR")
    assert_close(r["mmr"], 1.227830, tol=0.001, label="long call MMR")
    assert r["delta_contrib"] > 0, "long call should have positive delta"

def test_xm_total():
    """Total XM for live positions matches exchange within $0.02 (fee provision)."""
    result = compute_xm(POSITIONS, [], MARKET_DATA, MARKET_SPECS, BALANCES)
    # Exchange reported: IMR $3.2504, MMR $1.6339
    assert_close(result["IMR"], 3.2504, tol=0.02, label="total IMR vs exchange")
    assert_close(result["MMR"], 1.6339, tol=0.02, label="total MMR vs exchange")

def test_spot_balance_margin_usdc_excluded():
    """USDC balance should not contribute to spot balance margin."""
    sbm = spot_balance_margin([{"token": "USDC", "size": 100.0}], MARKET_DATA)
    assert sbm == 0.0, "USDC should not add margin"

def test_spot_balance_margin_non_usdc():
    """Non-USDC token should be charged at mark price × size."""
    md = {"ETH-USD-PERP": {"mark_price": 2000.0}}
    sbm = spot_balance_margin([{"token": "ETH", "size": 0.5}], md)
    assert_close(sbm, 1000.0, tol=0.01, label="ETH spot margin")


# ── Delta hedge ────────────────────────────────────────────────────────────

def test_delta_hedge_positive_delta():
    """Positive portfolio delta → SELL hedge."""
    side, size = delta_hedge_size(0.01, 1.0, size_increment=0.00001)
    assert side == "SELL"
    assert_close(size, 0.01, tol=0.00001, label="sell size")

def test_delta_hedge_negative_delta():
    """Negative portfolio delta → BUY hedge."""
    side, size = delta_hedge_size(-0.01, 1.0, size_increment=0.00001)
    assert side == "BUY"
    assert_close(size, 0.01, tol=0.00001, label="buy size")

def test_delta_hedge_rounds_down():
    """Size rounds DOWN to size_increment."""
    _, size = delta_hedge_size(0.00047, 1.0, size_increment=0.00001)
    assert size == 0.00047  # 0.47 / 1.0 = exactly 47 increments

def test_delta_hedge_near_zero():
    """Delta below one increment → NONE, 0."""
    side, size = delta_hedge_size(0.000005, 1.0, size_increment=0.00001)
    assert side == "NONE"
    assert size == 0.0


# ── compute() dispatcher ───────────────────────────────────────────────────

def test_compute_cross_margin_routes_to_xm():
    r = compute(POSITIONS, [], MARKET_DATA, MARKET_SPECS,
                margin_methodology="cross_margin", balances=BALANCES)
    assert r["margin_methodology"] == "cross_margin"
    assert "IMR" in r and "MMR" in r
    assert r["IMR"] > 0

def test_compute_pm_routes_to_scenario_scan():
    r = compute(POSITIONS, [], MARKET_DATA, MARKET_SPECS,
                margin_methodology="portfolio_margin", balances=BALANCES)
    assert r["margin_methodology"] == "portfolio_margin"
    assert "worst_loss" in r
    assert "delta_min" in r

def test_compute_pm_worst_scenario_is_vol_crush():
    """
    For a LONG call + SHORT perp (small net long vega), the worst scenario
    should be a vol crush (vol_shock = -0.22) because the call loses value
    while the short perp PnL is limited.
    Scenario #9 (0% spot, -22% vol) dominates for long vol positions.
    """
    r = compute(POSITIONS, [], MARKET_DATA, MARKET_SPECS,
                margin_methodology="portfolio_margin")
    worst_sc = SCENARIOS[r["worst_idx"]]
    assert worst_sc[1] < 0, (
        f"Expected negative vol shock for long-vol position, "
        f"got scenario #{r['worst_idx']+1}: {worst_sc}"
    )

def test_compute_what_if_increases_imr():
    """Adding a 0.01 BTC long should increase IMR."""
    base = compute(POSITIONS, [], MARKET_DATA, MARKET_SPECS)
    what_if_pos = POSITIONS + [{"market": "BTC-USD-PERP", "side": "BUY", "size": 0.01}]
    with_pos = compute(what_if_pos, [], MARKET_DATA, MARKET_SPECS)
    assert with_pos["IMR"] > base["IMR"], "Adding long position must increase IMR"


# ── Runner (no pytest needed) ──────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗  {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
