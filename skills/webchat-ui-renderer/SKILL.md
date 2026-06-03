---
name: paradex-webchat-ui-renderer
description: >
  Renders structured UI components for the Paradex webchat terminal instead of plain text.
  Outputs raw JSON specs that the webchat UI parses and renders as rich components: metric cards,
  positions tables, performance charts, alert banners, labeled outputs, and data tables.
  Use this skill whenever responding in the webchat channel with data that benefits from visual
  structure: account summaries, open positions, price charts, funding rate tables, trade history,
  KPI metrics, or any risk/margin alerts. Trigger phrases include "show my positions",
  "account summary", "plot price", "chart", "table", or any Paradex data query in the webchat
  context. Do NOT use for plain conversational replies - use the markdown component for those.
compatibility: No MCP tools required — formats structured data as webchat UI JSON specs
metadata:
  author: tradeparadex
  version: "1.1"
---

# Webchat UI Renderer

Outputs raw JSON UI specs consumed by the Paradex webchat terminal. The webchat parses these directly from the agent message stream.

## Output Rules

> **CRITICAL — no code fences, ever.** Output the raw JSON object starting with `{` directly. Never wrap in ` ```json ``` ` or any other fence. The webchat parses the raw message stream — fences break rendering.

- **Output ONLY the JSON spec** — no prose before or after, no markdown code fences
- Every response that contains data MUST use a UI spec; never output plain text tables or bullet lists for structured data in webchat
- Use `markdown` component for conversational/explanatory text when needed alongside data components
- Generate a unique `id` per response (e.g. `"positions-001"`, `"summary-002"`)

## Spec Format

```json
{
  "id": "<unique-id>",
  "layout": "stack | grid",
  "columns": "<1-4, grid only>",
  "children": [
    { "component": "<component_id>", "props": {} }
  ]
}
```

Use `"layout": "grid"` with `"columns": 2â€“4` for side-by-side metric cards. Use `"layout": "stack"` for everything else.

## Component Selection Guide

| Data type | Component |
|---|---|
| Account value, free collateral, margin ratio | `metric_card` |
| Open positions | `positions_table` |
| Price history, PnL curve, equity chart | `performance_chart` |
| Key-value pairs (entry price, liq price, fee) | `labeled_output` |
| Funding rates, trade history, orderbook, fills | `data_table` |
| Risk warnings, margin alerts, status | `alert_banner` |
| Explanations, analysis, freeform text | `markdown` |

## Component Schemas

Read `references/components.json` for full prop schemas. Summary:

- **alert_banner**: `variant` (error/warning/info) + `message`
- **metric_card**: `label` + `value` + optional `direction` (up/down for color)
- **labeled_output**: `label` + `value` + optional `direction`
- **performance_chart**: `label` + `values` (array of `{name, value}`) + optional `tooltip/grid/yAxis`
- **positions_table**: `positions` array â€” each item requires `market`, `marketDisplayName`, `side`, `size` (formatted+value), `averageEntryPrice`, `markPrice` (formatted+value), `liquidationPrice` (formatted+value), `unrealizedPnl` (formatted+value+direction+percent), `leverage`, `notional` (formatted+value)
- **data_table**: `columns` (array of `{key, header, align}`) + `rows` (array of objects)
- **markdown**: `content` (markdown string)

## Typical Patterns

**Account summary** â†’ `alert_banner` (status) + grid of `metric_card`s + `positions_table`

**Positions only** â†’ `positions_table`

**Price chart** â†’ `performance_chart` with hourly close prices, x-axis as HH:MM labels

**Funding rate table** â†’ `data_table` with columns: Market, Rate, 8h Payment

**Risk alert** â†’ `alert_banner` (error/warning) + `labeled_output`s for liq distance, margin ratio

## Updating the Component Registry

When the user asks to refresh or update components, fetch:
`https://app.paradex.trade/agent-components.json`

If the URL returns valid JSON, overwrite `references/components.json` with the new content (preserve the `update_url` field). If it returns HTML or an error, keep the existing cached version and notify the user.
