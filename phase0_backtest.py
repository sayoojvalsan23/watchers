"""
Phase 0 catalogue backtest — THE KILL GATE.

Replays the real USGS catalogue through the production filter and counts how
many times it would have fired. This is go/no-go, not a tuning exercise.

    <= 2 alerts/yr   viable, proceed to Phase 1
    2 - 10 /yr       tune thresholds, re-run
    > 10 /yr         catalogue-only detection is not viable; Phase 2 waveform
                     discriminators are mandatory, not optional

Two numbers are reported, because one is not enough:

  FLOOR    alert rate against the CURRENT registry (8 placeholder sites).
           Optimistic. Registry coverage is the binding constraint
           (design finding 2), and this registry covers almost nothing.

  CEILING  alert rate with the hazard-proximity test disabled, i.e. what you
           get if the registry were dense enough to put every valley within
           the radius. The real ICIMOD inventory (~2k lakes in the HKH) sits
           much closer to this end than to the floor.

The honest Phase 0 number is a range, and the gate must be judged on the
CEILING. You cannot ship a filter whose false-alarm rate is acceptable only
because the hazard list is incomplete -- incompleteness is a false-negative
generator, not a false-positive suppressor.

HINDSIGHT: USGS catalogue values here are REVIEWED. A real-time watcher sees
preliminary origins, whose depth and magnitude move. The noise trials stand in
for that; they are not a substitute for a true preliminary-value replay.

    python3 phase0_backtest.py --years 2015 2026 --noise-trials 100
"""

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from hew.detect import evaluate, DEFAULT_CONFIG, is_fixed_depth
from hew.registry import load_registry

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DISPATCH_TIERS = ("advisory", "warning")


# -- ingest -----------------------------------------------------------------

def _cache_key(year, cfg, end_cap):
    """
    The cache key must cover every parameter that changes the RESULT SET.

    It used to be the year alone. Widening the bbox from the Nepal box to the
    full Himalayan arc -- a 20x area change -- then re-running the gate replayed
    the OLD box's 573 events from disk and printed GO, with the new bbox in the
    header. A kill gate that answers for a configuration you are no longer
    running is worse than no gate: it launders a stale pass as a fresh one.
    """
    bb = cfg["bbox"]
    sig = json.dumps({"bbox": bb, "minmag": cfg["min_magnitude"],
                      "end_cap": end_cap}, sort_keys=True)
    return "usgs_%d_%s.json" % (year, hashlib.sha1(sig.encode()).hexdigest()[:12])


def fetch_year(year, cfg, cache_dir, end_cap=None):
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, _cache_key(year, cfg, end_cap))
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    bb = cfg["bbox"]
    start = f"{year}-01-01T00:00:00"
    end = end_cap or f"{year + 1}-01-01T00:00:00"
    params = {
        "format": "geojson", "starttime": start, "endtime": end,
        "minlatitude": bb["min_lat"], "maxlatitude": bb["max_lat"],
        "minlongitude": bb["min_lon"], "maxlongitude": bb["max_lon"],
        "minmagnitude": cfg["min_magnitude"],
        "orderby": "time-asc",
    }
    url = USGS + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as r:
        data = json.loads(r.read().decode())
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def to_events(geojson):
    out = []
    for f in geojson.get("features", []):
        p = f.get("properties", {}) or {}
        c = (f.get("geometry", {}) or {}).get("coordinates") or [None, None, None]
        if c[0] is None or c[1] is None or not p.get("time"):
            continue
        out.append({
            "id": f.get("id"),
            "lon": c[0], "lat": c[1], "depth": c[2],
            "mag": p.get("mag"),
            "t": datetime.fromtimestamp(p["time"] / 1000, timezone.utc),
            "type": p.get("type"),
        })
    out.sort(key=lambda e: e["t"])
    return out


# -- evaluation -------------------------------------------------------------

OPEN_SITE = [{"name": "<any-valley>", "lat": 0.0, "lon": 0.0, "reach_id": "x"}]


def score_all(events, registry, cfg, ignore_proximity=False):
    """Run the production filter. ignore_proximity models a complete registry."""
    results = []
    for e in events:
        if ignore_proximity:
            # Ceiling: put a registered site AT the event, then score normally.
            # Scoring the real function beats reconstructing it by hand -- the
            # hand-rolled version silently went stale when proximity changed
            # from a step to a probability.
            here = [{"name": "<any-valley>", "lat": e["lat"], "lon": e["lon"],
                     "reach_id": "x"}]
            r = evaluate(e["lat"], e["lon"], e["depth"], e["mag"], here, cfg)
        else:
            r = evaluate(e["lat"], e["lon"], e["depth"], e["mag"], registry, cfg)
        results.append((e, r))
    return results


def apply_circuit_breaker(results, cfg):
    """Chronological replay of the 24h / max_dispatched cap."""
    cb = cfg["circuit_breaker"]
    window = timedelta(hours=cb["window_hours"])
    fired, suppressed, recent = [], 0, []
    for e, r in results:
        if r["tier"] not in DISPATCH_TIERS:
            continue
        recent = [t for t in recent if e["t"] - t < window]
        if len(recent) >= cb["max_dispatched"]:
            suppressed += 1
            continue
        recent.append(e["t"])
        fired.append((e, r))
    return fired, suppressed


