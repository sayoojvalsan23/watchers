"""
Phase 5b — the rainfall watch engine.

WHAT THIS IS, STATED BEFORE ANYTHING ELSE
------------------------------------------
This raises a WATCH. It cannot raise an advisory or a warning, and the cap is
enforced in code rather than left to configuration, because the measurement
that justifies the cap is unambiguous.

Backtested against five Kerala landslide disasters (Chooralmala 2024,
Pettimudi 2020, Puthumala and Kavalappara 2019, Koottickal 2021), on
Open-Meteo hourly with ten years of each site's own prior climatology:

    p99.5   5/5 recall   28 h median lead   ~9.9 alerts/yr/site   FAIL gate
    p99.8   0/5 recall                      ~5.0 alerts/yr/site   FAIL gate
    p99.9   0/5 recall                      ~2.9 alerts/yr/site   FAIL gate

The five events cluster between p99.27 and p99.79, so every threshold above
that band loses all of them simultaneously. **There is no setting that both
detects and passes the <=2 alerts/year gate.** The best honest operating point
is 5/5 recall at ~10 alerts/year -- five times the gate.

Ten false alarms a year is a defensible WATCH. It is roughly IMD's own
heavy-rain alert cadence, and a watch asks people to pay attention rather than
to abandon their homes. It is NOT defensible as an evacuation trigger: move a
village five times on a false alarm and the sixth time nobody moves.

So: MAX_TIER is "watch", and `assess()` will not return anything above it no
matter what the configuration says.

WHY PERCENTILES AND NOT MILLIMETRES
------------------------------------
Six global NWP models under-read Western Ghats orographic extremes by 2-4x,
and inconsistently (0.16x to 1.23x across nine stations in ninety days), so
there is no bias to correct and absolute thresholds cannot be calibrated.
Comparing the model only to ITSELF cancels most of that. The five-event
backtest is the evidence that it works: all five land in the model's own top
1% even though their millimetre values are wrong by a factor of three.

WHY TERRAIN COMES FIRST
------------------------
Percentile thresholds fire at a fixed rate PER SITE, so system alerts scale
with the number of sites. Screening to steep ground before evaluating rainfall
is what keeps that number finite -- see hew/terrain.py.

WHAT IT STILL CANNOT DO
------------------------
Sub-daily skill is UNVALIDATED. The only ground truth available (KSDMA) is a
daily bulletin, so while the 3 h and 6 h windows fired earliest at three of
the five events, nothing here has verified the model's hourly timing against
a gauge. Treat short-window lead times as indicative, not established.
"""

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import terrain

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
UA = {"User-Agent": "hew-rainwatch/1.0 (Himalayan Early Warning; Phase 5b)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

# Short windows carry the lead time; long ones carry antecedent wetness.
WINDOWS_H = (3, 6, 12, 24, 48, 72)

# The cap. Not configurable -- see the module docstring.
MAX_TIER = "watch"

DEFAULTS = {
    # From the backtest: the five events span p99.27-p99.79, so this is the
    # highest threshold that still catches all of them.
    "watch_percentile": 99.25,
    # Years of the site's own history that define "normal" here.
    "climatology_years": 10,
    # Two firings inside this many hours are the same storm, not two alerts.
    # An operator woken twice in one night has been woken once.
    "episode_hours": 48,
    # Below this the percentile is meaningless -- a dry site's p99.25 can be
    # a few millimetres and the engine would watch on drizzle. Monotonic with
    # duration, and all below the values the five backtest events reached.
    "min_mm": {3: 8.0, 6: 12.0, 12: 18.0, 24: 25.0, 48: 35.0, 72: 45.0},
    # Terrain bands allowed to raise anything at all.
    "watchable_bands": ("steep", "moderate"),
    # The "primed and triggered" co-signal. NOT a gate -- see below.
    "antecedent_windows": (48, 72),
    "burst_windows": (3, 6, 12),
    "primed_percentile": 99.0,
    "triggered_percentile": 99.0,
    # Once raised, a watch HOLDS for this long even as rainfall eases.
    # See WHY A WATCH LATCHES, below.
    "hold_hours": 18,
    # While latched, stand down only if every window falls below this. A
    # slope that is still in the top 5% of wet is not a slope to stand down on.
    "release_percentile": 95.0,
}

# WHY A WATCH LATCHES
# --------------------
# Replaying Chooralmala hour by hour exposed a defect that a threshold test
# cannot see. The watch fired at 18:00 on 29 July -- 7 h ahead -- and then
# WITHDREW at 21:00, four hours before the hillside failed at 01:00:
#
#     29 Jul 18:00   3 h at p99.30   WATCH   <- fires
#     29 Jul 20:00   3 h at p99.52   WATCH
#     29 Jul 21:00   3 h at p97.58   log     <- stands down
#     30 Jul 01:00                   log     <- failure, system silent
#
# The cause is physical, not numerical. Rainfall PEAKED at 18:00 (9.6 mm/h)
# and was easing to 1.3 mm/h by the time the slope went. Slopes fail after
# the rain, because pore pressure keeps climbing once the downpour stops.
# A detector driven by a 3 h window therefore stands down precisely when the
# risk is greatest.
#
# The longer windows knew: 12 h held p98.5 and 24 h kept climbing through the
# failure. But `assess` takes the MAX across windows, so a collapsing short
# window pulls the verdict down with it.
#
# So a raised watch holds for `hold_hours` and is released early only if
# every window has dropped below `release_percentile`. Withdrawing a warning
# is a far more consequential act than raising one -- people go back indoors --
# and it must not happen because a 3 h average moved.

# WHY "PRIMED AND TRIGGERED" IS REPORTED BUT NOT REQUIRED
# -------------------------------------------------------
# The standard landslide-EWS formulation couples antecedent saturation with a
# rainfall burst -- the soil-saturation-index idea. Requiring BOTH is a large
# improvement in false-alarm rate, measured on the five-event backtest:
#
#     any window >= p99.5             5/5 recall    9.9 alerts/yr   FAIL gate
#     ante>=p99 AND burst>=p99        3/5 recall    2.0 alerts/yr   PASS gate
#
# Passing the gate is worth a great deal, and the temptation is to adopt it.
# But look at WHICH two events it drops:
#
#     Chooralmala   antecedent p94.82   burst p99.59   MISS
#     Koottickal    antecedent p98.56   burst p99.55   MISS
#
# Chooralmala's slope was NOT unusually primed. The rain fell in one night on
# ordinary ground, which is exactly what the KSDMA gauge record shows
# independently: Vythiri read 27.6 mm the morning before, and the preceding
# week contained heavier days that produced no landslide.
#
# So the antecedent model would have passed the gate by being blind to the
# deadliest landslide in Kerala's recent history. It is therefore reported as
# a CONFIDENCE SIGNAL -- "this slope was primed as well as hit" -- and never
# used to suppress a watch. Burst-only events are real and they are the ones
# that kill.


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=90) as r:
        return json.loads(r.read().decode())


