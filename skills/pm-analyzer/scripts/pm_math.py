"""
pm_math.py — Pure margin calculation functions, no I/O, no auth.

Input: plain dicts (positions, orders, market data) — can come from
       the Paradex API, a JSON file, a test fixture, or the MCP server.

Output: structured dicts with IMR, MMR, delta, per-position breakdown.

All formulas mirror the PM Calculator web app (_recalc() in index.html).
"""

import math
from datetime import datetime, timezone

# ── Constants ──────────────────────────────────────────────────────────────
VEGA_POWER_ST      = 0.30
VEGA_POWER_LT      = 0.13
DTE_FLOOR          = 1
UNHEDGED_MF        = 0.02
HEDGED_MF          = 0.01
MMR_FACTOR         = 0.50
YEAR_IN_DAYS       = 365        # matches exchange calculator (not 365.25)
OPTION_EXPIRY_HOUR = 8          # UTC
MIN_VOL_SHOCK_UP   = 0.40       # floor on upward vol shocks (spec §2.3)
TWAP_SETTLEMENT_MIN = 30        # minutes before expiry when TWAP kicks in

SCENARIOS = [
    [0.16,0.40,1],[0.12,0.40,1],[0.12,-0.22,1],[0.08,0.40,1],[0.08,-0.22,1],
    [0.04,0.40,1],[0.04,-0.22,1],[0.0,0.40,1],[0.0,-0.22,1],
    [-0.04,0.40,1],[-0.04,-0.22,1],[-0.08,0.40,1],[-0.08,-0.22,1],
    [-0.12,0.40,1],[-0.12,-0.22,1],[-0.16,0.40,1],
    [-0.66,0.40,0.18],[-0.33,0.40,0.36],[0.50,0.40,0.24],
    [1.0,0.40,0.12],[2.0,0.40,0.06],[3.0,0.40,0.04],[4.0,0.40,0.03],[5.0,0.40,0.024],
]
WEIGHTS = [s[2] for s in SCENARIOS]
N_SC = len(SCENARIOS)

MONTH_MAP = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
             "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}


# ── Black-Scholes ──────────────────────────────────────────────────────────

def norm_cdf(x: float) -> float:
    a1,a2,a3,a4,a5,p = 0.254829592,-0.284496736,1.421413741,-1.453152027,1.061405429,0.3275911
    sign = -1 if x < 0 else 1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1 - (((((a5*t+a4)*t)+a3)*t+a2)*t+a1)*t * math.exp(-x*x)
    return 0.5 * (1 + sign * y)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K if is_call else K - S)
    cp = 1 if is_call else -1
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return cp * S * norm_cdf(cp * d1) - cp * K * math.exp(-r * T) * norm_cdf(cp * d2)


# ── Market parsing ─────────────────────────────────────────────────────────

def parse_expiry(s: str):
    """'8MAY26' → datetime UTC 08:00, or None."""
    import re
    m = re.match(r"^(\d{1,2})([A-Z]{3})(\d{2})$", s or "")
    if not m:
        return None
    return datetime(2000 + int(m.group(3)), MONTH_MAP[m.group(2)], int(m.group(1)),
                    OPTION_EXPIRY_HOUR, tzinfo=timezone.utc)


def parse_market(symbol: str) -> dict | None:
    """
    Parse a Paradex market symbol into its components.
    Returns dict with 'type', 'is_call', 'strike', 'expiry' or None.
    """
    parts = symbol.split("-")
    if parts[-1] == "PERP":
        return {"type": "perp"}
    if parts[-1] in ("C", "P"):
        is_call = parts[-1] == "C"
        if len(parts) == 5:
            # e.g. BTC-USD-8MAY26-78000-C
            p2_num = parts[2].replace(".", "").isdigit()
            strike = float(parts[3] if not p2_num else parts[2])
            exp_str = parts[2] if not p2_num else parts[3]
            return {"type": "dated_option", "is_call": is_call,
                    "strike": strike, "expiry": parse_expiry(exp_str)}
        if len(parts) == 4:
            return {"type": "perp_option", "is_call": is_call, "strike": float(parts[2])}
    return None


