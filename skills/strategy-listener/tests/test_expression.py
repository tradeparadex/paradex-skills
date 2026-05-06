#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py>=0.6.0", "httpx", "pytest", "pytest-asyncio"]
# ///
"""
test_expression.py — JSON expression DSL: validation, evaluation, parity
with the legacy `conditions` form.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from indicators import IndicatorState
from expression import (
    evaluate,
    validate,
    validate_or_raise,
    smoke_eval,
    required_window,
    ExpressionError,
)
from conditions import catalog


# ── Validation ───────────────────────────────────────────────────────────────


def test_validate_simple_comparison_ok():
    expr = {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}
    assert validate(expr) == []


def test_validate_unknown_indicator():
    expr = {"op": "<", "lhs": {"indicator": "nope"}, "rhs": {"const": 1}}
    errs = validate(expr)
    assert any("unknown indicator" in e for e in errs), errs


def test_validate_unknown_event_field():
    expr = {"op": "<", "lhs": {"event": "purple"}, "rhs": {"const": 1}}
    errs = validate(expr)
    assert any("unknown field" in e for e in errs), errs


def test_validate_unknown_operator():
    expr = {"op": "twixt", "lhs": {"const": 1}, "rhs": {"const": 2}}
    errs = validate(expr)
    assert any("unknown operator" in e for e in errs), errs


def test_validate_root_must_be_bool():
    # const at root → number, not bool
    errs = validate({"const": 30})
    assert any("expected bool" in e for e in errs), errs


def test_validate_unexpected_keys():
    expr = {"op": "<", "lhs": {"const": 1}, "rhs": {"const": 2}, "extra": "x"}
    errs = validate(expr)
    assert any("unexpected keys" in e for e in errs), errs


def test_validate_combinator_needs_nonempty_array():
    errs = validate({"all": []})
    assert any("non-empty array" in e for e in errs), errs


def test_validate_indicator_required_arg():
    # SMA requires `period`
    errs = validate({"op": ">", "lhs": {"event": "close"},
                     "rhs": {"indicator": "sma"}})
    assert any("missing required arg" in e for e in errs), errs


def test_validate_indicator_unknown_arg():
    errs = validate({"op": ">", "lhs": {"event": "close"},
                     "rhs": {"indicator": "sma", "period": 24, "speed": "fast"}})
    assert any("unknown args" in e for e in errs), errs


def test_validate_or_raise_collects_all():
    expr = {"op": "twixt", "lhs": {"indicator": "nope"},
            "rhs": {"event": "purple"}}
    with pytest.raises(ExpressionError) as ei:
        validate_or_raise(expr)
    # All three issues reported, not just the first
    assert len(ei.value.errors) >= 3


def test_validate_deep_nesting():
    expr = {
        "all": [
            {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}},
            {"any": [
                {"op": ">", "lhs": {"indicator": "fundingPct"}, "rhs": {"const": 0.5}},
                {"not": {"op": "above", "lhs": {"event": "close"},
                         "rhs": {"indicator": "sma", "period": 50}}},
            ]},
        ]
    }
    assert validate(expr) == []


# ── Evaluation ───────────────────────────────────────────────────────────────


def downtrending_state() -> IndicatorState:
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    s.seed_closes([100 - i * 0.4 for i in range(80)])
    s.update_funding(0.01)  # 1% → fundingRate=0.01, fundingPct=1.0
    return s


def test_eval_rsi_lt_30_fires():
    expr = {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}
    assert evaluate(expr, downtrending_state(), {}) is True


def test_eval_rsi_lt_30_does_not_fire_uptrend():
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    s.seed_closes([100 + i * 0.4 for i in range(80)])
    expr = {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}
    assert evaluate(expr, s, {}) is False


def test_eval_missing_data_returns_none():
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    s.seed_closes([100, 101])  # not enough for RSI
    expr = {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}
    assert evaluate(expr, s, {}) is None


def test_eval_compound_all_short_circuits():
    s = downtrending_state()
    # First fails → entire `all` is False (no need to evaluate second)
    expr = {"all": [
        {"op": ">", "lhs": {"indicator": "rsi"}, "rhs": {"const": 80}},
        {"op": ">", "lhs": {"indicator": "fundingPct"}, "rhs": {"const": 0.0}},
    ]}
    assert evaluate(expr, s, {}) is False


def test_eval_compound_any_short_circuits():
    s = downtrending_state()
    expr = {"any": [
        {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}},
        {"op": ">", "lhs": {"indicator": "fundingPct"}, "rhs": {"const": 999}},
    ]}
    assert evaluate(expr, s, {}) is True


def test_eval_not():
    s = downtrending_state()
    inner = {"op": ">", "lhs": {"indicator": "rsi"}, "rhs": {"const": 50}}
    expr = {"not": inner}
    assert evaluate(expr, s, {}) is True   # rsi !> 50, so not(...) = True


def test_eval_event_field_against_sma():
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    s.seed_closes([100.0] * 30)  # flat → sma(24)=100
    expr = {"op": "above", "lhs": {"event": "close"},
            "rhs": {"indicator": "sma", "period": 24}}
    assert evaluate(expr, s, {"close": 110.0}) is True
    assert evaluate(expr, s, {"close":  90.0}) is False


def test_eval_template_vars_recorded():
    s = downtrending_state()
    expr = {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}}
    vars: dict = {}
    evaluate(expr, s, {}, vars)
    assert "rsi" in vars and isinstance(vars["rsi"], float)


def test_eval_indicator_with_args_distinguished_in_vars():
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    s.seed_closes([100.0 + i for i in range(60)])
    expr = {"all": [
        {"op": "above", "lhs": {"event": "close"},
         "rhs": {"indicator": "sma", "period": 24}},
        {"op": "above", "lhs": {"event": "close"},
         "rhs": {"indicator": "sma", "period": 50}},
    ]}
    vars: dict = {}
    evaluate(expr, s, {"close": 200.0}, vars)
    # Two SMA periods → recorded under distinct labels
    sma_keys = [k for k in vars if k.startswith("sma")]
    assert len(sma_keys) == 2, sma_keys


# ── Parity with legacy conditions form ────────────────────────────────────────


@pytest.mark.parametrize("rsi_value, funding_pct, expected_match", [
    (20, 1.0, True),    # rsi<30 + funding>0.5 → both fire
    (50, 1.0, False),   # rsi too high
    (20, 0.1, False),   # funding too low
])
def test_parity_rsi_and_funding(rsi_value, funding_pct, expected_match):
    """Same gate expressed two ways must give the same result."""
    sys.path.insert(0, str(HERE.parent / "scripts"))
    from evaluator import EvaluatorState, evaluate as eval_evaluator

    # Build an indicator state that produces the requested RSI by hand
    s = IndicatorState("BTC-USD-PERP", max_window=200)
    if rsi_value < 50:
        s.seed_closes([100 - i * 0.4 for i in range(80)])  # rsi ≈ 0
    else:
        s.seed_closes([100 + i * 0.4 for i in range(80)])  # rsi ≈ 100
    s.update_funding(funding_pct / 100.0)

    legacy_ev = {
        "id": "L", "on": ["bar_close.BTC-USD-PERP"],
        "conditions": {
            "gateMode": "all",
            "rsi":         {"enabled": True, "op": "<", "value": 30},
            "fundingRate": {"enabled": True, "op": ">", "value": 0.5},
        },
        "webhook": {"url": "x"},
    }
    expr_ev = {
        "id": "E", "on": ["bar_close.BTC-USD-PERP"],
        "expression": {"all": [
            {"op": "<", "lhs": {"indicator": "rsi"},        "rhs": {"const": 30}},
            {"op": ">", "lhs": {"indicator": "fundingPct"}, "rhs": {"const": 0.5}},
        ]},
        "webhook": {"url": "x"},
    }
    event = {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP",
             "close": 100}
    legacy = eval_evaluator(legacy_ev, EvaluatorState("L"), event,
                            {"BTC-USD-PERP": s})
    expr = eval_evaluator(expr_ev, EvaluatorState("E"), event,
                          {"BTC-USD-PERP": s})
    assert legacy.fired == expr.fired == expected_match, \
        f"legacy={legacy.fired} expr={expr.fired} expected={expected_match}"


# ── Smoke + helpers ──────────────────────────────────────────────────────────


def test_smoke_eval_runs():
    expr = {"all": [
        {"op": "<", "lhs": {"indicator": "rsi"}, "rhs": {"const": 30}},
        {"op": ">", "lhs": {"indicator": "fundingPct"}, "rhs": {"const": 0}},
    ]}
    # Just doesn't raise
    smoke_eval(expr)


def test_required_window_picks_max():
    expr = {"all": [
        {"op": "above", "lhs": {"event": "close"},
         "rhs": {"indicator": "sma", "period": 24}},
        {"op": "above", "lhs": {"event": "close"},
         "rhs": {"indicator": "sma", "period": 50}},
        {"op": "<", "lhs": {"indicator": "rvPctile", "window": 168},
         "rhs": {"const": 80}},
    ]}
    assert required_window(expr) == 168


def test_catalog_lists_all_indicators():
    cat = catalog()
    assert "rsi" in cat["indicators"]
    assert "sma" in cat["indicators"]
    assert "fundingRate" in cat["indicators"]
    assert "close" in cat["event_fields"]
    assert "<" in cat["operators"] and "above" in cat["operators"]
    assert "all" in cat["combinators"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
