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
  references/auth.md. Optional: paradex-webchat-ui-renderer for rich
  cards (live quote ladder, confirmation card) in the Paradex webchat
  channel — plain text everywhere else.
metadata:
  author: tradeparadex
  version: "5.6"
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
| `paradigm_drfqv2_counterparties` | Maker desk names + per-desk venue / prime-venue eligibility. **Paginated** — loop the cursor / `has_more` to the end before using the list; page 1 is not the full set. Used to resolve the prime-LP set for a venue | no |
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
Paradigm MCP — poll the read tools at 1–3 s during an active RFQ and
**render each quote the moment it lands** (Step 3a). When the streaming
tools (`paradigm_subscribe` / `paradigm_poll` / `paradigm_unsubscribe`,
channels `rfq` / `order` / `bbo` / `trade` / `error`) ship, switch this
loop to event-driven and **subscribe to the `error` channel** so
rejections / failures arrive push-side instead of being inferred from
polled terminal state — see Step 3a · 7 and Caveats.

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
| `legs` | `{instrument_id, ratio, side, price?}` rows. Outright = 1 leg; spread / straddle / RR = 2 legs; condors etc. = more. `side` defines structure orientation — see **Direction** below |
| `quantity` | Decimal string in base units |
| `counterparties` | **Default: send to every prime-venue-enabled LP for the venue, by name.** Resolve them with `paradigm_drfqv2_counterparties` — **page through the whole result** (follow the cursor / `has_more`; don't stop at page 1) — then filter to desks flagged prime-venue-enabled for this venue and pass that explicit list (see Step 3a · 1). Narrow to specific desks only when the user names them. Last-resort fallback (counterparties tool unavailable): send an empty / omitted list so Paradigm open-broadcasts (GRFQ), and note it in the trace |
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

### Direction — read before building `legs`

Leg `side` values define the *structure*; the package you submit defines
the *direction you hold it*. To go **long** a structure, configure the
leg sides so the package IS the position the user wants and submit it as
a **BUY** (positive quantity). Do **not** also flip every leg to a
"short" orientation and then SELL — that double-negates back to long
(the common bug).

Use **SELL on the package only** when you built a *conventional /
textbook* structure and the user wants its inverse — e.g. "short call
spread" = build the conventional debit call spread (BUY lower call +
SELL higher call), then SELL the package.

- Bullish call spread → BUY lower-strike call + SELL higher-strike call,
  submit **BUY**. "Short call spread" → same legs, submit **SELL**.
- Bearish put spread → BUY higher-strike put + SELL lower-strike put,
  submit **BUY**.
- Bullish risk reversal (e.g. 90/80) → BUY 90 call + SELL 80 put, submit
  **BUY**. Bearish → SELL call + BUY put in the legs, submit **BUY**.
- **Outright** (1 leg) → no structure to orient: short = a single leg
  `side=SELL`; don't also flip a package direction.

**Worked example — "short a 90000/95000 call spread"** (the textbook case
the bug bites): the *conventional* structure is the debit call spread, so
build it conventionally and short the **package**, never the legs.

- legs: `BUY 90000-C` (lower strike) **+** `SELL 95000-C` (higher strike)
- package: submit **SELL** to be short it.
- Do **not** invert to `SELL 90000-C + BUY 95000-C` *and* submit SELL — that
  double-negates back to long the call spread. The lower strike is always
  the BUY leg in the conventional build.

The cross `side` at `post_order` (Step 3a · 5) is a separate
matching-mechanics concern — see there.

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

1. **Resolve counterparties, then create the RFQ.** Unless the user
   named specific desks, **default to every prime-venue-enabled LP for
   the venue**:
   - Call `paradigm_drfqv2_counterparties` and **page through every
     result** — follow the cursor / `next` / `has_more` until exhausted.
     Do **not** stop at the first page; a partial list silently drops LPs.
   - Filter the desks to those flagged prime-venue-enabled for this
     venue (see `references/venues.md`), and pass that explicit list as
     `counterparties`. Capture the resolved count `N`.
   - Last-resort fallback only if the counterparties tool is unavailable
     or returns nothing: send an empty / omitted `counterparties` list so
     Paradigm open-broadcasts (GRFQ), and call this out in the trace.

   Then `paradigm_drfqv2_create_rfq(venue=..., legs=[...], quantity=...,
   counterparties=[...], account_name=..., label=...)`. Capture `rfq_id`.
   Show: id, venue, legs, quantity, counterparties (`all N PRDX prime
   LPs`, the named desks, or `open broadcast (fallback)`), expiry.
