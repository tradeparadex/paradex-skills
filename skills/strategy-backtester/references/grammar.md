# Strategy JSON Grammar Reference

Complete field-level specification for the Paradex Strategy Backtester JSON format.
This is the authoritative source for valid values, operators, and defaults.

---

## Strategy Structure

```
Strategy
├── name             string           Strategy identifier (snake_case recommended)
├── underlying       "BTC"|"ETH"|"SOL"
├── capital          number           Starting equity in USD
├── riskFreeRate     number           Annual rate, default 0.05 (5%)
├── marginMode       "XM"|"PM"        Cross or Portfolio margin, default "XM"
├── maxImrPctEntry   number           Skip entry if IMR > N% of equity, default 70
├── atmIvTermDays    integer          DTE for constant-term ATM IV, default 7
├── deltaHedge       DeltaHedge
├── legs[]           Leg[]            One or more strategy legs (required)
├── entry            Entry            Entry gate conditions (required)
├── exit             Exit             Exit gate conditions (required)
└── backtest         Backtest         Date window (can be overridden via CLI)
```

---

## Time Duration Reference

`window`, `period`, `frequency`, and `maxHold` are all in **hours**.

| Hours | Label    | Common use |
|-------|----------|------------|
| 24    | 1 day    | Daily rebalance; short RV/IV lookback |
| 72    | 3 days   | Short maxHold |
| 168   | 7 days   | Weekly rebalance (default `frequency`); 7-day SMA |
| 336   | 14 days  | maxHold for 14-DTE options; 2-week SMA |
| 672   | 28 days  | maxHold for monthly cycle |
| 720   | 30 days  | Standard IV/RV percentile window |
| 1440  | 60 days  | Medium-term percentile |
| 2160  | 90 days  | Long-term percentile |

---

## Gate Logic

Entry and exit conditions both use the same gate evaluation model:

```
enabled_results = [pass/fail for each enabled condition]
pass_count      = count of True in enabled_results

threshold:
  gateMode="all"  →  threshold = len(enabled_results)   (AND — all must pass)
  gateMode="any"  →  threshold = 1                       (OR  — at least one)
  gateMode="min"  →  threshold = gateMin                 (AT LEAST N of M)

gate PASSES when pass_count >= threshold
If no conditions are enabled, the gate always passes.
```

---

## Condition Operators

| Operator   | Valid for          | Meaning |
|------------|--------------------|---------|
| `">"`      | rvPctile, ivPctile, rsi, fundingRate | greater than value |
| `"<"`      | rvPctile, ivPctile, rsi, fundingRate | less than value |
| `"above"`  | sma only           | spot price is above the SMA |
| `"below"`  | sma only           | spot price is below the SMA |

---

## Leg Schema

```json
{
  "type":        "option",      // "option" | "perp"
  "side":        "SELL",        // "BUY" | "SELL"
  "optionType":  "PUT",         // "CALL" | "PUT"  (ignored for perp)
  "strikeMode":  "delta",       // "delta" | "atm" | "otm_pct"
  "strikeParam": 0.25,          // see Strike Modes below
  "dteTarget":   14,            // days to expiry at entry (integer)
  "size":        1.0,           // contracts, or % of capital if sizeMode="pct_capital"
  "sizeMode":    "contracts"    // "contracts" | "pct_capital"
}
```

### Strike Modes

| `strikeMode`  | `strikeParam` meaning                         | Example |
|---------------|-----------------------------------------------|---------|
| `"delta"`     | Absolute BS delta (0–1). Engine binary-searches for matching strike. | `0.25` → 25Δ OTM option |
| `"atm"`       | Ignored — engine rounds spot to nearest tick. | — |
| `"otm_pct"`   | Fractional distance from spot.                | `0.10` → put at 90% of spot, call at 110% |

For puts, delta is expressed as a positive number (0.25 = 25Δ put); the engine applies the correct sign internally.

`sizeMode: "pct_capital"`: `size` × equity ÷ spot = contracts at entry time.

---

## Entry Schema

```json
{
  "frequency":   168,
  "gateMode":    "all",
  "gateMin":     2,
  "rvPctile":    { "enabled": false, "op": ">", "value": 50, "window": 168 },
  "ivPctile":    { "enabled": false, "op": ">", "value": 50, "window": 720 },
  "rsi":         { "enabled": false, "op": "<", "value": 70 },
  "sma":         { "enabled": false, "op": "above", "period": 168 },
  "fundingRate": { "enabled": false, "op": ">", "value": 0.01 }
}
```

### Conditions

| Condition     | `op`            | Fields                              | Notes |
|---------------|-----------------|-------------------------------------|-------|
| `rvPctile`    | `">"` or `"<"`  | `value` 0–100; `window` hours       | Realized-vol percentile of spot returns over `window` |
| `ivPctile`    | `">"` or `"<"`  | `value` 0–100; `window` hours       | ATM IV percentile over `window`. `value: 60` = top 40% |
| `rsi`         | `"<"` or `">"`  | `value` 0–100                       | RSI(14) of hourly closes |
| `sma`         | `"above"` or `"below"` | `period` hours               | Spot vs. SMA of closes over `period` hours |
| `fundingRate` | `">"` or `"<"`  | `value` in %/8h (e.g. `0.01`)      | 8-hour funding rate. Requires perp leg or delta hedge |

