---
name: paradex-options-pricer
description: >
  Options chain viewer, greek calculator, and sell-candidate ranker for Paradex
  option markets. Fetches all listed options for BTC and/or ETH, computes
  delta/gamma/theta/vega in-context using Black-Scholes with the exchange mark IV,
  displays the options chain grouped by expiry, analyses IV skew (put-call premium
  differential at same |delta|), and ranks sell candidates by premium
  attractiveness, IV level, and liquidity. Use when the user asks about option
  pricing, the options chain, greeks, implied volatility, IV skew, which options
  to sell, volatility surface, "options overview", "scan for options to sell",
  "what calls/puts are available", "compute greeks for a strike", "rank sell
  candidates", "find the 25-delta put", "what is the ATM IV", "price the 90K call",
  or any question about individual option prices, volatility, or greeks on Paradex.
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.1"
---

# Paradex Options Pricer

Scans the Paradex option universe, computes greeks in-context, surfaces IV skew, and ranks sell candidates.

## Available MCP Tools

| Tool | What it provides |
|------|-----------------|
| `paradex_markets` | Full list of option markets: strike, expiry, asset_kind, order_size_increment |
| `paradex_market_summaries` | Mark price, mark IV, delta, open interest, 24h volume per market |
| `paradex_bbo` | Live best bid/ask for spread calculation |

## Greek Computation

All greeks are computed in-context using the exchange-reported `mark_iv` (sigma) and current underlying price.

### Inputs per option

```
S      = underlying_price   (mark price of BTC-USD-PERP or ETH-USD-PERP)
K      = strike_price       (from market symbol or market spec)
T      = (expiry_utc − now) / YEAR_IN_DAYS   (time to expiry in years; YEAR_IN_DAYS = 365)
r      = 0.0                (default; use perp funding rate if available)
sigma  = mark_iv            (from paradex_market_summaries, decimal — e.g. 0.65 for 65%)
```

### Black-Scholes formulas

```
d1 = ( ln(S/K) + (r + 0.5 × sigma²) × T ) / ( sigma × sqrt(T) )
d2 = d1 − sigma × sqrt(T)

Call price = S × N(d1)  − K × e^(−r×T) × N(d2)
Put  price = K × e^(−r×T) × N(−d2) − S × N(−d1)

Delta_call = +N(d1)
Delta_put  =  N(d1) − 1

Gamma = N'(d1) / ( S × sigma × sqrt(T) )          [same for calls and puts]
Vega  = S × N'(d1) × sqrt(T)                       [divide by 100 for $/1% IV move]
Theta = −[ S × N'(d1) × sigma / (2 × sqrt(T))
           + r × K × e^(−r×T) × N(cp × d2) ] / YEAR_IN_DAYS
        (cp = +1 for calls, −1 for puts; result is $/calendar day)
```

Where `N(x)` is the cumulative standard normal CDF and `N'(x) = exp(−x²/2) / sqrt(2π)`.

Use `mark_iv` from `paradex_market_summaries` as sigma. Skip options where `mark_iv == 0` or missing.

See [references/black-scholes.md](references/black-scholes.md) for derivations and normalization conventions.

## Parsing Option Symbols

Paradex option symbols follow the pattern `{UL}-USD-{EXPIRY}-{STRIKE}-{C|P}`, e.g. `BTC-USD-8MAY26-90000-C`.

```
underlying = parts[0]           # BTC or ETH
expiry_str = parts[2]           # e.g. "8MAY26"  → datetime(2026,5,8, 8,0, UTC)
strike     = float(parts[3])    # e.g. 90000.0
is_call    = parts[4] == "C"
DTE        = (expiry_utc − now).total_seconds() / 86400  (rounded to nearest int)
```

Expiry time is always **08:00 UTC** on the expiry date.

## Workflows

### 1. Options Chain View

*Trigger: "show me the BTC options chain", "what options are listed for ETH", "options overview"*

