"""
Replay the rainfall watch against Kerala's real landslide disasters.

WHY THIS IS A MODULE AND NOT A SCRATCH SCRIPT
----------------------------------------------
Every threshold in rain_watch.py is justified by this backtest. If the
thresholds drift, or the windows change, or someone "improves" the percentile
transform, the only thing that can say whether the detector still works is a
replay against events that actually happened. That makes this a regression
test, and regression tests live in the repo.

THE EVENTS
----------
Kerala's major rainfall-triggered landslide disasters within the reach of
Open-Meteo's hourly archive. Coordinates are APPROXIMATE -- village or scarp
area, not surveyed initiation points -- and times are from public reporting,
so treat lead times as accurate to a few hours, not to the minute.

This is a small sample of catastrophic events, not a representative sample of
landslides. Five events cannot establish a false-negative rate; they can only
establish that the detector is not blind to the events we know about.

WHAT IT MEASURED
----------------
Each event's position in its OWN site's ten-year Open-Meteo climatology,
using the peak accumulation in the 24 h before failure:

    Chooralmala    p99.84  (corrected)   Kavalappara   p99.55  (unverified)
    Pettimudi      p99.79  (unverified)  Koottickal    p99.30  (corrected)
    Puthumala      p99.76  (unverified)

All five inside the top 1%, which is what licenses the percentile design. But
the range is narrow and it sits close to the noise: at p99.5 all five are
caught at ~9.9 alerts/yr/site, and at p99.8 NONE of them are. There is no
threshold that both detects and meets the <=2/yr gate.

CORRECTION. An earlier version of this module reported Chooralmala as the
WEAKEST of the five (p99.30) and drew a conclusion from it -- "the model saw
the deadliest disaster least clearly". That was an artefact of a coordinate
3.3 km off the village. At the corrected position it is the STRONGEST event
(p99.84). The narrative was wrong; the operating point barely moved, because
the weakest event is now Koottickal at p99.30 against the old p99.27.

Three of the five coordinates remain UNVERIFIED. Name-matching against OSM is
not a fix: there are two Puthumalas in Kerala and the searchable one is 268 km
from the landslide. Coordinates need a landslide inventory, not a gazetteer.

A NOTE ON COUNTING
------------------
An earlier version of this analysis reported "43.8 alerts/year" at every
percentile and treated it as a finding. It was tautological: p99.5 of hourly
values exceeds its threshold on 0.5% of hours BY DEFINITION, which is 43.8
hours a year. Alerts must be counted as EPISODES -- consecutive or nearby
firing hours are one storm and one alert -- or the number is just the
percentile restated.
"""

import bisect
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .rain_watch import WINDOWS_H, rolling

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
UA = {"User-Agent": "hew-rainbacktest/1.0 (Himalayan Early Warning; Phase 5b)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

# (name, ISO failure time IST, lat, lon, deaths, note)
EVENTS = [
    ("Chooralmala/Mundakkai", "2024-07-30 01:00", 11.4865, 76.1557, "~250+",
     "Wayanad. The founding case. Coordinate CORRECTED to the OSM Mundakai "
     "node; the earlier one was 3.3 km off and understated the signal."),
    ("Pettimudi (Rajamala)", "2020-08-06 22:30", 10.167, 77.050, "66",
     "Idukki. Tea estate line-rooms buried. COORDINATE UNVERIFIED -- no OSM "
     "node for Rajamala or Pettimudi."),
    ("Puthumala", "2019-08-08 15:00", 11.490, 76.100, "17",
     "Wayanad, near Meppadi. COORDINATE UNVERIFIED -- OSM has a Puthumala, "
     "but in Kollam, 268 km away. Name matching across Kerala is unsafe."),
    ("Kavalappara", "2019-08-08 19:30", 11.310, 76.290, "59",
     "Malappuram. Same day as Puthumala. COORDINATE UNVERIFIED and probably "
     "wrong -- terrain here reads 58 m of relief, implausible for a landslide "
     "that killed 59."),
    ("Koottickal/Kokkayar", "2021-10-16 17:00", 9.5841, 76.8854, "~35",
     "Kottayam/Idukki. October, not the SW monsoon. Coordinate CORRECTED to "
     "the OSM Koottickal node."),
]

# Two firings within this many hours are the same storm.
EPISODE_HOURS = 48
# Days before each event excluded from its own climatology, so the ladder
# cannot be contaminated by the event it is being used to detect.
LEAKAGE_GUARD_DAYS = 14


def fetch(lat, lon, end_date, years=10, cache_dir=None):
    """Hourly precipitation for the ten years before `end_date`, cached."""
    d = cache_dir or os.path.join(DATA_DIR, "backtest")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"bt_{lat:.3f}_{lon:.3f}_{end_date}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    y = int(end_date[:4])
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": f"{y - years}-01-01", "end_date": end_date,
        "hourly": "precipitation", "timezone": "Asia/Kolkata"})
    req = urllib.request.Request(f"{ARCHIVE}?{q}", headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode())
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def episodes(fired, gap=EPISODE_HOURS):
    """Count firing SPELLS, not firing hours. See the docstring."""
    n, last = 0, None
    for i, f in enumerate(fired):
        if f:
            if last is None or i - last > gap:
                n += 1
            last = i
    return n