---

## Exit Schema

```json
{
  "gateMode":     "any",
  "gateMin":      2,
  "profitTarget": { "enabled": true,  "value": 25  },
  "stopLoss":     { "enabled": true,  "value": 100 },
  "ivPctile":     { "enabled": false, "op": ">", "value": 80, "window": 720 },
  "dteFloor":     { "enabled": true,  "value": 1  },
  "maxHold":      { "enabled": false, "value": 336 },
  "distToLiq":    { "enabled": false, "value": 10  }
}
```

> **EXPIRY override**: When any option leg reaches DTE = 0, all positions close regardless of `gateMode`.

### Conditions

| Condition      | `value` unit              | Semantics |
|----------------|---------------------------|-----------|
| `profitTarget` | % of entry premium        | Close when P&L ≥ value% of premium collected at entry. `25` = keep 75% of max profit. |
| `stopLoss`     | % of entry premium        | Close when loss ≥ value% of entry premium. `100` = lose at most 1× premium received. |
| `ivPctile`     | 0–100 percentile          | Same op/window semantics as entry. Typically used to cut risk when IV spikes. |
| `dteFloor`     | days remaining            | Close when any leg has ≤ value DTE. Use 1–7 for short-premium; 0 = expire worthless. |
| `maxHold`      | hours                     | Close after value hours regardless of P&L. `336` = 14 days. |
| `distToLiq`    | % distance to liquidation | Close when estimated liquidation price is within value% of spot. Requires margin computation. |

---

## DeltaHedge Object

```json
{ "enabled": true, "band": 0.1 }
```

When enabled, the engine opens a perp to neutralise net option delta whenever the portfolio delta drifts beyond `band × total_option_size`. A `band` of 0.1 means re-hedge when |Δ| > 10% of total option notional.

---

## Backtest Object

```json
{ "startDate": "2026-01-01", "endDate": "2026-04-27" }
```

ISO-8601 (`YYYY-MM-DD`), interpreted as UTC midnight. Override at runtime with `--start` / `--end` CLI flags.

---

## Window / Period Quick Reference

| Hours | Human label |
|-------|-------------|
| 24    | 1 day |
| 168   | 7 days |
| 336   | 14 days |
| 720   | 30 days |
| 1440  | 60 days |
| 2160  | 90 days |

---

## Known Schema Gaps

These are not yet supported but are natural extensions:

- **`ratio` per leg** — for ratio spreads (1×2 call spread) without duplicating legs
- **Scaled exits** — partial profit-taking (close 50% at 25% profit, rest at 50%)
- **`crossover` / `crossunder` operators** — signal when price crosses indicator, not just above/below at a snapshot

---

## Complete Example: Iron Condor

```json
{
  "name": "iron_condor_btc_14d",
  "underlying": "BTC",
  "capital": 100000,
  "atmIvTermDays": 7,
  "riskFreeRate": 0.05,
  "marginMode": "PM",
  "maxImrPctEntry": 70,
  "deltaHedge": { "enabled": false, "band": 0.1 },
  "legs": [
    { "type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" },
    { "type": "option", "side": "BUY",  "optionType": "CALL", "strikeMode": "delta", "strikeParam": 0.10, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" },
    { "type": "option", "side": "SELL", "optionType": "PUT",  "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" },
    { "type": "option", "side": "BUY",  "optionType": "PUT",  "strikeMode": "delta", "strikeParam": 0.10, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" }
  ],
  "entry": {
    "frequency": 168,
    "gateMode": "all",
    "gateMin": 2,
    "rvPctile":    { "enabled": false, "op": ">", "value": 50, "window": 168 },
    "ivPctile":    { "enabled": true,  "op": ">", "value": 55, "window": 720 },
    "rsi":         { "enabled": false, "op": "<", "value": 70 },
    "sma":         { "enabled": false, "op": "above", "period": 168 },
    "fundingRate": { "enabled": false, "op": ">", "value": 0.01 }
  },
  "exit": {
    "gateMode": "any",
    "gateMin": 2,
    "profitTarget": { "enabled": true,  "value": 50  },
    "stopLoss":     { "enabled": true,  "value": 100 },
    "ivPctile":     { "enabled": false, "op": ">", "value": 80, "window": 720 },
    "dteFloor":     { "enabled": true,  "value": 1 },
    "maxHold":      { "enabled": false, "value": 336 },
    "distToLiq":    { "enabled": false, "value": 10 }
  },
  "backtest": {
    "startDate": "2026-01-01",
    "endDate": "2026-04-27"
  }
}
```
