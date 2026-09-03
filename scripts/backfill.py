"""
Load historical catalogue decisions into a live database.

WHY A FRESH DEPLOYMENT IS MISLEADING WITHOUT THIS

watcher._fetch_usgs looks back 90 minutes. A watcher started today therefore
has no memory of anything before today, and the dashboard's LAST DETECTION
line reads "none on record" -- which a reader takes as "nothing has ever
happened here", on a system whose whole point is that things do. The Pi
started on 31 August and the founding event was 26 August, so the event this
project exists because of was never in any window it looked at.

This replays real catalogue records through the SAME evaluate() the watcher
uses, and writes the resulting decisions to the ledger.

THREE THINGS IT DELIBERATELY DOES NOT DO

  It does not dispatch. Nothing is sent anywhere.
  It does not notify the operator. Backfilling six months of history must
  not fire six months of phone pushes.
  It does not route corridors. Those are computed live for a real event;
  recomputing them for history would cost hours and change nothing on screen.

Decisions are marked `backfill` in suppress_reason so they are visibly
historical in the ledger and cannot be mistaken for something the running
watcher saw.

    python3 scripts/backfill.py --db ~/.local/share/hew/hew.db --days 365
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hew.detect import evaluate, DEFAULT_CONFIG
from hew.registry import load_registry
from hew.store import Store
from hew.watcher import Watcher

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def fetch(cfg, start, end):
    bb = cfg["bbox"]
    q = {"format": "geojson",
         "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
         "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
         "minlatitude": bb["min_lat"], "maxlatitude": bb["max_lat"],
         "minlongitude": bb["min_lon"], "maxlongitude": bb["max_lon"],
         "minmagnitude": cfg["min_magnitude"], "orderby": "time-asc"}
    with urllib.request.urlopen(USGS + "?" + urllib.parse.urlencode(q),
                                timeout=120) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cfg = DEFAULT_CONFIG
    registry = load_registry()
    store = Store(a.db)
    store.put_config(cfg["version"], cfg, "backfill")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=a.days)
    print("backfilling %s from %s (%d days), config %s, registry %d sites"
          % (a.db, start.date(), a.days, cfg["version"], len(registry)))

    # USGS caps a single response; walk in 30-day windows.
    feats, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        gj = fetch(cfg, cur, nxt)
        got = gj.get("features", [])
        feats.extend(got)
        print("  %s .. %s : %d events" % (cur.date(), nxt.date(), len(got)))
        cur = nxt

    tiers, written = {}, 0
    for f in Watcher.parse_features({"features": feats}):
        if not f["external_id"]:
            continue
        r = evaluate(f["lat"], f["lon"], f["depth_km"], f["magnitude"],
                     registry, cfg)
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        if a.dry_run:
            continue
        cid, _is_new, _changed = store.upsert_candidate(
            f["external_id"], f["observed_at"], f["lat"], f["lon"],
            f["depth_km"], f["magnitude"], "usgs", f["raw"])
        store.record_decision(cid, cfg["version"], r["score"], r["tier"],
                              json.dumps(list(r["factors"])),
                              r["nearest_site"], r["nearest_km"],
                              suppressed=True, suppress_reason="backfill")
        written += 1

    print("\n%d events -> %s" % (len(feats), tiers))
    print("%s %d decisions%s" % ("would write" if a.dry_run else "wrote",
                                 len(feats) if a.dry_run else written,
                                 "" if a.dry_run else " (marked backfill)"))


if __name__ == "__main__":
    main()
