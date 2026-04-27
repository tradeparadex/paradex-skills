---
name: paradex-order-builder
description: >
  Order sizing and multi-leg execution assistant for Paradex. Translates
  natural-language sizing instructions — "use 10% of free collateral",
  "scale 25% more into my short", "risk 1% of account to the stop",
  "sell the 90K call and hedge with a perp short" — into validated order
  payloads with pre-trade checks and an explicit confirmation step before
  any order is placed. Use when the user wants to enter a position, scale
  an existing position, size an order from collateral or risk parameters,
  or place a coordinated multi-leg trade. Trigger phrases: "place an order",
  "enter a position", "scale into", "size an order", "use X% of collateral",
  "open a position", "buy/sell N% of free margin", "add to my position",
  "build a position", "risk X% to the stop", "multi-leg entry",
  "sell the call and hedge", "place a spread", "size this trade",
  "how many contracts can I buy".
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.1"
---

# Paradex Order Builder

Converts sizing instructions into validated Paradex orders with a mandatory confirmation gate.

## Available MCP Tools

| Tool | What it provides |
|------|-----------------|
| `paradex_account_summary` | Free collateral, account value, current IMR/MMR |
| `paradex_account_positions` | Open positions with size and side (for scaling) |
| `paradex_market_summaries` | Mark price, delta, BBO for sizing calculations |
| `paradex_markets` | `order_size_increment`, `price_tick_size`, `min_order_size`, margin params |
| `paradex_bbo` | Live best bid/offer for limit price selection |
| `paradex_pre_trade_check` | Collateral check + margin estimate before submission |
| `paradex_create_order` | Submit the order (only after explicit user confirmation) |
| `paradex_order_status` | Verify order accepted post-submit |
| `paradex_open_orders` | Check for conflicting open orders |
| `paradex_cancel_orders` | Cancel a conflicting order if user requests |

## Sizing Methods

### 1. Collateral-based sizing

User says: *"use 10% of free collateral to buy BTC perp"*

```
free_collateral = paradex_account_summary().free_collateral
notional_target = free_collateral × collateral_pct
mark_price      = paradex_market_summaries(market_id).mark_price
raw_size        = notional_target / mark_price
size            = floor(raw_size / size_increment) × size_increment
```

### 2. Scale existing position

User says: *"scale 25% more into my ETH short"*

```
current_size  = abs(paradex_account_positions[market].size)
add_size_raw  = current_size × scale_pct
size          = floor(add_size_raw / size_increment) × size_increment
side          = same as current position side
```

### 3. Risk-based sizing

User says: *"risk 1% of account equity, stop at 75000"*

```
account_value = paradex_account_summary().account_value
risk_dollars  = account_value × risk_pct
entry_price   = mark_price  (or user-specified)
stop_price    = user-specified
price_risk    = abs(entry_price − stop_price)
raw_size      = risk_dollars / price_risk
size          = floor(raw_size / size_increment) × size_increment
```

### 4. Fixed size / fixed notional

User says: *"buy 0.1 BTC"* or *"buy $5000 of ETH"*

Direct: use stated size, or `size = notional / mark_price` rounded to increment.

## Order Type Selection

| User intent | Suggested type |
|-------------|---------------|
| "now", "market", "ASAP" | MARKET |
| specific price given | LIMIT GTC |
| "post only", "as maker" | LIMIT POST_ONLY |
| "if it drops to X" | STOP_LIMIT or STOP_MARKET |

Default to LIMIT at mid (best_bid + best_ask) / 2 rounded to tick unless user specifies otherwise. For options, use LIMIT at mark price ± 0.5%.

## Multi-Leg Orders

When the user requests coordinated legs (e.g., *"sell the call and delta-hedge it with a perp short"*):

1. Compute each leg's size independently (may use pm_math delta-hedge logic for the hedge leg)
2. Run `paradex_pre_trade_check` for **each** leg
3. Present all legs together in the confirmation block
4. On confirmation: submit legs in sequence — risk-reducing leg first, then risk-adding leg
5. Report status for each leg separately

