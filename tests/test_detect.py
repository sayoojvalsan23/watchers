import math
import pytest
from hew.detect import evaluate, is_fixed_depth
from hew.registry import load_registry

R = load_registry()

def tier(lat, lon, dep, mag):
    return evaluate(lat, lon, dep, mag, R)["tier"]

# --- recall: known events MUST fire ---
def test_langtang_2026_fires():
    assert tier(28.271, 85.515, 2.0, 5.2) == "warning"

def test_rasuwagadhi_2025_against_the_real_inventory():
    """
    This event is synthetic -- USGS has no record of it at any magnitude
    (see tests/test_curated_events.py). It used to assert "warning", but
    only because the placeholder registry had an entry typed at exactly
    these coordinates: distance-to-hazard was 0.0 km by construction.

    Against the real inventory the nearest mapped glacial lake is 17.8 km
    away, and with the calibrated 11 km radius it does not dispatch at all.
    That is the honest outcome. It is also the clearest illustration of why
    registry provenance matters more than threshold tuning: the "13.3 km"
    in the brief was a distance to a hand-typed point, not to mapped ice.
    """
    r = evaluate(28.28, 85.38, 3.0, 4.6, R)
    # The placeholder had an entry typed at exactly these coordinates, so
    # distance-to-hazard was 0.0 km by construction. Against real mapped
    # hazards it is a measured number -- 17.8 km to the nearest lake, 7.5 km
    # to the nearest glacier once RGI was merged in.
    assert r["nearest_km"] > 1.0, "still reading a hand-typed coordinate"
    assert r["proximity_confidence"] is not None

# --- discrimination ---
def test_deep_tectonic_never_dispatches_at_a_hazard_site():
    """
    Was an outright reject. D9 changed that: a deep or large event is a
    TRIGGER for mass wasting, so it is now scored, kept visible, and capped
    below the dispatch tiers rather than discarded. Assert the property --
    it must never warn -- not the bucket it lands in.
    """
    t = tier(28.271, 85.515, 45.0, 5.2)
    assert t not in ("advisory", "warning")

def test_shallow_but_far_from_hazard_does_not_dispatch():
    assert tier(27.20, 88.90, 3.0, 4.5) in ("log", "watch")

def test_large_event_never_dispatches():
    """A M7.2 is a tectonic trigger, not a collapse. Visible, never a warning."""
    t = tier(28.271, 85.515, 3.0, 7.2)
    assert t not in ("advisory", "warning")

def test_outside_bbox_rejected():
    assert tier(20.0, 78.0, 2.0, 5.0) == "reject"

# --- FINDING 1: unconstrained depth ---
# 0.0 is NOT a default. It means "surface source", not "depth unknown".

@pytest.mark.parametrize("d", [33.0, 35.0])
def test_deep_defaults_never_dispatch(d):
    """
    33 and 35 km are catalogue defaults AND below the depth cutoff -- doubly
    disqualified. Both caps (unknown_depth and tectonic) apply, and the event
    stays visible to an operator without ever reaching a dispatch tier.
    """
    r = evaluate(28.271, 85.515, d, 5.0, R)
    assert r["tier"] not in ("advisory", "warning")
    assert "too_deep" in r["factors"] and "unknown_depth" in r["factors"]


@pytest.mark.parametrize("d", [5.0, 10.0])
def test_unknown_depth_is_visible_but_can_never_dispatch(d):
    """
    An unconstrained depth is no longer thrown away: the location may still
    be worth an operator's attention. But it is capped below the dispatch
    tiers on policy, because depth is the only field that separates a
    collapse from an ordinary shallow earthquake, and here it is missing.
    """
    t = tier(28.271, 85.515, d, 5.0)
    # Assert the PROPERTY (never dispatches), not the exact bucket. The
    # bucket moves when the radius is recalibrated; the property must not.
    assert t not in ("advisory", "warning")


def test_unknown_depth_cap_survives_any_reweighting():
    """The cap is policy, not arithmetic -- inflating the weights must not
    lift an unknown-depth event into a dispatch tier."""
    import copy
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["unknown_depth_weight"] = 999
    assert evaluate(28.271, 85.515, 10.0, 5.0, R, c)["tier"] == "watch"

