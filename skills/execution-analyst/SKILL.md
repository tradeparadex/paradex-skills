---
name: paradex-execution-analyst
description: >
  Execution quality analysis and order replay for Paradex personal trading accounts.
  Reconstructs the chronological trading sequence for a market or time window,
  benchmarks fill prices against market prices at the time of each fill (arrival
  price, period VWAP), computes realized slippage in basis points, analyzes
  maker/taker ratio and fill patterns, and scores execution quality 1-10. Use this
  skill when the user asks how their orders were executed — "was my execution good",
  "how much slippage did I get", "replay my trades from this morning", "analyze my
  BTC entry", "why did my order fill at that price", "benchmark my fills", "execution
  quality report", "show me my order timeline". Distinct from trading-recap (activity
  summary/P&L) and market-analyst (market microstructure) — this skill focuses on
  the quality of the user's own execution against market benchmarks.
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.0"
---

# Paradex Execution Analyst

Measures how well orders were executed — not just what happened, but how efficiently
it happened. Benchmarks fill prices against arrival price and period VWAP, scores
execution quality, and replays the order timeline chronologically.

## Available MCP Tools

| Tool | Data |
|---|---|
| `paradex_orders_history` | Orders in period — id, market, side, type, size, price, filled_size, status, created_at, updated_at |
| `paradex_account_fills` | Fills — order_id, market, side, size, price, fee, timestamp, liquidity (MAKER/TAKER), flags, realized_pnl |
| `paradex_klines` | 1-min OHLCV candles — used for arrival price proxy at order submission time |
| `paradex_trades` | Market trade tape — used to compute period VWAP |
| `paradex_account_funding_payments` | Funding events for interleaving into the replay timeline (optional) |

## Capabilities

### 1. Order Reconstruction

Link orders to their fills via `fill.order_id == order.id`. Build per-order record:

- `avg_fill_price = sum(fill.size × fill.price) / sum(fill.size)` — weighted average fill price
- `fill_rate_pct = sum(fill.size) / order.size × 100`
- `time_to_first_fill_ms = fills[0].created_at - order.created_at`
- `fill_duration_ms = fills[-1].created_at - fills[0].created_at` (multi-fill only)
- `n_fills = count(fills linked to this order)`

Sort all orders by `created_at` for chronological replay. Note: orders placed outside
the query window but filled within it may appear in fills without a matching order
record — include them in fill analysis but mark as "order outside window".

### 2. Arrival Price Benchmarking

Arrival price = fair-value estimate at the moment the order was submitted.
We use kline close (not current mark price) because we need the market context
*at submission time*, not at analysis time — this is what the trader actually
faced when they placed the order.

**Method:**
1. Fetch `paradex_klines(market_id, resolution=1, start=order.created_at - 60000, end=order.created_at + 120000)`
2. Find the candle whose timestamp is closest to (but not after) `order.created_at`
3. Use that candle's `close` as the arrival price proxy

Efficiency: fetch a single kline window covering all orders in the batch, then look
up the relevant candle per order.

**Arrival slippage formula:**
```
# BUY order (want to buy as cheaply as possible)
arrival_slippage_bps = (avg_fill_price - arrival_price) / arrival_price × 10000
# Positive = paid more than arrival (bad). Negative = paid less (good).

# SELL order (want to sell as expensively as possible)
arrival_slippage_bps = (arrival_price - avg_fill_price) / arrival_price × 10000
# Positive = sold lower than arrival (bad). Negative = sold higher (good).
# Convention is consistent with BUY: positive = unfavorable, negative = favorable.
```

**Thresholds:**

| Slippage | Rating |
|---|---|
| 0–5 bps | Excellent |
| 5–15 bps | Good |
| 15–30 bps | Acceptable |
| 30–50 bps | Elevated |
| >50 bps | Poor |

### 3. VWAP Benchmarking

Period VWAP = volume-weighted average price all market participants paid during the
analysis window.

Method: fetch `paradex_trades(market_id, start_unix_ms, end_unix_ms)` → market trade tape.
```
market_vwap = sum(trade.size × trade.price) / sum(trade.size)
```

**VWAP comparison (directional):**
```
# BUY: buying below VWAP = good
vwap_delta_bps = (market_vwap - avg_fill_price) / market_vwap × 10000
# Positive = beat VWAP (good). Negative = missed VWAP (bad).

# SELL: selling above VWAP = good
vwap_delta_bps = (avg_fill_price - market_vwap) / market_vwap × 10000
# Positive = beat VWAP (good). Negative = missed VWAP (bad).
```

### 4. Fill Pattern Analysis

For orders with multiple fills:

- `price_walk_bps`: for BUY, did fill prices increase over time (adverse)?
  `(fills[-1].price - fills[0].price) / fills[0].price × 10000`
  Positive for BUY = prices rose as you filled = market impact
