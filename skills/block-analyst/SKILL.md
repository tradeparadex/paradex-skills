---
name: paradigm-block-analyst
description: >
  Cross-venue analysis of Paradigm RFQ block trades using live market data from
  Deribit, OKX, and Bybit. Parses the trade JSON from the Paradigm block-trade
  tape, fetches live mark prices, IVs, and greeks from each venue, computes net
  portfolio greeks for multi-leg structures, benchmarks the fill against mark
  price cross-venue, reports how much of the structure has traded over the last
  24h / 7d / 30d and where else it printed (Paradigm, Deribit, OKX, Bullish,
  Paradex), reads whether the flow moved the vol surface, and outputs a concise
  analysis with full data-source trace. Use
  when the user pastes a Paradigm block trade JSON or asks to analyze, benchmark,
  or get market color on a specific Paradigm RFQ execution. Covers outright
  calls/puts (CL/PL), strangles (SN), straddles (ST), butterflies (BF), condors
  (CO), calendars (CA), risk reversals (RR), covered calls, and custom
  multi-leg combos (CM). Also handles perp combos with option and perp legs.
compatibility: No authentication required for market data. Works with
  deribit__get_ticker MCP (if available), web_fetch, or any injected DuckDB market
  data source. Falls back gracefully when venues are unreachable.
metadata:
  author: tradeparadex
  version: "2.0"
---

# Paradigm Block Trade Analyst

Cross-venue analysis of Paradigm RFQ executions against live Deribit, OKX, and
Bybit market data.

## Trigger

Fire when the user pastes a Paradigm block trade JSON object or references a
specific trade from the tape (e.g. "analyze this", "what's this trade doing",
"benchmark the fill", "pull live greeks").

## Step 1 — Parse the Trade

Extract from the JSON:

| Field | Use |
|---|---|
| `description` | Parse legs: direction (+ buy / - sell), ratio, instrument type, expiry, strike |
| `action` | Taker side: BUY = taker takes the structure as described; SELL = taker takes the opposite |
| `quantity` | Number of contracts |
| `price` | Fill price (in `quote_currency` units) |
| `mark_price` | Deribit mark at trade time |
| `displayValues.markOffset` | Fill vs mark: +/- premium |
| `index_price` | Spot at trade time. **Label this "Spot" in the output, never "Index".** |
| `strategy_code` | Structure type (see references/strategy-codes.md) |
| `rfqType` | `grfq` (multi-maker) or `drfq` (directed) |
| `venue` | `DBT` = Deribit, `BIT` = Bit.com, `OKX` = OKX |
| `product_codes` | `DO`/`EH` = BTC/ETH options; `DP`/`EP` = BTC/ETH perps |

**Leg parsing from `description`:**
- Format: `[+/-][ratio] [Type] [DD Mon YY] [Strike]`
- `+` = long, `-` = short; ratio is the leg multiplier
- Multiple legs separated by `\n`
- Single-leg trades: `description` is just the instrument name

**Taker side — resolve this FIRST and state it up front (it sets every greek sign):**
The taker's real position comes from the **leg-level `side` fields** plus the sign of
`strategy_delta` — these are authoritative. Each leg `side` is what the taker holds
(BUY = long that leg, SELL = short it); `strategy_delta` is computed from those same signs.
- The **top-level `side`/`action`** is the RFQ-quote-direction convention and can CONTRADICT the
  legs. Example: top-level `SELL` with both legs `BUY` and `strategy_delta` > 0 is a **long**
  straddle — taker is long vol, NOT short. When they disagree, trust the leg sides +
  `strategy_delta`. Resolve this **silently** and put only the plain conclusion in the header
  ("long straddle"). NEVER show the reasoning in the output — no "top-level SELL is
  quote-convention", no BUY/SELL leg mechanics. That logic is internal; the reader sees the verdict.
- Single-leg `description` is just the instrument name; for multi-leg, parse legs from the `legs`
  array (or `description`: `[+/-][ratio] [Type] [DD Mon YY] [Strike]`, one per line).

## Step 2 — Fetch Live Data

Use whatever data sources are available — query all reachable venues in parallel.
See `references/venues.md` for exact endpoints, instrument naming, and limitations.

**Deribit (primary):**
Preferred: `deribit__get_ticker` per leg (native MCP, fastest).
Fallback: `web_fetch` on `https://www.deribit.com/api/v2/public/ticker?instrument_name=<name>`,
or any injected DuckDB table with current Deribit marks.
Returns mark price, bid/ask, mark IV, delta, gamma, theta, vega, OI.

