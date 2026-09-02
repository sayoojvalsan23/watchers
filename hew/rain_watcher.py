"""
The rainfall watch service — polls, decides, records, notifies.

    python -m hew.rain_watcher --once           one cycle, then exit
    python -m hew.rain_watcher                  continuous
    python -m hew.rain_watcher --allow-dispatch actually notify

WHAT MAKES THIS DIFFERENT FROM THE SEISMIC WATCHER
---------------------------------------------------
The seismic watcher polls every 60 s because a collapse is instantaneous and
the only currency is latency. Rainfall accumulates over 12-72 h, so a 3-hourly
cycle loses nothing -- and the feed is a free service with a quota, which the
seismic feed is not.

THE QUOTA IS A DESIGN CONSTRAINT, NOT A DETAIL
-----------------------------------------------
The terrain scan produced 1,552 watch cells at 4 km spacing. Polling all of
them 8x a day is 12,416 requests against a 10,000/day allowance -- the service
would die every afternoon. Open-Meteo also prices by LOCATION, not by HTTP
call, so batching coordinates saves round-trips and no quota at all.

Two things make it fit:

  GROUPING. The forecast model's grid was measured at ~0.07 deg lat by
  ~0.16 deg lon (about 8 x 17 km) near Wayanad -- finer than ERA5's 25 km, so
  neighbouring cells do NOT return identical values and cannot be collapsed
  carelessly. Cells are therefore snapped to a lattice matched to the OBSERVED
  grid, and one poll serves every watch cell that snaps to the same node.

  LAZY CLIMATOLOGY. Each node needs a ten-year hourly ladder, which is the
  expensive part. Almost no node ever approaches its threshold, so the ladder
  is fetched only when raw accumulation clears a cheap absolute pre-filter.
  A dry node costs one forecast call per cycle and nothing else, ever.

LATCHING WITHOUT PERSISTED STATE
---------------------------------
A raised watch must hold after the rain eases (see rain_watch.latch). The
obvious implementation stores latch state in the database, which then drifts
from reality on restart, on a missed cycle, or on a clock change. Instead each
cycle fetches the last several days of hourly data anyway, recomputes the
whole run of assessments, and applies the latch to that. State is DERIVED from
data every time, so a restart cannot lose or invent a watch.

DISPATCH IS OFF BY DEFAULT
---------------------------
Same rule as Phase 1. This raises a WATCH at roughly 12 alerts/year against a
gate of 2; that is a defensible attention product and not an evacuation
trigger, and the decision to send it to anyone is not the software's to make.
"""

import argparse
import json
import logging
import math
import os
import signal
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import rain_watch
from .store import Store

log = logging.getLogger("hew.rain_watcher")

FORECAST = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "hew-rainwatch/1.0 (Himalayan Early Warning; Phase 5b)"}

# Matched to the observed model grid near Wayanad (~0.07 lat x ~0.16 lon).
# Snapping finer than the model resolves nothing and costs quota; snapping
# coarser throws away real spatial variation.
GROUP_LAT = 0.07
GROUP_LON = 0.16

# Coordinates per HTTP request. Quota is charged per location regardless, so
# this only reduces round-trips.
POLL_BATCH = 50

# Cheap gate before spending a ten-year archive on a node. Set below the
# smallest floor in rain_watch so it can never hide a real watch.
CLIM_PREFILTER_MM_72H = 40.0

DEFAULT_INTERVAL = 3 * 3600
PAST_DAYS = 7

# Routing runs ONLY for nodes that raise a watch, never for all of them.
# Loading the Kerala waterway snapshot takes ~3 s and tracing a single node
# ~5 s; 316 nodes would be 26 minutes a cycle. Routing is a downstream
# product, not part of the decision, so it belongs off the trigger path --
# same rule as the seismic side.
ROUTE_CORRIDOR_KM = 2.5
ROUTE_UNCERTAINTY_KM = 2.0
KERALA_RIVERS = "rivers_kerala.json"
KERALA_PLACES = "places_kerala.json"
# Precomputed corridors, one per node. PREFERRED over live routing: the
# Kerala network costs 862 MB parsed and the Pi has ~790 MB free, so loading
# it on the device would OOM the box and take the seismic watcher with it.
# Corridors below a fixed node do not change between three-hourly polls --
# river courses move on a scale of years -- so they are computed once by
# scripts/precompute_corridors.py and shipped as a lookup table.
KERALA_CORRIDORS = "kerala_corridors.json"
# Every cycle writes what it actually SAW, not just what crossed a threshold.
# Without this the dashboard can say "watching 316 places" and show a reader
# no rain at all, which is the least useful honest page imaginable. Only the
# wettest nodes are kept: 316 rows every 3 h is noise, and the question a
# reader has is "where is it raining hardest right now".
CONDITIONS_FILE = "latest_conditions.json"
CONDITIONS_TOP_N = 25


