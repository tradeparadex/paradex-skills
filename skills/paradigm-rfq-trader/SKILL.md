---
name: paradigm-rfq-trader
description: >
  Trigger institutional block trades via Paradigm's DRFQv2 flow. The
  workflow is venue-agnostic — resolve instruments, build the RFQ /
  order payload, benchmark, run a confirmation gate, submit, verify
  settlement. Per-venue specifics (fair-value sources, naming
  conventions, edge syntax, settlement checks) live in
  references/venues.md. In scope today: PRDX (Paradex, primary focus)
  and DBT (Deribit). Adding more DRFQv2 venues is a references/venues.md
  edit, not a skill-body change. Covers takers (build, benchmark,
  cross) and makers (poll, price, manage). Every state-changing action
  goes through an explicit confirmation gate. Use when the user asks
  to "send a Paradigm block RFQ", "block-trade X BTC", "send a BTC
  straddle on Paradex / Deribit", "quote rfq_X", "hit the best bid",
  "cancel rfq_X". Does NOT cover small Paradex order-book trades
  (paradex-order-builder), post-trade analysis (paradigm-block-analyst),
  historical tape (paradigm-data-discovery).
compatibility: >
  Requires mcp-paradigm-py
  (github.com/tradeparadigm/mcp-paradigm-py). Per-venue fair-value
  dependencies are documented in references/venues.md: mcp-paradex-py
  for PRDX RFQs; deribit__get_ticker MCP or web_fetch for DBT RFQs.
  Install mcp-paradigm via .mcpb bundle (Claude Desktop) or
  `pip install mcp-paradigm` / `uvx mcp-paradigm`. Env vars set in
  the MCP server: PARADIGM_ACCESS_KEY, PARADIGM_SIGNING_KEY,
  PARADIGM_ENVIRONMENT=testnet|prod. REST fallback in
  references/auth.md.
metadata:
  author: tradeparadex
  version: "5.0"
---

# Paradigm RFQ Trader

Drives the Paradigm DRFQv2 lifecycle — taker and maker — through the
`mcp-paradigm-py` MCP server. The skill owns workflow and the
confirmation gate; the MCP server owns transport, auth, and signing;
`references/venues.md` owns everything that varies between settlement
venues.

## Scope

| Venue | Status |
|---|---|
| `PRDX` (Paradex) | **Primary focus.** Perp, dated future, option |
| `DBT` (Deribit) | Supported. Option is the dominant product; perp/future also supported |
| `BYB` (Bybit), `BIT` (Bit.com) | Out of scope at this version. Add by appending to `references/venues.md` |

See [`references/venues.md`](references/venues.md) for the per-venue
recipe (naming, fair-value tools, edge syntax, settlement check).

**Out of scope at this skill version:**

- Small / liquid orders on Paradex's central order book →
  `paradex-order-builder`.
- Post-trade analysis of a filled block → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- Heavy options pricing math (greek formulas, IV surface fitting)
  → defer to `paradex-options-pricer` patterns. The math is the
  same regardless of settlement venue.

## Trigger

Fire on live RFQ-lifecycle intent. Examples:

- *"send a block RFQ for 500 BTC perp"*
- *"send a BTC 8MAY26 90/80 risk reversal on Paradex"*
- *"Deribit BTC strangle, 100 contracts, send the RFQ"*
- *"quote rfq_12345 at 2 bps over mid"*
- *"quote rfq_X at +0.5 vol over mark IV"*
- *"hit the best bid on this RFQ"*
- *"cancel rfq_12345"*

If the user doesn't specify a venue, ask — don't guess. The choice
(PRDX vs DBT) determines counterparties, settlement, and fees.

Do **not** fire on:

- Direct Paradex order-book trades → `paradex-order-builder`.
- Post-trade analysis of a filled block JSON → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- RFQs on Bybit / Bit.com — currently out of scope.

## MCP tools used

From `mcp-paradigm-py` (RFQ workflow):

