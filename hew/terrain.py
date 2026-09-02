"""
Terrain susceptibility from a free elevation API.

WHY THIS EXISTS
---------------
The rainfall feed we can actually poll (Open-Meteo) under-reads Western Ghats
orographic extremes by 2-4x, and the error is not a stable bias that could be
corrected away. Measured against KSDMA gauges over 92 days, six global models
ranged 0.16x to 1.23x of truth at the same nine stations.

That rules out absolute millimetre thresholds. It does NOT rule out
percentile-of-own-climatology thresholds, which cancel a model's systematic
bias by comparing the model only to itself. Those failed earlier for a
different reason: MULTIPLICITY. A per-station percentile fires at a fixed rate
per station, so system alerts scale with station count -- 65 stations at
p99.9 produced 14.8 alerts/year against a gate of 2.

Terrain is the fix for multiplicity, not for measurement. Landslides initiate
on steep ground; a percentile exceedance over Alappuzha at 5 m elevation is
noise by construction. Screening to genuinely steep cells cuts the number of
places that can fire by roughly six, which is the difference between the
method passing and failing.

    terrain does not tell you WHETHER a slope fails.
    it tells you WHERE failure is possible, so the rainfall
    signal is only evaluated where it could mean anything.

WHERE THE DATA COMES FROM
-------------------------
Open-Meteo's elevation endpoint, which serves the Copernicus DEM at ~90 m.
Free, no API key, no registration, batchable, and the same provider as the
rainfall feed -- so the whole rainfall track carries exactly one attribution
requirement (CC BY 4.0) and one point of failure.

    https://api.open-meteo.com/v1/elevation

WHAT THE NUMBERS MEAN, AND WHAT THEY DO NOT
--------------------------------------------
`relief_m` is the elevation range across the sampled window and `slope_deg` is
the gradient implied by it. These are LANDSCAPE measures, not site measures:
Chooralmala samples at ~15 deg over 4 km while the failure scarp itself is
nearer 38 deg. That understatement is expected and acceptable for a SCREEN --
it separates Ghats from lowland by an order of magnitude -- but these values
must never be presented as the slope angle of a specific hillside, and must
not be fed to any physical stability model.

A CAVEAT THAT BIT ONCE ALREADY
-------------------------------
The screen is only as good as the coordinate handed to it. Probing "Nilambur
town" instead of the Kavalappara scarp returned 58 m of relief and screened
out a site where 59 people died in 2019. A hand-typed list of place names
will keep producing that error quietly.

The right foundation is therefore NOT a curated site list but a grid scan of
the terrain itself (see `scan_region`), where cells fall out of the DEM rather
than out of somebody's memory of which villages are dangerous.

Results are cached to disk. Terrain does not change; this should be computed
once and then read forever.
"""

import json
import math
import os
import time
import urllib.parse
import urllib.request

ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
UA = {"User-Agent": "hew-terrain/1.0 (Himalayan Early Warning; Phase 5)"}
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")
CACHE = os.path.join(DATA_DIR, "terrain_cache.json")

# The API accepts many coordinates per call, but Open-Meteo prices requests by
# LOCATION, not by HTTP call -- a 100-coordinate request costs ~100 against the
# quota. Batching therefore saves round-trips, not allowance. The free tier
# allows roughly 600 locations/minute, 5,000/hour, 10,000/day, and exceeding
# it returns 429. PACE accordingly: the default pause below keeps a 100-point
# batch under the per-minute ceiling.
BATCH = 100
PACE_SECONDS = 10.0          # 100 locations per 10 s = 600/min, the ceiling
MAX_RETRIES = 5

# Sampling window for the susceptibility screen: 5x5 over ~4 km.
#
# A 1 km window was tried first and FAILED on the sites that matter. Towns sit
# on valley floors; the slope that kills them is above them. At 1 km, Meppadi
# scored 38 m of relief and Vythiri 54 m -- both "gentle", both screened out,
# both in the Wayanad disaster area. The question is not "is this point
# steep" but "is there steep ground within a few km of it".
#
# At 4 km the separation is clean (relief in metres):
#
#     landslide sites   Chooralmala 1090, Pettimudi 1055, Devikulam 821,
#                       Puthumala 725, Vagamon 597, Munnar 306,
#                       Vythiri 308, Meppadi 243
#     lowland towns     Kottayam 35, Palakkad 33, Kollam 21,
#                       Thrissur 19, Alappuzha 13
#
# An order of magnitude between the two groups, with no overlap.
WINDOW_DEG = 0.009           # ~1 km spacing, ~4 km span at 5x5
GRID = 5

# Susceptibility bands, in metres of relief across the ~4 km window.
# Deliberately coarse: this is a screen, and pretending to finer resolution
# than a 90 m DEM over a 4 km window supports would be false precision.
# Thresholds sit in the empty ground between the two groups above. The
# lowland maximum measured was 35 m and the lowest landslide site 243 m
# (excluding one whose coordinate was wrong -- see the caveat below), so the
# 200 m cut is comfortably clear of both.
BANDS = [(400.0, "steep"), (200.0, "moderate"), (60.0, "gentle"), (0.0, "flat")]

# Only these can ever raise a watch. Everything else is screened out before
# the rainfall signal is even evaluated -- see the module docstring.
WATCHABLE = {"steep", "moderate"}


def _load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(c):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f)
    os.replace(tmp, CACHE)          # never leave a half-written cache


def _key(lat, lon):
    return f"{lat:.4f},{lon:.4f}"