def group_key(lat, lon):
    return (round(lat / GROUP_LAT) * GROUP_LAT,
            round(lon / GROUP_LON) * GROUP_LON)


def build_groups(cells):
    """
    Collapse watch cells onto the model grid.

    Returns [{lat, lon, cells, band, relief_m}] where lat/lon is the node and
    `band` is the WORST band among the cells it serves -- a node speaks for
    its steepest ground, never its gentlest.
    """
    g = {}
    order = {"steep": 2, "moderate": 1, "gentle": 0, "flat": 0}
    for c in cells:
        k = group_key(c["lat"], c["lon"])
        e = g.setdefault(k, {"lat": round(k[0], 4), "lon": round(k[1], 4),
                             "cells": [], "band": "flat", "relief_m": 0.0})
        e["cells"].append(c)
        if order.get(c["band"], 0) > order.get(e["band"], 0):
            e["band"] = c["band"]
        e["relief_m"] = max(e["relief_m"], c.get("relief_m") or 0.0)
    return sorted(g.values(), key=lambda x: (-x["relief_m"]))


def fetch_many(points, past_days=PAST_DAYS, timeout=90):
    """Hourly precipitation for many coordinates. Returns [(times, series)]."""
    out = []
    for i in range(0, len(points), POLL_BATCH):
        chunk = points[i:i + POLL_BATCH]
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{a:.4f}" for a, _ in chunk),
            "longitude": ",".join(f"{b:.4f}" for _, b in chunk),
            "hourly": "precipitation", "past_days": past_days,
            "forecast_days": 1, "timezone": "Asia/Kolkata"})
        req = urllib.request.Request(f"{FORECAST}?{q}", headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        items = d if isinstance(d, list) else [d]
        if len(items) != len(chunk):
            raise RuntimeError(f"forecast API returned {len(items)} series "
                               f"for {len(chunk)} coordinates")
        for it in items:
            out.append((it["hourly"]["time"],
                        [x or 0.0 for x in it["hourly"]["precipitation"]]))
        if i + POLL_BATCH < len(points):
            time.sleep(1.0)
    return out


class RainWatcher:

    def __init__(self, store, groups, allow_dispatch=False, dispatcher=None,
                 cfg=None, interval=DEFAULT_INTERVAL, route=True):
        self.store = store
        self.groups = groups
        self.allow_dispatch = allow_dispatch
        self.dispatcher = dispatcher
        self.cfg = cfg or {}
        self.interval = interval
        self.route = route
        self._net = self._places = None
        self._corr = None
        self._running = True

    def _corridors(self):
        """The precomputed lookup, if present. Cheap: a few hundred kB."""
        if self._corr is None:
            import os as _os
            from . import routing as _r
            path = _os.path.join(_r.DATA_DIR, KERALA_CORRIDORS)
            try:
                with open(path) as f:
                    self._corr = json.load(f)["corridors"]
                log.info("corridors loaded: %d nodes", len(self._corr))
            except (OSError, ValueError, KeyError):
                self._corr = {}
        return self._corr

    def _geo(self):
        """Load the full routing snapshot. 862 MB -- never on the device."""
        if self._net is None and self.route:
            from . import routing
            import os as _os
            self._net = routing.RiverNetwork.load(
                _os.path.join(routing.DATA_DIR, KERALA_RIVERS))
            self._places = routing.load_settlements(
                _os.path.join(routing.DATA_DIR, KERALA_PLACES))
            log.info("routing loaded: %d ways, %d places",
                     len(self._net.ways), len(self._places))
        return self._net, self._places

    def downstream(self, g, decision_id=None):
        """
        Who is below this node. Off the trigger path, so a routing failure
        degrades the product without touching the decision.

        The Kerala snapshot deliberately includes waterway=stream: around
        Chooralmala 27 of 30 mapped channels are streams and the nearest
        mapped river is 3.5 km away, so a river-only network routes the
        founding case to nothing.
        """
        # Precomputed first. Live routing is a developer fallback only.
        hit = self._corridors().get(f"{g['lat']:.4f},{g['lon']:.4f}")
        if hit is not None:
            if decision_id is not None and hit:
                try:
                    self.store.record_impact(decision_id, hit)
                except Exception as e:
                    log.error("impact not recorded: %s", e)
            return hit
        try:
            from . import routing
            net, places = self._geo()
            if net is None:
                return []
            br = routing.trace_branches(net, g["lat"], g["lon"],
                                        uncertainty_km=ROUTE_UNCERTAINTY_KM)
            ex = routing.exposed_settlements_union(br, places,
                                                   corridor_km=ROUTE_CORRIDOR_KM)
            if decision_id is not None and ex:
                self.store.record_impact(decision_id, ex)
            return ex
        except Exception as e:
            log.error("routing failed for %.3f,%.3f: %s", g["lat"], g["lon"], e)
            return []

    def stop(self, *a):
        self._running = False

    # -- one cycle ---------------------------------------------------------

    def cycle(self):
        pts = [(g["lat"], g["lon"]) for g in self.groups]
        try:
            series = fetch_many(pts)
        except Exception as e:
            log.error("rain feed failed: %s", e)
            self.store.heartbeat("rain", False, str(e)[:200])
            return []

        hold = rain_watch.DEFAULTS["hold_hours"]
        raised = []
        seen = []
        for g, (times, vals) in zip(self.groups, series):
            n = len(vals)
            if n < 73:
                continue
            # 72 h total, the cheap pre-filter that keeps the ten-year
            # climatology from being fetched for a dry node.
            recent72 = sum(vals[-72:])
            seen.append({"lat": g["lat"], "lon": g["lon"], "band": g["band"],
                         "relief_m": g["relief_m"], "at": times[n - 1],
                         "mm_3h": round(sum(vals[-3:]), 1),
                         "mm_24h": round(sum(vals[-24:]), 1),
                         "mm_72h": round(recent72, 1),
                         "places": [x["name"] for x in
                                    (self._corridors().get(
                                        "%.4f,%.4f" % (g["lat"], g["lon"])) or [])[:3]]})
            if recent72 < CLIM_PREFILTER_MM_72H:
                continue
            try:
                clim = rain_watch.climatology(g["lat"], g["lon"])
            except Exception as e:
                log.warning("climatology unavailable for %.3f,%.3f: %s",
                            g["lat"], g["lon"], e)
                continue

            # Recompute the whole recent run and latch it. State is derived
            # from data, never persisted -- a restart cannot lose a watch.
            run = []
            for i in range(max(0, n - hold - 2), n):
                run.append(rain_watch.assess(
                    rain_watch.accumulations_at(vals, i), clim, g, self.cfg))
            latched = rain_watch.latch(run, self.cfg)
            a, at = latched[-1], times[n - 1]
            did = self._record(g, a, at, recent72)
            if a["tier"] == "watch":
                ex = self.downstream(g, did) if self.route else []
                self._notify(g, a, ex)
                raised.append((g, a, at, ex))

        self._write_conditions(seen, len(raised))
        self.store.heartbeat("rain", True,
                             f"{len(self.groups)} nodes, {len(raised)} watching")
        return raised

    def _notify(self, g, a, ex):
        """
        Push a watch to the operator's phone. Never raises.

        Operator awareness is not public dispatch: --allow-dispatch gates
        telling VILLAGES something, which is an institutional decision. This
        tells the person running the system that their detector fired, the
        same way the fault alarm does.
        """
        try:
            from . import hew_operator as operator
            if not operator.configured():
                return
            names = [s["name"] for s in ex]
            operator.rain_watch(a.get("driving_percentile") or 0.0,
                                a.get("driving_window_h"), g["band"],
                                names, max(0, len(names) - 3))
        except Exception as e:
            log.error("operator push failed: %s", e)

    def _write_conditions(self, seen, n_watching):
        """What this cycle saw, for the dashboard. Never breaks the cycle."""
        try:
            from . import routing as _r
            wet = sorted(seen, key=lambda x: -x["mm_24h"])[:CONDITIONS_TOP_N]
            doc = {"at": datetime.now(timezone.utc).isoformat(),
                   "nodes_polled": len(seen), "watching": n_watching,
                   "any_rain_24h": sum(1 for x in seen if x["mm_24h"] >= 1.0),
                   "wettest": wet,
                   "note": "24 h totals from Open-Meteo, which under-reads "
                           "Ghats extremes by ~3x. Useful for WHERE it is "
                           "raining hardest, not for absolute millimetres."}
            path = os.path.join(_r.DATA_DIR, CONDITIONS_FILE)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(doc, f)
            os.replace(tmp, path)
        except Exception as e:
            log.error("could not write conditions: %s", e)

    def _record(self, g, a, at, recent72):
        """Every evaluated node lands in the ledger, watching or not."""
        ext = f"rain:{g['lat']:.3f},{g['lon']:.3f}:{at}"
        did = None
        cand, is_new, changed = self.store.upsert_candidate(
            ext, at, g["lat"], g["lon"], None, None, "open-meteo",
            {"band": g["band"], "relief_m": g["relief_m"],
             "cells": len(g["cells"]), "mm_72h": round(recent72, 1),
             "windows": a.get("windows"), "pattern": a.get("pattern")})
        if not (is_new or changed):
            return None
        return self.store.record_decision(
            cand, self.cfg.get("version", "rain-1"),
            a.get("driving_percentile") or 0.0, a["tier"],
            [f"window {a.get('driving_window_h')}h",
             f"p{a.get('driving_percentile')}",
             a.get("pattern", ""), f"band {g['band']}"],
            f"{g['lat']:.3f},{g['lon']:.3f}", 0.0,
            suppressed=not self.allow_dispatch,
            suppress_reason=None if self.allow_dispatch
            else "dispatch disabled (--allow-dispatch to enable)")

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("rain watcher up: %d nodes from %d cells, every %d min, "
                 "dispatch=%s", len(self.groups),
                 sum(len(g["cells"]) for g in self.groups),
                 self.interval // 60, self.allow_dispatch)
        while self._running:
            t0 = time.time()
            try:
                raised = self.cycle()
                for g, a, at, ex in raised:
                    who = (", ".join(s["name"] for s in ex[:4]) if ex
                           else "no mapped settlement downstream")
                    log.warning("WATCH %.3f,%.3f  p%s  %s  -> %s", g["lat"],
                                g["lon"], a.get("driving_percentile"),
                                a.get("pattern"), who)
            except Exception as e:
                log.exception("cycle failed: %s", e)
            slept = 0.0
            while self._running and slept < self.interval - (time.time() - t0):
                time.sleep(min(5.0, self.interval))
                slept += 5.0
        log.info("rain watcher stopped")


def main(argv=None):
    p = argparse.ArgumentParser(description="rainfall watch service")
    p.add_argument("--db", default="hew.db")
    p.add_argument("--cells", default=None)
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    p.add_argument("--allow-dispatch", action="store_true")
    p.add_argument("--no-route", action="store_true",
                   help="skip downstream routing (faster, no settlement names)")
    p.add_argument("--limit", type=int, default=0,
                   help="poll only the N steepest nodes (quota control)")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    cells, cov = rain_watch.load_cells(a.cells)
    warn = rain_watch.coverage_warning(cov)
    if warn:
        log.warning(warn)
    groups = build_groups(cells)
    if a.limit:
        groups = groups[:a.limit]
    log.info("%d watch cells -> %d model-grid nodes", len(cells), len(groups))

    w = RainWatcher(Store(a.db), groups, allow_dispatch=a.allow_dispatch,
                    interval=a.interval, route=not a.no_route)
    if a.once:
        raised = w.cycle()
        print(f"{len(groups)} nodes polled, {len(raised)} watching")
        for g, x, at, ex in raised:
            print(f"  WATCH {g['lat']:.3f},{g['lon']:.3f}  band {g['band']}  "
                  f"p{x.get('driving_percentile')}  {x.get('pattern')}")
            for s2 in ex[:6]:
                print(f"        {s2['river_km']:6.1f} km  {s2['name']}")
        return 0
    w.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
