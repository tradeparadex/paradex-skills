#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py>=0.6.0", "httpx"]
# ///
"""
benchmark.py — dispatcher throughput + latency under realistic retail load.

Spins up N strategies (default 10) covering common retail patterns, generates
M synthetic events (default 100k), and reports:
  - total wall time
  - events/sec
  - p50 / p95 / p99 dispatch latency (μs)
  - per-strategy fire count

This isolates the dispatch hot path. Network I/O is excluded — all webhooks
run in dry-run mode, which short-circuits before httpx is touched.

Run:
    uv run skills/strategy-listener/tests/benchmark.py
    uv run skills/strategy-listener/tests/benchmark.py --strategies 20 --events 200000
    uv run skills/strategy-listener/tests/benchmark.py --no-bar-aggregation  # skip bar agg overhead
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from indicators import IndicatorState                        # noqa: E402
from webhook import WebhookSender                            # noqa: E402
from paradex_listener import Dispatcher, BarAggregator       # noqa: E402


MARKETS = ["BTC-USD-PERP", "ETH-USD-PERP", "SOL-USD-PERP"]


def build_retail_strategies(n: int) -> list[dict]:
    """
    Generate `n` strategies in the shape a typical retail trader would deploy.
    Cycles through the canonical patterns so the load is representative.
    """
    patterns = [
        ("rsi-oversold", lambda mkt: {
            "conditions": {
                "gateMode": "all",
                "rsi":         {"enabled": True, "op": "<", "value": 30},
                "fundingRate": {"enabled": True, "op": ">", "value": 0.5},
            },
        }),
        ("rsi-overbought", lambda mkt: {
            "conditions": {
                "gateMode": "all",
                "rsi": {"enabled": True, "op": ">", "value": 70},
            },
        }),
        ("breakout-sma", lambda mkt: {
            "conditions": {
                "gateMode": "all",
                "sma": {"enabled": True, "op": "above", "period": 24},
                "rsi": {"enabled": True, "op": ">",     "value": 55},
            },
        }),
        ("drawdown-sma", lambda mkt: {
            "conditions": {
                "gateMode": "all",
                "sma": {"enabled": True, "op": "below", "period": 24},
                "rsi": {"enabled": True, "op": "<",     "value": 45},
            },
        }),
        ("funding-flip-pos", lambda mkt: {
            "conditions": {
                "gateMode": "any",
                "fundingRate": {"enabled": True, "op": ">", "value": 0.0},
            },
        }),
        ("funding-flip-neg", lambda mkt: {
            "conditions": {
                "gateMode": "any",
                "fundingRate": {"enabled": True, "op": "<", "value": 0.0},
            },
        }),
        ("whale-fill", lambda mkt: {
            "match": {"market": mkt, "minSize": 1.0},
        }),
    ]

    out: list[dict] = []
    for i in range(n):
        name, build = patterns[i % len(patterns)]
        market = MARKETS[i % len(MARKETS)]
        body = build(market)
        is_match = "match" in body
        out.append({
            "name": f"{name}-{market}-{i:02d}",
            "underlying": market.split("-")[0],
            "subscriptions": (
                {"user": ["fills"]} if is_match
                else {"market": [f"trades.{market}", f"funding.{market}"]}
            ),
            "evaluators": [{
                "id": "g",
                "on": ["fills"] if is_match else [f"bar_close.{market}"],
                **body,
                "webhook": {"url": "https://example/hooks/agent",
                            "messageTemplate": "{strategy}/{evaluator} fired"},
            }],
        })
    return out


def build_indicators(strategies: list[dict]) -> dict[str, IndicatorState]:
    """One IndicatorState per market that any strategy cares about, seeded
    with a noisy 200-bar history so RSI/SMA produce non-None values."""
    markets: set[str] = set()
    for s in strategies:
        for ev in s["evaluators"]:
            for tok in ev["on"]:
                if tok.startswith("bar_close."):
                    markets.add(tok.split(".", 1)[1])

    rng = random.Random(42)
    out: dict[str, IndicatorState] = {}
    for mkt in markets:
        # Realistic-looking noisy series with mild drift
        price = 100.0
        closes: list[float] = []
        for _ in range(200):
            price *= 1.0 + rng.uniform(-0.01, 0.012)
            closes.append(price)
        ind = IndicatorState(market=mkt, max_window=200)
        ind.seed_closes(closes)
        ind.update_funding(rng.uniform(-0.005, 0.015))
        out[mkt] = ind
    return out


def synthesize_events(n: int, indicators: dict[str, IndicatorState]) -> list[dict]:
    """
    Generate a mix of bar_close and fill events at retail-realistic ratios:
    80% bar_close, 20% fills. Markets cycle through the indicator pool.
    """
    rng = random.Random(7)
    market_list = list(indicators.keys()) or MARKETS
    events: list[dict] = []
    for i in range(n):
        if i % 5 == 0:
            mkt = rng.choice(MARKETS)
            events.append({
                "type": "fills",
                "market": mkt,
                "side": rng.choice(("BUY", "SELL")),
                "size": rng.uniform(0.05, 3.0),
                "price": rng.uniform(50_000, 100_000),
            })
        else:
            mkt = rng.choice(market_list)
            close = rng.uniform(50_000, 100_000)
            events.append({
                "type": f"bar_close.{mkt}",
                "market": mkt,
                "open": close * (1.0 + rng.uniform(-0.005, 0.005)),
                "high": close * (1.0 + rng.uniform(0.0, 0.01)),
                "low":  close * (1.0 - rng.uniform(0.0, 0.01)),
                "close": close,
                "volume": rng.uniform(0.1, 100.0),
            })
    return events


# ── Benchmark drivers ────────────────────────────────────────────────────────


async def run_bench(args: argparse.Namespace) -> None:
    print(f"# strategies={args.strategies} events={args.events} "
          f"warmup={args.warmup}")

    strategies = build_retail_strategies(args.strategies)
    indicators = build_indicators(strategies)
    events = synthesize_events(args.events + args.warmup, indicators)

    async with WebhookSender(dry_run=True) as sender:
        d = Dispatcher(strategies, sender, dry_run=True)

        # Warmup
        for ev in events[:args.warmup]:
            await d.dispatch(ev, indicators)
        for s in strategies:
            d.states[(s["name"], "g")] = type(d.states[(s["name"], "g")])(evaluator_id="g")

        latencies_us: list[float] = []
        wall_start = time.perf_counter()
        for ev in events[args.warmup:]:
            t0 = time.perf_counter_ns()
            await d.dispatch(ev, indicators)
            latencies_us.append((time.perf_counter_ns() - t0) / 1_000.0)
        wall = time.perf_counter() - wall_start

    fired_total = sum(s.fire_count for s in d.states.values())
    eval_total = sum(s.eval_count for s in d.states.values())
    n = len(latencies_us)
    latencies_us.sort()
    p50 = latencies_us[n // 2]
    p95 = latencies_us[int(n * 0.95)]
    p99 = latencies_us[int(n * 0.99)]
    pmax = latencies_us[-1]
    mean = statistics.fmean(latencies_us)

    print()
    print(f"wall_time            {wall*1000:>10.1f} ms")
    print(f"throughput           {n/wall:>10.0f} events/sec")
    print(f"per-event mean       {mean:>10.2f} μs")
    print(f"per-event p50        {p50:>10.2f} μs")
    print(f"per-event p95        {p95:>10.2f} μs")
    print(f"per-event p99        {p99:>10.2f} μs")
    print(f"per-event max        {pmax:>10.2f} μs")
    print(f"evaluations total    {eval_total:>10}")
    print(f"fires total          {fired_total:>10}")
    print(f"fires per 1k events  {fired_total*1000/n:>10.1f}")

    if args.show_per_strategy:
        print()
        print("# fires per strategy")
        for (sname, eid), st in sorted(d.states.items()):
            if st.fire_count:
                print(f"  {sname:50s} {st.fire_count:5d}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--strategies", type=int, default=10,
                   help="number of concurrent strategies (default: 10)")
    p.add_argument("--events", type=int, default=100_000,
                   help="number of events to dispatch (default: 100000)")
    p.add_argument("--warmup", type=int, default=1_000,
                   help="warmup events (excluded from timings)")
    p.add_argument("--show-per-strategy", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run_bench(parse_args()))
