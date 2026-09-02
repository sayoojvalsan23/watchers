"""
Refresh the pinned river-network and settlement snapshots.

RUN THIS DELIBERATELY, NEVER FROM THE WATCHER. Routing data is static
reference data: river courses and settlements change on a scale of years,
not seconds. The watcher loads a local snapshot at startup so that a hazard
alert never depends on a third-party API being reachable at the moment of
the event.

    python3 scripts/fetch_geodata.py --basin trishuli

Writes data/rivers_<basin>.json and data/places_<basin>.json, plus a
manifest recording when each snapshot was taken and what bbox it covers.
Source: OpenStreetMap via Overpass (ODbL). Cadence: annually, or after a
major flood that is known to have changed the channel.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# Overpass 504s and 429s under load. Mirrors are tried in order; this is a
# maintenance script, so being slow and stubborn is the right trade.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

BASINS = {
    # name: (south, west, north, east)
    "trishuli": (27.55, 84.85, 28.45, 85.95),
    # Kerala, for the Phase 5b rainfall watch. Covers the Western Ghats and
    # the whole downstream plain, because debris from a Ghats slope travels
    # down river valleys to settlements well away from the initiation point --
    # Chooralmala's dead were at the valley bottom, not on the scarp.
    #
    # Roughly 12x the area of the Trishuli box, so Overpass needs the longer
    # timeout and will sometimes still 504. It is a maintenance script; retry.
    "kerala": (8.15, 74.85, 12.85, 77.45),
}

# Kerala needs STREAMS as well as rivers, and that is not a preference.
# Around Chooralmala, 27 of 30 mapped waterways are waterway=stream and the
# nearest waterway=river is 3.5 km away -- so a river-only query routes the
# founding case to nothing at all. Debris flows START in headwater channels;
# in the Ghats those are streams. Nepal's glacial valleys happened to have
# their channels tagged river, which is why this went unnoticed.
RIVERS_Q = ('[out:json][timeout:900];'
            'way["waterway"~"^(river|stream)$"]({s},{w},{n},{e});out geom;')
RIVERS_Q_RIVERONLY = ('[out:json][timeout:600];'
                      'way["waterway"="river"]({s},{w},{n},{e});out geom;')
PLACES_Q = ('[out:json][timeout:600];'
            '(node["place"~"^(city|town|village|hamlet)$"]({s},{w},{n},{e}););out body;')


# Overpass rejects urllib's default User-Agent with HTTP 406.
UA = "hew-geodata/1.0 (Himalayan Early Warning; static reference data refresh)"


def query(q, attempts=3):
    data = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for attempt in range(attempts):
        for url in MIRRORS:
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.loads(r.read().decode())
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                last = f"{url}: {e}"
                print(f"    retry ({last})", flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"all Overpass mirrors failed; last error {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--basin", default="trishuli", choices=sorted(BASINS))
    a = ap.parse_args()
    s, w, n, e = BASINS[a.basin]
    box = {"s": s, "w": w, "n": n, "e": e}
    os.makedirs(DATA, exist_ok=True)

    manifest_path = os.path.join(DATA, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            manifest = json.load(f)

    for label, q in (("rivers", RIVERS_Q), ("places", PLACES_Q)):
        print(f"fetching {label} for {a.basin} ...", flush=True)
        payload = query(q.format(**box))
        path = os.path.join(DATA, f"{label}_{a.basin}.json")
        with open(path, "w") as f:
            json.dump(payload, f)
        manifest[f"{label}_{a.basin}"] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "bbox": [s, w, n, e],
            "elements": len(payload.get("elements", [])),
            "source": "OpenStreetMap via Overpass (ODbL)",
            "osm_base": payload.get("osm3s", {}).get("timestamp_osm_base"),
        }
        print(f"  {len(payload.get('elements', []))} elements -> {path}")

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
