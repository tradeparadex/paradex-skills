# Instrument Naming — Paradigm RFQ Legs

Paradigm RFQs reference instruments using the **settlement venue's**
native naming. The skill must use the exact format the venue accepts;
Paradigm does not normalize. For deeper venue notes (greeks conventions,
strike grids, endpoint quirks), see
[`../block-analyst/references/venues.md`](../../block-analyst/references/venues.md).

## Deribit (`venue: DBT`)

Primary venue for Paradigm BTC/ETH options flow.

| Product | Format | Example |
|---|---|---|
| BTC option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-7MAY26-90000-C` |
| ETH option | `ETH-DDMMMYY-STRIKE-C/P` | `ETH-10MAY26-2375-P` |
| BTC perp | `BTC-PERPETUAL` | — |
| ETH perp | `ETH-PERPETUAL` | — |

- Day is **not** zero-padded (`7MAY26`, not `07MAY26`).
- Month is the three-letter uppercase abbreviation.
- Year is two digits.
- Strike is an integer (no decimals, no commas).
- Greeks: delta is in BTC/ETH per contract (1 contract = 1 BTC or 1 ETH).
- IV is returned as a percentage (e.g. `34.52` = 34.52%).

## OKX (`venue: OKX`)

| Product | Format | Example |
|---|---|---|
| BTC option (coin-margined) | `BTC-USD-YYMMDD-STRIKE-C/P` | `BTC-USD-260507-90000-C` |
| BTC option (USDC-margined) | `BTC-USD_UM-DDMMMYY-STRIKE-C/P` | — |
| ETH option | `ETH-USD-YYMMDD-STRIKE-C/P` | — |

- Date is `YYMMDD` (six digits), **different from Deribit's `DDMMMYY`**.
- USDC-margined variants append `_UM` to the underlier — prices are in
  BTC terms but greeks may differ slightly from coin-margined.
- OKX strike grids do not always include Deribit strikes — when an
  RFQ targets a Deribit strike that OKX doesn't list, use OKX only for
  fair-value cross-check (interpolating IV by moneyness), not as an
  alternative settlement venue.

## Bit.com (`venue: BIT`)

| Product | Format | Example |
|---|---|---|
| BTC option | `BTC-DDMMMYY-STRIKE-C/P` | `BTC-07MAY26-90000-C` |

- Day **is** zero-padded (`07MAY26`).
- Otherwise identical to Deribit naming.

## Leg-string format on the Paradigm tape

The Paradigm block-trade tape's `description` field uses a compact
human-readable format that the skill may need to parse if the user pastes
a partial structure:

```
+1 C 7 May 26 90000
-1 P 7 May 26 80000
```

- Each line is one leg.
- `+` / `-` = long / short.
- Ratio follows the sign (`+1`, `-2`, etc.).
- `C` / `P` = call / put.
- Date in `D Mon YY` form.
- Strike at the end.

When constructing an RFQ from this format, convert each line to the
venue-native instrument string above. For Deribit options:

```
"+1 C 7 May 26 90000"  →  {"instrument": "BTC-7MAY26-90000-C", "ratio": "1", "side": "BUY"}
"-1 P 7 May 26 80000"  →  {"instrument": "BTC-7MAY26-80000-P", "ratio": "1", "side": "SELL"}
```

## Strategy codes (informational)

Paradigm tags structures with a strategy code on the tape. The skill
doesn't need to set this when creating an RFQ — Paradigm infers it from
the legs — but recognising codes helps when echoing structure summaries
to the user. Common ones:

| Code | Structure |
|---|---|
| `CL` / `PL` | Outright call / put |
| `ST` | Straddle |
| `SN` | Strangle |
| `BF` | Butterfly |
| `CO` | Condor |
| `CA` | Calendar |
| `RR` | Risk reversal |
| `CC` | Covered call |
| `CM` | Custom multi-leg combo |

Full list at
[`../block-analyst/references/strategy-codes.md`](../../block-analyst/references/strategy-codes.md).

## Out of scope for v1.0

Perp-only and option+perp combos exist in the Paradigm DRFQv2 namespace
(`product_codes: ["DP"]` etc. on the tape) but are deferred — the v1.0
skill covers options only. Spot RFQ is also out of scope.