# -- noise ------------------------------------------------------------------

def jitter(events, rng, d_km=3.0, d_depth=4.0, d_mag=0.2):
    """
    Plausible preliminary-vs-reviewed catalogue error. Depth is the sensitive
    axis: the fixed-depth rejection is a 0.05 km equality test, so any depth
    jitter converts rejected default-depth events into live candidates.
    """
    out = []
    for e in events:
        if e["depth"] is None or e["mag"] is None:
            out.append(e)
            continue
        j = dict(e)
        j["lat"] = e["lat"] + rng.gauss(0, d_km / 111.0)
        j["lon"] = e["lon"] + rng.gauss(0, d_km / 100.0)
        j["depth"] = max(0.0, e["depth"] + rng.gauss(0, d_depth))
        j["mag"] = max(0.0, e["mag"] + rng.gauss(0, d_mag))
        out.append(j)
    return out


# -- report -----------------------------------------------------------------

def verdict(rate):
    if rate <= 2:    return "GO", "viable — proceed to Phase 1"
    if rate <= 10:   return "TUNE", "tune thresholds and re-run"
    return "NO-GO", "catalogue-only detection not viable; Phase 2 waveform discriminators are MANDATORY"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs=2, type=int, required=True, metavar=("FROM", "TO"))
    ap.add_argument("--noise-trials", type=int, default=0)
    ap.add_argument("--cache-dir", default=".catalogue_cache")
    ap.add_argument("--seed", type=int, default=20260826)
    a = ap.parse_args()

    cfg = DEFAULT_CONFIG
    registry = load_registry()
    y0, y1 = a.years
    today = datetime.now(timezone.utc)

    print(f"Phase 0 backtest — config {cfg['version']}, registry {len(registry)} sites")
    print(f"bbox {cfg['bbox']}  M>={cfg['min_magnitude']}  years {y0}..{y1}\n")

    events, per_year_span = [], {}
    for y in range(y0, y1 + 1):
        cap = None
        if y >= today.year:
            cap = today.strftime("%Y-%m-%dT%H:%M:%S")
        gj = fetch_year(y, cfg, a.cache_dir, cap)
        ev = to_events(gj)
        events.extend(ev)
        start = datetime(y, 1, 1, tzinfo=timezone.utc)
        end = min(datetime(y + 1, 1, 1, tzinfo=timezone.utc), today)
        per_year_span[y] = (end - start).days / 365.25
        print(f"  {y}: {len(ev):5d} events   ({per_year_span[y]:.2f} yr)")

    span = sum(per_year_span.values())
    print(f"\n  TOTAL {len(events)} events over {span:.2f} years\n")
    if not events:
        sys.exit("no events fetched")

    for label, ignore in (("MEASURED (real glacial-lake inventory)", False),
                          ("CEILING (a lake at every event — upper bound)", True)):
        res = score_all(events, registry, cfg, ignore_proximity=ignore)
        tiers = Counter(r["tier"] for _, r in res)
        raw = sum(tiers[t] for t in DISPATCH_TIERS)
        fired, cb_supp = apply_circuit_breaker(res, cfg)

        print("=" * 68)
        print(label)
        print("=" * 68)
        print("  tiers: " + "  ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
        print(f"  dispatch-tier events      : {raw}")
        print(f"  suppressed by breaker     : {cb_supp}")
        print(f"  WOULD HAVE FIRED          : {len(fired)}")
        rate = len(fired) / span
        v, why = verdict(rate)
        print(f"  rate                      : {rate:.2f} alerts/year")
        print(f"  VERDICT                   : {v} — {why}")

        by_year = defaultdict(int)
        for e, _ in fired:
            by_year[e["t"].year] += 1
        if by_year:
            print("  by year: " + "  ".join(
                f"{y}={by_year[y]}" for y in sorted(by_year)))
        print()

    # fixed-depth attribution
    fd = sum(1 for e in events
             if e["depth"] is not None and is_fixed_depth(e["depth"]))
    print(f"fixed-depth events in catalogue : {fd} / {len(events)} "
          f"({100.0 * fd / len(events):.1f}%) — rejected by FINDING 1")

    # noise
    if a.noise_trials:
        rng = random.Random(a.seed)
        print(f"\nnoise trials ({a.noise_trials}, depth sigma 4 km, loc sigma 3 km, mag sigma 0.2)")
        for label, ignore in (("floor", False), ("ceiling", True)):
            rates = []
            for _ in range(a.noise_trials):
                res = score_all(jitter(events, rng), registry, cfg, ignore_proximity=ignore)
                f, _s = apply_circuit_breaker(res, cfg)
                rates.append(len(f) / span)
            rates.sort()
            p = lambda q: rates[min(len(rates) - 1, int(q * len(rates)))]
            print(f"  {label:8s} median {statistics.median(rates):6.2f}   "
                  f"p90 {p(0.90):6.2f}   max {max(rates):6.2f}  alerts/yr"
                  f"   [{verdict(p(0.90))[0]} at p90]")


if __name__ == "__main__":
    main()
