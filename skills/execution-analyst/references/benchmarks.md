# Execution Benchmarks Reference

Complete formulas, derivations, and scoring methodology for the Execution Analyst skill.

---

## 1. Slippage Formula Derivation

### Why the formula flips for BUY vs. SELL

Slippage measures cost relative to a reference price — but "cost" is directional:

- **BUY**: you want the lowest fill price possible. Paying *more* than the reference is bad.
  Cost = `avg_fill_price - reference_price` (positive = overpaid)
- **SELL**: you want the highest fill price possible. Receiving *less* than the reference is bad.
  Cost = `reference_price - avg_fill_price` (positive = undersold)

Normalizing to basis points and unifying sign convention (positive = bad, negative = good):

```
# BUY
slippage_bps = (avg_fill_price - reference_price) / reference_price × 10000

# SELL
slippage_bps = (reference_price - avg_fill_price) / reference_price × 10000
```

**Symmetry proof.** Suppose a buy and sell both fill exactly at the reference price:
- BUY: `(ref - ref) / ref × 10000 = 0` ✓
- SELL: `(ref - ref) / ref × 10000 = 0` ✓

Suppose a buy fills 5 bps above reference, a sell fills 5 bps below reference (same
adverse scenario for both sides):
- BUY: `ref × 1.0005 - ref) / ref × 10000 = +5` bps ✓ (bad)
- SELL: `(ref - ref × 0.9995) / ref × 10000 = +5` bps ✓ (bad)

Both directions produce the same positive-bad / negative-good convention. The formula
flip is required to preserve this symmetry.

### Reference denominator

Use the **reference price** (arrival or VWAP) in the denominator, not the fill price.
The reference is the benchmark — you're measuring deviation from it. Using fill price
as the denominator would introduce circularity and produce asymmetric results when
fills deviate significantly from reference.

### Multi-fill weighted average

When an order produces multiple fills, always compute `avg_fill_price` first, then
apply slippage once:

```
avg_fill_price = sum(fill_i.size × fill_i.price) / sum(fill_i.size)
slippage_bps = (avg_fill_price - arrival_price) / arrival_price × 10000  # BUY
```

Do not average per-fill slippage values — this is incorrect because it ignores
fill size weighting.

---

## 2. VWAP Computation from Trade Tape

### Standard formula

```
market_vwap = sum(trade_i.size × trade_i.price) / sum(trade_i.size)
```

This is the volume-weighted mean fill price for all market participants over the window.

### Edge cases

**Empty tape** (`paradex_trades` returns no records for the window):
- VWAP is undefined. Do not show VWAP benchmark for this window.
- Common cause: very short windows (<1 minute), illiquid markets, or API history limits.
- Fallback: use mid-price from `paradex_bbo` at the analysis start time if available,
  and label it "BBO mid (no tape)" instead of VWAP.

**Very short periods (<5 minutes)**:
- VWAP computed from a handful of trades is noisy — a single large print dominates.
- If `count(trades) < 10`, note: "VWAP based on {N} trades — limited sample."
- Still compute it, but flag low confidence.

**Partial tape (gap in records)**:
- If timestamps show gaps >5 minutes within the window, VWAP may understate volume.
- Flag: "VWAP may be incomplete — gap detected in trade tape from {time} to {time}."

**Mixed sides** (trades table includes both buys and sells):
- Include all trades regardless of side — market VWAP represents the consensus price
  all participants paid, not just one side.

### VWAP window alignment

Use the same window as the analysis period (`start_unix_ms` / `end_unix_ms`). For
single-order analysis, align the window to the order lifetime: from `order.created_at`
to the last fill timestamp (or cancellation timestamp). This gives a VWAP that reflects
market conditions during the actual execution window, not a broader session.

---

## 3. Arrival Price Estimation from 1-Min Klines

### Which candle to use

The arrival price proxy uses the last completed 1-minute candle before order submission.

**Timing diagram:**

```
Candles:   [08:59–09:00)   [09:00–09:01)   [09:01–09:02)
                 ↑                               ↑
           close = $X                     close = $Y

Order submitted at 09:01:47
→ Use candle [09:01–09:02)? No — it hasn't closed yet at 09:01:47.
→ Use candle [09:00–09:01)? Yes — last closed candle at or before 09:01:47.
   Its close ($X) is the arrival price proxy.
```

**Rule**: find the candle whose `open_time ≤ order.created_at` and pick the one with
the largest `open_time` satisfying this constraint. Use its `close` price.

### API call strategy

```
paradex_klines(
    market_id=market,
    resolution=1,           # 1-minute candles
    start=order.created_at - 60_000,   # one candle before
    end=order.created_at + 120_000     # buffer for clock skew
)
```

For a batch of N orders in the same market, fetch a single window covering all orders:
```
start = min(order.created_at) - 60_000
end   = max(order.created_at) + 120_000
```
Then look up each order's candle with a binary search on the returned array.

### Edge cases

