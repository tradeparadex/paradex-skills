"""
vol_math.py — deterministic vol calculations for the options-recap skill.

Single source of truth for the math the agent must NOT do by mental arithmetic:
realized volatility and Black-76 flow greeks. Both the production CLI
(paradex_options_recap.py) and the eval fixture generator (../evals/
generate_fixture.py) import from here, so the formula never forks.

Pure functions, no I/O, no network — unit-tested in test_vol_math.py.
"""

import math
from collections import defaultdict

# Crypto trades 24/7, so the calendar annualization factor is √(24×365).
HOURS_PER_YEAR = 24 * 365  # 8760
RV_LOOKBACK_DAYS = 7        # paired with DVOL's 30-day implied; desk standard

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


# ── Realized vol (#1) ──────────────────────────────────────────────────────

def compute_realized_vol(closes: list[float]) -> dict:
    """Close-to-close realized vol from hourly closes, annualized (24/7).

    Returns annualized vol in vol points (%). Realized-vs-implied is a slow
    statistic, so callers should pass a fixed multi-day lookback (see
    RV_LOOKBACK_DAYS), not a short recap window.
    """
    if not closes or len(closes) < 3:
        return {"annualized_vol": None, "candles": len(closes or [])}
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)   # sample variance
    sd_hourly = math.sqrt(var)
    rv = sd_hourly * math.sqrt(HOURS_PER_YEAR) * 100
    return {
        "annualized_vol": round(rv, 1),
        "candles": len(closes),
        "lookback_days": RV_LOOKBACK_DAYS,
        "method": "close-to-close log returns · sample stdev · ×√8760 (24/7) · ×100",
    }


def realized_vs_implied(closes: list[float], dvol_close: float | None) -> dict:
    """Realized vol + the vol risk premium read (implied − realized)."""
    rv = compute_realized_vol(closes)
    value = rv["annualized_vol"]
    vrp = None
    label = None
    if value is not None and dvol_close is not None:
        vrp = round(dvol_close - value, 1)
        if vrp > 1:
            label = "implied rich vs realized — vol overpriced vs delivered"
        elif vrp < -1:
            label = "implied cheap vs realized — vol underpriced vs delivered"
        else:
            label = "implied roughly in line with realized"
    return {
        "value": value,
        "lookback_days": rv.get("lookback_days"),
        "vrp": vrp,
        "vrp_label": label,
    }


