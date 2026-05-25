# Endpoints — Paradigm DRFQv2

Reference for every REST path and WS channel the `paradigm-rfq-trader`
skill touches. Authoritative source: https://api.docs.paradigm.co/

## Base URLs

| Env | REST | WS |
|---|---|---|
| Prod | `https://api.paradigm.co` | `wss://ws.api.paradigm.trade/v2/drfq/` |
| Testnet | `https://api.test.paradigm.co` | `wss://ws.api.testnet.paradigm.trade/v2/drfq/` |

Paths in this file are relative to the REST base URL. The DRFQv2 routes
live under `/v1/drfq/` (the v2 in "DRFQv2" refers to the product version,
not the URL prefix); `/v2/drfq/` paths exist for a few newer endpoints —
prefer `/v1/drfq/` unless an endpoint is only documented at v2.

## REST endpoints

### Taker — RFQ lifecycle

| Method | Path | Purpose | Rate limit |
|---|---|---|---|
| `POST` | `/v1/drfq/rfqs/` | Create a new RFQ | **1 per 3 s** |
| `GET` | `/v1/drfq/rfqs/` | List your active / recent RFQs | 500 req/s/desk |
| `GET` | `/v1/drfq/rfqs/{rfq_id}` | Fetch a single RFQ | 500 req/s/desk |
| `DELETE` | `/v1/drfq/rfqs/{rfq_id}` | Cancel an open RFQ before expiry | 500 req/s/desk |
| `GET` | `/v1/drfq/rfqs/{rfq_id}/quotes/` | List quotes posted against this RFQ | 500 req/s/desk |
| `POST` | `/v1/drfq/orders/` | Cross to execute against a chosen quote | 500 req/s/desk |
| `GET` | `/v1/drfq/orders/{order_id}` | Fetch order state | 500 req/s/desk |
| `GET` | `/v1/drfq/trades/` | List cleared trades | 500 req/s/desk |
| `GET` | `/v1/drfq/instruments/` | Catalog of tradable instruments | 500 req/s/desk |

### Maker — quote lifecycle

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/drfq/rfqs/` | Discover open RFQs you can quote |
| `POST` | `/v1/drfq/rfqs/{rfq_id}/quotes/` | Submit a quote |
| `GET` | `/v1/drfq/quotes/` | List your own active quotes |
| `DELETE` | `/v1/drfq/quotes/{quote_id}` | Pull a live quote |

Amend semantics vary by product version — if PATCH is not accepted,
cancel + re-post and let the user know that re-quoting re-anchors
price-then-time priority.

## Request bodies

### `POST /v1/drfq/rfqs/`

```json
{
  "venue": "DBT",
  "legs": [
    {"instrument": "BTC-7MAY26-90000-C", "ratio": "1", "side": "BUY"},
    {"instrument": "BTC-7MAY26-80000-P", "ratio": "1", "side": "SELL"}
  ],
  "quantity": "100",
  "counterparties": ["DSK1", "DSK2"],
  "client_order_id": "rfq-trader-1745612345678"
}
```

- `venue` — settlement venue code. `DBT` = Deribit, `OKX`, `BIT` = Bit.com.
- `legs[].instrument` — venue-native instrument name. See
  `instruments.md`.
- `legs[].ratio` — string-encoded positive integer. Per-leg multiplier.
- `legs[].side` — `BUY` or `SELL` as written. For DRFQ the taker's side
  is set on execution, not RFQ creation; legs encode the structure.
- `quantity` — string-encoded positive integer.
- `counterparties` — omit for GRFQ (open to all). Provide a list for
  DRFQ (directed to specific maker desks).
- `client_order_id` — caller-supplied idempotency key. Always set.

### `POST /v1/drfq/rfqs/{rfq_id}/quotes/`

```json
{
  "side": "BUY",
  "price": "0.0045",
  "quantity": "100",
  "client_order_id": "rfq-trader-q-1745612345678"
}
```

- `side` — `BUY` (bid) or `SELL` (offer). For two-way, post two quotes.
- `price` — string-encoded structure price in the venue's quote currency
  (BTC for Deribit BTC options).
- `quantity` — defaults to the RFQ's quantity; can quote partial.

### `POST /v1/drfq/orders/`

```json
{
  "rfq_id": "rfq_abc123",
  "quote_id": "qt_xyz789",
  "price": "0.0045",
  "quantity": "100",
  "client_order_id": "rfq-trader-x-1745612345678"
}
```

- `quote_id` — the maker quote being crossed.
- `price` — crossing limit. Set ≥ ask for buys, ≤ bid for sells.

## WS — JSON-RPC 2.0

Connect with the access key as a query parameter:

```
wss://ws.api.paradigm.trade/v2/drfq/?api-key=${PARADIGM_ACCESS_KEY}&cancel_on_disconnect=false
```

Subscribe message:

```json
{"jsonrpc": "2.0", "id": 1, "method": "subscribe", "params": {"channel": "rfq"}}
```

### DRFQv2 channels

| Channel | What you get | Who subscribes |
|---|---|---|
| `rfq` | New RFQ broadcasts on RFQs you're invited on | Makers (primary) |
| `quote` | Quote add / amend / cancel events on RFQs you own or are quoting | Both |
| `trade` | Public trade prints on this product family | Either, optional |
| `trade_confirmation` | Your own fills (both sides) | Both |

Ack format: `{"jsonrpc": "2.0", "id": 1, "result": {...}}`.

Event format: `{"jsonrpc": "2.0", "method": "subscription", "params":
{"channel": "rfq", "data": {...}}}`.

### `cancel_on_disconnect`

Query parameter on the WS URL. Default `false`.

- `true` — Paradigm cancels all of this connection's live quotes /
  orders if the socket drops. Recommended for makers running a quote
  book.
- `false` — quotes survive disconnect. Useful for read-only subscribers.

## Rate limits

- **500 req/s/desk** global. Counted across all keys on the same desk —
  not per key.
- **1 per 3 s** on `POST /v1/drfq/rfqs/`. The skill must pace bulk RFQ
  creation and tell the user when it does.
- WS connection cap and per-channel message limits exist but are not
  hit in normal interactive use.

## Status / error codes

| Code | Meaning | What to do |
|---|---|---|
| 200 / 201 | Success | Parse JSON |
| 400 | Bad payload | Show the response body to the user — usually a field-level validation message |
| 401 | Auth failed | See `auth.md` — diagnose in order |
| 403 | Forbidden (counterparty not whitelisted, RFQ not visible) | Surface to user; do not retry |
| 404 | RFQ / quote / order id not found | Likely expired or wrong env (prod vs test) |
| 429 | Rate-limited | Back off; if `POST /rfqs/`, wait 3 s before retry |
| 5xx | Paradigm-side | Retry once with backoff; otherwise surface |

## Useful query parameters

- `GET /v1/drfq/rfqs/?status=ACTIVE` — only open RFQs.
- `GET /v1/drfq/trades/?start_time=<ms>&end_time=<ms>` — time-windowed
  fills.
- `GET /v1/drfq/rfqs/{id}/quotes/?status=ACTIVE` — only live quotes
  (filters out cancelled / expired).
