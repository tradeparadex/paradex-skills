#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
paradex_options_recap.py — deterministic vol math for the options recap.

Computes realized-vs-implied vol and net dealer positioning (flow greeks) from
a pre-fetched Deribit snapshot, so the agent never does the arithmetic by hand
(stdev/annualization and Black-76 are exactly what LLMs hallucinate).

Read-only, no network, no auth. The agent fetches Deribit, saves the snapshot,
runs this, and reads the JSON back.

Input snapshot (--data FILE, JSON):
    {
      "dvol_close": 48.16,                      // last DVOL close (vol points)
      "spot": 61973.5,                           // spot/index at fetch (for surface)
      "spot_closes_7d": [63670, 63812, ...],    // hourly BTC-PERPETUAL closes, 7d
      "trades": [                                // option trades for the window
        {"instrument_name": "BTC-26JUN26-55000-P", "index_price": 62000,
         "iv": 72.0, "timestamp": 1780000000000, "direction": "buy",
         "amount": 100, "block_trade_id": "BLOCK-1"}
      ],
      "tickers": {                               // per-strike surface tickers
        "BTC-5JUN26-62000-C": {"mark_iv": 82.87, "delta": 0.4956}
      }
    }
Any field may be omitted; the corresponding section is then reported as null.

Usage:
    uv run scripts/paradex_options_recap.py --data snapshot.json
    uv run scripts/paradex_options_recap.py --data snapshot.json --pretty

Output (stdout, JSON):
    {"realized_vol": {...}, "flow_greeks": {...}, "top_blocks": [...], "vol_surface": {...}}

To verify the math without any data: python3 scripts/test_vol_math.py
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from vol_math import (
    realized_vs_implied,
    compute_flow_greeks,
    cluster_blocks,
    compute_vol_surface,
    summarize_blocks,
)


def compute(snapshot: dict) -> dict:
    """Pure: snapshot dict → {realized_vol, flow_greeks, top_blocks, vol_surface}."""
    closes = snapshot.get("spot_closes_7d") or []
    dvol_close = snapshot.get("dvol_close")
    trades = snapshot.get("trades") or []
    tickers = snapshot.get("tickers") or {}
    spot = snapshot.get("spot")

    rv = realized_vs_implied(closes, dvol_close)
    clusters = cluster_blocks(trades)
    fg = compute_flow_greeks(clusters)
    top_blocks = summarize_blocks(clusters)
    surface = compute_vol_surface(tickers, spot) if tickers else None
    return {
        "realized_vol": rv,
        "flow_greeks": fg,
        "top_blocks": top_blocks,
        "vol_surface": surface,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Options-recap vol math (RV + flow greeks)")
    parser.add_argument("--data", required=True, help="Path to a pre-fetched JSON snapshot")
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON output")
    args = parser.parse_args()

    try:
        snapshot = json.loads(open(args.data).read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading --data {args.data}: {exc}", file=sys.stderr)
        sys.exit(1)

    result = compute(snapshot)
    print(json.dumps(result, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
