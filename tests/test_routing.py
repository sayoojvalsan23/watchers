"""
Downstream routing validation.

The brief states that Mailung, Betrawati, Bidur and Devighat lie 30-100 km
below the 26 Aug 2026 source and had usable lead time. These tests check
that the router reproduces that ordering and band from open data alone.
"""

import pytest
from hew.routing import RiverNetwork, load_settlements, exposed_settlements

SOURCE_LAT, SOURCE_LON = 28.271, 85.515   # us7000tbwb


@pytest.fixture(scope="module")
def corridor():
    net = RiverNetwork.load()
    path, snap = net.trace(SOURCE_LAT, SOURCE_LON, max_km=200)
    return path, snap, exposed_settlements(path, load_settlements(), corridor_km=2.0)


def test_trace_reaches_the_trunk_river(corridor):
    path, _, _ = corridor
    channels = {p["channel"] for p in path}
    assert any("Trishuli" in c or "Trisuli" in c for c in channels)
    assert path[-1]["river_km"] > 100


def test_brief_settlements_appear_in_order(corridor):
    _, _, ex = corridor
    order = [s["name"] for s in ex]
    idx = {n: order.index(n) for n in ("Mailung", "Betrawati", "Bidur", "Devighat")}
    assert idx["Mailung"] < idx["Betrawati"] < idx["Bidur"] < idx["Devighat"]


def test_brief_settlements_fall_in_the_30_to_100km_band(corridor):
    """The brief's claim, checked against open data rather than restated."""
    _, _, ex = corridor
    km = {s["name"]: s["river_km"] for s in ex}
    for town in ("Mailung", "Betrawati", "Bidur", "Devighat"):
        assert 30.0 <= km[town] <= 100.0, f"{town} at {km[town]} km"


def test_bidur_is_the_population_centre(corridor):
    _, _, ex = corridor
    bidur = next(s for s in ex if s["name"] == "Bidur")
    assert bidur["population"] > 20000
    assert bidur["kind"] == "town"


def test_snap_distance_is_reported_not_hidden(corridor):
    """The source is on the mountain, not in the channel. That gap is real
    and must be visible to the caller -- overland routing is not modelled."""
    _, snap, _ = corridor
    assert snap > 1.0


# --- source-location uncertainty ------------------------------------------
# The 2026 registry entry is named "Langtang Lirung / Lhende". The Lende
# Khola is the Rasuwagadhi-side tributary, ~13 km west across a divide, and
# it joins the Trishuli at the same confluence. Which branch the debris
# entered decides the entire near-field corridor, and the near-field is
# where lead time is shortest.

from hew.routing import trace_branches, exposed_settlements_union
from hew.detect import DEFAULT_CONFIG


def test_zero_uncertainty_traces_the_same_channel_as_a_bare_trace(corridor):
    """
    Same geometry, but river_km differs by exactly the snap offset:
    net.trace() counts from where the channel was met, trace_branches()
    counts from the source. Asserting they are equal would re-introduce the
    two-bases bug the re-basing exists to remove.
    """
    path, snap, _ = corridor
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=0.0)
    assert len(br) == 1
    assert len(br[0]["path"]) == len(path)
    assert br[0]["path"][-1]["river_km"] == pytest.approx(
        path[-1]["river_km"] + snap, abs=0.05)
    assert br[0]["path"][-1]["channel_km"] == pytest.approx(
        path[-1]["river_km"], abs=0.05)


def test_uncertainty_picks_up_the_sibling_tributary():
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    names = {b["name"] for b in br}
    assert len(br) >= 2, f"expected the Bhote Koshi branch too, got {names}"
    assert any("Lende" in n for n in names), names


def test_union_corridor_includes_rasuwagadhi():
    """The single-branch corridor structurally excludes it; the union must not."""
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    ex = exposed_settlements_union(br, load_settlements(), corridor_km=2.0)
    names = {s["name"] for s in ex}
    assert any("Rasuwa" in n for n in names), "Rasuwagadhi missing from union"
    assert "Khangjim" in names


def test_union_is_a_superset_of_the_single_branch():
    """
    Compared on the SAME basis. The `corridor` fixture calls net.trace()
    directly, whose river_km counts from the snap point; trace_branches()
    re-bases onto the source, so a fixed max_river_km cap truncates the two
    at different physical places and the comparison is meaningless.
    Zero uncertainty gives the single-branch case with identical semantics.
    """
    net = RiverNetwork.load()
    places = load_settlements()
    one = exposed_settlements_union(
        trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=0.0),
        places, corridor_km=2.0)
    many = exposed_settlements_union(
        trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0),
        places, corridor_km=2.0)
    assert {s["name"] for s in one} <= {s["name"] for s in many}
    assert len(many) > len(one)


