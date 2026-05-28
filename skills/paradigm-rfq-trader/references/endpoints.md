# Endpoints — Paradigm DRFQv2

Reference for every REST path and WS channel the `paradigm-rfq-trader`
skill touches. Authoritative source: the OpenAPI spec at
`tradeparadigm/mono` (PR #34164) — track that spec rather than this file
once codegen lands.

## Base URLs

| Env | REST | WS |
|---|---|---|
| Prod | `https://api.paradigm.co` | `wss://ws.api.paradigm.trade/v2/drfq/` |
| Testnet | `https://api.test.paradigm.co` | `wss://ws.api.testnet.paradigm.trade/v2/drfq/` |

All DRFQv2 REST paths live under `/v2/drfq/`. There is no `/v1/drfq/` —
the "v2" in DRFQv2 is the URL prefix.

## Mental model

DRFQv2 has **two object types**: `RFQ` and `Order`. There is no
standalone "quote" object — what other RFQ venues call a quote, Paradigm
models as an Order posted against an open RFQ. So:

- **Taker** creates an `RFQ` (`POST /v2/drfq/rfqs/`).
- **Maker** posts an `Order` (`POST /v2/drfq/orders/`) referencing that
  `rfq_id` on the side they want to provide.
- **Either side** crosses by posting another `Order` on the opposite
  side at a price that meets the resting one.
- Settlement produces a `BlockTrade`.

This single-endpoint flow for both maker quoting and taker execution is
why the spec is smaller than you might expect.

## REST endpoints

### RFQ lifecycle

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v2/drfq/rfqs/` | Create a new RFQ |
| `GET` | `/v2/drfq/rfqs/` | List RFQs (filter by `state`, `role`, `venue`, `strategies`, `product_codes`) |
| `GET` | `/v2/drfq/rfqs/{id}` | Fetch a single RFQ |
| `DELETE` | `/v2/drfq/rfqs/{id}` | Cancel an open RFQ before expiry |
| `GET` | `/v2/drfq/rfqs/{id}/bbo/` | Best bid/offer for this RFQ — `mark_price`, `min_price`, `max_price`, per-leg bbo + greeks |
| `GET` | `/v2/drfq/rfqs/{id}/orders/` | Order book against this RFQ — `asks[]` and `bids[]` with price/quantity |

### Order lifecycle (covers BOTH maker quote AND taker execute)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v2/drfq/orders/` | Submit an order against an RFQ (maker quote OR taker cross) |
| `GET` | `/v2/drfq/orders/` | List your desk's orders |
| `GET` | `/v2/drfq/orders/{id}` | Fetch one order |
| `PUT` | `/v2/drfq/orders/{id}` | Update an order |
| `DELETE` | `/v2/drfq/orders/{id}` | Cancel a single order |
| `DELETE` | `/v2/drfq/orders/` | Batch-cancel by filter (`rfq_id`, `venue`, `currency`, `base_currency`, `state`) |

### Trades

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v2/drfq/trades/` | Your desk's cleared `BlockTrade`s (filter `state`, `venue`, `product_codes`) |
| `GET` | `/v2/drfq/trades/{id}` | Single trade |
| `GET` | `/v2/drfq/trade_tape/` | Public trade tape — anonymized block trades across the network |

### Reference & meta

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v2/drfq/instruments/` | Tradable instrument catalog. Filter by `venue`, `base_currency`, `kind`, `margin_kind`, `state`, `venue_instrument_name` to resolve a venue-native name to its Paradigm `instrument_id` |
| `GET` | `/v2/drfq/instruments/{id}/` | One instrument by Paradigm id |
| `GET` | `/v2/drfq/counterparties/` | Desks your firm can RFQ — their `desk_name` is what goes in `counterparties` on RFQ create |
| `GET` | `/v2/drfq/platform_state/` | Current and next platform state (read-only / maintenance windows) |
| `POST` | `/v2/drfq/pricing/` | Given a bid/ask + legs, return per-leg prices. Helpful for multi-leg quoting |

### Maker safety

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v2/drfq/mmp/status/` | Market-maker protection state — `rate_limit_hit` flag |
| `PATCH` | `/v2/drfq/mmp/status/` | Reset MMP flag (re-arm the desk after a circuit-breaker trip) |

### Signing self-test

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v2/drfq/echo/` | Returns 200 if auth + signature are valid. Use to verify OneCLI + signing wiring without side effects |
| `POST` | `/v2/drfq/echo/` | Echo arbitrary JSON. Verify body-byte handling without touching production state |

`/v2/drfq/echo/` is the right first call after setup — failures here are
auth/signing problems, not business logic.

## Key request bodies

### `POST /v2/drfq/rfqs/` — RFQCreateSerializerDRFQV2

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

Notes:

- `legs[].instrument_id` is an **integer**. Look it up first with
  `GET /v2/drfq/instruments/?venue=DBT&venue_instrument_name=BTC-7MAY26-90000-C`.
- `legs[].ratio` is a decimal string. `legs[].price` is optional —
  include only when the taker wants to disclose a hint price.
- `counterparties` is an array of **desk names** (strings) from
  `GET /v2/drfq/counterparties/`. Empty array means open / GRFQ-style.
- `quantity` is a decimal string.
- `venue` is one of `BIT` (Bit.com), `BYB` (Bybit), `DBT` (Deribit),
  `PRDX` (Paradex). Settlement happens on this venue.
- `is_taker_anonymous: true` hides taker identity from makers.

### `POST /v2/drfq/orders/` — OrderCreateSerializerDRFQV2

Single endpoint for both maker quotes and taker crosses.

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

Notes:

- `side`: `BUY` or `SELL` — your side of the structure. Maker quoting
  bid → `BUY`; offer → `SELL`. Taker crossing the top ask → `BUY`.
- `type`: `LIMIT` or `HIDDEN`. No `MARKET`.
- `time_in_force`: `GOOD_TILL_CANCELED` (resting maker quote) or
  `FILL_OR_KILL` (taker cross).
- `legs` here echo the RFQ's legs with per-leg prices — useful when the
  structure price doesn't split evenly across legs.
- `label` is a caller-supplied tag. Treat as the idempotency key.

Response is `OrderCreateOutputSerializerDRFQV2`. The endpoint is
**async-first**: the response always has `state = "OrderState.PENDING"`
and zeroed `filled_quantity` / `canceled_quantity` / `pending_fill_quantity`.
Poll `GET /v2/drfq/orders/{id}` (or subscribe to the WS `order` channel)
to observe state transitions.

### `DELETE /v2/drfq/orders/` — batch cancel by filter

Returns `DRFQv2OrderBatchDelete`:

```json
{
  "successes": {"count": 5, "order_ids": ["o_1","o_2","o_3","o_4","o_5"]},
  "failures":  {"count": 1, "order_ids": ["o_6"]}
}
```

200 = all succeeded; 207 = partial (multi-status). Surface the partial
case to the user — don't treat 207 as failure.

## Enums you'll touch

| Enum | Values |
|---|---|
| Venue (RFQ create) | `BIT BYB DBT PRDX` |
| Venue (broader) | adds `RBN TTN BLT FBX FKN FTX SKD CME` |
| Side | `BUY SELL` |
| Order type | `LIMIT HIDDEN` |
| Order TIF | `FILL_OR_KILL GOOD_TILL_CANCELED` |
| Instrument kind | `OPTION FUTURE LOAN SPOT` |
| Option kind | `CALL PUT` |
| Margin kind | `INVERSE LINEAR` |
| RFQ state | `RFQState.OPEN RFQState.CLOSED RFQState.DRAFT` |
| Order state | `OrderState.OPEN OrderState.CLOSED OrderState.PENDING` |
| BlockTrade state | `FILLED PENDING_SETTLEMENT REJECTED` |
| Closed reason | `CANCELED_BY_CREATOR EXPIRED EXECUTION_LIMIT CLOSED_DRAFT` |
| Role | `MAKER TAKER` |
| Base currencies (RFQ-facing) | `AVAX BCH BTC ETH SOL TONCOIN TRX XRP` |

See `instruments.md` for `StrategyCodeEnum` (32 codes).

## WS — JSON-RPC 2.0

Connect with the access key as a query parameter:

```
wss://ws.api.paradigm.trade/v2/drfq/?api-key=${PARADIGM_ACCESS_KEY}&cancel_on_disconnect=false
```

Subscribe message:

```json
{"jsonrpc":"2.0","id":1,"method":"subscribe","params":{"channel":"rfq"}}
```

OpenAPI doesn't cover the WS surface — channel set is sourced from
Paradigm's docs portal. The skill subscribes to `rfq` (incoming RFQs for
makers), `order` (order state changes — replaces the "quote" channel of
other venues), and `trade_confirmation` (your own fills).

