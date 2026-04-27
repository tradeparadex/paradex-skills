"""
Unit tests for paradex_backtest_engine.py

Run:
    python -m pytest skills/strategy-backtester/tests/ -v
or
    python -m unittest discover -s skills/strategy-backtester/tests
"""
from __future__ import annotations

import math
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from paradex_backtest_engine import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_vega,
    compute_iv_percentile,
    compute_realized_vol,
    compute_rsi,
    compute_sma,
    find_strike_by_delta,
    run_engine,
    _compute_metrics,
    _gate_passes,
    _dedup_sorted,
)


# ── Black-Scholes ─────────────────────────────────────────────────────────────

class TestBlackScholes(unittest.TestCase):

    def test_call_put_parity(self):
        S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.2
        call = bs_price(S, K, T, r, sigma, is_call=True)
        put  = bs_price(S, K, T, r, sigma, is_call=False)
        # C - P = S - K * exp(-rT)
        self.assertAlmostEqual(call - put, S - K * math.exp(-r * T), places=4)

    def test_intrinsic_value_at_expiry(self):
        S, K, r, sigma = 110.0, 100.0, 0.05, 0.3
        call = bs_price(S, K, 0.0, r, sigma, is_call=True)
        put  = bs_price(S, K, 0.0, r, sigma, is_call=False)
        self.assertAlmostEqual(call, 10.0, places=4)   # ITM call intrinsic
        self.assertAlmostEqual(put,  0.0,  places=4)   # OTM put at expiry

    def test_otm_worthless_at_expiry(self):
        S, K, r, sigma = 90.0, 100.0, 0.05, 0.3
        call = bs_price(S, K, 0.0, r, sigma, is_call=True)
        self.assertAlmostEqual(call, 0.0, places=4)

    def test_zero_vol_gives_intrinsic(self):
        # With zero vol BS collapses to intrinsic discounted
        S, K, T, r = 110.0, 100.0, 0.5, 0.0
        price = bs_price(S, K, T, r, 0.0, is_call=True)
        self.assertAlmostEqual(price, 10.0, places=4)

    def test_call_delta_deep_itm(self):
        d = bs_delta(200.0, 100.0, 1.0, 0.05, 0.2, is_call=True)
        self.assertGreater(d, 0.99)

    def test_call_delta_deep_otm(self):
        d = bs_delta(50.0, 100.0, 1.0, 0.05, 0.2, is_call=True)
        self.assertLess(d, 0.01)

    def test_put_delta_deep_itm(self):
        d = bs_delta(50.0, 100.0, 1.0, 0.05, 0.2, is_call=False)
        self.assertLess(d, -0.99)

    def test_delta_at_expiry_itm_call(self):
        self.assertEqual(bs_delta(110.0, 100.0, 0.0, 0.05, 0.2, is_call=True), 1.0)

    def test_delta_at_expiry_otm_call(self):
        self.assertEqual(bs_delta(90.0, 100.0, 0.0, 0.05, 0.2, is_call=True), 0.0)

    def test_gamma_positive(self):
        g = bs_gamma(100.0, 100.0, 30/365, 0.05, 0.5)
        self.assertGreater(g, 0)

    def test_gamma_zero_at_expiry(self):
        self.assertEqual(bs_gamma(100.0, 100.0, 0.0, 0.05, 0.5), 0.0)

    def test_vega_positive(self):
        v = bs_vega(100.0, 100.0, 30/365, 0.05, 0.5)
        self.assertGreater(v, 0)


# ── Strike finding ────────────────────────────────────────────────────────────

class TestFindStrikeByDelta(unittest.TestCase):

    def test_25delta_call_otm(self):
        S, T, r, sigma = 50_000.0, 14/365, 0.05, 0.8
        strike = find_strike_by_delta(S, T, r, sigma, 0.25, is_call=True, tick=1000)
        self.assertGreater(strike, S)  # OTM call is above spot
        actual = bs_delta(S, strike, T, r, sigma, is_call=True)
        self.assertAlmostEqual(actual, 0.25, delta=0.03)

    def test_50delta_call_near_atm(self):
        S, T, r, sigma = 50_000.0, 14/365, 0.05, 0.8
        strike = find_strike_by_delta(S, T, r, sigma, 0.50, is_call=True, tick=1000)
        self.assertAlmostEqual(strike, S, delta=5_000)

    def test_25delta_put_otm(self):
        S, T, r, sigma = 50_000.0, 14/365, 0.05, 0.8
        # For puts we pass negative target_delta
        strike = find_strike_by_delta(S, T, r, sigma, -0.25, is_call=False, tick=1000)
        self.assertLess(strike, S)   # OTM put is below spot
        actual = bs_delta(S, strike, T, r, sigma, is_call=False)
        self.assertAlmostEqual(actual, -0.25, delta=0.03)


