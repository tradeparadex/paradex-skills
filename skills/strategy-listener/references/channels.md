# WebSocket Channel Inventory

Channels exposed by `paradex_py.api.ws_client.ParadexWebsocketClient`.
The listener subscribes via:

```python
from paradex_py.api.ws_client import ParadexWebsocketChannel

await paradex.ws_client.connect()
await paradex.ws_client.subscribe(
    ParadexWebsocketChannel.<NAME>,           # or string form
    callback=on_event,
    params={"market": "BTC-USD-PERP"},        # for parameterised channels
)
```

For parameterised channels, the strategy file uses the dotted token form
`bbo.<MARKET>` and the runner translates it into the SDK's
`(channel, params)` pair.

## Public market data (no auth)

| Token form            | SDK channel              | Frequency     | Payload (key fields)                                                  |
| --------------------- | ------------------------ | ------------- | --------------------------------------------------------------------- |
| `bbo.<MARKET>`        | `BBO`                    | tick          | `bid`, `ask`, `bid_size`, `ask_size`, `seq_no`, `last_updated_at`     |
| `trades.<MARKET>`     | `TRADES`                 | tick          | `price`, `size`, `side`, `trade_type`, `created_at`                   |
| `funding.<MARKET>`    | `FUNDING_DATA`           | every funding | `funding_rate`, `funding_index`, `created_at`                         |
| `markets_summary`     | `MARKETS_SUMMARY`        | snapshot      | full markets list with mark/index/funding/oi                          |
| `orderbook.<MARKET>`  | `ORDER_BOOK`             | tick          | `bids`, `asks` arrays, `seq_no`                                       |
| `mark_price.<MARKET>` | `MARK_PRICE`             | tick          | `mark_price`, `created_at`                                            |

`<MARKET>` is the Paradex market symbol, e.g.:
- Perp: `BTC-USD-PERP`, `ETH-USD-PERP`, `SOL-USD-PERP`
- Option: `BTC-32500-C-30JAN26` (rarely used by listener — option marks
  are better fetched via REST poll because the WS option feed is heavy)

## User data (auth required)

Requires one of:
- `PARADEX_JWT_TOKEN` — pre-issued JWT (preferred; works with dashboard
  API keys or MCP-issued tokens). Injected via
  `paradex.api_client.set_token(jwt)` after construction.
- `PARADEX_ACCOUNT_PRIVATE_KEY` + `PARADEX_L1_ADDRESS` — raw L1 keys; the
  SDK signs an onboarding tx and obtains its own JWT.

JWT wins when both are present. With neither, the listener still runs
(public market channels only).

| Token form     | SDK channel | Payload (key fields)                                                                |
| -------------- | ----------- | ----------------------------------------------------------------------------------- |
| `fills`        | `FILLS`     | `market`, `side`, `size`, `price`, `fill_type`, `created_at`, `order_id`, `fill_id` |
| `orders`       | `ORDERS`    | `market`, `side`, `size`, `price`, `status`, `order_id`, `client_id`, `type`        |
| `positions`    | `POSITIONS` | `market`, `side`, `size`, `average_entry_price`, `unrealized_pnl`, `liquidation_price` |
| `account`      | `ACCOUNT`   | `account_value`, `total_collateral`, `free_collateral`, `margin_cushion`            |

## Synthetic events (emitted by the listener, not the SDK)

| Token              | When                                                         |
| ------------------ | ------------------------------------------------------------ |
| `bar_close.<MARKET>` | A `barSize`-aligned bar closes after aggregating ticks     |
| `tick.<MARKET>`    | Internal — used for indicator buffer updates only            |

`bar_close` is the canonical event for indicator-driven evaluators. The
runner derives bar OHLCV from the `trades.<MARKET>` channel (or the
`bbo.<MARKET>` mid if trades are not subscribed).

## HTTP polling fallback

When `dataMode = "poll"` (or `auto` after WS gives up), the runner emits
the same event tokens by polling REST endpoints at `pollIntervalSec`:

| Token             | REST endpoint               | Notes                                            |
| ----------------- | --------------------------- | ------------------------------------------------ |
| `bar_close.*`     | `/v1/markets/klines`        | `resolution=1` (1m), `5`, `15`, or `60` (1h)     |
| `bbo.*`           | `/v1/bbo/{market}`          | One per poll                                     |
| `funding.*`       | `/v1/markets/summary`       | `funding_rate` field                             |
| `mark_price.*`    | `/v1/markets/summary`       | `mark_price` field                               |
| `fills` (auth)    | `/v1/fills`                 | Cursor-paginated; tracks last-seen `fill_id`     |
| `orders` (auth)   | `/v1/orders`                | Polled and diffed against last snapshot          |
| `positions` (auth)| `/v1/positions`             | Polled and diffed; emits on size/PnL change      |

Polling has higher latency and lower granularity than WS — prefer WS
when available.
