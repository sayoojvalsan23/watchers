"""
The scheduler / watcher service.

Runs three timers:
  - poll     (60s)  fetch catalogue, evaluate, decide
  - heartbeat (5m)  page if no successful poll. Absence of events is NOT health.
  - canary   (1h)   inject a known-shape event through the full path.
                    A system that hasn't alerted in six months and a system
                    that broke six months ago look identical from outside.

Phase 1 dispatches to the LOG TIER ONLY by default. Public alerting is gated
behind an explicit config flag and a human confirmation step (design doc §12).

Run:
    python -m hew.watcher --once          # single cycle, for testing
    python -m hew.watcher                 # continuous
    python -m hew.watcher --replay FILE   # replay a saved feed
"""

import argparse
import json
import logging
import signal
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .detect import evaluate, DEFAULT_CONFIG
from .store import Store, utcnow
from .registry import load_registry
from .notify import Dispatcher
from . import alarm

log = logging.getLogger("hew")

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"

POLL_SECONDS = 60
HEARTBEAT_SECONDS = 300
CANARY_SECONDS = 3600
STALE_POLL_SECONDS = 300     # page if no successful poll within this


class Watcher:
    ROUTED_TIERS = ("watch", "advisory", "warning")

    def __init__(self, store, registry, dispatcher, config,
                 fetcher=None, allow_dispatch=False,
                 network=None, settlements=None, geo_version=None):
        self.store = store
        self.registry = registry
        self.dispatcher = dispatcher
        self.config = config
        self.version = config["version"]
        self.fetcher = fetcher or self._fetch_usgs
        self.allow_dispatch = allow_dispatch
        # Static routing data, loaded once at startup. Never fetched mid-cycle.
        self.network = network
        self.settlements = settlements or []
        self.geo_version = geo_version
        self._running = True

    # -- ingest ------------------------------------------------------------

    def _fetch_usgs(self, minutes=90):
        bb = self.config["bbox"]
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        params = {
            "format": "geojson",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlatitude": bb["min_lat"], "maxlatitude": bb["max_lat"],
            "minlongitude": bb["min_lon"], "maxlongitude": bb["max_lon"],
            "minmagnitude": self.config["min_magnitude"],
        }
        url = USGS + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())

    @staticmethod
    def parse_features(geojson):
        out = []
        for f in geojson.get("features", []):
            p, g = f.get("properties", {}), f.get("geometry", {})
            coords = g.get("coordinates") or [None, None, None]
            out.append({
                "external_id": f.get("id"),
                "lon": coords[0], "lat": coords[1], "depth_km": coords[2],
                "magnitude": p.get("mag"),
                "observed_at": (datetime.fromtimestamp(
                    p["time"] / 1000, timezone.utc).isoformat()
                    if p.get("time") else utcnow()),
                "usgs_type": p.get("type"),
                "raw": p,
            })
        return out

    # -- circuit breaker ---------------------------------------------------

    def _breaker_tripped(self):
        cb = self.config["circuit_breaker"]
        since = (datetime.now(timezone.utc)
                 - timedelta(hours=cb["window_hours"])).isoformat()
        n = self.store.recent_dispatched(["advisory", "warning"], since)
        return n >= cb["max_dispatched"], n

    # -- one cycle ---------------------------------------------------------

    def cycle(self):
        try:
            gj = self.fetcher()
        except Exception as e:
            self.store.heartbeat("usgs_catalogue", ok=False, detail=str(e))
            log.error("FETCH FAILED: %s", e)
            return {"error": str(e)}

        self.store.heartbeat("usgs_catalogue", ok=True)
        feats = self.parse_features(gj)
        stats = {"seen": len(feats), "new": 0, "revised": 0, "by_tier": {},
                 "dispatched": 0, "routed": 0}

        for f in feats:
            if not f["external_id"]:
                continue
            cid, is_new, changed = self.store.upsert_candidate(
                f["external_id"], f["observed_at"], f["lat"], f["lon"],
                f["depth_km"], f["magnitude"], "usgs", f["raw"])
            if not (is_new or changed):
                continue                       # nothing moved; nothing to do
            if is_new:
                stats["new"] += 1
            else:
                stats["revised"] += 1
                log.info("REVISED %s: re-evaluating (depth=%s mag=%s)",
                         f["external_id"], f["depth_km"], f["magnitude"])

            r = evaluate(f["lat"], f["lon"], f["depth_km"], f["magnitude"],
                         self.registry, self.config)
            stats["by_tier"][r["tier"]] = stats["by_tier"].get(r["tier"], 0) + 1

            suppressed, reason = False, None
            if r["tier"] in ("advisory", "warning"):
                tripped, n = self._breaker_tripped()
                if tripped:
                    suppressed = True
                    reason = f"circuit_breaker n={n}"
                    log.warning("CIRCUIT BREAKER: suppressed %s (%s)",
                                f["external_id"], reason)
                elif not self.allow_dispatch:
                    suppressed = True
                    reason = "dispatch_disabled_phase1"

            # A revision may ESCALATE -- watch to warning, once a default
            # depth is replaced by a measured one. That is new information and
            # must reach someone. What must not happen is the same alert going
            # out twice, so the dedupe is on the TIER already dispatched for
            # this candidate, not on whether the record has been seen before.
            #
            # This runs BEFORE record_decision: a decision suppressed here must
            # say so IN THE LEDGER, or "why did it not fire" stops being
            # answerable from the record alone.
            if not is_new and not suppressed:
                if r["tier"] in self.store.dispatched_tiers(cid):
                    suppressed = True
                    reason = f"already_dispatched_{r['tier']}"
                    log.info("SUPPRESSED %s: %s already sent for this event",
                             f["external_id"], r["tier"])

            did = self.store.record_decision(
                cid, self.version, r["score"], r["tier"], r["factors"],
                r["nearest_site"], r["nearest_km"], suppressed, reason)

            corridor = None
            if r["tier"] in self.ROUTED_TIERS:
                n_routed, corridor = self.route(did, f["lat"], f["lon"])
                stats["routed"] += n_routed

            # Notify the OPERATOR of any dispatch-tier decision, including
            # ones suppressed by Phase 1. That is the point: the operator
            # should see what the system would have done. This is not public
            # dissemination -- see hew/operator.py.
            if r["tier"] in ("advisory", "warning"):
                try:
                    from . import hew_operator as operator
                    if operator.configured():
                        operator.detection(
                            r["tier"], r["score"], r["nearest_site"],
                            r["nearest_km"], len(corridor) if corridor else None,
                            corridor[0]["name"] if corridor else None)
                except Exception as e:
                    log.error("operator notify failed: %s", e)

            if r["tier"] in ("advisory", "warning") and not suppressed:
                ok, err = self.dispatcher.send(r["tier"], f, r, corridor)
                self.store.record_alert(did, self.dispatcher.channel,
                                        r["reach_id"], 1 if ok else 0, err)
                stats["dispatched"] += 1

            if r["tier"] != "reject":
                log.info("%-8s score=%-3d %s M%.1f %.1fkm  %s (%.1f km)%s",
                         r["tier"].upper(), r["score"], f["external_id"],
                         f["magnitude"] or 0, f["depth_km"] or 0,
                         r["nearest_site"], r["nearest_km"] or 0,
                         "  [SUPPRESSED: %s]" % reason if suppressed else "")
        return stats

    # -- routing -----------------------------------------------------------

    def route(self, decision_id, lat, lon):
        """
        Record the downstream corridor for a decision.

        OFF THE TRIGGER PATH BY CONSTRUCTION. This runs after the decision is
        already in the ledger and cannot change it. Any failure here is logged
        and swallowed: a routing error must never suppress a hazard decision,
        and must never delay dispatch. Returns (settlements written, corridor).
        """
        if not self.network:
            return 0, None
        try:
            from .routing import trace_branches, exposed_settlements_union
            unc = self.config.get("source_uncertainty_km", 0.0)
            branches = trace_branches(self.network, lat, lon,
                                      uncertainty_km=unc)
            if not branches:
                log.warning("ROUTING: source off-network — no corridor "
                            "recorded for decision %s", decision_id)
                return 0, None
            snap_km = branches[0]["snap_km"]
            ex = exposed_settlements_union(branches, self.settlements,
                                           corridor_km=2.0)
            n = self.store.record_impact(decision_id, ex, snap_km,
                                         self.geo_version)
            if ex:
                pop = sum(s["population"] or 0 for s in ex)
                log.info("CORRIDOR: %d settlements over %.0f km across "
                         "%d branch(es) [%s]%s (snap %.1f km, uncertainty %.0f km)",
                         n, ex[-1]["river_km"], len(branches),
                         ", ".join(b["name"] for b in branches),
                         f", {pop:,} tagged residents" if pop else "",
                         snap_km, unc)
            return n, ex
        except Exception as e:                      # never break the cycle
            log.error("ROUTING FAILED for decision %s: %s", decision_id, e)
            return 0, None

    # -- health ------------------------------------------------------------

    def check_health(self):
        """
        Fail-safe: loss of confidence pages an operator. On a headless box a
        log line is not a page, so this also sounds the fault alarm -- see
        hew/alarm.py for why that alarm must never be wired to a hazard
        decision.
        """
        last = self.store.last_ok_heartbeat("usgs_catalogue")
        if not last:
            log.error("HEALTH: no successful poll ever recorded")
            alarm.sound("no successful poll ever recorded")
            return False
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(last)).total_seconds()
        if age > STALE_POLL_SECONDS:
            log.error("HEALTH: stale — last good poll %.0fs ago. PAGE.", age)
            alarm.sound("feed stale: no good poll for %.0f minutes" % (age / 60))
            return False
        log.info("HEALTH: ok (last poll %.0fs ago)", age)
        alarm.reset()          # recovered; a later failure sounds again
        return True

    def canary(self):
        """Synthetic event through the full evaluation path."""
        site = self.registry[0]
        r = evaluate(site["lat"], site["lon"], 2.0, 5.0,
                     self.registry, self.config)
        ok = r["tier"] in ("advisory", "warning")
        self.store.heartbeat("canary", ok=ok, detail=f"tier={r['tier']}")
        (log.info if ok else log.error)(
            "CANARY: %s (tier=%s score=%d)",
            "pass" if ok else "FAIL — evaluation path broken",
            r["tier"], r["score"])
        if not ok:
            alarm.sound("canary failed: evaluation path broken "
                        "(tier=%s score=%d)" % (r["tier"], r["score"]))
        return ok

    # -- loop --------------------------------------------------------------

    def stop(self, *_):
        log.info("shutting down")
        self._running = False

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        next_poll = next_hb = next_canary = 0.0
        while self._running:
            now = time.time()
            if now >= next_poll:
                self.cycle(); next_poll = now + POLL_SECONDS
            if now >= next_hb:
                self.check_health(); next_hb = now + HEARTBEAT_SECONDS
            if now >= next_canary:
                self.canary(); next_canary = now + CANARY_SECONDS
            time.sleep(1)


