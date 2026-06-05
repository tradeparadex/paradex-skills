---
name: paradex-options-recap
description: >
  Market recap for crypto options flow over a user-specified time window, invoked
  via /recap. Parses the command syntax "/recap [asset] [options|perps|all] [window]"
  (e.g. "/recap btc options 8h", "/recap eth options 24h", "/recap btc options 1d").
  Produces: DVOL/spot summary, largest block prints with greeks and IV context,
  screen flow themes, vol surface reads, and a net bias call. Use when the user
  types /recap or asks for a market recap, options flow summary, vol surface snapshot,
  "what happened in BTC options", "give me the last Xh of flow", "what's been trading",
  or any broad options market summary question. Distinct from paradigm-block-analyst
  (single trade deep-dive) and paradex-market-analyst (Paradex perp technicals) —
  this skill is about the OPTIONS MARKET as a whole over a period, not a single trade.
compatibility: Uses Deribit public API (web_fetch), Paradigm block tape (if injected),
  and deribit__get_ticker MCP (if available). No authentication required.
metadata:
  author: tradeparadex
  version: "1.0"
---

# Options Recap

Turns raw options flow — block prints, screen activity, and vol surface data — into
a concise market recap for a user-specified window. Answers "what happened in the options
market over the last N hours?" with facts: DVOL, spot range, block structures, screen themes,
and a surface read.

## Trigger — Command Syntax

Fire when the user types `/recap` or a close paraphrase. Parse the command as:

```
/recap [asset] [market_type] [window]
```

| Token | Examples | Default |
|---|---|---|
| `asset` | `btc`, `eth`, `sol` | `btc` |
| `market_type` | `options`, `opts`, `perps`, `all` | `options` |
| `window` | `1h`, `4h`, `8h`, `24h`, `1d`, `2d`, `7d` | `24h` |

**Parsing rules:**
- Tokens can appear in any order: `/recap 8h btc options` and `/recap btc options 8h` are equivalent.
- Missing tokens take their defaults: `/recap` alone means BTC options, last 24h.
- If `market_type` is `perps` or not `options`, note that this skill covers options flow
  and route the user to `paradex-market-analyst` for perp technicals.
- Convert window to start/end UTC timestamps (now − window → now).

**Examples:**
- `/recap` → BTC options, last 24h
- `/recap btc options 8h` → BTC options, last 8h
- `/recap eth options 4h` → ETH options, last 4h
- `/recap btc 1d` → BTC options, last 24h

## Data Sources

Fetch all sources in parallel. Never wait for one before starting the next.

| Source | What to fetch | Endpoint |
|---|---|---|
| Deribit DVOL | Index level + history for the window | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=DVOL&resolution=60&start_timestamp=<start_ms>&end_timestamp=<end_ms>` |
| Deribit spot / futures | Index price history + spot range for the window | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=<ASSET>_USDC&resolution=60` |
| Deribit block trades | Blocks over the window | `GET /api/v2/public/get_last_trades_by_currency?currency=<BTC/ETH>&kind=option&count=500&start_timestamp=<start_ms>&end_timestamp=<end_ms>&sorting=desc` — filter for `block_trade_id` set |
| Deribit screen flow | On-screen option trades, large clips | Same endpoint, filter trades WITHOUT `block_trade_id` — take the top ~200 by size for pattern analysis |
| Vol surface snapshot | Current mark IV for key strikes/expiries | `GET /api/v2/public/get_instruments?currency=<BTC/ETH>&kind=option&expired=false` then `deribit__get_ticker` or `web_fetch /api/v2/public/ticker?instrument_name=<inst>` for near-dated ATM and key strikes |
| Paradigm block tape | If injected in session — block structs, size, direction | Already parsed (no fetch needed) |

**If `deribit__get_ticker` MCP is available**, prefer it for vol surface fetches (faster than web_fetch).

## Step 1 — DVOL and Spot