2. **Stream quotes live** — poll `paradigm_drfqv2_rfq_snapshot(rfq_id=...)`
   every 1–3 s (returns RFQ + BBO + asks/bids in one call) and **surface
   each new or improved quote the instant it appears** — do **not** wait
   for the auction to close before showing anything. Keep one compact
   live ladder that updates in place: best price on top (ties → earlier
   timestamp), each row `desk · side · price · size · age · offset vs
   fair`. Mark the current best. **On every tick, check the RFQ `state`
   / `closed_reason` first:** if the RFQ has left `OPEN` for a non-fill
   reason (`EXPIRED`, `EXECUTION_LIMIT`, rejected / errored), **stop the
   quote loop and surface the failure** per Step 3a · 7 — never keep
   spinning on a dead RFQ. Otherwise repeat until the user crosses,
   cancels, or the RFQ expires.
3. **Benchmark inline** — fold the venue's fair-value reference (per
   `references/venues.md`, by `venue` + `kind`) into the ladder's
   `offset` column: `price − fair` in the venue's natural units (bps for
   linear; absolute + implied-vol bump for options). Pull it once, refresh
   on a slower cadence than the quote poll.
4. **Confirmation gate** (see below). Wait for explicit `yes`.
5. **Cross** — `paradigm_drfqv2_post_order(rfq_id=..., side=...,
   type="LIMIT", time_in_force="FILL_OR_KILL", price=..., quantity=...,
   legs=[...])`. `side` is opposite the resting order being taken.
   This cross `side` is matching mechanics (lift an offer = BUY, hit a
   bid = SELL) and is independent of the structure's long/short
   orientation, which the leg sides already fixed at create-time (see
   Direction). Response is async-first (`state: OrderState.PENDING`) — poll
   `paradigm_drfqv2_orders` and branch on the terminal state:
   - **`CLOSED`** → fetch `trade_id` from `paradigm_drfqv2_trades(rfq_id=...)`,
     then follow the venue's settlement-check recipe in `references/venues.md`.
     A FOK cross that fills nothing also terminates — treat a closed order
     with no resulting trade as a non-fill, not an open wait.
   - **`REJECTED` / failed (order terminal, or BlockTrade `state=REJECTED`)**
     → **stop. Do not poll `trades` for a fill that will never come.**
     Surface the rejection with full detail per Step 3a · 7.
6. **Cancel** — on abort, `paradigm_drfqv2_cancel(rfq_id=...)`.
7. **Errors & rejections** — the single rule for failure handling. On
   **any** RFQ / order / trade terminal failure (non-fill RFQ
   `closed_reason`, rejected / failed order state, BlockTrade
   `state=REJECTED`), **halt fill-polling immediately** — the common bug
   is continuing to wait for fills on an RFQ that is already dead.
   Gather the maximum error detail the payloads expose and quote it
   verbatim: RFQ `closed_reason`, the order's terminal `state`,
   `BlockTrade.state`, plus any `error` / `reason` / `message` / `code` /
   request-id / timestamp fields present. Present it plainly — what
   failed, why (raw reason / code), and the next step (re-send, widen
   counterparties, adjust price) — rather than a silent spinning loop.
   When the `error` subscription channel ships (see MCP tools note),
   subscribe to it at `create_rfq` time and render failures push-side;
   until then this polling branch is the mechanism.

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

The block has two parts: (1) the **assembled call** — the exact tool and
arguments that will run on `yes`, fully resolved (integer `instrument_id`s,
leg sides, `quantity`, `counterparties`, `venue`, `time_in_force`); and
(2) a one-line **fair-value** reference. Showing the assembled call is what
"live-money confirmation" means — the user sees precisely what will be
submitted. Assemble it *now*, before the gate; do not defer assembly to
after `yes`.

