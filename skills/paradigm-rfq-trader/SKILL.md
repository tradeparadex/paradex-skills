---
name: paradigm-rfq-trader
description: >
  Trigger institutional block trades on Paradex via Paradigm's DRFQv2
  flow. Scope: Paradex-settled (venue=PRDX) RFQs across all three
  Paradex product families — perps, dated futures, and options
  (single-leg + multi-leg). Block settles on the user's Paradex
  account. Built on mcp-paradigm-py with mcp-paradex-py for
  fair-value + greek lookups. Covers takers (build, benchmark vs
  Paradex book / IV surface, cross) and makers (poll, price with
  bps-over-mid for linear or vol-over-IV for options, manage).
  Every state-changing action goes through a confirmation gate. Use
  when the user asks to "send a Paradex block RFQ", "block-trade X
  BTC perp", "send a BTC straddle on Paradex", "quote rfq_X", "hit
  the best bid", "cancel rfq_X". Does NOT cover small Paradex
  order-book trades (paradex-order-builder), post-trade analysis
  (paradigm-block-analyst), historical tape (paradigm-data-discovery),
  or block trades on Deribit/Bybit/Bit.com (follow-up scope).
compatibility: >
  Requires the mcp-paradigm-py MCP server
  (github.com/tradeparadigm/mcp-paradigm-py) plus the Paradex MCP
  server (mcp-paradex-py) for fair-value benchmarking. Install
  mcp-paradigm via the .mcpb bundle for Claude Desktop or
  `pip install mcp-paradigm` / `uvx mcp-paradigm` for other hosts.
  Env vars set in the MCP server: PARADIGM_ACCESS_KEY,
  PARADIGM_SIGNING_KEY, PARADIGM_ENVIRONMENT=testnet|prod. A
  direct-REST fallback is documented in references/auth.md for
  environments without the MCP.
metadata:
  author: tradeparadex
  version: "4.0"
---

# Paradigm RFQ Trader

Drives the Paradigm DRFQv2 lifecycle for **block trades that settle on
Paradex** — taker sources liquidity from LP desks via Paradigm, makers
quote, the cleared trade lands on the user's Paradex account. The
skill owns workflow, fair-value benchmarking against the Paradex book,
and the confirmation gate; the MCP server owns transport, auth, and
signing.

## Scope

**Initial target:** Paradigm RFQs with `venue=PRDX`. Three product
families, all on the same venue:

| Product | Examples | Common strategy codes |
|---|---|---|
| Perpetual | `BTC-USD-PERP`, `ETH-USD-PERP` | `FT` (outright), `FS` (calendar between perp + future) |
| Dated future | `BTC-USD-27JUN26` | `FT`, `FS` |
| Option | `BTC-USD-8MAY26-90000-C`, `ETH-USD-8MAY26-1800-P` | `CL` `PT` (outrights), `CS` `PS` (spreads), `SD` `SG` (straddle/strangle), `CR` `PR` (risk reversal), `CC` `PC` (calendars), `CB` `PB` `CD` `PD` (flies/condors), `CM` (custom multi-leg) |

The skill switches benchmarking and edge-pricing approach based on
the instrument's `kind` (`OPTION` vs `FUTURE`).

**Out of scope at this version (handled by other skills or
follow-ups):**

- Small / liquid orders on Paradex's central order book →
  `paradex-order-builder`.
- Block trades on Deribit / Bybit / Bit.com via Paradigm — supported
  by the MCP, follow-up version of this skill will add cross-venue
  Deribit benchmarking.
- Post-trade analysis of a filled block → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- Options pricing math (greeks, IV surface fitting) → defer to
  `paradex-options-pricer` for the heavy logic; this skill consumes
  its outputs.

## Trigger

Fire on live RFQ-lifecycle intent against Paradex. Examples:

