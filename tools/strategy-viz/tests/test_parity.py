"""JS ↔ Python parity test for the pricing math, Greeks, and label helpers.

The two implementations live in `strategy_viz/pricing.py` + `specs.py` and in
`strategy_viz/js/index.mjs`. They've drifted before (different binary-search
counts, slightly different strike-from-delta bounds). This test makes any
divergence visible: it shells out to `node` with a JS harness, computes the
same outputs in Python, and asserts agreement.

Skipped if `node` is not on PATH.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from strategy_viz.pricing import (
    leg_greeks_at_entry, leg_entry_premium, leg_payoff_at, leg_strike,
    portfolio_greeks,
)
from strategy_viz.specs import entry_lines, exit_lines, thesis

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "parity_harness.mjs"
SAMPLES_DIR = ROOT / "samples"
BACKTESTER_SAMPLES = [
    "iron_condor_btc", "short_strangle_eth",
    "csp_btc_oversold", "covered_perp_call_sol",
    "bull_put_spread_btc", "long_call_btc", "long_straddle_eth",
]
PROBE_SPOTS = [80, 95, 100, 105, 120]
ABS_TOL = 1e-3  # ≥ 3-decimal agreement


def _python_outputs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in BACKTESTER_SAMPLES:
        strat = json.loads((SAMPLES_DIR / f"{name}.json").read_text())
        legs = strat.get("legs", [])
        per_leg = []
        for leg in legs:
            g = leg_greeks_at_entry(leg)
            per_leg.append({"delta": g["delta"], "gamma": g["gamma"],
                            "vega": g["vega"], "theta": g["theta"]})
        payoff_at_spots: dict[str, float] = {}
        for s in PROBE_SPOTS:
            total = 0.0
            for leg in legs:
                K = leg_strike(leg)
                prem = leg_entry_premium(leg, K)
                total += leg_payoff_at(leg, s, K, prem)
            payoff_at_spots[str(s)] = total
        out[name] = {
            "thesis": thesis(strat),
            "entry_lines": entry_lines(strat.get("entry", {})),
            "exit_lines": exit_lines(strat.get("exit", {})),
            "per_leg_greeks": per_leg,
            "portfolio_greeks": portfolio_greeks(legs),
            "payoff_at_spots": payoff_at_spots,
        }
    return out


def _js_outputs() -> dict[str, dict]:
    payload = {
        "samples": [
            {"name": n, "strat": json.loads((SAMPLES_DIR / f"{n}.json").read_text())}
            for n in BACKTESTER_SAMPLES
        ],
        "probes": {"spots": PROBE_SPOTS},
    }
    proc = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(payload), capture_output=True, text=True,
        timeout=30, check=True,
    )
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def both_sides():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    js = _js_outputs()
    py = _python_outputs()
    return py, js


@pytest.mark.parametrize("name", BACKTESTER_SAMPLES)
def test_thesis_matches(both_sides, name):
    py, js = both_sides
    assert py[name]["thesis"] == js[name]["thesis"]


@pytest.mark.parametrize("name", BACKTESTER_SAMPLES)
def test_entry_exit_lines_match(both_sides, name):
    py, js = both_sides
    assert py[name]["entry_lines"] == js[name]["entry_lines"]
    assert py[name]["exit_lines"] == js[name]["exit_lines"]


@pytest.mark.parametrize("name", BACKTESTER_SAMPLES)
def test_per_leg_greeks_match(both_sides, name):
    py, js = both_sides
    py_legs, js_legs = py[name]["per_leg_greeks"], js[name]["per_leg_greeks"]
    assert len(py_legs) == len(js_legs)
    for i, (p, j) in enumerate(zip(py_legs, js_legs)):
        for k in ("delta", "gamma", "vega", "theta"):
            assert abs(p[k] - j[k]) < ABS_TOL, \
                f"{name} leg {i} {k}: py={p[k]:.6f} js={j[k]:.6f}"


@pytest.mark.parametrize("name", BACKTESTER_SAMPLES)
def test_portfolio_greeks_match(both_sides, name):
    py, js = both_sides
    for k in ("delta", "gamma", "vega", "theta"):
        assert abs(py[name]["portfolio_greeks"][k] - js[name]["portfolio_greeks"][k]) < ABS_TOL, \
            f"{name} portfolio {k}"


@pytest.mark.parametrize("name", BACKTESTER_SAMPLES)
def test_payoff_at_probes_match(both_sides, name):
    py, js = both_sides
    for s in PROBE_SPOTS:
        # node JSON serialises numeric keys as strings; both sides do the same.
        py_v = py[name]["payoff_at_spots"][str(s)]
        js_v = js[name]["payoff_at_spots"][str(s)]
        assert abs(py_v - js_v) < ABS_TOL, \
            f"{name} spot={s}: py={py_v:.6f} js={js_v:.6f}"
