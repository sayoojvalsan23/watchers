"""
Phase 5 — rainfall-triggered slope failure.

A SEPARATE ENGINE, deliberately. The seismic track answers "something just
happened, who is downstream". This answers "conditions have been building for
days, which slopes are primed". Different physics, different timescale,
different feed, different failure modes. Sharing a dashboard is fine; sharing
a detector would be wrong.

WHAT THE FEED CAN AND CANNOT DO
-------------------------------
Measured against Chooralmala / Mundakkai, 30 July 2024 (~250 dead):

    IMD stations       373 mm in 24 h,  586 mm antecedent 72 h
    Open-Meteo (ERA5)   51.6 mm 24 h,    72.2 mm 72 h        -- 7x low

That is not a calibration offset. ERA5 runs at ~25 km; the storm was a few
kilometres wide over the Western Ghats escarpment, and the grid averages it
away. The best neighbouring cell was still 3x short.

Worse, it does not DISCRIMINATE. Across the 2024 monsoon at that location:

    24 h at the disaster: 93rd percentile   (season max 129 mm)
    72 h at the disaster: 79th percentile   (season max 260 mm)

The deadliest landslide in Kerala's recent history reads as an unremarkable
wet day. A threshold low enough to catch it fires on 33 days a season.

THE DESIGN CONSEQUENCE
----------------------
Absolute millimetre thresholds from the literature (Caine, Guzzetti and
successors) are calibrated against RAIN GAUGES. Feeding them a reanalysis
value that is 7x low means they never trip. So this engine works in
PERCENTILES OF THE FEED'S OWN CLIMATOLOGY, not in millimetres:

    "the last 72 hours are wetter than 99.5% of the last ten years here"

That transfers across feeds. Swap ERA5 for IMERG or for a real gauge network
and the thresholds still mean the same thing, because each is re-derived from
that feed's own history. It does NOT fix the discrimination problem -- if the
feed cannot separate a disaster from a wet Tuesday, no percentile can -- but
it stops the engine being silently miscalibrated by a factor of seven, and it
makes the discrimination measurable per location.

Every assessment therefore reports the percentile AND the raw millimetres,
so a hydrologist can see both what the feed said and how unusual it was.
"""

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "hew-rainfall/1.0 (Himalayan Early Warning; Phase 5)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

# Windows the engine tracks. Short ones capture intensity, long ones capture
# the antecedent wetness that decides whether a slope has any margin left.
WINDOWS_H = (1, 3, 6, 12, 24, 48, 72, 120)

DEFAULTS = {
    # Percentile of the location's own climatology, per window. Derived from
    # the feed, not from the literature -- see the module docstring.
    "watch_percentile": 95.0,
    "advisory_percentile": 99.0,
    "warning_percentile": 99.8,
    # Below these the percentile is meaningless: a dry location's 99.8th
    # percentile can be a few millimetres, and the engine would warn on
    # drizzle. EVERY window needs one -- an earlier version floored only 24
    # and 72 h, so a dry site tipped straight through the unfloored 1 h.
    # Monotonic with duration, and all comfortably below the Chooralmala
    # readings (12 h 39.9, 24 h 50.9, 72 h 71.8 mm) so the floor cannot mask
    # the one event we are calibrating against.
    "min_mm_to_consider": {1: 10.0, 3: 15.0, 6: 20.0, 12: 25.0,
                           24: 30.0, 48: 40.0, 72: 50.0, 120: 70.0},
    # How many years of history define "normal" here.
    "climatology_years": 10,
}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=90) as r:
        return json.loads(r.read().decode())


def fetch_hourly(lat, lon, start, end, cache_dir=None):
    """Hourly precipitation, cached on disk so replays are offline."""
    key = f"rain_{lat:.3f}_{lon:.3f}_{start}_{end}.json"
    path = os.path.join(cache_dir or os.path.join(DATA_DIR, "rain_cache"), key)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": start,
        "end_date": end, "hourly": "precipitation", "timezone": "UTC"})
    d = _get(f"{ARCHIVE}?{q}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f)
    return d


def rolling(series, hours):
    """Accumulation over the preceding `hours` at each index."""
    out, run = [], 0.0
    for i, v in enumerate(series):
        run += v
        if i >= hours:
            run -= series[i - hours]
        out.append(round(run, 2))
    return out


def climatology(lat, lon, years=None, end=None, cache_dir=None):
    """
    Percentile ladders for this location, from the feed's own history.

    Returns {window_hours: sorted list of accumulations}. Sorted so a value
    can be turned into a percentile by bisection.
    """
    years = years or DEFAULTS["climatology_years"]
    end = end or datetime.now(timezone.utc).date()
    start = end - timedelta(days=365 * years)
    d = fetch_hourly(lat, lon, start.isoformat(), end.isoformat(), cache_dir)
    p = [x or 0.0 for x in d["hourly"]["precipitation"]]
    return {h: sorted(rolling(p, h)) for h in WINDOWS_H}


def percentile_of(value, ladder):
    """Where `value` sits in a sorted ladder, as a percentile."""
    if not ladder:
        return None
    import bisect
    return round(100.0 * bisect.bisect_left(ladder, value) / len(ladder), 2)


def assess(accumulations, clim, cfg=None):
    """
    Turn accumulations into a tier, using percentiles of local climatology.

    accumulations: {window_hours: mm}
    clim:          {window_hours: sorted ladder}

    Reports BOTH the percentile and the millimetres, because a percentile
    alone hides a feed reading 7x low and millimetres alone hide whether the
    number is unusual here.
    """
    c = {**DEFAULTS, **(cfg or {})}
    rows, worst_pct, worst_w = [], 0.0, None
    for h, mm in sorted(accumulations.items()):
        pct = percentile_of(mm, clim.get(h, []))
        rows.append({"window_h": h, "mm": round(mm, 1), "percentile": pct})
        floor = c["min_mm_to_consider"].get(h)
        if pct is not None and (floor is None or mm >= floor):
            if pct > worst_pct:
                worst_pct, worst_w = pct, h

    if worst_pct >= c["warning_percentile"]:
        tier = "warning"
    elif worst_pct >= c["advisory_percentile"]:
        tier = "advisory"
    elif worst_pct >= c["watch_percentile"]:
        tier = "watch"
    else:
        tier = "log"

    return {
        "tier": tier,
        "driving_window_h": worst_w,
        "driving_percentile": worst_pct if worst_w else None,
        "windows": rows,
        "basis": "percentile of this location's own climatology in this feed",
        "caveat": "Reanalysis under-reports convective rainfall severely -- "
                  "7x at Chooralmala. A percentile makes thresholds portable "
                  "between feeds; it does not make a blind feed see.",
    }


def accumulations_at(series, index):
    """Accumulations over each tracked window, ending at `index`."""
    return {h: sum(series[max(0, index - h):index]) for h in WINDOWS_H}