- *"send a block RFQ for 500 BTC perp"*
- *"source liquidity for an ETH-USD-PERP block, 200 ETH"*
- *"create a calendar spread RFQ — BTC 27JUN26 vs 26SEP26"*
- *"send a BTC 8MAY26 90/80 risk reversal RFQ on Paradex"*
- *"quote rfq_12345 at 2 bps over the Paradex mid"*
- *"quote rfq_X at +0.5 vol over the Paradex mark IV"*
- *"hit the best bid on this Paradex RFQ"*
- *"cancel rfq_12345"*

Do **not** fire on:

- Direct Paradex order-book trades → `paradex-order-builder`.
- Post-trade analysis of a filled block JSON → `paradigm-block-analyst`.
- Historical tape queries → `paradigm-data-discovery`.
- RFQs on Deribit / Bybit / Bit.com — currently out of this skill's
  scope; the MCP supports them but no skill UX yet.

## MCP tools used

From `mcp-paradigm-py` (RFQ workflow):

| Tool | Purpose | Confirmation? |
|---|---|---|
| `paradigm_echo` | Signing self-test; first call after wiring | no |
| `paradigm_desk_overview` | Positions + MMP + platform state in one call | no |
| `paradigm_kill_switch` | Cancel ALL open orders across all products | **yes — destructive** |
| `paradigm_drfqv2_instruments` | Resolve venue-native name → Paradigm `instrument_id` | no |
| `paradigm_drfqv2_counterparties` | Maker desk names for directed RFQs | no |
| `paradigm_drfqv2_rfqs` | List RFQs (filter by `role`, `state`, `venue=PRDX`, `strategies`) | no |
| `paradigm_drfqv2_rfq_snapshot` | Composite — RFQ + BBO + order book in one call | no |
| `paradigm_drfqv2_create_rfq` | Taker creates an RFQ | **yes** |
| `paradigm_drfqv2_orders` | List orders, filter by `rfq_id` / `state` | no |
| `paradigm_drfqv2_post_order` | Maker quote OR taker cross (side + TIF distinguish) | **yes** |
| `paradigm_drfqv2_cancel` | Cancel RFQ or order (single or batch by filter) | no |
| `paradigm_drfqv2_trades` | Your cleared block trades | no |
| `paradigm_drfqv2_price_legs` | Multi-leg structure pricer (bid/ask in → per-leg out) | no |
| `paradigm_drfqv2_mmp` | Maker circuit-breaker — status or reset | **yes** for reset |

From `mcp-paradex-py` (fair-value benchmarking + post-settle check):

| Tool | Purpose |
|---|---|
| `paradex_bbo` | Best bid/ask for perps + futures — primary linear benchmark |
| `paradex_orderbook` | Walk the book for the full RFQ size (linear) |
| `paradex_market_summaries` | Mark, funding, 24h stats; for options also returns `mark_iv`, `delta`, `vega` |
| `paradex_markets` | Option-chain listing — strikes, expiries, kinds — for resolving option symbols |
| `paradex_account_fills` | Confirm the cleared block landed in the user's Paradex account |
| `paradex_account_positions` | Updated position post-fill |

For option-specific math (BS reprice, IV surface) defer to the
`paradex-options-pricer` skill's formulas — don't duplicate them here.

WebSocket subscriptions are designed but not yet shipped in the
Paradigm MCP — poll the read tools at 1–3 s during an active RFQ.

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
maker). At this skill version, `venue` defaults to `PRDX` (Paradex
settlement) — confirm with the user before assuming any other.

**Taker:**

| Field | Meaning |
|---|---|
| `venue` | **Defaults to `PRDX`** (Paradex). Other DRFQv2 venues exist but are out of scope for this skill version |
| `legs` | For outrights: one row `{instrument_id, ratio=1, side, price?}`. For calendar spreads: two rows with opposite sides on two expiries of the same underlying |
| `quantity` | Decimal string in base units (e.g. `"500"` BTC for a BTC perp block) |
| `counterparties` | Desk names from `paradigm_drfqv2_counterparties` (empty = open / GRFQ-style) |
| `is_taker_anonymous` | Hide taker identity from makers (optional) |
| `account_name`, `label` | Account label + idempotency tag |

**Maker:**

