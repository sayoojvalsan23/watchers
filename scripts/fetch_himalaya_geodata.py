"""
Tiled Overpass fetch for the whole Himalayan arc.

fetch_geodata.py issues ONE query per basin. That works for Trishuli (1.0 sq
deg) and just barely for Kerala (12 sq deg, "will sometimes still 504"). The
Himalayan box is ~287 sq deg -- roughly 290x Trishuli -- so a single query is
not a slow version of the same thing, it is a query that never returns.

So: tile it, checkpoint every tile, and make re-runs resumable. A fetch that
dies at tile 47 of 50 must not start over.

WATERWAYS ARE river ONLY, not river|stream. Kerala needs streams and that is
load-bearing there (27 of 30 mapped waterways around Chooralmala are
waterway=stream). The same query over this box extrapolates to ~2.4 GB from
the Kerala density, which Overpass will not serve and we should not ask it
for. river-only matches what rivers_region.json already used for Nepal.
The limitation is real and inherited: Himalayan headwater channels tagged
stream are not in the routing graph.

Usage:
    python3 scripts/fetch_himalaya_geodata.py            # resume/continue
    python3 scripts/fetch_himalaya_geodata.py --merge    # merge tiles only
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Imported after the sys.path line above, which is what makes it importable:
# fetch_geodata is a sibling script, not a package module. Reused rather than
# reimplemented so both fetchers share one mirror list and one retry policy.
from fetch_geodata import query, DATA

HIMALAYA = (26.0, 71.0, 37.5, 96.0)            # s, w, n, e
STEP = 2.5
TILEDIR = os.path.join(DATA, "himalaya_tiles")

RIVERS_Q = ('[out:json][timeout:600];'
            'way["waterway"="river"]({s},{w},{n},{e});out geom;')
PLACES_Q = ('[out:json][timeout:300];'
            '(node["place"~"^(city|town|village|hamlet)$"]({s},{w},{n},{e}););'
            'out body;')


def tiles():
    s, w, n, e = HIMALAYA
    out = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            out.append((round(lat, 2), round(lon, 2),
                        round(min(lat + STEP, n), 2), round(min(lon + STEP, e), 2)))
            lon += STEP
        lat += STEP
    return out


def fetch_all(layer, q):
    os.makedirs(TILEDIR, exist_ok=True)
    ts = tiles()
    for i, (s, w, n, e) in enumerate(ts, 1):
        path = os.path.join(TILEDIR, "%s_%s_%s_%s_%s.json" % (layer, s, w, n, e))
        if os.path.exists(path):
            print("  [%d/%d] %s cached" % (i, len(ts), os.path.basename(path)),
                  flush=True)
            continue
        print("  [%d/%d] %s %s,%s..%s,%s" % (i, len(ts), layer, s, w, n, e),
              flush=True)
        try:
            payload = query(q.format(s=s, w=w, n=n, e=e))
        except Exception as err:
            # Deliberately broad. query() only retries HTTPError/URLError/
            # TimeoutError, but Overpass also drops connections mid-response
            # (http.client.RemoteDisconnected), which killed a 34/50 run.
            # This is a maintenance fetcher, not the trigger path: one bad
            # tile must cost one tile, not the other 49. The tile is left
            # absent, reported below as missing, and retried on re-run.
            # Checkpointed: the tile is simply absent and a re-run retries it.
            print("    FAILED %s (%s)" % (os.path.basename(path), err), flush=True)
            continue
        with open(path, "w") as f:
            json.dump(payload, f)
        print("    %d elements" % len(payload.get("elements", [])), flush=True)


def merge(layer):
    """Merge tiles, de-duplicating by OSM element id across tile seams."""
    seen, elements, base = set(), [], None
    missing = 0
    for (s, w, n, e) in tiles():
        path = os.path.join(TILEDIR, "%s_%s_%s_%s_%s.json" % (layer, s, w, n, e))
        if not os.path.exists(path):
            missing += 1
            continue
        with open(path) as f:
            d = json.load(f)
        base = base or d.get("osm3s", {}).get("timestamp_osm_base")
        for el in d.get("elements", []):
            key = (el.get("type"), el.get("id"))
            if key in seen:
                continue
            seen.add(key)
            elements.append(el)
    return {"elements": elements,
            "osm3s": {"timestamp_osm_base": base}}, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="merge existing tiles only")
    ap.add_argument("--layers", nargs="*", default=["places", "rivers"])
    a = ap.parse_args()

    jobs = {"places": PLACES_Q, "rivers": RIVERS_Q}
    for layer in a.layers:
        if not a.merge:
            print("fetching %s over %d tiles ..." % (layer, len(tiles())), flush=True)
            fetch_all(layer, jobs[layer])
        payload, missing = merge(layer)
        out = os.path.join(DATA, "%s_himalaya.json" % layer)
        with open(out, "w") as f:
            json.dump(payload, f)
        print("%s: %d elements, %d tiles missing -> %s"
              % (layer, len(payload["elements"]), missing, out), flush=True)

        mpath = os.path.join(DATA, "manifest.json")
        man = json.load(open(mpath)) if os.path.exists(mpath) else {}
        man["%s_himalaya" % layer] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bbox": list(HIMALAYA),
            "elements": len(payload["elements"]),
            "tiles": len(tiles()), "tiles_missing": missing,
            "source": "OpenStreetMap via Overpass (ODbL)",
            "osm_base": payload["osm3s"]["timestamp_osm_base"],
            "note": ("waterway=river only; stream-tagged headwater channels are"
                     " absent" if layer == "rivers" else ""),
        }
        with open(mpath, "w") as f:
            json.dump(man, f, indent=2)


if __name__ == "__main__":
    main()
