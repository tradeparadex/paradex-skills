#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py>=0.6.0", "httpx", "pytest", "pytest-asyncio"]
# ///
"""
test_strategies.py — pytest suite for common retail-trader strategies.

Covers six representative patterns plus a multi-strategy dispatch test:
  1. RSI oversold + funding (mean-reversion entry alert)
  2. Breakout above N-bar high (momentum)
  3. Funding-flip (cross-zero detection — funding-arb opportunity)
  4. Whale-fill mirror (event-driven copy of large trades)
  5. Drawdown-from-high (stop-loss / capitulation alert)
  6. SMA crossover (trend-follow alert)
  7. Multi-strategy: 5 strategies in one dispatcher

Each test wires up the dispatcher with a dry-run webhook sender, feeds
deterministic events, and asserts on fire counts and template renders.

Run:
    uv run scripts/../tests/test_strategies.py        # run directly
    uv run -m pytest skills/strategy-listener/tests   # via pytest
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make scripts/ importable
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from indicators import IndicatorState                   # noqa: E402
from evaluator import EvaluatorState, evaluate, _gate_passes  # noqa: E402
from webhook import WebhookSender, render_message       # noqa: E402
from paradex_listener import Dispatcher, BarAggregator  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sender():
    async with WebhookSender(dry_run=True) as s:
        yield s


def make_state(closes: list[float], funding: float | None = None,
               max_window: int = 200) -> IndicatorState:
    s = IndicatorState("BTC-USD-PERP", max_window=max_window)
    s.seed_closes(closes)
    if funding is not None:
        s.update_funding(funding)
    return s


# ── 1. RSI oversold + funding (mean-reversion entry) ─────────────────────────


@pytest.mark.asyncio
async def test_rsi_oversold_with_funding_fires(sender):
    """User: 'alert me when BTC RSI < 30 AND funding > 0.5%'."""
    closes = [100 - i * 0.4 for i in range(80)]  # downtrend → low RSI
    ind = make_state(closes, funding=0.01)       # 1% funding
    assert ind.rsi() is not None and ind.rsi() < 30

    strategy = {
        "name": "rsi-mean-reversion",
        "underlying": "BTC",
        "subscriptions": {"market": ["bbo.BTC-USD-PERP", "funding.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "long-signal",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "all",
                "rsi":         {"enabled": True, "op": "<", "value": 30},
                "fundingRate": {"enabled": True, "op": ">", "value": 0.5},
            },
            "webhook": {
                "url": "https://example/hooks/agent",
                "messageTemplate": "BTC oversold rsi={rsi:.1f}",
            },
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    bar = {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP",
           "open": 70, "high": 70, "low": 70, "close": 70, "volume": 1.0}
    await d.dispatch(bar, {"BTC-USD-PERP": ind})
    assert d.states[("rsi-mean-reversion", "long-signal")].fire_count == 1


@pytest.mark.asyncio
async def test_rsi_oversold_skips_when_funding_low(sender):
    """Same gate, low funding → conditions.gateMode='all' should NOT fire."""
    closes = [100 - i * 0.4 for i in range(80)]
    ind = make_state(closes, funding=0.001)  # 0.1% — below 0.5% threshold
    strategy = {
        "name": "rsi-mr",
        "underlying": "BTC",
        "subscriptions": {"market": ["bbo.BTC-USD-PERP", "funding.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "long",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "all",
                "rsi":         {"enabled": True, "op": "<", "value": 30},
                "fundingRate": {"enabled": True, "op": ">", "value": 0.5},
            },
            "webhook": {"url": "https://example/h"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    bar = {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP", "close": 70}
    await d.dispatch(bar, {"BTC-USD-PERP": ind})
    assert d.states[("rsi-mr", "long")].fire_count == 0


# ── 2. Breakout above N-bar high (uses SMA as proxy) ─────────────────────────


@pytest.mark.asyncio
async def test_breakout_sma_above(sender):
    """
    Spot above 24-bar SMA = trend up. Common momentum trigger:
    'alert me when BTC closes above its 24h average and RSI > 60'.
    """
    closes = [100.0] * 12 + [100 + i for i in range(50)]  # flat then rip
    ind = make_state(closes)
    sma = ind.sma(24)
    rsi = ind.rsi()
    assert sma is not None and rsi is not None and rsi > 60

    strategy = {
        "name": "breakout",
        "underlying": "BTC",
        "subscriptions": {"market": ["trades.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "trend-up",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "all",
                "sma": {"enabled": True, "op": "above", "period": 24},
                "rsi": {"enabled": True, "op": ">",     "value": 60},
            },
            "webhook": {"url": "https://example/h",
                        "messageTemplate": "Breakout: spot={close} sma={sma:.2f}"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    bar = {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP",
           "close": closes[-1]}
    await d.dispatch(bar, {"BTC-USD-PERP": ind})
    assert d.states[("breakout", "trend-up")].fire_count == 1


# ── 3. Funding flip (any-mode gate, retail funding-arb alert) ────────────────


@pytest.mark.asyncio
async def test_funding_flip_positive(sender):
    """User: 'tell me when BTC funding flips above 0%'. gateMode 'any'."""
    closes = [100 + (i % 5) for i in range(40)]
    ind = make_state(closes, funding=0.0002)  # +0.02% — just flipped positive

    strategy = {
        "name": "funding-flip",
        "underlying": "BTC",
        "subscriptions": {"market": ["funding.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "long-funding",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "any",
                "fundingRate": {"enabled": True, "op": ">", "value": 0.0},
            },
            "webhook": {"url": "https://example/h"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    await d.dispatch(
        {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP", "close": 100},
        {"BTC-USD-PERP": ind},
    )
    assert d.states[("funding-flip", "long-funding")].fire_count == 1


# ── 4. Whale-fill mirror (event-driven match) ────────────────────────────────


@pytest.mark.asyncio
async def test_whale_fill_mirror(sender):
    """User: 'mirror every BTC fill ≥ 1 BTC to my hook'."""
    strategy = {
        "name": "whale-mirror",
        "underlying": "BTC",
        "subscriptions": {"user": ["fills"]},
        "evaluators": [{
            "id": "mirror",
            "on": ["fills"],
            "match": {"market": "BTC-USD-PERP", "minSize": 1.0},
            "webhook": {"url": "https://example/h",
                        "messageTemplate": "{side} {size} {market} @ {price}"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)

    fires_expected = [
        ({"type": "fills", "market": "BTC-USD-PERP", "side": "BUY",
          "size": 1.5, "price": 70_000.0}, True),
        ({"type": "fills", "market": "BTC-USD-PERP", "side": "SELL",
          "size": 2.0, "price": 70_500.0}, True),
        ({"type": "fills", "market": "BTC-USD-PERP", "side": "BUY",
          "size": 0.5, "price": 70_000.0}, False),
        ({"type": "fills", "market": "ETH-USD-PERP", "side": "BUY",
          "size": 5.0, "price": 3_500.0}, False),
    ]
    for ev, _ in fires_expected:
        await d.dispatch(ev, {})
    assert d.states[("whale-mirror", "mirror")].fire_count == \
        sum(1 for _, ok in fires_expected if ok)


# ── 5. Drawdown-from-high (stop-loss alert) ──────────────────────────────────


@pytest.mark.asyncio
async def test_drawdown_alert_via_sma(sender):
    """
    Retail pattern: 'tell me if BTC drops 5% below the 24h SMA.'
    Modeled as sma below + rsi < 40 (proxy for drawdown momentum).
    """
    # Climb then crash
    closes = [100 + i * 0.3 for i in range(40)] + [100.0 - i * 0.5 for i in range(40)]
    ind = make_state(closes)

    strategy = {
        "name": "drawdown",
        "underlying": "BTC",
        "subscriptions": {"market": ["trades.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "stop-loss",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "all",
                "sma": {"enabled": True, "op": "below", "period": 24},
                "rsi": {"enabled": True, "op": "<",     "value": 40},
            },
            "webhook": {"url": "https://example/h"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    await d.dispatch(
        {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP",
         "close": closes[-1]},
        {"BTC-USD-PERP": ind},
    )
    assert d.states[("drawdown", "stop-loss")].fire_count == 1


# ── 6. SMA crossover (trend-follow exit alert) ───────────────────────────────


@pytest.mark.asyncio
async def test_sma_crossover_trend_change(sender):
    """User: 'fire when BTC closes below its 24h SMA — exit trend.'"""
    closes = [100 + i * 0.5 for i in range(40)] + [100.0] * 8 + [90.0]
    ind = make_state(closes)
    spot = closes[-1]
    sma = ind.sma(24)
    assert sma is not None and spot < sma

    strategy = {
        "name": "sma-cross",
        "underlying": "BTC",
        "subscriptions": {"market": ["trades.BTC-USD-PERP"]},
        "evaluators": [{
            "id": "exit-trend",
            "on": ["bar_close.BTC-USD-PERP"],
            "conditions": {
                "gateMode": "any",
                "sma": {"enabled": True, "op": "below", "period": 24},
            },
            "webhook": {"url": "https://example/h"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    await d.dispatch(
        {"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP", "close": spot},
        {"BTC-USD-PERP": ind},
    )
    assert d.states[("sma-cross", "exit-trend")].fire_count == 1


# ── 7. Throttle + cooldown ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_throttle_blocks_back_to_back(sender):
    """Throttle rejects a second eval within the window."""
    strategy = {
        "name": "thr",
        "underlying": "BTC",
        "subscriptions": {"user": ["fills"]},
        "evaluators": [{
            "id": "th",
            "on": ["fills"],
            "throttle": "1m",
            "match": {"side": "BUY"},
            "webhook": {"url": "https://example/h"},
        }],
    }
    d = Dispatcher([strategy], sender, dry_run=True)
    await d.dispatch({"type": "fills", "side": "BUY"}, {})
    await d.dispatch({"type": "fills", "side": "BUY"}, {})  # throttled
    assert d.states[("thr", "th")].fire_count == 1


# ── 8. Multi-strategy dispatch (5 strategies in one process) ─────────────────


@pytest.mark.asyncio
async def test_five_strategies_one_dispatcher(sender):
    """
    Realistic retail load: five common strategies sharing one dispatcher.
    Confirms each fires independently and namespaces don't collide.
    """
    closes_down = [100 - i * 0.4 for i in range(80)]
    closes_up = [100 + i * 0.4 for i in range(80)]
    ind_btc = make_state(closes_down, funding=0.01)         # rsi low, funding +
    ind_eth = make_state(closes_up,   funding=-0.005)        # rsi high, funding -
    ind_sol = make_state([100.0] * 50,  funding=0.0)         # flat

    def rsi_strat(name, market, op, value):
        return {
            "name": name, "underlying": market.split("-")[0],
            "subscriptions": {"market": [f"trades.{market}"]},
            "evaluators": [{
                "id": "g", "on": [f"bar_close.{market}"],
                "conditions": {"gateMode": "all",
                               "rsi": {"enabled": True, "op": op, "value": value}},
                "webhook": {"url": "https://example/h"},
            }],
        }

    strategies = [
        rsi_strat("btc-oversold", "BTC-USD-PERP", "<", 30),     # fires
        rsi_strat("eth-overbought", "ETH-USD-PERP", ">", 70),   # fires
        rsi_strat("sol-oversold", "SOL-USD-PERP", "<", 30),     # no fire
        {  # whale-fill, fires once
            "name": "whale", "underlying": "BTC",
            "subscriptions": {"user": ["fills"]},
            "evaluators": [{"id": "m", "on": ["fills"],
                            "match": {"minSize": 0.5},
                            "webhook": {"url": "https://example/h"}}],
        },
        {  # funding flip on ETH → fires (negative funding = short bias alert)
            "name": "eth-funding-neg", "underlying": "ETH",
            "subscriptions": {"market": ["funding.ETH-USD-PERP"]},
            "evaluators": [{"id": "shf", "on": ["bar_close.ETH-USD-PERP"],
                            "conditions": {"gateMode": "any",
                                           "fundingRate": {"enabled": True,
                                                           "op": "<", "value": 0.0}},
                            "webhook": {"url": "https://example/h"}}],
        },
    ]
    d = Dispatcher(strategies, sender, dry_run=True)
    indicators = {"BTC-USD-PERP": ind_btc, "ETH-USD-PERP": ind_eth, "SOL-USD-PERP": ind_sol}

    await d.dispatch({"type": "bar_close.BTC-USD-PERP", "market": "BTC-USD-PERP",
                      "close": 70.0}, indicators)
    await d.dispatch({"type": "bar_close.ETH-USD-PERP", "market": "ETH-USD-PERP",
                      "close": 130.0}, indicators)
    await d.dispatch({"type": "bar_close.SOL-USD-PERP", "market": "SOL-USD-PERP",
                      "close": 100.0}, indicators)
    await d.dispatch({"type": "fills", "market": "BTC-USD-PERP", "side": "BUY",
                      "size": 1.5, "price": 70000.0}, indicators)

    assert d.states[("btc-oversold", "g")].fire_count == 1
    assert d.states[("eth-overbought", "g")].fire_count == 1
    assert d.states[("sol-oversold", "g")].fire_count == 0
    assert d.states[("whale", "m")].fire_count == 1
    assert d.states[("eth-funding-neg", "shf")].fire_count == 1


# ── 9. Bar aggregator (verifies bar_close synthesis) ─────────────────────────


def test_bar_aggregator_emits_close_on_boundary():
    agg = BarAggregator(bar_size_min=1)
    base = 1_700_000_000_000
    # First tick — initialises bucket, no close emitted
    assert agg.update("BTC-USD-PERP", 100.0, ts_ms=base) is None
    # Same minute — updates high/low/close, no emit
    assert agg.update("BTC-USD-PERP", 105.0, ts_ms=base + 30_000) is None
    # Next minute → emits the closed previous bar
    closed = agg.update("BTC-USD-PERP", 110.0, ts_ms=base + 60_001)
    assert closed is not None
    assert closed["type"] == "bar_close.BTC-USD-PERP"
    assert closed["open"] == 100.0
    assert closed["high"] == 105.0
    assert closed["close"] == 105.0


# ── Gate primitive directly ──────────────────────────────────────────────────


def test_gate_passes_modes():
    assert _gate_passes([True, True, True], "all") is True
    assert _gate_passes([True, False, True], "all") is False
    assert _gate_passes([False, False, True], "any") is True
    assert _gate_passes([False, False, False], "any") is False
    assert _gate_passes([True, True, False, False], "min", 2) is True
    assert _gate_passes([True, False, False, False], "min", 2) is False
    assert _gate_passes([], "all") is True  # empty = pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
