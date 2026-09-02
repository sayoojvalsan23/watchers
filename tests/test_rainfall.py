"""
Phase 5 rainfall engine.

The tests that matter are not about arithmetic. They are that the engine
reports the feed's blindness rather than laundering it into a confident tier.
"""

import datetime
import pytest
from hew import rainfall as R


def test_rolling_accumulation():
    s = [1.0] * 10
    r = R.rolling(s, 3)
    assert r[0] == 1.0 and r[2] == 3.0 and r[9] == 3.0


def test_percentile_of_places_a_value_in_its_ladder():
    lad = [float(i) for i in range(100)]
    assert R.percentile_of(0, lad) == 0.0
    assert R.percentile_of(50, lad) == 50.0
    assert R.percentile_of(999, lad) == 100.0
    assert R.percentile_of(1, []) is None


def test_assessment_reports_millimetres_as_well_as_percentile():
    """
    A percentile alone hides a feed reading 7x low; millimetres alone hide
    whether the number is unusual here. Both, always.
    """
    clim = {h: [float(i) for i in range(1000)] for h in R.WINDOWS_H}
    a = R.assess({h: 999.0 for h in R.WINDOWS_H}, clim)   # p99.8+
    for w in a["windows"]:
        assert "mm" in w and "percentile" in w
    assert a["tier"] == "warning"


def test_a_dry_location_cannot_be_tipped_by_a_trivial_amount():
    """
    A dry place's 99.8th percentile can be a few millimetres. Without a floor
    the engine would warn on drizzle.
    """
    clim = {h: [0.0] * 990 + [float(i) for i in range(10)] for h in R.WINDOWS_H}
    a = R.assess({h: 5.0 for h in R.WINDOWS_H}, clim)
    assert a["tier"] == "log", "warned on 5 mm because the location is dry"


def test_tiers_rise_with_percentile():
    clim = {h: [float(i) for i in range(1000)] for h in R.WINDOWS_H}
    seen = []
    for v in (500.0, 960.0, 992.0, 999.0):   # p50, p96, p99.2, p99.9
        seen.append(R.assess({h: v for h in R.WINDOWS_H}, clim)["tier"])
    assert seen == ["log", "watch", "advisory", "warning"]


def test_the_caveat_is_carried_with_every_assessment():
    """The under-reporting must travel with the number, not live in a doc."""
    clim = {h: [float(i) for i in range(100)] for h in R.WINDOWS_H}
    a = R.assess({h: 50.0 for h in R.WINDOWS_H}, clim)
    assert "under-report" in a["caveat"] or "under-reports" in a["caveat"]
    assert "percentile" in a["basis"]


@pytest.mark.skipif(not __import__("os").path.isdir(
    __import__("os").path.join(R.DATA_DIR, "rain_cache")),
    reason="rain cache not populated")
def test_chooralmala_does_not_reach_advisory_on_this_feed():
    """
    The Phase 5 kill-gate result, pinned. ~250 people died; on Open-Meteo
    ERA5 against ten years of local climatology this reads as a WATCH.
    If this test ever starts failing, the feed changed -- go and re-run the
    gate before celebrating.
    """
    clim = R.climatology(11.47, 76.13, years=10, end=datetime.date(2024, 7, 1))
    d = R.fetch_hourly(11.47, 76.13, "2024-07-20", "2024-08-01")
    p = [x or 0.0 for x in d["hourly"]["precipitation"]]
    i = d["hourly"]["time"].index("2024-07-29T20:00")
    a = R.assess(R.accumulations_at(p, i), clim)
    assert a["tier"] not in ("warning", "advisory"), \
        f"feed now reaches {a['tier']} -- re-run the Phase 5 gate"
    w72 = next(x for x in a["windows"] if x["window_h"] == 72)
    assert w72["percentile"] < 95, "72h antecedent still below the local p95"


def test_every_window_has_a_floor():
    """
    Regression. Floors were declared for 24 and 72 h only, so six of the
    eight windows were unfloored and a dry location tipped through the 1 h.
    """
    floors = R.DEFAULTS["min_mm_to_consider"]
    assert set(floors) == set(R.WINDOWS_H), "a window has no floor"
    ordered = [floors[h] for h in sorted(floors)]
    assert ordered == sorted(ordered), "floors must rise with duration"


def test_floors_cannot_mask_chooralmala():
    """The floors must sit below the readings of the event we calibrate on."""
    f = R.DEFAULTS["min_mm_to_consider"]
    assert f[12] < 39.9 and f[24] < 50.9 and f[72] < 71.8
