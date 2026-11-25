#!/usr/bin/env python3
"""
Kalshi mispricing scanner (read-only, public market data, no API key needed).

Flags three kinds of objective inconsistencies:
  1. BRACKET ARB   - mutually exclusive events where buying YES on every outcome
                     costs < $1 after estimated fees (or selling all YES at the
                     bids collects > $1 after fees).
  2. LADDER        - "above X" threshold markets where a higher strike is priced
                     above a lower strike (P(>X2) can't exceed P(>X1) if X2 > X1).
  3. WIDE SPREAD   - liquid-ish markets with a big bid/ask gap (info only).

Usage:
  pip install requests
  python kalshi_scan.py                 # full scan
  python kalshi_scan.py --min-edge 1    # only show arbs with >= 1c edge per set
  python kalshi_scan.py --csv out.csv

Notes:
  - Prices are top-of-book only; depth may be 1 contract. Check the order book.
  - Fee model is an approximation of Kalshi's published taker fee
    (ceil(0.07 * C * P * (1-P)) per order). Verify against current fee schedule.
  - This is a data tool, not trading advice.
"""
import argparse, csv, math, re, sys, time
import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
MIN_EXHAUSTIVE_SUM = 85   # cents; bracket asks summing below this are treated as non-exhaustive
MAX_PLAUSIBLE_EDGE = 25   # cents; bigger "edges" are almost always data artifacts


def cents(m, side):
    """Return price in cents for yes_bid / yes_ask, handling old and new field formats."""
    v = m.get(side)
    if v is None and m.get(f"{side}_dollars") is not None:
        v = round(float(m[f"{side}_dollars"]) * 100)
    return int(v) if v is not None else None


def fee_cents(p, contracts=1):
    """Approx taker fee in cents for `contracts` at price p (cents)."""
    P = p / 100
    return math.ceil(0.07 * contracts * P * (1 - P) * 100)


def fetch_events():
    events, cursor = [], None
    s = requests.Session()
    while True:
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        r = s.get(f"{BASE}/events", params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        d = r.json()
        events += d.get("events", [])
        cursor = d.get("cursor")
        print(f"\rfetched {len(events)} events", end="", file=sys.stderr)
        if not cursor:
            break
        time.sleep(0.15)
    print(file=sys.stderr)
    return events


def active(markets):
    return [m for m in markets if m.get("status") in (None, "active", "open")]


def scan_brackets(ev, min_edge):
    out = []
    if not ev.get("mutually_exclusive"):
        return out
    ms = active(ev.get("markets", []))
    if len(ms) < 2:
        return out
    asks = [cents(m, "yes_ask") for m in ms]
    bids = [cents(m, "yes_bid") for m in ms]
    # Buy every YES: one must pay $1 (assumes outcomes are exhaustive!)
    # If the asks sum far below $1, the list almost certainly isn't exhaustive
    # (e.g. "Top Netflix show" listing 10 of many shows), so skip it.
    if all(a and 0 < a < 100 for a in asks) and sum(asks) >= MIN_EXHAUSTIVE_SUM:
        cost = sum(asks) + sum(fee_cents(a) for a in asks)
        edge = 100 - cost
        if edge >= min_edge:
            out.append(("BRACKET_BUY_ALL_YES", ev, edge,
                        f"{len(ms)} legs, sum asks={sum(asks)}c, w/ fees={cost}c"))
    # Sell every YES at bids: you pay out $1 on exactly one
    if all(b and 0 < b < 100 for b in bids):
        take = sum(bids) - sum(fee_cents(b) for b in bids)
        edge = take - 100
        if edge >= min_edge:
            out.append(("BRACKET_SELL_ALL_YES", ev, edge,
                        f"{len(ms)} legs, sum bids={sum(bids)}c, net of fees={take}c"))
    return out


def underlying(m):
    """Group key for a market: last ticker segment with its trailing strike removed.
    e.g. ...-DAL54 -> DAL, ...-DEN53 -> DEN, ...-T50 -> T, ...-MIA -> MIA."""
    return re.sub(r"[\d.]+$", "", m["ticker"].split("-")[-1])


def scan_ladder(ev):
    out = []
    groups = {}
    for m in active(ev.get("markets", [])):
        if m.get("strike_type") in ("greater", "greater_or_equal") and m.get("floor_strike") is not None:
            groups.setdefault(underlying(m), []).append(m)
    for ms in groups.values():
        out += _scan_ladder_group(ev, ms)
    return out


def _scan_ladder_group(ev, ms):
    out = []
    ms.sort(key=lambda m: float(m["floor_strike"]))
    # A ladder needs distinct strikes; equal strikes usually mean different
    # underlyings (e.g. one market per city), which are not comparable.
    if len({float(m["floor_strike"]) for m in ms}) != len(ms):
        return out
    for lo, hi in zip(ms, ms[1:]):
        hi_bid, lo_ask = cents(hi, "yes_bid"), cents(lo, "yes_ask")
        # Executable violation: sell higher strike at bid, buy lower strike at ask
        if hi_bid and lo_ask and hi_bid > lo_ask:
            edge = hi_bid - lo_ask - fee_cents(hi_bid) - fee_cents(lo_ask)
            out.append(("LADDER_INVERSION", ev, edge,
                        f">{hi['floor_strike']} bid {hi_bid}c vs >{lo['floor_strike']} ask {lo_ask}c "
                        f"[{hi['ticker']} / {lo['ticker']}]"))
    return out


def scan_spreads(ev, min_vol=1000, min_spread=15):
    out = []
    for m in active(ev.get("markets", [])):
        b, a = cents(m, "yes_bid"), cents(m, "yes_ask")
        if b and a and (m.get("volume") or 0) >= min_vol and a - b >= min_spread:
            out.append(("WIDE_SPREAD", ev, a - b,
                        f"{m['ticker']} bid {b}c / ask {a}c, vol {m.get('volume')}"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-edge", type=int, default=0, help="min edge in cents for arbs")
    ap.add_argument("--no-spreads", action="store_true")
    ap.add_argument("--csv")
    ap.add_argument("--allow-huge", action="store_true",
                    help=f"keep flags with edge > {MAX_PLAUSIBLE_EDGE}c (usually artifacts)")
    ap.add_argument("--top", type=int, default=0, help="keep only the top N flags (0 = all)")
    args = ap.parse_args()

    events = fetch_events()
    hits = []
    for ev in events:
        hits += scan_brackets(ev, args.min_edge)
        hits += [h for h in scan_ladder(ev) if h[2] >= args.min_edge]
        if not args.no_spreads:
            hits += scan_spreads(ev)

    if not args.allow_huge:
        hits = [h for h in hits if h[0] == "WIDE_SPREAD" or h[2] <= MAX_PLAUSIBLE_EDGE]

    order = {"BRACKET_BUY_ALL_YES": 0, "BRACKET_SELL_ALL_YES": 0, "LADDER_INVERSION": 1, "WIDE_SPREAD": 2}
    hits.sort(key=lambda h: (order[h[0]], -h[2]))
    if args.top:
        hits = sorted(hits, key=lambda h: -h[2])[:args.top]

    for kind, ev, edge, detail in hits:
        print(f"[{kind}] edge={edge}c  {ev.get('event_ticker')}  {ev.get('title','')[:70]}\n    {detail}")
    print(f"\n{len(hits)} flags across {len(events)} open events", file=sys.stderr)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["kind", "edge_cents", "event_ticker", "title", "detail"])
            for kind, ev, edge, detail in hits:
                w.writerow([kind, edge, ev.get("event_ticker"), ev.get("title"), detail])


if __name__ == "__main__":
    main()
