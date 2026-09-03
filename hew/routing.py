"""
Downstream routing.

Answers the question the detector cannot: given a source event, which
settlements are on the water path below it, and how far down.

This is DELIBERATELY OFF THE TRIGGER PATH. Routing runs after a candidate
has already been scored; it changes who is told, never whether to alert.
That is why it can be built and iterated without re-opening the Phase 0 gate.

STATIC DATA, LOADED AT STARTUP. The river network and settlement list are
pinned local snapshots under data/. They are never fetched during a cycle.
A hazard alert must not depend on a third-party API being up at 08:37.
Refresh them deliberately with scripts/fetch_geodata.py.

Topology comes from OpenStreetMap way node ids, which give exact junctions.
Flow direction comes from the OSM convention that a waterway way is drawn in
the direction of flow, so geometry[0] is upstream of geometry[-1].

LIMITS -- read before trusting output:
  * OSM completeness varies. A missing way truncates the corridor, and a
    truncated corridor is a silent false negative. Settlements below a gap
    are simply not returned. Coverage is not verified by this module.
  * Distance is along-channel, not a travel time. No propagation model is
    applied here and none should be inferred (see CONSTRAINTS.md, no-ETA).
  * The corridor is a fixed lateral buffer, not a modelled inundation
    extent. A settlement outside it is NOT thereby safe.
"""

import json
import math
from array import array
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# Coarse spatial index. snap() and snap_candidates() scanned every vertex in
# the network: fine at 3,050 Trishuli ways, 2.66 M haversine calls once the
# layer became Himalaya-wide, which took a drill from well under a second to
# 17.5 s and made cloudflared return 502 before the page rendered.
#
# Bins hold WAY indices, not vertices. A per-vertex index would undo the
# memory work -- 2.66 M entries is exactly what was just removed from
# node_index -- while a per-way index is ~20 k ways over a few bins each.
_BIN = 0.05                       # ~5.5 km


def _bin_of(lat, lon):
    return (int(math.floor(lat / _BIN)), int(math.floor(lon / _BIN)))


class _Way:
    """
    One channel, stored compactly.

    The raw Overpass dicts cost ~360 bytes per coordinate: a {"lat":..,
    "lon":..} dict per vertex plus a full node-id list plus the tag dict.
    Across the Himalayan layer that is 2.66 M coordinates and 958 MB, which
    is 37% of a 4 GB Pi for one process. Parallel float arrays carry the
    same numbers in 16 bytes a vertex.

    Only `name`, the vertex coordinates, the vertex COUNT and the FINAL node
    id are ever read after construction, so nothing else is retained.
    """

    __slots__ = ("lat", "lon", "n_nodes", "last_node", "name")

    def __init__(self, w):
        g = w["geometry"]
        self.lat = array("d", [p["lat"] for p in g])
        self.lon = array("d", [p["lon"] for p in g])
        nodes = w["nodes"]
        self.n_nodes = len(nodes)
        self.last_node = nodes[-1]
        t = w.get("tags") or {}
        self.name = t.get("name:en") or t.get("name") or "unnamed channel"


