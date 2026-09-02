"""
Build the Himalayan watch-cell set for the rainfall track.

WHY THIS IS NOT JUST kerala_cells.json WITH A BIGGER BOX

The rainfall method is self-calibrating -- it compares each site to ten years
of its OWN prior climatology at that exact spot, so a new region needs no new
threshold table. What a new region DOES need is a cell list that fits the
Open-Meteo quota, and the Himalaya does not fit the way Kerala does.

  82,152 hazard sites -> 4,870 model-grid nodes (0.07 lat x 0.16 lon).
  At Kerala's 3-hourly cadence that is 38,960 locations/day against a cap of
  ~10,000, with Kerala itself already using 2,528. Four times over budget.

So quota is rationed by EXPOSURE, not spread evenly. A hazard with nobody
downstream does not earn a poll:

  nodes with >=1 settlement within 10 km : 1,842
  the top 500 of those cover 7,722 of 11,277 exposed settlements (68%)

Hence two tiers. Tier A polls 3-hourly, tier B twelve-hourly. Rainfall
accumulates over 12-72 h, so the slower tier loses little; the fast tier is
reserved for where the most people are.

EXPOSURE HERE IS A RADIAL PROXY, NOT A ROUTED CORRIDOR. Settlements within
10 km straight-line, not settlements traced downstream. Routing all 4,870
nodes at ~5 s each is ~7 h, and this is a budgeting decision, not a dispatch
decision -- no alert is issued from this number. Tier assignment should be
revisited against real corridors before the tiers mean anything operational.

TERRAIN COSTS THE SAME QUOTA AS THE FORECASTS. susceptibility() spends
GRID*GRID = 25 elevation samples per node, so screening all 1,842 exposed
nodes is ~46,050 locations -- several days of the entire daily cap. It is
cached forever, but spent carelessly it throttles the LIVE Kerala watcher.
So --max-elevations caps a run and the script is resumable: re-run it daily
until it reports 0 unscreened.

Usage:
    python3 scripts/build_himalaya_cells.py --max-elevations 2000
    python3 scripts/build_himalaya_cells.py --plan-only
"""

import argparse
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hew import rain_watcher as rw
from hew import terrain
from hew.routing import load_settlements

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data")
REGISTRY = os.path.join(DATA, "hazard_sites_himalaya.json")
OUT = os.path.join(DATA, "himalaya_cells.json")

EXPOSURE_KM = 10.0
TIER_A = 400
BIN = 0.25


def node_of(lat, lon):
    return (round(lat / rw.GROUP_LAT) * rw.GROUP_LAT,
            round(lon / rw.GROUP_LON) * rw.GROUP_LON)


def exposure_index(settlements):
    grid = collections.defaultdict(list)
    for p in settlements:
        grid[(int(p["lat"] / BIN), int(p["lon"] / BIN))].append((p["lat"], p["lon"]))
    return grid


def exposure(grid, lat, lon, km=EXPOSURE_KM):
    r = int(km / 111 / BIN) + 1
    n = 0
    for i in range(int(lat / BIN) - r, int(lat / BIN) + r + 1):
        for j in range(int(lon / BIN) - r, int(lon / BIN) + r + 1):
            for (pa, po) in grid.get((i, j), ()):
                if math.hypot((pa - lat) * 111,
                              (po - lon) * 111 * math.cos(math.radians(lat))) <= km:
                    n += 1
    return n


def plan():
    with open(REGISTRY) as f:
        sites = json.load(f)
    nodes = collections.defaultdict(int)
    for s in sites:
        nodes[node_of(s["lat"], s["lon"])] += 1

    grid = exposure_index(load_settlements())
    scored = []
    for (la, lo), n_haz in nodes.items():
        e = exposure(grid, la, lo)
        if e:
            scored.append({"lat": round(la, 4), "lon": round(lo, 4),
                           "hazards": n_haz, "settlements_10km": e})
    scored.sort(key=lambda c: (-c["settlements_10km"], -c["hazards"]))
    for i, c in enumerate(scored):
        c["tier"] = "A" if i < TIER_A else "B"
    return scored, len(nodes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-elevations", type=int, default=0,
                    help="cap elevation locations spent this run (0 = plan only)")
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args()

    cells, total_nodes = plan()
    a_n = sum(1 for c in cells if c["tier"] == "A")
    print("hazard nodes %d | exposed %d | tier A %d | tier B %d"
          % (total_nodes, len(cells), a_n, len(cells) - a_n))
    print("forecast cost/day: A %d*8 + B %d*12h = %d locations"
          % (a_n, len(cells) - a_n, a_n * 8 + (len(cells) - a_n) * 2))

    existing = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            d = json.load(f)
        prev = d["cells"] if isinstance(d, dict) else d
        existing = {(c["lat"], c["lon"]): c for c in prev}

    todo = [c for c in cells if (c["lat"], c["lon"]) not in existing]
    print("unscreened: %d  (%d elevation locations to finish)"
          % (len(todo), len(todo) * terrain.GRID ** 2))
    if a.plan_only or not a.max_elevations:
        return

    budget = a.max_elevations // (terrain.GRID ** 2)
    cache = terrain._load_cache()
    done = 0
    for c in todo[:budget]:
        t = terrain.susceptibility(c["lat"], c["lon"], cache)
        c.update({k: t[k] for k in
                  ("elev_m", "relief_m", "slope_deg", "band", "watchable")})
        existing[(c["lat"], c["lon"])] = c
        done += 1
    terrain._save_cache(cache)

    out = sorted(existing.values(),
                 key=lambda c: (c["tier"], -c["settlements_10km"]))

    # Emit the CONTAINER form, not a bare list. rain_watch.load_cells accepts
    # a list but then reports {"complete": None, "WARNING": "no coverage
    # record"}, and an incremental build is exactly when the page must say
    # what it has not looked at. An unscanned cell is NOT a safe cell.
    scanned = {(c["lat"], c["lon"]) for c in out}
    unscanned = [c for c in cells if (c["lat"], c["lon"]) not in scanned]
    bands = sorted({math.floor(c["lat"]) for c in unscanned})
    spans, run = [], None
    for b in bands:
        if run and b == run[1]:
            run[1] = b + 1
        else:
            run = [b, b + 1]
            spans.append(run)

    doc = {
        "region": "Himalayan arc",
        "bbox": [26.0, 37.5, 71.0, 96.0],
        "step_deg": rw.GROUP_LAT,
        "sampling": ("hazard-derived nodes on the forecast model grid "
                     "(%.2f lat x %.2f lon), relief over %dx%d Copernicus DEM "
                     "~90 m via Open-Meteo; quota rationed by exposure, not "
                     "spread evenly" % (rw.GROUP_LAT, rw.GROUP_LON,
                                        terrain.GRID, terrain.GRID)),
        "complete": not unscanned,
        "coverage": {
            "cells_in_bbox": len(cells),
            "cells_evaluated": len(out),
            "unscanned_latitude_bands": [[a, b] for a, b in spans],
        },
        "bands": terrain.BANDS,
        "cells": out,
    }
    with open(OUT, "w") as f:
        json.dump(doc, f)

    watchable = sum(1 for c in out if c.get("watchable"))
    print("screened %d this run; %d of %d cells, %d watchable -> %s"
          % (done, len(out), len(cells), watchable, OUT))
    print("complete=%s  unscanned latitude bands: %s"
          % (doc["complete"], doc["coverage"]["unscanned_latitude_bands"] or "none"))


if __name__ == "__main__":
    main()