def build(db="hew.db", allow_dispatch=False, fetcher=None, routing=True):
    store = Store(db)
    store.put_config(DEFAULT_CONFIG["version"], DEFAULT_CONFIG, "bootstrap")
    version, cfg = store.active_config()

    network = settlements = geo_version = None
    if routing:
        # Load the pinned snapshot once, at startup. If it is missing the
        # watcher still runs -- detection must not depend on routing data.
        try:
            import json as _json, os as _os
            from .routing import RiverNetwork, load_settlements, DATA_DIR
            network = RiverNetwork.load()
            settlements = load_settlements()
            mpath = _os.path.join(DATA_DIR, "manifest.json")
            if _os.path.exists(mpath):
                with open(mpath) as fh:
                    m = _json.load(fh).get("rivers_trishuli", {})
                geo_version = m.get("osm_base") or m.get("fetched_at")
            log.info("routing: %d ways, %d settlements, geo_version=%s",
                     len(network.ways), len(settlements), geo_version)
        except Exception as e:
            log.warning("routing unavailable (%s) — detection continues", e)
            network, settlements = None, []

    return Watcher(store, load_registry(), Dispatcher(), cfg,
                   fetcher=fetcher, allow_dispatch=allow_dispatch,
                   network=network, settlements=settlements,
                   geo_version=geo_version)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--db", default="hew.db")
    ap.add_argument("--replay", help="JSON file of a saved USGS feed")
    ap.add_argument("--allow-dispatch", action="store_true",
                    help="enable advisory/warning dispatch (default: off)")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    fetcher = None
    if a.replay:
        data = json.load(open(a.replay))
        fetcher = lambda: data

    w = build(a.db, a.allow_dispatch, fetcher)
    if a.once or a.replay:
        s = w.cycle()
        w.check_health()
        w.canary()
        print("\n" + json.dumps(s, indent=2))
    else:
        w.run()


if __name__ == "__main__":
    main()
