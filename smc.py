"""Deterministic Smart-Money-Concepts detection on plain OHLCV candles.

Candle = {"t": epoch_ms, "o": float, "h": float, "l": float, "c": float, "v": float}

The LLM is bad at reading raw candles, so all the geometry (swings, BOS/CHoCH,
FVG, IFVG, liquidity, order blocks...) is computed here, and GPT only interprets
the result.
"""
from datetime import datetime, timezone


def _r(x):
    return float(f"{x:.8g}")


def atr(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else 0.0


def _pos(lo, hi, price):
    return "above_price" if lo > price else "below_price" if hi < price else "price_inside"


# --------------------------------------------------------------- swings ----
def swings(candles, k=3):
    """Fractal pivots: a high/low that beats k candles on each side."""
    out = []
    for i in range(k, len(candles) - k):
        rng = [j for j in range(i - k, i + k + 1) if j != i]
        if all(candles[i]["h"] > candles[j]["h"] for j in rng):
            out.append({"i": i, "type": "high", "price": candles[i]["h"], "conf": i + k})
        if all(candles[i]["l"] < candles[j]["l"] for j in rng):
            out.append({"i": i, "type": "low", "price": candles[i]["l"], "conf": i + k})
    return sorted(out, key=lambda s: s["conf"])


# ------------------------------------------------------------ structure ----
def structure(candles, sw):
    """BOS = close through a swing in trend direction, CHoCH = close through it against trend."""
    events, trend, hi, lo, p = [], None, None, None, 0
    for k, c in enumerate(candles):
        while p < len(sw) and sw[p]["conf"] <= k:
            if sw[p]["type"] == "high":
                hi = sw[p]
            else:
                lo = sw[p]
            p += 1
        if hi and c["c"] > hi["price"]:
            events.append({"i": k, "kind": "CHoCH" if trend == "bear" else "BOS", "dir": "bull", "level": hi["price"]})
            trend, hi = "bull", None
        elif lo and c["c"] < lo["price"]:
            events.append({"i": k, "kind": "CHoCH" if trend == "bull" else "BOS", "dir": "bear", "level": lo["price"]})
            trend, lo = "bear", None
    return events, trend


# ------------------------------------------------------------ FVG / IFVG ----
def fvgs(candles, a):
    """Returns (fair value gaps, inverse fair value gaps)."""
    n, min_gap = len(candles), 0.1 * a
    gaps, inverses = [], []
    for i in range(2, n):
        c0, c2 = candles[i - 2], candles[i]
        if c2["l"] - c0["h"] > min_gap:
            d, lo, hi = "bull", c0["h"], c2["l"]
        elif c0["l"] - c2["h"] > min_gap:
            d, lo, hi = "bear", c2["h"], c0["l"]
        else:
            continue
        status, inv_i = "open", None
        for j in range(i + 1, n):
            c = candles[j]
            if d == "bull":
                if c["c"] < lo:
                    inv_i = j
                    break
                if c["l"] <= lo:
                    status = "filled"
                elif c["l"] < hi and status == "open":
                    status = "partial"
            else:
                if c["c"] > hi:
                    inv_i = j
                    break
                if c["h"] >= hi:
                    status = "filled"
                elif c["h"] > lo and status == "open":
                    status = "partial"
        if inv_i is None:
            gaps.append({"dir": d, "lo": lo, "hi": hi, "i": i, "status": status})
            continue
        # gap was closed through -> polarity flips (bull FVG becomes bearish IFVG, and vice versa)
        nd = "bear" if d == "bull" else "bull"
        state, retested = "active", False
        for m in range(inv_i + 1, n):
            c = candles[m]
            if (nd == "bear" and c["c"] > hi) or (nd == "bull" and c["c"] < lo):
                state = "failed"
                break
            if (nd == "bear" and c["h"] >= lo) or (nd == "bull" and c["l"] <= hi):
                retested = True
        inverses.append({"dir": nd, "lo": lo, "hi": hi, "i": inv_i, "status": state, "retested": retested})
    return gaps, inverses


# ------------------------------------------------------------- liquidity ----
def liquidity(candles, sw, a):
    n, price, tol = len(candles), candles[-1]["c"], 0.25 * a
    bsl, ssl, sweeps = [], [], []
    for kind in ("high", "low"):
        clusters = []
        for s in (x for x in sw if x["type"] == kind):
            for cl in clusters:
                if abs(cl["level"] - s["price"]) <= tol:
                    cl["m"].append(s)
                    break
            else:
                clusters.append({"level": s["price"], "m": [s]})
        for cl in clusters:
            level = max(m["price"] for m in cl["m"]) if kind == "high" else min(m["price"] for m in cl["m"])
            status, at = "unswept", None
            for k in range(max(m["conf"] for m in cl["m"]), n):
                c = candles[k]
                beyond = c["h"] > level if kind == "high" else c["l"] < level
                closed = c["c"] > level if kind == "high" else c["c"] < level
                if closed:
                    status, at = "broken", k
                    break
                if beyond:
                    status, at = "swept", k
                    break
            item = {"level": _r(level), "equal": len(cl["m"]) >= 2, "touches": len(cl["m"])}
            if status == "unswept":
                if kind == "high" and level > price:
                    bsl.append(item)
                if kind == "low" and level < price:
                    ssl.append(item)
            elif status == "swept" and n - 1 - at <= 30:
                sweeps.append({"side": "buy_side" if kind == "high" else "sell_side", "level": _r(level), "bars_ago": n - 1 - at})
    bsl.sort(key=lambda x: x["level"] - price)
    ssl.sort(key=lambda x: price - x["level"])
    return {"buy_side_above": bsl[:4], "sell_side_below": ssl[:4], "recent_sweeps": sweeps[-4:]}


def daily_levels(candles, tf_minutes):
    if tf_minutes >= 1440:
        return {}
    days = {}
    for c in candles:
        days.setdefault(datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc).date(), []).append(c)
    keys = sorted(days)
    if len(keys) < 2:
        return {}
    prev, cur = days[keys[-2]], days[keys[-1]]
    pdh, pdl = max(c["h"] for c in prev), min(c["l"] for c in prev)
    th, tl = max(c["h"] for c in cur), min(c["l"] for c in cur)
    return {"PDH": _r(pdh), "PDL": _r(pdl), "today_high": _r(th), "today_low": _r(tl),
            "PDH_swept_today": th > pdh, "PDL_swept_today": tl < pdl}