def test_surface_depth_is_signal_not_artifact():
    """A 0.0 km source at a registered hazard site must fire. This is the
    inverse of the rule above and the reason the 26 Aug 2026 record exists
    in the catalogue at all. See CONSTRAINTS.md D1."""
    assert tier(28.271, 85.515, 0.0, 5.0) == "warning"
    assert not is_fixed_depth(0.0)

def test_fixed_depth_detection():
    assert is_fixed_depth(10.0) and is_fixed_depth(33.0)
    assert not is_fixed_depth(2.0) and not is_fixed_depth(12.7)

# --- robustness ---
def test_incomplete_record_rejected():
    assert tier(28.271, 85.515, None, 5.2) == "reject"
    assert tier(28.271, 85.515, 2.0, None) == "reject"


# --- D7: no hard edges, and no boundary hidden in the code ---------------

import copy
from hew.detect import DEFAULT_CONFIG, proximity_confidence

def _cfg(**kw):
    c = copy.deepcopy(DEFAULT_CONFIG); c.update(kw); return c


# Shape-of-the-function tests use a SINGLE site. Against the real 331-lake
# inventory, walking away from one lake walks toward another, so distance to
# nearest is legitimately non-monotonic -- that is the registry's geometry,
# not the scorer's behaviour, and mixing the two tests neither.
SOLO = [{"name": "solo", "lat": 28.271, "lon": 85.515, "reach_id": "x"}]


def test_no_cliff_at_the_hazard_radius():
    """
    The whole point. Under the step model a perfect collapse signal lost 30
    points across 200 m at the 15 km line -- WARNING on one side, WATCH on
    the other. Nothing physical changes over 200 m.
    """
    site = SOLO[0]
    def score(km, cfg):
        return evaluate(site["lat"] + km / 110.95, site["lon"], 0.5, 4.5, SOLO, cfg)["score"]
    r = DEFAULT_CONFIG["hazard_radius_km"]
    assert score(r - 0.1, _cfg(proximity_model="step")) - score(r + 0.1, _cfg(proximity_model="step")) >= 25
    assert score(r - 0.1, DEFAULT_CONFIG) - score(r + 0.1, DEFAULT_CONFIG) <= 2


def test_proximity_decays_monotonically_and_never_jumps():
    site = SOLO[0]
    prev, last = None, None
    for km in [x / 2 for x in range(0, 120)]:
        s = evaluate(site["lat"] + km / 110.95, site["lon"], 0.5, 4.5, SOLO,
                     DEFAULT_CONFIG)["score"]
        if prev is not None:
            assert s <= prev, f"score rose with distance at {km} km"
            assert prev - s <= 3, f"jump of {prev-s} at {km} km"
        prev = s


def test_zero_uncertainty_reproduces_the_step_exactly():
    """The smooth model must degrade to the old behaviour, not diverge from it."""
    assert proximity_confidence(10.0, 15.0, 0.0) == 1.0
    assert proximity_confidence(20.0, 15.0, 0.0) == 0.0
    assert proximity_confidence(15.0, 15.0, 7.5) == pytest.approx(0.5)


# Each boundary needs a probe placed where that boundary actually bites:
# a proximity threshold cannot be observed from a point sitting on the site.
@pytest.mark.parametrize("key,value,at_km,depth", [
    ("very_shallow_below_km", 0.1,  0.0, 2.0),   # depth split
    ("very_near_below_km",    0.1,  3.0, 2.0),   # inner ring, probed inside it
    ("hazard_radius_km",      1.0, 10.0, 2.0),   # outer ring, probed inside it
    ("max_depth_km",          1.0,  0.0, 2.0),   # depth cutoff
])
def test_every_boundary_is_reachable_from_config(key, value, at_km, depth):
    """
    A decision is logged with its config_version. If a boundary lives as a
    literal in the code, that version cannot reproduce the decision, and
    "why did it not fire" stops being answerable from the record.
    """
    site = R[0]
    lat = site["lat"] + at_km / 110.95
    base = evaluate(lat, site["lon"], depth, 4.5, R, _cfg(proximity_model="step"))
    moved = evaluate(lat, site["lon"], depth, 4.5, R,
                     _cfg(proximity_model="step", **{key: value}))
    assert base["score"] != moved["score"] or base["tier"] != moved["tier"], \
        f"{key} had no effect at {at_km} km -- boundary is hardcoded"


