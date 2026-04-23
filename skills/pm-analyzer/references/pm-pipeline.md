# Portfolio Margin (PM) Pipeline

Applies only when `margin_methodology == "portfolio_margin"` (account-level setting).

## Constants

All constants come from `paradex_system_config().portfolio_margin` for the account's base asset — do not hardcode them. Fixed math constants only:

```python
OPTION_EXPIRY_HOUR = 8     # UTC — option expiry time
YEAR_IN_DAYS       = 365.25
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
iv_shocked   = mark_iv × (1 + v × (30 / max(DTE_FLOOR, dte))^vega_power)
price_option = BS(S_shock, strike, tte, interest_rate, iv_shocked, is_call)

# Perp option: mark_price unchanged (no repricing)
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

# Perp positions (netted across long/short):
pf_sum = Σ fr8h × signed_size × spot   (perp positions only)
pF     = max(0, pf_sum)

# Perp orders (NOT netted):
oF = Σ max(0, fr8h × size × direction_sign × spot)

fundP = pF + oF
```

## Step 4: IMR & MMR

```
netIM  = max(worstLoss, deltaMin)           # includes orders
pmIMR  = netIM + fundP + spotBM

# MMR: positions only, no orders, × MMR_FACTOR
posW   = max(0, −min(posPnl[i]))
p_nd   = Σ pos_delta                        # net pos delta
p_gd   = Σ |pos_delta|                      # gross pos delta
p_H    = (p_gd − |p_nd|) / 2
p_DM   = (UNHEDGED_MF × |p_nd| + HEDGED_MF × p_H) × spot
posNI  = max(posW, p_DM)

pmMMR  = posNI × MMR_FACTOR + pF + spotBM
```