**OKX (secondary — fetch when Deribit venue or cross-venue benchmark needed):**
Use `web_fetch` on the opt-summary endpoint. Returns mark IV and greeks for all
strikes of an expiry. OKX uses different strike grids — find nearest strike(s)
and interpolate if exact strike absent. See `references/venues.md`.

**Bybit (tertiary — check availability, use market module):**
Follow Bybit skill Module Router: load `modules/market.md`, then call
`GET /v5/market/tickers?category=option&baseCoin=BTC&expDate=<DDMMMYY>`.
Bybit frequently does not list short-dated (<3 DTE) or illiquid strikes —
empty list is an expected result, not an error.

## Step 3 — Prior Prints & Flow Impact (last 30 days)

**This is the highest-value part of the analysis. ALWAYS run the fetches below — never
report "not checked" or defer them as optional.** The trader's first questions are: has this
structure printed before, is one taker accumulating, and is the flow moving the market? Answer
concretely with counts, sizes, levels, and impact.

**Match the STRUCTURE, not loose legs.** Recurrence means *this whole structure* printing
again — all legs together. A straddle is "the straddle", not "the call traded" + "the put
traded" separately; a spread is the spread, etc. Cluster prints by shared `block_trade_id` to
reconstruct prior packages and match the **full leg set** (strikes + expiries + ratios). A single
leg printing on its own is NOT a prior print of the structure — at most it's leg-level liquidity
context, worth a mention only if material. Never present "similar strike/expiry" single-leg
activity as if the structure recurred. (For genuine single-leg trades — `CL`/`PL` — the leg IS
the structure, so leg-level recurrence is the structure.)

Two sources, both mandatory every time:

### 3a — Paradigm prior blocks (most important)
Block recurrence on Paradigm is the strongest signal — a repeating block means a programmatic
or conviction taker, not random flow.
- **If a Paradigm block tape is injected** into the session (via a block-trade context tool or
  equivalent feed): scan it for prior blocks matching this structure — same `strategy_code` +
  same leg geometry (underlying, expiry pattern, strike/width or moneyness) within 30d. Report:
  count of matching blocks, size range, most recent (date + level + side), and whether one-sided
  (single taker building) or two-way.
- **If no Paradigm tape is injected** (e.g. running outside the Dime terminal): say so in one
  line and fall back to identifying Paradigm-routed prints on the Deribit tape (see 3b). Never
  fabricate block counts.

### 3b — Deribit tape, always fetch (public, no auth)
Per leg:
`web_fetch GET /api/v2/public/get_last_trades_by_instrument?instrument_name=<leg>&count=1000&start_timestamp=<now_ms − 30d>&end_timestamp=<now_ms>&sorting=desc`
(fall back to `count=100&sorting=desc` if the windowed pull returns nothing).

**Identify Paradigm / block prints on the tape:** each trade carrying a `block_trade_id` field
is a block trade — Paradigm-routed flow surfaces here as blocks (and multi-leg blocks share one
`block_trade_id` with `block_trade_leg_count` > 1). Trades with no `block_trade_id` are on-screen.
Split them: block prints on the same leg/strike are the strongest cross-confirmation of the same
flow when the native Paradigm tape isn't injected.

Per leg, capture: total prints, of which blocks, total contracts, most-recent timestamp (30d window).
Then **cluster the block prints by `block_trade_id` and match the full leg set against this trade's
structure** — report recurrence at the **structure level** (e.g. "this straddle blocked 3× in 30d,
all same-side"), not leg by leg. Loose single-leg prints that don't reconstruct into the structure
are context only.

**Always bucket the structure's volume by time — last 24h / 7d / 30d** (count of matching blocks +
total contracts in each), so the reader sees whether this is fresh flow today or a longer-running
program — e.g. "24h: 3 blocks / 85x · 7d: 5 / 140x · 30d: 7 / 180x".

### 3c — Flow impact (when the structure printed in multiple clips recently)
When a leg/structure has traded in several clips — especially same-day, same side — quantify the
accumulation footprint. Show clip-by-clip, and for **every clip include the traded vol and the spread**:
- **Clips:** each fill's time, size, and price.
- **Traded vol (IV):** use the `iv` field Deribit returns on each trade. Show per clip so vol drift is visible.
- **Spread:** bid/ask width around each clip. Use current ticker for live spread; report in premium units and/or bps.
- **The read:** state whether **vol and spread are widening or tightening** across clips.

Keep the output tight — one or two lines / a small table.

