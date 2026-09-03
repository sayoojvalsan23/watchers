"""
Deterministic detection filter.

No ML. A weighted rule score, because it must be explainable to a hydrologist
who decides whether to trust it.

Findings from the Phase 0 simulation, both applied here:

  FINDING 1 — Fixed-depth artifact.
    Catalogues assign a default depth (commonly exactly 10.0 km, also 33/35 km)
    when depth is poorly constrained. Reading "unknown depth" as "shallow" is
    backwards and accounted for ~38% of simulated false positives.
    Fixed-depth events are now rejected outright.

    0.0 km IS NOT ONE OF THESE. It is the opposite signal: the catalogue's
    marker for a source at the surface, which is what a landslide or ice
    collapse is. Measured against the real USGS catalogue:

        wider Himalaya, 830 events M>=3, 2015-2026
          depth 10.0 km   624 events   "depth unconstrained"
          depth  0.0 km     2 events   both landslide-typed, nothing else

        all 56 landslide-typed events USGS has catalogued globally
          55 sit at or above the surface (41 at exactly 0.0, 14 negative)

    Rejecting 0.0 discarded the 26 Aug 2026 Langtang record (us7000tbwb,
    M5.2, depth 0.0, type=landslide) -- the founding event of this project.
    It cost 100% of curated recall to suppress a false-positive class that
    has never once appeared in this region. Restoring it costs 0.17
    alerts/yr. See ADR and CONSTRAINTS.md D1.

  FINDING 2 — Registry completeness is the binding constraint.
    Tightening hazard proximity to 5 km cut alerts to 0.3/yr but MISSED
    Rasuwagadhi 2025, whose source lay 13.3 km from the nearest registered
    site. You cannot tune past a hazard that is not in the list.
    Radius stays at 15 km; registry coverage is a Phase 0 dependency.
"""

import math

# Depths commonly used as catalogue defaults when depth is UNCONSTRAINED.
# 0.0 is deliberately absent: it means "surface source", not "unknown". A
# depth at or below 0.0 is signal and must reach the scorer.
FIXED_DEPTHS = {5.0, 10.0, 33.0, 35.0}
FIXED_DEPTH_EPS = 0.05

