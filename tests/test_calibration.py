"""
Calibration statistics.

The point of these tests is not that the arithmetic is right -- it is that
the script REFUSES to produce a number the sample cannot support. Quoting a
95th percentile from 22 events is how a guess acquires the appearance of
evidence, which is worse than an honest guess.
"""

import importlib.util
import os
import pytest

_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "scripts", "calibrate_radius.py")
_s = importlib.util.spec_from_file_location("calib", _p)
calib = importlib.util.module_from_spec(_s)
_s.loader.exec_module(calib)


def test_quantile_interpolates():
    v = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert calib.quantile(v, 0.0) == 0.0
    assert calib.quantile(v, 1.0) == 40.0
    assert calib.quantile(v, 0.5) == 20.0
    assert calib.quantile(v, 0.25) == 10.0


def test_tiny_samples_support_no_percentile_at_all():
    for n in (1, 5, 19):
        assert calib.max_defensible_quantile(n) is None


def test_the_supportable_quantile_rises_with_sample_size():
    assert calib.max_defensible_quantile(22) == pytest.approx(0.545, abs=0.01)
    assert calib.max_defensible_quantile(100) == pytest.approx(0.90, abs=0.01)
    assert calib.max_defensible_quantile(1000) == pytest.approx(0.99, abs=0.01)


def test_a_p95_needs_roughly_two_hundred_samples():
    """The headline claim in the docstring, asserted rather than asserted-at."""
    assert calib.max_defensible_quantile(199) < 0.95
    assert calib.max_defensible_quantile(200) >= 0.95


def test_bootstrap_interval_brackets_the_estimate_and_is_deterministic():
    vals = [float(x) for x in range(100)]
    lo, hi = calib.bootstrap_ci(vals, 0.5, 500)
    assert lo <= calib.quantile(sorted(vals), 0.5) <= hi
    assert (lo, hi) == calib.bootstrap_ci(vals, 0.5, 500)   # same seed, same answer


def test_narrow_data_gives_a_narrow_interval():
    tight = [10.0 + (i % 3) * 0.1 for i in range(300)]
    spread = [float(i) for i in range(300)]
    lo1, hi1 = calib.bootstrap_ci(tight, 0.5, 400)
    lo2, hi2 = calib.bootstrap_ci(spread, 0.5, 400)
    assert (hi1 - lo1) < (hi2 - lo2)


def test_populations_are_declared_and_landslides_are_flagged_irrelevant():
    """Rainfall-triggered slope failure must never be pooled with the
    seismic track -- 90% of Nepal's landslide records fall in the monsoon."""
    assert calib.SOURCES["landslide"]["relevant"] is False
    assert calib.SOURCES["avalanche"]["relevant"] is True
    assert calib.SOURCES["usgs"]["relevant"] is True


def test_placeholder_detection_tracks_which_inventory_was_loaded():
    """
    The script must never emit a radius calibrated against the 8 hand-typed
    placeholder sites. It detects the fallback by identity, so this holds
    whether or not the real inventory file is present on disk.
    """
    from hew.registry import REGISTRY
    inv, placeholder = calib.load_inventory(None)
    assert placeholder is (inv is REGISTRY)
    if placeholder:
        assert len(inv) == len(REGISTRY)      # fell back
    else:
        assert len(inv) > len(REGISTRY)       # real inventory loaded


def test_explicit_inventory_is_never_flagged_placeholder(tmp_path):
    f = tmp_path / "inv.json"
    f.write_text('[{"name":"a","lat":28.0,"lon":85.0}]')
    inv, placeholder = calib.load_inventory(str(f))
    assert placeholder is False and len(inv) == 1