# ── Input schema (what callers must provide) ───────────────────────────────
#
# positions: list of dicts, each:
#   { "market": str, "side": "BUY"|"SELL"|"LONG"|"SHORT", "size": float }
#
# orders: list of dicts, each:
#   { "market": str, "side": "BUY"|"SELL", "size": float, "price": float }
#
# market_data: dict keyed by symbol, each value:
#   {
#     "mark_price": float,
#     "delta": float,          # per-unit delta (1.0 for perp)
#     "mark_iv": float,        # implied vol (options only)
#     "underlying_price": float,
#     "funding_rate": float,   # 8h rate (perps only)
#     "interest_rate": float,  # derived from funding rate; used in BS (optional)
#     "fee_rate": float,       # HFR taker fee rate for this market (optional)
#   }
#
# market_specs: dict keyed by symbol, each value:
#   {
#     "asset_kind": str,       # "PERP", "OPTION", etc.
#     "strike_price": str,
#     "option_type": str,      # "CALL" | "PUT"
#     "delta1_cross_margin_params": {
#         "imf_base": float,
#         "imf_factor": float,   # size-scaling coefficient
#         "imf_shift": float,    # size threshold where scaling starts
#         "mmf_factor": float,
#     },
#     "option_cross_margin_params": {
#         "imf": { "long_itm", "short_itm", "short_otm", "short_put_cap",
#                  "premium_multiplier" },
#         "mmf": { "long_itm", "short_itm", "short_otm", "short_put_cap",
#                  "premium_multiplier" },
#     },
#     "order_size_increment": str,
#   }
#
# balances: list of { "token": str, "size": float }  (optional)
# margin_methodology: "cross_margin" | "portfolio_margin"


# ── XM margin formulas ─────────────────────────────────────────────────────

def _xm_option_margin(params: dict, mark: float, spot: float, strike: float,
                      is_call: bool, is_long: bool, size: float) -> float:
    """
    Compute XM margin for one side (imf or mmf params) of an option position.

    Long:  min(mark × premium_multiplier, long_itm × spot) × size
    Short: max(short_itm × spot − otm_amount, short_otm × spot) × size
           capped at short_put_cap × spot × size for puts
    """
    if is_long:
        pm  = float(params.get("premium_multiplier") or 1.0)
        li  = float(params.get("long_itm") or 1.0)
        return min(mark * pm, li * spot) * size
    else:
        otm_amt = max(0.0, (strike - spot) if is_call else (spot - strike))
        raw = max(
            float(params.get("short_itm") or 0.15) * spot - otm_amt,
            float(params.get("short_otm") or 0.10) * spot,
        )
        if not is_call:
            cap = float(params.get("short_put_cap") or 0.5)
            raw = min(raw, cap * spot)
        return raw * size


def xm_position(pos: dict, market_data: dict, market_specs: dict) -> dict:
    """
    Compute cross-margin IMR/MMR for a single position.

    Returns:
        { imr, mmr, delta_contrib, mark_price, notional }
    """
    sym   = pos["market"]
    side  = pos["side"]
    size  = abs(float(pos["size"]))
    md    = market_data.get(sym, {})
    spec  = market_specs.get(sym, {})

    mark  = float(md.get("mark_price") or 0)
    delta = float(md.get("delta") or 0)
    signed_size = size if side in ("BUY", "LONG") else -size
    delta_contrib = delta * signed_size
    notional = size * mark
    asset_kind = spec.get("asset_kind", "")

    if asset_kind in ("PERP", "FUTURE"):
        xm        = spec.get("delta1_cross_margin_params") or {}
        imf_base  = float(xm.get("imf_base")   or 0.02)
        imf_factor = float(xm.get("imf_factor") or 0.0)
        imf_shift  = float(xm.get("imf_shift")  or 0.0)
        mmf       = float(xm.get("mmf_factor")  or 0.5)
        # Size-scaled IMF: imf_base + imf_factor × √max(0, size − imf_shift)
        imf = imf_base + imf_factor * math.sqrt(max(0.0, size - imf_shift))
        imr = notional * imf
        mmr = imr * mmf

    elif asset_kind in ("OPTION", "PERP_OPTION"):
        ocp    = spec.get("option_cross_margin_params") or {}
        imf_p  = ocp.get("imf") or {}
        mmf_p  = ocp.get("mmf") or {}
        spot   = float(md.get("underlying_price") or mark)
        strike = float(spec.get("strike_price") or 0)
        is_call = spec.get("option_type") == "CALL"
        is_long = side in ("BUY", "LONG")
        imr = _xm_option_margin(imf_p, mark, spot, strike, is_call, is_long, size)
        mmr = _xm_option_margin(mmf_p, mark, spot, strike, is_call, is_long, size)
    else:
        imr = mmr = 0.0

    return {
        "imr": imr,
        "mmr": mmr,
        "delta_contrib": delta_contrib,
        "mark_price": mark,
        "notional": notional,
    }


