---
name: paradex-options-recap
description: >
  Options market recap for a user-specified window, invoked via /recap. Parses
  "/recap [asset] [options] [window]" (e.g. "/recap btc options 8h") and produces
  a fixed-format recap: DVOL/spot, volume by venue, block structure mix, flow themes,
  vol surface movers, and a summary. Use when the user types /recap or asks for
  a market recap, options flow summary, "what happened in BTC options", or "last Xh of flow".
  The output format is fixed — always the same sections in the same order.
compatibility: Deribit public API (web_fetch), Paradigm block tape (if injected),
  OKX/Bullish/IBIT public APIs (web_fetch). No authentication required.
metadata:
  author: tradeparadex
  version: "2.3"
---

# Options Recap

## Command Syntax

`/recap [asset] [window]` — tokens order-independent, all optional.

| Token | Examples | Default |
|---|---|---|
| `asset` | `btc`, `eth` | `btc` |
| `window` | `1h`, `4h`, `8h`, `24h`, `1d` | `24h` |

`/recap` alone = BTC options, last 24h.

## Data Fetches

DVOL, spot, and trades are independent — fetch in parallel. Vol surface needs the instrument list first, then per-instrument tickers.

| Data | Endpoint |
|---|---|
| DVOL history | `GET /api/v2/public/get_volatility_index_data?currency=BTC&resolution=3600&start_timestamp=<ms>&end_timestamp=<ms>` |
| Spot range | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=60&start_timestamp=<ms>&end_timestamp=<ms>` |
| Option trades | `GET /api/v2/public/get_last_trades_by_currency?currency=BTC&kind=option&count=1000&start_timestamp=<ms>&end_timestamp=<ms>&sorting=desc` |
| Instrument list | `GET /api/v2/public/get_instruments?currency=BTC&kind=option&expired=false` |
| Per-instrument ticker | `GET /api/v2/public/ticker?instrument_name=<inst>` (or `deribit__get_ticker` MCP) |
| OKX trades | `GET /api/v5/market/trades?instType=OPTION&instFamily=BTC-USD` |
| Bullish trades | `GET /trading-api/v1/trades?type=option` |

**Response shapes:**
- DVOL: `result.data` → `[[timestamp_ms, open, high, low, close], ...]` — first open = DVOL start, last close = DVOL end.
- Spot: `result` → `{open,high,low,close,ticks}` — min of `low[]` = spot low, max of `high[]` = spot high, last of `close[]` = now.
- Instruments: `result` → `[{instrument_name, strike, expiration_timestamp, option_type}]` — use exact `instrument_name` values; never construct names by hand (Deribit format: `5JUN26` not `05JUN26`).
- Ticker: `result` carries `mark_iv`, `bid_iv`, `ask_iv`, `greeks.delta`. Always use `mark_iv`, not `ask_iv` (thin books push ask_iv to extreme values).

`block_trade_id` present = block; absent = screen flow.

## Output Format — FIXED

**This format is mandatory and must not change between recaps.** Always output all six sections in this exact order. Traders rely on positional scanning — section 3 is always blocks, section 4 is always themes. Never reorder, add, or drop sections.

Work silently — no narration, no "fetching…". Output the recap block only.

---

**Shape to mirror exactly:**

---

**BTC Options · [WINDOW] Recap · [HH:MM]–[HH:MM] UTC**

---

**DVOL / Spot**

[ASSET] DVOL: Xv → Yv (±Zv) · range A–B · [one-word drift: rising / drifting lower / flat]
Spot: index now $X (up/down from $Y prior) · [one-clause vol/spot read]

**Volume · $[TOTAL]M across all venues · P/C ratio [X.Xx] ([puts/calls] dominant)**

| Venue | BTC | ETH | Other | Total |
|---|---|---|---|---|
| Deribit | $XM | $XM | $XM | $XM |
| OKX | $XM | $XM | $XM | $XM |
| Bullish | $XM | $XM | $XM | $XM |
| IBIT | $XM | $XM | $XM | $XM |
| **Total** | **$XM** | **$XM** | **$XM** | **$XM** |

---

**Largest Blocks · Deribit / OKX / Bullish / IBIT (incl. Paradigm-routed flow)**

**Largest single print:** [DDMMMYY] [Strike] [Structure] · [Nx] · $[X]M · [Venue] · [HH:MM] UTC

**Structure mix (block notional)**

| Structure | Notl | Where active |
|---|---|---|
| Outright puts | $XM | [e.g. Jun 55k P × 200, Sep 50k P × 150] |
| Put spreads | $XM | [e.g. Jun 60/55k × 250, Jun 45/40k × 250] |
| Outright calls | $XM | [e.g. Sep 62k C × 100 (3 clips), Dec 90k C × 250] |
| Risk reversals | $XM | [e.g. Dec 62k/90k RR × 250 (5 clips)] |
| Call spreads | $XM | [e.g. Jun 65/70k × 100] |
| Calendars | $XM | [e.g. Jun/Sep 60k PCal × 50 two-way] |
| Custom combos | $XM | [e.g. Sep 62k P sell / Dec 90k C buy × 250] |
| Other | $XM | [e.g. mixed small clips] |

---

**Flow Themes**

[Theme name] — [2–3 sentence description: what structure, what size, what strikes, what direction, any notable pattern or recurrence. This is the key insight section — be specific with strikes, sizes, and IV levels.]

[Theme name] — [same format]

[Theme name] — [same format]

[3–5 themes total. Each theme is a short paragraph — name in bold, then the facts.]

---

**Vol Surface · Biggest Movers**

| Strike | IV | Δ IV | OI | Δ OI |
|---|---|---|---|---|
| DDMMMYY Xk C/P | Xv | ±Xv | X BTC | ±X BTC |

[5–8 rows: biggest IV movers in the window, sorted by absolute Δ IV descending. OI and Δ OI show whether the move came from real positioning or just repricing.]

---

**Summary**

[1–2 sentences only: net market read. Who is in control. What the dominant flow expressed. No trade recommendations.]

---

## Section Rules

**DVOL / Spot** — state open→close, range, and a one-word drift label. One clause on vol/spot relationship.

**Volume** — total notional across execution venues (Deribit, OKX, Bullish, IBIT) + per-venue table broken into BTC / ETH / Other columns with a totals row. Do not list Paradigm as a separate venue — Paradigm-routed trades settle on these venues and are already counted there. P/C ratio = total put notional / total call notional; label which side is dominant. Estimate from trade tape if unavailable; label as estimated.

**Largest Blocks** — open with the single largest individual block print (one line: expiry, strike, structure, size, notional, venue, time). Paradigm-routed blocks appear on the execution venue tape — attribute to the execution venue but note "via Paradigm" where identifiable. Then aggregate all blocks by structure type into the mix table, sorted by notional descending. The "Where active" column is one line naming the dominant strikes/expiries/sizes for that structure type — enough detail for a trader to know where the action was without reading the flow themes.

**Flow Themes** — 3–5 themes, each named and described. This is the highest-value section. Be specific: name the strikes, sizes, IV levels, and venue. State whether flow is one-sided or two-way. Note any repeated taker on the same line.

**Vol Surface Movers** — fetch current IV and OI for the most-active strikes. Compare IV to window-open IV (use per-trade `iv` field as the "before"); compare OI to window-open OI from `get_book_summary_by_instrument`. Sort by |Δ IV| descending. Show 5–8 rows. Δ OI sign indicates whether positioning is being added (+) or unwound (−).

**Summary** — 1–2 sentences, facts only.

## Thin Window

(< 2h, no blocks, < 20 screen trades) — still output all six sections; mark empty sections as `No data`.