**Order at session open (very first candle)**:
- No prior candle exists. Use the `open` of the first available candle instead.
- Label it "session open proxy" rather than "arrival price."

**Order placed during a data gap**:
- If no candle exists within 5 minutes of `order.created_at`, arrival price is
  unavailable. Skip arrival benchmark for this order and note the gap.

**Clock skew**:
- Exchange timestamps and kline timestamps may differ by up to a few seconds.
  The ±60s window handles normal skew. The candle selection rule (last closed
  candle before the order) is conservative — it will never use a future candle.

**Precision limitation**:
- 1-minute klines mean the arrival proxy can be up to 60 seconds stale.
  If the market moved sharply during that minute, the proxy will be off.
  This is a known limitation. The arrival benchmark is directional, not exact.

---

## 4. Execution Score Rubric

### Factor definitions and scoring

#### Factor 1: Arrival Slippage (weight 35%)

Measures fill quality versus the price at order submission.

| Slippage (bps) | Raw Score | Notes |
|---|---|---|
| ≤ 0 (negative) | 10 | Filled better than arrival — maker rebate or favorable move |
| 0 | 10 | |
| 2 | 9.6 | |
| 5 | 9 | |
| 10 | 8 | |
| 15 | 7 | Acceptable threshold |
| 20 | 6 | |
| 25 | 5 | |
| 30 | 4 | |
| 40 | 2 | |
| 50+ | 0 | |

Formula: `arrival_score = max(0, 10 - slippage_bps / 5)`

#### Factor 2: Fill Completeness (weight 25%)

Measures what fraction of the intended order was actually executed.

| Fill Rate | Raw Score | Notes |
|---|---|---|
| 100% | 10.5 → capped at 10 | |
| 95% | 10 | Threshold for "complete" |
| 80% | 8.4 | |
| 60% | 6.3 | |
| 50% | 5.3 | |
| 30% | 3.2 | Threshold for "mostly missed" |
| 10% | 1.1 | |
| 0% | 0 | |

Formula: `completeness_score = min(10, fill_rate_pct / 9.5)`

#### Factor 3: VWAP Comparison (weight 20%)

Measures fill quality versus market consensus price during the window. A score of 5
means you filled exactly at VWAP — neither better nor worse than average.

| VWAP Delta (bps) | Raw Score | Notes |
|---|---|---|
| +15 (beat by 15 bps) | 10 | |
| +9 | 8 | |
| +3 | 6 | Slight beat |
| 0 (at VWAP) | 5 | Average execution |
| -3 | 4 | Slight miss |
| -9 | 2 | |
| -15 | 0 | |
| < -15 | 0 (capped) | |

Formula: `vwap_score = min(10, max(0, 5 + vwap_delta_bps / 3))`

Positive `vwap_delta_bps` = beat VWAP (BUY filled below VWAP or SELL filled above).

#### Factor 4: Maker Ratio (weight 10%)

Maker fills pay no fee (or receive a rebate) and indicate the order rested in the
book, providing liquidity rather than consuming it.

| Maker % | Raw Score | Notes |
|---|---|---|
| 100% | 10 | All fills as maker |
| 70% | 7 | |
| 50% | 5 | |
| 30% | 3 | |
| 0% | 0 | All taker |

Formula: `maker_score = maker_ratio_pct / 10`

#### Factor 5: Price Walk (weight 10%, multi-fill orders only)

Measures adverse price movement across multiple fills — did the market move against
you as you were filling? Only computed when `n_fills >= 2`.

| Price Walk (bps) | Raw Score | Notes |
|---|---|---|
| ≤ 0 (favorable) | 10 | Market moved in your favor while filling |
| 2 | 9.3 | |
| 5 | 8.3 | |
| 10 | 6.7 | |
| 15 | 5 | |
| 20 | 3.3 | |
| 30 | 0 | |
| > 30 | 0 (capped) | Heavy market impact |

Formula: `walk_score = max(0, 10 - price_walk_bps / 3)`

For single-fill orders, assign `walk_score = 10` (no adverse walk possible).

### Composite score formula

```
score = round(
    arrival_score    × 0.35 +
    comp_score       × 0.25 +
    vwap_score       × 0.20 +
    maker_score      × 0.10 +
    walk_score       × 0.10
)
```

### Score labels

| Score | Label | Interpretation |
|---|---|---|
| 9–10 | Excellent | Near-optimal execution |
| 7–8 | Good | Above average; minor room for improvement |
| 5–6 | Fair | Average or below; identifiable inefficiencies |
| 3–4 | Poor | Significant execution drag |
| 1–2 | Very Poor | Substantial cost vs. benchmark |

### Worked example

**Order:** LIMIT BUY 1.0 ETH, two fills:
- Fill 1: 0.6 ETH @ $3,200 (TAKER), 200ms after submit
- Fill 2: 0.4 ETH @ $3,205 (TAKER), 800ms after submit