### 3d — Where else did it trade (required, reported compactly)
After Paradigm/Deribit, check **OKX**, **Bullish**, **Paradex**, and Bybit if relevant.
Report as **ONE compact line**: venues where it actually printed (with rough size), then "not seen on X/Y" for the rest.

## Step 4 — Compute Net Greeks

Apply leg ratios to per-instrument greeks. For taker side `SELL`, flip signs.

Report net greeks **scaled to the full position** (× quantity):
- **delta** in coin (BTC/ETH)
- **vega** in $ per vol point
- **theta** in $ per day
- **gamma** in coin per $ move
- **vanna** — approximate only; never present as an exact API figure.

Never label theta or vega in "BTC/day" — theta and vega are USD; only Δ is coin.

## Step 5 — IV Skew, Surface & Vol-Surface Impact

- Per-leg IV: Deribit mark IV (primary), OKX mark IV (secondary)
- IV differential between legs; cross-venue IV spread (flag if >2 vol points)
- Vol-surface impact when trade had size / multiple clips: compare traded strikes' IV and expiry ATM/skew now vs before the flow. State in one line.

## Step 6 — P&L Mark (if position is live / follow-up analysis)

Only compute P&L when asked or when the trade was previously analyzed in session.

## Step 7 — Output Format

**Your ENTIRE response is the block shown below.** Work silently — no narration, no preamble.

Two lines of plain text (header + view), then a single `yaml` code block with the four bracket rows.
The `yaml` fence renders the data block in blue/teal in the terminal while keeping the header scannable outside.

---

**Shape to mirror:**

**BTC Put Calendar 60k · long Jun26 / short Sep26 · ×12.5 | Seller | Recd 0.0451 (~$35.4k) | −22 bps vs mark**

Spot 62,728 · 60k −4.3% OTM · long near-Γ / short far-vega · max loss at 60k Jun expiry · grfq/DBT

```yaml
[Greeks]   Δ +0.70 BTC (+5.6%) · Vega −$985/v · Γ long (near) · Θ −$423/d
[Fair]     −22 bps vs mark · Jun60P 46.9v / Sep60P 43.8v · near-far spread 3.0v
[History]  6× 60k PCal today — 2×25 BUY → 4×12.5 SELL, two-way @ ~0.0450 · Jun IV 47.3→46.9v · OI Jun 5,225 / Sep 3,644
[Live]     Jun60P 0.0220/0.0230 · Sep60P 0.0660/0.0675 · cal screen ~0.0443 mid · fill +18 bps above
```

---

**Line 1 — Header:**
`<COIN> <EXPIRY DDMMMYY> <strikes k/k> <ratio a×b> <Structure> | <Buyer|Seller> | <size/leg> BTC | <Paid|Recd> <price> <±N bps> <above|below> mark`
- Plain structure name ("Call Ratio", "Straddle", "Risk Reversal") — never the raw code.
- `Buyer` if taker paid net debit, `Seller` if they took in net credit.
- Size **per leg in coin** = block qty × each leg ratio.

**Line 2 — View:**
`<spot + moneyness> · <exposure in greek shorthand> · <key level> · <flow type>`
- Tokens separated by ` · `, no full sentences. Include any uncapped / naked-risk level.

**The four bracket rows inside the `yaml` block — each exactly one line:**
- `[Greeks]`  net, scaled to position: `Δ <coin> (<%>)` · `Vega <±$/v>` · `Γ <val>` · `Θ <±$/d>` · `Vanna <~val>` (only if non-trivial)
- `[Fair]`    `<±bps> vs mark` · per-leg vol · net spread/edge. Surface move as one token if applicable.
- `[History]` recurrence verdict · 24h/7d/30d buckets · `OI <val>`. Terse verdict only.
- `[Live]`    per-leg `<bid>/<ask>` · screen mid · fill vs screen in bps.

**Rules:**
- Drop a bracket row only if its data is genuinely unavailable — never pad, never invent.
- Δ as the triangle symbol; theta & vega are USD ($/v, $/d), only Δ is coin.
- Resolve Buyer/Seller and long/short from leg sides + `strategy_delta` silently — state only the verdict.
- Cite only real `block_trade_id`s; never invent a `combo_id`.

## Notes

- For perp legs (`product_codes` includes `DP`/`EP`): fetch `BTC-PERPETUAL` / `ETH-PERPETUAL` mark; delta = ±1.0 per contract.
- OKX uses USDC-margined options (`BTC-USD_UM`); flag greek differences when relevant.
- See `references/venues.md` for instrument naming, endpoint quirks, and known gaps.
