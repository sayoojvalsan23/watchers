"""
Curated-event recall — the other half of the Phase 0 gate.

A false-alarm rate is not a gate on its own. A detector that rejects
everything scores 0.00 alerts/yr and passes. These tests assert the paired
condition: the events the project exists to catch must actually fire.

The distinction that matters: these use REAL USGS catalogue records, not
hand-written fixtures. tests/test_detect.py asserts recall on synthetic
depths (2.0 km, 3.0 km) that the real feed never supplied, which is why that
suite was green while the founding event was being rejected.

Populate the cache first (once, then offline):
    python3 phase0_backtest.py --years 2015 2026
"""

import json
import os
import glob
import pytest

from hew.detect import evaluate
from hew.registry import load_registry

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     ".catalogue_cache")
R = load_registry()


def catalogue():
    recs = {}
    for f in glob.glob(os.path.join(CACHE, "*.json")):
        with open(f) as fh:
            for x in json.load(fh).get("features", []):
                recs[x["id"]] = x
    return recs


needs_cache = pytest.mark.skipif(
    not glob.glob(os.path.join(CACHE, "*.json")),
    reason="no catalogue cache; run phase0_backtest.py once to populate")


def tier_of(feature):
    c = feature["geometry"]["coordinates"]
    p = feature["properties"]
    return evaluate(c[1], c[0], c[2], p.get("mag"), R)["tier"]


# --- Langtang / Bhote Koshi, 26 Aug 2026 08:37 NPT -------------------------
# Real record: us7000tbwb  M5.2  depth 0.0 km  type=landslide
# Fires as of 2026-08-31. 0.0 was removed from FIXED_DEPTHS: it marks a
# surface source, not an unconstrained one. In 830 regional events the only
# two records at depth 0.0 are this event and its aftershock. (D1, closed.)

@needs_cache
def test_langtang_2026_real_record_fires():
    rec = catalogue().get("us7000tbwb")
    assert rec is not None, "26 Aug 2026 record missing from cache"
    assert tier_of(rec) in ("advisory", "warning")


@needs_cache
def test_langtang_2026_is_typed_landslide_and_is_nearly_unique():
    """USGS typed it. Two such records in 573 events across 12 years, and both
    are this event. watcher.py parses usgs_type and discards it (D3)."""
    cat = catalogue()
    assert cat["us7000tbwb"]["properties"]["type"] == "landslide"
    slides = [x for x in cat.values() if x["properties"].get("type") == "landslide"]
    assert len(slides) == 2


# --- Rasuwagadhi, 2025 -----------------------------------------------------
# There is no USGS record. Zero events of ANY magnitude within 40 km in all of
# 2025. This is not a proximity-tuning failure -- there is nothing to tune
# against. USGS magnitude completeness in this bbox is M4.0 (see D4).
#
# No radius, threshold or registry change recovers this event. It is only
# reachable from regional-network or waveform data.

@needs_cache
def test_rasuwagadhi_2025_is_absent_from_the_catalogue():
    """Documents a structural limit, not a bug. If this test ever fails, USGS
    has backfilled the region and the Phase 1 feed assumption should be
    re-examined."""
    from hew.detect import haversine_km
    near = [x for x in catalogue().values()
            if haversine_km(28.28, 85.38,
                            x["geometry"]["coordinates"][1],
                            x["geometry"]["coordinates"][0]) < 40
            and x["properties"]["time"] / 1000 >= 1735689600  # 2025-01-01Z
            and x["properties"]["time"] / 1000 < 1767225600]  # 2026-01-01Z
    assert near == [], f"unexpected 2025 records near Rasuwagadhi: {[x['id'] for x in near]}"


# --- D5: what the feed actually carried at 08:37 --------------------------

def test_preliminary_record_would_not_have_fired():
    """
    USGS first reported this event as M4.4 and as an earthquake; the
    landslide characterisation came +13h later, from long-period analysis
    and satellite imagery. An unconstrained teleseismic origin carries the
    10 km default depth.

    This test asserts the uncomfortable half of the result: the record that
    existed during the warning window is rejected. It guards against anyone
    reading the passing recall test above as proof of timeliness.
    """
    tier = evaluate(28.271, 85.515, 10.0, 4.4, R)["tier"]
    assert tier not in ("advisory", "warning"), \
        "the record available during the warning window must not dispatch"
