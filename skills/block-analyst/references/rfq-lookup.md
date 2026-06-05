# Resolve an RFQ by `rfq_id` — Paradigm DRFQv2 RFQ lookup

The block analyst's input is `/analyze <rfq_id> <rfq description>`. The `rfq_id`
is the authoritative key: it identifies a single RFQ / cleared block on
Paradigm. This file is the recipe for turning that id into the full trade
record the analysis needs (the same fields that used to be pasted as JSON).

**Resolution endpoint — Paradigm DRFQv2 `GET /rfqs/{rfq_id}`.** Doc:
<https://api.docs.paradigm.co/#drfqv2-get-rfqs-rfq_id>. This returns the RFQ
object — legs (instrument, strike, ratio, side), strategy, venue, quantity, and
the cleared price/mark — keyed directly by the `rfq_id`, so it is the single
lookup that replaces the pasted JSON.

---

## How to call it (in priority order)

### 1. `mcp-paradigm-py` MCP tools (preferred when available)

The same MCP server the `paradigm-rfq-trader` skill uses exposes the DRFQv2
read tools. These handle transport, auth, and signing in-process — no keys in
chat. For a known `rfq_id`, prefer in this order:

| Tool | Returns |
|---|---|
| `paradigm_drfqv2_rfqs` (filter to the `rfq_id`) | the RFQ: legs, strategy, venue, quantity, state |
| `paradigm_drfqv2_rfq_snapshot(rfq_id=...)` | RFQ + BBO + book in one call |
| `paradigm_drfqv2_trades(rfq_id=...)` | the cleared block(s) for that RFQ — fill price, side, size |

A cleared block carries the fill; the RFQ object carries the structure. Pull
both when present (snapshot/rfqs for legs, trades for the executed price).

### 2. REST fallback — `GET /v2/drfq/rfqs/{rfq_id}`

When the MCP server is not installed, call the endpoint directly:

| Env | Base URL | Path |
|---|---|---|
| Prod | `https://api.paradigm.co` | `GET /v2/drfq/rfqs/{rfq_id}` |
| Testnet | `https://api.test.paradigm.co` | `GET /v2/drfq/rfqs/{rfq_id}` |

The cleared trade for the RFQ is at `GET /v2/drfq/trades/?rfq_id={rfq_id}`.

**Auth:** the DRFQv2 API is **not an anonymous public feed** — reads carry the
standard Paradigm headers (`Authorization: Bearer`, `Paradigm-API-Timestamp`,
`Paradigm-API-Signature`). The MCP server signs these for you; for the raw REST
path the signing scheme lives in the `paradigm-rfq-trader` skill's
[`references/auth.md`](../../paradigm-rfq-trader/references/auth.md). **Never
ask the user to paste `PARADIGM_*` keys into chat** — they live in the MCP
server / env. If neither credentials nor an MCP tool are available, this path is
unavailable; use the fallbacks below.

### 3. Fallbacks (when neither MCP nor signed REST resolves the id)

| Source | When | How |
|---|---|---|
| Injected block-trade context | running inside the Dime/terminal session | the terminal attaches the cleared block (e.g. via a `set_block_trade_context` feed) — read it directly |
| S3 historical tape | older / settled blocks | `paradigm-data-discovery` → `paradigm_trade_tape_slim`, keyed by rfq / block id |
| Deribit public tape | last resort, no Paradigm access | reconstruct the block from `block_trade_id` clusters (SKILL Step 3b) |

A multi-leg structure is **one block** sharing a single `block_trade_id` across
its leg prints — keep all legs of the matched block together (a single leg is
not the structure).

**If the id cannot be resolved on any source, do NOT fabricate the record.**
Fall back to the inline `<rfq description>` for the structure, fetch live marks
per the normal flow, and **state plainly that the RFQ could not be resolved** so
the fill price / mark offset read as *unavailable* rather than invented. See the
SKILL.md output rules for the failure-mode line.

---

## Field mapping — RFQ record → analysis fields

The DRFQv2 RFQ object (plus its cleared trade) carries the same information that
used to arrive as pasted JSON. Field names vary slightly by source (MCP tool vs
REST vs injected context); map by **meaning**, not by an exact key:

| Analysis field (SKILL Step 1) | RFQ / trade source |
|---|---|
| `description` / legs | `legs[]` (`instrument` / `instrument_id`, `strike`, `ratio`, `side`) |
| leg-level `side` (authoritative) | `legs[].side` — what the taker holds per leg |
| `action` (top-level RFQ side) | the RFQ / block `side` |
| `strategy_code` | `strategy_code` / `strategy.code` (see `references/strategy-codes.md`) |
| `quantity` | `quantity` / `amount` (block size in contracts) |
| `price` | the cleared block fill price (from the trade record) |
| `mark_price` | `mark_price` (venue mark at trade time) |
| `displayValues.markOffset` | `mark_offset` (fill − mark) |
| `index_price` | `index_price` (spot at trade time — **label "Spot", never "Index"**) |
| `rfqType` | `rfq_type` (`grfq` multi-maker / `drfq` directed) |
| `venue` | `venue` (`DBT` Deribit, `OKX`, `BIT` Bit.com, `PRDX` Paradex) |
| `product_codes` | `product_codes` (`DO`/`EH` options, `DP`/`EP` perps) |
| `quote_currency` | `quote_currency` (`BTC` / `ETH` / `USDC`) |
| `strategy_delta` | `strategy_delta` (signed; used to resolve taker side) |
| `block_trade_id` | clusters multi-leg legs + matches prior prints |

If the lookup omits a numeric field the analysis needs (e.g. no `mark_price`),
pull it live in Step 2 rather than guessing, and benchmark against the live mark
instead of the trade-time mark.

---

## The role of the inline `<rfq description>`

The `<rfq description>` after the `rfq_id` is a **human-readable hint**, not the
source of truth:

- **Cross-check** — confirm the resolved RFQ matches what the user expects
  (right structure, strikes, expiry). If the resolved RFQ and the description
  disagree materially, surface that the `rfq_id` resolved to a *different* trade
  rather than silently overriding.
- **Disambiguation** — if the lookup returns more than one block, use the
  description to pick the right one.
- **Fallback** — if the lookup fails entirely, parse the structure from the
  description (`[+/-][ratio] [Type] [DD Mon YY] [Strike]`, one leg per line) so
  the greeks/fair/live brackets can still be produced from live data, with the
  fill-vs-mark line marked unavailable.

The resolved RFQ record always wins for numeric fields (fill price, mark, spot,
quantity). The description never overrides a retrieved number.