| Field | Meaning |
|---|---|
| `rfq_id` | RFQ to quote against |
| `side` | `BUY` (bid) / `SELL` (offer). Two-way = two `post_order` calls |
| `price` or `edge` | Absolute price, or "Y bps over Paradex mid" / "tighten the BBO by Z" |
| `quantity` | Defaults to RFQ quantity |
| `type` | `LIMIT` (default) or `HIDDEN` |
| `time_in_force` | `GOOD_TILL_CANCELED` (rest) or `FILL_OR_KILL` (cross) |

If anything is ambiguous, ask before calling tools.

## Step 2 — Resolve instrument IDs

Paradigm references legs by integer `instrument_id`. For each leg:

```
paradigm_drfqv2_instruments(venue="PRDX", venue_instrument_name="BTC-USD-PERP")
```

Capture `results[0].id`. Cache for the session; do not invent IDs.

**Paradex (`PRDX`) naming:**

| Product | Format | Example |
|---|---|---|
| Perpetual | `<BASE>-USD-PERP` | `BTC-USD-PERP`, `ETH-USD-PERP` |
| Dated future | `<BASE>-USD-<DDMMMYY>` | `BTC-USD-27JUN26` |
| Option | `<BASE>-USD-<DDMMMYY>-<STRIKE>-<C\|P>` | `BTC-USD-8MAY26-90000-C` |

Day is **not** zero-padded on Paradex. Use the live catalog via
`paradigm_drfqv2_instruments` to confirm — the lookup also tells you
`kind` (`OPTION` vs `FUTURE`), which determines fair-value approach
in Step 3.

Other DRFQv2 venues (BIT / BYB / DBT) use different formats — see
[`references/instruments.md`](references/instruments.md). Those are
out of scope at this skill version; if a user explicitly asks for a
non-PRDX RFQ, surface that it's not yet supported and offer to
escalate.

## Step 3a — Taker flow

1. **Create the RFQ** — `paradigm_drfqv2_create_rfq(venue="PRDX",
   legs=[...], quantity=..., counterparties=[...], account_name=...,
   label=...)`. Capture `rfq_id`. Show: id, venue=PRDX, instrument(s),
   quantity, counterparties, expiry.
2. **Watch resting orders** — poll
   `paradigm_drfqv2_rfq_snapshot(rfq_id=...)` every 1–3 s. Returns
   RFQ + BBO + asks/bids in one call.
3. **Rank** — best price first; on ties, earlier timestamp wins. Show
   top 3 orders: desk, side, price, size, age, offset vs Paradex mid.
4. **Benchmark vs Paradex** — branch on instrument `kind`:
   - **Perp / future:** pull `paradex_bbo(market=...)` for current
     best bid/ask + `paradex_market_summaries(...)` for mark and
     funding. Call `paradex_orderbook(...)` and walk it for the full
     RFQ size — that's the implicit "what would I get on-screen?"
     benchmark. Surface each top RFQ order as `price − mid` in bps
     and as notional savings vs the walked book.
   - **Option:** pull `paradex_market_summaries(market=...)` for each
     leg to get `mark_price`, `mark_iv`, `delta`, `vega`. Pull the
     underlying perp's `mark_price` (`BTC-USD-PERP` /
     `ETH-USD-PERP`). For multi-leg, aggregate: net delta, net vega,
     structure mark = Σ (ratio × leg_mark × side_sign). Show each
     top RFQ order as `price − structure_mark` in absolute terms
     and as the implied vol bump. Defer the BS math to
     `paradex-options-pricer` patterns if you need a custom
     re-price.