def replay(event, percentile, data=None):
    """
    Replay one event at one threshold.

    Returns detection, lead time in hours, the event's own percentile, and the
    alert rate the threshold implies at that site.
    """
    name, when, lat, lon, dead, _ = event
    d = data or fetch(lat, lon, when[:10])
    T = d["hourly"]["time"]
    P = [x or 0.0 for x in d["hourly"]["precipitation"]]
    stamp = datetime.strptime(when, "%Y-%m-%d %H:%M").strftime("%Y-%m-%dT%H:00")
    if stamp not in T:
        raise ValueError(f"{name}: failure hour {stamp} not in series")
    ei = T.index(stamp)
    cut = ei - 24 * LEAKAGE_GUARD_DAYS
    years = cut / 8766.0

    fired = [False] * cut
    best_lead, best_window, best_pct = None, None, 0.0
    for h in WINDOWS_H:
        roll = rolling(P, h)
        clim = sorted(roll[:cut])
        if not clim:
            continue
        thr = clim[min(len(clim) - 1, int(len(clim) * percentile / 100.0))]
        if thr <= 0:
            continue
        for i in range(cut):
            if roll[i] >= thr:
                fired[i] = True
        peak = max(roll[max(0, ei - 24):ei + 1])
        pct = 100.0 * bisect.bisect_left(clim, peak) / len(clim)
        best_pct = max(best_pct, pct)
        first = next((i for i in range(max(0, ei - 120), ei + 1)
                      if roll[i] >= thr), None)
        if first is not None and (best_lead is None or ei - first > best_lead):
            best_lead, best_window = ei - first, h

    return {"event": name, "deaths": dead, "when": when,
            "detected": best_lead is not None,
            "lead_hours": best_lead, "driving_window_h": best_window,
            "event_percentile": round(best_pct, 2),
            "alerts_per_year": round(episodes(fired) / years, 1)
            if years else None}


def run(percentile=99.25, events=None):
    """Replay every event. Returns (rows, summary)."""
    rows = [replay(e, percentile) for e in (events or EVENTS)]
    det = sum(1 for r in rows if r["detected"])
    leads = sorted(r["lead_hours"] for r in rows if r["lead_hours"] is not None)
    rates = [r["alerts_per_year"] for r in rows if r["alerts_per_year"]]
    return rows, {
        "percentile": percentile,
        "recall": f"{det}/{len(rows)}",
        "median_lead_hours": leads[len(leads) // 2] if leads else None,
        "mean_alerts_per_year_per_site": round(sum(rates) / len(rates), 1)
        if rates else None,
        "gate_alerts_per_year": 2.0,
        "passes_gate": bool(rates) and sum(rates) / len(rates) <= 2.0,
    }


if __name__ == "__main__":
    import sys
    ps = [float(x) for x in sys.argv[1:]] or [99.25, 99.5, 99.8, 99.9]
    for p in ps:
        rows, s = run(p)
        print(f"\n=== p{p} ===   recall {s['recall']}   "
              f"median lead {s['median_lead_hours']} h   "
              f"{s['mean_alerts_per_year_per_site']} alerts/yr/site   "
              f"{'PASS' if s['passes_gate'] else 'FAIL'}")
        for r in rows:
            print(f"   {r['event']:<24}{r['deaths']:>7}  "
                  f"{'HIT ' if r['detected'] else 'MISS'}  "
                  f"lead {str(r['lead_hours'] or '-'):>4}h  "
                  f"win {str(r['driving_window_h'] or '-'):>3}h  "
                  f"event at p{r['event_percentile']}")
