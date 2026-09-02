"""
Mode 2 — co-seismic cascade.

The blind spot: a M7.8 is rejected as tectonic, which discards the single
largest trigger of catastrophic mass wasting. Gorkha 2015 buried Langtang
village; replayed through the point-source detector it produces zero
dispatches across 36 hours and 76 catalogued events.
"""

import pytest
from hew import cascade
from hew.registry import load_registry

R = load_registry()


def test_radius_grows_with_magnitude_and_is_monotonic():
    prev = 0
    for m in [4.0, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0]:
        r = cascade.shaking_radius_km(m)
        assert r >= prev, f"radius fell at M{m}"
        prev = r


def test_radius_is_clamped_at_both_ends():
    assert cascade.shaking_radius_km(2.0) == cascade.KEEFER_LIMIT[0][1]
    assert cascade.shaking_radius_km(9.9) == cascade.KEEFER_LIMIT[-1][1]
    assert cascade.shaking_radius_km(None) == 0.0


def test_small_shallow_events_stay_in_mode_one():
    """The 26 Aug event is a point source, not a regional trigger."""
    assert cascade.applies(0.0, 5.2) is False
    assert cascade.assess(28.271, 85.515, 0.0, 5.2, R) is None


def test_gorkha_is_a_regional_trigger_and_finds_shaken_hazards():
    """The event the point-source detector is silent for."""
    a = cascade.assess(28.15, 84.71, 15.0, 7.8, R)
    assert a is not None
    assert a["shaking_radius_km"] > 200
    assert a["sites_in_footprint"] > 50, "no mapped hazards inside a M7.8 footprint"
    # ordered by PRIORITY, not by raw distance — that is the whole point
    pri = [x["priority"] for x in a["sites"]]
    assert pri == sorted(pri, reverse=True)


def test_deep_events_are_not_treated_as_surface_triggers():
    assert cascade.applies(300.0, 7.5) is False


def test_output_is_capped_and_says_so():
    a = cascade.assess(28.15, 84.71, 15.0, 7.8, R,
                       {"cascade_max_sites": 5})
    assert len(a["sites"]) == 5
    assert a["truncated"] == a["sites_in_footprint"] - 5


def test_assessment_is_not_an_alert_tier():
    """It must never be fed to the public alert templates."""
    a = cascade.assess(28.15, 84.71, 15.0, 7.8, R)
    assert a["mode"] == "cascade"
    assert "tier" not in a
    assert "INSPECT" in a["action"]
    assert "not a ground-motion model" in a["caveat"]


def test_ranking_beats_raw_distance():
    """Nearest-to-epicentre is not most-dangerous. A big steep glacier
    further out should outrank a small flat one close in."""
    sites = [
        {"name": "near_small_flat", "lat": 0, "lon": 0, "distance_km": 5,
         "area_km2": 0.1, "slope_deg": 12},
        {"name": "far_big_steep", "lat": 0, "lon": 0, "distance_km": 60,
         "area_km2": 40.0, "slope_deg": 38},
    ]
    r = cascade.rank(sites, 100.0)
    assert r[0]["name"] == "far_big_steep"


def test_ranking_is_transparent():
    """Every component must be visible, not just the total."""
    r = cascade.rank([{"name": "x", "lat": 0, "lon": 0, "distance_km": 10,
                       "area_km2": 5.0, "slope_deg": 30}], 100.0)[0]
    for k in ("shaking", "mass", "steepness", "exposure", "priority"):
        assert k in r
    assert r["priority"] == pytest.approx(
        100 * (r["shaking"] + r["mass"] + r["steepness"] + 2 * r["exposure"]) / 5,
        abs=0.2)


def test_exposure_survives_hazards_with_no_routed_corridor():
    """The river network covers one basin; the registry covers the whole box.
    Hazards outside the routed basin must return 0, not raise."""
    sites = [{"name": "outside", "lat": 27.0, "lon": 88.5, "distance_km": 10,
              "area_km2": 1.0, "slope_deg": 30, "priority": 50}]
    out = cascade.with_exposure(sites, top_n=1)
    assert out[0]["downstream_settlements"] == 0