DEFAULT_CONFIG = {
    "version": "v3-himalaya-74e",
    # The whole arc, not one basin. The previous box (27-30 N, 84-89 E) was
    # the binding constraint on coverage and it was invisible as such: events
    # outside it were never fetched, so they appear in no metric and no reject
    # reason. Kedarnath 2013 sat 510 km outside it, Chamoli 2021 449 km.
    # Widening the box widens what is FETCHED; the false-alarm rate this
    # implies is re-measured by the Phase 0 backtest, which is a kill gate.
    # NOTE the margin over the registry extent (26-37.5 N, 71-96 E). A hazard
    # sitting on the box edge otherwise has a truncated detection radius: an
    # event 5 km outside is rejected as out-of-area even though it is well
    # inside hazard_radius_km (11) plus source_uncertainty_km (15). The margin
    # is 0.5 deg (~55 km), comfortably more than that 26 km interaction range.
    # Western edge moved 70.5 -> 74.0 E. Everything west of 74 is the Hindu
    # Kush and Pamir, not the Himalaya: it held 12% of mapped hazards but
    # produced 49% of catalogue events (1,936 of 3,982), because the Hindu
    # Kush intermediate-depth seismic zone emits a constant stream of
    # 100-250 km events that can never be a surface collapse. Measured:
    # ceiling 3.77 -> 3.08 alerts/yr, measured rate 1.20 -> 0.86.
    #
    # Every curated event survives, Nanga Parbat (74.59 E) and Shishper
    # (74.55 E) closest to the edge with ~65 km of margin -- more than the
    # 26 km hazard_radius + source_uncertainty interaction range.
    #
    # This does NOT reach the gate. 3.08 is still TUNE, not GO. It is done
    # because the box should match the name, not to manufacture a pass.
    "bbox": {"min_lat": 25.5, "max_lat": 38.0,
             "min_lon": 74.0, "max_lon": 96.5},
    "min_magnitude": 3.0,
    "max_magnitude": 6.5,
    "max_depth_km": 10.0,
    # CALIBRATED, not guessed. The value and its justification travel
    # together: a decision is logged with config_version, so the version
    # must also say where the number came from. Re-run
    # scripts/calibrate_radius.py after any inventory or sample change.
    "hazard_radius_km": 11.0,
    "hazard_radius_provenance": {
        "method": "p54 of distance from confirmed events to nearest mapped hazard",
        "sources": ["bipad_avalanche"],
        "n": 22,
        "ci95": [8.2, 20.9],
        "inventory": "NSIDC HMA Near-Global Glacial Lake Inventory v1, "
                     "2015-2018 epoch, 331 lakes in the study box",
        "calibrated_on": "2026-08-31",
        "note": "n=22 supports a median, not a p90. Treat as provisional; "
                "the CI is wider than half the estimate.",
    },
    "reject_fixed_depth": True,
    # Every boundary the scorer uses lives here. Nothing that moves a decision
    # may be a literal in the code: a decision is logged with its
    # config_version, and that version must be enough to reproduce it.
    "very_shallow_below_km": 5.0,
    "very_near_below_km": 5.0,
    "magnitude_band": [3.5, 6.0],
    # "smooth" scores proximity as the PROBABILITY the source was within
    # hazard_radius_km, given that the reported location is itself uncertain.
    # "step" is the original hard-edged behaviour, kept so the two can be
    # compared through the Phase 0 gate rather than argued about.
    "proximity_model": "smooth",
    # What to do with an event whose depth the catalogue could not constrain.
    #   "reject"  throw it away (original Finding 1 behaviour)
    #   "cap"     score it on location and size, but never let it dispatch
    # "cap" keeps the event visible without pretending we know what it was:
    # an unconstrained depth cannot distinguish a collapse from an ordinary
    # shallow earthquake, and location alone is not evidence enough to warn.
    # A scalar threshold on a SUM cannot express "proximity must be better
    # than even" -- other evidence combinations reach the same total by a
    # different route. This makes the claim real: no dispatch unless the
    # source is more likely than not inside the calibrated radius. Policy,
    # not arithmetic, so no reweighting can bypass it.
    "min_proximity_confidence_to_dispatch": 0.5,
    # A large or deep event is a TRIGGER for mass wasting, not a collapse
    # itself (D9). Rejecting it discarded the single largest cause of
    # catastrophic secondary failures -- Gorkha 2015 buried Langtang village.
    # It is now scored and capped, so it reaches an operator without ever
    # producing a public flood warning.
    "tectonic_event_max_tier": "watch",
    "unknown_depth_policy": "cap",
    "unknown_depth_weight": 0,
    "unknown_depth_max_tier": "watch",
    # Derived from what each tier MEANS, not from a target alert count and
    # not from the sample. Anchors, using the weights below:
    #   WARNING  35 (surface) + 22.5 (proximity better than even) + 15 (band)
    #            = 72.5  -> "all three lines agree, and the source is more
    #                        likely than not inside the calibrated radius"
    #   ADVISORY 35 + 22.5 = 57.5 -> "surface and probably close, but one
    #                        line of evidence missing"
    #   WATCH    45 -> one strong line; unchanged.
    # Floors are used so that proximity of exactly 0.50 clears the bar.
    "thresholds": {"watch": 45, "advisory": 57, "warning": 72},
    "threshold_provenance": {
        "method": "semantic anchors on evidence combinations",
        "warning": "very_shallow + very_near_hazard*0.50 + magnitude_band",
        "advisory": "very_shallow + very_near_hazard*0.50",
        "derived_on": "2026-08-31",
        "supersedes": {"watch": 45, "advisory": 60, "warning": 75},
        "why": "the old set was chosen when the registry was circular and "
               "every curated event scored 95 by construction (nearest "
               "hazard 0.0 km, typed in). Against real mapped hazards the "
               "achievable maximum is 92 and the founding event scored "
               "exactly 75 -- the threshold sat on top of a data point.",
        "note": "an empirical gap exists at 60-74 in the 573-event sample, "
                "so 65-72 all give identical dispatch behaviour. The anchor "
                "was preferred over the gap midpoint because the gap is a "
                "feature of a sparse sample and will fill in; the definition "
                "will not.",
    },
    "weights": {
        "very_shallow": 35,
        "shallow": 20,
        "very_near_hazard": 45,
        "near_hazard": 30,
        "magnitude_band": 15,
    },
    "circuit_breaker": {"window_hours": 24, "max_dispatched": 3},
    # Source-location uncertainty used by downstream ROUTING only. Does not
    # affect evaluate() or the Phase 0 gate. Near a confluence a location
    # error sends the corridor down the wrong branch, so routing takes the
    # union of every channel within this radius. See CONSTRAINTS.md D6.
    "source_uncertainty_km": 15.0,
}


