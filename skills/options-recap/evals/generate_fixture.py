#!/usr/bin/env python3
"""
Generate a fixture for options-recap evals.

Fetches real Deribit data for a past window, saves the raw snapshot to
fixtures/<asset>_<window>_<date>.json, and prints the ground-truth values
to embed in evals.json assertions.

Usage:
    python3 generate_fixture.py                      # BTC, last 8h
    python3 generate_fixture.py --asset eth --window 4h
    python3 generate_fixture.py --start 2026-06-04T10:00:00Z --end 2026-06-04T18:00:00Z

Output:
    fixtures/<asset>_<window>_<YYYY-MM-DD>.json   — raw API snapshot
    Ground truth printed to stdout                 — paste into evals.json assertions
"""

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Shared vol math — single source of truth for the formulas the agent must not
# do by hand. The production CLI (../scripts/paradex_options_recap.py) imports
# the same module, so the fixture and live runs can never diverge.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from vol_math import (  # noqa: E402
    compute_realized_vol,
    realized_vs_implied,
    compute_flow_greeks,
    compute_vol_surface,
    classify_structure,
    dominant_side,
    summarize_blocks,
    RV_LOOKBACK_DAYS,
)

DERIBIT = "https://www.deribit.com/api/v2/public"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def fetch(path: str, params: dict) -> dict:
    from urllib.parse import urlencode
    qs = urlencode(params)
    url = f"{DERIBIT}/{path}?{qs}"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    if "error" in data:
        raise RuntimeError(f"Deribit error for {path}: {data['error']}")
    return data["result"]


def parse_window(window: str) -> int:
    """Return window duration in milliseconds."""
    units = {"h": 3600_000, "d": 86400_000}
    unit = window[-1].lower()
    if unit not in units:
        raise ValueError(f"Unknown window unit '{unit}' — use h or d (e.g. 8h, 1d)")
    return int(window[:-1]) * units[unit]



