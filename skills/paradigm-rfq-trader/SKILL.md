---
name: paradigm-rfq-trader
description: >
  End-to-end Paradigm.co DRFQv2 workflow for institutional options
  block trading via the official mcp-paradigm-py MCP server (with REST
  fallback). Covers both sides: takers building multi-leg options
  RFQs, sourcing maker quotes via paradigm_drfqv2_* tools, ranking
  price-then-time, crossing to execute; and makers polling incoming
  RFQs, pulling fair value from Deribit / Bybit, pricing with
  user-supplied edge, and managing the order lifecycle. Every
  state-changing action goes through an explicit confirmation gate —
  never auto-submits. Use when the user asks to "create an RFQ",
  "send a Paradigm block", "quote this RFQ", "stream Paradigm quotes",
  "hit the best bid", "price a strangle on Paradigm", "cancel rfq_X".
  Does NOT cover post-trade analysis of a filled block (use
  paradigm-block-analyst) or historical tape queries (use
  paradigm-data-discovery). Scope: options only, single- and
  multi-leg. Perp / futures combos and spot RFQ are out of scope.
compatibility: Prefer the official MCP server
  tradeparadigm/mcp-paradigm-py (PyPI `mcp-paradigm` or .mcpb bundle).
  Env vars PARADIGM_ACCESS_KEY + PARADIGM_SIGNING_KEY +
  PARADIGM_ENVIRONMENT. Fallback: direct DRFQv2 REST with the in-skill
  HMAC helper (references/auth.md). OneCLI useful for Bearer swap.
  Vault/KMS signers + OAuth + WebSocket are on the MCP roadmap, not
  shipped. Fair-value reuses deribit__get_ticker or web_fetch.
metadata:
  author: tradeparadex
  version: "3.0"
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
| **Taker** — sources liquidity | 1, 2, 3, 4a, 5 | `POST /v2/drfq/rfqs/`, `GET /v2/drfq/rfqs/{id}/orders/`, `GET /v2/drfq/rfqs/{id}/bbo/`, `POST /v2/drfq/orders/` (cross), `DELETE /v2/drfq/rfqs/{id}` |
| **Maker** — provides liquidity | 1, 2, 3, 4b, 5 | `GET /v2/drfq/rfqs/`, `POST /v2/drfq/orders/` (quote), `DELETE /v2/drfq/orders/{id}`, `GET/PATCH /v2/drfq/mmp/status/` |

> **DRFQv2 has no separate quote object.** What other venues call a
> "quote" is just an `Order` posted against an RFQ with `side: BUY`
> (bid) or `side: SELL` (offer). The same `POST /v2/drfq/orders/`
> endpoint handles both maker quoting and taker crossing — the
> difference is the side relative to the resting book.

Both roles share the signing and confirmation-gate plumbing. The skill
picks the role from user intent — if ambiguous, ask.

## Transport

| Capability | Primary | Fallback |
|---|---|---|
| Paradigm REST calls | **`mcp-paradigm-py` MCP tools** (recommended — handles HMAC signing in-process) | Direct `web_fetch` with HMAC-SHA256 headers (see `references/auth.md`) |
| Stream live RFQs / orders / fills | Polling via `paradigm_drfqv2_rfqs`, `paradigm_drfqv2_orders` (WS subscriptions are planned but not yet shipped in the MCP) | Direct WS at `wss://ws.api.paradigm.trade/v2/drfq/` when a WS bridge is wired in |
| Settlement-venue fair value | `deribit__get_ticker` MCP | `web_fetch` Deribit / OKX / Bybit public ticker endpoints (reuse the cross-venue pattern from `paradigm-block-analyst`) |

**Prefer the MCP path** wherever available — the skill body assumes
it. The direct-REST path is documented for environments where the MCP
server can't be installed (constrained agents, CI bots, etc.).

## MCP server — `mcp-paradigm-py`

