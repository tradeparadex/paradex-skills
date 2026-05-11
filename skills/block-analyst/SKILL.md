---
name: paradigm-block-analyst
description: >
  Cross-venue analysis of Paradigm RFQ block trades using live market data from
  Deribit, OKX, and Bybit. Parses the trade JSON from the Paradigm block-trade
  tape, fetches live mark prices, IVs, and greeks from each venue, computes net
  portfolio greeks for multi-leg structures, benchmarks the fill against mark
  price cross-venue, and outputs a structured analysis with full data-source
  trace. Use when the user pastes a Paradigm block trade JSON or asks to analyze,
  benchmark, or get market color on a specific Paradigm RFQ execution. Covers
  outright calls/puts (CL/PL), strangles (SN), straddles (ST), butterflies (BF),
  condors (CO), calendars (CA), risk reversals (RR), covered calls, and custom
  multi-leg combos (CM). Also handles perp combos with option and perp legs.
compatibility: No authentication required for market data. Works with
  deribit__get_ticker MCP (if available), web_fetch, or any injected DuckDB market
  data source. Falls back gracefully when venues are unreachable.
metadata:
  author: tradeparadex
  version: "1.2"
---

# Paradigm Block Trade Analyst

Cross-venue analysis of Paradigm RFQ executions against live Deribit/OKX/Bybit.

## Trigger

User pastes a Paradigm block trade JSON or references a trade from the tape.

## Step 1 — Parse

Extract `description` (legs), `action` (BUY = take legs as signed; SELL = flip
all signs), `quantity`, `price`, `mark_price`, `index_price`, `strategy_code`,
`venue`. Leg format: `[+/-][ratio] [Type] [DD Mon YY] [Strike]`, legs split by
`\n`. See `references/strategy-codes.md`.

## Step 2 — Fetch Live Data (parallel)

- **Deribit (primary):** `deribit__get_ticker` per leg, else `web_fetch` on
  `https://www.deribit.com/api/v2/public/ticker?instrument_name=<name>`.
- **OKX (secondary):** `web_fetch` opt-summary for cross-venue IV.
- **Bybit (tertiary):** market module ticker; empty for <3 DTE is expected.

See `references/venues.md` for instrument naming and endpoint details. Note
unreachable venues in the trace and proceed.

## Step 3 — Historical Frequency (last 90 days)

Search the Paradigm block-trade tape (or any injected trade-history source) for
prior fills with the same `strategy_code` and matching leg structure (same
expiry pattern, strike geometry, and underlying) over the last 90 days. Report:

- Count of matching trades and rough notional range
- Most recent occurrence (date + fill vs mark)
- Whether this structure is recurring flow or a one-off

If no historical source is available, state "tape history unavailable" in the
trace and skip — do not fabricate counts.

## Step 4 — Compute Net Greeks

`net_greek = Σ (taker_sign × leg_ratio × instrument_greek)`, scale by quantity.
Report delta, gamma, theta ($/day), vega.

## Step 5 — IV & Cross-Venue

Per-leg mark IV (Deribit primary, OKX secondary). Flag cross-venue spread
>2 vol points. Note whether taker bought or sold the higher-IV leg.

## Step 6 — P&L Mark (only if asked or follow-up)

```
structure_value_now = Σ (taker_sign × leg_ratio × current_mark)
mark_pnl_per_unit   = structure_value_now - fill_price
total_pnl           = mark_pnl_per_unit × quantity × spot
```

## Step 7 — Output

1. **Structure** — legs table
2. **Market Context** — spot, moneyness, DTE
3. **Live Greeks** — per-leg + net position
4. **IV** — per-leg, cross-venue, skew read
5. **History (90d)** — frequency, last occurrence, recurring vs one-off
6. **View** — directional/vol thesis (marked as inference)
7. **Sizing** — notional, premium, mark offset, execution quality
8. **Data Trace** — source per data point

Keep prose tight. Tables over paragraphs.

## Notes

- Perp legs (`DP`/`EP`): delta = ±1.0 per contract; fetch `*-PERPETUAL` mark.
- OKX USDC-margined options: greeks may differ slightly from coin-margined
  Deribit; flag when relevant.
- If a venue or history source is unreachable, note in trace and continue.