def test_config_carries_the_uncertainty_radius():
    """Recorded with the config version so a corridor can be reproduced."""
    assert DEFAULT_CONFIG["source_uncertainty_km"] > 0


def test_branches_share_one_origin():
    """
    Branches snap at different distances from the source (5.9 km and 10.3 km
    for the 26 Aug event). If each numbers from its own zero, "0.0 km" means
    two places 10 km apart and the public distance bands are meaningless.
    """
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    assert len(br) >= 2
    starts = [b["path"][0]["river_km"] for b in br]
    assert len(set(starts)) == len(starts), f"branches share a start km: {starts}"
    for b in br:
        assert b["path"][0]["river_km"] == pytest.approx(b["snap_km"], abs=0.05)


def test_river_km_is_never_less_than_the_straight_line_distance():
    """A river cannot reach somewhere in less distance than a straight line."""
    from hew.routing import haversine_km
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    ex = exposed_settlements_union(br, load_settlements(), corridor_km=2.0)
    for s in ex:
        straight = haversine_km(SOURCE_LAT, SOURCE_LON, s["lat"], s["lon"])
        assert s["river_km"] >= straight - 2.5, (
            f"{s['name']} at river_km {s['river_km']} but {straight:.1f} km "
            "straight line — river_km is not measured from the source")


def test_settlements_upstream_of_a_branch_start_are_excluded():
    """
    A trace begins where the uncertainty circle meets water. Settlements
    laterally near that first vertex but BEHIND it are not downstream of
    anything -- reporting them tells people above the source that water is
    coming at them. Four villages beside the Lende snap point did exactly
    that until the filter was applied per-branch.
    """
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    ex = exposed_settlements_union(br, load_settlements(), corridor_km=2.0)
    names = {s["name"] for s in ex}
    for upstream in ("Baxuequ’an", "本波", "Galong"):
        assert upstream not in names, f"{upstream} is upstream of the branch start"
    # and the genuinely downstream ones survive
    for kept in ("Rasuwa Gadhi", "Mailung", "Betrawati", "Bidur", "Devighat"):
        assert kept in names, f"{kept} was wrongly dropped"


def test_upstream_filter_is_applied_per_branch_not_on_the_merged_path():
    """
    Regression guard. Merging paths first buries each branch's vertex 0
    mid-list, so an index-0 test silently never fires.
    """
    from hew.routing import _is_upstream_of_start
    net = RiverNetwork.load()
    br = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=15.0)
    lende = [b for b in br if "Lende" in b["name"]][0]
    behind = {"lat": 28.3369, "lon": 85.4639}          # 本波
    assert _is_upstream_of_start(behind, lende["path"]) is True
    ahead = {"lat": lende["path"][40]["lat"], "lon": lende["path"][40]["lon"]}
    assert _is_upstream_of_start(ahead, lende["path"]) is False


# --- population accounting ------------------------------------------------

def test_population_summary_separates_recorded_from_estimated():
    """
    OSM records population for ~1.6% of places here. A single total would be
    mostly invented, so the summary must keep what is known apart from what
    is guessed.
    """
    from hew.routing import population_summary
    s = population_summary([
        {"name": "a", "population": 26750, "kind": "town"},
        {"name": "b", "population": None, "kind": "hamlet"},
        {"name": "c", "population": None, "kind": "village"},
    ])
    assert s["recorded"] == 26750 and s["recorded_places"] == 1
    assert s["unknown_places"] == 2
    assert s["estimated_low"] > s["recorded"]
    assert s["estimated_high"] > s["estimated_low"]
    assert "overlap" in s["caveat"]


def test_population_band_is_wide_not_a_point_estimate():
    """A hamlet in this dataset runs from 10 to 18,000 people. Any band that
    looks precise is lying."""
    from hew.routing import population_summary
    s = population_summary([{"name": f"h{i}", "population": None,
                             "kind": "hamlet"} for i in range(20)])
    assert s["estimated_high"] >= 5 * max(1, s["estimated_low"])


def test_empty_corridor_does_not_divide_by_zero():
    from hew.routing import population_summary
    s = population_summary([])
    assert s["recorded"] == 0 and s["unknown_places"] == 0


def test_river_km_basis_does_not_depend_on_the_uncertainty_parameter():
    """
    Regression. trace_branches had an early return for zero uncertainty that
    skipped re-basing, so the same call reported distances from the snap
    point in one mode and from the source in the other. A fixed
    max_river_km then truncated the two at different physical places.
    """
    net = RiverNetwork.load()
    zero = trace_branches(net, SOURCE_LAT, SOURCE_LON, uncertainty_km=0.0)[0]
    assert zero["path"][0]["river_km"] == pytest.approx(zero["snap_km"], abs=0.05)
    assert "channel_km" in zero["path"][0]
    assert zero["path"][0]["channel_km"] == pytest.approx(0.0, abs=0.05)
