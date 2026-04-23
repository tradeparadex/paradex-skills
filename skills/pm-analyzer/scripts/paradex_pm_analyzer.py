#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["paradex-py", "httpx"]
# ///
"""
paradex_pm_analyzer.py — Fetch live Paradex data, compute margin, output hedge payload.

Auth (pick one):
    PARADEX_JWT_TOKEN   long-lived API key or short-lived JWT from the auth endpoint
    PARADEX_API_KEY     alias for PARADEX_JWT_TOKEN
    --data FILE         pre-fetched JSON snapshot — no credentials needed

The script is read-only. Order placement is left to the caller:
  - Claude / MCP: call paradex_create_order with the printed payload
  - Direct API: POST /orders with the JSON from --delta-hedge --json

Usage:
    uv run paradex_pm_analyzer.py                                   # margin report
    uv run paradex_pm_analyzer.py --what-if BTC-USD-PERP BUY 0.01  # what-if
    uv run paradex_pm_analyzer.py --delta-hedge                     # compute hedge + print payload
    uv run paradex_pm_analyzer.py --json                            # save full snapshot
    uv run paradex_pm_analyzer.py --data snapshot.json             # replay offline
    uv run paradex_pm_analyzer.py --pm-config btc-pm.json          # override PM config

Workflow with Claude / MCP:
    1. Script fetches data and prints the hedge order payload
    2. Claude calls paradex_create_order with the payload to execute
    OR:
    1. Ask Claude to fetch data via MCP tools → save as --json snapshot
    2. Run offline: uv run paradex_pm_analyzer.py --data snapshot.json

Calculation logic lives entirely in pm_math.py.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pm_math import compute, delta_hedge_size


# ── Auth + API ──────────────────────────────────────────────────────────────

def _make_client():
    """
    Create a Paradex API client authenticated with a JWT token.
    Accepts PARADEX_JWT_TOKEN or PARADEX_API_KEY (same precedence as mcp-paradex-py).
    """
    import httpx
    from paradex_py.api.api_client import ParadexApiClient
    from paradex_py.api.protocols import DefaultRetryStrategy
    from paradex_py.environment import PROD

    token = os.environ.get("PARADEX_JWT_TOKEN") or os.environ.get("PARADEX_API_KEY")
    if not token:
        print(
            "Error: no credentials.\n"
            "  Set PARADEX_JWT_TOKEN (long-lived API key or short-lived JWT), or\n"
            "  use --data FILE to provide a pre-fetched snapshot.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Authenticating...", end=" ", flush=True)
    client = ParadexApiClient(
        env=PROD,
        logger=None,
        http_client=httpx.Client(timeout=30.0),
        auto_auth=False,
        retry_strategy=DefaultRetryStrategy(),
    )
    client.set_token(token)
    print("OK")
    return client


def _get(client, path: str, params: dict = None):
    return client.get(client.api_url, path, params or {})


# ── Fetch + normalise to pm_math input schema ───────────────────────────────

def fetch(client) -> dict:
    """Fetch all required data and normalise to pm_math input dicts."""
    print("Fetching...", end=" ", flush=True)

    margin_resp  = _get(client, "account/margin", {"market": "BTC-USD-PERP"})
    summary_resp = _get(client, "account")
    pos_resp     = _get(client, "positions")
    ord_resp     = _get(client, "orders", {"status": "OPEN"})
    bal_resp     = _get(client, "balance")
    mkt_summ     = _get(client, "markets/summary", {"market": "ALL"})
    mkt_spec     = _get(client, "markets")
    pm_cfg_resp  = _get(client, "system/portfolio-margin-config")

    print("OK")

    positions = [
        {
            "market": x["market"],
            "side":   x["side"],
            "size":   abs(float(x["size"])),
        }
        for x in (pos_resp.get("results") or [])
        if x.get("status") == "OPEN" and abs(float(x.get("size") or 0)) > 0
    ]

    orders = [
        {
            "market": o["market"],
            "side":   o["side"],
            "size":   float(o.get("remaining_size") or o.get("size") or 0),
            "price":  float(o.get("price") or 0),
        }
        for o in (ord_resp.get("results") or [])
        if float(o.get("remaining_size") or o.get("size") or 0) > 0
    ]

    market_data = {}
    for m in (mkt_summ.get("results") or []):
        sym = m["symbol"]
        delta = float((m.get("greeks") or {}).get("delta") or m.get("delta") or 0)
        market_data[sym] = {
            "mark_price":       float(m.get("mark_price") or 0),
            "delta":            delta,
            "mark_iv":          float(m["mark_iv"]) if m.get("mark_iv") else None,
            "underlying_price": float(m.get("underlying_price") or 0),
            "funding_rate":     float(m["funding_rate"]) if m.get("funding_rate") else 0.0,
        }

    market_specs = {m["symbol"]: m for m in (mkt_spec.get("results") or [])}

    balances = [
        {"token": b["token"], "size": float(b.get("size") or 0)}
        for b in (bal_resp.get("result") or bal_resp.get("results") or [])
    ]

    pm_configs = pm_cfg_resp.get("results", [])
    pm_config = next(
        (c for c in pm_configs if c.get("base_asset") == "BTC"),
        pm_configs[0] if pm_configs else None,
    )

    return {
        "margin_methodology": margin_resp.get("margin_methodology", "cross_margin"),
        "positions":    positions,
        "orders":       orders,
        "market_data":  market_data,
        "market_specs": market_specs,
        "balances":     balances,
        "pm_config":    pm_config,
        "exchange": {
            "imr":             float(summary_resp.get("initial_margin_requirement") or 0),
            "mmr":             float(summary_resp.get("maintenance_margin_requirement") or 0),
            "account_value":   float(summary_resp.get("account_value") or 0),
            "free_collateral": float(summary_resp.get("free_collateral") or 0),
        },
    }


# ── Display ─────────────────────────────────────────────────────────────────

def print_report(data: dict, what_if: dict = None) -> dict:
    positions = data["positions"]
    if what_if:
        positions = positions + [what_if]

    result = compute(
        positions, data["orders"],
        data["market_data"], data["market_specs"],
        margin_methodology=data["margin_methodology"],
        balances=data["balances"],
        pm_config=data.get("pm_config"),
    )

    ex       = data["exchange"]
    av       = ex["account_value"]
    fc       = ex["free_collateral"]
    ex_imr   = ex["imr"]
    ex_mmr   = ex["mmr"]
    calc_imr = result["IMR"]
    calc_mmr = result["MMR"]
    liq_dist = av - ex_mmr

    print("\n" + "═"*58)
    print("  PARADEX MARGIN REPORT")
    if what_if:
        print(f"  (What-if: +{what_if['side']} {what_if['size']} {what_if['market']})")
    print("═"*58)
    print(f"  Methodology:      {data['margin_methodology'].replace('_',' ').title()}")
    print(f"  Account value:    ${av:>10.4f}")
    print(f"  Free collateral:  ${fc:>10.4f}")
    print(f"  Margin util:      {calc_imr/av*100 if av else 0:>9.1f}%")
    print(f"  Liq distance:     ${liq_dist:>10.4f}  ({liq_dist/av*100:.0f}% of AV)")
    print()
    print(f"  {'':32}  {'Calc':>8}  {'Exchange':>8}")
    print(f"  {'IMR':32}  ${calc_imr:>7.4f}  ${ex_imr:>7.4f}  (Δ{calc_imr-ex_imr:+.4f})")
    print(f"  {'MMR':32}  ${calc_mmr:>7.4f}  ${ex_mmr:>7.4f}  (Δ{calc_mmr-ex_mmr:+.4f})")
    if result.get("spot_balance_margin"):
        print(f"  {'Spot balance margin':32}  ${result['spot_balance_margin']:>7.4f}")
    print()

    pos_detail = result.get("positions", [])
    if pos_detail:
        print(f"  {'Market':<28} {'Side':<5} {'Size':>8} {'Mark':>10} {'Delta':>8} {'IMR':>7}")
        print("  " + "-"*74)
        for r in pos_detail:
            side = r.get("side", "")
            size = float(r.get("size") or 0)
            print(f"  {r['market']:<28} {side:<5} {size:>8.5f} "
                  f"${r['mark_price']:>9.2f} {r['delta_contrib']:>+8.5f} ${r['imr']:>6.4f}")
    else:
        for pos in positions:
            md = data["market_data"].get(pos["market"], {})
            print(f"  {pos['market']:<28} {pos['side']:<5} "
                  f"{float(pos['size']):>8.5f} ${float(md.get('mark_price',0)):>9.2f}")

    print()
    print(f"  Portfolio delta:  {result['portfolio_delta']:+.6f} BTC")

    if data["margin_methodology"] == "portfolio_margin" and "worst_loss" in result:
        sc = result["worst_scenario"]
        print()
        print("  ── PM Breakdown ──────────────────────────────")
        print(f"  Step 1 worst loss:  ${result['worst_loss']:.4f}  "
              f"(scen #{result['worst_idx']+1}: {sc[0]*100:+.0f}% spot, {sc[1]*100:+.0f}% vol)")
        print(f"  Step 2 delta-min:   ${result['delta_min']:.4f}  "
              f"(maxL={result['maxL']:.5f}, maxS={result['maxS']:.5f})")
        print(f"  Step 3 funding:     ${result['fund_p']:.6f}")
        print(f"  Step 4 IMR/MMR:     ${result['IMR']:.4f} / ${result['MMR']:.4f}")

    print("═"*58)
    return result


def compute_hedge_payload(data: dict, result: dict) -> dict | None:
    """
    Compute the delta hedge order payload.
    Returns a dict suitable for paradex_create_order, or None if no hedge needed.
    """
    port_delta = result["portfolio_delta"]
    hedge_mkt  = "BTC-USD-PERP"
    md         = data["market_data"].get(hedge_mkt, {})
    spec       = data["market_specs"].get(hedge_mkt, {})
    inst_delta = float(md.get("delta") or 1.0)
    mark       = float(md.get("mark_price") or 0)
    size_step  = float(spec.get("order_size_increment") or 0.00001)

    side, size = delta_hedge_size(port_delta, inst_delta, size_step)
    if side == "NONE":
        return None

    sign = 1 if side == "BUY" else -1
    return {
        "market":      hedge_mkt,
        "side":        side,
        "type":        "MARKET",
        "size":        size,
        "price":       0,
        "instruction": "IOC",
        # Context fields (not sent to API, informational only)
        "_delta_before": round(port_delta, 6),
        "_delta_after":  round(port_delta + sign * size * inst_delta, 6),
        "_notional":     round(size * mark, 2),
        "_mark_price":   mark,
    }


def print_hedge(data: dict, result: dict, as_json: bool = False):
    payload = compute_hedge_payload(data, result)

    if payload is None:
        print(f"\n  Portfolio delta {result['portfolio_delta']:+.6f} is already ~zero. No hedge needed.")
        return

    if as_json:
        # Emit clean API payload (strip _ context fields)
        api_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        print(json.dumps(api_payload, indent=2))
        return

    print(f"\n  ── Delta Hedge Order ──────────────────────────")
    print(f"  Instrument:    {payload['market']}")
    print(f"  Side:          {payload['side']}")
    print(f"  Size:          {payload['size']:.5f} BTC")
    print(f"  Mark:          ${payload['_mark_price']:,.2f}")
    print(f"  Notional:      ${payload['_notional']:,.2f}")
    print(f"  Delta before:  {payload['_delta_before']:+.6f}")
    print(f"  Delta after:   {payload['_delta_after']:+.6f}")
    print()
    print(f"  To execute via Claude: \"place this delta hedge order\"")
    print(f"  To execute via MCP:    paradex_create_order(market='{payload['market']}', "
          f"order_side='{payload['side']}', order_type='MARKET', size={payload['size']}, "
          f"price=0, instruction='IOC')")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Paradex margin analyzer — read-only, JWT auth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Auth (pick one):\n"
            "  PARADEX_JWT_TOKEN   long-lived API key or short-lived JWT\n"
            "  PARADEX_API_KEY     alias for PARADEX_JWT_TOKEN\n"
            "  --data FILE         pre-fetched snapshot, no credentials needed\n\n"
            "Order execution is not performed by this script.\n"
            "Pass the printed payload to Claude / paradex_create_order.\n"
        ),
    )
    ap.add_argument("--delta-hedge", action="store_true",
                    help="Compute delta-neutral hedge order and print payload")
    ap.add_argument("--what-if",     nargs=3, metavar=("MARKET", "SIDE", "SIZE"))
    ap.add_argument("--json",        action="store_true",
                    help="Output full JSON snapshot (for --data replay or passing to Claude)")
    ap.add_argument("--data",        metavar="FILE",
                    help="Load pre-fetched data from a --json snapshot (no credentials needed)")
    ap.add_argument("--pm-config",   metavar="FILE",
                    help="Override PM config JSON (skips system/portfolio-margin-config fetch)")
    args = ap.parse_args()

    # Load data
    if args.data:
        with open(args.data) as f:
            data = json.load(f)
    else:
        client = _make_client()
        data   = fetch(client)

    if args.pm_config:
        with open(args.pm_config) as f:
            data["pm_config"] = json.load(f)

    # JSON snapshot output
    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return

    what_if = None
    if args.what_if:
        mkt, side, size = args.what_if
        what_if = {"market": mkt, "side": side.upper(), "size": float(size)}

    result = print_report(data, what_if=what_if)

    if args.delta_hedge:
        print_hedge(data, result)


if __name__ == "__main__":
    main()