1. Call `paradex_markets` — filter `asset_kind == "OPTION"` for the requested underlying
2. Call `paradex_market_summaries` — fetch `mark_iv`, `mark_price`, `delta`, `open_interest`, `volume_24h` for every option
3. Fetch `paradex_market_summaries` for the underlying perp (e.g. `BTC-USD-PERP`) to get `S`
4. Compute delta, gamma, theta, vega in-context for each option
5. Group by expiry date; within each group sort strikes ascending, interleave calls and puts at each strike
6. Present one table per expiry:

```
## BTC Options Chain — 8 May 2026  (14 DTE)

| Strike  | Type | Mark    | IV    | Delta  | Gamma    | Theta/day | Vega/1% | OI     |
|---------|------|---------|-------|--------|----------|-----------|---------|--------|
| 75,000  | C    | 8,420   | 62.1% | +0.820 | 0.000012 | −42.10    | 0.48    | 12.4   |
| 75,000  | P    | 180     | 65.3% | −0.180 | 0.000012 | −18.40    | 0.48    | 5.1    |
| 80,000  | C    | 4,810   | 58.3% | +0.650 | 0.000021 | −55.30    | 0.62    | 44.1   |
| 80,000  | P    | 560     | 61.5% | −0.350 | 0.000021 | −42.10    | 0.62    | 38.7   |
| 85,000  | C    | 2,240   | 55.2% | +0.440 | 0.000028 | −60.80    | 0.69    | 88.2   |
| 85,000  | P    | 1,950   | 58.7% | −0.560 | 0.000028 | −58.20    | 0.69    | 64.3   |
```

Add a DTE label in the expiry header. Show "OTM" / "ITM" badge if helpful.

### 2. IV Skew Analysis

*Trigger: "IV skew for BTC", "put-call skew", "volatility surface", "which expiry has most skew"*

1. Fetch the full chain as in Workflow 1
2. For each expiry find the option pairs nearest the 25-delta and 10-delta marks (call and put)
3. Compute:

```
ATM IV       = IV of the option with |delta| closest to 0.50
25Δ skew     = IV(25Δ put) − IV(25Δ call)     [positive = puts richer]
25Δ butterfly = 0.5 × (IV(25Δ put) + IV(25Δ call)) − ATM IV
```

4. Present a skew summary table:

```
## BTC IV Skew Summary

| Expiry     | DTE |  ATM IV | 25Δ Skew | 25Δ Butterfly | Put premium |
|------------|-----|---------|----------|---------------|-------------|
| 8-MAY-26   |  14 |  56.8%  |  +6.3%   |    +2.1%      | Puts +11.1% |
| 30-MAY-26  |  36 |  54.2%  |  +4.8%   |    +1.8%      | Puts +8.9%  |
| 27-JUN-26  |  64 |  52.1%  |  +3.9%   |    +1.5%      | Puts +7.5%  |
```

State skew values factually. Do not recommend trading the skew.

### 3. Sell Candidate Ranking

*Trigger: "rank options to sell", "best options to sell", "find options with high IV", "scan sell candidates"*

Score every option across four factors (each 0–10, equal weight):

| Factor | What it measures | Scoring |
|--------|-----------------|---------|
| **IV level** | IV relative to min/max IV in the chain | `(IV − IV_min) / (IV_max − IV_min) × 10` |
| **Theta/Vega ratio** | Time decay earned per unit of vol risk | `min(|theta| / vega × scale, 10)` |
| **DTE suitability** | Theta sweet spot | Score 10 at 21–45 DTE; scales down towards 0 at 3 DTE and at 90+ DTE |
| **Liquidity** | Bid-ask spread width | Fetch `paradex_bbo` for each candidate; `spread_pct = (ask - bid) / mark_price × 100`. Score: `max(0, 10 - spread_pct × 5)` — 0% spread = 10, 2% spread = 0. Fallback to OI score if BBO unavailable: `min(OI / OI_p75 × 5, 10)` |