| Tool | Purpose | Confirmation? |
|---|---|---|
| `paradigm_echo` | Signing self-test; first call after wiring | no |
| `paradigm_desk_overview` | Positions + MMP + platform state across all products | no |
| `paradigm_kill_switch` | Cancel ALL open orders across all products | **yes — destructive** |
| `paradigm_drfqv2_instruments` | Resolve venue-native name → integer `instrument_id`; returns `kind` used in Step 3 | no |
| `paradigm_drfqv2_counterparties` | Maker desk names for directed RFQs | no |
| `paradigm_drfqv2_rfqs` | List RFQs (filter by `role`, `state`, `venue`, `strategies`) | no |
| `paradigm_drfqv2_rfq_snapshot` | Composite — RFQ + BBO + order book in one call | no |
| `paradigm_drfqv2_create_rfq` | Taker creates an RFQ | **yes** |
| `paradigm_drfqv2_orders` | List orders, filter by `rfq_id` / `state` | no |
| `paradigm_drfqv2_post_order` | Maker quote OR taker cross (side + TIF distinguish) | **yes** |
| `paradigm_drfqv2_cancel` | Cancel RFQ or order (single or batch by filter) | no |
| `paradigm_drfqv2_trades` | Your cleared block trades | no |
| `paradigm_drfqv2_price_legs` | Multi-leg structure pricer (bid/ask in → per-leg out) | no |
| `paradigm_drfqv2_mmp` | Maker circuit-breaker — status or reset | **yes** for reset |

The skill also calls **venue-specific fair-value tools** per
`references/venues.md` — e.g. `paradex_bbo`, `paradex_market_summaries`,
`deribit__get_ticker`. Which exact tools depends on the RFQ's
settlement venue and the instrument's `kind`.

WebSocket subscriptions are designed but not yet shipped in the
Paradigm MCP — poll the read tools at 1–3 s during an active RFQ.

## Setup