Canonical taker example (PRDX perp; same structure for any venue —
swap in the venue's fair-value section per `references/venues.md`):

```
CONFIRM RFQ — taker · BTC-USD-PERP (id 98765) · PRDX
Will call on yes:
  paradigm_drfqv2_create_rfq(venue="PRDX",
    legs=[{instrument_id: 98765, ratio: 1, side: "BUY"}],
    quantity="500", counterparties=[...14 prime LPs], label="...")   # resolved prime-venue LP set (paginated)
BUY 500 BTC → all 14 PRDX prime LPs           ~$48.23M
Fair: mid $96,455 · BBO 96,450/96,460 (10 bps) · walk 500 ~$96,612 (+16 bps)
[yes / no / adjust]
```

Keep it tight — header line, the assembled `Will call on yes:` block, action
line + notional, one fair-value line, prompt. Don't restate fields the user
already gave. For multi-leg structures, the action line states the **net**
direction the taker will hold (long / short the structure), confirmed against
**Direction** (Step 1) — not merely a restatement of leg sides — while the
assembled call shows the literal per-leg `side`s.

For options or for Deribit, the **structure of the block is the
same** — header line, assembled call, leg(s) listed, fair-value reference,
sizing line — but the fair-value section is shaped per
`references/venues.md` for that venue + kind. Options **must** show, *inside
the confirmation block itself* (not only in an earlier step), a per-leg line
with `mark + mark_iv + delta + vega`, the **underlying spot** (pull
`BTC-USD-PERP` mark on PRDX / `BTC-PERPETUAL` on DBT), and an aggregated
structure mark + net delta/vega. Deribit options show prices in BTC terms
(not USD). Example (PRDX risk reversal):

```
CONFIRM RFQ — taker · BTC 8MAY26 90/80 risk reversal · PRDX · short/bearish
Will call on yes:
  paradigm_drfqv2_create_rfq(venue="PRDX", quantity="100",
    legs=[{instrument_id: 50121, ratio: 1, side: "SELL"},   # 90000-C
          {instrument_id: 50144, ratio: 1, side: "BUY"}],    # 80000-P
    counterparties=["LP1","LP2"], label="...")
  90000-C  mark 0.021 · IV 58% · Δ +0.34 · vega 9.2
  80000-P  mark 0.018 · IV 61% · Δ −0.22 · vega 8.1
  Underlying BTC-USD-PERP mark $96,455 · net structure mark 0.003 · net Δ +0.12
[yes / no / adjust]
```

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

**Terse by default.** A few tight lines and one table beat a wall of
prose. Surface only what the trader acts on:

- One header line (instrument · side · quantity), or a small legs table
  for multi-leg.
- The **live quote ladder** during an open RFQ (Step 3a · 2), updated in
  place as quotes stream in — not re-printed in full each tick.
- One fair-value line, shaped per the venue's recipe in
  `references/venues.md`.
- The slim confirmation block before any state-changing tool (Step 4).
- A one-line result on success (`rfq_id` / `order_id` / `trade_id`).
- **Data trace** — one line, the concrete tools actually called, e.g.
  `instruments → snapshot → market_summaries → create_rfq`.

Drop empty sections. Don't restate inputs the user just gave. Never
invent fair-value numbers when a data source is unreachable — say so.

### Webchat channel (rich UI — default for non-trivial data)

In the Paradex webchat channel, **default to rich UI** via the
`paradex-webchat-ui-renderer` skill for any non-trivial output — the
multi-leg structures this skill builds, the live quote ladder,
confirmation blocks, and results. Reserve plain text for one-line
replies, or when the user explicitly asks for plain text / raw values.
Map:

- **Live quote ladder** → `data_table` (columns: Desk, Side, Price,
  Size, Age, Offset), re-emitted as quotes arrive. Best row first.
- **Confirmation block** → `alert_banner` (warning: "Confirm RFQ —
  live money") + `labeled_output`s for side/qty/counterparties/notional
  and the fair-value reference.
- **Result** → `metric_card`s (`rfq_id`, fill price, notional).

Emit the renderer's raw JSON spec (no code fences). Outside webchat,
use the compact plain-text format above.

## Caveats

- **Live-money venue.** Never auto-execute. The confirmation gate
  is non-negotiable.
- **Credentials live in the MCP server's env, not in chat.** Refuse
  to echo `PARADIGM_*` values or ask the user to paste them; direct
  them to the MCP config.
- **Async-first orders.** `post_order` returns `PENDING`; poll
  `paradigm_drfqv2_orders` for terminal state — and branch on failure,
  not just on `CLOSED`. A rejected / failed RFQ or order must stop the
  fill-poll and surface the error (Step 3a · 7), never hang waiting.
- **MCP server is Alpha.** WebSocket subscriptions, OAuth 2.1, and
  production signers (Vault Transit / AWS KMS / sidecar) are
  designed but not yet shipped — only `EnvKeySigner` is in this
  release. The signing key lives in the MCP server's process until
  those land. Until the streaming tools (`paradigm_subscribe` /
  `paradigm_poll` / `paradigm_unsubscribe`, channels including
  `error`) land, the live quote ladder is driven by 1–3 s polling of
  `paradigm_drfqv2_rfq_snapshot` and rejections / failures are detected
  by checking polled terminal state; swap in the subscriptions (and the
  `error` channel) when available for true push updates.
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
  signing scheme. Only relevant if the MCP server isn't available;
  the MCP signs in its own process and the live `paradigm_echo`
  tool is the canonical end-to-end signing self-test.

For endpoint paths, payload shapes, and enums in the REST-fallback
path, read the OpenAPI spec at
[`tradeparadigm/mono#34164`](https://github.com/tradeparadigm/mono/pull/34164)
directly. The MCP server is generated from it.
