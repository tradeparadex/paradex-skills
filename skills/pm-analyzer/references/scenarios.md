# PM Scenario Table

Fetched live from `paradex_system_config().portfolio_margin[base_asset].scenarios`.

Each entry: `{ spot_shock, vol_shock, weight }`.

## Structure

- **Core scenarios** (weight = 1): ±4/8/12/16% spot × +40% or −22% vol (16 scenarios)
- **Tail scenarios** (weight < 1): −66% to +500% spot with +40% vol (8 scenarios)

## Notes

- `vol_shock` applies as: `iv_shocked = iv × (1 + vol_shock × (30 / max(DTE_FLOOR, dte))^vega_power)`
- `spot_shock = 0, vol_shock < 0` (pure vol crush, no spot move) typically dominates for long vol positions
- Tail scenario sub-1 weights reduce their impact vs core scenarios