# ---------------------------------------------------------- order blocks ----
def order_blocks(candles, events):
    """Last opposite candle before the impulse that produced a BOS/CHoCH."""
    n, out = len(candles), []
    for e in events[-8:]:
        k, j = e["i"], e["i"]
        want_bull = e["dir"] == "bull"
        steps = 0
        while j > 0 and steps < 10 and ((candles[j]["c"] > candles[j]["o"]) == want_bull):
            j -= 1
            steps += 1
        c = candles[j]
        if (c["c"] < c["o"]) != want_bull:
            continue
        lo, hi = c["l"], c["h"]
        broken = any((x["c"] < lo) if want_bull else (x["c"] > hi) for x in candles[k + 1:])
        if not broken:
            tested = any((x["l"] <= hi) if want_bull else (x["h"] >= lo) for x in candles[k + 1:])
            out.append({"dir": e["dir"], "lo": lo, "hi": hi, "i": j, "tested": tested})
    return out


# --------------------------------------------------------------- analyze ----
def analyze(candles, tf_minutes=15):
    n, price = len(candles), candles[-1]["c"]
    a = atr(candles)
    sw = swings(candles)
    events, trend = structure(candles, sw)
    gaps, inverses = fvgs(candles, a)

    def zone(z):
        return {"dir": z["dir"], "low": _r(z["lo"]), "high": _r(z["hi"]), "bars_ago": n - 1 - z["i"],
                "vs_price": _pos(z["lo"], z["hi"], price),
                **({"status": z["status"]} if "status" in z else {}),
                **({"retested": z["retested"]} if "retested" in z else {})}

    active_gaps = sorted((g for g in gaps if g["status"] in ("open", "partial")),
                         key=lambda g: abs((g["lo"] + g["hi"]) / 2 - price))[:6]
    active_ifvg = [z for z in inverses if z["status"] == "active"][-4:]
    obs = order_blocks(candles, events)[-4:]

    sh = next((s for s in reversed(sw) if s["type"] == "high"), None)
    sl = next((s for s in reversed(sw) if s["type"] == "low"), None)
    pd = None
    if sh and sl and sh["price"] > sl["price"]:
        pct = (price - sl["price"]) / (sh["price"] - sl["price"]) * 100
        pd = {"range_high": _r(sh["price"]), "range_low": _r(sl["price"]),
              "equilibrium": _r((sh["price"] + sl["price"]) / 2), "price_pct_of_range": round(pct, 1),
              "zone": "premium" if pct > 55 else "discount" if pct < 45 else "equilibrium"}

    last = candles[-1]
    return {
        "price": _r(price), "atr14": _r(a), "candles_analyzed": n,
        "last_candle": {"o": _r(last["o"]), "h": _r(last["h"]), "l": _r(last["l"]), "c": _r(last["c"])},
        "market_structure_trend": trend,
        "recent_structure_events": [{"kind": e["kind"], "dir": e["dir"], "level": _r(e["level"]), "bars_ago": n - 1 - e["i"]} for e in events[-5:]],
        "recent_swing_highs": [{"price": _r(s["price"]), "bars_ago": n - 1 - s["i"]} for s in sw if s["type"] == "high"][-3:],
        "recent_swing_lows": [{"price": _r(s["price"]), "bars_ago": n - 1 - s["i"]} for s in sw if s["type"] == "low"][-3:],
        "fair_value_gaps_unfilled": [zone(g) for g in active_gaps],
        "inverse_fvgs_active": [zone(z) for z in active_ifvg],
        "liquidity": liquidity(candles, sw, a),
        "daily_levels": daily_levels(candles, tf_minutes),
        "order_blocks_unbroken": [zone(o) | {"tested": o["tested"]} for o in obs],
        "premium_discount": pd,
    }