Composite score = average of four factor scores.

Pre-filter: exclude options where `mark_iv == 0`, `mark_price < 10 USDC`, `OI == 0`, or `DTE < 3`.

Present top 10 ranked:

```
## Top Option Sell Candidates

| Rank | Market                    | Ty | Strike |  DTE | Mark  |  IV   | Delta  | Theta | Vega | OI   | Score |
|------|---------------------------|----|--------|------|-------|-------|--------|-------|------|------|-------|
|  1   | BTC-USD-8MAY26-90000-C    | C  | 90,000 |  14  | 1,820 | 64.2% | +0.282 | −85   | 0.62 | 88.2 |  8.4  |
|  2   | BTC-USD-8MAY26-78000-P    | P  | 78,000 |  14  | 1,650 | 70.1% | −0.241 | −78   | 0.58 | 71.3 |  8.1  |
|  3   | ETH-USD-8MAY26-1800-C     | C  |  1,800 |  14  |    82 | 71.3% | +0.310 |  −5.2 | 0.04 | 42.1 |  7.9  |
```

Always append:
> **Note**: Score reflects premium-selling attractiveness (IV, theta/vega, DTE, spread liquidity). Spread width is measured at query time — re-run before placing orders as liquidity can change rapidly. Margin impact requires **pm-analyzer** before placing any sell.

### 4. Single Option Pricing

*Trigger: "price the BTC 90K call", "greeks for the ETH 1800 put", "what is the 85K strike worth"*

1. Resolve description to a Paradex market symbol (call `paradex_markets` if needed)
2. Fetch `paradex_market_summaries` for the option and its underlying perp
3. Compute all four greeks in-context
4. Fetch `paradex_bbo` for the bid/ask spread
5. Present a greek card:

```
## BTC-USD-8MAY26-90000-C

Underlying: $84,250  |  Expiry: 8 May 2026 08:00 UTC  |  DTE: 14  |  Strike: $90,000  |  OTM by $5,750

| Metric     | Value            |
|------------|------------------|
| Mark price | $1,820.00        |
| Bid / Ask  | $1,800 / $1,840  |
| Spread     | $40 (2.2%)       |
| Mark IV    | 64.2%            |
| Delta (Δ)  | +0.282           |
| Gamma (Γ)  | +0.0000182       |
| Theta (Θ)  | −$85.20 / day    |
| Vega  (V)  | $62.40 / 1% IV   |
| OI         | 88.2 contracts   |

Intrinsic value: $0.00  |  Time value: $1,820.00
```

## Output Conventions

- **DTE**: `ceil((expiry_utc − now).total_seconds() / 86400)`, minimum 0
- **Theta**: $/calendar day (negative for long options — time decay works against buyers)
- **Vega**: $/1 percentage point of IV move (raw vega ÷ 100)
- **IV**: displayed as a percentage (64.2%, not 0.642)
- **Delta**: show sign — positive for calls (`+0.282`), negative for puts (`−0.241`)
- **OI**: in contracts (one contract = one option on one unit of underlying)
- Strikes formatted with comma thousands separator; no decimals for round strikes
- Market symbols shown in full (e.g. `BTC-USD-8MAY26-90000-C`)

## Caveats

- **Greeks are model-dependent**: computed from Black-Scholes with exchange mark IV; for illiquid or deep ITM/OTM options, model and market greeks can diverge materially.
- **IV changes fast**: mark IV can move significantly intraday — re-run before acting.
- **No margin computation**: this skill prices options and reports greeks only. Margin impact of selling requires **pm-analyzer**.
- **Sell candidate score is heuristic**: it reflects premium characteristics, not risk-adjusted return or portfolio fit. A high score does not imply a good trade.
- **Liquidity scoring**: uses real-time bid-ask spread width as the primary signal. OI is used only as a filter (zero-OI exclusion). High OI with wide spreads = poor practical liquidity for entry and exit.
- This skill computes option analytics only. It does not recommend buying or selling.
