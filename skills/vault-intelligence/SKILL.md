---
name: paradex-vault-intelligence
description: >
  Vault discovery, comparison, and analytics for Paradex vaults. Ranks vaults by
  risk-adjusted returns, analyzes operator track records, monitors TVL changes,
  and recommends vaults based on user risk profile — all by orchestrating the
  Paradex MCP vault tools (vaults, vault_summary, vault_positions, vault_balance,
  vault_account_summary, vault_transfers).
  Use this skill whenever the user asks about Paradex vaults, wants to find the best
  vaults, compare vault performance, understand vault risks, check a vault's positions
  or strategy, or asks "where should I deposit", "which vaults are performing well",
  "show me vault analytics", "vault ROI", "vault drawdown", or anything related to
  Paradex vault investing and passive income. Also trigger for "yield", "earn", or
  "passive" in a Paradex context. Use this skill even if the user doesn't say "vault"
  explicitly — any question about where to deposit USDC, how to earn passive returns
  on Paradex, or which strategies have the best track record should route here.
compatibility: Requires Paradex MCP server (mcp-paradex-py)
metadata:
  author: tradeparadex
  version: "1.1"
---

# Paradex Vault Intelligence

Turns raw vault data from Paradex MCP into investment-grade vault analytics.
Helps users discover, compare, and monitor vaults for passive income strategies.

## Available MCP Tools (data sources)

| Tool | What it gives you | Key params |
|---|---|---|
| `paradex_vaults` | Vault config, owner, status, kind | vault_address, jmespath_filter |
| `paradex_vault_summary` | Performance metrics (ROI, PnL, drawdown, volume, TVL) | vault_address, jmespath_filter |
| `paradex_vault_positions` | Current open positions | vault_address |
| `paradex_vault_balance` | Available/locked/total balance | vault_address |
| `paradex_vault_account_summary` | Account health, margin, exposure | vault_address |
| `paradex_vault_transfers` | Deposit/withdrawal history | vault_address |

## Capabilities

### 1. Vault Discovery & Screening

Use `paradex_vault_summary` with JMESPath to screen the full vault universe:

**By performance:**
```
# Top 5 by total ROI
"sort_by([*], &to_number(total_roi))[-5:]"

# Profitable in last 7 days
"[?to_number(roi_7d) > `0`]"

# Best 30-day performers
"sort_by([*], &to_number(roi_30d))[-10:]"
```

**By risk:**
```
# Lowest max drawdown (safer vaults)
"sort_by([*], &to_number(max_drawdown))[:5]"

# Low recent drawdown + positive returns
"[?to_number(max_drawdown_30d) < `0.05` && to_number(roi_30d) > `0`]"
```

**By size/activity:**
```
# Largest by TVL
"reverse(sort_by([*], &to_number(tvl)))"

# Most active by 24h volume
"reverse(sort_by([*], &to_number(volume_24h)))"

# Most depositors (social proof) — apply to paradex_vaults only, NOT paradex_vault_summary
# paradex_vault_summary does not include num_depositors; use paradex_vaults for this filter
"reverse(sort_by([*], &to_number(num_depositors)))"
```

Note: `num_depositors` is available on `paradex_vaults`, not `paradex_vault_summary`. For
depositor-count screening, call `paradex_vaults` and join on `vault_address` to attach
performance metrics from `paradex_vault_summary`.

### 2. Vault Deep Dive

For a specific vault, gather comprehensive data:

1. **`paradex_vaults`** — get config: owner, kind, status, creation details
2. **`paradex_vault_summary`** — performance: ROI (24h/7d/30d/total), PnL, drawdowns, volume, TVL, token price
3. **`paradex_vault_positions`** — current holdings: which markets, sizes, unrealized PnL
4. **`paradex_vault_account_summary`** — account health: margin usage, leverage, exposure
5. **`paradex_vault_balance`** — cash position: available vs. locked
6. **`paradex_vault_transfers`** — fund flows: deposit/withdrawal patterns

### 3. Vault Comparison

When comparing 2+ vaults, build a comparison matrix:

| Metric | Vault A | Vault B | Better |
|---|---|---|---|
| Total ROI | X% | Y% | ✓ higher |
| 30d ROI | X% | Y% | ✓ higher |
| Max Drawdown | X% | Y% | ✓ lower |
| Sharpe-like ratio | ROI/DD | ROI/DD | ✓ higher |
| TVL | $X | $Y | context-dependent |
| # Depositors | N | M | ✓ higher (social proof) |
| 24h Volume | $X | $Y | ✓ higher (activity) |
| Position count | N | M | context-dependent |

