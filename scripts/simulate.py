"""
Drill: push a synthetic event through the whole path and see what happens.

    python3 scripts/simulate.py --list
    python3 scripts/simulate.py langtang_2026
    python3 scripts/simulate.py --lat 28.3 --lon 85.4 --depth 1.5 --mag 5.0
    python3 scripts/simulate.py --fault stale     # exercise the fault alarm

WRITES NOTHING. Not one row.

That is the point, not a limitation. The decision ledger is append-only and
its purpose is that "why did it not fire" is answerable from the record
alone. Drill events sitting in it, indistinguishable from real ones, would
destroy exactly that property -- and they would land in the Phase 0
false-alarm count, which is the number the whole project is judged on.

The canary already does this correctly: it runs a synthetic event through
evaluate() and records only a heartbeat, tagged as a canary. This follows
the same rule and goes further by persisting nothing at all.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hew import alarm                                                  # noqa: E402
from hew.detect import evaluate, DEFAULT_CONFIG                        # noqa: E402
from hew.notify import Dispatcher                                      # noqa: E402
from hew.registry import load_registry                                 # noqa: E402
from hew.routing import (RiverNetwork, load_settlements, trace_branches,  # noqa: E402
                         exposed_settlements_union)

# Real records where a real record exists. Where one does not, that is stated.
SCENARIOS = {
    "langtang_2026": {
        "what": "26 Aug 2026, the reviewed USGS record (us7000tbwb)",
        "lat": 28.271, "lon": 85.515, "depth": 0.0, "mag": 5.2,
        "note": "The founding event. Should WARN.",
    },
    "langtang_2026_as_published": {
        "what": "26 Aug 2026 as the feed actually carried it at 08:37",
        "lat": 28.271, "lon": 85.515, "depth": 10.0, "mag": 4.4,
        "note": "M4.4 earthquake, default depth. The landslide "
                "characterisation came +13h. Should NOT dispatch (D5).",
    },
    "rasuwagadhi_2025": {
        "what": "Rasuwagadhi 2025 — SYNTHETIC, no USGS record exists",
        "lat": 28.28, "lon": 85.38, "depth": 3.0, "mag": 4.6,
        "note": "Nothing in the catalogue at any magnitude. 17.8 km from "
                "the nearest mapped lake.",
    },
    "gorkha_2015": {
        "what": "Gorkha M7.8, 25 Apr 2015 — the trigger for Langtang",
        "lat": 28.21, "lon": 85.55, "depth": 8.2, "mag": 7.8,
        "note": "Rejected as tectonic. The collapse it triggered killed "
                "~350 and produced no separate record (D9).",
    },
    "deep_tectonic": {
        "what": "An ordinary deep earthquake at the same place",
        "lat": 28.271, "lon": 85.515, "depth": 45.0, "mag": 5.2,
        "note": "Should reject. Discrimination check.",
    },
    "far_from_hazard": {
        "what": "Shallow and well sized, but nowhere near mapped ice",
        "lat": 27.20, "lon": 88.90, "depth": 3.0, "mag": 4.5,
        "note": "Should not dispatch. Proximity is half the score.",
    },
}


def rule(c="-"):
    print(c * 72)


def run(lat, lon, depth, mag, label=""):
    R = load_registry()
    print()
    rule("=")
    print(f"  DRILL{': ' + label if label else ''}")
    print(f"  M{mag}  depth {depth} km  at {lat}, {lon}")
    rule("=")

    t = time.perf_counter()
    r = evaluate(lat, lon, depth, mag, R)
    ms = (time.perf_counter() - t) * 1000

    print(f"\n  DECISION   {r['tier'].upper()}   score {r['score']}   [{ms:.2f} ms]")
    print(f"  factors    {', '.join(r['factors'])}")
    if r["nearest_km"] is not None:
        print(f"  nearest    {r['nearest_km']} km from a mapped glacial lake"
              f"   confidence {r['proximity_confidence']}")

    dispatches = r["tier"] in ("advisory", "warning")
    if not dispatches:
        print(f"\n  Would NOT dispatch. Nothing is sent to anyone.")
        if r["tier"] == "reject":
            print(f"  Rejected outright: {r['factors'][0]}")
        rule("=")
        return r

    print("\n  Would dispatch — routing the corridor ...")
    t = time.perf_counter()
    net = RiverNetwork.load()
    br = trace_branches(net, lat, lon,
                        uncertainty_km=DEFAULT_CONFIG.get("source_uncertainty_km", 0))
    ex = exposed_settlements_union(br, load_settlements(), corridor_km=2.0)
    rms = (time.perf_counter() - t) * 1000

    if not ex:
        print("  no corridor — source is off the routed network")
        rule("=")
        return r

    print(f"  {len(ex)} settlements over {ex[-1]['river_km']:.0f} km, "
          f"{len(br)} branch(es)   [{rms:.0f} ms]")
    pop = sum(s["population"] or 0 for s in ex if s["population"])
    if pop:
        print(f"  {pop:,} recorded residents (OSM records population for very few)")
    print()
    for s in ex[:8]:
        print(f"     {s['river_km']:6.1f} km  {s['name'][:30]:30s} [{s['channel']}]")
    if len(ex) > 8:
        print(f"     ... and {len(ex) - 8} more")

    print("\n  ALERT TEXT (fixed template, structured slots):\n")
    print("    " + (Dispatcher().render(r["tier"], r, ex) or "(no template)"))
    print("\n  NOT SENT. Phase 1 dispatch is off, and nothing was written.")
    rule("=")
    return r


def fault(kind):
    print()
    rule("=")
    print(f"  FAULT DRILL: {kind}")
    rule("=")
    method, why = alarm.available()
    print(f"\n  alarm output: {method}  ({why})")
    reasons = {
        "stale": "feed stale: no good poll for 42 minutes",
        "nopoll": "no successful poll ever recorded",
        "canary": "canary failed: evaluation path broken (tier=log score=20)",
    }
    if kind not in reasons:
        sys.exit(f"unknown fault: choose from {', '.join(reasons)}")
    print(f"  firing: {reasons[kind]}\n")
    made = alarm.sound(reasons[kind], force=True)
    print(f"  produced a signal: {made}")
    if not made:
        print("  (no output device — see hew/alarm.py for options)")
    print("\n  This is a SYSTEM fault, not a hazard. The alarm is deliberately")
    print("  not wired to advisory/warning; that would be a dispatch channel.")
    rule("=")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", help="named scenario, or use --lat/--lon")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="run every scenario")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--depth", type=float)
    ap.add_argument("--mag", type=float)
    ap.add_argument("--fault", choices=["stale", "nopoll", "canary"])
    a = ap.parse_args()

    if a.list:
        print("\n  scenarios:\n")
        for k, v in SCENARIOS.items():
            print(f"    {k:28s} {v['what']}")
            print(f"    {'':28s} {v['note']}\n")
        return
    if a.fault:
        return fault(a.fault)
    if a.all:
        for k, v in SCENARIOS.items():
            run(v["lat"], v["lon"], v["depth"], v["mag"], f"{k} — {v['what']}")
        return
    if a.scenario:
        s = SCENARIOS.get(a.scenario)
        if not s:
            sys.exit(f"unknown scenario. --list to see them.")
        return run(s["lat"], s["lon"], s["depth"], s["mag"],
                   f"{a.scenario} — {s['what']}")
    if None not in (a.lat, a.lon, a.depth, a.mag):
        return run(a.lat, a.lon, a.depth, a.mag, "custom")
    ap.error("give a scenario name, or --lat --lon --depth --mag, or --fault")


if __name__ == "__main__":
    main()