- `fill_size_distribution`: were fills roughly even or skewed?
- `maker_fills = count(fills where liquidity == "MAKER")` — limit order rested before fill
- `taker_fills = count(fills where liquidity == "TAKER")` — immediate execution
- `maker_ratio_pct = maker_fills / total_fills × 100`
- `fastfill_count = count(fills where "fastfill" in (fill.flags or []))`

**Fill completeness:**
- ≥95% filled: complete
- 50–95%: partial — significant portion missed
- <50%: mostly missed

### 5. Execution Score (1–10)

Weighted composite score. Higher = better execution.

| Factor | Weight | Score 10 | Score 1 |
|---|---|---|---|
| Arrival slippage | 35% | ≤2 bps | ≥50 bps |
| Fill completeness | 25% | ≥95% | <30% |
| VWAP comparison | 20% | Beat by >5 bps | Miss by >30 bps |
| Maker ratio | 10% | >70% maker | 0% maker |
| Price walk (multi-fill) | 10% | <2 bps walk | >30 bps adverse |

Individual factor scoring (0–10):
- Arrival: `max(0, 10 - arrival_slippage_bps / 5)`
- Completeness: `min(10, fill_rate_pct / 9.5)`
- VWAP: `min(10, max(0, 5 + vwap_delta_bps / 3))` (5 = at VWAP, 10 = beat by 15 bps)
- Maker: `maker_ratio_pct / 10`
- Walk: `max(0, 10 - price_walk_bps / 3)`

Final: `round(arr × 0.35 + comp × 0.25 + vwap × 0.20 + maker × 0.10 + walk × 0.10)`

Labels: 8–10 Excellent, 6–7 Good, 4–5 Fair, 1–3 Poor.

**Market order adjustment:** when a user places a market order, they are explicitly
choosing certainty of fill over best price — they want the position, now, and are
willing to pay taker spread for it. This shapes the entire evaluation:

- **Fill completeness** is what matters most (weighted 40%) — did they get the
  size they needed? An incomplete fill defeats the purpose of using a market order.
- **VWAP comparison** (35%) tells you whether the timing was good relative to
  where other participants traded during the same window.
- **Price walk** (25%) reveals market impact — if prices moved adversely across
  multiple fills, that's the real cost of size.
- **Maker ratio** is not a performance metric for market orders (weight 0%). Market
  orders physically cannot fill as maker; 0% is correct and expected. Framing this
  as a gap, or suggesting the user should use limit orders to "capture rebates" or
  "improve maker ratio", would be evaluating them against a benchmark they never
  aimed for.
- **Arrival slippage** is expected taker spread, not a flaw — skip this factor
  entirely and do not include it in the score for market orders.

The score interpretation should briefly note the weight redistribution: e.g.,
"Score based on fill completeness (40%), VWAP (35%), and price walk (25%).
Arrival slippage and maker ratio excluded — market orders trade price precision
for execution certainty."

### 6. Chronological Order Replay

Build a single timeline of all events sorted by timestamp:

- `SUBMIT` — order placed (type, side, size, limit price if applicable)
- `FILL` — fill received (fill price, size, liquidity, **slippage bps vs. arrival price**, fee)
  — slippage bps is required in every FILL line, even if zero
- `CANCEL` — order cancelled (cancel_reason, remaining size)
- `FUNDING` — funding payment (if `paradex_account_funding_payments` requested)

Format each event as:
```
{HH:MM:SS UTC} | {EVENT_TYPE:<8} | {details}
```

This lets the user see: "at 09:02 I placed a limit buy, at 09:04 it partially filled
at $64,450, at 09:07 I cancelled the remainder."

## Output Formats

### Single Order Analysis

```
## Execution Analysis — {market} {side} | {date} {time} UTC

**Order:** {type} {side} {size} @ {limit_price or "MARKET"} → submitted {time}
**Result:** {fill_pct}% filled — avg fill ${avg_fill} in {n_fills} fill(s)
**Time to first fill:** {ttff}ms

### Benchmarks
*Slippage convention: positive bps = paid more / received less than benchmark (unfavorable); negative bps = beat the benchmark (favorable). Applies to both BUY and SELL.*

| Benchmark | Price | Your Fill | Slippage | Rating |
|---|---|---|---|---|
| Arrival price | ${arrival} | ${avg_fill} | {arr_bps} bps | {rating} |
| Period VWAP | ${vwap} | ${avg_fill} | {vwap_bps} bps | {rating} |

### Fill Detail
| # | Time | Price | Size | Liquidity | Fee |
|---|---|---|---|---|---|
| 1 | {time} | ${price} | {size} | MAKER/TAKER | ${fee} |

**Execution Score: {score}/10 — {label}**
{1-2 sentence interpretation}
```

**Example:**

