---
name: paradex-trading-recap
description: >
  Activity-focused trading recap for a user-specified time period on Paradex. Fetches
  fills, order history, and funding payments for a window (today, yesterday, last 7 days,
  or custom) and produces: realized P&L from fills, fee totals, net P&L attribution
  (trading + funding − fees), order placement and fill rate metrics, and per-market
  breakdown. Use this skill when the user asks about activity over a period — "recap my
  trading today", "what did I do last week", "how many trades did I place", "what was my
  P&L yesterday", "summarize my fills", "what was my win rate", "did I make money this
  week". Distinct from portfolio-copilot (current account state/positions) and
  risk-guardian (risk metrics) — this skill is about what happened during a period,
  using actual fill data.
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.3"
---

# Paradex Trading Recap

Turns raw fill, order, and funding data into concise activity summaries for a
user-specified time window. Answers "what happened during this period?" with realized
numbers — not estimates.

## Available MCP Tools

| Tool | Data |
|---|---|
| `paradex_account_positions` | Current markets — used to determine which markets to query |
| `paradex_account_fills` | Fills for a period (params: market_id, start_unix_ms, end_unix_ms) — price, size, fee, realized_pnl, liquidity, flags |
| `paradex_orders_history` | Orders placed in period — status (FILLED/CANCELED/REJECTED), type, size, price |
| `paradex_account_funding_payments` | Funding payments in period (market_id optional, start/end) |
| `paradex_market_summaries` | Current prices and 24h context for traded markets |

## Capabilities

### 1. Period Resolution

Convert the user's time expression to unix millisecond timestamps.

| Expression | start_unix_ms | end_unix_ms |
|---|---|---|
| "today" | 00:00 UTC today | now |
| "yesterday" | 00:00 UTC prev day | 23:59:59 UTC prev day |
| "this week" | Monday 00:00 UTC | now |
| "last week" | Monday prev week 00:00 UTC | Sunday prev week 23:59:59 UTC |
| "last 7 days" | now − 7×86400000 | now |
| "last 24 hours" | now − 86400000 | now |
| "last N hours" | now − N×3600000 | now |

**Multi-market fetch strategy:**
1. Build the market list from **two sources** (union of both):
   - `paradex_account_positions` → currently open positions (fast path)
   - Any markets the user names explicitly in their request (always include these even if
     no open position exists)
   - If the user suspects fills are missing, ask them to name additional markets:
     "Which markets should I include? I can only see fills for markets you're currently
     in or that you name explicitly."
   - Always produce the per-market breakdown table for the markets you do have data for.
     The missing-markets caveat goes at the **bottom** of the response, not in place of the
     table. An incomplete table with a footnote is better than no table.
   - Fallback note (add at the end): "Markets queried: [list]. Fills from markets with
     positions fully closed before this query may be absent. Name additional markets to include them."
2. Call `paradex_account_fills(market_id, start_unix_ms, end_unix_ms)` per market.
3. Call `paradex_orders_history(market_id, start_unix_ms, end_unix_ms)` per market.
4. Call `paradex_account_funding_payments(start_unix_ms=start, end_unix_ms=end)` once — no market filter needed.

### 2. Trade Activity Summary

From `paradex_orders_history` across all markets:

- `orders_placed = count(all orders)`
- `orders_filled = count(status == "FILLED")`
- `orders_cancelled = count(status == "CANCELED")`
- `orders_rejected = count(status == "REJECTED")`
- `fill_rate_pct = orders_filled / orders_placed × 100`
- `total_volume_usd = sum(float(fill.size) × float(fill.price))` from fills
- `avg_trade_size_usd = total_volume_usd / orders_filled`

Group by market for per-market order counts.

### 3. P&L Attribution

From `paradex_account_fills`:

