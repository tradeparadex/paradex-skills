# Strategy Template Catalogue

Ready-to-use strategy templates for the Paradex Strategy Backtester.
Load via the **Templates** dropdown in the HTML tool, or generate the equivalent JSON when the user asks for one of these structures.

All templates default to BTC underlying, $100K capital, PM margin. Adjust as needed.

## Template Reference

| Template key | Legs | Thesis |
|---|---|---|
| `iron_condor` | Sell 25Δ P + Buy 10Δ P + Sell 25Δ C + Buy 10Δ C | Defined-risk short vol — collect premium from both wings, capped by long OTM wings. Best in low/falling IV, range-bound spot |
| `short_strangle` | Sell 25Δ P + Sell 25Δ C | Naked short vol — higher premium than condor but unlimited downside on either tail. Uses far more margin |
| `long_strangle` | Buy 25Δ P + Buy 25Δ C | Long vol — needs a large spot move. Pays theta every day. TP 100% / SL 50% |
| `long_straddle` | Buy 50Δ P + Buy 50Δ C | ATM long vol — maximum convexity and maximum theta cost. Often paired with delta hedging |
| `short_straddle_hedged` | Sell 50Δ P + Sell 50Δ C + δ-hedge perp | Vol-carry — collect ATM theta, perp neutralises delta within 10% band |
| `bull_call_spread` | Buy 50Δ C + Sell 25Δ C | Bullish directional, defined risk — profits if spot drifts up moderately |
| `bear_put_spread` | Buy 50Δ P + Sell 25Δ P | Bearish directional — mirror of bull call spread |
| `cash_secured_put` | Sell 25Δ P, 30 DTE | Yield — harvest put premium, willing to be assigned at the strike. No stop loss |
| `covered_call_perp` | Buy 1 perp + Sell 25Δ C | Long perp + short OTM call — caps upside in exchange for steady premium |
| `collar` | Buy 1 perp + Buy 25Δ P (30d) + Sell 25Δ C (30d) | Long perp with downside floor and upside cap. Often near-zero cost |
| `iron_butterfly` | Sell 50Δ P + Buy 25Δ P + Sell 50Δ C + Buy 25Δ C | Short ATM straddle hedged by OTM wings. Narrow profit zone — needs spot to pin |
| `jade_lizard` | Sell 25Δ P + Sell 50Δ C + Buy 25Δ C | No upside risk when net credit > call-spread width. Short-biased |
| `call_ratio_spread` | Buy 50Δ C + Sell 2× 25Δ C | 1×2 ratio — profit if spot moves to the short strike, undefined risk above |
| `long_butterfly_call` | Buy 70Δ C + Sell 2× 50Δ C + Buy 30Δ C | 1-2-1 butterfly — cheap, max profit at middle strike at expiry |
| `risk_reversal` | Sell 25Δ P + Buy 25Δ C | Synthetic long — short OTM put pays for the OTM call. Often near-zero cost with put skew |
| `calendar_call` | Sell 50Δ C (7d) + Buy 50Δ C (30d) | Sell near-dated theta, buy far-dated vega. Profits if spot pins + back-month IV rises |
| `diagonal_call` | Sell 25Δ C (7d) + Buy 50Δ C (30d) | Bullish calendar — long ATM call gives upside, short OTM call pays for it via decay |

## Regime Guide

| Market Regime | Best Templates | Avoid |
|---|---|---|
| **High IV, range-bound** | iron_condor, short_strangle, short_straddle_hedged | long_strangle, long_straddle |
| **Low IV, range-bound** | long_strangle, long_straddle | iron_condor, short_strangle |
| **Trending upward** | bull_call_spread, risk_reversal, covered_call_perp | iron_condor, short_strangle |
| **Trending downward** | bear_put_spread | iron_condor, covered_call_perp |
| **High funding (perp longs pay)** | collar, covered_call_perp (perp long receives funding asymmetric) | naked perp long |
| **Low IV + upside event risk** | jade_lizard (no upside risk), call_ratio_spread | iron_butterfly |
| **Term structure steep (front < back)** | calendar_call, diagonal_call | near-term iron condor |
| **Term structure flat** | near-term short_strangle | calendar spreads |

## Entry Condition Guidance

| Template family | Recommended entry conditions |
|---|---|
| Short vol (iron_condor, strangle, straddle) | `ivPctile > 50–65` over 30d; no directional filter |
| Long vol (strangle, straddle) | `ivPctile < 30–40` over 30d; optionally RSI extreme |
| Directional (bull/bear spreads, risk_reversal) | SMA above/below for trend; optionally RSI |
| Carry/perp strategies (collar, covered_call) | Funding rate filter (`> 0.01%/8h` for positive carry) |
| Calendar/diagonal | ATM IV percentile + term structure slope check |

## Exit Condition Guidance

| Template family | Recommended exits |
|---|---|
| Short vol | TP: 25–50% of premium; SL: 100–200%; DTE floor: 1–7 days |
| Long vol | TP: 100–200%; SL: 40–50%; max hold: DTE target |
| Directional | TP: 50–100%; SL: 50%; DTE floor: 1 day |
| Delta-hedged | TP: 20–35% (theta carry); SL: 75–100%; DTL enabled |
| Calendar | max hold: same as short leg DTE; close if front leg near expiry |

## JSON Leg Patterns

### Standard 25Δ call/put legs (e.g. for iron condor)
```json
{ "type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" }
{ "type": "option", "side": "BUY",  "optionType": "CALL", "strikeMode": "delta", "strikeParam": 0.10, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" }
```

### ATM (50Δ) legs
```json
{ "type": "option", "side": "SELL", "optionType": "CALL", "strikeMode": "delta", "strikeParam": 0.50, "dteTarget": 14, "size": 1.0, "sizeMode": "contracts" }
```

### Perp leg
```json
{ "type": "perp", "side": "BUY", "size": 1.0, "sizeMode": "contracts" }
```

### Size by % of capital (alternative to fixed contracts)
```json
{ "type": "option", "side": "SELL", "optionType": "PUT", "strikeMode": "delta", "strikeParam": 0.25, "dteTarget": 30, "size": 5.0, "sizeMode": "pct_capital" }
```
`size: 5.0` with `sizeMode: pct_capital` means 5% of current account equity ÷ spot price = number of contracts.