def is_fixed_depth(d):
    return any(abs(d - f) < FIXED_DEPTH_EPS for f in FIXED_DEPTHS)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def proximity_confidence(dist_km, radius_km, sigma_km):
    """
    Probability that the source really lay within radius_km of the site.

    A hard `dist <= 15.0` test asserts the location is known exactly. It is
    not. Preliminary origins are uncertain by roughly the same distance as
    the radius itself, so a step function compares a razor edge against a
    fuzzy input, and a hazard at 15.1 km scores the same as one at 300 km.

    Modelling the location error as Gaussian turns the question into one
    that can actually be answered: given where we think it was and how
    badly we might be wrong, how likely is it that it was close enough?

    sigma_km <= 0 reproduces the old step behaviour exactly.
    """
    if sigma_km <= 0:
        return 1.0 if dist_km <= radius_km else 0.0
    z = (radius_km - dist_km) / (sigma_km * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def nearest_hazard(lat, lon, registry):
    best, best_d = None, float("inf")
    for site in registry:
        d = haversine_km(lat, lon, site["lat"], site["lon"])
        if d < best_d:
            best, best_d = site, d
    return best, best_d


def evaluate(lat, lon, depth_km, magnitude, registry, config=None):
    """
    Returns dict with score, tier, factors, nearest site.
    Tiers: reject | log | watch | advisory | warning
    """
    cfg = config or DEFAULT_CONFIG
    w, th = cfg["weights"], cfg["thresholds"]
    factors = []

    site, dist = nearest_hazard(lat, lon, registry)
    sigma = cfg.get("source_uncertainty_km", 0.0) / 2.0
    if cfg.get("proximity_model", "step") == "smooth":
        conf = proximity_confidence(dist, cfg["hazard_radius_km"], sigma)
    else:
        conf = None

    def reject(reason):
        return _reject(reason, site, dist, conf)

    bb = cfg["bbox"]
    if not (bb["min_lat"] <= lat <= bb["max_lat"]
            and bb["min_lon"] <= lon <= bb["max_lon"]):
        return reject("outside_bbox")
    if depth_km is None or magnitude is None:
        return reject("incomplete_record")
        
    is_tectonic = False
    if not (cfg["min_magnitude"] <= magnitude <= cfg["max_magnitude"]):
        factors.append("magnitude_out_of_range")
        is_tectonic = True

    # FINDING 1
    unknown_depth = is_fixed_depth(depth_km)
    policy = cfg.get("unknown_depth_policy", "reject")
    if unknown_depth and (policy == "reject" or cfg.get("reject_fixed_depth")
                          and policy not in ("cap",)):
        return reject(f"fixed_depth_artifact_{depth_km}")

    if depth_km > cfg["max_depth_km"]:
        factors.append("too_deep")
        is_tectonic = True

    score = 0
    if unknown_depth:
        score += cfg.get("unknown_depth_weight", 0)
        factors.append("unknown_depth")
    elif depth_km > cfg["max_depth_km"]:
        pass # Too deep gets 0 points for depth
    elif depth_km < cfg["very_shallow_below_km"]:
        score += w["very_shallow"]; factors.append("very_shallow")
    else:
        score += w["shallow"]; factors.append("shallow")

    if cfg.get("proximity_model", "step") == "smooth":
        prox = w["very_near_hazard"] * conf
        score += prox
        factors.append(f"proximity_{conf:.2f}")
    else:
        if dist <= cfg["very_near_below_km"]:
            score += w["very_near_hazard"]; factors.append("very_near_hazard")
        elif dist <= cfg["hazard_radius_km"]:
            score += w["near_hazard"]; factors.append("near_hazard")

    mb_lo, mb_hi = cfg["magnitude_band"]
    if mb_lo <= magnitude <= mb_hi:
        score += w["magnitude_band"]; factors.append("magnitude_band")

    score = int(round(score))
    if score >= th["warning"]:
        tier = "warning"
    elif score >= th["advisory"]:
        tier = "advisory"
    elif score >= th["watch"]:
        tier = "watch"
    else:
        tier = "log"

    # Proximity gate. The advisory/warning anchors are defined as "surface
    # source AND proximity better than even"; without this, a weak-proximity
    # event can reach the same score by another route and dispatch anyway.
    floor = cfg.get("min_proximity_confidence_to_dispatch")
    if (floor is not None and conf is not None and conf < floor
            and tier in ("advisory", "warning")):
        tier = "watch"
        factors.append(f"capped_watch_proximity_{conf:.2f}<{floor}")

    # An unconstrained depth cannot separate a collapse from an ordinary
    # shallow earthquake. Location may still be interesting, so the event
    # stays visible -- but it is capped below the dispatch tiers, on policy
    # rather than on arithmetic, so no weighting change can lift it.
    if unknown_depth:
        cap = cfg.get("unknown_depth_max_tier")
        order = ["log", "watch", "advisory", "warning"]
        if cap in order and order.index(tier) > order.index(cap):
            tier = cap
            factors.append(f"capped_{cap}_unknown_depth")
            
    if is_tectonic:
        cap = cfg.get("tectonic_event_max_tier", "watch")
        order = ["log", "watch", "advisory", "warning"]
        if cap in order and order.index(tier) > order.index(cap):
            tier = cap
            factors.append(f"capped_{cap}_tectonic")

    return {
        "score": score, "tier": tier, "factors": tuple(factors),
        "nearest_site": site["name"] if site else None,
        "nearest_km": round(dist, 1) if site else None,
        # Which KIND of hazard. A moraine-dam failure and a hanging-glacier
        # detachment are different mechanisms with different onsets; the UI
        # said "lake" for everything long after glaciers were merged in.
        "nearest_kind": site.get("kind") if site else None,
        "proximity_confidence": round(conf, 3) if conf is not None else None,
        "reach_id": site.get("reach_id") if site else None,
    }


def _reject(reason, site=None, dist=None, conf=None):
    return {"score": 0, "tier": "reject", "factors": (reason,),
            "nearest_site": site["name"] if site else None,
            "nearest_km": round(dist, 1) if site else None,
            "nearest_kind": site.get("kind") if site else None,
            "proximity_confidence": round(conf, 3) if conf is not None else None,
            "reach_id": site.get("reach_id") if site else None}
