#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py", "httpx"]
# ///
"""
paradex_backtest_engine.py — CLI strategy backtester for Paradex options.

Ports the full simulation engine from strategy_backtester.html to Python.
Runs on low-memory / low-CPU machines via uv — no browser required.

Usage:
    uv run paradex_backtest_engine.py strategy.json
    uv run paradex_backtest_engine.py strategy.json --start 2025-01-01 --end 2026-04-27
    uv run paradex_backtest_engine.py strategy.json --deribit data.csv
    uv run paradex_backtest_engine.py strategy.json --output results.json
    uv run paradex_backtest_engine.py strategy.json --testnet

The strategy JSON format is identical to the browser tool's Export/Import format.
Templates are documented in skills/strategy-backtester/SKILL.md.

Data sources:
    Paradex   Fetches klines + mark IV + margin config from the Paradex REST API.
              No auth required (public endpoints).
    Deribit   Reads a CSV exported from Deribit historical data. Pass via --deribit.

Output:
    Terminal: metrics summary + trade log
    --output FILE: full JSON with equity curve, trades, metrics
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

HOURS_PER_YEAR = 8760
MAX_KLINES_PER_REQ = 500
STRIKE_TICK = {"BTC": 1000, "ETH": 100, "SOL": 5}


# ── Black-Scholes ────────────────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 1e-10:
        return max((S - K) if is_call else (K - S), 0.0)
    if sigma <= 0:
        return max((S - K) if is_call else (K - S), 0.0)
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    if T <= 1e-10:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    if sigma <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1) if is_call else norm_cdf(d1) - 1.0


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 1e-10 or sigma <= 0:
        return 0.0
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    return norm_pdf(d1) / (S * sigma * sqT)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 1e-10 or sigma <= 0:
        return 0.0
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    return S * sqT * norm_pdf(d1) / 100.0


def find_strike_by_delta(
    S: float, T: float, r: float, sigma: float,
    target_delta: float, is_call: bool, tick: float
) -> float:
    """Binary-search for the strike whose BS delta equals target_delta."""
    lo, hi = S * 0.3, S * 3.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        d = bs_delta(S, mid, T, r, sigma, is_call)
        if abs(d - target_delta) < 0.0005:
            lo = hi = mid
            break
        if d > target_delta:
            lo = mid
        else:
            hi = mid
    raw = (lo + hi) / 2.0
    return round(raw / tick) * tick


# ── Technical Indicators ─────────────────────────────────────────────────────

def compute_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n < period + 1:
        return rsi
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            avg_gain += d
        else:
            avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    rsi[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return rsi


def compute_sma(closes: list[float], period: int) -> list[float | None]:
    n = len(closes)
    sma: list[float | None] = [None] * n
    total = 0.0
    for i in range(n):
        total += closes[i]
        if i >= period:
            total -= closes[i - period]
        if i >= period - 1:
            sma[i] = total / period
    return sma


def compute_realized_vol(closes: list[float], window: int) -> list[float | None]:
    n = len(closes)
    rv: list[float | None] = [None] * n
    if n < window + 1:
        return rv
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    for i in range(window, len(log_rets)):
        sl = log_rets[i - window:i]
        mean = sum(sl) / window
        var = sum((x - mean) ** 2 for x in sl) / (window - 1)
        rv[i + 1] = math.sqrt(var) * math.sqrt(HOURS_PER_YEAR)
    return rv


def compute_iv_percentile(current_iv: float, history: list[float]) -> float:
    """Rank current_iv against history. Returns 0–100."""
    if not history:
        return 50.0
    below = sum(1 for v in history if v < current_iv)
    return below / len(history) * 100.0


# ── Paradex API helpers ───────────────────────────────────────────────────────

def _make_client(testnet: bool = False) -> Any:
    """Create a Paradex SDK client for public (unauthenticated) endpoints."""
    import httpx
    from paradex_py.api.api_client import ParadexApiClient
    from paradex_py.api.protocols import DefaultRetryStrategy
    from paradex_py.environment import PROD, TESTNET

    env = TESTNET if testnet else PROD
    return ParadexApiClient(
        env=env,
        logger=None,
        http_client=httpx.Client(
            transport=httpx.HTTPTransport(retries=1),
            timeout=httpx.Timeout(60.0),
            headers={"User-Agent": "paradex-backtest-engine/1.0"},
        ),
        auto_auth=False,
        retry_strategy=DefaultRetryStrategy(),
    )


def _get(client: Any, path: str, params: dict | None = None) -> Any:
    """GET a public Paradex API endpoint. Returns the decoded JSON dict."""
    return client.get(client.api_url, path, params or {})


def _paginate(client: Any, path: str, initial_params: dict) -> list[Any]:
    """
    Exhaust a cursor-paginated Paradex endpoint.

    Paradex returns an opaque base64 `next` token in paginated responses
    (see PaginatedAPIResults in the SDK). Passing `cursor=<next>` as the
    sole parameter on subsequent calls fetches the next page — the cursor
    encodes the original filter so you don't need to repeat it.

    Endpoints that support this:  funding/data, fills, orders, transactions.
    Endpoints that do NOT (time-windowed only):  markets/klines, markets/summary.

    Returns the combined `results` list across all pages.
    """
    all_results: list[Any] = []
    params: dict = dict(initial_params)
    while True:
        data = _get(client, path, params)
        results = data.get("results") or []
        all_results.extend(results)
        next_cursor = data.get("next")
        if not next_cursor:
            break
        params = {"cursor": next_cursor}  # cursor encodes original filter
    return all_results


class _DataCache:
    """
    Simple JSON file cache for fetched market data.

    Historical klines/IV/funding data is immutable once settled — safe to cache
    permanently. Margin config changes occasionally — cached with a 24h TTL.

    Usage:
        cache = _DataCache("~/.paradex_backtest_cache")
        data  = cache.get("klines_BTC_USD_PERP_...")  # None on miss
        cache.set("klines_BTC_USD_PERP_...", data)
    """

    def __init__(self, directory: str | None):
        self.directory = os.path.expanduser(directory) if directory else None
        if self.directory:
            os.makedirs(self.directory, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.directory, key.replace("-", "_") + ".json")  # type: ignore[arg-type]

    def get(self, key: str, ttl_s: float | None = None) -> Any:
        if not self.directory:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            envelope = json.load(fh)
        if ttl_s is not None and time.time() - envelope.get("_ts", 0) > ttl_s:
            return None
        return envelope["data"]

    def set(self, key: str, data: Any) -> None:
        if not self.directory:
            return
        with open(self._path(key), "w") as fh:
            json.dump({"_ts": time.time(), "data": data}, fh)


def _start_timeout(seconds: int) -> None:
    """Start a daemon thread that force-exits the process after `seconds` seconds."""
    import threading

    def _fire() -> None:
        print(f"\nBacktest timed out after {seconds}s", file=sys.stderr)
        os._exit(1)

    t = threading.Timer(seconds, _fire)
    t.daemon = True
    t.start()


def _gate_passes(results: list[bool], mode: str, gate_min: int = 1) -> bool:
    """Return True if `results` satisfy the gate threshold defined by `mode`.

    mode="all"  → all conditions must pass (AND)
    mode="any"  → at least one must pass (OR)
    mode="min"  → at least gate_min must pass

    Empty results (no enabled conditions) always pass.
    """
    if not results:
        return True
    count = sum(results)
    if mode == "any":
        return count >= 1
    if mode == "min":
        return count >= min(max(1, gate_min), len(results))
    return count == len(results)  # "all" (default)


def _dedup_sorted(items: list[dict], key: str = "t") -> list[dict]:
    """Sort items by `key` and remove entries with duplicate key values."""
    items.sort(key=lambda x: x[key])
    seen: set = set()
    return [x for x in items if not (x[key] in seen or seen.add(x[key]))]


def fetch_klines(
    client: Any,
    symbol: str,
    start_ms: int,
    end_ms: int,
    resolution: int = 60,
    log=print,
    cache: _DataCache | None = None,
) -> list[dict]:
    cache_key = f"klines_{symbol}_{start_ms}_{end_ms}_{resolution}"
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            log(f"  klines: cache hit ({len(cached)} bars)")
            return cached

    bars: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + MAX_KLINES_PER_REQ * resolution * 60_000, end_ms)
        try:
            data = _get(client, "markets/klines", {
                "symbol": symbol,
                "resolution": resolution,
                "start_at": cursor,
                "end_at": chunk_end,
            })
        except Exception as e:
            log(f"  klines fetch error: {e}")
            break
        # API returns list-of-lists: [timestamp_ms, open, high, low, close, volume]
        results = data.get("results") or []
        if not results:
            break
        for k in results:
            bars.append({"t": k[0], "o": float(k[1]), "h": float(k[2]),
                         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
        last_t = results[-1][0]
        if last_t <= cursor:
            break
        cursor = last_t + resolution * 60_000

    bars = _dedup_sorted(bars)

    if cache and bars:
        cache.set(cache_key, bars)
    return bars


def fetch_historical_iv(
    client: Any,
    underlying: str,
    bars: list[dict],
    r: float,
    log=print,
    atm_term_days: int = 7,
    cache: _DataCache | None = None,
) -> dict | None:
    """
    Fetch per-bar mark IV from /markets/summary snapshots.
    Returns {iv_arr, greeks_arr, atm_iv_arr, mean_iv, coverage} or None.
    Mirrors fetchHistoricalIV() from the JS engine.
    """
    if not bars:
        return None
    start_ms = bars[0]["t"]
    end_ms = bars[-1]["t"]

    cache_key = f"iv_{underlying}_{start_ms}_{end_ms}_{atm_term_days}"
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            log(f"  IV: cache hit ({cached.get('coverage', '?')}/{len(bars)} bars)")
            return cached
    avg_spot = (bars[0]["c"] + bars[-1]["c"]) / 2.0
    MIN_DTE_DAYS = 7

    # 1. Fetch option markets
    log(f"  Fetching option markets for {underlying}...")
    try:
        mkts_data = _get(client, "markets", {"limit": 100})
    except Exception as e:
        log(f"  Markets fetch failed: {e}")
        return None

    all_mkts = mkts_data.get("results") or []
    opt_calls = [
        m for m in all_mkts
        if m.get("asset_kind") == "OPTION"
        and m.get("base_currency") == underlying
        and m.get("option_type") == "CALL"
    ]
    if not opt_calls:
        log(f"  No option markets for {underlying}")
        return None

    # 2. Group by expiry, pick nearest ATM call per expiry
    by_expiry: dict[int, list] = {}
    for m in opt_calls:
        exp = int(m["expiry_at"])
        by_expiry.setdefault(exp, []).append(m)

    selected: list[dict] = []
    for exp, mkts in by_expiry.items():
        mkts.sort(key=lambda m: abs(float(m["strike_price"]) - avg_spot))
        selected.append({
            "symbol": mkts[0]["symbol"],
            "strike": float(mkts[0]["strike_price"]),
            "expiry": exp,
            "summaries": [],
        })
    selected.sort(key=lambda c: c["expiry"])
    to_fetch = selected[:8]

    # 3. Paginate /markets/summary for each contract
    log(f"  Fetching market summaries for {len(to_fetch)} contracts...")
    for c in to_fetch:
        snaps: list[dict] = []
        cursor = start_ms
        guard = 0
        while cursor < end_ms and guard < 100:
            guard += 1
            try:
                data = _get(client, "markets/summary", {
                    "market": c["symbol"],
                    "start": cursor,
                    "end": end_ms,
                    "page_size": 500,
                })
                rows = data.get("results") or []
            except Exception:
                break
            if not rows:
                break
            for s in rows:
                iv = float(s.get("mark_iv") or 0)
                if iv and 0.01 < iv < 5.0:
                    greeks = s.get("greeks") or {}
                    snaps.append({
                        "t": s["created_at"],
                        "mark_iv": iv,
                        "mark_price": float(s.get("mark_price") or 0),
                        "delta": float(greeks.get("delta") or 0) if greeks else None,
                        "gamma": float(greeks.get("gamma") or 0) if greeks else None,
                        "vega": float(greeks.get("vega") or 0) if greeks else None,
                        "theta": float(greeks.get("theta") or 0) if greeks else None,
                    })
            last_t = rows[-1]["created_at"]
            if not last_t or last_t <= cursor:
                break
            cursor = last_t + 1

        snaps.sort(key=lambda s: s["t"])
        c["summaries"] = snaps
        log(f"    {c['symbol']}: {len(snaps)} snapshots")

    # 4. Build per-bar IV arrays
    n = len(bars)
    iv_arr: list[float | None] = [None] * n
    greeks_arr: list[dict | None] = [None] * n
    atm_iv_arr: list[float | None] = [None] * n
    last_iv: float | None = None

    for i, bar in enumerate(bars):
        spot = bar["c"]

        # Best contract: ≥7 DTE, nearest ATM, has summaries
        best = None
        best_dist = float("inf")
        for c in to_fetch:
            dte = (c["expiry"] - bar["t"]) / (24 * 3_600_000)
            if dte < MIN_DTE_DAYS or not c["summaries"]:
                continue
            dist = abs(c["strike"] - spot) / spot
            if dist < best_dist:
                best_dist = dist
                best = c

        if best:
            snaps = best["summaries"]
            snap = _closest_snap(snaps, bar["t"])
            if snap:
                iv_arr[i] = snap["mark_iv"]
                greeks_arr[i] = {"delta": snap["delta"], "gamma": snap["gamma"],
                                  "vega": snap["vega"], "theta": snap["theta"]}
                last_iv = snap["mark_iv"]
        else:
            iv_arr[i] = last_iv

        # Constant-DTE ATM IV via linear interpolation across expiries
        points: list[dict] = []
        for c in to_fetch:
            if not c["summaries"]:
                continue
            dte_now = (c["expiry"] - bar["t"]) / (24 * 3_600_000)
            if dte_now < 1:
                continue
            snap = _closest_snap(c["summaries"], bar["t"])
            if not snap or not snap["mark_iv"]:
                continue
            if abs(snap["t"] - bar["t"]) > 24 * 3_600_000:
                continue
            points.append({"dte": dte_now, "iv": snap["mark_iv"]})

        if points:
            points.sort(key=lambda p: p["dte"])
            atm_iv_arr[i] = _interp_iv(points, atm_term_days)

    coverage = sum(1 for v in iv_arr if v is not None)
    atm_coverage = sum(1 for v in atm_iv_arr if v is not None)
    valid = [v for v in iv_arr if v is not None]
    mean_iv = sum(valid) / len(valid) if valid else None
    log(f"  IV series: {coverage}/{n} bars, mean={mean_iv*100:.1f}% " if mean_iv else
        f"  IV series: {coverage}/{n} bars")
    log(f"  ATM IV ({atm_term_days}d term): {atm_coverage}/{n} bars")

    result = {"iv_arr": iv_arr, "greeks_arr": greeks_arr, "atm_iv_arr": atm_iv_arr,
              "mean_iv": mean_iv, "coverage": coverage}
    if cache and coverage > 0:
        cache.set(cache_key, result)
    return result


def _closest_snap(snaps: list[dict], t: int) -> dict | None:
    """Binary-search for the snapshot closest in time to t."""
    if not snaps:
        return None
    if t < snaps[0]["t"]:
        return None  # Don't extrapolate backwards
    if t >= snaps[-1]["t"]:
        return snaps[-1]
    lo, hi = 0, len(snaps) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if snaps[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    return snaps[lo] if (t - snaps[lo]["t"]) <= (snaps[hi]["t"] - t) else snaps[hi]


def _interp_iv(points: list[dict], target_dte: float) -> float:
    if target_dte <= points[0]["dte"]:
        return points[0]["iv"]
    if target_dte >= points[-1]["dte"]:
        return points[-1]["iv"]
    for j in range(len(points) - 1):
        if points[j]["dte"] <= target_dte <= points[j + 1]["dte"]:
            span = points[j + 1]["dte"] - points[j]["dte"]
            frac = (target_dte - points[j]["dte"]) / span if span > 0 else 0.0
            return points[j]["iv"] + frac * (points[j + 1]["iv"] - points[j]["iv"])
    return points[-1]["iv"]


def fetch_funding_index(
    client: Any, symbol: str, start_ms: int, end_ms: int, log=print,
    cache: _DataCache | None = None,
) -> list[dict]:
    """Fetch cumulative funding_index series. Returns [{t, idx}, ...] sorted by t."""
    cache_key = f"funding_{symbol}_{start_ms}_{end_ms}"
    if cache:
        cached = cache.get(cache_key)
        if cached is not None:
            log(f"  funding: cache hit ({len(cached)} points)")
            return cached

    log(f"  Fetching funding index for {symbol}...")
    rows = _paginate(client, "funding/data", {
        "market": symbol,
        "start_at": start_ms,
        "end_at": end_ms,
        "page_size": 500,
    })

    all_points: list[dict] = []
    for f in rows:
        t = f.get("created_at") or f.get("timestamp")
        idx = float(f.get("funding_index", "nan"))
        if t and math.isfinite(idx):
            all_points.append({"t": t, "idx": idx})
    all_points = _dedup_sorted(all_points)

    if cache and all_points:
        cache.set(cache_key, all_points)
    return all_points


def funding_index_at(series: list[dict], t: int) -> float | None:
    """Right-continuous lookup of funding_index at time t."""
    if not series:
        return None
    if t < series[0]["t"]:
        return series[0]["idx"]
    if t >= series[-1]["t"]:
        return series[-1]["idx"]
    lo, hi = 0, len(series) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if series[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    return series[lo]["idx"]


def fetch_margin_config(
    client: Any, underlying: str, log=print,
    cache: _DataCache | None = None,
) -> dict:
    """Fetch XM and PM margin parameters from the Paradex REST API."""
    cache_key = f"margin_{underlying}"
    if cache:
        cached = cache.get(cache_key, ttl_s=86400)  # 24h TTL; params change rarely
        if cached is not None:
            log("  margin config: cache hit")
            return cached

    config: dict = {
        "mode": "XM",
        "perp_params": {},
        "option_params": {},
        "pm_config": None,
        "fee_rate": 0.0005,
    }
    log("  Fetching margin parameters...")
    try:
        mkts_data = _get(client, "markets")
        all_mkts = mkts_data.get("results") or []
    except Exception as e:
        log(f"  Markets fetch failed: {e}")
        return config

    for m in all_mkts:
        sym = m.get("symbol")
        if not sym:
            continue
        xm = m.get("delta1_cross_margin_params")
        if xm:
            config["perp_params"][sym] = {
                "imf_base": float(xm.get("imf_base") or 0),
                "imf_factor": float(xm.get("imf_factor") or 0),
                "imf_shift": float(xm.get("imf_shift") or 0),
                "mmf_factor": float(xm.get("mmf_factor") or 0.5),
            }
        oxm = m.get("option_cross_margin_params")
        if oxm and oxm.get("imf") and oxm.get("mmf"):
            config["option_params"][sym] = {
                "imf": {k: float(v) for k, v in oxm["imf"].items()},
                "mmf": {k: float(v) for k, v in oxm["mmf"].items()},
            }
        fee_cfg = m.get("fee_config") or {}
        for cat_key in ("interactive_fee", "api_fee", "rpi_fee"):
            cat = fee_cfg.get(cat_key) or {}
            taker = cat.get("taker_fee") or {}
            rate = float(taker.get("fee") or 0)
            if rate > config["fee_rate"]:
                config["fee_rate"] = rate

    try:
        pm_data = _get(client, "system/portfolio-margin-config")
        configs = pm_data.get("results") or []
        ul_cfg = next((c for c in configs if c.get("base_asset") == underlying), None)
        if ul_cfg:
            vsp = ul_cfg.get("vol_shock_params") or {}
            config["pm_config"] = {
                "scenarios": [[s["spot_shock"], s["vol_shock"], s["weight"]]
                               for s in (ul_cfg.get("scenarios") or [])],
                "unhedged_mf": ul_cfg.get("unhedged_margin_factor"),
                "hedged_mf": ul_cfg.get("hedged_margin_factor"),
                "mmr_factor": ul_cfg.get("mmf_factor"),
                "vega_power_st": vsp.get("vega_power_short_dte"),
                "vega_power_lt": vsp.get("vega_power_long_dte"),
                "dte_floor": vsp.get("dte_floor_days") or 5,
                "funding_period_hours": ul_cfg.get("funding_provision_hour") or 24,
            }
            log(f"  PM config: {len(config['pm_config']['scenarios'])} scenarios, "
                f"MMF={config['pm_config']['mmr_factor']}")
    except Exception as e:
        log(f"  PM config fetch failed (XM still available): {e}")

    if cache:
        cache.set(cache_key, config)
    return config


# ── Margin Engine ─────────────────────────────────────────────────────────────

def xm_perp_margin(size: float, price: float, params: dict) -> dict:
    open_size = abs(size)
    imf = params["imf_base"] + params["imf_factor"] * math.sqrt(max(0, open_size - params["imf_shift"]))
    return {
        "imr": open_size * price * imf,
        "mmr": open_size * price * imf * params["mmf_factor"],
    }


def xm_option_margin(
    is_buy: bool, is_call: bool, size: float,
    strike: float, spot: float, mark_price: float, params: dict
) -> dict:
    def calc(p: dict) -> float:
        if is_buy:
            return min(mark_price * p["premium_multiplier"], p["long_itm"] * spot) * size
        else:
            otm = max(0, strike - spot) if is_call else max(0, spot - strike)
            margin = max(p["short_itm"] * spot - otm, p["short_otm"] * spot)
            if not is_call:
                margin = min(margin, p["short_put_cap"] * spot)
            return margin * size

    return {"imr": calc(params["imf"]), "mmr": calc(params["mmf"])}


def pm_margin_at_spot(
    positions: list[dict],
    test_spot: float,
    pricing_vol: float,
    r: float,
    pm_cfg: dict,
    fund_rate_8h: float,
) -> dict:
    scenarios = pm_cfg["scenarios"]
    n_sc = len(scenarios)
    pos_pnls = [0.0] * n_sc
    pos_deltas: list[float] = []

    for pos in positions:
        signed = pos["size"] if pos["side"] == "BUY" else -pos["size"]
        for sc in range(n_sc):
            spot_shock, vol_shock, weight = scenarios[sc]
            shocked_spot = test_spot * (1 + spot_shock)
            if pos["leg_type"] == "perp":
                sc_price = shocked_spot
            else:
                dte = max(0, pos["dte_at_entry"] - pos["bars_held"] / 24)
                T = dte / 365.0
                dte_days = dte
                dte_floor = pm_cfg.get("dte_floor") or 5
                if dte_days < 30:
                    vega_pow = pm_cfg.get("vega_power_st") or 0.5
                else:
                    vega_pow = pm_cfg.get("vega_power_lt") or 0.5
                iv_shock_scale = math.pow(30 / max(dte_floor, dte_days), vega_pow)
                shocked_vol = max(0.01, pricing_vol * (1 + vol_shock * iv_shock_scale))
                sc_price = bs_price(shocked_spot, pos["strike"], T, r, shocked_vol, pos["is_call"])
            pos_pnls[sc] += (sc_price - pos["current_price"]) * weight * signed
        pos_deltas.append((pos.get("current_delta") or 0) * signed)

    worst_loss = max(0.0, max((-min(0.0, p) for p in pos_pnls), default=0.0))

    long_delta = sum(d for d in pos_deltas if d > 0)
    short_delta = sum(abs(d) for d in pos_deltas if d < 0)
    max_unhedged = max(0.0, max(long_delta, short_delta) - min(long_delta, short_delta))
    hedged = max(0.0, max(long_delta, short_delta) - max_unhedged)
    delta_min = (hedged * pm_cfg["hedged_mf"] + max_unhedged * pm_cfg["unhedged_mf"]) * test_spot

    net_im = max(worst_loss, delta_min)

    fund_prov = 0.0
    for pos in positions:
        if pos["leg_type"] == "perp":
            signed = pos["size"] if pos["side"] == "BUY" else -pos["size"]
            fund_prov += -fund_rate_8h * signed * test_spot
    fund_prov = max(0.0, -fund_prov)

    mmr = net_im * pm_cfg["mmr_factor"] + fund_prov
    imr = net_im + fund_prov
    return {"imr": imr, "mmr": mmr}


def calculate_margin_at_spot(
    positions: list[dict],
    test_spot: float,
    pricing_vol: float,
    r: float,
    margin_config: dict,
    underlying: str,
    fund_rate_8h: float,
) -> dict:
    mode = margin_config["mode"]
    total_imr = total_mmr = 0.0

    if mode == "PM" and margin_config.get("pm_config"):
        pm = pm_margin_at_spot(positions, test_spot, pricing_vol, r,
                               margin_config["pm_config"], fund_rate_8h)
        total_imr = pm["imr"]
        total_mmr = pm["mmr"]
    else:
        perp_key = f"{underlying}-USD-PERP"
        perp_params = margin_config["perp_params"].get(perp_key)
        for pos in positions:
            if pos["leg_type"] == "perp" and perp_params:
                m = xm_perp_margin(pos["size"], test_spot, perp_params)
                total_imr += m["imr"]
                total_mmr += m["mmr"]
            elif pos["leg_type"] == "option":
                opt_params = next(
                    (p for sym, p in margin_config["option_params"].items()
                     if sym.startswith(f"{underlying}-USD-")),
                    None,
                )
                if opt_params:
                    dte = max(0, pos["dte_at_entry"] - pos["bars_held"] / 24)
                    T = dte / 365.0
                    mp = bs_price(test_spot, pos["strike"], T, r, pricing_vol, pos["is_call"])
                    m = xm_option_margin(pos["side"] == "BUY", pos["is_call"],
                                         pos["size"], pos["strike"], test_spot, mp, opt_params)
                    total_imr += m["imr"]
                    total_mmr += m["mmr"]
            notional = (pos["size"] * test_spot if pos["leg_type"] == "perp"
                        else pos["size"] * pos["current_price"])
            total_imr += notional * margin_config["fee_rate"]
            total_mmr += notional * margin_config["fee_rate"]

    return {"imr": total_imr, "mmr": total_mmr}


def brent_root(f, a: float, b: float, tol: float = 1e-8, max_iter: int = 80) -> float | None:
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        return None
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa
    c, fc = a, fa
    mflag = True
    d = 0.0
    for _ in range(max_iter):
        if abs(fb) < tol:
            return b
        if abs(fa - fc) > 1e-15 and abs(fb - fc) > 1e-15:
            s = (a * fb * fc / ((fa - fb) * (fa - fc))
                 + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)
        cond1 = not ((3 * a + b) / 4 < s < b or b < s < (3 * a + b) / 4)
        cond2 = mflag and abs(s - b) >= abs(b - c) / 2
        cond3 = (not mflag) and abs(s - b) >= abs(c - d) / 2
        cond4 = mflag and abs(b - c) < tol
        cond5 = (not mflag) and abs(c - d) < tol
        if cond1 or cond2 or cond3 or cond4 or cond5:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False
        fs = f(s)
        d, c, fc = c, b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa
    return b


def find_liquidation_price(
    cash: float,
    positions: list[dict],
    spot: float,
    pricing_vol: float,
    r: float,
    margin_config: dict,
    underlying: str,
    fund_rate_8h: float,
) -> dict:
    if not positions:
        return {"down": None, "up": None, "nearest": None, "dist_pct": None}

    current_account_value = cash + sum(
        p["unrealized_pnl"] - p["funding_paid"] for p in positions
    )

    def account_value_at_spot(test_spot: float) -> float:
        mtm_delta = 0.0
        for pos in positions:
            if pos["leg_type"] == "perp":
                diff = test_spot - spot
                val = (diff if pos["side"] == "BUY" else -diff) * pos["size"]
            else:
                dte = max(0, pos["dte_at_entry"] - pos["bars_held"] / 24)
                T = dte / 365.0
                price_at_test = bs_price(test_spot, pos["strike"], T, r, pricing_vol, pos["is_call"])
                price_at_cur = pos.get("current_price") or bs_price(spot, pos["strike"], T, r, pricing_vol, pos["is_call"])
                val = (1 if pos["side"] == "BUY" else -1) * (price_at_test - price_at_cur) * pos["size"]
            mtm_delta += val
        return current_account_value + mtm_delta

    def f(test_spot: float) -> float:
        av = account_value_at_spot(test_spot)
        ms = calculate_margin_at_spot(positions, test_spot, pricing_vol, r,
                                       margin_config, underlying, fund_rate_8h)
        return av - ms["mmr"]

    lo, hi = spot * 0.01, spot * 5.0
    f_lo, f_hi, f_spot = f(lo), f(hi), f(spot)
    liq_down = brent_root(f, lo, spot) if f_lo * f_spot < 0 else None
    liq_up = brent_root(f, spot, hi) if f_spot * f_hi < 0 else None

    if liq_down and liq_up:
        nearest = liq_down if (spot - liq_down) < (liq_up - spot) else liq_up
    else:
        nearest = liq_down or liq_up

    dist_pct = abs(spot - nearest) / spot * 100 if nearest else None
    return {"down": liq_down, "up": liq_up, "nearest": nearest, "dist_pct": dist_pct}


# ── Deribit CSV ────────────────────────────────────────────────────────────────

def load_deribit_csv(path: str, log=print) -> list[dict]:
    """
    Parse a Deribit historical data CSV.
    Expected columns: TIMESTAMP, SYMBOL, TYPE, STRIKE_PRICE, EXPIRATION,
                      MARK_IV, MARK_PRICE, UNDERLYING_PRICE,
                      DELTA, GAMMA, VEGA, THETA
    """
    log(f"  Loading Deribit CSV: {path}")
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                rows.append({
                    "t": int(datetime.fromisoformat(
                        row["TIMESTAMP"].strip().replace("Z", "+00:00")
                    ).timestamp() * 1000),
                    "symbol": row["SYMBOL"].strip(),
                    "type": row["TYPE"].strip().lower(),
                    "strike": float(row["STRIKE_PRICE"]),
                    "expiry": int(datetime.fromisoformat(
                        row["EXPIRATION"].strip().replace("Z", "+00:00")
                    ).timestamp() * 1000),
                    "mark_iv": float(row["MARK_IV"]) / 100.0,  # convert % → decimal
                    "mark_price_btc": float(row["MARK_PRICE"]),
                    "underlying": float(row["UNDERLYING_PRICE"]),
                    "delta": float(row["DELTA"]),
                    "gamma": float(row["GAMMA"]),
                    "vega": float(row["VEGA"]),
                    "theta": float(row["THETA"]),
                })
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda r: r["t"])
    log(f"  Loaded {len(rows):,} Deribit rows")
    return rows


def deribit_build_klines(
    deribit_rows: list[dict], start_ms: int, end_ms: int, log=print
) -> list[dict]:
    """Build hourly OHLC bars from Deribit underlying prices."""
    hour_ms = 3_600_000
    hour_buckets: dict[int, list[float]] = {}
    for r in deribit_rows:
        if r["t"] < start_ms or r["t"] > end_ms:
            continue
        hkey = r["t"] // hour_ms
        hour_buckets.setdefault(hkey, []).append(r["underlying"])

    bars: list[dict] = []
    cursor_ms = (math.ceil(start_ms / hour_ms)) * hour_ms
    while cursor_ms <= end_ms:
        hkey = cursor_ms // hour_ms
        prices = hour_buckets.get(hkey)
        if prices:
            bars.append({
                "t": cursor_ms,
                "o": prices[0],
                "h": max(prices),
                "l": min(prices),
                "c": prices[len(prices) // 2],
                "v": 0.0,
            })
        cursor_ms += hour_ms
    return bars


def deribit_build_iv_series(
    deribit_rows: list[dict],
    bars: list[dict],
    log=print,
    atm_term_days: int = 7,
) -> dict:
    """Build per-bar IV series from Deribit CSV, mirroring deribitBuildIVSeries() in JS."""
    MIN_DTE_DAYS = 7
    hour_ms = 3_600_000

    hour_buckets: dict[int, list[dict]] = {}
    for r in deribit_rows:
        hkey = r["t"] // hour_ms
        hour_buckets.setdefault(hkey, []).append(r)

    n = len(bars)
    iv_arr: list[float | None] = [None] * n
    greeks_arr: list[dict | None] = [None] * n
    atm_iv_arr: list[float | None] = [None] * n
    last_iv: float | None = None
    target_dte = atm_term_days

    for i, bar in enumerate(bars):
        spot = bar["c"]
        hkey = bar["t"] // hour_ms
        snaps = hour_buckets.get(hkey) or []

        # Best ATM call with >7 DTE
        best = None
        best_dist = float("inf")
        for s in snaps:
            if s["type"] != "call":
                continue
            dte = (s["expiry"] - bar["t"]) / (24 * 3_600_000)
            if dte < MIN_DTE_DAYS:
                continue
            dist = abs(s["strike"] - spot) / spot
            if dist < best_dist:
                best_dist = dist
                best = s
        if best and 0.01 < best["mark_iv"] < 5.0:
            iv_arr[i] = best["mark_iv"]
            greeks_arr[i] = {"delta": best["delta"], "gamma": best["gamma"],
                               "vega": best["vega"], "theta": best["theta"]}
            last_iv = best["mark_iv"]
        else:
            iv_arr[i] = last_iv

        # Constant-DTE ATM IV across expiries
        by_exp: dict[int, dict] = {}
        for s in snaps:
            if s["type"] != "call":
                continue
            if not s["mark_iv"] or not (0.01 < s["mark_iv"] < 5.0):
                continue
            dte = (s["expiry"] - bar["t"]) / (24 * 3_600_000)
            if dte < 1:
                continue
            dist = abs(s["strike"] - spot) / spot
            cur = by_exp.get(s["expiry"])
            if cur is None or dist < cur["dist"]:
                by_exp[s["expiry"]] = {"dte": dte, "iv": s["mark_iv"], "dist": dist}

        points = sorted(by_exp.values(), key=lambda p: p["dte"])
        if points:
            atm_iv_arr[i] = _interp_iv(points, target_dte)

    # Fill leading nulls
    first_known = next((v for v in iv_arr if v is not None), None)
    if first_known:
        for i in range(len(iv_arr)):
            if iv_arr[i] is None:
                iv_arr[i] = first_known
            else:
                break

    coverage = sum(1 for v in iv_arr if v is not None)
    atm_coverage = sum(1 for v in atm_iv_arr if v is not None)
    valid = [v for v in iv_arr if v is not None]
    mean_iv = sum(valid) / len(valid) if valid else None
    log(f"  Deribit IV: {coverage}/{n} bars, mean={mean_iv*100:.1f}%" if mean_iv else
        f"  Deribit IV: {coverage}/{n} bars")
    log(f"  Deribit ATM IV ({target_dte}d term): {atm_coverage}/{n} bars")

    return {"iv_arr": iv_arr, "greeks_arr": greeks_arr, "atm_iv_arr": atm_iv_arr,
            "mean_iv": mean_iv, "coverage": coverage}


# ── Backtest Engine ────────────────────────────────────────────────────────────

def run_engine(
    strategy: dict,
    bars: list[dict],
    iv_series: list[float | None] | None,
    fallback_iv: float | None,
    margin_config: dict | None,
    log=print,
    funding_index_series: list[dict] | None = None,
    atm_iv_series: list[float | None] | None = None,
) -> dict:
    """
    Main simulation loop. Mirrors runEngine() from strategy_backtester.html.
    Returns {equity, trades, metrics, error}.
    """
    capital = strategy["capital"]
    r = strategy.get("riskFreeRate", 0.05)
    s_legs = strategy["legs"]
    entry_cfg = strategy["entry"]
    exit_cfg = strategy["exit"]
    underlying = strategy["underlying"]
    tick = STRIKE_TICK.get(underlying, 100)
    has_option_legs = any(l["type"] == "option" for l in s_legs)
    has_perp_legs = any(l["type"] == "perp" for l in s_legs)
    has_funding_idx = bool(funding_index_series)

    def fund_rate_8h_at(t: int, spot: float) -> float:
        if not has_funding_idx or spot <= 0:
            return 0.0
        idx_now = funding_index_at(funding_index_series, t)
        idx_then = funding_index_at(funding_index_series, t - 8 * 3_600_000)
        if idx_now is None or idx_then is None:
            return 0.0
        return (idx_now - idx_then) / spot

    margin_mode = strategy.get("marginMode", "XM")
    if margin_config:
        margin_config["mode"] = margin_mode

    # Pre-compute indicators
    closes = [b["c"] for b in bars]
    rsi_arr = compute_rsi(closes, 14)
    sma_period = entry_cfg.get("sma", {}).get("period") or 168
    sma_arr = compute_sma(closes, sma_period)
    rv_window = min(entry_cfg.get("rvPctile", {}).get("window") or 720, 168)
    rv_arr = compute_realized_vol(closes, rv_window)
    rv_pctile_window = entry_cfg.get("rvPctile", {}).get("window") or 720

    warmup = 15
    if entry_cfg.get("sma", {}).get("enabled"):
        warmup = max(warmup, sma_period + 1)
    if entry_cfg.get("rvPctile", {}).get("enabled"):
        warmup = max(warmup, rv_window + 2)

    if len(bars) < warmup + 10:
        return {"equity": [], "trades": [], "metrics": None,
                "error": f"Not enough data bars ({len(bars)} < {warmup+10} needed)"}

    log(f"Warmup: {warmup} bars. First entry possible at bar {warmup}")

    cash = float(capital)
    equity: list[dict] = []
    open_positions: list[dict] = []
    trades: list[dict] = []
    bars_since_last_entry = entry_cfg.get("frequency", 24)

    for i, bar in enumerate(bars):
        spot = bar["c"]
        bar_iv = (iv_series[i] if iv_series else None) or fallback_iv or None
        pricing_vol = bar_iv or 0.5
        has_real_iv = bar_iv is not None

        # Mark-to-market open positions
        for pos in open_positions:
            pos["bars_held"] += 1
            if pos["leg_type"] == "option":
                dte_now = max(pos["dte_at_entry"] - pos["bars_held"] / 24, 0)
                pos["current_dte"] = dte_now
                if dte_now <= 0:
                    pos["current_price"] = max(
                        (spot - pos["strike"]) if pos["is_call"] else (pos["strike"] - spot), 0
                    )
                    pos["current_delta"] = (
                        (1.0 if spot > pos["strike"] else 0.0) if pos["is_call"]
                        else (-1.0 if spot < pos["strike"] else 0.0)
                    )
                elif has_real_iv:
                    T = dte_now / 365.0
                    pos["current_price"] = bs_price(spot, pos["strike"], T, r, pricing_vol, pos["is_call"])
                    pos["current_delta"] = bs_delta(spot, pos["strike"], T, r, pricing_vol, pos["is_call"])
            else:
                pos["current_price"] = spot

            price_diff = pos["current_price"] - pos["entry_price"]
            pos["unrealized_pnl"] = (price_diff if pos["side"] == "BUY" else -price_diff) * pos["size"]

            if pos["leg_type"] == "perp":
                if has_funding_idx and pos.get("entry_funding_index") is not None:
                    curr_idx = funding_index_at(funding_index_series, bar["t"])
                    if curr_idx is not None:
                        delta = curr_idx - pos["entry_funding_index"]
                        pos["funding_paid"] = (delta if pos["side"] == "BUY" else -delta) * pos["size"]
                else:
                    rate_8h = fund_rate_8h_at(bar["t"], spot)
                    per_bar = rate_8h / 8
                    cost = per_bar * spot * pos["size"]
                    pos["funding_paid"] += (cost if pos["side"] == "BUY" else -cost)

        # Delta hedging
        delta_hedge_cfg = strategy.get("deltaHedge") or {}
        if delta_hedge_cfg.get("enabled") and open_positions:
            net_option_delta = 0.0
            total_option_size = 0.0
            current_hedge_delta = 0.0
            existing_hedge = None
            for p in open_positions:
                sgn = 1.0 if p["side"] == "BUY" else -1.0
                if p["leg_type"] == "option":
                    net_option_delta += sgn * (p.get("current_delta") or 0) * p["size"]
                    total_option_size += abs(p["size"])
                elif p.get("is_hedge"):
                    current_hedge_delta += sgn * p["size"]
                    existing_hedge = p

            if total_option_size > 0:
                total_net_delta = net_option_delta + current_hedge_delta
                band = delta_hedge_cfg.get("band", 0.1)
                breach = abs(total_net_delta) / total_option_size > band
                if breach:
                    if existing_hedge:
                        pnl = existing_hedge["unrealized_pnl"] - existing_hedge["funding_paid"]
                        cash += pnl
                        trades.append({
                            "entry_time": existing_hedge["entry_time"],
                            "exit_time": bar["t"],
                            "exit_spot": spot,
                            "leg_type": "perp",
                            "side": existing_hedge["side"],
                            "option_type": None,
                            "strike": None,
                            "dte_at_entry": None,
                            "entry_price": existing_hedge["entry_price"],
                            "exit_price": spot,
                            "size": existing_hedge["size"],
                            "pnl": pnl,
                            "funding": existing_hedge["funding_paid"],
                            "reason": "REHEDGE",
                            "bars_held": existing_hedge["bars_held"],
                            "is_hedge": True,
                        })
                        open_positions = [p for p in open_positions if p is not existing_hedge]

                    target_size = abs(net_option_delta)
                    if target_size > 1e-4:
                        hedge = {
                            "leg_type": "perp",
                            "side": "SELL" if net_option_delta > 0 else "BUY",
                            "size": target_size,
                            "entry_time": bar["t"],
                            "entry_price": spot,
                            "current_price": spot,
                            "strike": None,
                            "is_call": None,
                            "dte_at_entry": None,
                            "current_dte": None,
                            "bars_held": 0,
                            "funding_paid": 0.0,
                            "unrealized_pnl": 0.0,
                            "current_delta": 0.0,
                            "is_hedge": True,
                            "entry_funding_index": (
                                funding_index_at(funding_index_series, bar["t"])
                                if has_funding_idx else None
                            ),
                        }
                        open_positions.append(hedge)

        # Margin + liquidation
        bar_dist_to_liq = None
        bar_liq_down = bar_liq_up = None
        bar_imr = bar_mmr = 0.0
        if margin_config and open_positions:
            fr8 = fund_rate_8h_at(bar["t"], spot)
            ms = calculate_margin_at_spot(open_positions, spot, pricing_vol, r,
                                           margin_config, underlying, fr8)
            bar_imr, bar_mmr = ms["imr"], ms["mmr"]
            liq = find_liquidation_price(cash, open_positions, spot, pricing_vol, r,
                                          margin_config, underlying, fr8)
            bar_liq_down, bar_liq_up = liq["down"], liq["up"]
            bar_dist_to_liq = liq["dist_pct"]

        # Exit checks
        exit_reason = None
        if open_positions:
            portfolio_pnl = sum(p["unrealized_pnl"] - p["funding_paid"] for p in open_positions)
            portfolio_entry_notional = sum(abs(p["entry_price"] * p["size"]) for p in open_positions)
            pnl_pct = (portfolio_pnl / portfolio_entry_notional * 100) if portfolio_entry_notional > 0 else 0.0

            exit_gates: list[dict] = []
            if exit_cfg.get("profitTarget", {}).get("enabled"):
                exit_gates.append({"reason": "TP", "triggered": pnl_pct >= exit_cfg["profitTarget"]["value"]})
            if exit_cfg.get("stopLoss", {}).get("enabled"):
                exit_gates.append({"reason": "SL", "triggered": pnl_pct <= -exit_cfg["stopLoss"]["value"]})
            iv_exit = exit_cfg.get("ivPctile") or {}
            if iv_exit.get("enabled") and atm_iv_series and atm_iv_series[i] is not None:
                win = iv_exit.get("window") or 720
                history = [v for v in (atm_iv_series[max(0, i - win):i]) if v is not None]
                if len(history) >= 5:
                    pctile = compute_iv_percentile(atm_iv_series[i], history)
                    triggered = (pctile > iv_exit["value"]) if iv_exit.get("op") == ">" else (pctile < iv_exit["value"])
                    exit_gates.append({"reason": "IVP", "triggered": triggered})
            dte_floor = exit_cfg.get("dteFloor") or {}
            if dte_floor.get("enabled"):
                triggered = any(
                    p["leg_type"] == "option" and p.get("current_dte", 999) <= dte_floor["value"]
                    for p in open_positions
                )
                exit_gates.append({"reason": "DTE", "triggered": triggered})
            max_hold = exit_cfg.get("maxHold") or {}
            if max_hold.get("enabled"):
                triggered = any(p["bars_held"] >= max_hold["value"] for p in open_positions)
                exit_gates.append({"reason": "MAX", "triggered": triggered})
            dtl_cfg = exit_cfg.get("distToLiq") or {}
            if dtl_cfg.get("enabled") and bar_dist_to_liq is not None:
                exit_gates.append({"reason": "DTL", "triggered": bar_dist_to_liq < dtl_cfg["value"]})

            if exit_gates:
                triggered = [g for g in exit_gates if g["triggered"]]
                if _gate_passes([g["triggered"] for g in exit_gates],
                                exit_cfg.get("gateMode") or "any",
                                exit_cfg.get("gateMin") or 1):
                    exit_reason = "+".join(g["reason"] for g in triggered)

            if not exit_reason:
                for pos in open_positions:
                    if pos["leg_type"] == "option" and pos.get("current_dte", 999) <= 0:
                        exit_reason = "EXPIRY"
                        break

        if exit_reason:
            for pos in open_positions:
                pnl = pos["unrealized_pnl"] - pos["funding_paid"]
                cash += pnl
                trades.append({
                    "entry_time": pos["entry_time"],
                    "exit_time": bar["t"],
                    "exit_spot": spot,
                    "leg_type": pos["leg_type"],
                    "side": pos["side"],
                    "option_type": "CALL" if pos.get("is_call") else ("PUT" if pos.get("is_call") is False else None),
                    "strike": pos.get("strike"),
                    "dte_at_entry": pos.get("dte_at_entry"),
                    "entry_price": pos["entry_price"],
                    "exit_price": pos["current_price"],
                    "size": pos["size"],
                    "pnl": pnl,
                    "funding": pos["funding_paid"],
                    "reason": exit_reason,
                    "bars_held": pos["bars_held"],
                    "is_hedge": pos.get("is_hedge", False),
                })
            open_positions = []

        # Entry checks
        bars_since_last_entry += 1
        if i >= warmup and not open_positions and bars_since_last_entry >= entry_cfg.get("frequency", 24):
            can_enter = True

            if can_enter and has_option_legs and not has_real_iv:
                can_enter = False

            gate_results: list[bool] = []
            rv_pctile_cfg = entry_cfg.get("rvPctile") or {}
            if rv_pctile_cfg.get("enabled") and rv_arr[i] is not None:
                history = [v for v in rv_arr[max(0, i - rv_pctile_window):i] if v is not None]
                pctile = compute_iv_percentile(rv_arr[i], history)
                gate_results.append(
                    pctile > rv_pctile_cfg["value"] if rv_pctile_cfg.get("op") == ">"
                    else pctile < rv_pctile_cfg["value"]
                )
            iv_entry_cfg = entry_cfg.get("ivPctile") or {}
            if iv_entry_cfg.get("enabled") and atm_iv_series and atm_iv_series[i] is not None:
                win = iv_entry_cfg.get("window") or 720
                history = [v for v in (atm_iv_series[max(0, i - win):i]) if v is not None]
                if len(history) >= 5:
                    pctile = compute_iv_percentile(atm_iv_series[i], history)
                    gate_results.append(
                        pctile > iv_entry_cfg["value"] if iv_entry_cfg.get("op") == ">"
                        else pctile < iv_entry_cfg["value"]
                    )
            rsi_cfg = entry_cfg.get("rsi") or {}
            if rsi_cfg.get("enabled") and rsi_arr[i] is not None:
                gate_results.append(
                    rsi_arr[i] < rsi_cfg["value"] if rsi_cfg.get("op") == "<"
                    else rsi_arr[i] > rsi_cfg["value"]
                )
            sma_cfg = entry_cfg.get("sma") or {}
            if sma_cfg.get("enabled") and sma_arr[i] is not None:
                gate_results.append(
                    spot > sma_arr[i] if sma_cfg.get("op") == "above" else spot < sma_arr[i]
                )
            fr_cfg = entry_cfg.get("fundingRate") or {}
            if fr_cfg.get("enabled") and has_funding_idx:
                fr = fund_rate_8h_at(bar["t"], spot)
                if math.isfinite(fr):
                    fr_pct = fr * 100
                    gate_results.append(
                        fr_pct > fr_cfg["value"] if fr_cfg.get("op") == ">"
                        else fr_pct < fr_cfg["value"]
                    )

            if can_enter and gate_results:
                can_enter = _gate_passes(gate_results,
                                         entry_cfg.get("gateMode") or "all",
                                         entry_cfg.get("gateMin") or 1)

            if can_enter:
                pre_entry_cash = cash
                new_positions: list[dict] = []
                for leg in s_legs:
                    pos: dict = {
                        "leg_type": leg["type"],
                        "side": leg["side"],
                        "size": (cash * leg["size"] / 100 / spot
                                 if leg.get("sizeMode") == "pct_capital"
                                 else float(leg["size"])),
                        "entry_time": bar["t"],
                        "bars_held": 0,
                        "funding_paid": 0.0,
                        "unrealized_pnl": 0.0,
                        "current_price": 0.0,
                        "current_dte": 0.0,
                        "current_delta": 0.0,
                    }
                    if leg["type"] == "option":
                        is_call = leg["optionType"] == "CALL"
                        dte = float(leg["dteTarget"])
                        T = dte / 365.0
                        strike_mode = leg.get("strikeMode", "delta")
                        strike_param = float(leg["strikeParam"])
                        if strike_mode == "atm":
                            strike = round(spot / tick) * tick
                        elif strike_mode == "delta":
                            target_delta = abs(strike_param) if is_call else -abs(strike_param)
                            strike = find_strike_by_delta(spot, T, r, pricing_vol, target_delta, is_call, tick)
                        else:  # otm_pct
                            offset = spot * abs(strike_param) / 100
                            raw = (spot + offset) if is_call else (spot - offset)
                            strike = round(raw / tick) * tick
                        price = bs_price(spot, strike, T, r, pricing_vol, is_call)
                        pos.update({
                            "strike": strike,
                            "is_call": is_call,
                            "dte_at_entry": dte,
                            "current_dte": dte,
                            "entry_price": price,
                            "current_price": price,
                            "current_delta": bs_delta(spot, strike, T, r, pricing_vol, is_call),
                        })
                    else:  # perp
                        pos.update({
                            "entry_price": spot,
                            "current_price": spot,
                            "strike": None,
                            "is_call": None,
                            "dte_at_entry": None,
                            "current_dte": None,
                            "entry_funding_index": (
                                funding_index_at(funding_index_series, bar["t"])
                                if has_funding_idx else None
                            ),
                        })
                    new_positions.append(pos)

                # IMR-at-entry filter
                accepted = True
                max_imr_pct = strategy.get("maxImrPctEntry")
                if margin_config and isinstance(max_imr_pct, (int, float)) and 0 < max_imr_pct < 100:
                    fr8 = fund_rate_8h_at(bar["t"], spot)
                    ms = calculate_margin_at_spot(new_positions, spot, pricing_vol, r,
                                                   margin_config, underlying, fr8)
                    new_mtm = sum(p["unrealized_pnl"] - p["funding_paid"] for p in new_positions)
                    equity_at_entry = cash + new_mtm
                    imr_pct = (ms["imr"] / equity_at_entry * 100) if equity_at_entry > 0 else 1e9
                    if imr_pct > max_imr_pct:
                        cash = pre_entry_cash
                        accepted = False
                        bars_since_last_entry = 0
                        ts = datetime.fromtimestamp(bar["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                        log(f"  Entry skipped at {ts} UTC: IMR {imr_pct:.1f}% > cap {max_imr_pct}%")

                if accepted:
                    bars_since_last_entry = 0
                    open_positions.extend(new_positions)

        # Recompute margin after entry
        if margin_config and open_positions and bar_dist_to_liq is None:
            fr8 = fund_rate_8h_at(bar["t"], spot)
            ms = calculate_margin_at_spot(open_positions, spot, pricing_vol, r,
                                           margin_config, underlying, fr8)
            bar_imr, bar_mmr = ms["imr"], ms["mmr"]
            liq = find_liquidation_price(cash, open_positions, spot, pricing_vol, r,
                                          margin_config, underlying, fr8)
            bar_liq_down, bar_liq_up = liq["down"], liq["up"]
            bar_dist_to_liq = liq["dist_pct"]

        total_equity = cash + sum(p["unrealized_pnl"] - p["funding_paid"] for p in open_positions)
        equity.append({
            "t": bar["t"],
            "equity": total_equity,
            "spot": spot,
            "cash": cash,
            "has_positions": bool(open_positions),
            "imr": bar_imr,
            "mmr": bar_mmr,
            "dist_to_liq": bar_dist_to_liq,
            "liq_down": bar_liq_down,
            "liq_up": bar_liq_up,
        })

    metrics = _compute_metrics(equity, trades, capital)
    return {"equity": equity, "trades": trades, "metrics": metrics, "error": None}


def _compute_metrics(equity: list[dict], trades: list[dict], capital: float) -> dict:
    if not equity:
        return {}

    final_equity = equity[-1]["equity"]
    total_pnl = final_equity - capital
    total_return = total_pnl / capital * 100

    # Hourly returns for Sharpe
    equities = [e["equity"] for e in equity]
    rets = [(equities[i] - equities[i - 1]) / equities[i - 1] for i in range(1, len(equities))]
    mean_ret = sum(rets) / len(rets) if rets else 0
    var_ret = sum((x - mean_ret) ** 2 for x in rets) / len(rets) if rets else 0
    std_ret = math.sqrt(var_ret)
    sharpe = (mean_ret / std_ret * math.sqrt(HOURS_PER_YEAR)) if std_ret > 0 else 0.0

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    for v in equities:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win/loss from trade cycles (group by entry time)
    cycles: dict[int, dict] = {}
    for t in trades:
        if t.get("is_hedge"):
            continue
        key = t["entry_time"]
        if key not in cycles:
            cycles[key] = {"pnl": 0.0, "reason": t["reason"]}
        cycles[key]["pnl"] += t["pnl"]
    cycle_list = list(cycles.values())
    wins = [c["pnl"] for c in cycle_list if c["pnl"] > 0]
    losses = [c["pnl"] for c in cycle_list if c["pnl"] <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = len(wins) / len(cycle_list) * 100 if cycle_list else 0.0

    # DTL stats
    dtl_bars = [e["dist_to_liq"] for e in equity if e.get("dist_to_liq") is not None]
    min_dtl = min(dtl_bars) if dtl_bars else None
    avg_dtl = sum(dtl_bars) / len(dtl_bars) if dtl_bars else None

    # Holding time
    holding_bars = sum(1 for e in equity if e["has_positions"])
    holding_pct = holding_bars / len(equity) * 100 if equity else 0.0

    return {
        "total_pnl": total_pnl,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_dd": max_dd * 100,
        "win_rate": win_rate,
        "num_trades": len(cycle_list),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "final_equity": final_equity,
        "min_dtl": min_dtl,
        "avg_dtl": avg_dtl,
        "holding_bars": holding_bars,
        "holding_pct": holding_pct,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paradex Strategy Backtester — CLI port of strategy_backtester.html"
    )
    parser.add_argument("strategy", help="Strategy JSON file (or - for stdin)")
    parser.add_argument("--start", help="Backtest start date YYYY-MM-DD (overrides JSON)")
    parser.add_argument("--end", help="Backtest end date YYYY-MM-DD (overrides JSON)")
    parser.add_argument("--deribit", metavar="CSV", help="Deribit historical data CSV file")
    parser.add_argument("--output", metavar="FILE", help="Save full results JSON to FILE")
    parser.add_argument("--testnet", action="store_true", help="Use Paradex testnet API")
    parser.add_argument("--no-margin", action="store_true", help="Skip margin/liquidation computation (faster)")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress messages")
    parser.add_argument("--timeout", type=int, metavar="SECONDS",
                        help="Hard wall-clock timeout; exits with error if exceeded")
    parser.add_argument("--cache-dir", metavar="DIR",
                        help="Directory to cache fetched data for reuse across runs "
                             "(e.g. ~/.paradex_cache). Historical data is kept indefinitely; "
                             "margin config is refreshed after 24h.")
    args = parser.parse_args()

    log = (lambda _: None) if args.quiet else print

    # Load strategy
    if args.strategy == "-":
        strategy = json.load(sys.stdin)
    else:
        with open(args.strategy) as fh:
            strategy = json.load(fh)

    # Date range
    bt = strategy.get("backtest") or {}
    start_str = args.start or bt.get("startDate")
    end_str = args.end or bt.get("endDate")
    if not start_str or not end_str:
        print("Error: provide --start and --end or set 'backtest.startDate/endDate' in the strategy JSON")
        sys.exit(1)

    start_ms = int(datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc).timestamp() * 1000)

    underlying = strategy["underlying"]
    client = _make_client(args.testnet)
    cache = _DataCache(args.cache_dir)

    if args.timeout:
        _start_timeout(args.timeout)

    log(f"Strategy: {strategy.get('name', 'Unnamed')}")
    log(f"Underlying: {underlying} | Capital: ${strategy['capital']:,.0f} | "
        f"Mode: {strategy.get('marginMode', 'XM')}")
    log(f"Window: {start_str} → {end_str}")

    iv_series = atm_iv_series = None
    funding_index_series = None
    margin_config = None
    fallback_iv = None

    if args.deribit:
        # Deribit CSV path
        log("\n[Deribit] Loading CSV...")
        deribit_rows = load_deribit_csv(args.deribit, log=log)

        log("[Deribit] Building hourly klines from underlying prices...")
        bars = deribit_build_klines(deribit_rows, start_ms, end_ms, log=log)
        log(f"  {len(bars)} hourly bars")

        log("[Deribit] Building IV series...")
        iv_data = deribit_build_iv_series(deribit_rows, bars, log=log,
                                           atm_term_days=strategy.get("atmIvTermDays", 7))
        iv_series = iv_data["iv_arr"]
        atm_iv_series = iv_data["atm_iv_arr"]
        fallback_iv = iv_data.get("mean_iv")

        # Margin always from Paradex even for Deribit runs
        if not args.no_margin:
            log("\n[Paradex] Fetching margin config...")
            margin_config = fetch_margin_config(client, underlying, log=log, cache=cache)
            margin_config["mode"] = strategy.get("marginMode", "XM")

    else:
        # Paradex path
        log(f"\n[Paradex] Fetching klines{'  [testnet]' if args.testnet else ''}...")
        perp_market = f"{underlying}-USD-PERP"
        bars = fetch_klines(client, perp_market, start_ms, end_ms, log=log, cache=cache)
        log(f"  {len(bars)} hourly bars")

        has_option_legs = any(l["type"] == "option" for l in strategy["legs"])
        if has_option_legs:
            log("[Paradex] Fetching historical IV from option markets/summary...")
            iv_data = fetch_historical_iv(
                client, underlying, bars,
                strategy.get("riskFreeRate", 0.05), log=log,
                atm_term_days=strategy.get("atmIvTermDays", 7),
                cache=cache,
            )
            if iv_data:
                iv_series = iv_data["iv_arr"]
                atm_iv_series = iv_data["atm_iv_arr"]
                fallback_iv = iv_data.get("mean_iv")

                # Trim to IV coverage window
                first_real = next((i for i, v in enumerate(iv_series) if v is not None), None)
                last_real = next((i for i, v in reversed(list(enumerate(iv_series))) if v is not None), None)
                if first_real is not None and last_real is not None:
                    if first_real > 0 or last_real < len(bars) - 1:
                        log(f"  Trimming to IV coverage: bars {first_real}→{last_real}")
                        bars = bars[first_real:last_real + 1]
                        iv_series = iv_series[first_real:last_real + 1]
                        atm_iv_series = (atm_iv_series[first_real:last_real + 1]
                                         if atm_iv_series else None)

        has_perp_legs = any(l["type"] == "perp" for l in strategy["legs"])
        if has_perp_legs or strategy.get("deltaHedge", {}).get("enabled"):
            log("[Paradex] Fetching funding index...")
            funding_index_series = fetch_funding_index(
                client, perp_market, start_ms, end_ms, log=log, cache=cache,
            )
            log(f"  {len(funding_index_series)} funding index points")

        if not args.no_margin:
            log("[Paradex] Fetching margin config...")
            margin_config = fetch_margin_config(client, underlying, log=log, cache=cache)
            margin_config["mode"] = strategy.get("marginMode", "XM")

    if not bars:
        print("Error: no price data fetched. Check date range and data source.")
        sys.exit(1)

    log(f"\nRunning simulation on {len(bars)} bars...")
    t0 = time.time()
    results = run_engine(
        strategy, bars, iv_series, fallback_iv, margin_config,
        log=log, funding_index_series=funding_index_series, atm_iv_series=atm_iv_series,
    )
    elapsed = time.time() - t0

    if results.get("error"):
        print(f"Engine error: {results['error']}")
        sys.exit(1)

    mx = results["metrics"]
    log(f"\nSimulation complete in {elapsed:.1f}s")

    # Print summary
    print("\n" + "=" * 60)
    print(f"  {strategy.get('name', 'Strategy')} — Backtest Results")
    print("=" * 60)
    print(f"  Total P&L:       ${mx['total_pnl']:,.0f}  ({mx['total_return']:.1f}%)")
    print(f"  Final Equity:    ${mx['final_equity']:,.0f}")
    print(f"  Sharpe:          {mx['sharpe']:.2f}")
    print(f"  Max Drawdown:    {mx['max_dd']:.1f}%")
    print(f"  Win Rate:        {mx['win_rate']:.1f}%  ({mx['num_trades']} cycles)")
    print(f"  Avg Win:         ${mx['avg_win']:,.0f}")
    print(f"  Avg Loss:        ${mx['avg_loss']:,.0f}")
    print(f"  Holding:         {mx['holding_pct']:.1f}% of bars")
    if mx.get("min_dtl") is not None:
        print(f"  Min Dist-to-Liq: {mx['min_dtl']:.1f}%")
    print("=" * 60)

    # Trade log
    non_hedge_trades = [t for t in results["trades"] if not t.get("is_hedge")]
    if non_hedge_trades:
        print(f"\nTrade log ({len(non_hedge_trades)} legs):")
        print(f"  {'Entry':<17} {'Exit':<17} {'Leg':<8} {'Side':<5} {'Strike':<8} {'DTE':<5} {'P&L':>10}  Reason")
        for t in non_hedge_trades[-50:]:  # show last 50 to keep output bounded
            entry_ts = datetime.fromtimestamp(t["entry_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            exit_ts = datetime.fromtimestamp(t["exit_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            strike_str = f"{t['strike']:,.0f}" if t.get("strike") else "-"
            dte_str = str(int(t["dte_at_entry"])) if t.get("dte_at_entry") else "-"
            leg = t["leg_type"]
            if leg == "option":
                leg = "C" if t.get("option_type") == "CALL" else "P"
            print(f"  {entry_ts:<17} {exit_ts:<17} {leg:<8} {t['side']:<5} {strike_str:<8} {dte_str:<5} ${t['pnl']:>9,.0f}  {t['reason']}")

    if args.output:
        # Serialise — replace None with null-safe dicts
        out = {
            "strategy": strategy,
            "window": {"start": start_str, "end": end_str},
            "metrics": mx,
            "trades": results["trades"],
            "equity_curve": results["equity"],
        }
        with open(args.output, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