def rolling(series, hours):
    """Accumulation over the preceding `hours` at each index."""
    out, run = [], 0.0
    for i, v in enumerate(series):
        run += v
        if i >= hours:
            run -= series[i - hours]
        out.append(round(run, 2))
    return out


def percentile_of(value, ladder):
    """Where `value` sits in a sorted ladder, as a percentile."""
    if not ladder:
        return None
    import bisect
    return round(100.0 * bisect.bisect_left(ladder, value) / len(ladder), 3)


def climatology(lat, lon, years=None, end=None, cache_dir=None):
    """
    Percentile ladders from the site's OWN model history.

    Returns {window_hours: sorted accumulations}. Cached on disk: this is ten
    years of hourly data and must not be re-fetched on every poll.
    """
    years = years or DEFAULTS["climatology_years"]
    end = end or (datetime.now(timezone.utc).date() - timedelta(days=7))
    start = end - timedelta(days=365 * years)
    d = os.path.join(cache_dir or DATA_DIR, "rain_clim")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"clim_{lat:.3f}_{lon:.3f}_{years}y_{end}.json")
    if os.path.exists(path):
        with open(path) as f:
            return {int(k): v for k, v in json.load(f).items()}
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": start.isoformat(),
        "end_date": end.isoformat(), "hourly": "precipitation",
        "timezone": "Asia/Kolkata"})
    p = [x or 0.0 for x in _get(f"{ARCHIVE}?{q}")["hourly"]["precipitation"]]
    lad = {h: sorted(rolling(p, h)) for h in WINDOWS_H}
    with open(path, "w") as f:
        json.dump(lad, f)
    return lad


def recent(lat, lon, past_days=7):
    """Hourly precipitation up to now, from the live forecast API."""
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": "precipitation",
        "past_days": past_days, "forecast_days": 1, "timezone": "Asia/Kolkata"})
    d = _get(f"{FORECAST}?{q}")
    return d["hourly"]["time"], [x or 0.0 for x in d["hourly"]["precipitation"]]


def accumulations_at(series, index):
    """Accumulation over each tracked window, ending at `index`."""
    return {h: round(sum(series[max(0, index - h + 1):index + 1]), 2)
            for h in WINDOWS_H}


def latch(assessments, cfg=None):
    """
    Apply the hold to a chronological run of assessments.

    Takes [(hours_since_start, assessment)] or a plain list in hour order and
    returns the same list with `tier` upgraded where a watch is still held.
    Adds `held_from_h` so the record shows WHY it is still watching.
    """
    c = {**DEFAULTS, **(cfg or {})}
    out, raised_at = [], None
    for i, a in enumerate(assessments):
        a = dict(a)
        if a["tier"] == "watch":
            raised_at = i
        elif raised_at is not None:
            held = i - raised_at
            peak = max((r["percentile"] or 0.0) for r in a["windows"]) \
                if a.get("windows") else 0.0
            if held < c["hold_hours"] and peak >= c["release_percentile"]:
                a["tier"] = "watch"
                a["held_from_h"] = held
                a["hold_reason"] = (
                    f"held {held} h after the trigger; slopes fail AFTER the "
                    f"rain peaks. Highest window still p{peak:.2f}.")
            else:
                raised_at = None
        out.append(a)
    return out


