"""
Build the hazard registry from the two source inventories.

The Nepal-box registry this replaces was built by hand on 2026-08-31 and left
no script, so its extent could not be audited or reproduced. That extent turned
out to be the binding constraint: 27.46-30.00 N, 84.00-88.93 E. Replayed
against real events, Kedarnath 2013 sat 510 km outside it and Chamoli 2021
449 km outside. Neither could ever have fired. Registry gaps are silent false
negatives -- they appear in no metric until an event is missed (registry.py).

BOTH source types are required, and this is not a preference. The 2026 founding
event was a rock-ice avalanche off a hanging glacier, not a lake outburst. A
lake-only inventory put the nearest mapped hazard 18.7 km away when the source
was on top of one, and scored the border scenario a WATCH instead of a WARNING.

Sources:
  glaciers  RGI 7.0 glacier centroids (cenlat/cenlon), which also carry the
            slope_deg and zmax_m the cascade model reads.
  lakes     NSIDC HMA Near-Global Glacial Lake Inventory v1 (2015-2018),
            already vendored at data/glacial_lakes_hma.json.

Usage:
    python3 scripts/build_hazard_registry.py --rgi /path/to/rgi7_global.csv
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data")

# The whole Himalayan arc plus the Tibetan side of the border, which is where
# both the 2025 and 2026 sources originated (registry.py). West to Nanga Parbat
# and the Karakoram, east to Namcha Barwa, north far enough to hold Ladakh and
# the trans-Himalayan ranges.
# The registry sits INSIDE the detector's bbox, never outside it. detect.py
# fetches 25.5-38.0 N, 74.0-96.5 E; this is that box inset by 0.5 deg, so a
# hazard on the registry edge still has the full detector box around it --
# more than hazard_radius_km (11) plus source_uncertainty_km (15).
#
# The western edge was 71.0 E. That put 9,979 sites in the Hindu Kush and
# Pamir which the detector, after narrowing to 74.0 E, could never reach:
# sites that can never fire are not coverage, they are weight.
HIMALAYA = {"min_lat": 26.0, "max_lat": 37.5, "min_lon": 74.5, "max_lon": 96.0}


def in_box(lat, lon, b):
    return (b["min_lat"] <= lat <= b["max_lat"]
            and b["min_lon"] <= lon <= b["max_lon"])


def glaciers_from_rgi(path, box):
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["cenlat"]), float(row["cenlon"])
            except (TypeError, ValueError):
                continue
            if not in_box(lat, lon, box):
                continue
            try:
                area = float(row["area_km2"])
            except (TypeError, ValueError):
                area = 0.0
            slope = row.get("slope_deg") or ""
            out.append({
                "name": (row.get("glac_name") or "").strip() or row["rgi_id"],
                "lat": lat, "lon": lon,
                "area_km2": round(area, 4),
                "slope_deg": round(float(slope), 1) if slope else None,
                "zmax_m": row.get("zmax_m") or None,
                "kind": "glacier",
            })
    return out


def lakes_from_hma(path, box):
    with open(path) as f:
        src = json.load(f)
    return [{"name": x["name"], "lat": x["lat"], "lon": x["lon"],
             "area_km2": x.get("area_km2"), "kind": "lake"}
            for x in src if in_box(x["lat"], x["lon"], box)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rgi", required=True,
                    help="RGI2000-v7.0-G-global-attributes.csv")
    ap.add_argument("--lakes", default=os.path.join(DATA, "glacial_lakes_hma.json"))
    ap.add_argument("--out", default=os.path.join(DATA, "hazard_sites_himalaya.json"))
    a = ap.parse_args()

    lakes = lakes_from_hma(a.lakes, HIMALAYA)
    glac = glaciers_from_rgi(a.rgi, HIMALAYA)
    sites = glac + lakes
    if not glac:
        sys.exit("no glaciers extracted -- refusing to write a lake-only registry")

    with open(a.out, "w") as f:
        json.dump(sites, f)

    mpath = os.path.join(DATA, "manifest.json")
    man = json.load(open(mpath)) if os.path.exists(mpath) else {}
    man[os.path.splitext(os.path.basename(a.out))[0]] = {
        "sources": [
            "Randolph Glacier Inventory 7.0, glacier centroids"
            " (open mirror, cluster.klima.uni-bremen.de/~oggm)",
            "NSIDC HMA Near-Global Glacial Lake Inventory v1 (2015-2018)",
        ],
        "bbox": [HIMALAYA["min_lat"], HIMALAYA["min_lon"],
                 HIMALAYA["max_lat"], HIMALAYA["max_lon"]],
        "glaciers": len(glac), "lakes": len(lakes), "total": len(sites),
        "built": datetime.now(timezone.utc).isoformat(),
        "why": "The Nepal-box registry could not see Kedarnath (510 km outside)"
               " or Chamoli (449 km outside). Built by script so the extent is"
               " auditable and reproducible.",
        "built_by": "scripts/build_hazard_registry.py",
    }
    with open(mpath, "w") as f:
        json.dump(man, f, indent=2)

    print("glaciers %d  lakes %d  total %d -> %s"
          % (len(glac), len(lakes), len(sites), a.out))


if __name__ == "__main__":
    main()