From DVOL history:
- `dvol_start` = DVOL at window open
- `dvol_end` = DVOL now
- `dvol_range` = min–max across window
- `dvol_delta` = end − start (vol sold through / bought through)

From spot / futures:
- `spot_start`, `spot_end`, `spot_low`, `spot_high` for the window
- `spot_change_pct` = (end − start) / start × 100
- Nearest front-month futures: fetch `paradex_market_summaries` or Deribit futures summary
  for 24h volume if available

**Spot vs vol relationship:** label it factually:
- Spot up + vol down → "vol sold through a rally"
- Spot down + vol up → "vol bid into weakness"
- Spot up + vol up → "vol bought through a rally" (fear bid or demand)
- Spot flat + vol up/down → "vol moved independently of spot"

## Step 2 — Block Flow Analysis

From the Deribit tape, isolate block trades (`block_trade_id` present). For Paradigm-routed
blocks, they appear here with multi-leg `block_trade_leg_count` > 1.

**Cluster by `block_trade_id`** to reconstruct multi-leg structures. Do not report legs individually
when they share a block ID — report the reconstructed structure.

For each significant block cluster (threshold: > 10 BTC notional, or any multi-leg structure):

| Field | Derivation |
|---|---|
| Time UTC | `timestamp` → HH:MM UTC |
| Structure | Reconstruct from leg instruments: type (call/put/spread/straddle/calendar/ratio), strikes, expiry(s) |
| Size | Total contracts (sum of one leg for spreads, use the common ratio) |
| Side | Buyer (paid debit) or Seller (took credit) — from net premium direction |
| Level | Fill price in BTC or vol units as appropriate |
| IV | Per-leg mark IV from the Deribit trade's `iv` field — show both legs for spreads |

**Structure classification from instrument names:**
- Same expiry, same strike, C+P = Straddle
- Same expiry, different strikes, same type = Spread (call spread / put spread)
- Different expiries, same strike, same type = Calendar
- Put + Call same expiry, different strikes, net delta trade = Risk Reversal or Strangle
- Single leg = Outright

**Size grouping for the block table:**
List blocks from largest to smallest. Cap the table at the 8 most significant clusters —
summarise smaller flow in one prose line ("several 1–5x clips in near-dated OTM calls/puts, mixed flow").

**Two-way vs one-sided read:**
If the same structure prints on both sides within the window (buyer then seller or vice versa),
call it "two-way flow" — not accumulation. If only one side, note it.

## Step 3 — Screen Flow Themes

From on-screen (non-block) trades, identify recurring patterns. Group by:

1. **Expiry cluster** — which expiries are active (0DTE, weekly, monthly, quarterly)?
2. **Strike cluster** — which strikes are repeatedly printing?
3. **Direction cluster** — consistent put buying vs put selling, call buying vs call selling?
4. **Size pattern** — small retail clips vs medium institutional?
5. **IV vs ATM** — strikes printing above or below ATM vol → skew direction

Surface 3–5 themes, each stated as one factual bullet:

```
- 0DTE / near-dated put bid: <strike> P trading <Xv> vs ATM <Yv> · <n> clips
- OTM call selling: <strike> C sold <Xv> · <n> clips · [sizes]
- [Large single print]: <size>x <structure> @ <price> / <IV>v — largest screen print
- Put selling / premium collection: <strikes> P sold <Xv> on <n>–<m> DTE
- Tail buying: <strike> P/C @ <price> / <IV>v · <n>x
```

Keep each bullet to one line. No trailing commentary about what it implies.

## Step 4 — Vol Surface Read

Pull the current vol surface for the main near-dated expiry (and second expiry if calendar flow
was present). For each expiry, fetch a representative strike grid — at minimum: 10-delta put,
25-delta put, ATM, 25-delta call, 10-delta call.

Report:
- **ATM vol** for each active expiry
- **Put/call skew** (25d put IV minus 25d call IV — positive = puts richer)
- **Term structure** (near ATM vs back ATM — contango = backwardation in vol)
- **Notable level** (any strike where mark IV is unusually elevated or compressed vs the rest)