# ── Flow greeks (#2) ───────────────────────────────────────────────────────

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def expiry_ms_from_instrument(inst: str) -> int | None:
    """Parse Deribit expiry (08:00 UTC) from an instrument name like
    BTC-26JUN26-55000-P → 2026-06-26T08:00Z in ms."""
    from datetime import datetime, timezone
    try:
        token = inst.split("-")[1]            # e.g. 26JUN26 or 5JUN26
        day = int(token[:-5])
        mon = _MONTHS[token[-5:-2]]
        yr = 2000 + int(token[-2:])
        return int(datetime(yr, mon, day, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        return None


def black76_greeks(F: float, K: float, T_years: float, iv_pct: float) -> dict:
    """Approximate per-contract vega and dollar-gamma via Black-76 (r=0,
    options on the index). Used to weight option flow by its vol / convexity
    exposure so a multi-leg structure nets correctly across tenors and strikes.

    - vega: USD change in option value per 1 vol-point (1%) move, per 1 BTC contract.
    - dollar_gamma: USD delta change per 1% spot move, per 1 BTC contract.

    Directional SIGN and RELATIVE magnitude across legs are robust; the
    absolute USD figure is approximate (ignores inverse-settlement nuance).
    """
    sigma = iv_pct / 100.0
    if T_years <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return {"vega": 0.0, "dollar_gamma": 0.0}
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T_years) / (sigma * math.sqrt(T_years))
    pdf = _norm_pdf(d1)
    vega = F * pdf * math.sqrt(T_years) / 100.0       # per 1 vol point, per contract
    gamma = pdf / (F * sigma * math.sqrt(T_years))    # ∂²price/∂F²
    dollar_gamma = gamma * F * F * 0.01               # per 1% spot move
    return {"vega": vega, "dollar_gamma": dollar_gamma}


def compute_flow_greeks(clusters: dict[str, list]) -> dict:
    """Aggregate signed vega / gamma across all block legs to read net
    customer (and therefore dealer) positioning.

    `clusters` maps block_trade_id → list of legs; each leg needs
    instrument_name, index_price, iv, timestamp, direction, amount.

    A customer who BUYS an option is long vega/gamma (sign +1); selling is -1.
    Dealers take the other side, so dealer exposure = −customer exposure.
    """
    net_vega = 0.0          # net CUSTOMER vega ($/vol-pt)
    net_dgamma = 0.0        # net CUSTOMER dollar-gamma ($/1% move)
    gross_vega = 0.0
    for legs in clusters.values():
        for leg in legs:
            parts = leg["instrument_name"].split("-")
            if len(parts) < 4:
                continue
            K = int(parts[2])
            F = leg.get("index_price")
            iv = leg.get("iv")
            exp_ms = expiry_ms_from_instrument(leg["instrument_name"])
            if not (F and iv and exp_ms):
                continue
            T = (exp_ms - leg["timestamp"]) / (HOURS_PER_YEAR * 3600_000)
            g = black76_greeks(F, K, T, iv)
            sign = 1.0 if leg["direction"] == "buy" else -1.0
            qty = leg["amount"]
            net_vega += sign * qty * g["vega"]
            net_dgamma += sign * qty * g["dollar_gamma"]
            gross_vega += qty * g["vega"]

    # Dealer is the opposite side of customer flow.
    dealer_vega = -net_vega
    dealer_dgamma = -net_dgamma

    # "Balanced" when net is small relative to gross vega traded.
    balanced = gross_vega > 0 and abs(net_vega) / gross_vega < 0.2

    if balanced:
        label = "two-way / vega-balanced — no decisive net positioning"
    else:
        vega_dir = "short vega (vulnerable to a vol spike)" if dealer_vega < 0 else "long vega"
        gamma_dir = ("short gamma → chase spot, amplify moves"
                     if dealer_dgamma < 0 else "long gamma → dampen, expect pinning")
        label = f"dealers {vega_dir}; {gamma_dir}"

    return {
        "net_customer_vega": round(net_vega),
        "net_customer_dollar_gamma": round(net_dgamma),
        "dealer_vega": round(dealer_vega),
        "dealer_dollar_gamma": round(dealer_dgamma),
        "gross_vega": round(gross_vega),
        "balanced": balanced,
        "positioning_label": label,
    }


def cluster_blocks(trades: list[dict]) -> dict[str, list]:
    """Group trades by block_trade_id (legs of the same block)."""
    clusters: dict[str, list] = defaultdict(list)
    for t in trades:
        bid = t.get("block_trade_id")
        if bid:
            clusters[bid].append(t)
    return dict(clusters)


# ── Block structures (#2b) ─────────────────────────────────────────────────

def classify_structure(legs: list[dict]) -> str:
    """Name a block cluster's structure from its leg instruments.

    same expiry + C&P + same strike → Straddle; C&P + diff strikes →
    Strangle/RR; same type + diff strikes → Spread; diff expiries + same
    strike → Calendar; ≥3 legs → Butterfly/Condor."""
    if len(legs) == 1:
        return "Call" if legs[0]["instrument_name"].endswith("-C") else "Put"
    expiries, strikes, types = set(), set(), set()
    for leg in legs:
        parts = leg["instrument_name"].split("-")
        expiries.add(parts[1])
        strikes.add(int(parts[2]))
        types.add(parts[3])
    if len(expiries) > 1 and len(strikes) == 1:
        return "Calendar"
    if len(expiries) == 1:
        if types == {"C", "P"} and len(strikes) == 1:
            return "Straddle"
        if types == {"C", "P"} and len(strikes) > 1:
            return "Strangle/RR"
        if len(types) == 1 and len(strikes) > 1:
            return "Spread"
        if len(legs) >= 3:
            return "Butterfly/Condor"
    return "Multi-leg"


def dominant_side(legs: list[dict]) -> str:
    """Buy, Sell, or Two-way for a block cluster (by leg direction count)."""
    buys = sum(1 for l in legs if l["direction"] == "buy")
    sells = len(legs) - buys
    if buys == 0:
        return "Sell"
    if sells == 0:
        return "Buy"
    return "Two-way"


def summarize_blocks(clusters: dict[str, list], top_n: int = 8,
                     min_btc: float = 10.0) -> list[dict]:
    """Rank block clusters by USD notional and describe each.

    Identifying the largest block and its structure is deterministic (cluster
    by block_trade_id, sum size × index, classify legs) — exactly the read an
    LLM gets wrong by eyeballing raw tape (mis-ranking, hallucinated notional),
    so it belongs here next to the vol math. Returns the top `top_n` clusters
    of at least `min_btc` total size, largest notional first.
    """
    from datetime import datetime, timezone
    rows = []
    for bid, legs in clusters.items():
        if not legs:
            continue
        total_btc = sum(l.get("amount", 0) for l in legs)
        index_price = legs[0].get("index_price") or 0
        ivs = [l["iv"] for l in legs if l.get("iv") is not None]
        ts = min(l["timestamp"] for l in legs)
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        parts = legs[0]["instrument_name"].split("-")
        rows.append({
            "block_trade_id": bid,
            "time_utc": dt.strftime("%H:%M"),
            "structure": classify_structure(legs),
            "size_btc": round(total_btc, 1),
            "notional_usd": round(total_btc * index_price),
            "side": dominant_side(legs),
            "avg_iv": round(sum(ivs) / len(ivs), 1) if ivs else None,
            "expiry": parts[1] if len(parts) > 1 else None,
            "strike": parts[2] if len(parts) > 2 else None,
            "leg_count": len(legs),
        })
    rows.sort(key=lambda r: r["notional_usd"], reverse=True)
    return [r for r in rows if r["size_btc"] >= min_btc][:top_n]


# ── Vol surface (#3) ───────────────────────────────────────────────────────

def _interp(points: list[tuple], x: float) -> tuple:
    """Linear interpolate y at x over points = sorted [(x_i, y_i)] ascending.
    Returns (y, extrapolated) — extrapolated=True when x is outside the
    observed range and the nearest endpoint was clamped to."""
    if not points:
        return None, True
    if x <= points[0][0]:
        return points[0][1], x < points[0][0]
    if x >= points[-1][0]:
        return points[-1][1], x > points[-1][0]
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0, False
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0), False
    return points[-1][1], True