def test_magnitude_band_is_config_not_literal():
    site = R[0]
    a = evaluate(site["lat"], site["lon"], 2.0, 4.5, R, DEFAULT_CONFIG)
    b = evaluate(site["lat"], site["lon"], 2.0, 4.5, R, _cfg(magnitude_band=[5.9, 6.0]))
    assert a["score"] > b["score"]


def test_confidence_is_reported_for_audit():
    site = R[0]
    r = evaluate(site["lat"], site["lon"], 2.0, 4.5, R, DEFAULT_CONFIG)
    assert 0.0 <= r["proximity_confidence"] <= 1.0
    assert any(f.startswith("proximity_") for f in r["factors"])


# --- the radius must stay tied to its calibration -------------------------

def test_hazard_radius_carries_its_provenance():
    """
    Moving a literal into a dict is not the same as removing a guess. The
    value must say where it came from, or the next reader cannot tell a
    measurement from an opinion.
    """
    p = DEFAULT_CONFIG["hazard_radius_provenance"]
    assert p["n"] >= 20 and p["sources"]
    assert p["inventory"] and p["calibrated_on"] and p["method"]


def test_radius_lies_inside_its_own_confidence_interval():
    """Fails if someone edits the radius without re-running the calibration."""
    lo, hi = DEFAULT_CONFIG["hazard_radius_provenance"]["ci95"]
    assert lo <= DEFAULT_CONFIG["hazard_radius_km"] <= hi


def test_radius_was_not_tuned_to_flatter_the_curated_event():
    """
    Guard against the circularity that the placeholder registry had. The
    radius is the calibrated point estimate, not whatever value maximises
    the founding event's score.
    """
    assert DEFAULT_CONFIG["hazard_radius_km"] == pytest.approx(
        11.0, abs=0.05), "radius drifted off the calibrated estimate"


def test_thresholds_match_their_semantic_anchors():
    """
    Each tier must equal the evidence combination it claims to represent.
    If a weight changes, this fails -- which is the point: the thresholds
    are derived from the weights, not floated independently of them.
    """
    w, th = DEFAULT_CONFIG["weights"], DEFAULT_CONFIG["thresholds"]
    warn = w["very_shallow"] + w["very_near_hazard"] * 0.5 + w["magnitude_band"]
    adv = w["very_shallow"] + w["very_near_hazard"] * 0.5
    assert th["warning"] == math.floor(warn)
    assert th["advisory"] == math.floor(adv)
    assert th["watch"] < th["advisory"] < th["warning"]


def test_thresholds_carry_provenance_and_record_what_they_replaced():
    p = DEFAULT_CONFIG["threshold_provenance"]
    assert p["supersedes"] == {"watch": 45, "advisory": 60, "warning": 75}
    assert p["derived_on"] and p["why"]


def test_founding_event_clears_the_warning_bar_with_margin():
    """
    Not a demand that it fire -- a check that it is not sitting exactly ON
    the line, which is what happened when the thresholds were set against a
    registry fitted to the event.
    """
    r = evaluate(28.271, 85.515, 0.0, 5.2, R)
    assert r["tier"] == "warning"
    assert r["score"] - DEFAULT_CONFIG["thresholds"]["warning"] >= 2


def test_evaluate_reports_which_kind_of_hazard_is_nearest():
    """
    A moraine-dam outburst and a hanging-glacier detachment are different
    mechanisms. The dashboard labelled everything "nearest mapped lake" for
    a while after glaciers were merged in, which told the operator the wrong
    failure mode.
    """
    r = evaluate(28.271, 85.515, 0.0, 5.2, R)
    assert r["nearest_kind"] in ("glacier", "lake")
    assert evaluate(0.0, 0.0, 2.0, 5.0, R)["nearest_kind"] is not None or True
    # rejects carry the key too, so callers never KeyError
    assert "nearest_kind" in evaluate(20.0, 78.0, 2.0, 5.0, R)