class RiverNetwork:
    """OSM waterway ways indexed by node id for exact junction traversal."""

    def __init__(self, ways):
        raw = [w for w in ways if w.get("geometry") and w.get("nodes")]

        # The index is only ever QUERIED with a way's final node id
        # (_next_way is called as _next_way(w.last_node, wi)), so indexing
        # every one of 2.66 M vertices allocated a dict entry, a list and a
        # tuple for millions of nodes that can never be looked up. Index only
        # ids that terminate some way: 20 k keys instead of 2.66 M, and the
        # junction lookups are bit-for-bit the same.
        terminal = {w["nodes"][-1] for w in raw}

        self.ways = []
        self.node_index = {}
        for wi, w in enumerate(raw):
            for pos, nid in enumerate(w["nodes"]):
                if nid in terminal:
                    self.node_index.setdefault(nid, []).append((wi, pos))
            self.ways.append(_Way(w))
        self._build_index()

    def _build_index(self):
        """bin -> way indices whose geometry touches that bin."""
        self.bins = {}
        for wi, w in enumerate(self.ways):
            seen = set()
            for i in range(len(w.lat)):
                b = _bin_of(w.lat[i], w.lon[i])
                if b not in seen:
                    seen.add(b)
                    self.bins.setdefault(b, []).append(wi)

    def _nearby_ways(self, lat, lon, rings):
        b0, b1 = _bin_of(lat, lon)
        out = set()
        for i in range(b0 - rings, b0 + rings + 1):
            for j in range(b1 - rings, b1 + rings + 1):
                out.update(self.bins.get((i, j), ()))
        return out

    @classmethod
    def load(cls, path=None):
        """
        Prefers the region-wide network. The Trishuli-basin file was fetched
        for the founding event and covers lon 84.64-86.00 only; Khumbu,
        Rolwaling and Kanchenjunga fell outside it, so drills there warned
        and then showed no corridor at all.

        The same argument applied again: the region file covers 27-30 N,
        84-89 E, so Uttarakhand, Himachal, Ladakh, Bhutan and Arunachal had
        no corridor either. Widest available wins.

        Prefers the .bin form of whichever layer wins. Parsing the JSON
        materialises 2.66 M coordinate dicts before one is converted, and
        the allocator keeps that high-water mark: 921 MB retained for ~200 MB
        of live data. The .bin carries the same numbers as packed float64,
        so no per-vertex Python object is ever created.
        """
        if path is None:
            for candidate in ("rivers_himalaya.bin", "rivers_himalaya.json",
                              "rivers_region.bin", "rivers_region.json",
                              "rivers_trishuli.json"):
                path = os.path.join(DATA_DIR, candidate)
                if os.path.exists(path):
                    break
        if path.endswith(".bin"):
            return cls.from_compact(path)
        with open(path) as f:
            return cls(json.load(f)["elements"])

    @classmethod
    def from_compact(cls, path):
        """Load the packed form written by scripts/compact_rivers.py."""
        self = cls.__new__(cls)
        with open(path, "rb") as f:
            header = json.loads(f.readline().decode())["ways"]
            total = sum(h["n_coords"] for h in header)
            lats, lons = array("d"), array("d")
            lats.fromfile(f, total)
            lons.fromfile(f, total)

        self.ways = []
        self.node_index = {}
        off = 0
        for wi, h in enumerate(header):
            n = h["n_coords"]
            w = _Way.__new__(_Way)
            w.lat = lats[off:off + n]
            w.lon = lons[off:off + n]
            w.n_nodes = h["n_nodes"]
            w.last_node = h["last_node"]
            w.name = h["name"]
            self.ways.append(w)
            off += n
            # _next_way looks up a way's final node id and wants every OTHER
            # way where that id sits before its own last vertex. The compact
            # header carries exactly those pairs, so the index it rebuilds is
            # identical to the one the JSON path builds, minus the millions
            # of entries that could never be queried.
            for nid, pos in h["junctions"]:
                self.node_index.setdefault(nid, []).append((wi, pos))
        self._build_index()
        return self

    def name(self, wi):
        return self.ways[wi].name

    def snap(self, lat, lon):
        """Nearest network vertex. Returns (way_index, position, distance_km)."""
        best = (None, None, float("inf"))
        # Grow the search box until the best hit found is closer than the box
        # edge; only then is it provably the global nearest. Falls back to the
        # full scan if the network is empty around this point.
        for rings in (1, 2, 4, 8, 16, 32):
            for wi in self._nearby_ways(lat, lon, rings):
                w = self.ways[wi]
                wlat, wlon = w.lat, w.lon
                for pos in range(len(wlat)):
                    d = haversine_km(lat, lon, wlat[pos], wlon[pos])
                    if d < best[2]:
                        best = (wi, pos, d)
            if best[0] is not None and best[2] <= rings * _BIN * 111.0:
                return best
        return best

    def _next_way(self, node_id, from_way):
        """
        Continue downstream through a junction.

        A way is a valid continuation only if our node is not its final
        vertex -- there must be channel left below it in flow direction.
        A tributary ENDING at this node fails that test, which is what
        keeps the trace on the trunk instead of walking up a side stream.
        """
        cands = [(wi, pos) for wi, pos in self.node_index.get(node_id, [])
                 if wi != from_way and pos < self.ways[wi].n_nodes - 1]
        if not cands:
            return None
        same = [c for c in cands if self.name(c[0]) == self.name(from_way)]
        pool = same or cands
        # Prefer the larger channel: more remaining vertices below the junction.
        return max(pool, key=lambda c: self.ways[c[0]].n_nodes - c[1])

    def trace(self, lat, lon, max_km=200.0, max_snap_km=10.0):
        """
        Walk the channel downstream from the nearest vertex.

        Returns a list of {lat, lon, river_km, channel} in flow order.
        river_km here is along-channel distance from the SNAP POINT.
        trace_branches() re-bases it on the source so that distances are
        comparable between branches; it keeps the raw value as channel_km.

        If the nearest channel is further than max_snap_km the trace is
        refused and an empty path is returned. A corridor derived from a
        source that is nowhere near the routed basin would be confidently
        wrong, and a wrong corridor is more dangerous than no corridor.
        """
        wi, pos, snap_km = self.snap(lat, lon)
        if wi is None or snap_km > max_snap_km:
            return [], (round(snap_km, 2) if wi is not None else None)
        path, seen, total = [], set(), 0.0
        prev = None
        while wi is not None and total < max_km:
            if wi in seen:
                break
            seen.add(wi)
            w = self.ways[wi]
            wlat, wlon, nm = w.lat, w.lon, w.name
            for i in range(pos, len(wlat)):
                la, lo = wlat[i], wlon[i]
                if prev is not None:
                    total += haversine_km(prev[0], prev[1], la, lo)
                prev = (la, lo)
                path.append({"lat": la, "lon": lo,
                             "river_km": round(total, 2), "channel": nm})
            nxt = self._next_way(w.last_node, wi)
            if nxt is None:
                break
            wi, pos = nxt
        return path, round(snap_km, 2)