def spot_balance_margin(balances: list, market_data: dict) -> float:
    """Non-USDC spot token balances charged at 100% USD value."""
    sbm = 0.0
    for b in balances:
        token = b.get("token", "")
        if token == "USDC":
            continue
        amt = abs(float(b.get("size") or 0))
        md = (market_data.get(f"{token}-USD-PERP") or
              market_data.get(f"{token}-USD") or {})
        price = float(md.get("mark_price") or md.get("underlying_price") or 0)
        sbm += amt * price
    return sbm


def compute_xm(positions: list, orders: list,
               market_data: dict, market_specs: dict,
               balances: list = None) -> dict:
    """
    Compute total cross-margin IMR/MMR for all positions.

    Returns full breakdown including per-position detail and portfolio delta.
    """
    spot_bm = spot_balance_margin(balances or [], market_data)
    total_imr = spot_bm
    total_mmr = spot_bm
    port_delta = 0.0
    position_detail = []

    for pos in positions:
        r = xm_position(pos, market_data, market_specs)
        total_imr += r["imr"]
        total_mmr += r["mmr"]
        port_delta += r["delta_contrib"]
        position_detail.append({**pos, **r})

    return {
        "IMR": total_imr,
        "MMR": total_mmr,
        "portfolio_delta": port_delta,
        "spot_balance_margin": spot_bm,
        "positions": position_detail,
    }


# ── PM pipeline (4-step scenario scan) ────────────────────────────────────

def _live_frac(expiry: datetime, now: datetime) -> float:
    """
    TWAP settlement scaling factor.

    During the final TWAP_SETTLEMENT_MIN minutes before expiry, PnL is scaled
    from 1.0 (full) down toward 0 as the option approaches settlement.
    Returns 1.0 outside the TWAP window.
    """
    ste = (expiry - now).total_seconds()
    tw  = TWAP_SETTLEMENT_MIN * 60
    if ste <= 0 or ste > tw:
        return 1.0
    return ste / tw


def _scenario_price(symbol: str, market_data: dict, spot: float,
                    basis: float, ss: float, vs: float,
                    now: datetime = None, *,
                    interest_rate: float = 0.0,
                    dte_floor: float = DTE_FLOOR,
                    vp_short: float = VEGA_POWER_ST,
                    vp_long: float = VEGA_POWER_LT,
                    min_vol_shock_up: float = MIN_VOL_SHOCK_UP) -> float:
    """Reprice a single instrument under a spot+vol scenario."""
    if now is None:
        now = datetime.now(timezone.utc)
    md = market_data.get(symbol, {})
    p  = parse_market(symbol)
    if not p:
        return float(md.get("mark_price") or 0)

    s_shock = spot * (1 + ss)

    if p["type"] == "perp":
        return s_shock * (1 + basis)

    if p["type"] == "dated_option":
        exp = p.get("expiry")
        if not exp:
            return float(md.get("mark_price") or 0)
        dte = (exp - now).total_seconds() / 86400
        tte = dte / YEAR_IN_DAYS
        iv  = float(md.get("mark_iv") or 0)
        vp  = vp_short if dte < 30 else vp_long
        mult   = (30 / max(dte_floor, dte)) ** vp
        iv_shocked = iv * (1 + vs * mult)
        # Upward vol shocks have a floor from vol_shock_params.min_vol_shock_up (spec §2.3)
        if vs > 0:
            iv_shocked = max(iv_shocked, min_vol_shock_up)
        r = interest_rate
        return bs_price(s_shock, p["strike"], tte, r, iv_shocked, p["is_call"])

    # Perp option or unknown — no repricing
    return float(md.get("mark_price") or 0)