def test_exposure_only_routes_the_top_n():
    sites = [{"name": f"s{i}", "lat": 28.2, "lon": 85.5, "distance_km": i,
              "area_km2": 1.0, "slope_deg": 30, "priority": 100 - i}
             for i in range(6)]
    out = cascade.with_exposure(sites, top_n=2)
    assert len(out) == 6
    assert "downstream_settlements" in out[0]
    assert "downstream_settlements" not in out[5]


def test_exposure_outranks_raw_hazard_size():
    """
    The Gorkha lesson, asserted. Ranked on hazard properties alone the
    largest steepest glaciers came top and every one had zero settlements
    below it; Langtang, where ~350 died, sat at #128 of 4,480 and never
    appeared in the 40 sites the system reports. Exposure is weighted double
    for that reason.
    """
    places = [{"name": f"v{i}", "lat": 28.20 + i * 0.002, "lon": 85.55,
               "population": None, "kind": "village"} for i in range(8)]
    sites = [
        {"name": "huge_empty", "lat": 29.5, "lon": 84.0, "distance_km": 10,
         "area_km2": 35.0, "slope_deg": 40},
        {"name": "modest_inhabited", "lat": 28.21, "lon": 85.55,
         "distance_km": 60, "area_km2": 5.0, "slope_deg": 30},
    ]
    r = cascade.rank(sites, 100.0, places=places)
    assert r[0]["name"] == "modest_inhabited", \
        "ranked the biggest ice above the biggest consequence"
    assert r[0]["settlements_near"] >= 6


def test_ranking_responds_to_the_epicentre():
    """
    The test that caught two bad rankings. Gorkha and Dolakha are 125 km
    apart; if they produce the same answer, this is not an event-response
    product, it is a static map of where glaciers and villages coexist.
    """
    import os
    from hew.routing import load_settlements, DATA_DIR
    wide = os.path.join(DATA_DIR, "places_region.json")
    if not os.path.exists(wide):
        pytest.skip("region-wide places not fetched")
    a = cascade.assess(28.15, 84.71, 15.0, 7.8, R)   # Gorkha
    b = cascade.assess(28.64, 87.36, 10.0, 7.1, R)   # Dingri, 260 km east
    ca = {(round(s["lat"], 2), round(s["lon"], 2)) for s in a["sites"]}
    cb = {(round(s["lat"], 2), round(s["lon"], 2)) for s in b["sites"]}
    # Some overlap is legitimate -- the footprints genuinely intersect and a
    # hazard in the middle is shaken by both. What must not happen is the two
    # lists being largely the same, which is what a static ranking produces.
    overlap = len(ca & cb) / max(1, min(len(ca), len(cb)))
    assert overlap < 0.25, (
        f"{overlap:.0%} of the top-40 shared between epicentres 260 km apart "
        "— the ranking is not responding to the event")
    # Deliberately NOT asserting the top-40 clusters near the epicentre.
    # Dingri sits on the Tibetan plateau and the nearest inhabited glacial
    # terrain is Khumbu, ~105 km south; a mean distance of 105 km against a
    # 163 km radius is the right answer, not a failure. Exposure is supposed
    # to pull the list toward people, wherever they are.


def test_shaking_attenuates_faster_than_linearly():
    """A linear 1 - d/R left a hazard 250 km out scoring 0.15, which drowned
    the epicentre signal under the static exposure term."""
    r = cascade.rank([
        {"name": "near", "lat": 0, "lon": 0, "distance_km": 15,
         "area_km2": 1, "slope_deg": 30},
        {"name": "far", "lat": 0, "lon": 0, "distance_km": 150,
         "area_km2": 1, "slope_deg": 30},
    ], 300.0)
    near = next(x for x in r if x["name"] == "near")
    far = next(x for x in r if x["name"] == "far")
    assert near["shaking"] > 5 * far["shaking"]
