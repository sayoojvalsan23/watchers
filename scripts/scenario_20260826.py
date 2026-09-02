"""
End-to-end replay of 26 August 2026, against the real USGS record and its
real publication history.

The question this answers is not "does the detector fire". It is: AT WHAT
TIME would it have fired, using only what existed at that time?

    python3 scripts/scenario_20260826.py

Run it before believing any lead-time claim about this system.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hew.detect import evaluate
from hew.registry import load_registry
from hew.routing import RiverNetwork, load_settlements, exposed_settlements

NPT = timezone(timedelta(hours=5, minutes=45))
EVENT = "us7000tbwb"
API = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid=" + EVENT
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     ".catalogue_cache", f"{EVENT}_detail.json")

# Nepal local time. CORRECTED 2026-08-31 against public reporting: the
# project brief's "first official warning 10:28, gap 1h51m" is not supported.
# The Flood Forecasting Division learned of the flood at ~09:00-09:05, by
# TELEPHONE from the Rasuwa District Administration Office and staff at the
# Betrabati hydrological station -- not from any seismic feed. SMS alerts
# reached 679,295 people at 09:15-09:16.
COLLAPSE_NPT = "08:37"
FFD_LEARNS_NPT = "09:00-09:05"
SMS_BROADCAST_NPT = "09:15"
SMS_RECIPIENTS = 679295


def npt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(NPT)


def load():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    req = urllib.request.Request(API, headers={"User-Agent": "hew-scenario/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(d, f)
    return d


def rule(c="─"):
    print(c * 74)


def main():
    d = load()
    p = d["properties"]
    lon, lat, dep = d["geometry"]["coordinates"]
    origin_ms = p["time"]
    o = npt(origin_ms)

    first_pub = min(pr["updateTime"] for pl in p["products"].values() for pr in pl)
    fp = npt(first_pub)
    lag_h = (first_pub - origin_ms) / 1000 / 3600

    rule("═")
    print("  26 AUGUST 2026 — END-TO-END SCENARIO REPLAY")
    print(f"  USGS {EVENT}  ·  all times Nepal local (UTC+5:45)")
    rule("═")

    print("\nWHAT THE CATALOGUE ACTUALLY DID\n")
    print(f"  {COLLAPSE_NPT}          collapse. Seismic energy radiates worldwide in seconds.")
    print(f"  {FFD_LEARNS_NPT}    Flood Forecasting Division learns of the flood --")
    print(f"                 by telephone from Rasuwa DAO and Betrabati station staff,")
    print(f"                 NOT from a seismic feed.                        (+~25 min)")
    print(f"  {SMS_BROADCAST_NPT}          SMS to {SMS_RECIPIENTS:,} residents            (+38 min)")
    print(f"  {fp:%H:%M} {fp:%b-%d}   USGS publishes the landslide-typed origin"
          f"  (+{lag_h:.0f}h{int((lag_h%1)*60):02d}m)")
    print()
    print("  USGS note on this event, verbatim from the record:")
    hdr = p["products"].get("general-text", [{}])[0]
    txt = hdr.get("contents", {}).get("", {}).get("bytes", "")
    for frag in ("initially reported as a magnitude 4.4 earthquake",
                 "satellite imagery",
                 "no earthquake had occurred"):
        if frag in txt:
            print(f'    · "...{frag}..."')

    print("\nPRODUCT PUBLICATION HISTORY\n")
    rows = sorted((pr["updateTime"], t, pr["properties"].get("event-type", ""),
                   pr["properties"].get("magnitude", ""))
                  for t, pl in p["products"].items() for pr in pl)
    for ms, t, et, mag in rows:
        n = npt(ms)
        mins = (ms - origin_ms) / 1000 / 60
        tag = " ".join(x for x in (et, ("M" + mag if mag else "")) if x)
        print(f"  +{mins:7.0f} min   {n:%b-%d %H:%M} NPT   {t:15s} {tag}")

    # ---- A: the reviewed record, which is what the backtest uses ----
    R = load_registry()
    rule()
    print("\nA · REVIEWED RECORD  (what the detector is tested against today)\n")
    print(f"  M{p['mag']}  depth {dep} km  type={p['type']}  {lat},{lon}")
    t0 = time.perf_counter()
    ra = evaluate(lat, lon, dep, p["mag"], R)
    det_ms = (time.perf_counter() - t0) * 1000
    print(f"  -> {ra['tier'].upper()}  score={ra['score']}  "
          f"{ra['nearest_site']} ({ra['nearest_km']} km)   [{det_ms:.2f} ms]")

    # ---- B: what existed at 08:37 ----
    rule()
    print("\nB · AS PUBLISHED AT THE TIME  (M4.4 earthquake, depth unconstrained)\n")
    print("  USGS's own note says the event was first reported as M4.4 and as an")
    print("  earthquake. An unconstrained teleseismic solution carries the 10 km")
    print("  default depth. Location came from satellite imagery only later, so")
    print("  even these coordinates are a generous assumption.")
    rb = evaluate(lat, lon, 10.0, 4.4, R)
    print(f"\n  M4.4  depth 10.0 km (default)  type=earthquake  {lat},{lon}")
    print(f"  -> {rb['tier'].upper()}   {rb['factors'][0]}")

    # ---- corridor ----
    rule()
    print("\nDOWNSTREAM CORRIDOR  (from the reviewed location)\n")
    # Use the SAME path the watcher uses -- branch union, river_km re-based
    # on the source. Calling net.trace() directly here gave snap-relative
    # distances and quietly disagreed with production by ~6 km.
    t0 = time.perf_counter()
    from hew.detect import DEFAULT_CONFIG
    from hew.routing import trace_branches, exposed_settlements_union
    net = RiverNetwork.load()
    branches = trace_branches(net, lat, lon,
                              uncertainty_km=DEFAULT_CONFIG.get("source_uncertainty_km", 0))
    snap = branches[0]["snap_km"] if branches else None
    ex = exposed_settlements_union(branches, load_settlements(), corridor_km=2.0)
    route_ms = (time.perf_counter() - t0) * 1000
    print(f"  {len(ex)} settlements over {ex[-1]['river_km']:.0f} km of channel"
          f"   [{route_ms:.0f} ms, snap {snap} km]")
    brief = ("Mailung", "Betrawati", "Bidur", "Devighat")
    for s in ex:
        if s["name"] in brief:
            pop = f"{s['population']:,}" if s["population"] else "—"
            print(f"    {s['river_km']:6.1f} km   {s['name']:12s} pop {pop}")

    # ---- who the warning actually reached in time ----
    rule()
    kmx = {s_["name"]: s_["river_km"] for s_ in ex}
    print("\nDID THE WARNING BEAT THE WATER?\n")
    print("  Anchor is MEASURED, not modelled: public reporting puts the Trishuli")
    print(f"  gauge spike at Galchhi ({kmx.get('Galchhi', 0):.1f} river-km) at "
          f"09:15-09:30 NPT. Collapse")
    print("  08:37. Everything below is linear interpolation from that one anchor")
    print("  and is retrospective analysis only -- it is NOT an operational ETA")
    print("  and must never be presented as one (see CONSTRAINTS.md, no-ETA).\n")
    # Galchhi's river_km is now measured from the source, so the implied
    # front speed changes with it. The anchor is a measured TIME at a PLACE.
    anchor, lo_m, hi_m, sms = kmx.get("Galchhi"), 38, 53, 38
    if anchor:
        print(f"  implied front speed {anchor/(hi_m/60):.0f}-{anchor/(lo_m/60):.0f} km/h\n")
        print(f"  {'town':12s} {'river km':>8s}  {'surge window':>13s}   vs the 09:15 SMS")
        for t in ("Mailung", "Betrawati", "Bidur", "Devighat", "Galchhi", "Gajuri"):
            if t not in kmx:
                continue
            d = kmx[t]
            a, b = d / anchor * lo_m, d / anchor * hi_m
            f = lambda m: f"{int(round(8*60+37+m))//60:02d}:{int(round(8*60+37+m))%60:02d}"
            v = ("SURGE PASSED FIRST" if b < sms else
                 "arrives with the SMS" if a < sms else
                 f"warned {a-sms:.0f}-{b-sms:.0f} min ahead")
            print(f"  {t:12s} {d:8.1f}  {f(a)}-{f(b)}   {v}")
        print("\n  Same towns had detection been automated from the 08:37 signal")
        print("  (alert out ~08:50, closing the ~25 min humans took to phone it in):")
        for t in ("Betrawati", "Bidur", "Devighat", "Galchhi"):
            if t in kmx:
                a = kmx[t] / anchor * lo_m
                print(f"    {t:12s} {a-sms:+4.0f} min  ->  {a-13:+4.0f} min")

    # ---- verdict ----
    rule("═")
    print("\nVERDICT\n")
    print("  Detection latency, once a usable record exists:  "
          f"{det_ms:.0f} ms detect + {route_ms/1000:.1f} s route.")
    print("  The pipeline is not the bottleneck. It never was.")
    print()
    print(f"  But the record it needs did not exist until +{lag_h:.0f} hours —")
    print(f"  {lag_h - 0.63:.0f} hours AFTER the SMS broadcast had already gone out.")
    print()
    print("  Case A fires because it reads a characterisation that took 13 hours")
    print("  of long-period analysis and satellite imagery to produce. Case B is")
    print("  what the feed actually carried at 08:37, and it is rejected.")
    print()
    print("  CATALOGUE-ONLY DETECTION CANNOT WARN ANYONE FOR THIS EVENT CLASS.")
    print("  The gap is not classification. It is that USGS review latency is")
    print("  measured in hours and the warning window is measured in minutes.")
    print()
    print("  THE REAL TARGET IS SMALLER THAN THE BRIEF CLAIMED, AND STILL REAL:")
    print("  humans phoned the flood in at ~09:00. The seismic signal existed at")
    print("  08:37. That ~25 minutes of detection latency is what an automated")
    print("  path could close -- not the 1h51m the brief asserts.")
    print()
    print("  This is the empirical case for Phase 2 waveform detection, and it")
    print("  is far stronger than the discrimination argument in the design docs:")
    print("  a local SeedLink + STA/LTA listener does not wait for review.")
    rule("═")


if __name__ == "__main__":
    main()