| Path | How |
|---|---|
| Claude Desktop | `.mcpb` bundle from [releases](https://github.com/tradeparadigm/mcp-paradigm-py/releases); double-click; enter keys when prompted |
| Claude Code / generic | `pip install mcp-paradigm` (or `uvx mcp-paradigm`), config block below |

```json
{
  "mcpServers": {
    "paradigm": {
      "command": "mcp-paradigm",
      "env": {
        "PARADIGM_ACCESS_KEY": "<key>",
        "PARADIGM_SIGNING_KEY": "<base64>",
        "PARADIGM_ENVIRONMENT": "testnet"
      }
    }
  }
}
```

Never ask the user to paste keys into chat — direct them to the MCP
config. Refuse to echo or log any `PARADIGM_*` value. If the user
asks "what's my key?", say it lives in the MCP server config and
point at the MCP repo.

## Roles

| Role | Steps |
|---|---|
| **Taker** — sources liquidity | 1, 2, 3a, 4 |
| **Maker** — provides liquidity | 1, 2, 3b, 4 |

DRFQv2 has no separate quote object. Maker quoting and taker crossing
both call `paradigm_drfqv2_post_order` — only `side` and
`time_in_force` differ (GTC for maker, FOK for taker cross).

## Step 1 — Gather inputs

Identify role and venue from the user's phrasing. If venue is
ambiguous, ask.

**Taker:**

| Field | Meaning |
|---|---|
| `venue` | `PRDX` or `DBT` (see scope table; ask if unspecified) |
| `legs` | `{instrument_id, ratio, side, price?}` rows. Outright = 1 leg; spread / straddle / RR = 2 legs; condors etc. = more |
| `quantity` | Decimal string in base units |
| `counterparties` | Desk names from `paradigm_drfqv2_counterparties`. Empty = open / GRFQ-style |
| `is_taker_anonymous` | Hide identity from makers (optional) |
| `account_name`, `label` | Account label + idempotency tag |

**Maker:**

| Field | Meaning |
|---|---|
| `rfq_id` | RFQ to quote — fetch it first to learn `venue` + `kind` |
| `side` | `BUY` (bid) / `SELL` (offer). Two-way = two `post_order` calls |
| `price` or `edge` | Absolute price, or an edge spec interpreted per `references/venues.md` for that RFQ's venue |
| `quantity` | Defaults to RFQ quantity |
| `type` | `LIMIT` (default) or `HIDDEN` |
| `time_in_force` | `GOOD_TILL_CANCELED` (rest) or `FILL_OR_KILL` (cross) |

If anything is ambiguous, ask before calling tools.

## Step 2 — Resolve instrument IDs

Paradigm references legs by integer `instrument_id`. For each leg:

```
paradigm_drfqv2_instruments(venue=<venue>, venue_instrument_name=<name>)
```

Capture `results[0].id` and `results[0].kind`. The `kind` (`OPTION`
vs `FUTURE`) drives the fair-value approach in Step 3.

For venue-native instrument naming, see
[`references/venues.md`](references/venues.md). Cache id + kind
for the session; do not invent IDs.

## Step 3a — Taker flow

1. **Create the RFQ** — `paradigm_drfqv2_create_rfq(venue=..., legs=[...],
   quantity=..., counterparties=[...], account_name=..., label=...)`.
   Capture `rfq_id`. Show: id, venue, legs, quantity, counterparties,
   expiry.
2. **Watch resting orders** — poll
   `paradigm_drfqv2_rfq_snapshot(rfq_id=...)` every 1–3 s. Returns
   RFQ + BBO + asks/bids in one call.
3. **Rank** — best price first; on ties, earlier timestamp wins.
   Show top 3: desk, side, price, size, age, offset vs fair value.
4. **Benchmark** — follow the venue's fair-value recipe in
   `references/venues.md` (which depends on `venue` and `kind`).
   Surface each top RFQ order as `price − fair` in the venue's
   natural units (bps for linear; absolute + implied vol bump for
   options).
5. **Confirmation gate** (see below). Wait for explicit `yes`.
6. **Cross** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="FILL_OR_KILL", price=..., quantity=...,
   legs=[...])`. `side` is opposite the resting order being taken.
   Response is async-first (`state: OrderState.PENDING`) — poll
   `paradigm_drfqv2_orders` for the transition to `CLOSED`. Surface
   `trade_id` from `paradigm_drfqv2_trades(rfq_id=...)` once cleared,
   then follow the venue's settlement-check recipe in
   `references/venues.md`.
7. **Cancel** — on abort, `paradigm_drfqv2_cancel(rfq_id=...)`.

## Step 3b — Maker flow

1. **Find open RFQs** — poll
   `paradigm_drfqv2_rfqs(state="RFQState.OPEN", role="AuctionRole.MAKER")`
   every 1–3 s. Filter by `venue` if the user only wants certain
   venues.
2. **Fair value** — follow the venue's fair-value recipe in
   `references/venues.md`.
3. **Optional pricing helper** — for multi-leg structures, call
   `paradigm_drfqv2_price_legs(bid_price=..., ask_price=..., legs=[...])`
   to split a structure price across legs the way Paradigm will.
4. **Apply edge** — the edge syntax depends on the venue; see
   `references/venues.md`. Common shapes:
   - Linear: "Y bps over mid", "tighten the BBO by Z".
   - Option: "X vol over mark IV", "Y bps over option mark",
     absolute price.
   Show the implied edge before going to the gate.
5. **Confirmation gate**. Wait for explicit `yes`.
6. **Post** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="GOOD_TILL_CANCELED", price=...,
   quantity=..., legs=[...])`. Two-way = two calls.
7. **Manage lifecycle** — poll each 1–3 s:
   - `paradigm_drfqv2_orders(rfq_id=...)` — surface when no longer
     top-of-book.
   - `paradigm_drfqv2_trades(rfq_id=...)` — surface fills.
   - `paradigm_drfqv2_mmp()` — circuit-breaker status. If
     `rate_limit_hit: true`, all desk orders are paused; reset to
     re-arm (gated).
   Amend by cancel + new post; same confirmation gate.

## Step 4 — Confirmation gate

**Always** present this block and wait for explicit `yes` before any
state-changing tool call (`create_rfq`, `post_order`, `kill_switch`,
`mmp` reset).

