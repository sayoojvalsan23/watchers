"""
Calibrate hazard_radius_km from confirmed events instead of guessing it.

Today the radius is 15 km because Rasuwagadhi 2025 happened to sit 13.3 km
from the nearest registered site. That is a threshold fitted to one point.
This script replaces it with a measured distribution: how far from a mapped
glacier or lake do confirmed mass-movement events actually start?

OFFLINE ONLY. This never runs in a cycle and never touches the trigger path.
Labels arriving late is fine here -- we are measuring history, not predicting.
That distinction is the whole reason this is legitimate while training a
real-time classifier on the same labels would not be.

    python3 scripts/calibrate_radius.py --inventory data/glaciers.json
    python3 scripts/calibrate_radius.py --sources avalanche,usgs --quantile 0.90

WHAT THIS WILL AND WILL NOT TELL YOU

  It reports a quantile of the event-to-inventory distance, with a bootstrap
  interval, and it REFUSES to quote a quantile the sample cannot support.
  A 95th percentile needs roughly 200 samples to be stable. From 46 events
  you get a median and a rough spread, and saying more than that is inventing
  precision -- the exact failure the no-ETA rule exists to prevent.

  It also keeps event populations SEPARATE. Nepal's 4,000+ landslide records
  are 90% June-September: rainfall-triggered slope failure, the Chooralmala
  class. Pooling them with ice-rock collapse would produce a confident number
  measuring the wrong physics.
"""

import argparse
import json
import math
import os
import random
import statistics
import sys
import urllib.parse
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hew.detect import haversine_km                                    # noqa: E402
from hew.registry import load_registry, REGISTRY                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".calibration_cache")
UA = {"User-Agent": "hew-calibration/1.0 (offline parameter fitting)"}

BIPAD = "https://bipadportal.gov.np/api/v1/incident/"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Event populations. Physics, not convenience -- these are not pooled.
SOURCES = {
    "avalanche": {
        "label": "BIPAD avalanche (Nepal, official)",
        "population": "high-altitude snow/ice mass movement",
        "relevant": True,
    },
    "usgs": {
        "label": "USGS landslide-typed seismic events (global)",
        "population": "seismically-detectable mass movement",
        "relevant": True,
    },
    "landslide": {
        "label": "BIPAD landslide (Nepal, official)",
        "population": "rainfall-triggered slope failure -- WRONG POPULATION "
                      "for the seismic track; use for the Phase 5 rainfall engine",
        "relevant": False,
    },
}


# -- fetch ------------------------------------------------------------------

def _cached(name, fetch):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    data = fetch()
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=60) as r:
        return json.loads(r.read().decode())


def fetch_bipad(hazard_id, pages=6):
    def go():
        out = []
        for off in range(0, pages * 1000, 1000):
            q = urllib.parse.urlencode({"hazard": hazard_id, "limit": 1000,
                                        "offset": off})
            try:
                res = _get(f"{BIPAD}?{q}").get("results", [])
            except Exception as e:
                print(f"    (stopped at offset {off}: {e})", file=sys.stderr)
                break
            if not res:
                break
            out += res
        return out
    return _cached(f"bipad_{hazard_id}", go)


def fetch_usgs_landslides():
    def go():
        q = urllib.parse.urlencode({"format": "geojson", "eventtype": "landslide",
                                    "starttime": "1990-01-01",
                                    "endtime": "2026-12-31"})
        return _get(f"{USGS}?{q}")
    return _cached("usgs_landslide", go)


def events(source):
    """Normalise every source to [{lat, lon, when, label}]."""
    if source == "usgs":
        out = []
        for f in fetch_usgs_landslides().get("features", []):
            c = f["geometry"]["coordinates"]
            p = f["properties"]
            if c[0] is None or c[1] is None:
                continue
            out.append({"lat": c[1], "lon": c[0], "when": p.get("time"),
                        "label": p.get("place") or f.get("id")})
        return out
    hz = {"avalanche": 3, "landslide": 17}[source]
    out = []
    for x in fetch_bipad(hz):
        pt = x.get("point")
        if not pt or not pt.get("coordinates"):
            continue
        c = pt["coordinates"]
        out.append({"lat": c[1], "lon": c[0], "when": x.get("incidentOn"),
                    "label": x.get("title")})
    return out


# -- inventory --------------------------------------------------------------

def load_inventory(path):
    """
    A glacier / glacial-lake inventory: [{"name":..,"lat":..,"lon":..}, ...].

    Convert RGI or the ICIMOD HKH inventory into this shape. Falling back to
    the 8-site placeholder registry makes the script RUN but makes its output
    meaningless -- the distances then measure how far events are from eight
    hand-typed points, not from mapped ice.
    """
    if not path:
        inv = load_registry()
        # The fallback is the hand-typed placeholder only when the real
        # inventory file is absent; load_registry returns REGISTRY itself.
        return inv, inv is REGISTRY
    with open(path) as f:
        raw = json.load(f)
    items = raw.get("features", raw) if isinstance(raw, dict) else raw
    inv = []
    for x in items:
        if "geometry" in x:                       # GeoJSON point or centroid
            c = x["geometry"].get("coordinates")
            if not c:
                continue
            inv.append({"name": (x.get("properties") or {}).get("name", "?"),
                        "lat": c[1], "lon": c[0]})
        else:
            inv.append({"name": x.get("name", "?"), "lat": x["lat"],
                        "lon": x["lon"]})
    return inv, False


def nearest_km(lat, lon, inventory):
    return min(haversine_km(lat, lon, s["lat"], s["lon"]) for s in inventory)


# -- statistics -------------------------------------------------------------

def quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    i = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    if lo == hi:
        return sorted_vals[int(i)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def max_defensible_quantile(n):
    """
    Roughly: a q-quantile needs ~10 observations in the tail beyond it, so
    n >= 10 / (1 - q). Inverted, the highest quantile a sample of n supports
    is 1 - 10/n. This is a rule of thumb, and it is deliberately strict --
    quoting a 95th percentile from 46 points is how a guess acquires the
    appearance of evidence.
    """
    if n < 20:
        return None
    return max(0.5, 1.0 - 10.0 / n)


def bootstrap_ci(vals, q, trials, seed=20260826):
    rng = random.Random(seed)
    est = []
    for _ in range(trials):
        s = sorted(rng.choices(vals, k=len(vals)))
        est.append(quantile(s, q))
    est.sort()
    return quantile(est, 0.025), quantile(est, 0.975)


# -- report -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", help="glacier/lake inventory JSON")
    ap.add_argument("--sources", default="avalanche,usgs",
                    help="comma-separated: " + ",".join(SOURCES))
    ap.add_argument("--quantile", type=float, default=0.90)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--bbox", default="27,84,30,89",
                    help="s,w,n,e — restrict events to the study region")
    ap.add_argument("--no-bbox", action="store_true")
    a = ap.parse_args()

    inv, placeholder = load_inventory(a.inventory)
    print("=" * 74)
    print("  HAZARD RADIUS CALIBRATION")
    print("=" * 74)
    print(f"  inventory : {len(inv)} features"
          f"{'  *** PLACEHOLDER REGISTRY ***' if placeholder else ''}")
    if placeholder:
        print()
        print("  !! No inventory supplied, so this fell back to the 8 hand-typed")
        print("  !! registry sites. The numbers below then measure distance to")
        print("  !! eight arbitrary points and MUST NOT be used to set a radius.")
        print("  !! Supply --inventory with RGI or the ICIMOD HKH inventory.")

    s, w, n_, e = (float(x) for x in a.bbox.split(","))
    results = {}

    for src in [x.strip() for x in a.sources.split(",") if x.strip()]:
        meta = SOURCES.get(src)
        if not meta:
            sys.exit(f"unknown source {src}")
        ev = events(src)
        if not a.no_bbox:
            ev = [x for x in ev if s <= x["lat"] <= n_ and w <= x["lon"] <= e]
        print()
        print("-" * 74)
        print(f"  {meta['label']}")
        print(f"  population: {meta['population']}")
        if not meta["relevant"]:
            print("  >> NOT POOLED with the seismic-track sources below.")
        print(f"  events in region: {len(ev)}")
        if len(ev) < 5:
            print("  too few events to characterise a distribution.")
            continue

        d = sorted(nearest_km(x["lat"], x["lon"], inv) for x in ev)
        results[src] = d
        print(f"  distance to nearest inventory feature (km):")
        print(f"     n={len(d)}  min={d[0]:.1f}  median={statistics.median(d):.1f}"
              f"  max={d[-1]:.1f}")
        for q in (0.5, 0.75, 0.90, 0.95):
            v = quantile(d, q)
            print(f"     p{int(q*100):<3d} {v:7.1f} km")

        cap = max_defensible_quantile(len(d))
        want = a.quantile
        print()
        if cap is None:
            print(f"  ONLY A MEDIAN IS DEFENSIBLE HERE (n={len(d)} < 20).")
            print(f"  median {statistics.median(d):.1f} km. Do not quote a percentile.")
        elif want > cap:
            print(f"  REFUSING p{int(want*100)}: n={len(d)} supports at most "
                  f"p{int(cap*100)}.")
            print(f"  Highest defensible value: p{int(cap*100)} = "
                  f"{quantile(d, cap):.1f} km")
            want = cap
        lo, hi = bootstrap_ci(d, want, a.bootstrap)
        print(f"  p{int(want*100)} = {quantile(d, want):.1f} km"
              f"   95% bootstrap CI [{lo:.1f}, {hi:.1f}] km"
              f"   ({a.bootstrap} resamples)")
        if hi - lo > 0.5 * quantile(d, want):
            print("  CI is wider than half the estimate -- the sample is too small")
            print("  to fix a radius. Report it as provisional.")

    # -- recommendation ----------------------------------------------------
    print()
    print("=" * 74)
    if placeholder:
        print("  NO RECOMMENDATION: placeholder inventory. Supply a real one.")
    elif not results:
        print("  NO RECOMMENDATION: no usable source produced a distribution.")
    else:
        pool = sorted(v for src, d in results.items()
                      if SOURCES[src]["relevant"] for v in d)
        cap = max_defensible_quantile(len(pool))
        q = min(a.quantile, cap) if cap else 0.5
        r = quantile(pool, q)
        lo, hi = bootstrap_ci(pool, q, a.bootstrap)
        print(f"  Pooled relevant sources: n={len(pool)}")
        print(f"  Suggested hazard_radius_km = {r:.1f}   (p{int(q*100)}, "
              f"CI [{lo:.1f}, {hi:.1f}])")
        print()
        print("  Config fragment:")
        print(f'      "hazard_radius_km": {r:.1f},')
        print(f'      # calibrated p{int(q*100)} from n={len(pool)} confirmed events,')
        print(f'      # 95% CI [{lo:.1f}, {hi:.1f}] km. Re-run after any inventory change.')
        print()
        print("  THEN RE-RUN THE PHASE 0 GATE. hazard_radius_km is on the")
        print("  trigger path; changing it changes what fires.")
    print("=" * 74)


if __name__ == "__main__":
    main()
