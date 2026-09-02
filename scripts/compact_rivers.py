"""
Convert an Overpass river layer into a compact binary the loader can mmap-read.

Slimming RiverNetwork's RETAINED structures was not enough. Parsing
rivers_himalaya.json materialises 2.66 M {"lat":..,"lon":..} dicts before a
single one is converted, and Python's allocator does not return that
high-water mark to the OS: measured 921 MB retained for ~200 MB of live data.
The fix is not to build the dicts at all.

Format: one JSON header line (way names + counts + final node ids), then all
latitudes as packed float64, then all longitudes. Loading is two array.fromfile
calls and some slicing -- no per-vertex Python objects are ever created.

    python3 scripts/compact_rivers.py data/rivers_himalaya.json
"""

import argparse
import json
import os
from array import array


def convert(src, dst):
    with open(src) as f:
        elements = json.load(f)["elements"]
    ways = [w for w in elements if w.get("geometry") and w.get("nodes")]

    # A junction is where another way's FINAL node appears part-way along
    # this one. Dropping per-vertex node ids entirely truncated every trace
    # (Kedarnath 2271 vertices -> 197), because _next_way had nothing to
    # look up. Only these pairs are needed, and there are few of them.
    terminal = {w["nodes"][-1] for w in ways}

    header, lats, lons = [], array("d"), array("d")
    for w in ways:
        g = w["geometry"]
        t = w.get("tags") or {}
        nodes = w["nodes"]
        header.append({
            "name": t.get("name:en") or t.get("name") or "unnamed channel",
            "n_coords": len(g),
            "n_nodes": len(nodes),
            "last_node": nodes[-1],
            "junctions": [[nid, pos] for pos, nid in enumerate(nodes)
                          if nid in terminal and pos < len(nodes) - 1],
        })
        lats.extend(p["lat"] for p in g)
        lons.extend(p["lon"] for p in g)

    with open(dst, "wb") as f:
        f.write((json.dumps({"ways": header}) + "\n").encode())
        lats.tofile(f)
        lons.tofile(f)
    return len(ways), len(lats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    dst = a.out or os.path.splitext(a.src)[0] + ".bin"
    n_ways, n_coords = convert(a.src, dst)
    print("%d ways, %d coordinates -> %s (%.1f MB)"
          % (n_ways, n_coords, dst, os.path.getsize(dst) / 1e6))


if __name__ == "__main__":
    main()