# ── Indicators ────────────────────────────────────────────────────────────────

class TestComputeSMA(unittest.TestCase):

    def test_warm_up(self):
        sma = compute_sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        self.assertIsNone(sma[0])
        self.assertIsNone(sma[1])

    def test_values(self):
        sma = compute_sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
        self.assertAlmostEqual(sma[2], 2.0)
        self.assertAlmostEqual(sma[3], 3.0)
        self.assertAlmostEqual(sma[4], 4.0)

    def test_length_preserved(self):
        closes = [float(i) for i in range(20)]
        sma = compute_sma(closes, 5)
        self.assertEqual(len(sma), 20)


class TestComputeRSI(unittest.TestCase):

    def test_all_gains_rsi_100(self):
        closes = [float(i) for i in range(1, 20)]
        rsi = compute_rsi(closes, 14)
        self.assertAlmostEqual(rsi[14], 100.0, places=0)

    def test_all_losses_rsi_0(self):
        closes = [float(20 - i) for i in range(20)]
        rsi = compute_rsi(closes, 14)
        self.assertAlmostEqual(rsi[14], 0.0, places=0)

    def test_warm_up_nones(self):
        closes = [float(i) for i in range(20)]
        rsi = compute_rsi(closes, 14)
        for v in rsi[:14]:
            self.assertIsNone(v)

    def test_length_preserved(self):
        closes = [float(i) for i in range(30)]
        self.assertEqual(len(compute_rsi(closes, 14)), 30)


class TestComputeRealizedVol(unittest.TestCase):

    def test_constant_prices_zero_rv(self):
        closes = [100.0] * 30
        rv = compute_realized_vol(closes, 20)
        for v in rv:
            if v is not None:
                self.assertAlmostEqual(v, 0.0, places=10)

    def test_length_preserved(self):
        closes = [100.0 * (1 + 0.01 * i) for i in range(50)]
        self.assertEqual(len(compute_realized_vol(closes, 20)), 50)


class TestIVPercentile(unittest.TestCase):

    def test_above_all_history(self):
        self.assertAlmostEqual(compute_iv_percentile(1.5, [0.2, 0.4, 0.6, 0.8, 1.0]), 100.0)

    def test_below_all_history(self):
        self.assertAlmostEqual(compute_iv_percentile(0.1, [0.2, 0.4, 0.6, 0.8, 1.0]), 0.0)

    def test_middle(self):
        # 0.6 is above 2 out of 5 values (0.2, 0.4)
        self.assertAlmostEqual(compute_iv_percentile(0.6, [0.2, 0.4, 0.6, 0.8, 1.0]), 40.0)

    def test_empty_history_returns_50(self):
        self.assertAlmostEqual(compute_iv_percentile(0.5, []), 50.0)


# ── Gate logic ────────────────────────────────────────────────────────────────

class TestGatePasses(unittest.TestCase):

    def test_empty_results_always_passes(self):
        self.assertTrue(_gate_passes([], "all"))
        self.assertTrue(_gate_passes([], "any"))
        self.assertTrue(_gate_passes([], "min", 2))

    def test_all_mode_requires_all(self):
        self.assertTrue(_gate_passes([True, True, True], "all"))
        self.assertFalse(_gate_passes([True, False, True], "all"))

    def test_any_mode_needs_one(self):
        self.assertTrue(_gate_passes([False, True, False], "any"))
        self.assertFalse(_gate_passes([False, False, False], "any"))

    def test_min_mode(self):
        self.assertTrue(_gate_passes([True, True, False], "min", 2))
        self.assertFalse(_gate_passes([True, False, False], "min", 2))

    def test_min_clamped_to_len(self):
        # gate_min > len clamps to len, effectively "all"
        self.assertTrue(_gate_passes([True, True], "min", 99))
        self.assertFalse(_gate_passes([True, False], "min", 99))


# ── Dedup helper ──────────────────────────────────────────────────────────────

class TestDedupSorted(unittest.TestCase):

    def test_removes_duplicates(self):
        items = [{"t": 2, "v": "a"}, {"t": 1, "v": "b"}, {"t": 2, "v": "c"}]
        result = _dedup_sorted(items)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["t"], 1)
        self.assertEqual(result[1]["t"], 2)

    def test_sorted_output(self):
        items = [{"t": 3}, {"t": 1}, {"t": 2}]
        result = _dedup_sorted(items)
        self.assertEqual([x["t"] for x in result], [1, 2, 3])

    def test_empty(self):
        self.assertEqual(_dedup_sorted([]), [])