- `realized_pnl_gross = sum(float(fill.realized_pnl or 0))`
- `total_fees = sum(float(fill.fee))`
- Maker/taker split: `fill.liquidity == "MAKER"` vs `"TAKER"`
- `maker_volume_pct = sum(maker fills size×price) / total_volume × 100`

From `paradex_account_funding_payments`:

- `funding_pnl = sum(float(p.funding_payment))` — positive = received, negative = paid

Net: `net_pnl = realized_pnl_gross + funding_pnl - total_fees`

Per-market: group fills by `fill.market`, compute per-market realized_pnl, fees, volume.

**Two hard rules for every P&L figure shown:**

1. **Realized only — never unrealized.** Every number in a recap comes from `fill.realized_pnl`,
   `fill.fee`, and funding payments. Do **not** add mark-to-market / unrealized P&L on open
   positions into any row, total, or net — not even for a market the user still holds. The
   per-market `Net` column is `realized_pnl + funding − fees` for that market, nothing else.
   (For unrealized P&L, that's `paradex-portfolio-copilot`, not this skill.)
2. **Net must reconcile.** The bold `Net P&L` must equal `realized_pnl_gross + funding_pnl −
   total_fees` exactly, and each per-market `Net` must equal that market's
   `realized + funding − fees`. Before emitting, check the arithmetic adds up; if a figure
   doesn't reconcile, fix it rather than shipping an inconsistent table.

### 4. Win Rate Analysis

From closing fills (fills where `float(fill.realized_pnl or 0) != 0`):

- `closing_fills = [f for f in fills if float(f.realized_pnl or 0) != 0]`
- `winning = [f for f in closing_fills if float(f.realized_pnl) > 0]`
- `losing = [f for f in closing_fills if float(f.realized_pnl) < 0]`
- `win_rate_pct = len(winning) / len(closing_fills) × 100`
- `avg_win = sum winners / count winners`
- `avg_loss = sum losers / count losers`
- `profit_factor = abs(sum winners) / abs(sum losers)`

**Thresholds:**

| Metric | Strong | Normal | Concerning |
|---|---|---|---|
| Win rate | > 60% | 45–60% | < 45% |
| Profit factor | > 2.0 | 1.0–2.0 | < 1.0 |

Only show this section if `len(closing_fills) >= 3` — too few to be meaningful otherwise.
**When fewer than 3 closing fills exist: do NOT calculate or mention any win rate figure,
not even informally (e.g., "50% on paper", "technically 1 for 2"). State only that the
sample is insufficient and give the count.**

### 5. Per-Market Breakdown

Table grouping fills by market — columns: Market, Fills, Volume, Realized P&L, Fees, Net.
Sort by net P&L descending. Mark best market (highest net) and worst (lowest net).

### 6. FastFill Detection

- `fastfill_count = count(fills where "fastfill" in (fill.flags or []))`
- `fastfill_pct = fastfill_count / total_fills × 100`

Show as a single line at the bottom of the report:
`FastFills: X of Y fills (X%) — Paradex 30% fee discount`

Only show this line if `fastfill_count > 0`.

## Output Formats

### Empty Period (no activity)

When `orders_placed == 0` AND `total_fills == 0` for the queried period, do NOT produce
tables with zero values. Instead, respond with a short prose statement:

```
No orders placed and no fills recorded in {market_list} for {period}.
Funding payments: {funding_pnl or "none"}.
{Fallback note if markets are limited}
```

Only the zero-fills / zero-orders case gets prose — any period with at least one fill or
one order uses the full table format below.

**Narrow / odd-hours windows are usually empty.** A request for a single overnight or
off-hours hour (e.g. "3am to 4am", "between 2 and 3 in the morning") most often had no
activity. If the fill/order queries come back empty for such a window, that is the expected
result — emit the Empty Period prose above. **Never invent an order log, fill prices, volume,
or a P&L table to "fill" an empty window** — a P&L attribution table or zeroed rows for a
window with no activity is wrong, not helpful. When in doubt for a narrow window with no
data, default to the empty-state response.

### Quick Recap

```
## Trading Recap — {period} ({start_date} to {end_date})

**Activity:** {orders_placed} orders placed — {orders_filled} filled, {orders_cancelled} cancelled ({fill_rate_pct}% fill rate)
**Volume:** ${total_volume_usd}
**Markets:** {market_list}

### P&L Breakdown
| Component | Amount |
|---|---|
| Realized P&L (fills) | {+/-}${realized_pnl} |
| Funding payments | {+/-}${funding_pnl} |
| Trading fees | -${total_fees} |
| **Net P&L** | **{+/-}${net_pnl}** |

Best: {best_market} ({+/-}${best_pnl}) | Worst: {worst_market} ({+/-}${worst_pnl})
```

### Detailed Activity Report

```
## Detailed Trading Recap — {period}

### Summary
Orders: {placed} placed | {filled} filled | {cancelled} cancelled | {fill_rate}% fill rate
Volume: ${total_volume} | Maker: {maker_pct}% / Taker: {taker_pct}%

### P&L Attribution
| Component | Amount |
|---|---|
| Realized P&L (fills) | {+/-}${realized_pnl} |
| Funding payments | {+/-}${funding_pnl} |
| Trading fees | -${total_fees} |
| **Net P&L** | **{+/-}${net_pnl}** |

### Win Rate
Closing fills: {n_closing} | Win rate: {win_rate}% | Profit factor: {pf}
Avg win: +${avg_win} | Avg loss: -${avg_loss}

### Per-Market Breakdown
| Market | Fills | Volume | Realized P&L | Fees | Net |
|---|---|---|---|---|---|
| BTC-USD-PERP | 3 | $28,000 | +$250 | -$14 | +$236 |
| ETH-USD-PERP | 2 | $12,000 | +$62 | -$6 | +$56 |

### Order Log
| Time (UTC) | Market | Side | Type | Size | Avg Fill | Status | Realized P&L |
|---|---|---|---|---|---|---|---|
| 08:14 | BTC-USD-PERP | BUY | LIMIT | 0.10 | $64,500 | FILLED | — |
| 09:02 | BTC-USD-PERP | SELL | LIMIT | 0.10 | $65,800 | FILLED | +$130 |
```

### Period Comparison

```
## Period Comparison

| Metric | {period_1} | {period_2} | Change |
|---|---|---|---|
| Volume | ${vol_1} | ${vol_2} | {delta} |
| Fills | {fills_1} | {fills_2} | {delta} |
| Net P&L | {+/-}${pnl_1} | {+/-}${pnl_2} | {+/-}${delta} |
| Win rate | {wr_1}% | {wr_2}% | {delta}pp |
| Fees | ${fees_1} | ${fees_2} | {delta} |
| Maker ratio | {maker_1}% | {maker_2}% | {delta}pp |
```

## Caveats

- `realized_pnl` on fills reflects the closed-portion P&L at that moment. Opening fills
  produce zero realized P&L and are included in volume but not win rate.
- Win rate requires at least 3 closing fills to be meaningful.
- `paradex_account_fills` requires `market_id` — fills are fetched per market. The market
  list is built from currently open positions plus any markets the user names explicitly.
  Markets where positions were fully closed before the query time **may be missed**. If the
  user suspects missing fills, they should name those markets explicitly (e.g., "include
  BTC and ETH even if I have no open position").
- Funding payments are reported at funding interval timestamps. Payments at period
  boundaries may partially fall inside or outside the window.
- This skill shows realized P&L from fills, not unrealized mark-to-market changes.
  For unrealized P&L and current positions, use `paradex-portfolio-copilot`.
- Covers personal trading accounts only. Vault trading activity requires vault-specific tools.
- Not financial advice.

See [period-calc.md](references/period-calc.md) for timestamp resolution details and multi-market fetch strategy.
