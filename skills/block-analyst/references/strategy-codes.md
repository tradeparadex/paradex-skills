# Strategy Codes — Paradigm Block Analyst

Maps `strategy_code` field values from the Paradigm block trade JSON to structure types.

| Code | Name | Description |
|---|---|---|
| `CL` | Outright Call | Single call option leg |
| `PL` | Outright Put | Single put option leg |
| `SN` | Strangle | OTM call + OTM put, same expiry, different strikes |
| `ST` | Straddle | ATM call + ATM put, same expiry, same strike |
| `BF` | Butterfly | Three-strike spread (long wings, short body or vice versa) |
| `CO` | Condor | Four-strike spread; wider wings than butterfly |
| `CA` | Calendar | Same strike, different expiries (long back / short front or reverse) |
| `RR` | Risk Reversal | Long OTM call + short OTM put (or reverse), same expiry |
| `CM` | Custom Multi-leg | Arbitrary combination not covered by named structures |

## Perp Combo Legs

When `product_codes` includes `DP` (BTC perp) or `EP` (ETH perp), the trade
contains one or more perpetual futures legs alongside option legs.

- Perp delta = ±1.0 per contract (sign from taker direction)
- Use `BTC-PERPETUAL` / `ETH-PERPETUAL` instrument names on Deribit for mark price
- Include perp leg in net delta calculation
