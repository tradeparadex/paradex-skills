---
name: paradex-pm-analyzer
description: >
  Margin calculation engine and delta-hedge order tool for Paradex accounts.
  Computes IMR and MMR using the correct methodology — cross-margin (XM) for
  most accounts or Portfolio Margin (PM) scenario scan for PM-enrolled accounts
  — determined at the account level via margin_methodology. Also computes and
  optionally submits delta-neutral hedge orders: calculates the exact perp size
  to neutralise portfolio delta and places it via paradex_create_order with
  confirmation before execution. Use when the user asks about margin
  requirements, IMR/MMR breakdown, worst-case scenario analysis, how a new
  position would affect margin, liquidation distance, or says "delta hedge my
  portfolio", "neutralise my delta", "what size perp to hedge", "place a delta
  hedge", "hedge my options", "check my margin", "run the margin calc", "how
  much margin do I need".
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.2"
---

# Paradex PM Analyzer

Computes margin (IMR/MMR) for a Paradex account and optionally places a delta-neutral hedge order.

## Script

A standalone Python script is available at `scripts/paradex_pm_analyzer.py`. Run it directly when you need a terminal report without going through the full MCP skill flow.

### Auth (pick one)

| Method | Credential | Notes |
|--------|------------|-------|
| Long-lived API key | `PARADEX_JWT_TOKEN` or `PARADEX_API_KEY` | Set once; works until revoked |
| Short-lived JWT | `PARADEX_JWT_TOKEN` | Obtained from auth endpoint or via Claude / MCP |
| Pre-fetched data | `--data FILE` — no credentials | Replay a saved `--json` snapshot |

The script is **read-only** — it computes the hedge order payload but does not place orders. Pass the printed payload to Claude (`paradex_create_order`) or place it via the Paradex UI.

### Usage

```bash
# Margin report (JWT in env)
export PARADEX_JWT_TOKEN=eyJ...
uv run scripts/paradex_pm_analyzer.py

# What-if
uv run scripts/paradex_pm_analyzer.py --what-if BTC-USD-PERP BUY 0.01

# Compute delta hedge and print the order payload
uv run scripts/paradex_pm_analyzer.py --delta-hedge

# Save full snapshot (for offline replay or sharing with Claude)
uv run scripts/paradex_pm_analyzer.py --json > snapshot.json

# Replay offline — no credentials needed
uv run scripts/paradex_pm_analyzer.py --data snapshot.json

# Override PM config
uv run scripts/paradex_pm_analyzer.py --pm-config btc-pm.json
```

To verify the math without live credentials: `python3 scripts/test_pm_math.py` (22 unit tests, no auth needed).

## Available MCP Tools

| Tool | What it provides |
|------|-----------------|
| `paradex_account_summary` | Exchange IMR, MMR, account value, free collateral (ground truth) |
| `paradex_account_positions` | Open positions with market, side, size |
| `paradex_open_orders` | Open limit orders with market, side, size, price |
| `paradex_account_balance` | Token balances (USDC + any spot tokens) |
| `paradex_market_summaries` | Mark prices, IV, funding rate, greeks (delta) per market |
| `paradex_markets` | asset_kind, delta1_cross_margin_params, option_cross_margin_params, order_size_increment |
| `paradex_system_config` | Live PM config per base asset: 24-scenario table, hedged/unhedged margin factors, mmf_factor, funding_provision_hour, vol_shock_params |
| `paradex_pre_trade_check` | Collateral check + BBO before placing hedge order |
| `paradex_create_order` | Place the hedge order on confirmation |
| `paradex_order_status` | Verify hedge order accepted post-submit |

## Margin Methodology

**Portfolio Margin is account-level, not per-instrument.** The `margin_methodology` field from `/account/margin` determines the pipeline. Fetch it from the server-side cached endpoint (no auth required):

```
GET /api/account-margin.json
→ { "margin_methodology": "cross_margin"|"portfolio_margin", "configs": [...], "fetched_at_iso": "..." }
```

If that endpoint is unavailable, default to `cross_margin`.

```
margin_methodology == "cross_margin"      → XM formulas for all instruments
margin_methodology == "portfolio_margin"  → 4-step PM pipeline for all instruments
```

## Cross-Margin (XM) Formulas

