---
name: paradigm-rfq-trader
description: >
  End-to-end Paradigm.co DRFQv2 workflow for institutional options block
  trading via the official mcp-paradigm-py MCP server. Covers takers
  (build multi-leg RFQs, watch resting orders, cross to execute) and
  makers (poll incoming RFQs, pull cross-venue fair value, price with
  user-supplied edge, manage orders). Every state-changing action goes
  through an explicit confirmation gate — never auto-submits. Use when
  the user asks to "create an RFQ", "send a Paradigm block", "quote
  this RFQ", "stream Paradigm quotes", "hit the best bid", "price a
  strangle on Paradigm", "cancel rfq_X". Does NOT cover post-trade
  analysis of a filled block (use paradigm-block-analyst) or historical
  tape queries (use paradigm-data-discovery). Scope: options only,
  single- and multi-leg. Perp / futures combos and spot RFQ are out of
  scope.
compatibility: >
  Requires the mcp-paradigm-py MCP server
  (github.com/tradeparadigm/mcp-paradigm-py). Install via the .mcpb
  bundle for Claude Desktop or `pip install mcp-paradigm` /
  `uvx mcp-paradigm` for other hosts. Env vars set in the MCP server:
  PARADIGM_ACCESS_KEY, PARADIGM_SIGNING_KEY,
  PARADIGM_ENVIRONMENT=testnet|prod. Fair-value lookups reuse
  deribit__get_ticker MCP or web_fetch. A direct-REST fallback is
  documented in references/auth.md for environments without the MCP.
metadata:
  author: tradeparadex
  version: "3.1"
---

# Paradigm RFQ Trader

Drives the full Paradigm DRFQv2 lifecycle — taker and maker — through
the `mcp-paradigm-py` MCP server. The skill owns workflow, fair-value
benchmarking, and the confirmation gate; the MCP server owns transport,
auth, and signing.

## Trigger

Fire on live RFQ-lifecycle intent. Examples:

- *"create an RFQ for a BTC strangle"*
- *"send a 100-lot 7MAY26 90/100 call spread on Paradigm"*
- *"quote rfq_12345 at 0.5 vol over Deribit mark"*
- *"hit the best quote on this RFQ"*
- *"cancel rfq_12345"*

Do **not** fire on:

- Post-trade analysis of a filled block JSON → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- Paradex DEX order placement → `paradex-order-builder`.

## MCP tools used

| Tool | Purpose | Confirmation? |
|---|---|---|
| `paradigm_echo` | Signing self-test; first call after wiring | no |
| `paradigm_desk_overview` | Positions + MMP + platform state in one call | no |
| `paradigm_kill_switch` | Cancel ALL open orders across all products | **yes — destructive** |
| `paradigm_drfqv2_instruments` | Resolve venue-native name → Paradigm `instrument_id` | no |
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

WebSocket subscriptions are designed but not yet shipped in the MCP —
poll the read tools at 1–3 s during an active RFQ.

## Setup

Server installation lives in the MCP repo. Quick paths:

- **Claude Desktop:** download the latest `.mcpb` bundle from
  [releases](https://github.com/tradeparadigm/mcp-paradigm-py/releases),
  double-click, enter access key + signing key when prompted.
- **Claude Code / generic host:** `pip install mcp-paradigm` (or
  `uvx mcp-paradigm`), then add to the MCP config:

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
config or the `.mcpb` setup prompt. Refuse to echo or log any
`PARADIGM_*` value. If the user asks "what's my key?", say it lives in
the MCP server config and point at the MCP repo.

## Roles

| Role | Steps |
|---|---|
| **Taker** — sources liquidity | 1, 2, 3a, 4 |
| **Maker** — provides liquidity | 1, 2, 3b, 4 |

DRFQv2 has no separate quote object. Maker quoting and taker crossing
both call `paradigm_drfqv2_post_order` — only `side` and
`time_in_force` differ (GTC for maker, FOK for taker cross).

## Step 1 — Gather inputs

Identify role from phrasing ("send an RFQ" = taker, "quote this RFQ" =
maker). Collect:

**Taker:**

| Field | Meaning |
|---|---|
| `venue` | `BIT` (Bit.com), `BYB` (Bybit), `DBT` (Deribit), `PRDX` (Paradex) |
| `legs` | `{instrument_id, ratio, side, price?}` rows |
| `quantity` | Decimal string |
| `counterparties` | Desk names from `paradigm_drfqv2_counterparties` (empty = open) |
| `is_taker_anonymous` | Hide taker identity from makers (optional) |
| `account_name`, `label` | Account label + idempotency tag |

**Maker:**

| Field | Meaning |
|---|---|
| `rfq_id` | RFQ to quote against |
| `side` | `BUY` (bid) / `SELL` (offer). Two-way = two `post_order` calls |
| `price` or `edge` | Absolute price, or "X vol over mark" / "Y bps over mark" |
| `quantity` | Defaults to RFQ quantity |
| `type` | `LIMIT` (default) or `HIDDEN` |
| `time_in_force` | `GOOD_TILL_CANCELED` (rest) or `FILL_OR_KILL` (cross) |

If anything is ambiguous, ask before calling tools.

## Step 2 — Resolve instrument IDs

Paradigm references legs by integer `instrument_id`. For each leg:

```
paradigm_drfqv2_instruments(venue="DBT", venue_instrument_name="BTC-7MAY26-90000-C")
```

Capture `results[0].id`. Cache for the session; do not invent IDs.

Venue-native naming summary (see [`references/instruments.md`](references/instruments.md)
for full table):

| Venue | Format | Example |
|---|---|---|
| `DBT` (Deribit) | `BTC-DDMMMYY-STRIKE-C/P` (day not zero-padded) | `BTC-7MAY26-90000-C` |
| `BYB` (Bybit) | `BTC-DDMMMYY-STRIKE-C/P` (day zero-padded) | `BTC-07MAY26-90000-C` |
| `BIT` (Bit.com) | `BTC-DDMMMYY-STRIKE-C/P` (day zero-padded) | `BTC-07MAY26-90000-C` |
| `PRDX` (Paradex) | `<BASE>-USD-PERP` or `<BASE>-USD-<EXPIRY>` | `BTC-USD-PERP` |

## Step 3a — Taker flow

1. **Create the RFQ** — `paradigm_drfqv2_create_rfq(venue=..., legs=[...],
   quantity=..., counterparties=[...], account_name=..., label=...)`.
   Capture `rfq_id`. Show: id, venue, legs, quantity, counterparties,
   expiry.
2. **Watch resting orders** — poll
   `paradigm_drfqv2_rfq_snapshot(rfq_id=...)` every 1–3 s. Returns
   RFQ + BBO + asks/bids in one call.
3. **Rank** — best price first; on ties, earlier timestamp wins. Show
   top 3: desk, side, price, size, age, offset vs Deribit fair value.
4. **Benchmark** — pull Deribit fair value per leg
   (`deribit__get_ticker` or web_fetch). Bybit as sanity cross-check;
   flag IV divergence >2 vol points. Show each top order as a delta
   vs the cross-venue fair.
5. **Confirmation gate** (see below). Wait for `yes`.
6. **Cross** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="FILL_OR_KILL", price=..., quantity=...,
   legs=[...])`. `side` is opposite the resting order being taken.
   Response is async-first (`state: OrderState.PENDING`) — poll
   `paradigm_drfqv2_orders` for the transition to `CLOSED`. Surface
   `trade_id` from `paradigm_drfqv2_trades(rfq_id=...)` once cleared.
7. **Cancel** — on abort, `paradigm_drfqv2_cancel(rfq_id=...)`.

## Step 3b — Maker flow

1. **Find open RFQs** — poll
   `paradigm_drfqv2_rfqs(state="RFQState.OPEN", role="AuctionRole.MAKER")`
   every 1–3 s.
2. **Fair value** — for each leg, fetch Deribit mark + IV + greeks.
   Bybit cross-check; flag IV divergence >2 vol points.
3. **Optional pricing helper** — for multi-leg, call
   `paradigm_drfqv2_price_legs(bid_price=..., ask_price=..., legs=[...])`
   to split structure price across legs.
4. **Apply edge:**
   - "X vol over mark" → bump per-leg IV by X, reprice via BS,
     re-aggregate.
   - "Y bps over mark" → `price = mark × (1 + Y/10000)` for ask;
     `× (1 - Y/10000)` for bid.
   - Absolute price → show implied edge.
5. **Confirmation gate**. Wait for `yes`.
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

```
RFQ to send  (taker, BTC, Deribit)
──────────────────────────────
Structure: 90/80 risk reversal, 7MAY26
Legs:
  +1  BTC-7MAY26-90000-C  (id 12345)
  -1  BTC-7MAY26-80000-P  (id 12346)
