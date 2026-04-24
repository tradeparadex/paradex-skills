# Portfolio Margin (PM) Pipeline

Applies only when `margin_methodology == "portfolio_margin"` (account-level setting).

## Constants

All constants come from `paradex_system_config().portfolio_margin` for the account's base asset — do not hardcode them. Fixed math constants only:

```python
OPTION_EXPIRY_HOUR   = 8      # UTC — option expiry time
YEAR_IN_DAYS         = 365    # matches exchange calculator
MIN_VOL_SHOCK_UP     = 0.40   # floor on shocked IV for upward vol scenarios
TWAP_SETTLEMENT_MIN  = 30     # minutes; options near expiry scale PnL by liveFrac
```

## 24-Scenario Table

Fetch live from `paradex_system_config().portfolio_margin[base_asset].scenarios`. Each entry has `spot_shock`, `vol_shock`, `weight`. Core scenarios: ±4/8/12/16% spot × ±22/40% vol (weight=1). Tail scenarios: −66% to +500% spot (weight < 1).

## Step 1: Scenario Scan

For each of 24 scenarios `(spot_shock s, vol_shock v, weight w)`:

```
S_shock = spot × (1 + s)

# Perp repricing:
price_perp = S_shock × (1 + basis)
  where basis = (perp_mark − spot) / spot

# Dated option repricing (Black-Scholes):
dte          = (expiry_utc − now) / 86400        # days
tte          = dte / YEAR_IN_DAYS
vega_power   = VEGA_POWER_ST if dte < 30 else VEGA_POWER_LT
mult         = (30 / max(DTE_FLOOR, dte))^vega_power
iv_shocked   = mark_iv × (1 + v × mult)
if v > 0: iv_shocked = max(iv_shocked, MIN_VOL_SHOCK_UP)  # floor on upward shocks
price_option = BS(S_shock, strike, tte, interest_rate, iv_shocked, is_call)
  where interest_rate is derived from perp funding rate (not 0)

# Perp option: mark_price unchanged (no repricing)

# TWAP settlement (final 30 min before expiry):
liveFrac     = seconds_to_expiry / (TWAP_SETTLEMENT_MIN × 60)   # 0..1
# Apply to PnL: pos_pnl += liveFrac × (scen_price − mark) × weight × signed_size
```

Position PnL per scenario:
```
posPnl[i] = Σ (scen_price[i] − mark_price) × w[i] × signed_size
```

Order PnL per scenario (adverse fill only):
```
gap        = (price − scen_price)   for BUY order
           = (scen_price − price)   for SELL order
ordPnl[i]  = Σ −size × max(0, gap) × w[i]
```

```
worstLoss = max(0, −min(totalPnl[i]))   for i in 0..23
```

## Step 2: Delta-Min Floor

```
mL  = Σ max(0,  pos_delta)       # long pos delta
mS  = Σ max(0, −pos_delta)       # short pos delta
loO = Σ max(0,  ord_delta)       # long order delta
soO = Σ max(0, −ord_delta)       # short order delta

maxL   = mL + loO
maxS   = mS + soO
maxU   = max(0, max(maxL − mS, maxS − mL))    # unhedged
hedged = max(0, max(maxL, maxS) − maxU)

deltaMin = (hedged × HEDGED_MF + maxU × UNHEDGED_MF) × spot
```

## Step 3: Funding Provision

```
fr8h = funding_rate   # from paradex_market_summaries

# Net positions and orders together, then apply max(0, −total)
# matches Go engine: posFundingPnL = −fr × signedSize × spot
#                    ordFundingPnL = +fr × ordSignedSize × spot
pos_fund_sum = Σ −fr8h × signed_pos_size × spot   (perp positions only)
ord_fund_sum = Σ  fr8h × signed_ord_size × spot   (perp orders only)

total_funding = pos_fund_sum + ord_fund_sum
fundP = max(0, −total_funding)     # IMR: positions + orders combined
pF    = max(0, −pos_fund_sum)      # MMR: positions only
```

## Step 4: Fee Provision & IMR/MMR

```
# Fee provision (spec §8.2): HFR per market from market data fee_rate field
# Non-option: HFR × size × mark_price
# Option:     min(HFR × spot_price, 0.125 × mark_price) × size
fee_pos = Σ fee_provision(market, |size|)   for positions
fee_ord = Σ fee_provision(market, size)     for orders
fee_imr = fee_pos + fee_ord    # IMR: positions + orders
fee_mmr = fee_pos              # MMR: positions only

netIM  = max(worstLoss, deltaMin)           # includes orders
pmIMR  = netIM + fundP + fee_imr + spotBM

# MMR: positions only, no orders, × MMR_FACTOR
posW   = max(0, −min(posPnl[i]))
p_nd   = Σ pos_delta                        # net pos delta
p_gd   = Σ |pos_delta|                      # gross pos delta
p_H    = (p_gd − |p_nd|) / 2
p_DM   = (UNHEDGED_MF × |p_nd| + HEDGED_MF × p_H) × spot
posNI  = max(posW, p_DM)

pmMMR  = posNI × MMR_FACTOR + pF + fee_mmr + spotBM
```