def compute_ground_truth(snapshot: dict) -> dict:
    """Derive expected values from raw API snapshot for use in assertions."""
    asset = snapshot["asset"].upper()

    # DVOL
    dvol_data = snapshot["dvol"]
    if dvol_data:
        dvol_open = dvol_data[0][1]   # open of first candle
        dvol_close = dvol_data[-1][4]  # close of last candle
        dvol_low = min(row[3] for row in dvol_data)
        dvol_high = max(row[2] for row in dvol_data)
    else:
        dvol_open = dvol_close = dvol_low = dvol_high = None

    # Spot — get_tradingview_chart_data returns dict with open/high/low/close arrays
    spot_data = snapshot.get("spot", {})
    if spot_data and spot_data.get("close"):
        spot_low = min(spot_data["low"])
        spot_high = max(spot_data["high"])
        spot_close = spot_data["close"][-1]
    else:
        spot_low = spot_high = spot_close = None

    # Block trades
    trades = snapshot["trades"]
    clusters: dict[str, list] = defaultdict(list)
    screen_trades = []
    for t in trades:
        bid = t.get("block_trade_id")
        if bid:
            clusters[bid].append(t)
        else:
            screen_trades.append(t)

    # Top blocks by notional — shared with the production CLI via vol_math.
    top_blocks = summarize_blocks(clusters)

    # Screen flow: group by (expiry, strike, type, direction)
    screen_groups: dict[tuple, list] = defaultdict(list)
    for t in screen_trades:
        parts = t["instrument_name"].split("-")
        if len(parts) < 4:
            continue
        key = (parts[1], parts[2], parts[3], t["direction"])
        screen_groups[key].append(t)

    screen_themes = []
    for (expiry, strike, opt_type, direction), ts_list in sorted(
        screen_groups.items(), key=lambda x: -sum(t["amount"] for t in x[1])
    )[:5]:
        total = sum(t["amount"] for t in ts_list)
        avg_iv = sum(t["iv"] for t in ts_list) / len(ts_list)
        screen_themes.append({
            "expiry": expiry,
            "strike": strike,
            "type": opt_type,
            "direction": direction,
            "total_btc": round(total, 1),
            "clips": len(ts_list),
            "avg_iv": round(avg_iv, 1),
        })

    # Vol surface from tickers
    tickers = snapshot.get("tickers", {})
    surface = {}
    for inst_name, ticker in tickers.items():
        if ticker and "mark_iv" in ticker:
            surface[inst_name] = round(ticker["mark_iv"], 1)

    # Realized vol (#1) — from the trailing 7d spot history, vs DVOL (implied)
    rv = snapshot.get("realized_vol") or {}
    rv_value = rv.get("annualized_vol")
    vrp = None          # vol risk premium: implied − realized
    vrp_label = None
    if rv_value is not None and dvol_close is not None:
        vrp = round(dvol_close - rv_value, 1)
        if vrp > 1:
            vrp_label = "implied rich vs realized — vol overpriced vs delivered"
        elif vrp < -1:
            vrp_label = "implied cheap vs realized — vol underpriced vs delivered"
        else:
            vrp_label = "implied roughly in line with realized"

    # Flow greeks (#2) — net dealer positioning across all block legs
    flow_greeks = compute_flow_greeks(clusters)

    # Vol surface metrics (#3) — ATM / 25Δ RR / fly / term structure
    vol_surface_metrics = compute_vol_surface(
        snapshot.get("tickers", {}), snapshot.get("spot_price_at_fetch"))

    # Derive spot-vol relationship label
    spot_vol_label = None
    if dvol_open and dvol_close and spot_low and spot_high and spot_close:
        spot_up = spot_close > (spot_low + spot_high) / 2
        vol_up = dvol_close > dvol_open
        if spot_up and not vol_up:
            spot_vol_label = "vol sold through a rally"
        elif not spot_up and vol_up:
            spot_vol_label = "vol bid into weakness"
        elif spot_up and vol_up:
            spot_vol_label = "vol bought through a rally"
        else:
            spot_vol_label = "vol faded with spot"

    return {
        "dvol": {
            "open": dvol_open,
            "close": dvol_close,
            "low": dvol_low,
            "high": dvol_high,
            "change": round(dvol_close - dvol_open, 1) if dvol_open and dvol_close else None,
        },
        "spot": {
            "low": round(spot_low) if spot_low else None,
            "high": round(spot_high) if spot_high else None,
            "close": round(spot_close) if spot_close else None,
        },
        "realized_vol": {
            "value": rv_value,
            "lookback_days": rv.get("lookback_days"),
            "vrp": vrp,
            "vrp_label": vrp_label,
        },
        "flow_greeks": flow_greeks,
        "vol_surface_metrics": vol_surface_metrics,
        "spot_vol_label": spot_vol_label,
        "top_blocks": top_blocks,
        "screen_themes": screen_themes,
        "vol_surface": surface,
        "trade_counts": {
            "total": len(trades),
            "blocks": sum(len(v) for v in clusters.values()),
            "block_clusters": len(clusters),
            "screen": len(screen_trades),
        },
    }


