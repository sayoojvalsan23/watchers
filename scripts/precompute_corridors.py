"""
Precompute the downstream corridor for every rain-watch node.

WHY THIS EXISTS
---------------
The Kerala waterway network is 96,553 ways and costs 862 MB once parsed into
Python. The Raspberry Pi running the watcher has ~790 MB free, so loading it
there would OOM the box and take the seismic watcher down with it. Pruning to
channels near a watch cell only reached 66% -- watch cells cover the whole
Ghats, so nearly every stream is near one.

But the corridor below a fixed node does not change between polls. River
courses move on a scale of years; the watcher polls every three hours. So the
routing is precomputed ONCE here, on a machine with memory, and the device
ships a small lookup table instead of a river network.

    python3 scripts/precompute_corridors.py            all nodes, resumable
    python3 scripts/precompute_corridors.py --limit 50

Writes data/kerala_corridors.json. Re-run after refreshing the waterway
snapshot, never from the watcher.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hew import rain_watch, rain_watcher, routing

OUT = os.path.join(routing.DATA_DIR, "kerala_corridors.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-settlements", type=int, default=40)
    a = ap.parse_args()

    cells, _ = rain_watch.load_cells()
    nodes = rain_watcher.build_groups(cells)
    if a.limit:
        nodes = nodes[:a.limit]

    done = {}
    if os.path.exists(OUT):
        done = {k: v for k, v in json.load(open(OUT))["corridors"].items()}
    net = routing.RiverNetwork.load(
        os.path.join(routing.DATA_DIR, rain_watcher.KERALA_RIVERS))
    places = routing.load_settlements(
        os.path.join(routing.DATA_DIR, rain_watcher.KERALA_PLACES))
    print(f"{len(nodes)} nodes, {len(done)} already done", flush=True)

    t0 = time.time()
    for i, g in enumerate(nodes):
        key = f"{g['lat']:.4f},{g['lon']:.4f}"
        if key in done:
            continue
        try:
            br = routing.trace_branches(net, g["lat"], g["lon"],
                                        uncertainty_km=rain_watcher.ROUTE_UNCERTAINTY_KM)
            ex = routing.exposed_settlements_union(
                br, places, corridor_km=rain_watcher.ROUTE_CORRIDOR_KM)
            done[key] = [{"name": s["name"], "river_km": round(s["river_km"], 1),
                          "lat": s["lat"], "lon": s["lon"],
                          "population": s.get("population")}
                         for s in ex[:a.max_settlements]]
        except Exception as e:
            done[key] = []
            print(f"  {key}: routing failed ({e})", flush=True)
        if (i + 1) % 20 == 0:
            _save(done, nodes)
            print(f"  {i+1}/{len(nodes)}  ({time.time()-t0:.0f}s)", flush=True)
    _save(done, nodes)
    n_pop = sum(1 for v in done.values() if v)
    print(f"DONE {len(done)} nodes, {n_pop} with settlements downstream")


def _save(done, nodes):
    json.dump({
        "source": "OSM waterways (river+stream) and places, via Overpass",
        "corridor_km": rain_watcher.ROUTE_CORRIDOR_KM,
        "uncertainty_km": rain_watcher.ROUTE_UNCERTAINTY_KM,
        "note": "Precomputed so the device needs no river network in memory. "
                "The 96,553-way Kerala network costs 862 MB parsed; the Pi has "
                "~790 MB free. River courses change on a scale of years -- "
                "re-run this script after refreshing the snapshot, never from "
                "the watcher.",
        "nodes": len(nodes), "corridors": done},
        open(OUT, "w"))


if __name__ == "__main__":
    main()