Quantity:   100
Counterparties: LP1, LP2  (directed)
Label:      rfq-trader-1745612345678

Fair value (Deribit mark): +0.0041 BTC
Spot:                       $84,200
Net delta (per unit):       ~+0.62

Est. premium:  100 × 0.0041 = 0.41 BTC  (~$34,562)
──────────────────────────────
Confirm? [yes / no / adjust]
```

```
Order to post  (maker, rfq_12345)
──────────────────────────────
Side:  SELL (offer)
Price: 0.0045 BTC
Size:  100
Edge:  +0.5 vol over Deribit mark
Label: rfq-trader-q-1745612345678

Reference: Deribit mark 0.0041 BTC (IV 34.5%), Bybit 0.0040 (IV 34.2%)
Implied edge: ~$337 per contract over fair
──────────────────────────────
Confirm? [yes / no / adjust]
```

**Responses:** `yes` → call the tool. `no` → abort. `adjust <field>
<value>` → re-render (`adjust price 0.0042`, `adjust quantity 50`,
`adjust edge 0.3vol`, etc.). Re-pull fair value where it matters.

Never submit without explicit confirmation — even if the user
pre-states "just send it" in the same message.

## Post-trade handoff

- **Post-fill analysis** — pass the trade JSON to
  `paradigm-block-analyst` for greeks, fill quality, and cross-venue
  benchmark.
- **Historical context** — `paradigm-data-discovery` over the S3 tape.
- **Hedging** — out of scope; suggest `paradex-order-builder` for
  Paradex delta hedges.

## Output format

Compact tables over prose. Always include:

- Legs table (direction, instrument, id, ratio, side, price).
- Fair-value benchmark line.
- Confirmation block when about to call a state-changing tool.
- Result block on success (`rfq_id`, `order_id`, `trade_id`).
- **Data trace** — one line per source actually called:
  `RFQ create → paradigm_drfqv2_create_rfq`,
  `Deribit fair value → deribit__get_ticker`,
  `Bybit cross-check → web_fetch v5 tickers`.

Drop empty sections. Never invent fair-value numbers when a venue is
unreachable — say so in the trace.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate is
  non-negotiable.
- **Credentials live in the MCP server's env, not in chat.** Refuse to
  echo `PARADIGM_*` values or ask the user to paste them; direct them
  to the MCP config.
- **Async-first orders.** `post_order` returns `PENDING`; poll
  `paradigm_drfqv2_orders` for the terminal state.
- **MCP server is Alpha.** WebSocket subscriptions, OAuth 2.1, and
  production signers (Vault Transit / AWS KMS / sidecar) are designed
  but not yet shipped — only `EnvKeySigner` is in this release. The
  signing key lives in the MCP server's process until those land.
- **Scope:** options only, single-leg and multi-leg. Perp / futures
  combos, spot RFQ, VRFQ (on-chain), FSPD (futures spreads) are out of
  scope at this skill version.
- **REST fallback exists** for environments that can't install the MCP
  — see `references/auth.md` for the HMAC scheme and
  `references/endpoints.md` for the underlying paths. Track the spec
  at `tradeparadigm/mono#34164` and regenerate via
  `tradeparadigm/mcp-paradigm-py`.
- Not financial advice. Fair-value benchmarks are reference, not a
  recommendation.

## References

- [`references/instruments.md`](references/instruments.md) — venue
  naming, base currencies, strategy codes (`StrategyCodeEnum`),
  margin kinds.
- [`references/auth.md`](references/auth.md) — REST-fallback HMAC
  signing scheme. Only relevant if the MCP server isn't available.
- [`references/endpoints.md`](references/endpoints.md) —
  REST-fallback endpoint reference: paths, payloads, enums, error
  codes. Maps each MCP tool to its underlying REST endpoint.
- [`references/test-signing.py`](references/test-signing.py) —
  runnable self-test of the REST-fallback signing helper with pinned
  synthetic vectors.