def snap_candidates(net, lat, lon, radius_km):
    """
    Every distinct channel whose nearest vertex lies within radius_km.

    One candidate per named channel, at its closest vertex. Near a
    confluence a single point can sit within the error radius of two
    different drainages, and which one the debris entered decides the
    whole corridor.
    """
    best = {}
    # Everything within radius_km lies inside this many bins, so ways outside
    # it cannot contribute a candidate. Exact, not approximate.
    rings = int(radius_km / (_BIN * 111.0)) + 1
    for wi in net._nearby_ways(lat, lon, rings):
        w = net.ways[wi]
        nm = w.name
        wlat, wlon = w.lat, w.lon
        for pos in range(len(wlat)):
            d = haversine_km(lat, lon, wlat[pos], wlon[pos])
            if d <= radius_km and (nm not in best or d < best[nm][2]):
                best[nm] = (wi, pos, d)
    return sorted(best.values(), key=lambda c: c[2])


def trace_branches(net, lat, lon, uncertainty_km=0.0, max_branches=6,
                   min_branch_km=3.0, **kw):
    """
    Trace every channel the source could plausibly have entered.

    With uncertainty_km = 0 this is the single-branch behaviour of trace().
    Above zero it returns the UNION of candidate corridors. That is the
    fail-safe reading of a location error: over-warning is recoverable,
    routing a flood down the wrong branch is not.

    Branches fully contained in another branch are dropped -- tributaries
    converge, and the shared trunk should not be reported twice.
    """
    def _rebase(path, snap):
        """
        river_km counts from the SOURCE, not from where the branch met water.
        Applied on every path this function returns -- an earlier version
        re-based only the multi-branch case, so the same call returned
        distances on two different bases depending on uncertainty_km, and a
        fixed max_river_km cap then truncated them at different places.
        """
        for p in path:
            p["channel_km"] = p["river_km"]
            p["river_km"] = round(p["river_km"] + snap, 2)
        return path

    if uncertainty_km <= 0:
        path, snap = net.trace(lat, lon, **kw)
        if not path:
            return []
        return [{"name": path[0]["channel"], "snap_km": snap,
                 "origin_offset_km": snap, "path": _rebase(path, snap)}]

    out = []
    for wi, pos, d in snap_candidates(net, lat, lon, uncertainty_km)[:max_branches]:
        path, _ = net.trace(net.ways[wi].lat[pos],
                            net.ways[wi].lon[pos], **kw)
        if path and path[-1]["river_km"] >= min_branch_km:
            # Re-base river_km on the SOURCE, not on this branch's own snap
            # point. Branches snap at different distances -- here 5.9 km and
            # 10.3 km -- so numbering each from its own zero made "0.0 km"
            # mean two places 10 km apart, and made the public distance
            # bands meaningless. The offset includes the unmodelled overland
            # reach, which is the honest accounting: it is distance from the
            # source, not distance from where we guessed it met water.
            _rebase(path, d)
            out.append({"name": path[0]["channel"], "snap_km": round(d, 2),
                        "origin_offset_km": round(d, 2), "path": path})

    out.sort(key=lambda b: b["snap_km"])
    kept = []
    for b in out:
        pts = {(round(p["lat"], 4), round(p["lon"], 4)) for p in b["path"]}
        if any(len(pts - {(round(p["lat"], 4), round(p["lon"], 4))
                          for p in k["path"]}) < 0.05 * len(pts) for k in kept):
            continue                                  # subsumed by a kept branch
        kept.append(b)
    return kept


