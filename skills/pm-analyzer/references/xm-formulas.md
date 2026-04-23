# Cross-Margin (XM) Formulas

## Delta-1 (Perps / Futures)

From `delta1_cross_margin_params` on each market:

| Field | Meaning |
|---|---|
| `imf_base` | Initial margin factor (e.g. `0.02` = 2%) |
| `mmf_factor` | MMR as a multiplier of IMR (e.g. `0.5` = 50% of IMR) |
| `imf_factor` | Size-dependent scaling (usually `0`) |
| `imf_shift` | Additive shift (usually `0`) |

```
notional = |size| × mark_price
IMR      = notional × imf_base
MMR      = IMR × mmf_factor
```

Example: 0.0005 BTC SHORT perp, mark $77,889, imf_base=0.02, mmf_factor=0.5
→ notional = $38.94 → IMR = $0.78, MMR = $0.39

## Long Options

Long options cannot lose more than the premium paid. Margin = current mark premium.

```
IMR = mark_price × size
MMR = IMR × 0.5
```

Example: LONG 0.001 BTC call, mark $2,456
→ IMR = $2.456, MMR = $1.228

## Short Options

From `option_cross_margin_params`:

```json
{
  "imf": {
    "long_itm": "0.2",
    "premium_multiplier": "1",
    "short_itm": "0.15",
    "short_otm": "0.1",
    "short_put_cap": "0.5"
  },
  "mmf": {
    "long_itm": "0.1",
    "premium_multiplier": "0.5",
    "short_itm": "0.075",
    "short_otm": "0.05",
    "short_put_cap": "0.5"
  }
}
```

Moneyness check:
```
is_itm = (is_call AND spot > strike) OR (is_put AND spot < strike)
imf    = short_itm if is_itm else short_otm
mmf    = mmf.short_itm if is_itm else mmf.short_otm
```

Margin:
```
underlying_notional = |size| × spot_price
IMR = max(imf × underlying_notional, premium_multiplier × mark × size)
MMR = max(mmf × underlying_notional, mmf.premium_multiplier × mark × size)
```

Note: short_put_cap applies a notional cap for put options:
```
IMR = min(IMR, short_put_cap × underlying_notional)
```

> ⚠️ Short option XM formulas are not yet empirically verified against exchange values.

## Spot Balance Margin

Non-USDC spot token balances contribute to collateral but also add margin at full USD value:

```
spotBM = Σ |balance[token]| × price[token]   for token ≠ USDC
```

Spot token prices: use mark prices from `paradex_market_summaries` for `{TOKEN}-USD` or `{TOKEN}-USD-PERP` markets.

## Empirical Verification

Verified against exchange for cross-margin account with:
- SHORT 0.0005 BTC-USD-PERP: calc IMR $0.779, exchange ~$0.779 ✓
- LONG 0.001 BTC-USD-8MAY26-78000-C: calc IMR $2.456, exchange ~$2.471 ✓

Total IMR diff: ~$0.016 (attributed to fee provision, not directly accessible via MCP).