```
## Execution Analysis — BTC-USD-PERP BUY | 2026-04-16 09:02 UTC

**Order:** LIMIT BUY 0.20 BTC @ $64,500 → submitted 09:02:14
**Result:** 100% filled — avg fill $64,487 in 1 fill
**Time to first fill:** 830ms

### Benchmarks
| Benchmark | Price | Your Fill | Slippage | Rating |
|---|---|---|---|---|
| Arrival price | $64,510 | $64,487 | -3.6 bps | Excellent |
| Period VWAP | $64,520 | $64,487 | -5.1 bps | Excellent |

### Fill Detail
| # | Time | Price | Size | Liquidity | Fee |
|---|---|---|---|---|---|
| 1 | 09:02:15 | $64,487 | 0.20 BTC | MAKER | $0.00 |

**Execution Score: 9/10 — Excellent**
Limit order filled as maker, beating both arrival price and VWAP. Zero fees as maker.
```

### Session Replay

Always state the **explicit date range** in the header (e.g., "Mon Apr 21 – Sun Apr 27" or
"2026-04-23 09:00–10:00 UTC"). Never use vague labels like "This Week" or "Today"
without the actual dates.

```
## Order Replay — {market} | {start_date} {start_time} to {end_date} {end_time} UTC
*Slippage: positive = unfavorable (paid more / received less than arrival); negative = beat arrival. Same convention for BUY and SELL.*

### Timeline
09:02:14 | SUBMIT   | LIMIT BUY 0.20 BTC @ $64,500
09:02:15 | FILL     | 0.20 BTC @ $64,487 (MAKER) | slippage: -3.6 bps | fee: $0.00
09:15:00 | FUNDING  | -$0.82 paid (BTC-USD-PERP)
10:30:44 | SUBMIT   | LIMIT SELL 0.20 BTC @ $65,800
10:31:02 | FILL     | 0.10 BTC @ $65,800 (MAKER) | slippage: 0.0 bps | fee: $0.00
10:45:30 | CANCEL   | 0.10 BTC remaining | reason: user_cancelled

### Session Summary
| Metric | Value |
|---|---|
| Orders | 2 placed, 1 fully filled, 1 partially filled |
| Avg arrival slippage | -1.8 bps |
| VWAP beat | +3.2 bps |
| Maker ratio | 100% |
| Total fees | $0.00 |
| Session score | 9/10 — Excellent |
```

### Execution Quality Report

```
## Execution Quality Report — {period}

### Overall Score: {score}/10 — {label}

| Market | Orders | Fill Rate | Avg Slippage | VWAP Beat | Maker% | Score |
|---|---|---|---|---|---|---|
| BTC-USD-PERP | 12 | 92% | 8.2 bps | -3.1 bps | 58% | 7/10 |
| ETH-USD-PERP | 8 | 87% | 14.1 bps | +5.2 bps | 32% | 5/10 |

### Patterns Detected
- Market orders during 09:00–10:00 UTC showed 2.3x higher slippage than limit orders
- ETH fills consistently lagged VWAP — consider limit orders rather than market orders
- 3 orders were cancelled before filling — possible premature cancellation

### Recommendations
- Switch to limit orders for ETH entries to reduce taker slippage
- Avoid market orders during the first hour of the trading session
```

## Gotchas

- **Market orders always fill as TAKER** — a 0% maker ratio is expected and correct for
  market orders. Never flag it as a performance gap, never recommend switching to limit
  orders to "improve maker ratio" when analyzing market order fills.
- **Interpretation must give qualitative insight** — do not restate the numeric values
  already shown in the tables. Wrong: "You had 2.4 bps slippage and $7.70 arrival price."
  Right: "Excellent fill — your limit order timed the market well and avoided all fees."
- **SELL slippage sign** — the formula `(arrival_price − avg_fill_price)` gives positive
  bps when you sold BELOW arrival (unfavorable). Negative bps = sold above arrival (good).
  The convention is the same as BUY: positive = cost, negative = beat the benchmark.
- **Current-week assertions** — when simulating, use realistic dates consistent with the
  current context; do not invent specific historical weeks (e.g., "May 19–25, 2025") for
  a "this week" request.

## Caveats

- Arrival price is estimated from 1-minute klines — up to 1 minute of timing imprecision.
  Treat as a directional benchmark, not a precise quote.
- VWAP computed from `paradex_trades` market tape. If the tape is incomplete, VWAP may
  not reflect all volume.
- Multi-market or long-period analysis requires many API calls (klines + trades per market
  per window). Latency will be noticeable.
- Orders placed outside the analysis window but filled within it will appear as fills
  without a matching order record — included in fill analysis but not order reconstruction.
- Execution score doesn't account for market-specific liquidity. A 10 bps slippage in
  a thin market may represent excellent execution.
- For market orders, arrival slippage is expected taker spread — this is not a flaw.
- Retrospective analysis only — not trading advice.
- Personal accounts only.

See [benchmarks.md](references/benchmarks.md) for complete slippage formulas and execution score methodology.