def assess(accum, clim, cell=None, cfg=None):
    """
    Turn accumulations into a tier, using percentiles of local climatology.

    Reports BOTH percentile and millimetres, because the percentile hides a
    feed reading 3x low and the millimetres hide whether it is unusual here.

    Returns tier "watch" or "log". Never higher -- see MAX_TIER.
    """
    c = {**DEFAULTS, **(cfg or {})}
    rows, best_p, best_w = [], 0.0, None
    for h in sorted(accum):
        mm = accum[h]
        pct = percentile_of(mm, clim.get(h, []))
        floor = c["min_mm"].get(h)
        counted = pct is not None and (floor is None or mm >= floor)
        rows.append({"window_h": h, "mm": round(mm, 1), "percentile": pct,
                     "counted": counted,
                     "floor_mm": floor})
        if counted and pct > best_p:
            best_p, best_w = pct, h

    # Terrain gate BEFORE the rainfall verdict. A percentile exceedance over
    # flat ground is noise by construction, and letting it through is what
    # makes system alerts scale with station count.
    band = (cell or {}).get("band")
    if cell is not None and band not in c["watchable_bands"]:
        return {"tier": "log", "suppressed_by": "terrain",
                "band": band, "driving_window_h": best_w,
                "driving_percentile": best_p or None, "windows": rows,
                "why": f"terrain band {band!r} cannot initiate a landslide; "
                       f"rainfall not evaluated"}

    # Co-signal: was the slope primed as well as hit? Reported, never required.
    def _best(ws):
        vals = [r["percentile"] for r in rows
                if r["window_h"] in ws and r["percentile"] is not None]
        return max(vals) if vals else None
    ante, burst = _best(c["antecedent_windows"]), _best(c["burst_windows"])
    primed = ante is not None and ante >= c["primed_percentile"]
    triggered = burst is not None and burst >= c["triggered_percentile"]

    tier = "watch" if best_p >= c["watch_percentile"] else "log"
    return {
        "tier": tier, "max_tier": MAX_TIER,
        "band": band,
        "antecedent_percentile": ante,
        "burst_percentile": burst,
        "primed_and_triggered": bool(primed and triggered),
        "pattern": ("primed and triggered" if primed and triggered
                    else "burst on unprimed ground" if triggered
                    else "wet antecedent, no burst" if primed else "neither"),
        "driving_window_h": best_w,
        "driving_percentile": best_p or None,
        "threshold_percentile": c["watch_percentile"],
        "windows": rows,
        "basis": "percentile of this site's own Open-Meteo climatology",
        "caveat": "WATCH ONLY. Backtest: 5/5 recall on Kerala disasters at "
                  "~10 alerts/yr/site against a gate of 2. Not an evacuation "
                  "trigger. Sub-daily skill is unvalidated -- no hourly gauge "
                  "truth exists to check it against.",
    }


def evaluate(cell, cfg=None, now_index=-1):
    """Assess one cell against the live feed. Returns the assessment."""
    lat, lon = cell["lat"], cell["lon"]
    times, series = recent(lat, lon)
    clim = climatology(lat, lon)
    i = (len(series) + now_index) if now_index < 0 else now_index
    a = assess(accumulations_at(series, i), clim, cell, cfg)
    return {**a, "lat": lat, "lon": lon, "at": times[i],
            "site": cell.get("name") or f"{lat:.3f},{lon:.3f}"}


def load_cells(path=None):
    """
    Watch cells from the terrain scan. Steep ground only.

    Returns (cells, coverage). The coverage half is NOT optional decoration:
    a partial scan means absence of a cell says "not scanned", never "safe",
    and a caller that ignores the distinction will conclude an unscanned
    valley is fine. Callers must surface it -- see `coverage_warning`.
    """
    p = path or os.path.join(DATA_DIR, "kerala_cells.json")
    with open(p) as f:
        d = json.load(f)
    if not isinstance(d, dict):
        return d, {"complete": None, "WARNING": "no coverage record"}
    return d["cells"], {**d.get("coverage", {}), "complete": d.get("complete")}


def coverage_warning(coverage):
    """A one-line statement of what the scan cannot speak for, or None."""
    if coverage.get("complete"):
        return None
    bands = coverage.get("unscanned_latitude_bands") or []
    if not bands:
        return "Terrain scan is incomplete; unscanned areas are not 'safe'."
    spans = ", ".join(f"{a}-{b}N" for a, b in bands)
    return (f"TERRAIN SCAN INCOMPLETE. Unscanned: {spans}. No cell exists "
            f"there, which means NOT SCANNED, not safe.")