5. **Confirmation gate** (see below). Wait for explicit `yes`.
6. **Cross** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="FILL_OR_KILL", price=..., quantity=...,
   legs=[...])`. `side` is opposite the resting order being taken.
   Response is async-first (`state: OrderState.PENDING`) — poll
   `paradigm_drfqv2_orders` for the transition to `CLOSED`. Surface
   `trade_id` from `paradigm_drfqv2_trades(rfq_id=...)` once cleared,
   and call out that it will appear on the user's Paradex account
   (check via `paradex_account_fills` or `paradex_account_positions`).
7. **Cancel** — on abort, `paradigm_drfqv2_cancel(rfq_id=...)`.

## Step 3b — Maker flow

1. **Find open RFQs** — poll
   `paradigm_drfqv2_rfqs(venue="PRDX", state="RFQState.OPEN",
   role="AuctionRole.MAKER")` every 1–3 s.
2. **Fair value** — branch on `kind`:
   - **Perp / future:** `paradex_bbo`, `paradex_orderbook`,
     `paradex_market_summaries`. Mid = `(best_bid + best_ask) / 2`;
     for size larger than top-of-book, the order-book walked-price
     is the more realistic benchmark.
   - **Option:** `paradex_market_summaries` for each leg returns
     `mark_price`, `mark_iv`, greeks. Use mark IV as σ and the
     underlying perp mark as S; reuse `paradex-options-pricer`
     conventions for any custom re-price.
3. **Optional pricing helper** — for spreads / multi-leg, call
   `paradigm_drfqv2_price_legs(bid_price=..., ask_price=..., legs=[...])`
   to split a structure price across legs the way Paradigm will.
4. **Apply edge** — branch on `kind`:
   - **Perp / future:**
     - "Y bps over mid" → `price = mid × (1 + Y/10000)` for ask;
       `× (1 - Y/10000)` for bid.
     - "Tighten the BBO by Z" → quote inside the current Paradex
       best by Z bps (be explicit if that would imply a negative
       spread).
   - **Option:**
     - "X vol over mark IV" → bump per-leg IV by X, reprice each leg
       via Black-Scholes (delegate to `paradex-options-pricer`
       formula), re-aggregate the structure price.
     - "Y bps over option mark" → simple mark-price scaling, useful
       for tight-spread instruments.
     - Absolute price → show implied vol bump for confirmation.
5. **Confirmation gate**. Wait for explicit `yes`.
6. **Post** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="GOOD_TILL_CANCELED", price=...,
   quantity=..., legs=[...])`. Two-way = two calls.
7. **Manage lifecycle** — poll each 1–3 s:
   - `paradigm_drfqv2_orders(rfq_id=...)` — surface when no longer
     top-of-book.
   - `paradigm_drfqv2_trades(rfq_id=...)` — surface fills; the maker
     will see the resulting position on their Paradex account.
   - `paradigm_drfqv2_mmp()` — circuit-breaker status. If
     `rate_limit_hit: true`, all desk orders are paused; reset to
     re-arm (gated).
   Amend by cancel + new post; same confirmation gate.

## Step 4 — Confirmation gate

**Always** present this block and wait for explicit `yes` before any
state-changing tool call (`create_rfq`, `post_order`, `kill_switch`,
`mmp` reset).

```
RFQ to send  (taker, BTC perp, settles on Paradex)
──────────────────────────────
Instrument: BTC-USD-PERP  (id 98765)
Side:       BUY
Quantity:   500 BTC
Counterparties: LP1, LP2, LP3  (directed)
Label:      rfq-trader-1745612345678

Paradex book reference:
  BBO:        $96,450 / $96,460   (spread 10 bps)
  Mid:        $96,455
  Walked ask (for 500 BTC): ~$96,612  (~16 bps slippage)

Est. notional: 500 × $96,455 = $48.23M
──────────────────────────────
Confirm? [yes / no / adjust]
```

```
Order to post  (maker, rfq_12345, Paradex BTC perp)
──────────────────────────────
Side:  SELL (offer)
Price: $96,470
Size:  500
Edge:  +1.5 bps over Paradex mid ($96,455)
Label: rfq-trader-q-1745612345678

Paradex reference: BBO $96,450 / $96,460  (current best ask: $96,460)
Your quote vs best ask: +$10 (1 bp above current Paradex offer)
──────────────────────────────
Confirm? [yes / no / adjust]
```

