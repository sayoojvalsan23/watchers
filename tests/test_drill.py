"""
Drill scenarios and the random generator.

Drills are synthetic and must stay that way: nothing here may reach the
decision ledger or the Phase 0 counts.
"""

import os
import collections
import pytest

from hew.drill import SCENARIOS, random_event
from hew.detect import evaluate
from hew.registry import load_registry
from hew.routing import load_settlements, DATA_DIR

R = load_registry()


@pytest.fixture(scope="module")
def places():
    wide = os.path.join(DATA_DIR, "places_region.json")
    return load_settlements(wide if os.path.exists(wide) else None)


def test_scenarios_are_well_formed():
    for k, v in SCENARIOS.items():
        label, lat, lon, dep, mag, note = v
        assert 27 <= lat <= 31 or k == "jajarkot_2023"
        assert 0.0 <= dep <= 60 and 3.0 <= mag <= 8.0
        assert note, f"{k} has no note explaining what it demonstrates"


def test_the_two_key_scenarios_still_behave():
    """D5 in two lines: the same event, before and after 13 hours of review."""
    _, la, lo, d, m, _ = SCENARIOS["langtang_2026"]
    assert evaluate(la, lo, d, m, R)["tier"] == "warning"
    _, la, lo, d, m, _ = SCENARIOS["as_published"]
    assert evaluate(la, lo, d, m, R)["tier"] not in ("advisory", "warning")


def test_random_drill_is_reproducible_from_its_seed(places):
    a = random_event(R, places, seed=1234)
    b = random_event(R, places, seed=1234)
    assert a == b
    assert a[1:5] != random_event(R, places, seed=1235)[1:5]


def test_random_drill_lands_somewhere_inhabited(places):
    """Weighted by exposure — a drill in an empty basin teaches nothing."""
    from hew.cascade import _settlement_index, _settlements_near
    grid, cell = _settlement_index(places)
    for seed in range(20):
        _, la, lo, _, _, _, _ = random_event(R, places, seed=seed)
        assert _settlements_near(la, lo, grid, cell, 25.0) > 0


def test_random_drill_stays_inside_the_domain(places):
    for seed in range(30):
        _, la, lo, d, m, _, _ = random_event(R, places, seed=seed)
        assert 26.5 <= la <= 30.5 and 83.5 <= lo <= 89.5
        assert 0.0 <= d <= 15.0 and 3.5 <= m <= 6.3


def test_random_drills_produce_a_spread_not_one_answer(places):
    """
    Sampling tightly onto inventory centroids made every drill a WARNING at
    91-92. A drill set that always returns the same answer cannot show an
    operator what a marginal call or a rejection looks like.
    """
    tiers = collections.Counter()
    for seed in range(60):
        _, la, lo, d, m, _, _ = random_event(R, places, seed=seed)
        tiers[evaluate(la, lo, d, m, R)["tier"]] += 1
    assert len(tiers) >= 3, f"only {len(tiers)} distinct outcomes: {dict(tiers)}"
    # Not asserting rejections specifically: since D9 nothing is discarded
    # outright, so a filtered event now surfaces as log or watch instead.
    non_dispatch = sum(v for k, v in tiers.items()
                       if k not in ("advisory", "warning"))
    assert non_dispatch > 0, "every drill dispatched — no marginal calls shown"
