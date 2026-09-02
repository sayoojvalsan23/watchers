import json
import os

"""
Hazard registry.

FINDING 2 from the Phase 0 simulation: registry completeness is the binding
constraint, not filter tuning. Rasuwagadhi 2025 was missed at a 5 km radius
because its source lay 13.3 km from the nearest registered site.

THIS LIST IS A PLACEHOLDER. Before Phase 1, replace it with:
  - ICIMOD HKH glacial lake inventory (Nepal side)
  - Equivalent coverage for the Tibetan side of the border, which is where
    both the 2025 and 2026 sources originated
Coverage gaps here are silent false negatives. They do not show up in any
metric until an event is missed.
"""

REGISTRY = [
    {"name": "Langtang Lirung / Lhende", "lat": 28.271, "lon": 85.515,
     "reach_id": "bhote_koshi_trishuli", "source": "event_2026"},
    {"name": "Rasuwagadhi upper basin",  "lat": 28.28,  "lon": 85.38,
     "reach_id": "bhote_koshi_trishuli", "source": "event_2025"},
    {"name": "Tsho Rolpa",               "lat": 27.870, "lon": 86.480,
     "reach_id": "tama_koshi", "source": "icimod_placeholder"},
    {"name": "Imja Tsho",                "lat": 27.900, "lon": 86.930,
     "reach_id": "dudh_koshi", "source": "icimod_placeholder"},
    {"name": "Thulagi",                  "lat": 28.490, "lon": 84.480,
     "reach_id": "marsyangdi", "source": "icimod_placeholder"},
    {"name": "Gokyo",                    "lat": 27.950, "lon": 86.690,
     "reach_id": "dudh_koshi", "source": "icimod_placeholder"},
    {"name": "Lumding Tsho",             "lat": 27.750, "lon": 86.600,
     "reach_id": "dudh_koshi", "source": "icimod_placeholder"},
    {"name": "Barun Tsho",               "lat": 27.800, "lon": 87.100,
     "reach_id": "arun", "source": "icimod_placeholder"},
]


# The real inventory, when present, supersedes the placeholder above.
# Glacial LAKES (NSIDC HMA GLI v1) plus GLACIER outlines (RGI 7.0 centroids).
# Both are needed: the 2026 event was a rock-ice avalanche off a hanging
# glacier, not a lake outburst, and a lake-only inventory scored it 18.7 km
# from the nearest hazard when it was on top of one.
# Provenance is in data/manifest.json.
_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Preference order, widest real inventory first. The Nepal box could not see
# Kedarnath (510 km outside) or Chamoli (449 km outside); the Himalaya-wide
# build covers the arc from Nanga Parbat to Namcha Barwa plus the Tibetan side.
# Built by scripts/build_hazard_registry.py -- provenance in data/manifest.json.
_INVENTORIES = (
    os.path.join(_DATA, "hazard_sites_himalaya.json"),
    os.path.join(_DATA, "hazard_sites_nepal.json"),
)
_INVENTORY = _INVENTORIES[1]


def load_registry(path=None):
    """
    Hazard sites. Prefers the real glacial-lake inventory; falls back to the
    hand-typed placeholder only if it is missing.

    Note what the placeholder was doing: its 2026 entry sat at exactly the
    event coordinates, because someone typed the event location into the
    registry. Distance-to-hazard was therefore 0.0 km by construction. The
    real inventory puts the nearest mapped hazard 2.2 km away -- a GLACIER,
    which is what actually failed. A lake-only registry had it at 10.0 km and
    scored the border scenario as a WATCH.
    """
    for candidate in ((path,) if path else _INVENTORIES):
        if candidate and os.path.exists(candidate):
            with open(candidate) as f:
                return json.load(f)
    return REGISTRY