### Delta-1 (Perps / Futures)
Uses `delta1_cross_margin_params`: `imf_base`, `mmf_factor`

```
notional = |size| × mark_price
IMR      = notional × imf_base
MMR      = IMR × mmf_factor
```

### Long Options
```
IMR = mark_price × size       # mark premium is the margin
MMR = IMR × 0.5
```

### Short Options
Uses `option_cross_margin_params` OTM/ITM brackets. See `references/xm-formulas.md`.

### Spot Balance Margin
```
spotBM = Σ |balance[token]| × price[token]   (for token ≠ USDC)
```

### Total
```
IMR = Σ IMR_per_position + spotBM
MMR = Σ MMR_per_position + spotBM
```

Calculated values match exchange within ~$0.02 (fee provision rounding).

## Portfolio Margin (PM) Pipeline

Only when `margin_methodology == "portfolio_margin"`.

**Before computing PM margin**, call `paradex_system_config` and extract the config for the account's base asset from `portfolio_margin`:

```
cfg = paradex_system_config().portfolio_margin[base_asset]
scenarios          = cfg.scenarios            # list of {spot_shock, vol_shock, weight} — the 24-scenario table
HEDGED_MF          = cfg.hedged_margin_factor
UNHEDGED_MF        = cfg.unhedged_margin_factor
MMR_FACTOR         = cfg.mmf_factor
FUNDING_PERIOD_H   = cfg.funding_provision_hour   # hours per funding period (typically 8)
DTE_FLOOR          = cfg.vol_shock_params.dte_floor_days
VEGA_POWER_LT      = cfg.vol_shock_params.vega_power_long_dte
VEGA_POWER_ST      = cfg.vol_shock_params.vega_power_short_dte
```

See `references/pm-pipeline.md` for the full 4-step scenario scan and Black-Scholes repricing formulas.

## Output Format

1. **Summary card** — IMR, MMR, account value, free collateral, margin utilisation %, liquidation distance (`account_value − MMR`)
2. **Calc vs exchange** — show both; note small diff (~$0.02) is normal
3. **Per-position table** — market, side, size, mark price, delta, IMR contribution
4. **Risk callout** — flag if liquidation distance < 20% of account value

## Delta Hedge Mode

When asked to compute a delta-neutral hedge (and optionally place it):

### 1. Compute portfolio delta
```
portfolioDelta = Σ greeks.delta × signed_size
  signed_size = +size (BUY) or −size (SELL)
```
`greeks.delta` comes from `paradex_market_summaries`.

### 2. Compute neutralising size
```
# Default hedge instrument: BTC-USD-PERP (delta ≈ 1.0)
# Choose side that reduces |portfolioDelta|

neutral_size = −portfolioDelta / (side_sign × instrument_delta)
# Round DOWN to order_size_increment from paradex_markets
```
If `neutral_size ≤ 0` for chosen side, flip to the other side.

### 3. Pre-trade check
Run `paradex_pre_trade_check(market, side, neutral_size)` to verify collateral and size limits.

### 4. Show the order payload and confirm before submitting

Always present the computed order and wait for explicit confirmation before calling `paradex_create_order`:

```
Delta hedge order
  Market:  BTC-USD-PERP
  Side:    SELL
  Size:    0.00047 BTC
  Type:    MARKET

  Portfolio delta before: +0.000477
  Portfolio delta after:  ~0.000000
  IMR change: $3.25 → $3.64 (+$0.39)

Place this order? [yes/no]
```

### 5. Submit and verify
On confirmation: `paradex_create_order` with `client_id: "delta-hedge-{unix_ms}"`, then `paradex_order_status` to confirm.

## What-If Mode

When asked "what if I add X position":
1. Add hypothetical to current positions/orders
2. Re-run correct margin formula
3. Show: new IMR, new MMR, Δ margin, new liq distance, new portfolio delta

## Caveats

- `margin_methodology` is served via `/api/account-margin.json` (cached server-side, refreshed periodically). A dedicated MCP tool would make this cleaner but is not yet available.
- Calculated IMR/MMR may differ from exchange by ~$0.01–$0.02 due to fee provision not being directly accessible.
- Delta hedge uses `greeks.delta` from `paradex_market_summaries`; for options near expiry or deep ITM/OTM, live delta can shift quickly — re-run before submitting.
- Short option XM margin is not yet empirically verified against exchange values.