### `cancel_on_disconnect`

Query parameter on the WS URL. Default `false`.

- `true` — Paradigm cancels all of this connection's live orders if the
  socket drops. Recommended for makers running a quote book.
- `false` — orders survive disconnect. Useful for read-only subscribers.

## Rate limits

The OpenAPI spec does not declare per-endpoint rate limits. Document them
from Paradigm's portal at runtime. The skill should always pace creation
of new RFQs and orders and surface 429s with exponential backoff.
**Market-maker protection (`/v2/drfq/mmp/status/`)** is the formal
per-desk circuit breaker — check it after a burst, PATCH to re-arm once
recovered.

## Status / error codes

| Code | Meaning | What to do |
|---|---|---|
| 200 / 201 / 204 | Success | Parse JSON or accept empty body |
| 207 | Multi-status (batch delete) | Surface successes + failures; not failure |
| 400 | Bad payload | Show response body — usually a field-level validation message |
| 401 | Auth failed | See `auth.md` — diagnose in order |
| 403 | Forbidden (counterparty not whitelisted, RFQ not visible) | Surface to user; don't retry |
| 404 | RFQ / order / instrument id not found | Likely expired or wrong env (prod vs test) |
| 429 | Rate-limited | Back off; retry with exponential delay |
| 5xx | Paradigm-side | Retry once with backoff; otherwise surface |

## Useful query parameters

- `GET /v2/drfq/rfqs/?state=RFQState.OPEN&role=AuctionRole.MAKER` — RFQs
  you're invited to quote.
- `GET /v2/drfq/rfqs/?strategies=SD&venue=DBT` — only straddles on
  Deribit.
- `GET /v2/drfq/orders/?rfq_id=rfq_abc&state=OrderState.OPEN` — live
  orders against a specific RFQ.
- `GET /v2/drfq/instruments/?venue=DBT&base_currency=BTC&kind=OPTION&state=ACTIVE` —
  active BTC option universe on Deribit (paginate via `cursor`).
- `GET /v2/drfq/instruments/?venue=DBT&venue_instrument_name=BTC-7MAY26-90000-C` —
  resolve a venue-native instrument to its Paradigm integer id.

## Pagination

All list endpoints use **cursor pagination**:

```json
{"count": 123, "next": "https://.../?cursor=cD00ODY%3D", "results": [...]}
```

Follow `next` until `null`. `page_size` controls page length where
supported.
