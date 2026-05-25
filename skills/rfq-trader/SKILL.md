---
name: paradigm-rfq-trader
description: >
  End-to-end Paradigm.co DRFQv2 workflow for institutional options block
  trading. Covers both sides: takers building multi-leg options RFQs,
  sourcing maker quotes, ranking price-then-time, crossing to execute; and
  makers subscribing to incoming RFQs, pulling fair value from Deribit /
  OKX / Bybit, pricing with user-supplied edge, and managing the quote
  lifecycle. Every state-changing action goes through an explicit
  confirmation gate — never auto-submits. Use when the user asks to
  "create an RFQ", "send a Paradigm block", "quote this RFQ", "stream
  Paradigm quotes", "hit the best bid", "price a strangle on Paradigm",
  "cancel rfq_X". Does NOT cover post-trade analysis of a filled block
  (use paradigm-block-analyst) or historical tape queries (use
  paradigm-data-discovery). v1.0 scope: options only, single- and
  multi-leg. Perp / futures combos and spot RFQ are out of scope.
compatibility: Requires Paradigm DRFQv2 REST access at api.paradigm.co
  (or api.test.paradigm.co for testnet). Credentials (PARADIGM_ACCESS_KEY
  + base64 PARADIGM_SIGNING_KEY) are injected at request time by an
  upstream credentials proxy — see references/auth.md. Falls back to
  upstream-injected Authorization / Paradigm-API-Timestamp /
  Paradigm-API-Signature headers when the signing key is not exposed to
  the skill. Fair-value lookups reuse deribit__get_ticker MCP (if
  available) or web_fetch.
metadata:
  author: tradeparadex
  version: "1.0"
---

# Paradigm RFQ Trader

Drives the full Paradigm DRFQv2 lifecycle — taker and maker — from RFQ
construction through quote streaming, ranking, and confirmation-gated
execution.

## Trigger

Fire on live RFQ-lifecycle intent. Examples:

- *"create an RFQ for a BTC strangle"*
- *"send a 100-lot 7MAY26 90/100 call spread on Paradigm"*
- *"quote rfq_12345 at 0.5 vol over Deribit mark"*
- *"hit the best quote on this RFQ"*
- *"stream incoming Paradigm RFQs"*
- *"cancel rfq_12345"*

Do **not** fire on:

- Post-trade analysis of a filled block trade JSON → `paradigm-block-analyst`.
- Historical RFQ tape queries (last week's biggest blocks, monthly volume) →
  `paradigm-data-discovery`.
- Paradex DEX order placement → `paradex-order-builder`.

## Roles

| Role | Steps | Endpoints touched |
|---|---|---|
| **Taker** — sources liquidity | 1, 2, 3, 4a, 5 | `POST /v1/drfq/rfqs/`, `GET /v1/drfq/rfqs/{id}/quotes/`, `POST /v1/drfq/orders/`, `DELETE /v1/drfq/rfqs/{id}` |
| **Maker** — provides liquidity | 1, 2, 3, 4b, 5 | `GET /v1/drfq/rfqs/`, `POST /v1/drfq/rfqs/{id}/quotes/`, `DELETE` / amend quote |

Both roles share the signing and confirmation-gate plumbing. The skill
picks the role from user intent — if ambiguous, ask.

## Transport

| Capability | Primary | Fallback |
|---|---|---|
| Signed REST request | `web_fetch` with HMAC-SHA256 headers (see `references/auth.md`) | upstream proxy attaches headers; skill posts unsigned bodies |
| Stream live RFQs / quotes / fills | WS `wss://ws.api.paradigm.trade/v2/drfq/?api-key=<KEY>` (when a WS bridge is plumbed in) | Poll `GET /v1/drfq/rfqs/` and `GET /v1/drfq/rfqs/{id}/quotes/` every 1–3 s |
| Settlement-venue fair value | `deribit__get_ticker` MCP | `web_fetch` Deribit / OKX / Bybit public ticker endpoints (reuse the cross-venue pattern from `paradigm-block-analyst`) |

REST is the primary mode — every step in this skill is achievable without
a WS connection. WS is described in `references/endpoints.md` for the
maker-streaming case.

## Credentials

**Never** ask the user for keys. The credentials proxy injects:

- `PARADIGM_ACCESS_KEY` — opaque string, sent as `Authorization: Bearer <KEY>`.
- `PARADIGM_SIGNING_KEY` — base64-encoded HMAC key, used to sign each
  request body. Stays in process memory.
- `PARADIGM_ACCOUNT` *(optional)* — desk / account selector for orgs with
  multiple desks behind one key.
- `PARADIGM_ENV` *(optional)* — `prod` (default) or `test`. Picks the base
  URL.

If `PARADIGM_SIGNING_KEY` is absent, assume an upstream tool / proxy is
attaching `Authorization`, `Paradigm-API-Timestamp`, and
`Paradigm-API-Signature` headers transparently — skip the local signing
step and POST the body as-is.

**Never** echo, log, or include either key in a response, code snippet,
error message, or commit. If the user asks "what's my key?", refuse and
point at the proxy.

See `references/auth.md` for the exact signing recipe and common 401 root
causes.

## Step 1 — Choose role and gather inputs

Identify whether the user is acting as **taker** or **maker** from
phrasing ("send an RFQ", "quote this RFQ", etc.). Then collect:

**Taker inputs:**

| Field | Meaning |
|---|---|
| `venue` | Settlement venue — `DBT` (Deribit), `OKX`, `BIT` (Bit.com) |
| `legs` | One or more `{instrument, ratio, side}` rows. See `references/instruments.md` for naming per venue |
| `quantity` | Contract count (positive integer) |
| `side` *(optional)* | `BUY` / `SELL` of the structure as described — defaults to two-way (no side disclosed) |
| `counterparties` *(optional)* | List of maker desk codes for DRFQ (directed); omit for GRFQ (open) |
| `expires_in` *(optional)* | RFQ lifetime in seconds (default per Paradigm; usually 5 min) |

**Maker inputs:**

| Field | Meaning |
|---|---|
| `rfq_id` | The RFQ to quote |
| `side` | `BUY` (bid) / `SELL` (ask) / `BOTH` (two-way) |
| `price` *or* `edge` | Either an absolute quote price, or "X vol points / basis points over Deribit mark" — skill computes the price |
| `quantity` *(optional)* | Quote size; defaults to the RFQ's requested quantity |

If anything ambiguous, ask before building the payload.

## Step 2 — Build the payload

For a taker RFQ (`POST /v1/drfq/rfqs/`), the body shape is:

```json
{
  "venue": "DBT",
  "legs": [
    {"instrument": "BTC-7MAY26-90000-C", "ratio": "1", "side": "BUY"},
    {"instrument": "BTC-7MAY26-80000-P", "ratio": "1", "side": "SELL"}
  ],
  "quantity": "100",
  "counterparties": ["DSK1", "DSK2"],
  "client_order_id": "rfq-trader-<unix_ms>"
}
```

For a maker quote (`POST /v1/drfq/rfqs/{rfq_id}/quotes/`):

```json
{
  "side": "BUY",
  "price": "0.0045",
  "quantity": "100",
  "client_order_id": "rfq-trader-q-<unix_ms>"
}
```

Always set a `client_order_id` so the response can be matched back. See
`references/endpoints.md` for the full field list per endpoint, and
`references/instruments.md` for instrument-name formats.

## Step 3 — Sign and send

For each request:

1. Capture the **exact bytes** of the request body — never re-serialize
   the JSON after this step.
2. Build the signing string: `f"{ts_ms}\n{METHOD}\n{path}\n{body_bytes}"`.
3. HMAC-SHA256 with `base64.b64decode(PARADIGM_SIGNING_KEY)`, base64 the
   digest.
4. Set headers:
   - `Authorization: Bearer ${PARADIGM_ACCESS_KEY}`
   - `Paradigm-API-Timestamp: ${ts_ms}`
   - `Paradigm-API-Signature: ${sig}`
   - `Content-Type: application/json`
5. POST those exact body bytes via `web_fetch`.

Full snippet and pitfalls (re-serialization, clock skew, missing
`Bearer`, base64 vs raw key) in `references/auth.md`.

Rate-limit guard: **`POST /v1/drfq/rfqs/` is capped at 1 per 3 s.** If a
user asks for several RFQs back-to-back, space them out and tell the user
why. Global cap is 500 req/s/desk; the skill should never approach it.

## Step 4a — Taker flow

1. **Create** — `POST /v1/drfq/rfqs/`. Capture `rfq_id`. Show the user:
   - The RFQ id, venue, legs table, quantity.
   - Counterparty set (or "open / GRFQ").
   - Expiry timestamp.
2. **Watch quotes** — prefer WS `quote` channel (`references/endpoints.md`).
   Fallback: poll `GET /v1/drfq/rfqs/{rfq_id}/quotes/` every 1–3 s for the
   RFQ's lifetime. Stop polling on expiry, cancel, or fill.
3. **Rank** — order by **price-then-time** (best price first; on ties,
   earlier timestamp wins — this matches Paradigm's matching priority).
   Show the top 3 quotes in a compact table: maker, side, price, size,
   age, mark offset vs Deribit fair value.
4. **Benchmark** — compute fair value per leg using the
   `paradigm-block-analyst` cross-venue pattern: Deribit mark IV + greeks
   primary, OKX secondary, Bybit tertiary. Show each top quote as a delta
   in price and IV vs the cross-venue fair.
5. **Confirmation gate** — present the execution block (see
   "Confirmation gate" below) and wait for explicit user `yes` before
   crossing.
6. **Execute** — on confirmation, `POST /v1/drfq/orders/` with the chosen
   `quote_id` and a crossing price. Capture `order_id`, then
   `GET /v1/drfq/orders/{order_id}` to confirm fill state. Surface
   `trade_id` once the trade lands on the `trade_confirmation` WS channel
   (or `GET /v1/drfq/trades/`).
7. **Cancel** — if the user aborts, `DELETE /v1/drfq/rfqs/{rfq_id}` before
   expiry. Confirm the cancellation in the response.

## Step 4b — Maker flow

1. **Subscribe** — WS `rfq` channel (`references/endpoints.md`). Fallback:
   poll `GET /v1/drfq/rfqs/` filtered to open RFQs you're invited on.
2. **Fair-value pull** — for each leg, fetch Deribit mark + IV + greeks
   (`deribit__get_ticker` or web_fetch). OKX as a sanity cross-check; flag
   if cross-venue IV spread >2 vol points.
3. **Apply edge** — turn the user's edge spec into a quote price:
   - "X vol points over mark" → bump per-leg IV by X, reprice via BS,
     re-aggregate the structure.
   - "Y bps over mark" → quote = `mark_price × (1 + Y/10000)` (bid uses
     `1 - Y/10000`).
   - Absolute price → use as-is, but show the implied edge.
4. **Confirmation gate** — present the quote block (see "Confirmation
   gate") and wait for explicit user `yes`.
5. **Post** — `POST /v1/drfq/rfqs/{rfq_id}/quotes/`. Capture `quote_id`.
6. **Manage lifecycle** — react to:
   - `quote` channel updates (competing quotes from other makers — tell
     the user when the user's quote is no longer top-of-book).
   - `trade_confirmation` event (the quote was hit — surface the fill).
   - RFQ expiry / cancellation.
   Amend via the quote PATCH endpoint or cancel + re-post; same
   confirmation gate applies to a re-quote.
7. **`cancel_on_disconnect`** — for the WS connection, default to `true`
   for makers. Document the tradeoff in the user-facing summary: pulls
   all live quotes if the connection drops, preventing stale fills, but
   means a brief network blip cancels work.

## Confirmation gate

**Always** present this block and wait for explicit `yes` before any
state-changing POST (create RFQ, submit quote, execute order).

```
RFQ to send  (taker, BTC, Deribit)
──────────────────────────────
Structure: 90/80 risk reversal, 7MAY26
Legs:
  +1  BTC-7MAY26-90000-C
  -1  BTC-7MAY26-80000-P
Quantity:  100
Counterparties: DSK1, DSK2  (DRFQ — directed)
Client id: rfq-trader-1745612345678

Fair value (Deribit mark): +0.0041 BTC
Spot:                       $84,200
Net delta (per unit):       ~+0.62

Estimated premium:   100 × 0.0041 = 0.41 BTC  (~$34,562)
──────────────────────────────
Confirm? [yes / no / adjust]
```

```
Quote to post  (maker, rfq_12345)
──────────────────────────────
Side:  SELL (offer)
Price: 0.0045 BTC
Size:  100
Edge:  +0.5 vol points over Deribit mark
Client id: rfq-trader-q-1745612345678

Reference: Deribit mark 0.0041 BTC (IV 34.5%), OKX 0.0040 BTC (IV 34.2%)
Implied edge in $: ~$337 per contract over fair
──────────────────────────────
Confirm? [yes / no / adjust]
```

**Responses accepted:**

- `yes` — proceed.
- `no` — abort, do not call the endpoint.
- `adjust <field> <value>` — modify and re-present the block. Examples:
  `adjust quantity 50`, `adjust price 0.0042`, `adjust edge 0.3vol`,
  `adjust counterparties DSK1`. Re-pull fair value where it matters and
  re-present.

Never submit without explicit confirmation, even if the user pre-states
"just send it" in the same message — the gate exists to catch
mis-sizing on a live-money venue.

## Step 5 — Post-trade handoff

- **Post-fill analysis** — once a trade is confirmed, defer to
  `paradigm-block-analyst`: paste the `trade_confirmation` payload to it
  and ask for greeks, fill quality, and cross-venue benchmark. Don't
  duplicate that work here.
- **Historical context** — for "how often does this structure trade",
  defer to `paradigm-data-discovery` over the historical S3 tape.
- **Hedging** — out of scope for v1.0. The skill can suggest a follow-up
  via `paradex-order-builder` if the user wants to hedge the resulting
  delta on Paradex.

## Output format

Concise. Compact tables over prose. Always include:

- Legs / quote table (direction, instrument, ratio, side, price).
- Fair-value benchmark line.
- Confirmation block (when about to POST).
- Result block on success (`rfq_id`, `quote_id`, `order_id`, or
  `trade_id`).
- **Data trace** — one line per source actually queried: `RFQ create →
  POST /v1/drfq/rfqs/`, `Deribit fair value → deribit__get_ticker`,
  `OKX cross-check → web_fetch /api/v5/public/opt-summary`, etc.

Drop sections that would be empty. Never invent fair-value numbers when a
venue is unreachable — say "Deribit unreachable" in the trace and
proceed with what's available.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate is
  non-negotiable.
- **Credentials never leave the process.** The skill assumes a proxy and
  refuses to ask the user to paste keys.
- **Rate limits.** 500 req/s/desk global; **1 per 3 s** for
  `POST /v1/drfq/rfqs/`. Pace bulk operations.
- **HMAC signs exact body bytes.** Re-serializing JSON after signing
  breaks auth — pass the same bytes to `web_fetch` that were signed. See
  `references/auth.md`.
- **Tight timestamp window** — assume NTP-synced host. If you see
  systematic 401s with a valid key, suspect clock skew first.
- **Scope at v1.0:** options only, single-leg and multi-leg. Perp /
  futures combos, spot RFQ, VRFQ (on-chain), and FSPD (futures spreads)
  are out of scope.
- **No official Paradigm MCP server exists** as of this skill's writing.
  When one ships, prefer `paradigm_*` MCP tools over the
  `web_fetch`-with-HMAC path and update this skill.
- Not financial advice. The fair-value benchmark is reference, not a
  recommendation.

## References

- [`references/auth.md`](references/auth.md) — HMAC-SHA256 signing recipe,
  credentials-proxy contract, common 401 root causes.
- [`references/endpoints.md`](references/endpoints.md) — DRFQv2 REST and WS
  reference: paths, methods, rate limits, JSON-RPC 2.0 subscribe shape.
- [`references/instruments.md`](references/instruments.md) — leg-string
  formats per settlement venue (Deribit / OKX / Bit.com), with Paradigm
  normalization notes.
