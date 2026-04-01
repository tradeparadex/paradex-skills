# Paradex MCP Tool Reference

Reference for skill developers. These are the tools available through the
[Paradex MCP server](https://github.com/tradeparadex/mcp-paradex-py).

## Public Tools (No Authentication)

These tools work without authentication. Any skill can use them for market analysis, vault screening, and system monitoring.

### System

| Tool | Purpose |
|------|---------|
| `paradex_system_state` | Exchange operational status and health |
| `paradex_system_config` | Global configuration: fee tiers, leverage settings, margin parameters |

### Market Data

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `paradex_markets` | Market specifications (tick size, min order, position limits, margin params) | `market_ids`, `jmespath_filter`, `limit`, `offset` |
| `paradex_market_summaries` | Live stats: price, 24h change, volume, open interest, funding rate | `market_ids`, `jmespath_filter`, `limit`, `offset` |
| `paradex_bbo` | Best bid/offer — real-time top-of-book prices and sizes | `market_id` |
| `paradex_orderbook` | Full orderbook depth at current moment | `market_id`, `depth` |
| `paradex_klines` | Historical OHLCV candles | `market_id`, `start_unix_ms`, `end_unix_ms`, `resolution` |
| `paradex_trades` | Recent trade executions | `market_id`, `start_unix_ms`, `end_unix_ms` |
| `paradex_funding_data` | Funding rate history | `market_id`, `start_unix_ms`, `end_unix_ms` |

### Vaults

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `paradex_vaults` | Vault discovery: config, owner, status, kind | `vault_address`, `jmespath_filter`, `limit`, `offset` |
| `paradex_vault_summary` | Performance metrics: ROI, PnL, drawdown, TVL, volume | `vault_address`, `jmespath_filter`, `limit`, `offset` |
| `paradex_vault_positions` | Vault's current open positions | `vault_address` |
| `paradex_vault_balance` | Available, locked, and total balance | `vault_address` |
| `paradex_vault_account_summary` | Account health: margin, leverage, exposure | `vault_address` |
| `paradex_vault_transfers` | Deposit/withdrawal history | `vault_address` |

### Utility

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `paradex_filters_model` | JMESPath filter schema for any tool | `tool_name` |

## Authenticated Tools (Require Private Key)

These tools require `PARADEX_ACCOUNT_PRIVATE_KEY` in the MCP server config. Use them for account management, position monitoring, and order execution.

### Account

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `paradex_account_summary` | Equity, margin usage, available balance | — |
| `paradex_account_positions` | Open positions with P&L and liquidation info | — |
| `paradex_account_fills` | Trade execution history | `market_id`, `start_unix_ms`, `end_unix_ms` |
| `paradex_account_funding_payments` | Funding payments received/paid | `market_id`, `start_unix_ms`, `end_unix_ms` |
| `paradex_account_transactions` | Full transaction history | `start_unix_ms`, `end_unix_ms`, `transaction_type`, `limit` |

### Orders

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `paradex_create_order` | Place new orders | `market_id`, `order_side`, `order_type`, `size`, `price`, `trigger_price`, `client_id`, `instruction`, `reduce_only` |
| `paradex_open_orders` | List pending orders | `market_id`, `limit`, `offset` |
| `paradex_cancel_orders` | Cancel orders | `market_id`, `order_id`, `client_id` |
| `paradex_order_status` | Check status of a specific order | `order_id`, `client_id` |
| `paradex_orders_history` | Historical order records | `market_id`, `start_unix_ms`, `end_unix_ms` |

## Common Parameters

### Time parameters

All time parameters use **Unix milliseconds** (not seconds).

```
# Example: last 24 hours
start_unix_ms: 1711900800000
end_unix_ms: 1711987200000
```

### Kline resolutions

The `resolution` parameter for `paradex_klines` accepts minutes:

| Value | Period |
|-------|--------|
| `1` | 1 minute |
| `3` | 3 minutes |
| `5` | 5 minutes |
| `15` | 15 minutes |
| `30` | 30 minutes |
| `60` | 1 hour |

### Order types

| Type | Description |
|------|-------------|
| `Market` | Execute immediately at best available price |
| `Limit` | Execute at specified price or better |
| `Stop_Limit` | Limit order triggered at stop price |
| `Stop_Market` | Market order triggered at stop price |

### Order instructions

| Instruction | Description |
|-------------|-------------|
| `GTC` | Good Till Cancelled (default) |
| `IOC` | Immediate Or Cancel |
| `POST_ONLY` | Only add liquidity, reject if would cross |

### JMESPath filtering

Many tools support `jmespath_filter` for server-side filtering. Use `paradex_filters_model` to get the schema for any tool.

```
# Top 5 vaults by TVL
"reverse(sort_by([*], &to_number(tvl)))[:5]"

# Markets with positive funding
"[?to_number(funding_rate) > `0`]"

# Vault with ROI > 10%
"[?to_number(total_roi) > `0.1`]"
```
