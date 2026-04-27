# Period Calculation & Multi-Market Fetch Reference

Timestamp resolution, edge cases, and fetch sequencing for the Trading Recap skill.

---

## 1. Timestamp Conversion Table

All timestamps passed to MCP tools are unix milliseconds (integer). All boundaries are
UTC — Paradex uses UTC throughout and there is no DST to account for.

| User expression | start_unix_ms formula | end_unix_ms formula | Notes |
|---|---|---|---|
| "today" | `floor(now / 86400000) × 86400000` | `now` | Start of current UTC day |
| "yesterday" | `floor(now / 86400000) × 86400000 − 86400000` | `floor(now / 86400000) × 86400000 − 1` | Full previous UTC day |
| "this week" | `monday_of_current_week_00:00_UTC` | `now` | ISO week: Monday start |
| "last week" | `monday_of_prev_week_00:00_UTC` | `sunday_of_prev_week_23:59:59_UTC` | Full Mon–Sun |
| "last 7 days" | `now − 7 × 86400000` | `now` | Rolling 7-day window |
| "last 24 hours" | `now − 86400000` | `now` | Rolling 24h window |
| "last N hours" | `now − N × 3600000` | `now` | Rolling N-hour window |
| "last N days" | `now − N × 86400000` | `now` | Rolling N-day window |
| Custom range | parse start datetime to unix ms | parse end datetime to unix ms | User provides both bounds |

**Computing Monday of the current week (UTC):**

```python
now_ms = current_unix_time_ms()
day_start_ms = (now_ms // 86400000) * 86400000       # 00:00 UTC today
weekday = (day_start_ms // 86400000) % 7              # 0=Thu epoch, adjust:
# Unix epoch day 0 was a Thursday. Monday offset from Thursday = -3 days.
# More directly: use date arithmetic from a known Monday reference.

# Practical formula using date math:
today_date = utc_date_from_ms(now_ms)
days_since_monday = today_date.weekday()              # 0=Monday in Python
monday_ms = day_start_ms - days_since_monday * 86400000
```

**"Last week" boundary:**

```python
monday_this_week_ms = monday_ms_from_above
monday_last_week_ms = monday_this_week_ms - 7 * 86400000
sunday_last_week_ms = monday_this_week_ms - 1         # 23:59:59.999 UTC Sunday
```

---

## 2. UTC Boundary Edge Cases

**Midnight rollover** — UTC midnight is well-defined. No clock adjustment exists.
`floor(now_ms / 86400000) × 86400000` always gives 00:00:00.000 UTC for the current day.

**Partial candles at boundaries** — Funding payments are emitted at the funding interval
(typically every 8 hours: 00:00, 08:00, 16:00 UTC). A payment timestamped at exactly
the boundary is included if the API returns it within `[start, end]`. Treat boundary
payments as included rather than excluded — document this assumption in the output if
the period boundary falls on a funding interval.

**"Yesterday" end boundary** — Use `day_start_ms - 1` (i.e., 23:59:59.999 UTC) so that
a fill or payment timestamped at 23:59:59.999 on the previous day is captured. Passing
the start of today as `end_unix_ms` would include 00:00:00.000 of today.

**Rolling vs. calendar windows** — "last 7 days" is a rolling window (`now - 7d` to `now`)
and will produce different results from "this week" (calendar week Monday–now). Clarify
which the user wants when ambiguous.

**User timezone mentions** — If the user says "yesterday my time" or mentions a local
timezone, convert their local midnight to UTC before applying the formula. Ask for
clarification if the timezone is ambiguous.

---

## 3. Multi-Market Fill Fetching — Worked Example

`paradex_account_fills` requires a `market_id` parameter. There is no "all markets" fill
endpoint. The fetch sequence is:

**Step 1 — Discover active markets**

```
CALL paradex_account_positions()
RESPONSE: [
  { "market": "BTC-USD-PERP", "size": "0.25", ... },
  { "market": "ETH-USD-PERP", "size": "-4.0", ... },
  { "market": "SOL-USD-PERP", "size": "80", ... }
]
markets = ["BTC-USD-PERP", "ETH-USD-PERP", "SOL-USD-PERP"]
```

⚠️ **Limitation:** `paradex_account_positions` only returns currently open positions. If the
user opened and fully closed a position in DOGE-USD-PERP during this recap period, that
market will NOT appear here — and its fills will be silently missed.

Mitigation: always combine the positions list with any markets the user names explicitly.
If the user says "recap my BTC and DOGE trades today", include DOGE even if no open position
exists. When delivering results, note which markets were queried:
"Recap covers: BTC-USD-PERP, ETH-USD-PERP, SOL-USD-PERP (currently open) + any you named."

**Step 2 — Resolve timestamps** (example: "today", called at 14:32 UTC on 2026-04-16)

```
now_ms       = 1744813920000      # 2026-04-16 14:32:00 UTC
start_ms     = 1744761600000      # 2026-04-16 00:00:00 UTC
end_ms       = now_ms
```