**Risk-adjusted ranking:**
Compute a simple Sharpe-like ratio: `total_roi / max_drawdown` (higher = better risk-adjusted returns).
For any vault where the ratio is unreliable — this includes vaults with zero or near-zero drawdown,
fewer than 30 days of data, **and small-TVL vaults where ROI may be inflated by small base effects** —
use total_roi alone but **always output the exact phrase: "insufficient drawdown history — ratio
unreliable"**. Use this exact language in both comparison tables (as a footnote `†`) and in any
narrative text. Do not substitute synonyms ("limited history", "small TVL", etc.).

### 4. Vault Risk Assessment

For each vault, assess and report:

- **Drawdown risk**: max_drawdown vs. max_drawdown_30d — is drawdown getting worse?
- **Concentration risk**: from positions — how many markets, what % of exposure is in largest position?
- **Leverage risk**: from account_summary — current leverage vs. available margin
- **Liquidity risk**: from TVL + transfers — is TVL stable, growing, or declining?
- **Operator activity**: from volume — is the vault actively trading or dormant?

**Risk score (1-5):**
- 1 (Low): Low drawdown, diversified positions, moderate leverage, stable/growing TVL
- 3 (Medium): Some drawdown history, concentrated in 2-3 markets, moderate leverage
- 5 (High): Large drawdowns, single-market concentration, high leverage, declining TVL

### 5. Vault Monitoring & Alerts

When asked to monitor a vault, check for:

- ROI dropping below a threshold
- Drawdown exceeding user-defined limit
- TVL declining (depositor exodus)
- Position concentration changing significantly
- New large positions opened (strategy shift)

### 6. Vault Recommendation Engine

When a user asks "which vault should I deposit in?", gather their preferences:

**Risk tolerance:**
- Conservative: prioritize low drawdown, stable returns, high TVL, many depositors
- Moderate: balance ROI and drawdown, accept some concentration
- Aggressive: prioritize highest ROI, accept higher drawdown and concentration

**Time horizon:**
- Short-term: weight roi_24h and roi_7d more heavily
- Medium-term: weight roi_30d and last_month_return
- Long-term: weight total_roi and max_drawdown

Then screen, score, and present top 3-5 vaults with reasoning.

## Output Format

### Vault Screening Results

**Screening format = list only.** Do NOT add per-vault narrative sections or deep-dives.
One bullet block per vault, then done. Reserve deep-dives for explicit "analyze vault X" requests.

```
## Paradex Vault Screening — [criteria]

Found N vaults matching criteria. Top picks:

| # | Vault | Total ROI | 7d ROI | Max DD | Sharpe-like | TVL | Risk |
|---|---|---|---|---|---|---|---|
| 1 | [Name/Address] | X% | X% | X% | X.Xx | $X | X/5 |
| 2 | [Name/Address] | X% | X% | N/A† | N/A† | $X | Unrated† |
| 3 | [Name/Address] | X% | X% | X% | X.Xx | $X | X/5 |

† Insufficient drawdown history (<30 days data) — Sharpe-like ratio unreliable; total ROI shown only.

*Past performance does not guarantee future results. Verify figures on the Paradex vault page before depositing. Vault operator strategies are not fully transparent — positions show current holdings but do not reveal the full strategy or risk model.*
```

### Vault Deep Dive
```
## Vault Analysis — [address]

### Performance
[ROI table across timeframes]

### Current Positions
[Position breakdown with market, size, unrealized PnL]

### Account Health
[Margin usage, leverage, available capacity]

### Risk Assessment
[Risk score with reasoning]

### Fund Flows
[Recent deposit/withdrawal trends]

### Recommendation
[Clear verdict: invest / avoid / monitor — with 1-sentence justification]

*Vault operator strategy is not fully transparent — current positions give a partial picture only. Past performance does not guarantee future results.*
```

## Important Caveats

- Past vault performance does not guarantee future results — state this clearly
- **Always recommend verifying performance figures on the Paradex UI before depositing** —
  include this in full analysis and any deposit recommendation ("Verify these figures on the
  Paradex vault page before depositing — data freshness and rounding can differ.")
- Vault token price can decline — depositors can lose money
- Withdrawal lockup periods apply — mention the vault's specific lockup
- This is analysis and screening, not investment advice
- **Vault operator strategies are not fully transparent — always state this in every response.** Positions show current holdings but do not reveal the full strategy or risk model.
- Small TVL vaults may have inflated ROI percentages from small base effects — **always flag any vault with small TVL or limited drawdown history** using the exact language: "insufficient drawdown history — ratio unreliable"

See [scoring.md](references/scoring.md) for detailed risk scoring methodology and JMESPath query cookbook.