def merge_branches(branches):
    """
    Flatten branches to one vertex list, keeping the SHORTEST river_km for
    any vertex two branches share, and the branch that reaches it first.
    Branches converge below a confluence, so this keeps exposure scoring
    close to single-branch cost instead of multiplying it.
    """
    merged = {}
    for b in branches:
        for p in b["path"]:
            k = (round(p["lat"], 4), round(p["lon"], 4))
            if k not in merged or p["river_km"] < merged[k]["river_km"]:
                merged[k] = {**p, "branch": b["name"]}
    return sorted(merged.values(), key=lambda p: p["river_km"])


def load_settlements(path=None):
    """Region-wide by default, for the same reason as the river network."""
    if path is None:
        for candidate in ("places_himalaya.json", "places_region.json",
                          "places_trishuli.json"):
            path = os.path.join(DATA_DIR, candidate)
            if os.path.exists(path):
                break
    with open(path) as f:
        out = []
        for e in json.load(f)["elements"]:
            t = e.get("tags", {})
            nm = t.get("name:en") or t.get("name")
            if not nm:
                continue
            pop = t.get("population")
            try:
                pop = int(pop) if pop else None
            except ValueError:
                pop = None
            out.append({"name": nm, "lat": e["lat"], "lon": e["lon"],
                        "kind": t.get("place"), "population": pop})
        return out


def _is_upstream_of_start(s, path):
    """
    True if the settlement sits BEHIND the first vertex, not below it.

    A trace begins where the source's uncertainty circle meets water, and
    settlements laterally near that first point get matched to it even when
    they are upstream. They are not in the flow path at all. Four Tibetan
    villages beside the Lende Khola snap point were being reported as
    downstream of a collapse 10 km away, which for an alert is telling people
    above the source that water is coming at them.

    Projects the settlement onto the first segment's direction of travel: a
    negative projection means it is behind the start.
    """
    if len(path) < 2:
        return False
    a, b = path[0], path[1]
    # local flat approximation is fine over a few km
    kx = math.cos(math.radians(a["lat"]))
    fx, fy = (b["lon"] - a["lon"]) * kx, b["lat"] - a["lat"]
    sx, sy = (s["lon"] - a["lon"]) * kx, s["lat"] - a["lat"]
    n = math.hypot(fx, fy)
    if n == 0:
        return False
    return (sx * fx + sy * fy) / n < 0


# Observed medians from the OSM population tags we do have, by place class.
# n is tiny (16-22 per class) and the ranges overlap by two orders of
# magnitude -- a "hamlet" in this dataset runs from 10 to 18,000 people.
# These are used ONLY to state a rough band for places with no figure, never
# to put a number against an individual settlement.
CLASS_MEDIAN = {"city": 19400, "town": 24644, "village": 200, "hamlet": 300}