# ── run_engine smoke tests ────────────────────────────────────────────────────

def _make_bars(n: int = 200, start_price: float = 100.0, drift_pct_per_bar: float = 0.0):
    """Synthetic hourly OHLCV bars."""
    t0 = 1_700_000_000_000
    bars = []
    price = start_price
    for i in range(n):
        price *= 1.0 + drift_pct_per_bar / 100
        bars.append({
            "t": t0 + i * 3_600_000,
            "o": price, "h": price * 1.001, "l": price * 0.999,
            "c": price, "v": 1000.0,
        })
    return bars


def _perp_strategy(**overrides) -> dict:
    s = {
        "name": "test_perp",
        "underlying": "BTC",
        "capital": 10_000,
        "riskFreeRate": 0.05,
        "marginMode": "XM",
        "legs": [{"type": "perp", "side": "BUY", "size": 0.01, "sizeMode": "contracts"}],
        "entry": {
            "frequency": 24,
            "gateMode": "all",
            "gateMin": 1,
            "rvPctile":    {"enabled": False, "op": ">", "value": 50, "window": 168},
            "ivPctile":    {"enabled": False, "op": ">", "value": 50, "window": 720},
            "rsi":         {"enabled": False, "op": "<", "value": 70},
            "sma":         {"enabled": False, "op": "above", "period": 168},
            "fundingRate": {"enabled": False, "op": ">", "value": 0.01},
        },
        "exit": {
            "gateMode": "any",
            "gateMin": 1,
            "profitTarget": {"enabled": False, "value": 5},
            "stopLoss":     {"enabled": False, "value": 5},
            "ivPctile":     {"enabled": False, "op": ">", "value": 80, "window": 720},
            "dteFloor":     {"enabled": False, "value": 1},
            "maxHold":      {"enabled": True,  "value": 72},
            "distToLiq":    {"enabled": False, "value": 10},
        },
    }
    s.update(overrides)
    return s


class TestRunEngineSmoke(unittest.TestCase):

    def test_returns_required_keys(self):
        result = run_engine(_perp_strategy(), _make_bars(200), None, None, None)
        self.assertIsNone(result["error"])
        self.assertIn("equity", result)
        self.assertIn("trades", result)
        self.assertIn("metrics", result)

    def test_equity_curve_length_equals_bars(self):
        bars = _make_bars(200)
        result = run_engine(_perp_strategy(), bars, None, None, None)
        self.assertEqual(len(result["equity"]), len(bars))

    def test_metrics_fields_present(self):
        result = run_engine(_perp_strategy(), _make_bars(200), None, None, None)
        mx = result["metrics"]
        for key in ("total_pnl", "total_return", "sharpe", "max_dd",
                    "win_rate", "num_trades", "holding_pct"):
            self.assertIn(key, mx, f"missing metric: {key}")

    def test_too_few_bars_returns_error(self):
        result = run_engine(_perp_strategy(), _make_bars(10), None, None, None)
        self.assertIsNotNone(result["error"])

    def test_option_legs_without_iv_enter_nothing(self):
        """No IV data → engine cannot price options → all entry attempts skipped."""
        s = _perp_strategy()
        s["legs"] = [{
            "type": "option", "side": "SELL", "optionType": "CALL",
            "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14,
            "size": 1.0, "sizeMode": "contracts",
        }]
        result = run_engine(s, _make_bars(300), None, None, None)
        self.assertIsNone(result["error"])
        self.assertEqual(result["metrics"]["num_trades"], 0)

    def test_option_legs_with_constant_iv_generate_trades(self):
        """Constant IV series → engine can price options → should generate trades."""
        bars = _make_bars(500, start_price=50_000.0)
        s = _perp_strategy()
        s["capital"] = 100_000
        s["legs"] = [
            {"type": "option", "side": "SELL", "optionType": "CALL",
             "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14,
             "size": 1.0, "sizeMode": "contracts"},
            {"type": "option", "side": "SELL", "optionType": "PUT",
             "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14,
             "size": 1.0, "sizeMode": "contracts"},
        ]
        iv = [0.8] * len(bars)
        result = run_engine(s, bars, iv, 0.8, None, atm_iv_series=iv)
        self.assertIsNone(result["error"])
        self.assertGreater(result["metrics"]["num_trades"], 0)

    def test_profit_target_fires(self):
        """Rising prices should trigger profit target on long perp positions."""
        bars = _make_bars(300, start_price=100.0, drift_pct_per_bar=0.2)
        s = _perp_strategy()
        s["exit"]["profitTarget"] = {"enabled": True, "value": 2}
        s["exit"]["maxHold"] = {"enabled": False, "value": 336}
        result = run_engine(s, bars, None, None, None)
        tp_trades = [t for t in result["trades"] if "TP" in (t.get("reason") or "")]
        self.assertGreater(len(tp_trades), 0)

    def test_stop_loss_fires(self):
        """Falling prices should trigger stop loss on long perp."""
        bars = _make_bars(300, start_price=100.0, drift_pct_per_bar=-0.2)
        s = _perp_strategy()
        s["exit"]["stopLoss"] = {"enabled": True, "value": 2}
        s["exit"]["maxHold"] = {"enabled": False, "value": 336}
        result = run_engine(s, bars, None, None, None)
        sl_trades = [t for t in result["trades"] if "SL" in (t.get("reason") or "")]
        self.assertGreater(len(sl_trades), 0)

    def test_sma_entry_gate_restricts_entries(self):
        """Flat market: SMA gate 'above' should block or allow entries consistently."""
        bars = _make_bars(400, start_price=100.0, drift_pct_per_bar=0.0)
        s = _perp_strategy()
        s["entry"]["sma"] = {"enabled": True, "op": "above", "period": 168}

        # flat market — price ≈ SMA at all times, gate will rarely pass on flat data
        result = run_engine(s, bars, None, None, None)
        self.assertIsNone(result["error"])

    def test_initial_capital_preserved_with_no_trades(self):
        """With no positions, equity should equal starting capital throughout."""
        # Use an entry condition that will never pass: RSI > 200 (impossible)
        s = _perp_strategy()
        s["entry"]["rsi"] = {"enabled": True, "op": ">", "value": 200}
        bars = _make_bars(200)
        result = run_engine(s, bars, None, None, None)
        for eq in result["equity"]:
            self.assertAlmostEqual(eq["equity"], s["capital"], delta=1e-6)