Canonical taker example (PRDX perp; same structure for any venue —
swap in the venue's fair-value section per `references/venues.md`):

```
RFQ to send  (taker, BTC perp, settles on Paradex)
──────────────────────────────
Instrument: BTC-USD-PERP  (id 98765)
Side:       BUY
Quantity:   500 BTC
Counterparties: LP1, LP2, LP3  (directed)
Label:      rfq-trader-1745612345678

Fair-value reference (per references/venues.md, venue=PRDX, kind=FUTURE):
  BBO:        $96,450 / $96,460   (spread 10 bps)
  Mid:        $96,455
  Walked ask (for 500 BTC): ~$96,612  (~16 bps slippage)

Est. notional: 500 × $96,455 = $48.23M
──────────────────────────────
Confirm? [yes / no / adjust]
```

For options or for Deribit, the **structure of the block is the
same** — header line, leg(s) listed, fair-value reference, sizing
line — but the fair-value section is shaped per
`references/venues.md` for that venue + kind. For example, options
show per-leg `mark + mark_iv + delta + vega` and an aggregated
structure mark + net greeks; Deribit options show prices in BTC
terms (not USD).

**Responses:** `yes` → call the tool. `no` → abort. `adjust <field>
<value>` → re-render. Common adjust verbs:

- Linear: `adjust price`, `adjust quantity`, `adjust edge (bps)`.
- Option: `adjust quantity`, `adjust edge (vol)`,
  `adjust counterparties`.

Re-pull the venue's fair-value reference before re-rendering.

Never submit without explicit confirmation — even if the user
pre-states "just send it" in the same message.

## Post-trade handoff

- **Post-fill analysis** — pass the trade JSON to
  `paradigm-block-analyst` for fill-quality benchmarking.
- **Settlement verification** — venue-specific. See the "Settlement
  check" subsection per venue in `references/venues.md`.
- **Historical context** — `paradigm-data-discovery` over the S3
  tape.
- **Hedging the new exposure** — `paradex-order-builder` for Paradex
  delta hedges.

## Output format

Compact tables over prose. Always include:

- Instrument + side + quantity line (or legs table for multi-leg).
- Fair-value reference shaped per the venue's recipe in
  `references/venues.md`.
- Confirmation block when about to call a state-changing tool.
- Result block on success (`rfq_id`, `order_id`, `trade_id`).
- **Data trace** — one line per source actually called. Concrete
  tool names, not generic descriptions. Example for a PRDX option
  taker flow:
  `Instrument lookup → paradigm_drfqv2_instruments`,
  `Fair value → paradex_market_summaries`,
  `Spot → paradex_market_summaries (BTC-USD-PERP)`,
  `RFQ create → paradigm_drfqv2_create_rfq`.

Drop empty sections. Never invent fair-value numbers when a venue
data source is unreachable — say so in the trace.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate
  is non-negotiable.
- **Credentials live in the MCP server's env, not in chat.** Refuse
  to echo `PARADIGM_*` values or ask the user to paste them; direct
  them to the MCP config.
- **Async-first orders.** `post_order` returns `PENDING`; poll
  `paradigm_drfqv2_orders` for terminal state.
- **MCP server is Alpha.** WebSocket subscriptions, OAuth 2.1, and
  production signers (Vault Transit / AWS KMS / sidecar) are
  designed but not yet shipped — only `EnvKeySigner` is in this
  release. The signing key lives in the MCP server's process until
  those land.
- **Venue scope:** PRDX (primary) + DBT today. Adding a venue is a
  `references/venues.md` edit, not a skill-body change. Bybit,
  Bit.com, and any future DRFQv2 venue plug in the same way.
- **REST fallback exists** for environments that can't install the
  MCP — see `references/auth.md`. The OpenAPI spec at
  `tradeparadigm/mono#34164` is the authoritative endpoint
  reference; this skill won't duplicate it.
- Not financial advice. Fair-value benchmarks are reference, not a
  recommendation.

## References

- [`references/venues.md`](references/venues.md) — **per-venue
  cookbook**: naming, fair-value tools, edge syntax, settlement
  check. The first place to look when extending the skill.
- [`references/instruments.md`](references/instruments.md) —
  venue-independent enum semantics (kinds, margin kinds, strategy
  codes / `StrategyCodeEnum`).
- [`references/auth.md`](references/auth.md) — REST-fallback HMAC
  signing scheme. Only relevant if the MCP server isn't available.
- [`references/test-signing.py`](references/test-signing.py) —
  runnable self-test of the REST-fallback signing helper.

For endpoint paths, payload shapes, and enums in the REST-fallback
path, read the OpenAPI spec at
[`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164)
directly. The MCP server is generated from it.