**Step 3 — Fetch fills per market** (can be parallelized)

```
CALL paradex_account_fills(market_id="BTC-USD-PERP", start_unix_ms=1744761600000, end_unix_ms=1744813920000)
CALL paradex_account_fills(market_id="ETH-USD-PERP", start_unix_ms=1744761600000, end_unix_ms=1744813920000)
CALL paradex_account_fills(market_id="SOL-USD-PERP", start_unix_ms=1744761600000, end_unix_ms=1744813920000)

RESULTS:
  BTC-USD-PERP: 3 fills
  ETH-USD-PERP: 2 fills
  SOL-USD-PERP: 0 fills  ← position exists but no fills in window
```

**Step 4 — Fetch orders per market** (can be parallelized with Step 3)

```
CALL paradex_orders_history(market_id="BTC-USD-PERP", start_unix_ms=..., end_unix_ms=...)
CALL paradex_orders_history(market_id="ETH-USD-PERP", ...)
CALL paradex_orders_history(market_id="SOL-USD-PERP", ...)
```

**Step 5 — Fetch funding payments** (single call, no market filter)

```
CALL paradex_account_funding_payments(start_unix_ms=1744761600000, end_unix_ms=1744813920000)
RESPONSE: [
  { "market": "BTC-USD-PERP", "funding_payment": "-4.91", "created_at": 1744790400000 },
  { "market": "ETH-USD-PERP", "funding_payment": "-3.57", "created_at": 1744790400000 },
  { "market": "SOL-USD-PERP", "funding_payment": "4.51",  "created_at": 1744790400000 }
]
```

**Step 6 — Aggregate and compute**

```python
all_fills   = btc_fills + eth_fills + sol_fills   # flatten
all_orders  = btc_orders + eth_orders + sol_orders

realized_pnl_gross = sum(float(f.realized_pnl or 0) for f in all_fills)
total_fees         = sum(float(f.fee) for f in all_fills)
funding_pnl        = sum(float(p.funding_payment) for p in funding_payments)
net_pnl            = realized_pnl_gross + funding_pnl - total_fees
```

---

## 4. Handling Empty Results Gracefully

**No fills in the period** — Return a minimal summary without the P&L and win rate
sections. Example response:

```
## Trading Recap — Today (2026-04-16 00:00–14:32 UTC)

No fills recorded in this period.

Orders placed: 2 (both cancelled)
Markets with open positions: BTC-USD-PERP, ETH-USD-PERP, SOL-USD-PERP

Funding payments in period: -$13.00
(Funding accumulates even when no trades are placed.)
```

**No orders and no fills** — The account was idle. Say so plainly.

**Positions list is empty** — `paradex_account_positions` returned nothing. The account
has no open positions. The market list for fill fetching is empty. Skip the per-market
fetch loop and report "no active positions and no fills to show."

**Partial markets missing** — If one market returns an API error, note it explicitly:
"ETH-USD-PERP fills could not be fetched (API error). Results exclude that market."
Do not silently omit data — incomplete totals are worse than flagged gaps.

**Win rate section suppression** — If `len(closing_fills) < 3`, omit the Win Rate section
entirely and add a footnote: "Win rate not shown — fewer than 3 closing fills in period."

---

## 5. Detecting Opened vs. Closed Positions During the Period

A fill's role (opening or closing) can be inferred from `realized_pnl` on the fill.

**Opening fill** — A fill that increases a position (or initiates a new one) from flat
produces `realized_pnl == 0` (or the field is absent/null). No P&L is realized because
no position was closed.

**Closing fill** — A fill that reduces or fully closes a position produces
`realized_pnl != 0`. The value reflects the P&L on the closed portion.

**Partial close** — A single fill can partially close a position. `realized_pnl` reflects
only the closed portion. The fill is still counted as a closing fill for win rate.

**Practical detection:**

```python
def is_closing_fill(fill):
    pnl = float(fill.get("realized_pnl") or 0)
    return pnl != 0

closing_fills = [f for f in all_fills if is_closing_fill(f)]
opening_fills = [f for f in all_fills if not is_closing_fill(f)]
```

**Opened during period** — An order placed and filled during the period where the fill
has `realized_pnl == 0` opened (or added to) a position. This contributes to volume
but not to win rate.

**Closed during period** — An order filled with `realized_pnl != 0` closed (or reduced)
a position that was opened previously (possibly before the period). The full realized
P&L is captured regardless of when the position was opened.

**Opened and closed in the same period (round trip)** — Appears as a sequence of fills:
first one or more opening fills (`realized_pnl == 0`), then one or more closing fills
(`realized_pnl != 0`). Both appear in the order log; only closing fills feed win rate.

**Caveat on order type signals** — Order type (LIMIT, MARKET, STOP) does not directly
indicate open vs. close intent. Rely on `realized_pnl` presence, not order type.
