# Venues — per-venue cookbook for paradigm-rfq-trader

The skill body is **venue-agnostic**. This file is the per-venue
recipe: instrument-name format, fair-value tools, settlement
verification, and venue-specific quirks. Adding a new DRFQv2 venue
means appending a section here in the same shape — the SKILL.md
workflow stays the same.

Currently in scope: **PRDX (Paradex, primary)** and **DBT (Deribit)**.

Each venue section answers four questions, in this order:

1. **Naming** — how venue-native instrument names look (for the
   `paradigm_drfqv2_instruments` lookup).
2. **Fair value** — which tool(s) to call to benchmark a quote or
   ranking, by `instrument.kind`.
3. **Edge syntax** — what "+X over mark" means on this venue.
4. **Settlement check** — how to verify the cleared trade landed.

Plus any venue-specific quirks at the end.

---

## PRDX — Paradex (primary focus)

### Naming

| Product | Format | Example |
|---|---|---|
| Perpetual | `<BASE>-USD-PERP` | `BTC-USD-PERP`, `ETH-USD-PERP` |
| Dated future | `<BASE>-USD-<DDMMMYY>` | `BTC-USD-27JUN26` |
| Option | `<BASE>-USD-<DDMMMYY>-<STRIKE>-<C\|P>` | `BTC-USD-8MAY26-90000-C` |

Day **not** zero-padded. Month uppercase 3-letter. `-USD-` infix is
the Paradex distinguisher vs Deribit.

### Counterparties / LP coverage

Default to sending to **every prime-venue-enabled LP for PRDX, by
name** — resolve them explicitly rather than relying on an open
broadcast:

1. Call `paradigm_drfqv2_counterparties` and **page through the entire
   result**. The response is paginated — follow the cursor / `next` /
   `has_more` until it is exhausted. **Stopping at page 1 silently drops
   LPs** and is the cause of "not all LPs got the RFQ".
2. Filter to desks flagged prime-venue-enabled for PRDX (the per-desk
   prime / venue-eligibility flag on each counterparty record). Pass
   that explicit list as `counterparties` to
   `paradigm_drfqv2_create_rfq`, and surface the count (`all N PRDX
   prime LPs`).

Narrow to a directed subset only when the user names specific desks.

Last-resort fallback (the counterparties tool is unavailable or returns
nothing): send `paradigm_drfqv2_create_rfq` with an empty / omitted
`counterparties` list → Paradigm open-broadcasts (GRFQ) to all eligible
PRDX makers. Note the fallback in the data trace so it's clear the
prime-LP filter was bypassed.

### Fair value

**`kind = FUTURE` (perp / dated future):**

- `paradex_bbo(market=...)` → best bid/ask.
- `paradex_market_summaries(...)` → mark + funding + 24h stats.
- `paradex_orderbook(...)` → walk the book for the full RFQ size.
  This is the implicit "what would I get on-screen?" benchmark
  that every RFQ price should be compared against.

**`kind = OPTION`:**

- `paradex_market_summaries(market=...)` per leg → `mark_price`,
  `mark_iv`, `delta`, `vega`.
- Pull `<BASE>-USD-PERP` mark for the underlying spot.
- Aggregate for multi-leg: `structure_mark = Σ (ratio × leg_mark ×
  side_sign)`, net delta = Σ (ratio × δ × side_sign), net vega
  similar.
- BS / IV math itself: defer to `paradex-options-pricer` formulas.

### Edge syntax

- "Y bps over mid" → `price = mid × (1 + Y/10000)` (ask) or
  `× (1 - Y/10000)` (bid). Mid = `(best_bid + best_ask) / 2` from
  `paradex_bbo`.
- "Tighten the BBO by Z bps" → quote inside the current Paradex
  best. Flag if it implies a negative spread.
- "X vol over mark IV" (options only) → bump per-leg IV by X,
  reprice via BS, re-aggregate.

### Settlement check

- `paradex_account_fills(market=..., start_at=...)` — confirm the
  block landed in the user's Paradex account.
- `paradex_account_positions` — surface the updated position.

### Quirks

- Same strike can exist as INVERSE *and* LINEAR margin variants —
  filter on `margin_kind` when resolving by name to disambiguate.

---

## DBT — Deribit

### Naming

| Product | Format | Example |
|---|---|---|
| Option | `<BASE>-<DDMMMYY>-<STRIKE>-<C\|P>` | `BTC-8MAY26-90000-C`, `ETH-10MAY26-2375-P` |
| Future | `<BASE>-<DDMMMYY>` | `BTC-27JUN26` |
| Perpetual | `<BASE>-PERPETUAL` | `BTC-PERPETUAL` |

Day **not** zero-padded (same convention as Paradex). No `-USD-`
infix.

### Fair value

**`kind = OPTION` (the dominant Deribit RFQ product):**

- `deribit__get_ticker(instrument_name=...)` per leg — returns
  mark, bid, ask, mark_iv, bid_iv, ask_iv, delta, gamma, theta,
  vega, open_interest.
- Fallback: `web_fetch`
  `https://www.deribit.com/api/v2/public/ticker?instrument_name=...`
  returns the same payload structure.
- Pull `BTC-PERPETUAL` / `ETH-PERPETUAL` mark for underlying spot.
- Aggregate exactly like the PRDX option case.

**`kind = FUTURE` (perp / dated future):**

- `deribit__get_ticker` (or web_fetch) for the instrument — returns
  mark, BBO.
- Cross-venue check vs Paradex via `paradex_bbo` is optional;
  Deribit's own book is the relevant benchmark since the trade
  settles there.

### Edge syntax

- "Y bps over mark" → `price = mark × (1 ± Y/10000)`. "Mark" here
  is `deribit__get_ticker.mark_price` (in BTC for inverse
  options).
- "X vol over mark IV" → bump per-leg IV by X (Deribit's
  `mark_iv` is a percentage, e.g. `34.52`), reprice via BS,
  re-aggregate.
- "Tighten the BBO" → quote inside Deribit's current best bid/ask.

### Settlement check

No Deribit account-MCP at this skill version. After the cross:

- Surface `trade_id` from `paradigm_drfqv2_trades(rfq_id=...)`.
- Tell the user the block will appear on their Deribit account and
  to verify there directly.

### Quirks

- Deribit option prices are in **BTC/ETH terms** for inverse
  options (the common case), not USD. When surfacing dollar
  notional, multiply by the underlying mark.
- `mark_iv` is in **percentage** form (`34.52` = 34.52%), not
  decimal — different from OKX which returns `0.3452`.
- For perps/futures on Deribit, prices ARE in USD.

---

## Adding a new venue (future scope)

To extend the skill to `BYB` (Bybit), `BIT` (Bit.com), or any
future DRFQv2 venue:

1. Append a section to this file in the same four-part shape.
2. If the venue needs a new fair-value MCP / tool dependency, list
   it under "Compatibility" in `SKILL.md`.
3. No changes to the SKILL.md workflow body — Step 2/3a/3b/4 all
   delegate to this file.

Strategy codes and product kinds (`OPTION` / `FUTURE` / `LOAN` /
`SPOT`) are venue-independent — see
[`instruments.md`](instruments.md) for the full strategy-code table.
