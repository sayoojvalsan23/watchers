"""
Mode 2 — co-seismic cascade assessment.

THE BLIND SPOT THIS CLOSES
--------------------------
detect.evaluate() rejects anything above max_magnitude as tectonic. In
isolation that is right: a M7.8 thrust rupture is not a glacier collapse.
But it discards the single largest TRIGGER of catastrophic mass wasting.
Gorkha 2015 (M7.8) set off tens of thousands of co-seismic landslides and
brought the hanging glaciers down onto Langtang village. Huascaran 1970
(M7.9, 300 km offshore) killed ~20,000 the same way. Replayed against the
real 36 hours around Gorkha -- 76 catalogued events within 80 km -- the
point-source detector produces ZERO dispatches. It is silent during the
period of maximum landslide risk in modern Himalayan history.

So a large earthquake is not "not a hazard". It is a DIFFERENT hazard, and
it needs a different question:

    Mode 1  a point source. Where did this one thing fail, and what is
            downstream of it?
    Mode 2  a region. Which mapped hazards were shaken hard enough to be
            worth inspecting, across every valley?

Mode 2 produces an ASSESSMENT, not an alert. It does not say a flood is
coming; it says "these catchments were shaken above a threshold at which
slopes and moraine dams historically fail, go and look at them". That is a
responder-tier product, and it must stay one: co-seismic failures are
scattered, delayed by hours to weeks, and unpredictable individually.

THE SHAKING RADIUS
------------------
The distance limit is empirical, from the classic observation that the
maximum epicentral distance of earthquake-triggered landslides scales with
magnitude (Keefer 1984 and successors). The table below is an approximation
of that relation and is NOT a ground-motion model: there is no PGA
calculation here, no site amplification, no directivity.

    *** THESE NUMBERS NEED SOURCING FROM THE PRIMARY LITERATURE BEFORE ANY
    *** OPERATIONAL USE. They are the right shape and the wrong precision.

A real implementation should consume USGS ShakeMap where it exists, which
gives modelled PGA rather than a distance proxy -- at the cost of another
feed with its own latency.
"""

import math

from .detect import haversine_km

# Approximate maximum epicentral distance (km) of triggered landsliding,
# by magnitude. Interpolated in log10(distance). See the warning above.
KEEFER_LIMIT = [
    (4.0, 5.0),
    (5.0, 20.0),
    (6.0, 60.0),
    (7.0, 150.0),
    (8.0, 350.0),
]

# Inside this distance the shaking term saturates: ground motion stops
# growing meaningfully once you are on top of the rupture.
NEAR_FIELD_KM = 15.0

DEFAULTS = {
    # Below this, use Mode 1. At or above it, a regional assessment.
    "cascade_min_magnitude": 6.0,
    # Crustal events shake the surface hardest. Deep ones are attenuated;
    # this is a crude proxy for that, not a physical model.
    "cascade_max_depth_km": 70.0,
    # Cap the output. An M8 shakes thousands of mapped hazards and a list of
    # thousands is not a responder product.
    "cascade_max_sites": 40,
}


def shaking_radius_km(magnitude):
    """
    Approximate outer limit of triggered landsliding. Log-linear
    interpolation of KEEFER_LIMIT; clamped at the ends.
    """
    if magnitude is None:
        return 0.0
    pts = KEEFER_LIMIT
    if magnitude <= pts[0][0]:
        return pts[0][1]
    if magnitude >= pts[-1][0]:
        return pts[-1][1]
    for (m1, d1), (m2, d2) in zip(pts, pts[1:]):
        if m1 <= magnitude <= m2:
            f = (magnitude - m1) / (m2 - m1)
            return round(10 ** (math.log10(d1) + f * (math.log10(d2) - math.log10(d1))), 1)
    return pts[-1][1]


def applies(depth_km, magnitude, cfg=None):
    """Is this event a regional trigger rather than a point source?"""
    c = {**DEFAULTS, **(cfg or {})}
    if magnitude is None or depth_km is None:
        return False
    return (magnitude >= c["cascade_min_magnitude"]
            and depth_km <= c["cascade_max_depth_km"])


def _settlement_index(places, cell=0.1):
    """Grid index so exposure can be counted for thousands of sites cheaply."""
    grid = {}
    for p in places:
        grid.setdefault((int(p["lat"] / cell), int(p["lon"] / cell)), []).append(p)
    return grid, cell


def _settlements_near(lat, lon, grid, cell, km):
    span = int(km / 111.0 / cell) + 1
    gy, gx = int(lat / cell), int(lon / cell)
    n = 0
    for dy in range(-span, span + 1):
        for dx in range(-span, span + 1):
            for p in grid.get((gy + dy, gx + dx), ()):
                if haversine_km(lat, lon, p["lat"], p["lon"]) <= km:
                    n += 1
    return n