def elevations(points, cache=None, pause=None):
    """
    Elevation in metres for [(lat, lon), ...], cached on disk.

    Batched, because one call per point would burn the daily quota on a task
    that only ever needs to run once.
    """
    cache = _load_cache() if cache is None else cache
    todo = [p for p in points if _key(*p) not in cache]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{a:.4f}" for a, _ in chunk),
            "longitude": ",".join(f"{b:.4f}" for _, b in chunk)})
        req = urllib.request.Request(f"{ELEVATION_API}?{q}", headers=UA)
        got = None
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    got = json.loads(r.read().decode())["elevation"]
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == MAX_RETRIES - 1:
                    _save_cache(cache)      # never lose what we already paid for
                    raise
                # Backing off on 429 is not politeness, it is the only way the
                # scan finishes: hammering a rate limiter just extends it.
                time.sleep(min(120.0, PACE_SECONDS * (2 ** (attempt + 1))))
        if len(got) != len(chunk):          # never silently mis-pair
            raise RuntimeError(f"elevation API returned {len(got)} for "
                               f"{len(chunk)} points")
        for p, e in zip(chunk, got):
            cache[_key(*p)] = e
        if i + BATCH < len(todo):
            time.sleep(PACE_SECONDS if pause is None else pause)
    if todo:
        _save_cache(cache)
    return [cache[_key(*p)] for p in points]


def _grid(lat, lon, window=WINDOW_DEG, n=GRID):
    """n x n sample points centred on (lat, lon), corrected for longitude."""
    kx = max(0.2, math.cos(math.radians(lat)))
    half = (n - 1) / 2.0
    return [(lat + (i - half) * window, lon + (j - half) * window / kx)
            for i in range(n) for j in range(n)]


def susceptibility(lat, lon, cache=None, window=WINDOW_DEG, n=GRID):
    """
    Terrain screen for one point.

    Returns relief, an implied slope, the band, and whether this cell is
    allowed to raise a watch at all. `slope_deg` is the gradient over the
    sampling window -- a landscape measure, NOT a hillside angle.
    """
    pts = _grid(lat, lon, window, n)
    e = elevations(pts, cache)
    relief = max(e) - min(e)
    span_m = window * 111000.0 * (n - 1)
    slope = math.degrees(math.atan(relief / span_m)) if span_m else 0.0
    band = next(b for t, b in BANDS if relief >= t)
    return {
        "lat": round(lat, 4), "lon": round(lon, 4),
        "elev_m": round(e[len(e) // 2], 1),
        "relief_m": round(relief, 1),
        "slope_deg": round(slope, 1),
        "band": band,
        "watchable": band in WATCHABLE,
        "basis": f"Copernicus DEM ~90 m via Open-Meteo, {n}x{n} over "
                 f"{span_m/1000:.1f} km. Landscape relief, not hillside angle.",
    }


def screen(sites, cache=None):
    """Attach terrain to a list of {name, lat, lon, ...} and mark watchable."""
    c = _load_cache() if cache is None else cache
    out = []
    for s in sites:
        t = susceptibility(s["lat"], s["lon"], c)
        out.append({**s, **{k: v for k, v in t.items() if k not in ("lat", "lon")}})
    return out


def scan_region(lat0, lat1, lon0, lon1, step=0.05, cache=None, progress=None):
    """
    Sweep a bounding box and return every cell with its terrain class.

    This is the principled alternative to a hand-written site list. Cells fall
    out of the DEM, so the scan cannot quietly omit a dangerous slope because
    nobody remembered it -- which is exactly how Kavalappara was missed.

    `step` is the cell size in degrees; 0.05 is ~5.5 km. Each cell costs
    GRID*GRID elevation samples, batched, so a Kerala-sized box is a few
    hundred API calls against a 10,000/day allowance -- run once, cached
    forever.
    """
    cache = _load_cache() if cache is None else cache
    cells, todo = [], []
    lat = lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            cells.append((round(lat, 4), round(lon, 4)))
            lon += step
        lat += step
    # Prefetch every sample point in batches before computing, so the whole
    # scan is one pass over the API rather than a call per cell.
    for la, lo in cells:
        todo.extend(_grid(la, lo))
    seen, uniq = set(), []
    for p in todo:
        k = _key(*p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    missing = [p for p in uniq if _key(*p) not in cache]
    for i in range(0, len(missing), BATCH):
        elevations(missing[i:i + BATCH], cache, pause=0.0)
        if progress and (i // BATCH) % 25 == 0:
            progress(i, len(missing))
        time.sleep(0.25)
    _save_cache(cache)
    return [susceptibility(la, lo, cache) for la, lo in cells]


if __name__ == "__main__":
    import sys
    probes = [("Chooralmala/Mundakkai", 11.4750, 76.1300),
              ("Meppadi", 11.5450, 76.1400),
              ("Vythiri", 11.5500, 76.0400),
              ("Munnar", 10.0890, 77.0600),
              ("Devikulam", 10.0500, 77.1000),
              ("Nilambur/Kavalappara", 11.2800, 76.2200),
              ("Thamarassery churam", 11.4100, 75.9500),
              ("Vagamon", 9.6860, 76.9040),
              ("Kottayam town", 9.5916, 76.5222),
              ("Alappuzha coast", 9.4981, 76.3388)]
    if len(sys.argv) == 3:
        probes = [("cli", float(sys.argv[1]), float(sys.argv[2]))]
    print(f"{'site':<24}{'elev':>7}{'relief':>8}{'slope':>7}  {'band':<10}watchable")
    print("-" * 66)
    for n, la, lo in probes:
        t = susceptibility(la, lo)
        print(f"{n:<24}{t['elev_m']:>7.0f}{t['relief_m']:>8.0f}"
              f"{t['slope_deg']:>7.1f}  {t['band']:<10}{t['watchable']}")