```
RFQ to send  (taker, BTC 8MAY26 90/80 risk reversal, settles on Paradex)
──────────────────────────────
Legs:
  +1  BTC-USD-8MAY26-90000-C  (id 31415)
  -1  BTC-USD-8MAY26-80000-P  (id 31416)
Quantity:   100
Counterparties: LP1, LP2  (directed)
Label:      rfq-trader-1745612345678

Paradex reference (per leg, mark_iv + delta + vega):
  90000-C:  $1,820  IV 64.2%  Δ +0.282  Vega 88.2
  80000-P:  $1,650  IV 70.1%  Δ -0.241  Vega 71.3
Underlying:  BTC-USD-PERP  $84,200
Structure mark: +$170  net Δ ≈ +0.523/unit  net Vega ≈ +16.9

Est. notional: 100 × $84,200 × 0.523 ≈ $4.40M delta-equivalent
──────────────────────────────
Confirm? [yes / no / adjust]
```

**Responses:** `yes` → call the tool. `no` → abort. `adjust <field>
<value>` → re-render. Examples:
- Perp/future: `adjust price 96465`, `adjust quantity 250`, `adjust edge 0.5bps`
- Option: `adjust quantity 50`, `adjust edge 0.3vol`, `adjust counterparties LP1`

Re-pull the relevant Paradex reference (BBO for linear, market
summaries for options) before re-rendering.

Never submit without explicit confirmation — even if the user
pre-states "just send it" in the same message.

## Post-trade handoff

- **Post-fill analysis** — pass the trade JSON to
  `paradigm-block-analyst` for fill-quality benchmarking.
- **Settlement check** — call `paradex_account_fills(market="BTC-USD-PERP",
  start_at=...)` to confirm the block landed in the user's Paradex
  account, and `paradex_account_positions` for the updated position.
- **Historical context** — `paradigm-data-discovery` over the S3 tape.
- **Hedging the new exposure** — `paradex-order-builder` if the user
  wants to hedge the resulting delta on Paradex's order book.

## Output format

Compact tables over prose. Always include:

- Instrument + side + quantity line (or legs table for a multi-leg).
- Paradex reference appropriate to the product:
  - linear (perp/future): BBO + mid + walked-ask for full size
  - option: per-leg mark + IV + delta + vega + underlying spot
- Confirmation block when about to call a state-changing tool.
- Result block on success (`rfq_id`, `order_id`, `trade_id`).
- **Data trace** — one line per source actually called:
  `RFQ create → paradigm_drfqv2_create_rfq`,
  `Paradex BBO → paradex_bbo` (linear),
  `Paradex option mark+IV → paradex_market_summaries` (options),
  `Settlement check → paradex_account_fills`.

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
- **Scope at this version:** Paradex-settled (`venue=PRDX`) — all
  three product families (perp, dated future, option) including
  multi-leg structures. Block trades on Deribit / Bybit / Bit.com
  via Paradigm are supported by the MCP but not by this skill's UX
  yet (no cross-venue Deribit benchmark, no Deribit-IV-surface edge
  pricing). Spot RFQ, VRFQ (on-chain), FSPD (futures spreads as a
  separate product) are out of scope entirely.
- **REST fallback exists** for environments that can't install the MCP
  — see `references/auth.md` for the HMAC scheme. For endpoint paths
  and payload shapes, read the OpenAPI spec at
  [`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164)
  directly; it's the authoritative source.
- Not financial advice. Fair-value benchmarks are reference, not a
  recommendation.

## References

- [`references/instruments.md`](references/instruments.md) — venue
  naming, base currencies, strategy codes (`StrategyCodeEnum`),
  margin kinds.
- [`references/auth.md`](references/auth.md) — REST-fallback HMAC
  signing scheme. Only relevant if the MCP server isn't available;
  the MCP signs in its own process.
- [`references/test-signing.py`](references/test-signing.py) —
  runnable self-test of the REST-fallback signing helper with pinned
  synthetic vectors.

For endpoint paths, payload shapes, and enums in the REST-fallback
path, read the OpenAPI spec at
[`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164)
directly. The MCP server is generated from it; this skill won't
duplicate it.
