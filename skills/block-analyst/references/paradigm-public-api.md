# Paradigm Public Trade-Tape Lookup — resolve an RFQ by `rfq_id`

The block analyst's input is now `/analyze <rfq_id> <rfq description>`. The
`rfq_id` is the authoritative key: it identifies a single cleared block on
Paradigm. This file is the recipe for turning that id into the full trade
record the analysis needs (the same fields that used to be pasted as JSON).

**No authentication.** Paradigm's RFQ block trades settle on the public tape —
the trade tape is a public feed of every cleared block, so reading it needs no
`PARADIGM_*` key, no HMAC signing, none of the `references/auth.md` machinery
(that file is for the *trader* skill's state-changing calls). Use `web_fetch`.

---

## Endpoint

**Base URL (prod):** `https://api.paradigm.co`

**Public trade tape (GRFQ):**

```
GET https://api.paradigm.co/v1/grfq/trades/
```

This returns recently cleared block trades platform-wide, newest first, in a
paginated envelope (`{ "count", "next", "results": [ ... ] }` shape). Each
element is one cleared block. No auth headers are required for the public tape.

**Resolving a specific `rfq_id`:**

1. Prefer a server-side filter if the tape accepts one — try
   `GET /v1/grfq/trades/?rfq_id=<rfq_id>` first. If the API ignores unknown
   query params it simply returns the unfiltered page, so always verify by
   matching client-side (next step).
2. Otherwise page the tape (`next` cursor / `offset` / `page`) and **match the
   `rfq_id` client-side** against each record's id fields — the same RFQ may be
   referenced as `rfq_id`, `block_rfq_id`, `id`, or carried on each leg. The
   tape is time-ordered; widen the window with the cursor until the id is found
   or the page range is exhausted.
3. A multi-leg structure surfaces as **one block** sharing a single
   `block_trade_id` across its leg prints — keep all legs of the matched block
   together (this is the structure; do not treat a single leg as the trade).

**Alternatives / fallbacks** (use in this order if the REST tape is unreachable):

| Source | When | How |
|---|---|---|
| WS `trade_tape` channel | streaming context | `wss://ws.api.paradigm.trade/` GRFQ `trade_tape` channel — public, same record shape |
| Injected Paradigm block tape | running inside the Dime/terminal session | scan the injected feed for the `rfq_id` (see SKILL Step 3a) |
| S3 historical tape | older / settled blocks not on the live tape | `paradigm-data-discovery` → `paradigm_trade_tape_slim` keyed by rfq/block id |
| Deribit public tape | last resort, no Paradigm tape at all | reconstruct the block from `block_trade_id` clusters (SKILL Step 3b) |

If **none** resolves the id, do not fabricate the record. Fall back to the
inline `<rfq description>` for the structure, fetch live marks per the normal
flow, and **note in the block that the RFQ record could not be retrieved** (so
fill price / mark offset are unavailable rather than invented).

---

## Field mapping — tape record → analysis fields

The public tape record carries the same information that used to arrive as
pasted JSON. Field names can vary slightly by tape version; map by **meaning**,
not by an exact key. Canonical mapping:

| Analysis field (SKILL Step 1) | Trade-tape source |
|---|---|
| `description` / legs | `description`, or the `legs[]` array (`instrument`, `ratio`, `side`) |
| `action` (top-level RFQ side) | `side` / `action` on the block |
| leg-level `side` (authoritative) | `legs[].side` — what the taker holds per leg |
| `strategy_code` | `strategy_code` / `strategy.code` (see `references/strategy-codes.md`) |
| `quantity` | `quantity` / `amount` (block size in contracts) |
| `price` | `price` / `mark_price`-adjacent fill field — the executed block price |
| `mark_price` | `mark_price` (venue mark at trade time) |
| `displayValues.markOffset` | `mark_offset` / `displayValues.markOffset` (fill − mark) |
| `index_price` | `index_price` (spot at trade time — **label "Spot", never "Index"**) |
| `rfqType` | `rfq_type` (`grfq` multi-maker / `drfq` directed) |
| `venue` | `venue` (`DBT` Deribit, `OKX`, `BIT` Bit.com, `PRDX` Paradex) |
| `product_codes` | `product_codes` (`DO`/`EH` options, `DP`/`EP` perps) |
| `quote_currency` | `quote_currency` (`BTC` / `ETH` / `USDC`) |
| `strategy_delta` | `strategy_delta` (signed; used to resolve taker side) |
| `block_trade_id` | `block_trade_id` (clusters multi-leg legs + matches prior prints) |

If the tape omits a numeric field the analysis needs (e.g. no `mark_price`),
pull it live in Step 2 rather than guessing, and benchmark against the live
mark instead of the trade-time mark.

---

## The role of the inline `<rfq description>`

The `<rfq description>` after the `rfq_id` in `/analyze <rfq_id> <rfq
description>` is a **human-readable hint**, not the source of truth:

- **Cross-check** — confirm the resolved record matches what the user expects
  (right structure, strikes, expiry). If the tape record and the description
  disagree materially (different strikes/expiry/structure), surface that the
  `rfq_id` resolved to a *different* trade rather than silently overriding.
- **Disambiguation** — if the id is ambiguous or the tape returns a cluster,
  use the description to pick the right block.
- **Fallback** — if the lookup fails entirely, parse the structure from the
  description (`[+/-][ratio] [Type] [DD Mon YY] [Strike]`, one leg per line) so
  the greeks/fair/live brackets can still be produced from live data, with the
  fill-vs-mark line marked unavailable.

The resolved tape record always wins for numeric fields (fill price, mark,
spot, quantity, greeks inputs). The description never overrides a retrieved
number.
