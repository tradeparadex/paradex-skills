#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py>=0.6.0", "httpx"]
# ///
"""
paradex_listener.py — real-time strategy evaluator with OpenCLAW webhook fanout.

Subscribes to Paradex WS feeds (or polls REST), evaluates one or more strategy
JSON specs against each event, and POSTs to an OpenCLAW Gateway webhook on
matches. Same gate schema as skills/strategy-backtester.

Usage:
    uv run paradex_listener.py strategy.json
    uv run paradex_listener.py strategies/                       # directory of *.json
    uv run paradex_listener.py strategy.json --dry-run
    uv run paradex_listener.py strategy.json --env testnet|prod
    uv run paradex_listener.py strategy.json --data-mode ws|poll|auto

Env vars:
    PARADEX_ENVIRONMENT          testnet | prod (default: testnet)
    OPENCLAW_TOKEN               default bearer token for webhook POSTs

Authentication (only required when a strategy declares subscriptions.user;
public channels need none of these):

    PARADEX_JWT_TOKEN            pre-issued JWT — preferred; works with API
                                 keys generated from the dashboard or MCP
    PARADEX_ACCOUNT_PRIVATE_KEY  L1 private key (alternative to JWT)
    PARADEX_L1_ADDRESS           L1 address (paired with PARADEX_ACCOUNT_PRIVATE_KEY)

If both are set, JWT wins. If neither is set and the strategy uses only
public market channels, the listener runs unauthenticated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

# Local modules — loaded relative to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import IndicatorState, required_window  # noqa: E402
from evaluator import EvaluatorState, evaluate          # noqa: E402
from webhook import WebhookSender, render_message, correlation_id  # noqa: E402


log = logging.getLogger("listener")


BAR_SIZE_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


# ── Strategy loading + validation ────────────────────────────────────────────


def load_strategies(path: str) -> list[dict]:
    strategies, _ = load_strategies_with_index(path)
    return strategies


def load_strategies_with_index(path: str) -> tuple[list[dict], dict[str, float]]:
    """Load + validate, returning the spec list and a {file_path: mtime} index
    used by the watch loop to detect file-system changes."""
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.json"))
    elif p.is_file():
        files = [p]
    else:
        raise SystemExit(f"strategy path not found: {path}")

    strategies: list[dict] = []
    index: dict[str, float] = {}
    for f in files:
        try:
            spec = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"{f}: invalid JSON: {e}")
        validate(spec, source=str(f))
        strategies.append(spec)
        index[str(f.resolve())] = f.stat().st_mtime
    if not strategies:
        raise SystemExit(f"no strategy files found under {path}")
    return strategies, index


def scan_dir_index(path: str) -> dict[str, float]:
    p = Path(path)
    if not p.is_dir():
        return {}
    return {str(f.resolve()): f.stat().st_mtime for f in p.glob("*.json")}


def validate(spec: dict, *, source: str) -> None:
    where = f"[{source}]"
    for required in ("name", "underlying", "subscriptions", "evaluators"):
        if required not in spec:
            raise SystemExit(f"{where} missing required field: {required}")
    bar_size = spec.get("barSize", "1m")
    if bar_size not in BAR_SIZE_MIN:
        raise SystemExit(f"{where} barSize must be one of {sorted(BAR_SIZE_MIN)}")
    subs = spec["subscriptions"]
    declared = set(subs.get("market") or []) | set(subs.get("user") or [])
    has_user = bool(subs.get("user"))
    if has_user and not _auth_available():
        raise SystemExit(
            f"{where} declares user channels {subs.get('user')} but no auth "
            f"is configured. Set PARADEX_JWT_TOKEN, or both "
            f"PARADEX_ACCOUNT_PRIVATE_KEY and PARADEX_L1_ADDRESS."
        )
    for ev in spec["evaluators"]:
        for required in ("id", "on", "webhook"):
            if required not in ev:
                raise SystemExit(f"{where} evaluator missing field: {required}")
        gate_keys = [k for k in ("conditions", "match", "expression") if k in ev]
        if len(gate_keys) != 1:
            raise SystemExit(
                f"{where} evaluator {ev['id']!r}: must have exactly one of "
                f"'conditions', 'expression', or 'match'; got {gate_keys}"
            )
        if "expression" in ev:
            from expression import validate_or_raise, ExpressionError
            try:
                validate_or_raise(ev["expression"])
            except ExpressionError as e:
                raise SystemExit(
                    f"{where} evaluator {ev['id']!r}: invalid expression:\n  - "
                    + "\n  - ".join(e.errors)
                )
        for token in ev["on"]:
            head = token.split(".", 1)[0]
            if token in declared:
                continue
            if head == "bar_close":
                # synthetic event derived from a market subscription
                want = "trades." + token.split(".", 1)[1] if "." in token else None
                if want and want not in declared:
                    # accept bbo too
                    bbo_want = "bbo." + token.split(".", 1)[1]
                    if bbo_want not in declared:
                        raise SystemExit(
                            f"{where} evaluator {ev['id']!r}: on={token!r} "
                            f"requires {want!r} or {bbo_want!r} in subscriptions.market"
                        )
                continue
            if head not in declared and not _head_matches(head, declared):
                raise SystemExit(
                    f"{where} evaluator {ev['id']!r}: on={token!r} not in "
                    f"declared subscriptions"
                )


def _head_matches(head: str, declared: set[str]) -> bool:
    """`fills` matches `fills` or `fills.<MARKET>` in declared."""
    return any(d == head or d.startswith(head + ".") for d in declared)


# ── Paradex client + indicator backfill ──────────────────────────────────────


def _auth_available() -> bool:
    """True if either auth path is configured."""
    if os.environ.get("PARADEX_JWT_TOKEN"):
        return True
    return bool(os.environ.get("PARADEX_ACCOUNT_PRIVATE_KEY")
                and os.environ.get("PARADEX_L1_ADDRESS"))


def make_paradex(env: str):
    """
    Build a Paradex SDK client. Three auth modes:

      1. PARADEX_JWT_TOKEN set → inject the JWT, skip key-based onboarding.
         Preferred path: works with API keys minted from the dashboard or MCP
         server, no raw L1 key required.
      2. PARADEX_ACCOUNT_PRIVATE_KEY + PARADEX_L1_ADDRESS set → key-based
         onboarding (the SDK signs an auth tx and gets a JWT itself).
      3. Neither set → unauthenticated. Public market channels only.
    """
    from paradex_py import Paradex
    jwt = os.environ.get("PARADEX_JWT_TOKEN")
    pk = os.environ.get("PARADEX_ACCOUNT_PRIVATE_KEY")
    addr = os.environ.get("PARADEX_L1_ADDRESS")

    if jwt:
        # Construct with auto_auth=False so the SDK doesn't try to onboard
        # without keys; then inject the pre-issued token directly.
        paradex = Paradex(env=env, auto_auth=False)
        paradex.api_client.set_token(jwt)
        log.info(json.dumps({"event": "auth_mode", "mode": "jwt"}))
        return paradex

    if pk and addr:
        log.info(json.dumps({"event": "auth_mode", "mode": "l1_key"}))
        return Paradex(env=env, l1_private_key=pk, l1_address=addr)

    log.info(json.dumps({"event": "auth_mode", "mode": "none"}))
    return Paradex(env=env)


async def backfill_indicators(
    api_url: str,
    indicators: dict[str, IndicatorState],
    bar_size: str,
    log_fn=log.info,
) -> None:
    """
    Seed each market's IndicatorState with the last `state.max_window` bars
    from the public klines endpoint. Resolution maps to bar size.
    """
    resolution = BAR_SIZE_MIN[bar_size]
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for market, state in indicators.items():
            n = state.max_window
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - n * resolution * 60_000
            try:
                r = await client.get(
                    f"{api_url}/markets/klines",
                    params={
                        "symbol": market,
                        "resolution": resolution,
                        "start_at": start_ms,
                        "end_at": end_ms,
                    },
                )
                results = (r.json() or {}).get("results") or []
                closes = [float(k[4]) for k in results]
                state.seed_closes(closes)
                log_fn(json.dumps({
                    "event": "backfilled",
                    "market": market,
                    "bars": len(closes),
                    "resolution_min": resolution,
                }))
            except Exception as e:
                log.error(json.dumps({
                    "event": "backfill_error",
                    "market": market,
                    "error": f"{type(e).__name__}: {e}",
                }))


# ── Bar aggregation ──────────────────────────────────────────────────────────


class BarAggregator:
    """
    Aggregates trade/BBO ticks into bars of bar_size_min minutes per market.
    Emits a synthetic `bar_close.<MARKET>` event when a bar boundary closes.
    """

    def __init__(self, bar_size_min: int):
        self.bar_size_ms = bar_size_min * 60_000
        self._buckets: dict[str, dict] = {}

    def update(self, market: str, price: float, ts_ms: Optional[int] = None) -> Optional[dict]:
        if price is None:
            return None
        ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
        bucket_ts = (ts // self.bar_size_ms) * self.bar_size_ms
        b = self._buckets.get(market)
        if b is None:
            self._buckets[market] = {
                "bucket_ts": bucket_ts, "open": price, "high": price,
                "low": price, "close": price, "volume": 0.0,
            }
            return None
        if bucket_ts > b["bucket_ts"]:
            closed = {
                "type": f"bar_close.{market}",
                "market": market,
                "open": b["open"], "high": b["high"], "low": b["low"],
                "close": b["close"], "volume": b["volume"],
                "bucket_ts": b["bucket_ts"],
            }
            self._buckets[market] = {
                "bucket_ts": bucket_ts, "open": price, "high": price,
                "low": price, "close": price, "volume": 0.0,
            }
            return closed
        if price > b["high"]:
            b["high"] = price
        if price < b["low"]:
            b["low"] = price
        b["close"] = price
        return None


# ── Dispatcher ───────────────────────────────────────────────────────────────


class Dispatcher:
    """Routes inbound events to the matching evaluators across all loaded strategies."""

    def __init__(self, strategies: list[dict], sender: WebhookSender, dry_run: bool):
        self.strategies = strategies
        self.sender = sender
        self.dry_run = dry_run
        # eval state keyed by (strategy_name, evaluator_id)
        self.states: dict[tuple, EvaluatorState] = {}
        for s in strategies:
            for ev in s["evaluators"]:
                key = (s["name"], ev["id"])
                self.states[key] = EvaluatorState(evaluator_id=ev["id"])

    def set_strategies(self, new_strategies: list[dict]) -> tuple[set[str], set[str]]:
        """
        Atomically swap the active strategy list. Preserves existing
        EvaluatorState (throttle / cooldown / counters) for keys that survive
        the swap. Returns (added_names, removed_names) for logging.
        """
        new_keys: set[tuple] = set()
        added_names: set[str] = set()
        for s in new_strategies:
            for ev in s["evaluators"]:
                key = (s["name"], ev["id"])
                new_keys.add(key)
                self.states.setdefault(key, EvaluatorState(evaluator_id=ev["id"]))

        old_names = {s["name"] for s in self.strategies}
        new_names = {s["name"] for s in new_strategies}
        added_names = new_names - old_names
        removed_names = old_names - new_names

        # Drop state for evaluators that are gone
        for key in list(self.states):
            if key not in new_keys:
                del self.states[key]

        self.strategies = new_strategies
        return added_names, removed_names

    async def dispatch(self, event: dict, indicators: dict[str, IndicatorState]) -> None:
        ev_type = event.get("type")
        for s in self.strategies:
            for ev in s["evaluators"]:
                if not _matches_on(ev["on"], ev_type):
                    continue
                state = self.states[(s["name"], ev["id"])]
                result = evaluate(ev, state, event, indicators)
                log.debug(json.dumps({
                    "event": "evaluated",
                    "strategy": s["name"],
                    "evaluator": ev["id"],
                    "type": ev_type,
                    "fired": result.fired,
                    "reason": result.reason,
                }))
                if not result.fired:
                    continue
                cid = correlation_id(s["name"], ev["id"], int(time.time() * 1000))
                vars_for_template = {
                    "strategy": s["name"],
                    "evaluator": ev["id"],
                    "ts": int(time.time() * 1000),
                    "iso_ts": datetime.now(timezone.utc).isoformat(),
                    "underlying": s.get("underlying"),
                    "correlation_id": cid,
                    **result.template_vars,
                }
                template = ev["webhook"].get("messageTemplate") or \
                    f"{s['name']}/{ev['id']} fired"
                message = render_message(template, vars_for_template)
                log.info(json.dumps({
                    "event": "fire",
                    "strategy": s["name"],
                    "evaluator": ev["id"],
                    "type": ev_type,
                    "correlation_id": cid,
                }))
                await self.sender.fire(
                    webhook_cfg=ev["webhook"],
                    message=message,
                    correlation_id=cid,
                )


def _matches_on(on_list: list[str], event_type: Optional[str]) -> bool:
    if event_type is None:
        return False
    if event_type in on_list:
        return True
    head = event_type.split(".", 1)[0]
    return head in on_list


# ── Main loop ────────────────────────────────────────────────────────────────


async def main_async(args: argparse.Namespace) -> int:
    strategies, file_index = load_strategies_with_index(args.strategy)
    if args.watch and not Path(args.strategy).is_dir():
        raise SystemExit("--watch requires the strategy path to be a directory")
    log.info(json.dumps({
        "event": "loaded",
        "strategies": [s["name"] for s in strategies],
        "count": len(strategies),
        "watch": bool(args.watch),
    }))

    # Union channels across strategies
    market_channels: list[str] = []
    user_channels: list[str] = []
    for s in strategies:
        for ch in (s["subscriptions"].get("market") or []):
            if ch not in market_channels:
                market_channels.append(ch)
        for ch in (s["subscriptions"].get("user") or []):
            if ch not in user_channels:
                user_channels.append(ch)

    has_user = bool(user_channels)
    bar_size = strategies[0].get("barSize", "1m")
    bar_size_min = BAR_SIZE_MIN[bar_size]

    # Build indicator state per market that any strategy cares about. The
    # required_window is the max across all evaluators that touch the market,
    # whether they use the legacy `conditions` block or the new `expression`
    # tree.
    markets = _markets_needing_indicators(strategies)
    indicators: dict[str, IndicatorState] = {
        mkt: IndicatorState(market=mkt, max_window=w) for mkt, w in markets.items()
    }

    paradex = make_paradex(args.env)
    api_url = paradex.api_client.api_url
    log.info(json.dumps({"event": "paradex_ready", "env": args.env, "api_url": api_url}))

    if indicators:
        await backfill_indicators(api_url, indicators, bar_size)

    queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)

    # Source: WS preferred; poll fallback. Keep the source object in scope so
    # the watch loop can subscribe new channels at runtime.
    source_task: Optional[asyncio.Task] = None
    ws_source = None
    if args.data_mode in ("ws", "auto"):
        from ws_source import WSSource
        ws_source = WSSource(paradex, market_channels + user_channels, queue)

        async def on_reconnect() -> None:
            if indicators:
                log.info(json.dumps({"event": "rebackfill_after_reconnect"}))
                await backfill_indicators(api_url, indicators, bar_size)

        ws_source.on_gap(on_reconnect)
        source_task = asyncio.create_task(ws_source.run(), name="ws_source")
    else:
        from poll_source import PollSource
        ps = PollSource(api_url, market_channels + user_channels, queue,
                        interval_sec=int(strategies[0].get("pollIntervalSec") or 15),
                        bearer_token=None)
        source_task = asyncio.create_task(ps.run(), name="poll_source")

    aggregator = BarAggregator(bar_size_min)

    async with WebhookSender(dry_run=args.dry_run) as sender:
        dispatcher = Dispatcher(strategies, sender, args.dry_run)
        stop = asyncio.Event()

        def _on_signal(*_: Any) -> None:
            log.info(json.dumps({"event": "shutdown_signal"}))
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                pass

        watch_task: Optional[asyncio.Task] = None
        if args.watch:
            watch_task = asyncio.create_task(
                _watch_loop(
                    args.strategy, args.watch_interval, dispatcher,
                    file_index, ws_source, indicators, api_url, bar_size, stop,
                ),
                name="watch",
            )

        # Main consume loop
        while not stop.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Update indicator state based on the event
            mkt = event.get("market")
            if mkt and mkt in indicators:
                price = event.get("close") or event.get("mid") or event.get("price")
                if price is not None:
                    closed_bar = aggregator.update(mkt, price)
                    if closed_bar is not None:
                        indicators[mkt].append_close(closed_bar["close"])
                        await dispatcher.dispatch(closed_bar, indicators)
                if event.get("type", "").startswith("funding"):
                    fr = event.get("funding")
                    if fr is not None:
                        indicators[mkt].update_funding(fr)

            await dispatcher.dispatch(event, indicators)

        if watch_task is not None:
            watch_task.cancel()
            try:
                await watch_task
            except (asyncio.CancelledError, Exception):
                pass
        if source_task is not None:
            source_task.cancel()
            try:
                await source_task
            except (asyncio.CancelledError, Exception):
                pass

    return 0


# ── Watch loop ───────────────────────────────────────────────────────────────


async def _watch_loop(
    path: str,
    interval_sec: float,
    dispatcher: "Dispatcher",
    file_index: dict[str, float],
    ws_source,
    indicators: dict[str, IndicatorState],
    api_url: str,
    bar_size: str,
    stop: asyncio.Event,
) -> None:
    """
    Periodically rescan the strategy directory and reconcile.

    A user "submits" a strategy by writing a new `.json` file into this dir;
    the watch loop loads it, registers the evaluators, subscribes to any
    new channels, and (if the strategy needs indicators on a new market)
    backfills the rolling buffer.

    Removing a file unloads the strategy. Channels that become unused stay
    subscribed — leaving them open is harmless and avoids resubscribe churn.
    """
    while not stop.is_set():
        try:
            await asyncio.sleep(interval_sec)
            current = scan_dir_index(path)
            added = [f for f in current if f not in file_index]
            removed = [f for f in file_index if f not in current]
            modified = [f for f in current
                        if f in file_index and current[f] != file_index[f]]
            if not (added or removed or modified):
                continue

            try:
                new_strategies, _ = load_strategies_with_index(path)
            except SystemExit as e:
                log.error(json.dumps({"event": "watch_load_error", "error": str(e)}))
                continue

            added_names, removed_names = dispatcher.set_strategies(new_strategies)
            log.info(json.dumps({
                "event": "watch_reload",
                "added_files": [Path(f).name for f in added],
                "modified_files": [Path(f).name for f in modified],
                "removed_files": [Path(f).name for f in removed],
                "added_strategies": sorted(added_names),
                "removed_strategies": sorted(removed_names),
                "active_count": len(new_strategies),
            }))
            file_index.clear()
            file_index.update(current)

            # Reconcile WS subscriptions + indicator buffers for new strategies
            if ws_source is not None:
                for s in new_strategies:
                    if s["name"] not in added_names:
                        continue
                    for tok in (s["subscriptions"].get("market") or []):
                        await ws_source.subscribe_one(tok)
                    for tok in (s["subscriptions"].get("user") or []):
                        await ws_source.subscribe_one(tok)

            # Seed indicator state for any newly-referenced market
            new_markets = _markets_needing_indicators(new_strategies)
            missing = {m: w for m, w in new_markets.items() if m not in indicators}
            if missing:
                for mkt, w in missing.items():
                    indicators[mkt] = IndicatorState(market=mkt, max_window=w)
                await backfill_indicators(api_url, {m: indicators[m] for m in missing},
                                          bar_size)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(json.dumps({
                "event": "watch_error",
                "error": f"{type(e).__name__}: {e}",
            }))


def _markets_needing_indicators(strategies: list[dict]) -> dict[str, int]:
    """
    Union of (market → max bar window required) across all evaluators in
    all strategies. Handles both the legacy `conditions` form and the new
    `expression` form.
    """
    from indicators import required_window as cond_window
    from expression import required_window as expr_window

    out: dict[str, int] = {}
    for s in strategies:
        for ev in s["evaluators"]:
            cond = ev.get("conditions") or {}
            expr = ev.get("expression")
            need = 0
            if cond:
                need = max(need, cond_window(cond))
            if expr:
                need = max(need, expr_window(expr))
            if not cond and not expr:
                continue
            mkt = _market_from_evaluator_on(ev["on"]) or \
                _market_from_underlying(s.get("underlying"))
            if not mkt:
                continue
            out[mkt] = max(out.get(mkt, 0), max(need, 60))  # min 60 warmup
    return out


def _market_from_evaluator_on(on_list: list[str]) -> Optional[str]:
    for tok in on_list:
        if "." in tok:
            return tok.split(".", 1)[1]
    return None


def _market_from_underlying(underlying: Optional[str]) -> Optional[str]:
    if not underlying:
        return None
    return f"{underlying}-USD-PERP"


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("strategy", help="strategy JSON file or directory of *.json")
    p.add_argument("--dry-run", action="store_true",
                   help="log webhook POSTs instead of sending them")
    p.add_argument("--env", default=os.environ.get("PARADEX_ENVIRONMENT", "testnet"),
                   choices=("testnet", "prod"))
    p.add_argument("--data-mode", default=None, choices=("ws", "poll", "auto"),
                   help="override strategy.dataMode")
    p.add_argument("--watch", action="store_true",
                   help="poll the strategy directory and hot-reload on changes "
                        "(directory mode only — drop a *.json file to submit a strategy)")
    p.add_argument("--watch-interval", type=float, default=2.0,
                   help="seconds between directory scans when --watch is set "
                        "(default: 2.0)")
    p.add_argument("--check", action="store_true",
                   help="validate strategy file(s) and exit 0/1 — no network, "
                        "no auth required. Agents should run this before "
                        "writing to a watched directory.")
    p.add_argument("--catalog", action="store_true",
                   help="print the supported indicators / event fields / "
                        "operators (JSON) and exit. Use to ground an agent "
                        "before it generates an expression.")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return p.parse_args()


def run_check(path: str) -> int:
    """
    Static + smoke validation of strategy file(s). Returns shell exit code.
    Bypasses auth (`subscriptions.user` channels are not blocked here — we
    only verify shape). The full auth check fires when you actually run.
    """
    p = Path(path)
    files: list[Path]
    if p.is_dir():
        files = sorted(p.glob("*.json"))
    elif p.is_file():
        files = [p]
    else:
        print(json.dumps({"event": "check_error",
                          "error": f"path not found: {path}"}))
        return 2
    if not files:
        print(json.dumps({"event": "check_error",
                          "error": f"no *.json under {path}"}))
        return 2

    failed = 0
    for f in files:
        try:
            spec = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(json.dumps({"event": "check_failed", "file": f.name,
                              "errors": [f"invalid JSON: {e}"]}))
            failed += 1
            continue

        errors = _check_spec(spec, source=str(f))
        if errors:
            print(json.dumps({"event": "check_failed", "file": f.name,
                              "errors": errors}))
            failed += 1
        else:
            print(json.dumps({"event": "check_ok", "file": f.name,
                              "name": spec.get("name"),
                              "evaluators": [e["id"] for e in
                                             spec.get("evaluators") or []]}))
    return 0 if failed == 0 else 1


def _check_spec(spec: dict, *, source: str) -> list[str]:
    """All checks that don't require network or auth env. Used by --check."""
    errors: list[str] = []

    # 1. Shape: reuse validate() but capture its SystemExits as errors.
    saved_env = {k: os.environ.get(k) for k in
                 ("PARADEX_JWT_TOKEN", "PARADEX_ACCOUNT_PRIVATE_KEY",
                  "PARADEX_L1_ADDRESS")}
    # Pretend auth is set so validate() doesn't reject user-channel strategies
    # in --check mode (auth is verified at run time, not check time).
    os.environ["PARADEX_JWT_TOKEN"] = saved_env.get("PARADEX_JWT_TOKEN") or "check-mode-placeholder"
    try:
        validate(spec, source=source)
    except SystemExit as e:
        errors.append(str(e))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # 2. Per-evaluator DSL smoke-eval (catches runtime errors validation missed)
    from expression import smoke_eval, ExpressionError
    for ev in spec.get("evaluators") or []:
        if "expression" in ev:
            try:
                smoke_eval(ev["expression"])
            except ExpressionError as e:
                errors.extend(e.errors)
            except Exception as e:
                errors.append(f"evaluator {ev.get('id', '?')!r}: smoke_eval "
                              f"raised {type(e).__name__}: {e}")
    return errors


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(message)s",  # we emit JSON ourselves
        stream=sys.stdout,
    )
    if args.catalog:
        from conditions import catalog
        print(json.dumps(catalog(), indent=2))
        return 0
    if args.check:
        return run_check(args.strategy)
    if args.data_mode is None:
        try:
            first = load_strategies(args.strategy)[0]
            args.data_mode = first.get("dataMode") or "ws"
        except SystemExit:
            raise
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