def _fee_provision(sym: str, size: float, market_data: dict,
                   market_specs: dict) -> float:
    """
    Fee provision for one instrument (spec §8.2).

    Non-option: HFR × size × mark_price
    Option:     min(HFR × spot_price, 0.125 × mark_price) × size
    """
    md      = market_data.get(sym, {})
    hfr     = float(md.get("fee_rate") or 0)
    if not hfr or not size:
        return 0.0
    mark    = float(md.get("mark_price") or 0)
    spec    = market_specs.get(sym, {})
    ak      = spec.get("asset_kind", "")
    if ak in ("OPTION", "PERP_OPTION"):
        spot = float(md.get("underlying_price") or mark)
        OPTION_FEE_CAP = 0.125
        return min(hfr * spot, OPTION_FEE_CAP * mark) * size
    return hfr * size * mark


def compute_pm(positions: list, orders: list,
               market_data: dict, market_specs: dict,
               balances: list = None,
               now: datetime = None,
               pm_config: dict = None) -> dict:
    """
    Compute Portfolio Margin IMR/MMR via the 4-step scenario scan.

    Mirrors _recalc() in the PM Calculator web app.

    pm_config: optional dict from paradex_system_config().portfolio_margin[asset].
               Keys: hedged_margin_factor, unhedged_margin_factor, mmf_factor,
               scenarios (list of {spot_shock, vol_shock, weight}), vol_shock_params.
               Falls back to module-level constants when absent.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Resolve effective constants from pm_config, falling back to module defaults
    cfg = pm_config or {}
    vsp = cfg.get("vol_shock_params") or {}
    dte_floor_eff     = float(vsp.get("dte_floor_days")       or DTE_FLOOR)
    vp_st_eff         = float(vsp.get("vega_power_short_dte") or VEGA_POWER_ST)
    vp_lt_eff         = float(vsp.get("vega_power_long_dte")  or VEGA_POWER_LT)
    min_vol_shock_up  = float(vsp.get("min_vol_shock_up")     or MIN_VOL_SHOCK_UP)
    hedged_mf         = float(cfg.get("hedged_margin_factor")   or HEDGED_MF)
    unhedged_mf    = float(cfg.get("unhedged_margin_factor") or UNHEDGED_MF)
    mmr_factor_eff = float(cfg.get("mmf_factor")             or MMR_FACTOR)
    raw_sc = cfg.get("scenarios")
    scenarios_eff = (
        [[s["spot_shock"], s["vol_shock"], s["weight"]] for s in raw_sc]
        if raw_sc else SCENARIOS
    )
    weights_eff = [s[2] for s in scenarios_eff]
    n_sc_eff    = len(scenarios_eff)

    spot_bm = spot_balance_margin(balances or [], market_data)

    # Derive spot/basis/funding from the PM underlying perp.
    # Detect underlying dynamically: look for a -PERP in the portfolio, else fall back to BTC.
    ul_sym = "BTC-USD-PERP"
    for p in positions:
        sym = p["market"]
        if sym.endswith("-PERP"):
            ul_sym = sym
            break
        parsed = parse_market(sym)
        if parsed and parsed["type"] == "dated_option":
            # e.g. BTC-USD-8MAY26-78000-C → BTC-USD-PERP
            parts = sym.split("-")
            ul_sym = f"{parts[0]}-{parts[1]}-PERP"
            break

    perp_md = market_data.get(ul_sym, {})
    spot    = float(perp_md.get("underlying_price") or 0)
    perp_mk = float(perp_md.get("mark_price") or spot)
    basis   = (perp_mk - spot) / spot if spot else 0
    fr8h    = float(perp_md.get("funding_rate") or 0)
    # Interest rate derived from funding rate (matches HTML calculator)
    interest_rate = float(perp_md.get("interest_rate") or 0)

    all_markets = {p["market"] for p in positions} | {o["market"] for o in orders}

    # Precompute scenario prices
    sc_prices = {
        sym: [_scenario_price(sym, market_data, spot, basis, ss, vs, now,
                              interest_rate=interest_rate,
                              dte_floor=dte_floor_eff, vp_short=vp_st_eff, vp_long=vp_lt_eff,
                              min_vol_shock_up=min_vol_shock_up)
              for (ss, vs, _) in scenarios_eff]
        for sym in all_markets
    }

    # ── Step 1: Scenario scan ──────────────────────────────────────────────
    pos_pnls   = [0.0] * n_sc_eff
    pos_deltas = []
    for pos in positions:
        sym  = pos["market"]
        md   = market_data.get(sym, {})
        mark = float(md.get("mark_price") or 0)
        delta = float(md.get("delta") or 0)
        size  = abs(float(pos["size"]))
        signed = size if pos["side"] in ("BUY", "LONG") else -size
        sc = sc_prices.get(sym, [mark] * n_sc_eff)
        # TWAP settlement: scale PnL toward zero within 30 min of expiry
        parsed = parse_market(sym)
        exp    = parsed.get("expiry") if parsed else None
        lf     = _live_frac(exp, now) if exp else 1.0
        for i in range(n_sc_eff):
            pos_pnls[i] += lf * (sc[i] - mark) * weights_eff[i] * signed
        pos_deltas.append(delta * signed)

    ord_pnls   = [0.0] * n_sc_eff
    ord_deltas = []
    for o in orders:
        sym   = o["market"]
        md    = market_data.get(sym, {})
        delta = float(md.get("delta") or 0)
        size  = float(o.get("size") or 0)
        price = float(o.get("price") or 0)
        is_buy = o["side"] == "BUY"
        sc = sc_prices.get(sym, [price] * n_sc_eff)
        parsed = parse_market(sym)
        exp    = parsed.get("expiry") if parsed else None
        lf     = _live_frac(exp, now) if exp else 1.0
        for i in range(n_sc_eff):
            gap = (price - sc[i]) if is_buy else (sc[i] - price)
            ord_pnls[i] += -size * lf * max(0, gap) * weights_eff[i]
        ord_deltas.append(delta * size * (1 if is_buy else -1))

    total_pnls  = [pos_pnls[i] + ord_pnls[i] for i in range(n_sc_eff)]
    losses      = [max(0.0, -p) for p in total_pnls]
    worst_loss  = max(losses) if losses else 0.0
    worst_idx   = losses.index(worst_loss) if losses else 0

    # ── Step 2: Delta-min floor ────────────────────────────────────────────
    mL  = sum(d for d in pos_deltas if d > 0)
    mS  = sum(abs(d) for d in pos_deltas if d < 0)
    loO = sum(d for d in ord_deltas if d > 0)
    soO = sum(abs(d) for d in ord_deltas if d < 0)
    maxL   = mL + loO
    maxS   = mS + soO
    maxU   = max(0.0, max(maxL - mS, maxS - mL))
    hedged = max(0.0, max(maxL, maxS) - maxU)  # equivalent to min(maxL, maxS)
    delta_min = (hedged * hedged_mf + maxU * unhedged_mf) * spot

    # ── Step 3: Funding provision ──────────────────────────────────────────
    # Net positions + orders together before applying max(0) (matches Go engine)
    pos_fund_sum = sum(
        -fr8h * (abs(float(p["size"])) * (1 if p["side"] in ("BUY","LONG") else -1)) * spot
        for p in positions if p["market"].endswith("-PERP")
    )
    ord_fund_sum = sum(
        fr8h * float(o.get("size") or 0) * (1 if o["side"] == "BUY" else -1) * spot
        for o in orders if o["market"].endswith("-PERP")
    )
    total_funding = pos_fund_sum + ord_fund_sum
    fund_p = max(0.0, -total_funding)   # IMR: positions + orders combined
    pF     = max(0.0, -pos_fund_sum)    # MMR: positions only

    # ── Step 4: Fee provision (spec §8.2) ──────────────────────────────────
    fee_pos = sum(_fee_provision(p["market"], abs(float(p["size"])), market_data, market_specs)
                  for p in positions)
    fee_ord = sum(_fee_provision(o["market"], float(o.get("size") or 0), market_data, market_specs)
                  for o in orders)
    fee_imr = fee_pos + fee_ord   # IMR: positions + orders
    fee_mmr = fee_pos             # MMR: positions only

    # ── Step 4: IMR & MMR ──────────────────────────────────────────────────
    net_im = max(worst_loss, delta_min)
    IMR    = net_im + fund_p + fee_imr + spot_bm

    # MMR: positions-only
    pos_losses = [max(0.0, -p) for p in pos_pnls]
    pos_worst  = max(pos_losses) if pos_losses else 0.0
    p_nd = sum(pos_deltas)
    p_gd = sum(abs(d) for d in pos_deltas)
    pH   = (p_gd - abs(p_nd)) / 2
    p_dm = (unhedged_mf * abs(p_nd) + hedged_mf * pH) * spot
    pos_ni = max(pos_worst, p_dm)
    MMR    = pos_ni * mmr_factor_eff + pF + fee_mmr + spot_bm

    return {
        "IMR": IMR,
        "MMR": MMR,
        "portfolio_delta": sum(pos_deltas),
        "spot_balance_margin": spot_bm,
        # Step detail
        "worst_loss":  worst_loss,
        "worst_idx":   worst_idx,
        "worst_scenario": scenarios_eff[worst_idx],
        "delta_min":   delta_min,
        "fund_p":      fund_p,
        "fee_provision_imr": fee_imr,
        "fee_provision_mmr": fee_mmr,
        "maxL": maxL, "maxS": maxS, "maxU": maxU, "hedged": hedged,
        "spot": spot,
    }


# ── Unified entry point ────────────────────────────────────────────────────

def compute(positions: list, orders: list,
            market_data: dict, market_specs: dict,
            margin_methodology: str = "cross_margin",
            balances: list = None,
            pm_config: dict = None) -> dict:
    """
    Compute IMR/MMR using the correct methodology.

    Args:
        positions:           list of {market, side, size}
        orders:              list of {market, side, size, price}
        market_data:         dict[symbol → {mark_price, delta, mark_iv, ...}]
        market_specs:        dict[symbol → {asset_kind, delta1_cross_margin_params, ...}]
        margin_methodology:  "cross_margin" or "portfolio_margin"
        balances:            list of {token, size}  (optional)
        pm_config:           live PM config from paradex_system_config().portfolio_margin
                             (optional; falls back to module-level hardcoded defaults)

    Returns:
        dict with IMR, MMR, portfolio_delta, and methodology-specific detail
    """
    if margin_methodology == "portfolio_margin":
        result = compute_pm(positions, orders, market_data, market_specs,
                            balances=balances, pm_config=pm_config)
    else:
        result = compute_xm(positions, orders, market_data, market_specs, balances)

    result["margin_methodology"] = margin_methodology
    return result


# ── Delta hedge size ───────────────────────────────────────────────────────

def delta_hedge_size(portfolio_delta: float, instrument_delta: float,
                     size_increment: float = 0.00001) -> tuple[str, float]:
    """
    Compute the side and size needed to neutralise portfolio delta.

    Logic: adding (side_sign × size × instrument_delta) to portfolio_delta
    should equal zero.
      side_sign × size × instrument_delta = -portfolio_delta
      size = -portfolio_delta / (side_sign × instrument_delta)

    Positive portfolio_delta → need to SELL (side_sign = -1) to reduce it.
    Negative portfolio_delta → need to BUY  (side_sign = +1) to increase it.

    Returns:
        (side, size) where side is "BUY" or "SELL" and size > 0,
        or ("NONE", 0) if delta is already ~zero.
    """
    if abs(portfolio_delta) < size_increment:
        return "NONE", 0.0

    # BUY adds delta (sign=+1), SELL subtracts (sign=-1)
    for side, sign in [("BUY", 1), ("SELL", -1)]:
        neutral = -portfolio_delta / (sign * instrument_delta)
        if neutral > 0:
            # Round down, guard float precision with round()
            steps = math.floor(round(neutral / size_increment, 8))
            size  = round(steps * size_increment, 8)
            return side, size

    return "NONE", 0.0