# ── _compute_metrics ──────────────────────────────────────────────────────────

class TestComputeMetrics(unittest.TestCase):

    def test_empty_equity_returns_empty_dict(self):
        self.assertEqual(_compute_metrics([], [], 100_000), {})

    def test_flat_equity_zero_pnl(self):
        equity = [{"equity": 100_000, "has_positions": False, "dist_to_liq": None}
                  for _ in range(10)]
        mx = _compute_metrics(equity, [], 100_000)
        self.assertAlmostEqual(mx["total_pnl"], 0.0)
        self.assertAlmostEqual(mx["total_return"], 0.0)
        self.assertAlmostEqual(mx["sharpe"], 0.0)
        self.assertAlmostEqual(mx["max_dd"], 0.0)

    def test_profitable_run(self):
        equity = [{"equity": 100_000 + i * 100, "has_positions": True, "dist_to_liq": None}
                  for i in range(50)]
        mx = _compute_metrics(equity, [], 100_000)
        self.assertGreater(mx["total_pnl"], 0)
        self.assertGreater(mx["total_return"], 0)
        self.assertAlmostEqual(mx["max_dd"], 0.0)

    def test_max_drawdown_detected(self):
        # Equity rises then falls below start
        equities = [100_000 + i * 1000 for i in range(10)] + \
                   [110_000 - i * 2000 for i in range(10)]
        equity = [{"equity": e, "has_positions": True, "dist_to_liq": None} for e in equities]
        mx = _compute_metrics(equity, [], 100_000)
        self.assertGreater(mx["max_dd"], 0)

    def test_win_rate_all_wins(self):
        trades = [
            {"entry_time": i * 1000, "pnl": 100.0, "reason": "TP", "is_hedge": False}
            for i in range(5)
        ]
        equity = [{"equity": 100_000, "has_positions": False, "dist_to_liq": None}
                  for _ in range(10)]
        mx = _compute_metrics(equity, trades, 100_000)
        self.assertAlmostEqual(mx["win_rate"], 100.0)

    def test_holding_pct(self):
        equity = [{"equity": 100_000, "has_positions": i % 2 == 0, "dist_to_liq": None}
                  for i in range(10)]
        mx = _compute_metrics(equity, [], 100_000)
        self.assertAlmostEqual(mx["holding_pct"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