[`tradeparadigm/mcp-paradigm-py`](https://github.com/tradeparadigm/mcp-paradigm-py)
is the official Paradigm MCP server. Status: Alpha. Surface: 39 tools
across DRFQv2, OBv1, FSPD, and firm-wide. The skill uses the DRFQv2
and foundational subsets.

### Installation

| Path | How |
|---|---|
| Claude Desktop | Download the latest `.mcpb` bundle from [releases](https://github.com/tradeparadigm/mcp-paradigm-py/releases); double-click; enter access key + signing key when prompted |
| Claude Code / generic MCP host | `pip install mcp-paradigm` (or `uvx mcp-paradigm` for one-shot) |
| Development | Clone the repo and `just install-dev` |

Config block for non-bundle setups:

```json
{
  "mcpServers": {
    "paradigm": {
      "command": "mcp-paradigm",
      "env": {
        "PARADIGM_ACCESS_KEY": "<access-key>",
        "PARADIGM_SIGNING_KEY": "<base64-signing-key>",
        "PARADIGM_ENVIRONMENT": "testnet"
      }
    }
  }
}
```

OneCLI still works for the access-key substitution if `HTTPS_PROXY` is
set in the MCP server's env — see `references/auth.md`. The signing
key lives in the MCP server's process; not in the agent's. Vault /
KMS / sidecar signers are on the roadmap.

### Tools the skill uses

| Tool | Purpose | Confirmation gate? |
|---|---|---|
| `paradigm_echo` | Signing self-test. First call after wiring | no |
| `paradigm_desk_overview` | Positions + MMP + platform state across all products in one call. Good "where am I?" opener | no |
| `paradigm_kill_switch` | Cancel ALL open orders / quotes across all products. **High blast radius** — only use when the user explicitly asks for an across-the-board pull | **yes — destructive** |
| `paradigm_drfqv2_instruments` | Look up the integer `instrument_id` for a venue-native instrument name | no |
| `paradigm_drfqv2_counterparties` | Resolve maker desks the user can RFQ | no |
| `paradigm_drfqv2_rfqs` | List RFQs (filter by role, state, venue, strategies) | no |
| `paradigm_drfqv2_rfq_snapshot` | Composite — RFQ + BBO + order book in one call. Right tool for "what's happening on rfq_X" | no |
| `paradigm_drfqv2_create_rfq` | Taker — create an RFQ | **yes** |
| `paradigm_drfqv2_orders` | List orders, filter by `rfq_id`, `state`, etc. | no |
| `paradigm_drfqv2_post_order` | Maker quote OR taker cross — same endpoint, side+TIF distinguish | **yes** |
| `paradigm_drfqv2_cancel` | Cancel an RFQ or order (or batch by filter) | no |
| `paradigm_drfqv2_trades` | Your cleared block trades | no |
| `paradigm_drfqv2_price_legs` | Multi-leg structure pricer — given bid/ask and legs, returns per-leg prices | no |
| `paradigm_drfqv2_mmp` | Maker circuit-breaker status; PATCH-style to re-arm | **yes** for reset |

There is no `paradigm_post_order`-style shorter name shipped — all
DRFQv2 tools carry the `paradigm_drfqv2_` prefix to disambiguate from
OBv1 / FSPD. The DESIGN.md in the MCP repo lists per-product surfaces
without the prefix as the *internal* grouping; the *exposed* tool
names include the product.

### What's not shipped yet

- WebSocket subscription tools (`paradigm_subscribe` /
  `paradigm_poll` / `paradigm_unsubscribe`). For now, poll the
  read-only tools every 1–3 s during an RFQ's lifetime.
- Production signers (Vault Transit, AWS KMS, sidecar) — only
  `EnvKeySigner` ships. Until Vault/KMS land, the signing key
  must be reachable to the MCP server's process.
- OAuth 2.1 inbound auth (planned per MCP spec 2025-03-26).
- Integration tests against testnet.

Track those in the MCP repo's DESIGN.md.

## Credentials

**Never** ask the user for keys. The Paradex environment uses
[**OneCLI**](https://onecli.sh) as a header-substitution proxy on
`HTTPS_PROXY`. OneCLI swaps a placeholder Bearer access key for the real
one in flight; it does **not** compute HMAC signatures. So the skill
**always** signs every request itself.

Env vars the skill expects at request time:

| Var | Filled by | Used by |
|---|---|---|
| `PARADIGM_ACCESS_KEY` | OneCLI rule (placeholder value in env, real value injected at proxy) | `Authorization: Bearer <KEY>` header |
| `PARADIGM_SIGNING_KEY` | Direct env (OneCLI does not proxy HMAC keys) | Local HMAC-SHA256 of every request |
| `PARADIGM_ACCOUNT` *(optional)* | Direct env | Multi-desk routing |
| `PARADIGM_ENV` *(optional)* | Direct env | `prod` (default) / `test` — picks the base URL |
| `HTTPS_PROXY` | OneCLI installer (default `http://localhost:10255`) | Routes the request through OneCLI |

The skill always emits all three Paradigm headers
(`Authorization`, `Paradigm-API-Timestamp`, `Paradigm-API-Signature`).
There is no signing fallback — if `PARADIGM_SIGNING_KEY` is absent,
fail fast and tell the user to set it; do **not** attempt unsigned
requests, they will 401.

If the user asks how to register their Paradigm key, walk them through
the OneCLI setup steps in `references/auth.md` ("Setting up your
Paradigm key in OneCLI"). Do not ask them to paste keys in chat — keys
go directly into the OneCLI admin dashboard.

**Never** echo, log, or include either key in a response, code snippet,
error message, or commit. If the user asks "what's my key?", refuse and
point at the OneCLI dashboard.

See `references/auth.md` for the signing recipe, the OneCLI setup steps,
and common 401 root causes. Run
[`references/test-signing.py`](references/test-signing.py) to verify the
signing helper end-to-end against pinned synthetic vectors.

## Step 1 — Choose role and gather inputs

Identify whether the user is acting as **taker** or **maker** from
phrasing ("send an RFQ", "quote this RFQ", etc.). Then collect:

**Taker inputs:**

| Field | Meaning |
|---|---|
| `venue` | Settlement venue — `BIT` (Bit.com), `BYB` (Bybit), `DBT` (Deribit), `PRDX` (Paradex) |
| `legs` | One or more `{instrument_id, ratio, side, price?}` rows. `instrument_id` is **Paradigm's integer id** — look up via `paradigm_drfqv2_instruments` (MCP) or `GET /v2/drfq/instruments/?venue=<v>&venue_instrument_name=<name>` (REST fallback) first |
| `quantity` | Decimal-string quantity |
| `counterparties` | List of maker **desk names** (from `GET /v2/drfq/counterparties/`) for directed RFQ; empty array for open |
| `is_taker_anonymous` *(optional)* | Hide taker identity from makers |
| `account_name` | Caller-supplied account label |
| `label` | Caller-supplied idempotency tag (e.g. `rfq-trader-<unix_ms>`) |

**Maker inputs:**

| Field | Meaning |
|---|---|
| `rfq_id` | RFQ to quote against |
| `side` | `BUY` (post a bid) or `SELL` (post an offer). Two-way = post two orders |
| `price` *or* `edge` | Absolute structure price, or "X vol points / basis points over Deribit mark" — skill computes |
| `quantity` *(optional)* | Order size; defaults to RFQ's quantity |
| `type` | `LIMIT` (default) or `HIDDEN` |
| `time_in_force` | `GOOD_TILL_CANCELED` (resting quote) or `FILL_OR_KILL` (cross) |

If anything ambiguous, ask before building the payload.

## Step 2 — Resolve instruments and build the payload

**Sub-step 2a — resolve instrument IDs.** Paradigm references legs by
integer `instrument_id`, not by venue-native name.

MCP path: call `paradigm_drfqv2_instruments(venue="DBT",
venue_instrument_name="BTC-7MAY26-90000-C")` and capture
`results[0].id`. Cache for the session; do not invent ids.

REST fallback: `GET /v2/drfq/instruments/?venue=DBT&venue_instrument_name=BTC-7MAY26-90000-C`.

**Sub-step 2b — taker RFQ.**

MCP path: `paradigm_drfqv2_create_rfq(...)` with the parameters below
(typed by the tool surface; the agent doesn't construct JSON).

REST fallback payload (`POST /v2/drfq/rfqs/`):

```json
{
  "account_name": "trading-desk-1",
  "counterparties": ["LP1", "LP2"],
  "is_taker_anonymous": false,
  "label": "rfq-trader-1745612345678",
  "legs": [
    {"instrument_id": 12345, "ratio": "1", "side": "BUY",  "price": "0.0041"},
    {"instrument_id": 12346, "ratio": "1", "side": "SELL", "price": "0.0020"}
  ],
  "quantity": "100",
  "venue": "DBT",
  "state": "RFQState.OPEN"
}
```

**Sub-step 2c — maker quote OR taker cross.**

Same endpoint either way. MCP: `paradigm_drfqv2_post_order(...)`. The
side + time_in_force distinguish:

- Maker quote: `side=BUY` (bid) or `SELL` (offer), `time_in_force="GOOD_TILL_CANCELED"`
- Taker cross: `side=` opposite of the resting order you're taking, `time_in_force="FILL_OR_KILL"`

REST fallback payload (`POST /v2/drfq/orders/`):

```json
{
  "account_name": "trading-desk-1",
  "label": "rfq-trader-q-1745612345678",
  "rfq_id": "rfq_abc123",
  "side": "BUY",
  "type": "LIMIT",
  "time_in_force": "GOOD_TILL_CANCELED",
  "price": "0.0045",
  "quantity": "100",
  "legs": [{"instrument_id": 12345, "price": "0.0045"}]
}
```

Always set a `label` for idempotency. See `references/endpoints.md` for
full field lists and enums, `references/instruments.md` for the lookup
flow and venue-native naming.

## Step 3 — Sign and send

MCP path: nothing to do — `mcp-paradigm-py` signs every request in its
own process. Just call the tool.

REST fallback (manual signing):

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

Rate limits are not declared in the OpenAPI spec — pace bulk RFQ /
order creation and back off on 429. For makers, watch
`GET /v2/drfq/mmp/status/` for the per-desk circuit-breaker flag;
PATCH to re-arm after recovery.

## Step 4a — Taker flow

1. **Create the RFQ** — MCP: `paradigm_drfqv2_create_rfq(...)`. REST
   fallback: `POST /v2/drfq/rfqs/`. Capture `rfq_id`. Show the user:
   RFQ id, venue, legs table, quantity, counterparty set (or "open"),
   expiry.
2. **Watch resting orders against the RFQ** — MCP:
   `paradigm_drfqv2_rfq_snapshot(rfq_id=...)` polled every 1–3 s
   (returns RFQ + BBO + order book in one call). REST fallback: poll
   `GET /v2/drfq/rfqs/{rfq_id}/orders/` and
   `GET /v2/drfq/rfqs/{rfq_id}/bbo/`. WS subscriptions are planned but
   not shipped — polling is the only option today.
3. **Rank** — order by price (best price first); when prices tie,
   earlier timestamp wins. Show the top 3 in a compact table: desk,
   side, price, size, age, mark offset vs Deribit fair value.
4. **Benchmark** — pull Deribit fair value cross-venue (reuse
   `paradigm-block-analyst` pattern: Deribit mark IV + greeks primary,
   Bybit secondary). Show each top order as a delta vs the cross-venue
   fair.
5. **Confirmation gate** — present the execution block (see
   "Confirmation gate") and wait for explicit user `yes`.
6. **Cross** — MCP: `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="FILL_OR_KILL", price=..., quantity=...,
   legs=[...])`. REST fallback: `POST /v2/drfq/orders/`. `side` is
   opposite the resting order you're taking; `price` meets or beats
   the resting one. The endpoint is async-first — the response carries
   `state: OrderState.PENDING` even on success. Poll
   `paradigm_drfqv2_orders(...)` (or REST `GET /v2/drfq/orders/{id}`)
   for the transition to `CLOSED`. Surface the `trade_id` from
   `paradigm_drfqv2_trades(rfq_id=...)` once the fill lands.
7. **Cancel** — MCP: `paradigm_drfqv2_cancel(...)`. REST fallback:
   `DELETE /v2/drfq/rfqs/{rfq_id}`. Confirm in the response.

## Step 4b — Maker flow

1. **Find open RFQs** — poll `paradigm_drfqv2_rfqs(state="RFQState.OPEN",
   role="AuctionRole.MAKER")` every 1–3 s. WS `rfq` subscription is
   planned but not shipped — polling is the only option today.
2. **Fair-value pull** — for each leg, fetch Deribit mark + IV +
   greeks (`deribit__get_ticker` or web_fetch). Bybit as a sanity
   cross-check; flag IV divergence >2 vol points.
3. **Optional pricing helper** — for multi-leg structures, call
   `paradigm_drfqv2_price_legs(...)` with the leg list and a target
   bid/ask; it returns per-leg prices that sum to the structure price.
4. **Apply edge** — turn the user's edge spec into an order price:
   - "X vol points over mark" → bump per-leg IV by X, reprice via BS,
     re-aggregate.
   - "Y bps over mark" → `price = mark_price × (1 + Y/10000)` for ask;
     `× (1 - Y/10000)` for bid.
   - Absolute price → show the implied edge for confirmation.
5. **Confirmation gate** — present the order block and wait for
   explicit user `yes`.
6. **Post the order** — MCP: `paradigm_drfqv2_post_order(rfq_id=...,
   side=...(BUY for bid, SELL for offer), type="LIMIT",
   time_in_force="GOOD_TILL_CANCELED", price=..., quantity=...,
   legs=[...])`. Capture `order_id`. For two-way quoting, post two
   orders.
7. **Manage lifecycle** — without WS, you poll. Each 1–3 s:
   - `paradigm_drfqv2_orders(rfq_id=...)` — competing orders. Surface
     when the user is no longer top-of-book.
   - `paradigm_drfqv2_trades(rfq_id=...)` — surface any fill.
   - `paradigm_drfqv2_rfqs` filtered by id — RFQ expiry / cancellation
     by the taker.
   - `paradigm_drfqv2_mmp` — circuit-breaker status. If
     `rate_limit_hit: true`, all the desk's orders are paused.
     `paradigm_drfqv2_mmp` reset call re-arms after investigation.
   Amend by re-posting (cancel + new order); same confirmation gate
   applies.
8. **`cancel_on_disconnect`** — applies when WS lands. For now (poll
   only), nothing to configure.

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
  paradigm_drfqv2_create_rfq`, `Deribit fair value →
  deribit__get_ticker`, `Bybit cross-check → web_fetch v5 tickers`,
  etc.

Drop sections that would be empty. Never invent fair-value numbers when a
venue is unreachable — say "Deribit unreachable" in the trace and
proceed with what's available.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate is
  non-negotiable.
- **Credentials never leave the process.** The skill assumes OneCLI for
  the access key (placeholder swap at proxy) and direct env for the
  signing key (HMAC happens in-process). Refuse to ask the user to
  paste keys in chat — direct them to the OneCLI dashboard.
- **Rate limits not in the OpenAPI spec.** Pace bulk operations and
  back off on 429. Makers: watch MMP (`GET /v2/drfq/mmp/status/`).
- **HMAC signs exact body bytes.** Re-serializing JSON after signing
  breaks auth — pass the same bytes to `web_fetch` that were signed. See
  `references/auth.md`.
- **Timestamp window is ±30 s of server time.** Assume NTP-synced
  host. On systematic 401s with a valid key, suspect clock skew first.
  `GET /v2/drfq/echo/` is the canonical signing self-test.
- **Scope at v1.0:** options only, single-leg and multi-leg. Perp /
  futures combos, spot RFQ, VRFQ (on-chain), and FSPD (futures spreads)
  are out of scope.
- **OpenAPI spec lives in `tradeparadigm/mono#34164`.** The MCP server
  in `tradeparadigm/mcp-paradigm-py` is generated against it; this
  skill's REST fallback details are aligned to the same spec.
- **`mcp-paradigm-py` is Alpha.** WebSocket subscriptions, OAuth 2.1
  inbound auth, and the Vault Transit / AWS KMS / sidecar signers are
  designed but not yet shipped — only the `EnvKeySigner` is in this
  release. Until those land, the signing key still lives in the MCP
  server's process (which is the agent's child process when installed
  via `.mcpb` bundle). For Paradex environments that need stronger
  isolation, ship the in-skill REST fallback instead and run the
  signing through a Vault Transit gate locally.
- Not financial advice. The fair-value benchmark is reference, not a
  recommendation.

## References

- [`references/auth.md`](references/auth.md) — HMAC-SHA256 signing recipe,
  OneCLI setup (placeholder substitution, dashboard fields, env vars,
  `HTTPS_PROXY`), and common 401 root causes.
- [`references/test-signing.py`](references/test-signing.py) — runnable
  self-test of the signing helper with pinned synthetic vectors. Run with
  `python3` to verify any change to the signing code.
- [`references/endpoints.md`](references/endpoints.md) — DRFQv2 REST and WS
  reference: paths, methods, rate limits, JSON-RPC 2.0 subscribe shape.
- [`references/instruments.md`](references/instruments.md) — leg-string
  formats per settlement venue (Deribit / OKX / Bit.com), with Paradigm
  normalization notes.
- [`references/mcp-design.md`](references/mcp-design.md) — working
  design spec for the upcoming `mcp-paradigm-py` server and its
  `paradigm-py` SDK. Tool surface, OAuth scopes, signing-layer
  interface, repo layout. Move to the new repo as `DESIGN.md` once
  that repo exists.