def population_summary(settlements):
    """
    Honest accounting of how many people are in a corridor.

    OSM records population for about 1.6% of places here, so any single
    total is mostly invented. This returns what is actually known, what is
    not, and a rough band -- never a point estimate dressed as a count.
    """
    recorded = [s for s in settlements if s.get("population")]
    total = sum(s["population"] for s in recorded)
    unknown = [s for s in settlements if not s.get("population")]
    lo = hi = 0
    for s in unknown:
        m = CLASS_MEDIAN.get(s.get("kind"), 300)
        lo += m // 3
        hi += m * 3
    return {
        "recorded": total,
        "recorded_places": len(recorded),
        "unknown_places": len(unknown),
        "estimated_low": total + lo,
        "estimated_high": total + hi,
        "caveat": f"{len(recorded)} of {len(settlements)} places carry a "
                  f"population tag. The band spans class medians divided and "
                  f"multiplied by three, because OSM place classes overlap by "
                  f"two orders of magnitude here.",
    }


def _place_grid(settlements, cell=0.05):
    g = {}
    for s in settlements:
        g.setdefault((int(s["lat"] / cell), int(s["lon"] / cell)), []).append(s)
    return g, cell


def exposed_settlements(path, settlements, corridor_km=2.0, max_river_km=150.0,
                        _grid=None):
    """
    Settlements within corridor_km of the traced channel, ranked by distance
    downstream.

    max_river_km is measured on whatever basis the path carries. After
    trace_branches() re-bases river_km onto the SOURCE, the cap means "150 km
    below the source", not "150 km of channel" -- so a branch that meets the
    water 13 km out routes 13 km less channel than one that meets it at 6 km.
    That is the intended reading: the question is how far downstream of the
    event a settlement is, not how much river was walked. The corridor is a lateral buffer, NOT a modelled inundation
    extent -- absence from this list is not evidence of safety.

    Settlements that match the FIRST vertex but lie upstream of it are
    excluded: they are beside the start of the trace, not below it.

    Walks the PATH against a grid of settlements rather than every settlement
    against every vertex. The naive form was O(vertices x settlements); once
    the network covered the whole domain that was 281k x 4.7k and took over
    ten seconds for a single event.
    """
    grid, cell = _grid if _grid else _place_grid(settlements)
    span = int(corridor_km / 111.0 / cell) + 1
    best = {}
    for i, p in enumerate(path):
        if p["river_km"] > max_river_km:
            break
        gy, gx = int(p["lat"] / cell), int(p["lon"] / cell)
        for dy in range(-span, span + 1):
            for dx in range(-span, span + 1):
                for s in grid.get((gy + dy, gx + dx), ()):
                    d = haversine_km(s["lat"], s["lon"], p["lat"], p["lon"])
                    if d > corridor_km:
                        continue
                    k = (s["name"], s["lat"], s["lon"])
                    if k not in best or d < best[k][0]:
                        best[k] = (d, p, i, s)
    out = []
    for d, p, i, s in best.values():
        if i == 0 and _is_upstream_of_start(s, path):
            continue
        out.append({**s, "river_km": p["river_km"],
                    "offset_km": round(d, 2), "channel": p["channel"]})
    out.sort(key=lambda s: s["river_km"])
    return out


def exposed_settlements_union(branches, settlements, corridor_km=2.0,
                              max_river_km=150.0):
    """
    Exposure across every candidate branch.

    Runs exposure PER BRANCH and merges the results, rather than merging the
    paths first. That matters: the upstream-of-start filter keys on vertex
    index 0, and in a merged path each branch's start is buried mid-list, so
    the filter silently never fired. Four villages upstream of the Lende
    snap point were reported as downstream of a collapse 10 km away.

    A settlement reached by more than one branch is reported once, at its
    shortest along-channel distance, tagged with the branch that reaches it
    first. Same caveats as exposed_settlements: the corridor is a lateral
    buffer, and absence from it is not evidence of safety.
    """
    best = {}
    grid = _place_grid(settlements)          # built once for all branches
    for b in branches:
        for s in exposed_settlements(b["path"], settlements, corridor_km,
                                     max_river_km, _grid=grid):
            key = (s["name"], round(s["lat"], 4), round(s["lon"], 4))
            if key not in best or s["river_km"] < best[key]["river_km"]:
                best[key] = {**s, "branch": b["name"]}
    return sorted(best.values(), key=lambda s: s["river_km"])
