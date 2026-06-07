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
  version: "1.4"
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
| Spot range (recap window) | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=60&start_timestamp=<ms>&end_timestamp=<ms>` |
| Spot history (7d, for realized vol) | `GET /api/v2/public/get_tradingview_chart_data?instrument_name=BTC-PERPETUAL&resolution=60&start_timestamp=<7d-ago-ms>&end_timestamp=<now-ms>` |
| All option trades | `GET /api/v2/public/get_last_trades_by_currency?currency=BTC&kind=option&count=1000&start_timestamp=<ms>&end_timestamp=<ms>&sorting=desc` |
| Instrument list (for vol surface) | `GET /api/v2/public/get_instruments?currency=BTC&kind=option&expired=false` |
| Per-instrument ticker (for vol surface) | `GET /api/v2/public/ticker?instrument_name=<inst>` (or `deribit__get_ticker` MCP if available) |
| OKX trades | `GET /api/v5/market/trades?instType=OPTION&instFamily=BTC-USD` |
| Bullish trades | `GET /trading-api/v1/trades?type=option` |

**Response shapes:**
- DVOL: `result.data` → `[[timestamp_ms, open, high, low, close], ...]` — first open = DVOL start, last close = DVOL end.
- Spot: `result` → `{open,high,low,close,ticks}` — min of `low[]` = spot low, max of `high[]` = spot high, last of `close[]` = now.
- Instruments: `result` → `[{instrument_name, strike, expiration_timestamp, option_type}]` — use exact `instrument_name` values; never construct names by hand (Deribit format: `5JUN26` not `05JUN26`).
- Ticker: `result` carries `mark_iv`, `bid_iv`, `ask_iv`, `greeks` (`delta`, `vega`, `gamma`). Always use `mark_iv`, not `ask_iv` (thin books push ask_iv to extreme values).

`block_trade_id` present = block; absent = screen flow.

## Computing the numbers

Realized vol (stdev + annualization), flow greeks (Black-76), and surface skew (delta interpolation) are math LLMs reliably get wrong by estimating. A bundled script does them deterministically — **always use it; never hand-compute these.** Save the fetched data to a snapshot, then run:

```bash
uv run scripts/paradex_options_recap.py --data snapshot.json
```

Snapshot shape (omit any field to skip that section):
```json
{
  "dvol_close": 48.16,
  "spot": 61973.5,
  "spot_closes_7d": [63670, 63812, ...],
  "trades": [{"instrument_name": "BTC-26JUN26-55000-P", "index_price": 62000,
              "iv": 72.0, "timestamp": 1780000000000, "direction": "buy",
              "amount": 100, "block_trade_id": "BLOCK-1"}],
  "tickers": {"BTC-5JUN26-62000-C": {"mark_iv": 82.87, "delta": 0.4956}}
}
```

Returns `{realized_vol: {value, vrp, vrp_label}, flow_greeks: {positioning_label, net_customer_vega, ...}, top_blocks: [{structure, size_btc, notional_usd, side, expiry, ...}], vol_surface: {expiries, term_structure, skew_label, ...}}` — read those fields straight into the recap. If a `derived` block is already present in your context (evals inject one), read it and skip the script. Verify the math anytime with `python3 scripts/test_vol_math.py` (no network/auth).

## Analysis Steps

**1. DVOL / Spot** — open → close, range, spot low/high. Label the relationship:
- Spot up + vol down → "vol sold through a rally"
- Spot down + vol up → "vol bid into weakness"
- Spot up + vol up → "vol bought through a rally"

Then read **realized vs implied** (the vol risk premium). Realized-vs-implied is a *slow* statistic — measure realized over a fixed **7-day** trailing window (paired with DVOL's 30-day implied), NOT the recap window; a short window annualizes one trending session into noise.

**Never compute realized vol by mental arithmetic** — stdev/annualization is exactly the math to get wrong by estimating. Get the value from the bundled script; it returns `realized_vol.value` (annualized, vol points) and `realized_vol.vrp_label`. If `derived.realized_vol` is already in context, read it directly.

**2. Volume** — aggregate notional across all execution venues (Deribit, OKX, Bullish, IBIT). P/C ratio = total put notional / total call notional; label which side is dominant.

**3. Block Flow** — don't eyeball the tape. The script clusters trades by `block_trade_id`, ranks by USD notional, and classifies each structure; read its `top_blocks` for the largest single print and the structure mix (each entry has `structure`, `size_btc`, `notional_usd`, `side`, `expiry`). Ranking 1000 raw trades by hand mis-identifies the largest block and hallucinates notionals — read `derived.top_blocks` if it's in context. Note two-way (`side: Two-way`) vs one-sided flow per structure.

Then read **net dealer positioning** — contract counts mislead (a long-dated option carries far more vega than a short-dated one), so weight flow by greeks. **Never compute the greeks by hand** (Black-76 needs a log, a square root, and a normal density). The bundled script returns `flow_greeks.positioning_label`; read `flow_greeks` from context if `derived.flow_greeks` is already supplied. Interpret the label:
- Dealers net **short gamma** → they buy rallies / sell dips → expect them to **chase and amplify** moves.
- Dealers net **long gamma** → they sell rallies / buy dips → expect **pinning / mean reversion**.
- Balanced / two-way → no decisive positioning.

**4. Screen Flow Themes** — group non-block trades by expiry, strike, and direction. Surface 3–5 factual bullets.

**5. Vol Surface** — discover-then-fetch the tickers, then let the script derive the metrics. Do NOT guess instrument names, and do NOT eyeball skew.

1. *Discover.* Call `get_instruments` once. Pick the front expiry (nearest `expiration_timestamp` ≥ now) and, if block flow spans expiries, the second expiry too.
2. *Select strikes.* In each chosen expiry, find the ATM strike (closest to spot) and take **ATM ± 4 strikes**, calls and puts. ±4 (not ±2) so the 25-delta wings are bracketed — otherwise the skew/butterfly are extrapolated and unreliable. Use the exact `instrument_name` strings from step 1 — never reconstruct them.
3. *Fetch.* Get the ticker for each selected instrument (parallel). Pass them as `tickers` (with `mark_iv` and `delta`) plus `spot` into the script (see "Computing the numbers").
4. *Read the result.* The script returns `vol_surface` with per-expiry `atm_iv`, `rr_25d` (25Δ risk reversal — skew), `fly_25d` (butterfly — wings), plus `term_structure` and `skew_label`. It uses linear interpolation in delta-space (line segments between observed strikes, not a fitted smile): 25Δ call = delta 0.25, 25Δ put = 0.75, ATM = 0.50, and flags `wings_extrapolated` when strikes don't bracket the wings — note that caveat if set. Read these straight in:
```
Skew: 25Δ RR Zv — [puts bid / calls bid]
Term structure: front Xv / back Yv — [contango / flat / backwardation]
ATM by expiry: DDMMMYY Xv · DDMMMYY Yv
```

The script reads `mark_iv` (not `ask_iv`) — thin books push ask_iv to extreme values (e.g. 190v on a barely-quoted wing) that misrepresent the surface. If `derived.vol_surface` is in context, read it directly.

## Output Format — FIXED

**This format is mandatory and must not change between recaps.** Always output all six sections in this exact order. Traders rely on positional scanning — section 3 is always blocks, section 4 is always themes. Never reorder, add, or drop sections.

Work silently — no narration, no "fetching…". Output the recap block only. If live data tools are unavailable, add one line at the very top: `⚠ Data estimated — no live feed available.`

---

**Shape to mirror exactly:**

---

**BTC Options · [WINDOW] Recap · [HH:MM]–[HH:MM] UTC**

---

**DVOL / Spot**

[ASSET] DVOL: Xv → Yv (±Zv) · range A–B · [one-word drift: rising / drifting lower / flat]
Spot: index now $X (up/down from $Y prior) · [one-clause vol/spot read]
RV(7d) Rv vs implied Yv → [VRP read: rich / cheap / in line]

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

[One line per flow cluster: two-way or one-sided]
Net positioning: dealers [long/short] gamma/vega → [chase & amplify / pin & mean-revert] · [≈$X vega/vol-pt]

---

**Flow Themes**

[Theme name] — [2–3 sentence description: what structure, what size, what strikes, what direction, any notable pattern or recurrence. This is the key insight section — be specific with strikes, sizes, and IV levels.]

[Theme name] — [same format]

[Theme name] — [same format]

[3–5 themes total. Each theme is a short paragraph — name in bold, then the facts.]

---

**Vol Surface · Biggest Movers**

Skew: 25Δ RR Zv — [puts bid / calls bid]
Term structure: front Xv / back Yv — [contango / flat / backwardation]
ATM by expiry: DDMMMYY Xv · DDMMMYY Yv

| Strike | IV | Δ IV | OI | Δ OI |
|---|---|---|---|---|
| DDMMMYY Xk C/P | Xv | ±Xv | X BTC | ±X BTC |

[5–8 rows: biggest IV movers in the window, sorted by absolute Δ IV descending. OI and Δ OI show whether the move came from real positioning or just repricing.]

---

**Summary**

[1–2 sentences only: net market read. Who is in control. What the dominant flow expressed. No trade recommendations.]

---

## Section Rules

**DVOL / Spot** — state open→close, range, and a one-word drift label. One clause on vol/spot relationship. Then the RV(7d) vs implied line from the bundled script.

**Volume** — total notional across execution venues (Deribit, OKX, Bullish, IBIT) + per-venue table broken into BTC / ETH / Other columns with a totals row. Do not list Paradigm as a separate venue — Paradigm-routed trades settle on these venues and are already counted there. P/C ratio = total put notional / total call notional; label which side is dominant. Estimate from trade tape if unavailable; label as estimated.

**Largest Blocks** — open with the single largest individual block print (one line: expiry, strike, structure, size, notional, venue, time). Paradigm-routed blocks appear on the execution venue tape — attribute to the execution venue but note "via Paradigm" where identifiable. Then aggregate all blocks by structure type into the mix table, sorted by notional descending. The "Where active" column is one line naming the dominant strikes/expiries/sizes for that structure type. Follow with a net positioning line from the bundled script.

**Flow Themes** — 3–5 themes, each named and described. This is the highest-value section. Be specific: name the strikes, sizes, IV levels, and venue. State whether flow is one-sided or two-way. Note any repeated taker on the same line.

**Vol Surface Movers** — open with skew, term structure, and ATM levels from the bundled script. Then table the biggest IV movers: compare IV to window-open IV (use per-trade `iv` field as the "before"); compare OI to window-open OI from `get_book_summary_by_instrument`. Sort by |Δ IV| descending. Show 5–8 rows.

**Summary** — 1–2 sentences, facts only.

## Thin Window

(< 2h, no blocks, < 20 screen trades) — still output all six sections; mark empty sections as `No data`.