def _call_delta(inst: str, delta: float) -> float | None:
    """Normalize a leg's delta to the CALL delta for its strike.
    Put delta = call delta − 1, so call delta = put delta + 1."""
    if delta is None:
        return None
    if inst.endswith("-C"):
        return delta
    if inst.endswith("-P"):
        return delta + 1.0
    return None


def compute_vol_surface(tickers: dict[str, dict], spot: float | None = None) -> dict:
    """Derive per-expiry ATM IV, 25-delta risk reversal (skew), 25-delta
    butterfly (wings), and the cross-expiry term-structure read from raw
    per-strike tickers (each carrying `mark_iv` and `delta`).

    Interpolates IV against call-delta: 25Δ call = delta 0.25, 25Δ put =
    delta 0.75 (same strike, put delta −0.25), ATM = 0.50. Metrics whose
    target delta falls outside the strike range are flagged `extrapolated`.
    """
    # Build per-expiry { call_delta: iv } (call & put at a strike share mark_iv).
    by_exp: dict[str, dict[float, float]] = defaultdict(dict)
    exp_ms: dict[str, int | None] = {}
    for name, d in tickers.items():
        iv = d.get("mark_iv")
        cd = _call_delta(name, d.get("delta"))
        if iv is None or cd is None:
            continue
        exp = name.split("-")[1]
        by_exp[exp][round(cd, 6)] = iv
        exp_ms.setdefault(exp, expiry_ms_from_instrument(name))

    expiries = []
    for exp, dmap in by_exp.items():
        pts = sorted(dmap.items())  # [(call_delta, iv)] ascending
        atm, atm_ex = _interp(pts, 0.50)
        c25, c25_ex = _interp(pts, 0.25)   # 25Δ call (OTM call)
        p25, p25_ex = _interp(pts, 0.75)   # 25Δ put  (OTM put)
        rr = fly = None
        if c25 is not None and p25 is not None:
            rr = round(c25 - p25, 1)                       # >0 calls bid, <0 puts bid
        if c25 is not None and p25 is not None and atm is not None:
            fly = round((c25 + p25) / 2 - atm, 1)          # >0 wings bid
        expiries.append({
            "expiry": exp,
            "expiry_ms": exp_ms.get(exp),
            "atm_iv": round(atm, 1) if atm is not None else None,
            "rr_25d": rr,
            "fly_25d": fly,
            "wings_extrapolated": bool(c25_ex or p25_ex),
        })

    # Chronological order (unknown expiry_ms sorts last).
    expiries.sort(key=lambda e: (e["expiry_ms"] is None, e["expiry_ms"] or 0))

    front = expiries[0] if expiries else None
    back = expiries[1] if len(expiries) > 1 else None
    front_atm = front["atm_iv"] if front else None
    back_atm = back["atm_iv"] if back else None

    term = None
    if front_atm is not None and back_atm is not None:
        diff = front_atm - back_atm
        if diff > 1:
            term = "backwardation (front > back) — near-term stress bid"
        elif diff < -1:
            term = "contango (back > front) — normal upward term structure"
        else:
            term = "flat term structure"

    skew = None
    if front and front["rr_25d"] is not None:
        rr = front["rr_25d"]
        if rr < -0.5:
            side = "puts bid, downside skew"
        elif rr > 0.5:
            side = "calls bid, upside skew"
        else:
            side = "skew roughly symmetric"
        flag = " (extrapolated — wings outside strike range)" if front["wings_extrapolated"] else ""
        skew = f"front {front['expiry']} 25Δ RR {rr:+}v → {side}{flag}"

    return {
        "spot": spot,
        "expiries": expiries,
        "front_atm": front_atm,
        "back_atm": back_atm,
        "term_structure": term,
        "skew_label": skew,
    }