**Arrival price:** $3,198 (1-min close at submit time)
**Period VWAP:** $3,202

```
avg_fill_price = (0.6 × 3200 + 0.4 × 3205) / 1.0 = $3,202
fill_rate_pct  = 1.0 / 1.0 × 100 = 100%
price_walk_bps = (3205 - 3200) / 3200 × 10000 = 15.6 bps (adverse for BUY)
maker_ratio_pct = 0%

# Arrival slippage (BUY)
arrival_slippage_bps = (3202 - 3198) / 3198 × 10000 = 12.5 bps

# VWAP delta (BUY: below VWAP = good)
vwap_delta_bps = (3202 - 3202) / 3202 × 10000 = 0.0 bps (exactly at VWAP)

# Factor scores
arrival_score = max(0, 10 - 12.5 / 5) = 7.5
comp_score    = min(10, 100 / 9.5)    = 10.0
vwap_score    = min(10, max(0, 5 + 0 / 3)) = 5.0
maker_score   = 0 / 10                = 0.0
walk_score    = max(0, 10 - 15.6 / 3) = 4.8

# Composite
score = round(7.5×0.35 + 10×0.25 + 5×0.20 + 0×0.10 + 4.8×0.10)
      = round(2.625 + 2.5 + 1.0 + 0 + 0.48)
      = round(6.605) = 7

Label: Good
```

---

## 5. Market Order vs. Limit Order Scoring Adjustment

Market orders are designed to execute immediately at the prevailing market price.
Arrival slippage on a market order is expected to equal approximately the taker
half-spread — this is not a failure of execution. Including arrival slippage in the
score for market orders would unfairly penalize a deliberate choice.

### Weight redistribution for market orders

| Factor | Limit Order Weight | Market Order Weight | Rationale |
|---|---|---|---|
| Arrival slippage | 35% | 0% | Expected taker cost — not a quality signal |
| Fill completeness | 25% | 40% | Critical: market orders should fill fully |
| VWAP comparison | 20% | 35% | Primary quality signal for market orders |
| Maker ratio | 10% | 0% | Market orders are always taker by design |
| Price walk | 10% | 25% | Indicates how much impact the order had |

Revised formula for market orders:
```
score = round(
    comp_score × 0.40 +
    vwap_score × 0.35 +
    walk_score × 0.25
)
```

### Identifying market orders

A market order has `order.type == "MARKET"` or no `order.price` (null/empty). For
stop-market orders (`order.type == "STOP_MARKET"`), apply the same market order weighting.

---

## 6. Pattern Detection Heuristics

A "pattern" is worth flagging when it appears in ≥3 data points and shows a
statistically meaningful deviation from baseline. Below are the heuristics used.

### Order type slippage gap

**Condition:** average arrival slippage for market orders > 2× average arrival
slippage for limit orders, with ≥3 market orders and ≥3 limit orders in the sample.

**Flag text:** "Market orders showed {X}x higher slippage than limit orders
({market_bps} bps vs. {limit_bps} bps) — consider limit orders for entries."

### Time-of-day clustering

**Condition:** divide the session into 1-hour UTC buckets. If any bucket's average
slippage is ≥2× the session average and contains ≥3 orders, flag it.

**Flag text:** "Orders during {HH:00–HH:59} UTC had {X}x higher slippage than
other periods ({bucket_bps} bps vs. {session_avg} bps)."

Common cause: first hour of major sessions (00:00, 08:00, 13:00 UTC) can be volatile.

### Consistent VWAP miss

**Condition:** ≥75% of fills for a given market missed VWAP (negative `vwap_delta_bps`)
with a mean miss of >5 bps, across ≥4 fills.

**Flag text:** "{market} fills consistently lagged VWAP by {mean_miss} bps
({n}/{total} fills below VWAP) — consider limit orders or passive entry timing."

### Premature cancellation

**Condition:** ≥2 orders cancelled with `fill_rate_pct < 20%` and the market
subsequently moved to within the limit price (i.e., the fill would have occurred
within 5 minutes of cancellation).

**Detection:** after identifying cancelled orders, check `paradex_klines` for the
5-minute window after cancellation. If price crossed the limit price, flag it.

**Flag text:** "{N} orders cancelled before filling — market reached the limit
price within 5 minutes of cancellation in {M} of those cases. Consider longer
time-in-force before cancelling."

### Adverse price walk concentration

**Condition:** orders with `n_fills >= 3` show mean `price_walk_bps > 20` bps.

**Flag text:** "Multi-fill orders showed significant adverse price walk (avg {X} bps)
— large orders may be moving the market. Consider splitting into smaller child orders."

### Fee concentration (fastfill absence)

**Condition:** `fastfill_count / total_fills < 0.10` when the account has placed
≥10 limit orders that filled as taker.

**Flag text:** "Less than 10% of fills used FastFill — consider enabling FastFill
for a 30% fee discount on taker fills."

This heuristic is only surfaced if `fastfill_count` data is available in fill flags.