def fetch_vol_surface(asset: str, spot_price: float) -> dict[str, dict]:
    """Fetch tickers for key strikes around spot for front two expiries."""
    currency = asset.upper()
    instruments = fetch("get_instruments", {"currency": currency, "kind": "option", "expired": "false"})

    expiries = sorted(set(i["expiration_timestamp"] for i in instruments))
    front_expiries = expiries[:2]

    # Find strikes near spot (ATM ±4 strikes) for each front expiry. ±4 (not
    # ±2) so the 25-delta wings are bracketed and the surface skew/fly metrics
    # interpolate rather than extrapolate.
    tickers = {}
    for exp_ms in front_expiries:
        exp_insts = [i for i in instruments if i["expiration_timestamp"] == exp_ms]
        strikes = sorted(set(int(i["instrument_name"].split("-")[2]) for i in exp_insts))
        # Find ATM strike index
        atm_strike = min(strikes, key=lambda s: abs(s - spot_price))
        atm_idx = strikes.index(atm_strike)
        # Take ATM ± 4 strikes for both C and P
        selected_strikes = strikes[max(0, atm_idx - 4): atm_idx + 5]
        # Build a lookup of actual instrument names from the exchange for this expiry+strike+type
        inst_lookup = {
            (int(i["instrument_name"].split("-")[2]), i["instrument_name"].split("-")[3]): i["instrument_name"]
            for i in exp_insts
        }
        for strike in selected_strikes:
            for opt_type in ("C", "P"):
                inst_name = inst_lookup.get((strike, opt_type))
                if not inst_name:
                    continue
                try:
                    result = fetch("ticker", {"instrument_name": inst_name})
                    greeks = result.get("greeks", {})
                    tickers[inst_name] = {
                        "mark_iv": result.get("mark_iv"),
                        "bid_iv": result.get("bid_iv"),
                        "ask_iv": result.get("ask_iv"),
                        "delta": greeks.get("delta"),
                        "vega": greeks.get("vega"),
                        "gamma": greeks.get("gamma"),
                    }
                except Exception:
                    pass

    return tickers


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate options-recap eval fixture")
    parser.add_argument("--asset", default="btc", choices=["btc", "eth"])
    parser.add_argument("--window", default="8h", help="Window size, e.g. 8h, 4h, 1d")
    parser.add_argument("--start", help="ISO8601 start (overrides --window), e.g. 2026-06-04T10:00:00Z")
    parser.add_argument("--end", help="ISO8601 end (overrides --window)")
    args = parser.parse_args()

    now_ms = int(time.time() * 1000)
    if args.start and args.end:
        start_ms = int(datetime.fromisoformat(args.start.replace("Z", "+00:00")).timestamp() * 1000)
        end_ms = int(datetime.fromisoformat(args.end.replace("Z", "+00:00")).timestamp() * 1000)
    else:
        window_ms = parse_window(args.window)
        end_ms = now_ms
        start_ms = end_ms - window_ms

    asset = args.asset.upper()
    window_label = args.window if not args.start else "custom"
    date_label = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    fixture_name = f"{args.asset}_{window_label}_{date_label}.json"
    fixture_path = FIXTURES_DIR / fixture_name

    print(f"Fetching {asset} options data {datetime.fromtimestamp(start_ms/1000, tz=timezone.utc).isoformat()} → {datetime.fromtimestamp(end_ms/1000, tz=timezone.utc).isoformat()}")

    # Fetch DVOL
    print("  • DVOL...")
    dvol = fetch("get_volatility_index_data", {
        "currency": asset,
        "resolution": "3600",
        "start_timestamp": start_ms,
        "end_timestamp": end_ms,
    })["data"]

    # Fetch spot OHLCV via perpetual klines
    print("  • Spot (perpetual klines)...")
    perp_name = f"{asset}-PERPETUAL"
    spot_ohlcv = fetch("get_tradingview_chart_data", {
        "instrument_name": perp_name,
        "resolution": "60",
        "start_timestamp": start_ms,
        "end_timestamp": end_ms,
    })
    spot_price = spot_ohlcv["close"][-1] if spot_ohlcv.get("close") else None

    # Fetch trailing 7d spot for realized vol (#1) — a longer, fixed lookback
    # than the recap window: RV-vs-implied is a slow statistic and needs a
    # stable sample, not the 8h window (which would annualize one trending
    # afternoon into noise).
    print(f"  • Spot {RV_LOOKBACK_DAYS}d history (realized vol)...")
    rv_start_ms = end_ms - RV_LOOKBACK_DAYS * 86400_000
    rv_ohlcv = fetch("get_tradingview_chart_data", {
        "instrument_name": perp_name,
        "resolution": "60",
        "start_timestamp": rv_start_ms,
        "end_timestamp": end_ms,
    })
    realized_vol = compute_realized_vol(rv_ohlcv.get("close") or [])

    # Fetch trades (Deribit paginates at 500 — fetch up to 1000)
    print("  • Trades (page 1)...")
    page1 = fetch("get_last_trades_by_currency", {
        "currency": asset,
        "kind": "option",
        "count": 500,
        "start_timestamp": start_ms,
        "end_timestamp": end_ms,
        "sorting": "desc",
    })
    trades = page1["trades"]
    if page1.get("has_more") and trades:
        print("  • Trades (page 2)...")
        oldest_ts = min(t["timestamp"] for t in trades)
        page2 = fetch("get_last_trades_by_currency", {
            "currency": asset,
            "kind": "option",
            "count": 500,
            "start_timestamp": start_ms,
            "end_timestamp": oldest_ts - 1,
            "sorting": "desc",
        })
        trades += page2["trades"]

    # Fetch vol surface tickers
    print("  • Vol surface tickers...")
    tickers = fetch_vol_surface(args.asset, spot_price)

    snapshot = {
        "asset": args.asset,
        "window": window_label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "fetched_at_ms": now_ms,
        "dvol": dvol,
        "spot": spot_ohlcv,
        "spot_price_at_fetch": spot_price,
        "realized_vol": realized_vol,
        "trades": trades,
        "tickers": tickers,
    }

    # Compute ground truth first so we can embed the pre-computed reads
    # (realized-vol VRP, flow greeks, vol surface) into the saved fixture.
    # These are the values the LLM should READ in evals rather than recompute —
    # Black-76, stdev/annualization, and delta-interpolation are exactly the
    # math LLMs hallucinate.
    gt = compute_ground_truth(snapshot)
    snapshot["derived"] = {
        "note": "Pre-computed reads — use these directly; do not recompute.",
        "realized_vol": gt["realized_vol"],
        "flow_greeks": gt["flow_greeks"],
        "top_blocks": gt["top_blocks"],
        "vol_surface": gt["vol_surface_metrics"],
    }

    FIXTURES_DIR.mkdir(exist_ok=True)
    fixture_path.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSaved fixture → {fixture_path.relative_to(Path(__file__).parent.parent.parent.parent)}")

    # Print ground truth
    print("\n" + "=" * 60)
    print("GROUND TRUTH (paste into evals.json assertions)")
    print("=" * 60)

    print(f"\nFixture file: evals/fixtures/{fixture_name}")
    print(f"Asset: {asset}, Window: {window_label}")

    print(f"\n--- DVOL ---")
    d = gt["dvol"]
    print(f"  open:   {d['open']}v")
    print(f"  close:  {d['close']}v")
    print(f"  change: {d['change']:+}v" if d["change"] else "  change: n/a")
    print(f"  range:  {d['low']}v – {d['high']}v")

    print(f"\n--- Spot ---")
    s = gt["spot"]
    print(f"  low:    ${s['low']:,}")
    print(f"  high:   ${s['high']:,}")
    print(f"  close:  ${s['close']:,}")
    print(f"  label:  {gt['spot_vol_label']}")

    print(f"\n--- Realized vol vs implied (#1) ---")
    rv = gt["realized_vol"]
    if rv["value"] is not None:
        print(f"  RV ({rv['lookback_days']}d):  {rv['value']}v")
        print(f"  DVOL:      {gt['dvol']['close']}v")
        print(f"  VRP:       {rv['vrp']:+}v  →  {rv['vrp_label']}")
    else:
        print("  (insufficient spot history)")

    print(f"\n--- Flow greeks / dealer positioning (#2) ---")
    fg = gt["flow_greeks"]
    print(f"  net customer vega:        ${fg['net_customer_vega']:,}/vol-pt")
    print(f"  net customer $gamma:      ${fg['net_customer_dollar_gamma']:,}/1% move")
    print(f"  → {fg['positioning_label']}")

    print(f"\n--- Trade counts ---")
    tc = gt["trade_counts"]
    print(f"  total: {tc['total']}, block legs: {tc['blocks']}, clusters: {tc['block_clusters']}, screen: {tc['screen']}")

    print(f"\n--- Top blocks (≥10 BTC notional) ---")
    if gt["top_blocks"]:
        for b in gt["top_blocks"]:
            print(f"  {b['time_utc']} | {b['structure']:<14} | {b['size_btc']}x {asset} | {b['side']:<8} | {b['expiry']} {b['strike']} | avg_iv={b['avg_iv']}v | notional=${b['notional_usd']:,}")
    else:
        print("  (no blocks ≥10 BTC)")

    print(f"\n--- Screen flow themes (top 5 by volume) ---")
    for t in gt["screen_themes"]:
        direction = "buy" if t["direction"] == "buy" else "sell"
        print(f"  {t['expiry']} {t['strike']}{t['type']} {direction}: {t['total_btc']}x in {t['clips']} clips @ avg {t['avg_iv']}v")

    print(f"\n--- Vol surface (mark IV) ---")
    for inst, iv in sorted(gt["vol_surface"].items()):
        print(f"  {inst}: {iv}v")

    print(f"\n--- Vol surface metrics (#3) ---")
    vs = gt["vol_surface_metrics"]
    for e in vs["expiries"]:
        flag = " [wings extrapolated]" if e["wings_extrapolated"] else ""
        print(f"  {e['expiry']}: ATM {e['atm_iv']}v · 25Δ RR {e['rr_25d']}v · fly {e['fly_25d']}v{flag}")
    print(f"  term structure: {vs['term_structure']}")
    print(f"  skew: {vs['skew_label']}")

    print("\n--- Suggested assertions ---")
    d = gt["dvol"]
    if d["open"]:
        tol = 1.0
        print(f'  "DVOL open is reported as approximately {d["open"]}v (within ±{tol}v)"')
        print(f'  "DVOL close is reported as approximately {d["close"]}v (within ±{tol}v)"')
        change_dir = "decrease" if d["change"] < 0 else "increase"
        print(f'  "DVOL shows a {change_dir} of approximately {abs(d["change"])}v over the window"')
    if gt["spot_vol_label"]:
        label = gt["spot_vol_label"]
        print(f'  "Response includes spot-vol relationship label consistent with: {label}"')
    rv = gt["realized_vol"]
    if rv["value"] is not None:
        print(f'  "Realized vol ({rv["lookback_days"]}d) is reported near {rv["value"]}v (within ±3v)"')
        print(f'  "Recap reads the vol risk premium as: {rv["vrp_label"]}"')
    fg = gt["flow_greeks"]
    print(f'  "Net dealer positioning is read as: {fg["positioning_label"]}"')
    vs = gt["vol_surface_metrics"]
    if vs["front_atm"] is not None:
        print(f'  "Front-expiry ATM IV is reported near {vs["front_atm"]}v (within ±3v)"')
    if vs["skew_label"]:
        print(f'  "Vol surface skew read matches: {vs["skew_label"]}"')
    if vs["term_structure"]:
        print(f'  "Term structure is described as: {vs["term_structure"]}"')
    if gt["top_blocks"]:
        b = gt["top_blocks"][0]
        structure = b["structure"]
        size = b["size_btc"]
        side = b["side"]
        avg_iv = b["avg_iv"]
        print(f'  "Largest block is identified as a {structure} ({size}x BTC, {side})"')
        print(f'  "Largest block IV is reported near {avg_iv}v (within ±3v)"')
    if gt["screen_themes"]:
        t = gt["screen_themes"][0]
        expiry = t["expiry"]
        strike = t["strike"]
        opt_type = t["type"]
        print(f'  "Screen flow mentions {expiry} {strike}{opt_type} as a notable theme"')


if __name__ == "__main__":
    main()