## Workflow

### Step 1 — Gather state

Fetch in parallel:
- `paradex_account_summary` — free collateral, account value
- `paradex_account_positions` — current positions (needed for scaling)
- `paradex_market_summaries(market_id)` — mark price, delta
- `paradex_markets(market_id)` — size increment, tick size, min notional

### Step 2 — Compute size

Apply the appropriate sizing method. Always:
- Round DOWN to `order_size_increment` from `paradex_markets`
- Verify `size × mark_price ≥ min_order_size` (reject if below minimum)
- Verify `size > 0`

### Step 3 — Pre-trade check

Run `paradex_pre_trade_check(market_id, side, size)` and surface:

> For a detailed IMR/MMR breakdown (especially for options sells or large perp positions),
> run `paradex-pm-analyzer` after confirming the order size. The pre-trade check confirms
> collateral sufficiency; pm-analyzer shows the full margin composition and liquidation distance.
- `ready_to_trade` — if false, show `not_ready_reasons` and stop
- `free_collateral` — confirm sufficient margin
- `estimates.fee_usdc` — include in confirmation block
- `estimates.slippage_bps` — flag if > 20 bps for perps, > 50 bps for options

### Step 4 — Confirmation block

**Always** present this block and wait for explicit confirmation before calling `paradex_create_order`:

```
Order to place
──────────────────────────────
Market:   BTC-USD-PERP
Side:     BUY
Size:     0.0500 BTC
Type:     LIMIT
Price:    $84,250.00  (mid)
Notional: $4,212.50

Sizing basis: 5% of free collateral ($84,250)
Est. fee:     $2.11  (0.05%)
Est. slippage: 2 bps

Collateral after: $79,825  (IMR: $126.38)
──────────────────────────────
Confirm? [yes / no / adjust]
```

**Responses accepted:**
- `yes` — proceed, place the order
- `no` — cancel, do not place the order
- `adjust <param> <value>` — modify one parameter before placing:
  - `adjust size 0.05` — change the order size
  - `adjust price 84000` — change the limit price
  - `adjust type market` — switch to a market order

  On receiving `adjust`, recompute affected fields, re-run `paradex_pre_trade_check`,
  and present an updated confirmation block. Do not place the order until `yes` is received.

For multi-leg:
```
Multi-leg order
──────────────────────────────
Leg 1 (risk-reducing first):
  SELL  BTC-USD-PERP  0.0500  MARKET
Leg 2:
  SELL  BTC-USD-8MAY26-90000-C  0.010  LIMIT  $1,820.00

Combined notional: $5,994
Est. total fee:    $4.21
Portfolio delta after: ~+0.002
──────────────────────────────
Confirm? [yes / no / adjust]
```

Same `adjust <param> <value>` syntax applies to multi-leg orders. To change a specific leg,
include the leg number: `adjust leg1 price 84000` or `adjust leg2 size 0.008`. Recompute all
affected fields and re-run pre-trade checks before presenting the updated confirmation block.

### Step 5 — Submit

On confirmation call `paradex_create_order` with:
- `client_id`: `"ob-{leg}-{unix_ms}"` (e.g. `"ob-1-1745612345678"`)
- All other parameters from the confirmation block

Then call `paradex_order_status(client_id=...)` to confirm acceptance.

## Caveats

- **Never submit without explicit confirmation.** If the user says "just do it" in the same message as the order instruction, still present the confirmation block and wait — this protects against accidental over-sizing.
- If `pre_trade_check.ready_to_trade` is false, explain why and suggest fixes (reduce size, reduce existing positions) rather than proceeding.
- For stop orders: `trigger_price` is the stop level; `price` is the limit price for STOP_LIMIT orders. Always set `price = trigger_price × (1 ± 0.5%)` as a safety buffer unless user specifies otherwise.
- Size calculations use `floor()` not `round()` — always round DOWN to avoid over-sizing.
- Do not guess `order_size_increment` or `price_tick_size` — fetch from `paradex_markets` every time.
