# Black-Scholes Formulas and Greek Derivations

Reference for the greek computations in the Paradex Options Pricer skill.

## Constants

| Symbol | Value | Notes |
|--------|-------|-------|
| `YEAR_IN_DAYS` | 365 | Matches exchange calculator |
| `OPTION_EXPIRY_HOUR` | 08:00 UTC | Paradex option expiry time |

## Inputs

| Symbol | Description |
|--------|-------------|
| `S` | Underlying price (mark price of the PERP) |
| `K` | Strike price |
| `T` | Time to expiry in years = `(expiry_utc − now) / YEAR_IN_DAYS` |
| `r` | Risk-free rate (default 0; optionally derived from perp funding rate) |
| `σ` | Implied volatility (decimal, from `mark_iv` in market summaries) |

## Core Values

```
d1 = ( ln(S/K) + (r + σ²/2) × T ) / ( σ × √T )
d2 = d1 − σ × √T
```

Standard normal PDF: `N'(x) = exp(−x²/2) / √(2π)`

Standard normal CDF `N(x)`: use the Abramowitz & Stegun rational approximation (same as pm_math.py `norm_cdf`).

## Option Prices

```
Call = S × N(d1) − K × e^(−rT) × N(d2)
Put  = K × e^(−rT) × N(−d2) − S × N(−d1)
```

## Greeks

### Delta

```
Δ_call = +N(d1)
Δ_put  =  N(d1) − 1
```

Delta represents the rate of change of option price with respect to the underlying price.
Range: [0, +1] for calls, [−1, 0] for puts (as quoted).

### Gamma

```
Γ = N'(d1) / ( S × σ × √T )
```

Same for calls and puts. Gamma is highest for ATM options near expiry.

### Theta

```
Θ_call = −[ S × N'(d1) × σ / (2 × √T) + r × K × e^(−rT) × N(d2) ]  / YEAR_IN_DAYS
Θ_put  = −[ S × N'(d1) × σ / (2 × √T) − r × K × e^(−rT) × N(−d2) ] / YEAR_IN_DAYS
```

Theta is the rate of change of option price per calendar day (when dividing by YEAR_IN_DAYS).
Always negative for long options (time decay works against buyers).

Display convention: **$/calendar day** — e.g. `−$85.20/day`.

### Vega

```
V = S × N'(d1) × √T
```

Raw vega is in option price units per unit of σ (i.e. per 100% IV).

Display convention: **$/1% IV move** = raw vega ÷ 100 — e.g. `$62.40 per 1% IV`.

## Special Cases

- If `T ≤ 0` (option expired): use intrinsic value only — `max(0, S−K)` for calls, `max(0, K−S)` for puts.
- If `σ ≤ 0` (zero IV): same as above.
- If `mark_iv` is missing or 0 in market summaries: skip the option entirely (do not compute greeks).

## IV Skew Metrics

```
ATM IV         = IV of the option where |delta| is closest to 0.50
25Δ skew       = IV(25Δ put) − IV(25Δ call)      [positive = puts richer than calls]
25Δ butterfly  = 0.5 × (IV(25Δ put) + IV(25Δ call)) − ATM IV
```

"25-delta put/call" means the option whose |delta| is closest to 0.25 among puts/calls for a given expiry.

## Interest Rate (Optional)

If the perp funding rate is available, a more precise interest rate can be derived:

```
r = funding_rate_8h / ( (1 + funding_rate_8h) × (8 / (24 × 365)) )
```

Default to `r = 0` when the funding rate is unavailable or small.