def rank(sites, radius_km, places=None, exposure_km=8.0):
    """
    Order shaken hazards by how much they warrant an inspector's time.

    A M7.8 shakes thousands of mapped hazards and a handful fail. This does
    NOT predict which: nothing in this data can. It narrows a haystack into
    a queue, using the three discriminators the inventories actually give us.

        shaking   closer to the epicentre was shaken harder, falling off
                  as 1/distance with a near-field floor. NOT a ground-motion
                  model, but the right SHAPE: a linear 1 - d/R was so flat
                  that a hazard 250 km out still scored 0.15, and the whole
                  ordering collapsed into a static map of where glaciers and
                  villages coexist. Gorkha and Dolakha -- epicentres 125 km
                  apart -- produced an identical top six. An event-response
                  product that returns the same answer regardless of the
                  event is not an event-response product.
        mass      bigger bodies deliver more material downstream. Log-scaled:
                  the difference between 1 and 10 km2 matters far more than
                  between 80 and 90.
        steepness slope is where failures concentrate. Rises from 20 deg and
                  saturates at 40; below ~15 deg detachment is unlikely.
        exposure  how many settlements sit within exposure_km. Counted on a
                  grid index so it is affordable for thousands of sites.

    EXPOSURE IS WEIGHTED DOUBLE, and it was learned the hard way. Ranked on
    hazard properties alone, the largest and steepest glaciers came top --
    and every one of them had ZERO settlements within 8 km. Langtang, where
    ~350 people died under a modest 4-12 km2 glacier, ranked #126 of 4,480,
    far outside the 40 sites this ever reports. The system optimised for the
    biggest ice when the question is the biggest consequence.

    Weights remain crude. They are a triage order, not a probability, and
    they should be calibrated against a co-seismic landslide inventory
    (Gorkha has one) before anyone relies on the ordering.
    """
    grid = cell = None
    if places:
        grid, cell = _settlement_index(places)
    out = []
    for s in sites:
        d = s.get("distance_km", radius_km)
        # 1/R attenuation, saturating inside NEAR_FIELD_KM, zero past the
        # footprint. Real peak ground motion falls off at least this fast.
        if not radius_km or d > radius_km:
            shaking = 0.0
        else:
            shaking = min(1.0, NEAR_FIELD_KM / max(d, NEAR_FIELD_KM))
        area = float(s.get("area_km2") or 0.0)
        mass = min(1.0, math.log10(1 + area) / math.log10(1 + 50.0))
        slope = float(s.get("slope_deg") or 0.0)
        steep = min(1.0, max(0.0, (slope - 20.0) / 20.0))
        n_near = (_settlements_near(s["lat"], s["lon"], grid, cell, exposure_km)
                  if grid else 0)
        expo = min(1.0, n_near / 6.0)
        score = round(100 * (shaking + mass + steep + 2 * expo) / 5, 1)
        out.append({**s, "shaking": round(shaking, 2), "mass": round(mass, 2),
                    "steepness": round(steep, 2), "exposure": round(expo, 2),
                    "settlements_near": n_near, "priority": score})
    out.sort(key=lambda x: -x["priority"])
    return out


def with_exposure(sites, top_n=10, corridor_km=2.0):
    """
    Route the top-ranked hazards and attach who is downstream.

    This is what turns a triage list into an inspection plan. A steep, large,
    hard-shaken glacier above an empty valley is a geology problem; the same
    glacier above Bidur is an evacuation problem, and the ranking above
    cannot tell them apart because it never looks downstream.

    Deliberately only the top N: routing costs seconds per site and a M7.8
    shakes thousands. Slow is acceptable here -- Mode 2 is a responder
    product measured in minutes, not the 30-second trigger budget.
    """
    from .detect import DEFAULT_CONFIG
    from .routing import (RiverNetwork, load_settlements, trace_branches,
                          exposed_settlements_union)
    net, places = RiverNetwork.load(), load_settlements()
    unc = DEFAULT_CONFIG.get("source_uncertainty_km", 0)
    out = []
    for s in sites[:top_n]:
        try:
            br = trace_branches(net, s["lat"], s["lon"], uncertainty_km=unc)
            ex = exposed_settlements_union(br, places, corridor_km)
            pop = sum(x["population"] or 0 for x in ex)
            out.append({**s, "downstream_settlements": len(ex),
                        "downstream_population": pop,
                        "corridor_km": round(ex[-1]["river_km"], 1) if ex else 0,
                        "first_settlement": ex[0]["name"] if ex else None})
        except Exception as e:                    # never break the assessment
            out.append({**s, "downstream_settlements": None, "routing_error": str(e)})
    return out + list(sites[top_n:])


def assess(lat, lon, depth_km, magnitude, registry, cfg=None):
    """
    Which mapped hazards fall inside the shaking footprint.

    Returns an assessment dict. Deliberately NOT a tier from the Mode 1
    ladder: this does not compete with advisory/warning and must never be
    fed to the public alert templates. It is an inspection list.
    """
    c = {**DEFAULTS, **(cfg or {})}
    if not applies(depth_km, magnitude, c):
        return None
    r = shaking_radius_km(magnitude)
    hits = []
    for s in registry:
        d = haversine_km(lat, lon, s["lat"], s["lon"])
        if d <= r:
            hits.append({**s, "distance_km": round(d, 1)})
    try:
        # REGION-WIDE places, not the Trishuli-basin file routing uses.
        # Exposure is 40% of the ranking; computing it against a one-valley
        # settlement list made every large earthquake point at that valley,
        # because it was the only place the system could see people.
        import os as _os
        from .routing import load_settlements, DATA_DIR
        wide = _os.path.join(DATA_DIR, "places_region.json")
        places = load_settlements(wide if _os.path.exists(wide) else None)
    except Exception:
        places = None
    ranked = rank(hits, r, places=places)
    truncated = max(0, len(ranked) - c["cascade_max_sites"])
    return {
        "mode": "cascade",
        "magnitude": magnitude,
        "depth_km": depth_km,
        "shaking_radius_km": r,
        "sites_in_footprint": len(hits),
        "ranking": "shaking + mass + steepness + 2x exposure. A triage "
                   "order, NOT a probability of failure.",
        "sites": ranked[:c["cascade_max_sites"]],
        "truncated": truncated,
        "action": "INSPECT — regional assessment, not an alert. These mapped "
                  "hazards were shaken inside the empirical limit for "
                  "triggered landsliding. Co-seismic failures are scattered "
                  "and can lag the earthquake by hours to weeks.",
        "caveat": "Distance proxy, not a ground-motion model. No PGA, no site "
                  "amplification, no directivity. Radius needs sourcing from "
                  "the primary literature before operational use.",
    }
