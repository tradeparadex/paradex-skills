import math

import pytest

from strategy_viz.pricing import (
    ASSUMED_IV, R, SPOT,
    bs_greeks, bs_price, leg_entry_premium, leg_greeks_at_entry,
    leg_payoff_at, leg_strike, norm_cdf, norm_pdf, payoff_curve,
    per_leg_curves, portfolio_greeks, strike_from_delta,
)


def test_norm_cdf_known_points():
    assert norm_cdf(0) == pytest.approx(0.5, abs=1e-9)
    # standard tail values
    assert norm_cdf(1) == pytest.approx(0.8413447, abs=1e-5)
    assert norm_cdf(-1) == pytest.approx(0.1586553, abs=1e-5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)


def test_norm_pdf_at_zero():
    assert norm_pdf(0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), abs=1e-9)


def test_bs_price_at_expiry_is_intrinsic():
    assert bs_price(110, 100, 0, 0.5, "CALL") == 10
    assert bs_price(90, 100, 0, 0.5, "CALL") == 0
    assert bs_price(90, 100, 0, 0.5, "PUT") == 10
    assert bs_price(110, 100, 0, 0.5, "PUT") == 0


def test_bs_put_call_parity():
    # C - P = S - K * e^{-rT}
    S, K, T, sig = 100, 100, 0.25, 0.4
    c = bs_price(S, K, T, sig, "CALL")
    p = bs_price(S, K, T, sig, "PUT")
    assert c - p == pytest.approx(S - K * math.exp(-R * T), abs=1e-6)


def test_atm_call_delta_near_half():
    # 30D ATM call delta should be ~0.5 (slightly higher due to drift term)
    g = bs_greeks(100, 100, 30 / 365, 0.6, "CALL")
    assert 0.50 < g["delta"] < 0.58
    assert g["gamma"] > 0
    assert g["vega"] > 0


def test_atm_put_delta_negative():
    g = bs_greeks(100, 100, 30 / 365, 0.6, "PUT")
    assert -0.5 < g["delta"] < -0.42


def test_strike_from_delta_round_trip_call():
    # find a 25Δ call strike, then check its delta is ≈ 25Δ
    T = 14 / 365
    K = strike_from_delta(0.25, T, ASSUMED_IV, "CALL")
    assert K > SPOT  # 25Δ call is OTM
    g = bs_greeks(SPOT, K, T, ASSUMED_IV, "CALL")
    assert g["delta"] == pytest.approx(0.25, abs=0.005)


def test_strike_from_delta_round_trip_put():
    T = 14 / 365
    K = strike_from_delta(0.25, T, ASSUMED_IV, "PUT")
    assert K < SPOT
    g = bs_greeks(SPOT, K, T, ASSUMED_IV, "PUT")
    assert abs(g["delta"]) == pytest.approx(0.25, abs=0.005)


def test_leg_strike_atm_and_otm_pct():
    assert leg_strike({"type": "option", "optionType": "CALL", "strikeMode": "atm",
                       "strikeParam": 0, "dteTarget": 14, "side": "SELL", "size": 1}) == SPOT
    assert leg_strike({"type": "option", "optionType": "CALL", "strikeMode": "otm_pct",
                       "strikeParam": 0.10, "dteTarget": 14, "side": "SELL", "size": 1}) == SPOT * 1.10
    assert leg_strike({"type": "option", "optionType": "PUT", "strikeMode": "otm_pct",
                       "strikeParam": 0.10, "dteTarget": 14, "side": "SELL", "size": 1}) == SPOT * 0.90


def test_perp_leg_payoff_is_linear():
    leg = {"type": "perp", "side": "BUY", "size": 2.0}
    K = leg_strike(leg)
    prem = leg_entry_premium(leg, K)
    assert leg_payoff_at(leg, 110, K, prem) == pytest.approx(2 * (110 - SPOT))
    assert leg_payoff_at(leg, 90, K, prem) == pytest.approx(2 * (90 - SPOT))


def test_short_put_payoff_at_atm_is_premium_received():
    leg = {"type": "option", "side": "SELL", "optionType": "PUT",
           "strikeMode": "atm", "strikeParam": 0, "dteTarget": 14, "size": 1}
    K = leg_strike(leg)
    prem = leg_entry_premium(leg, K)
    pl_at_spot = leg_payoff_at(leg, SPOT, K, prem)
    # at expiry, S=K so intrinsic=0, P&L = +premium
    assert pl_at_spot == pytest.approx(prem, abs=1e-6)


def test_payoff_curve_shapes():
    legs = [
        {"type": "option", "side": "SELL", "optionType": "PUT", "strikeMode": "delta",
         "strikeParam": 0.20, "dteTarget": 7, "size": 1.0},
        {"type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "delta",
         "strikeParam": 0.20, "dteTarget": 7, "size": 1.0},
    ]
    spots, net = payoff_curve(legs, n_points=40)
    assert len(spots) == 40
    assert len(net) == 40
    # short strangle: max profit near ATM, falls off on both wings
    mid = net[len(net) // 2]
    assert mid > net[0] and mid > net[-1]


def test_payoff_curve_empty_legs():
    spots, net = payoff_curve([], n_points=20)
    assert spots == [] and net == []


def test_per_leg_curves_decomposes_correctly():
    legs = [
        {"type": "option", "side": "BUY", "optionType": "CALL", "strikeMode": "atm",
         "strikeParam": 0, "dteTarget": 7, "size": 1.0},
        {"type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "otm_pct",
         "strikeParam": 0.10, "dteTarget": 7, "size": 1.0},
    ]
    out = per_leg_curves(legs, n_points=10)
    assert len(out["per_leg"]) == 2
    assert len(out["spots"]) == 10
    # net == sum of per-leg
    for i, _ in enumerate(out["spots"]):
        s = sum(leg["pnl"][i] for leg in out["per_leg"])
        assert out["net"][i] == pytest.approx(s, abs=1e-9)


def test_portfolio_greeks_sum_signed_legs():
    legs = [
        {"type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "atm",
         "strikeParam": 0, "dteTarget": 7, "size": 1.0},
        {"type": "option", "side": "BUY", "optionType": "CALL", "strikeMode": "atm",
         "strikeParam": 0, "dteTarget": 7, "size": 1.0},
    ]
    pg = portfolio_greeks(legs)
    # buying + selling identical legs nets to ~zero on every Greek
    assert pg["delta"] == pytest.approx(0, abs=1e-9)
    assert pg["gamma"] == pytest.approx(0, abs=1e-9)
    assert pg["vega"] == pytest.approx(0, abs=1e-9)


def test_perp_greeks_only_delta():
    leg = {"type": "perp", "side": "BUY", "size": 0.5}
    g = leg_greeks_at_entry(leg)
    assert g["delta"] == 0.5
    assert g["gamma"] == 0
    assert g["vega"] == 0
    assert g["theta"] == 0


def test_sell_leg_greeks_negated():
    bought = leg_greeks_at_entry({"type": "option", "side": "BUY", "optionType": "CALL",
                                  "strikeMode": "atm", "strikeParam": 0,
                                  "dteTarget": 7, "size": 1.0})
    sold = leg_greeks_at_entry({"type": "option", "side": "SELL", "optionType": "CALL",
                                "strikeMode": "atm", "strikeParam": 0,
                                "dteTarget": 7, "size": 1.0})
    for k in ("delta", "gamma", "vega", "theta"):
        assert sold[k] == pytest.approx(-bought[k], abs=1e-9)