Surface read format:

```
Near-dated put skew elevated/flat/inverted: <25d P> vs <25d C> = <diff>v put premium
Term structure: near <Xv> / back <Yv> — [contango / backwardation / flat]
ATM vol by expiry: <DDMMMYY> <Xv> · <DDMMMYY> <Yv> · ...
[Notable]: <specific strike/expiry IV anomaly — one line max>
```

## Step 5 — Output Format

**Your ENTIRE response is the recap block below — match its shape exactly.** Nothing before the
header, nothing after the bias line. Work silently — no narration, no "fetching…", no tool-call
commentary. Your single visible message is the recap.

**Formatting rules for terminal rendering:**
1. Use `##` markdown headers for each section — they render as clear separators.
2. Use `**bold**` for inline emphasis (DVOL levels, key sizes, bias verdict).
3. Tables render correctly — use them for block flow and surface reads.
4. Backtick-wrap section labels that are meant to stand out: `` `[Bias]` ``.
5. No triple-backtick code fences around the whole output — it renders as a grey box.

---

### Output shape:

```
## [ASSET] Options — Last [WINDOW] Recap ([START_UTC]–[END_UTC])

### DVOL / Spot
[ASSET]DVOL [window]: [start]v → [end]v ([+/-delta]v) · range [low]–[high] · [notable spike/dip description if any]
Spot [window]: low [low] / high [high] / now [price] · [front-month futures]: $[vol]M vol
[Spot vs vol relationship label]

### Largest Blocks (Paradigm/DBT)

| Time UTC | Structure | Size | Side | Level | IV |
|---|---|---|---|---|---|
| [HH:MM] | [structure description] | [N]x | Buy/Sell | [price/level] | [Xv / Yv] |
...

[Two-way / one-sided flow commentary — one line per distinct flow cluster]

### Screen Flow — Notable Themes
- [Theme 1]
- [Theme 2]
- [Theme 3]
- [Theme 4 if present]
- [Theme 5 if present]

### Vol Surface Reads
[Surface read — 3–5 lines following the format from Step 4]

`[Bias]` [2–4 sentence net bias call: who is in control (vol sellers / vol buyers), which expiry/skew is bid, what the dominant institutional flow was, and what the surface says about near-term positioning. Factual — state what the data shows, not what the user should do.]
```

---

**The `[Bias]` line is mandatory** — it is the synthesis the recap exists to deliver. State:
1. Who is in control (vol sellers / vol buyers / neutral)
2. What the biggest institutional block flow expressed
3. What the screen theme says about positioning
4. What the surface (skew, term structure) confirms or contradicts

Keep it to 2–4 sentences. Never recommend a trade. State the market's revealed positioning and let the reader decide.

## Empty / Thin Window

If the window is short (< 2h) and produces no block prints and < 20 screen trades, say:

```
## [ASSET] Options — Last [WINDOW] Recap

Light window: no block prints, [N] screen trades. DVOL [X]v → [Y]v. Spot [low]–[high].
[Any single notable print if present, else: "No notable activity."]
```

## Caveats

- Block trades on Deribit tagged with `block_trade_id` include Paradigm-routed flow. However,
  Paradigm tape (structure labels, `strategy_code`, taker direction) is only available when
  injected into the session — otherwise direction/structure is reconstructed from leg signs.
- IV per trade comes from Deribit's `iv` field on each trade record — this is the mark IV at
  the moment of the trade, not the transaction-implied IV. It is accurate enough for surface reads.
- On-screen trade flow is sampled (last ~500 trades). Very thin or very active markets may
  under- or over-represent screen themes. Treat themes as directional signals, not exhaustive counts.
- Vol surface snapshot is current (fetch time), not historical. "Elevated put skew" is the
  state now, which reflects both the window's flow and pre-existing positioning.
- Not financial advice. This skill describes what the market did — it does not say what to do next.
