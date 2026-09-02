"""
Drill scenarios — one definition, shared by the CLI and the dashboard.

Everything here is SYNTHETIC and nothing is persisted. Drill events must
never enter the decision ledger: that log exists so "why did it not fire" is
answerable from the record alone, and they would also land in the Phase 0
false-alarm count, which is the number the project is judged on.

The named scenarios are real records where a real record exists, and say so
where one does not. The hotspots were computed, not chosen: every hazard in
the registry was scored on how many settlements sit within 8 km, and these
are the highest-consequence points in each major glaciated basin.
"""

import math
import random

# (label, lat, lon, depth_km, magnitude, note)
SCENARIOS = {
    "langtang_2026": (
        "26 Aug 2026 — reviewed USGS record (us7000tbwb)",
        28.271, 85.515, 0.0, 5.2,
        "The founding event. Should WARN."),
    "as_published": (
        "26 Aug 2026 — as the feed carried it at 08:37",
        28.271, 85.515, 10.0, 4.4,
        "M4.4 earthquake, default depth. The landslide characterisation "
        "came +13 h. Must NOT dispatch (D5)."),
    "rasuwagadhi_border": (
        "2026 Langtang/Rasuwagadhi border rock-ice avalanche",
        28.28, 85.37, 1.2, 5.2,
        "Scored a WATCH until glaciers were merged into the registry."),
    "rasuwagadhi_2025": (
        "Rasuwagadhi 2025 — SYNTHETIC, no USGS record exists",
        28.28, 85.38, 3.0, 4.6,
        "Nothing in the catalogue at any magnitude."),
    "gorkha_mainshock": (
        "2015 Gorkha mainshock — Himalayan thrust",
        28.15, 84.71, 15.0, 7.8,
        "Mode 2. Triggered the collapse that buried Langtang village."),
    "dolakha_aftershock": (
        "2015 Dolakha/Kodari aftershock", 27.97, 85.96, 15.0, 7.3,
        "Mode 2."),
    "dingri_2025": (
        "2025 Dingri / south Tibet, shallow normal faulting",
        28.64, 87.36, 10.0, 7.1,
        "Mode 2. Points at Khumbu."),
    "jajarkot_2023": (
        "2023 Jajarkot, western Nepal crustal event",
        28.84, 82.18, 12.0, 5.7,
        "Outside the bbox — rejected on geography."),
    "deep_tectonic": (
        "Deep tectonic quake at the founding location",
        28.271, 85.515, 45.0, 5.2, "Should reject. Discrimination check."),
    "far_from_hazard": (
        "Shallow and well sized, but far from mapped ice",
        27.20, 88.90, 3.0, 4.5, "Proximity is most of the score."),

    # --- computed worst cases, one per glaciated basin ---------------------
    "hotspot_langtang": (
        "Worst case — Langtang (Gongbu, Qusumdo)",
        28.278, 85.537, 1.0, 5.0,
        "9.8 km2, 32.5 deg, 10 settlements within 8 km."),
    "hotspot_manaslu": (
        "Worst case — Manaslu (Lho, Shyala, Samdo)",
        28.625, 84.697, 1.0, 5.0,
        "13.4 km2, 24.4 deg, 5 settlements within 8 km."),
    "hotspot_rolwaling": (
        "Worst case — Rolwaling (Na, Beding)",
        27.921, 86.442, 1.0, 5.0,
        "7.8 km2, 32.9 deg. Tsho Rolpa's basin."),
    "hotspot_khumbu": (
        "Worst case — Khumbu (Thangnak, Tengboche, Monjo)",
        27.776, 86.799, 1.0, 5.0,
        "3.3 km2, 31.9 deg, 7 settlements within 8 km."),
    "hotspot_imja": (
        "Worst case — Imja / Chukhung (Dingboche, Pheriche)",
        27.874, 86.882, 1.0, 5.0,
        "5.5 km2, 26.2 deg, 5 settlements within 8 km."),
}


def random_event(registry, places, seed=None, min_settlements=3,
                 exposure_km=8.0):
    """
    A plausible synthetic collapse somewhere inhabited.

    Samples a hazard WEIGHTED BY how many settlements lie within
    exposure_km, so a drill lands somewhere with people below it rather than
    in one of the thousands of empty high basins. The position is jittered
    by a couple of km because a real source never sits exactly on an
    inventory centroid, and depth and magnitude are drawn from the range
    these events actually occupy.

    Returns (label, lat, lon, depth, magnitude, note, seed) so the same
    drill can be reproduced.
    """
    from .cascade import _settlement_index, _settlements_near
    seed = random.randrange(1, 10 ** 9) if seed is None else int(seed)
    rng = random.Random(seed)

    grid, cell = _settlement_index(places)
    pool = []
    for s in registry:
        n = _settlements_near(s["lat"], s["lon"], grid, cell, exposure_km)
        if n >= min_settlements:
            pool.append((n, s))
    if not pool:
        raise RuntimeError("no inhabited hazard found in the registry")

    total = sum(n for n, _ in pool)
    pick = rng.uniform(0, total)
    acc = 0
    site = pool[-1][1]
    for n, s in pool:
        acc += n
        if acc >= pick:
            site = s
            break

    # Offset up to ~12 km. Sampling tightly onto an inventory centroid made
    # every drill a WARNING at score 91-92, which teaches nothing: a drill
    # set that always returns the same answer cannot show an operator what a
    # marginal call or a rejection looks like. Real sources are not on
    # centroids either.
    off = rng.uniform(0.0, 0.11)
    ang = rng.uniform(0, 6.283185)
    lat = site["lat"] + off * math.cos(ang)
    lon = site["lon"] + off * math.sin(ang) / max(0.3, math.cos(math.radians(site["lat"])))
    # Depth spans the range the filter actually discriminates on, including
    # the catalogue default, so rejections show up too.
    depth = round(rng.choice([0.0, 0.0, 0.5, 1.0, 2.0, 3.0, 6.0, 10.0, 12.0]), 1)
    mag = round(rng.uniform(3.6, 6.2), 1)
    kind = site.get("kind", "hazard")
    return ("Random drill — synthetic event near a mapped " + kind,
            round(lat, 4), round(lon, 4), depth, mag,
            f"Sampled from {len(pool):,} inhabited hazards, weighted by how "
            f"many settlements lie within {exposure_km:.0f} km. "
            f"Seed {seed} reproduces it.", seed)
