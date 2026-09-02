"""
Tests for the rainfall watch engine.

These assert PROPERTIES, not tier buckets. Earlier tests in this project
repeatedly asserted a specific tier and had to be rewritten every time a
threshold moved; the durable question is "can this ever dispatch", not "is
this exactly a watch today".

Nothing here touches the network. The engine takes climatology ladders and
accumulations as arguments precisely so it can be tested offline.
"""

import json
import os

import pytest

from hew import rain_watch, terrain


def ladder(peak, n=8766):
    """A synthetic 10-year ladder rising linearly to `peak`."""
    return [peak * i / n for i in range(n)]


def clim(peak=100.0):
    return {h: ladder(peak * h / 24.0) for h in rain_watch.WINDOWS_H}


STEEP = {"lat": 11.47, "lon": 76.13, "band": "steep", "watchable": True}
FLAT = {"lat": 9.50, "lon": 76.34, "band": "flat", "watchable": False}


# -- the cap ---------------------------------------------------------------

def test_never_exceeds_watch_however_extreme_the_rain():
    """
    The whole justification for this engine is that it raises a WATCH. No
    input may produce advisory or warning: the feed measured ~10 alerts/yr
    against a gate of 2, which is a watch number and nothing more.
    """
    c = clim()
    huge = {h: 100000.0 for h in rain_watch.WINDOWS_H}
    a = rain_watch.assess(huge, c, STEEP)
    assert a["tier"] in ("watch", "log")
    assert a["tier"] != "warning" and a["tier"] != "advisory"


def test_cap_survives_a_config_that_tries_to_raise_it():
    c = clim()
    huge = {h: 100000.0 for h in rain_watch.WINDOWS_H}
    a = rain_watch.assess(huge, c, STEEP,
                          {"watch_percentile": 0.0, "max_tier": "warning"})
    assert a["tier"] == "watch"


# -- the terrain gate ------------------------------------------------------

def test_flat_ground_never_watches_however_wet():
    """
    A percentile exceedance over the coast is noise by construction. Letting
    it through is what makes system alerts scale with site count.
    """
    c = clim()
    huge = {h: 100000.0 for h in rain_watch.WINDOWS_H}
    a = rain_watch.assess(huge, c, FLAT)
    assert a["tier"] == "log"
    assert a["suppressed_by"] == "terrain"


def test_terrain_gate_runs_before_the_rainfall_verdict():
    """Suppression must be visible as suppression, not as a quiet low score."""
    a = rain_watch.assess({h: 100000.0 for h in rain_watch.WINDOWS_H},
                          clim(), FLAT)
    assert "suppressed_by" in a and a["suppressed_by"] == "terrain"


def test_steep_ground_can_watch():
    a = rain_watch.assess({h: 100000.0 for h in rain_watch.WINDOWS_H},
                          clim(), STEEP)
    assert a["tier"] == "watch"


# -- the millimetre floors -------------------------------------------------

def test_dry_site_does_not_watch_on_drizzle():
    """
    A dry site's p99.25 can be a couple of millimetres. Without floors the
    engine would watch on drizzle -- the failure an earlier version of the
    rainfall module actually had, on six of its eight windows.
    """
    dry = {h: ladder(0.5) for h in rain_watch.WINDOWS_H}
    drizzle = {h: 1.0 for h in rain_watch.WINDOWS_H}
    a = rain_watch.assess(drizzle, dry, STEEP)
    assert a["tier"] == "log"


def test_every_window_has_a_floor():
    """An unfloored window is a hole straight through the floors."""
    for h in rain_watch.WINDOWS_H:
        assert h in rain_watch.DEFAULTS["min_mm"], f"window {h}h has no floor"


def test_floors_rise_with_duration():
    f = rain_watch.DEFAULTS["min_mm"]
    ws = sorted(f)
    assert all(f[a] <= f[b] for a, b in zip(ws, ws[1:]))


# -- reporting -------------------------------------------------------------

def test_reports_both_millimetres_and_percentile():
    """
    A percentile alone hides a feed reading 3x low; millimetres alone hide
    whether the number is unusual here. Both, always.
    """
    a = rain_watch.assess({h: 60.0 for h in rain_watch.WINDOWS_H}, clim(), STEEP)
    for row in a["windows"]:
        assert "mm" in row and "percentile" in row


def test_carries_its_own_caveat():
    a = rain_watch.assess({h: 60.0 for h in rain_watch.WINDOWS_H}, clim(), STEEP)
    assert "caveat" in a and "WATCH ONLY" in a["caveat"]


def test_rolling_accumulates_over_the_window_not_the_whole_series():
    s = [1.0] * 100
    assert rain_watch.rolling(s, 3)[-1] == pytest.approx(3.0)
    assert rain_watch.rolling(s, 24)[-1] == pytest.approx(24.0)


def test_accumulations_include_the_current_hour():
    s = [0.0] * 10 + [5.0]
    a = rain_watch.accumulations_at(s, 10)
    assert a[3] == pytest.approx(5.0)


# -- terrain module --------------------------------------------------------

def test_watchable_bands_are_the_steep_ones():
    assert terrain.WATCHABLE == {"steep", "moderate"}


def test_band_thresholds_separate_ghats_from_lowland():
    """
    Measured relief: landslide sites 243-1090 m, lowland towns 13-35 m. The
    'moderate' cut must sit in the gap, clear of both.
    """
    cut = dict((b, t) for t, b in terrain.BANDS)["moderate"]
    assert 35 < cut < 243


def test_bands_are_ordered_descending():
    ts = [t for t, _ in terrain.BANDS]
    assert ts == sorted(ts, reverse=True)


# -- backtest harness ------------------------------------------------------

def test_backtest_events_are_well_formed():
    from hew import rain_backtest
    assert len(rain_backtest.EVENTS) >= 5
    for name, when, lat, lon, dead, note in rain_backtest.EVENTS:
        assert 8.0 < lat < 13.0 and 74.0 < lon < 78.0, name
        assert len(when) == 16 and note


def test_episodes_collapse_one_storm_into_one_alert():
    """
    Counting firing HOURS instead of episodes makes the alert rate a
    restatement of the percentile. This is the guard against that.
    """
    from hew import rain_backtest
    fired = [False] * 1000
    for i in range(100, 130):          # one 30 h storm
        fired[i] = True
    assert rain_backtest.episodes(fired) == 1


def test_episodes_separate_distinct_storms():
    from hew import rain_backtest
    fired = [False] * 1000
    for i in list(range(100, 110)) + list(range(600, 610)):
        fired[i] = True
    assert rain_backtest.episodes(fired) == 2


def test_chooralmala_is_in_the_backtest():
    from hew import rain_backtest
    assert any("Chooralmala" in e[0] for e in rain_backtest.EVENTS)


# -- the cell file, when it exists -----------------------------------------

CELLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "kerala_cells.json")


@pytest.mark.skipif(not os.path.exists(CELLS), reason="terrain scan not run")
def test_every_watch_cell_is_steep_ground():
    with open(CELLS) as f:
        d = json.load(f)
    assert d["cells"], "scan produced no watchable cells"
    for c in d["cells"]:
        assert c["band"] in terrain.WATCHABLE
        assert c["relief_m"] >= 200.0


@pytest.mark.skipif(not os.path.exists(CELLS), reason="terrain scan not run")
def test_lowland_towns_are_not_watch_cells():
    """
    Alappuzha, Kochi and Kollam sit on the coastal plain and cannot host a
    landslide initiation.

    NOTE this deliberately does NOT assert "no low-elevation cell is
    watchable". A cell at 29 m with 224 m of relief in its neighbourhood is a
    valley mouth BELOW steep ground -- which is exactly where Chooralmala's
    victims were. Flagging runout locations is correct behaviour; an earlier
    version of this test asserted the opposite and was wrong.
    """
    import math
    with open(CELLS) as f:
        cells = json.load(f)["cells"]
    towns = [("Alappuzha", 9.498, 76.339), ("Kochi", 9.931, 76.267),
             ("Kollam", 8.893, 76.614)]
    for name, la, lo in towns:
        for c in cells:
            dy = (c["lat"] - la) * 111.0
            dx = (c["lon"] - lo) * 111.0 * math.cos(math.radians(la))
            assert math.hypot(dx, dy) > 8.0, f"{name} has a watch cell on it"


@pytest.mark.skipif(not os.path.exists(CELLS), reason="terrain scan not run")
def test_partial_scan_declares_what_it_cannot_speak_for():
    """
    A partial scan that looks complete is worse than no scan: absence of a
    cell reads as 'safe'. The coverage record must say otherwise, loudly.
    """
    cells, cov = rain_watch.load_cells(CELLS)
    if not cov.get("complete"):
        w = rain_watch.coverage_warning(cov)
        assert w and "not safe" in w.lower()
        assert cov.get("unscanned_latitude_bands") is not None


@pytest.mark.skipif(not os.path.exists(CELLS), reason="terrain scan not run")
def test_load_cells_returns_coverage_not_just_cells():
    cells, cov = rain_watch.load_cells(CELLS)
    assert isinstance(cells, list) and isinstance(cov, dict)
    assert "complete" in cov


# -- the primed/triggered co-signal ----------------------------------------

def test_burst_on_unprimed_ground_still_watches():
    """
    THE most important test in this file.

    Requiring antecedent saturation AND a burst passes the alert-rate gate
    (2.0/yr vs 9.9), which is tempting. It also misses Chooralmala, whose
    antecedent was only p94.82 -- the slope was not primed, the rain simply
    fell in one night. The co-signal must never suppress a watch.
    """
    c = {h: ladder(100.0 * h / 24.0) for h in rain_watch.WINDOWS_H}
    # a hard burst, ordinary antecedent -- the Chooralmala shape
    accum = {3: 90.0, 6: 95.0, 12: 99.0, 24: 100.0, 48: 60.0, 72: 60.0}
    a = rain_watch.assess(accum, c, STEEP)
    assert a["tier"] == "watch", "a burst on unprimed ground must still watch"
    assert a["primed_and_triggered"] is False
    assert a["pattern"] == "burst on unprimed ground"


def test_co_signal_is_reported_not_required():
    c = {h: ladder(100.0 * h / 24.0) for h in rain_watch.WINDOWS_H}
    a = rain_watch.assess({h: 100000.0 for h in rain_watch.WINDOWS_H}, c, STEEP)
    assert "primed_and_triggered" in a and "pattern" in a
    assert a["primed_and_triggered"] is True


# -- the latch -------------------------------------------------------------

def _run(percentiles):
    """Assessments whose top window sits at each given percentile, in order."""
    out = []
    for p in percentiles:
        out.append({"tier": "watch" if p >= rain_watch.DEFAULTS["watch_percentile"]
                    else "log",
                    "windows": [{"window_h": 3, "mm": 50.0, "percentile": p}]})
    return out


def test_watch_holds_after_the_rain_eases():
    """
    THE Chooralmala defect. The watch fired at 18:00 and withdrew at 21:00,
    four hours before the hillside failed at 01:00, because rainfall peaked at
    18:00 and was easing when the slope went. Slopes fail AFTER the rain.
    """
    held = rain_watch.latch(_run([99.5, 99.5, 97.6, 96.0, 96.5, 96.6]))
    assert [a["tier"] for a in held] == ["watch"] * 6


def test_latch_expires_after_hold_hours():
    n = rain_watch.DEFAULTS["hold_hours"]
    held = rain_watch.latch(_run([99.5] + [96.0] * (n + 4)))
    assert held[-1]["tier"] == "log", "a watch must not latch forever"


def test_latch_releases_early_when_everything_dries_out():
    """A slope out of the top 5% wet is a slope to stand down on."""
    held = rain_watch.latch(_run([99.5, 20.0, 20.0]))
    assert [a["tier"] for a in held] == ["watch", "log", "log"]


def test_latch_records_why_it_is_still_watching():
    """'Why did it not stand down' must be answerable from the record."""
    held = rain_watch.latch(_run([99.5, 97.0]))
    assert held[1]["tier"] == "watch"
    assert "hold_reason" in held[1] and held[1]["held_from_h"] == 1


def test_latch_never_invents_a_watch():
    held = rain_watch.latch(_run([50.0, 60.0, 70.0]))
    assert all(a["tier"] == "log" for a in held)


# -- the service -----------------------------------------------------------

def test_grouping_collapses_cells_onto_the_model_grid():
    """
    1,552 cells polled 8x a day is 12,416 requests against a 10,000/day
    allowance. Grouping onto the model grid is what makes the service able to
    run at all, so it must actually reduce the count.
    """
    from hew import rain_watcher
    cells = [{"lat": 11.40 + i * 0.004, "lon": 76.10, "band": "steep",
              "relief_m": 500.0} for i in range(40)]
    g = rain_watcher.build_groups(cells)
    assert len(g) < len(cells)
    assert sum(len(x["cells"]) for x in g) == len(cells), "a cell was dropped"


def test_group_speaks_for_its_steepest_cell():
    """A node serving both steep and moderate ground must report steep."""
    from hew import rain_watcher
    cells = [{"lat": 11.40, "lon": 76.10, "band": "moderate", "relief_m": 250.0},
             {"lat": 11.41, "lon": 76.11, "band": "steep", "relief_m": 900.0}]
    g = rain_watcher.build_groups(cells)
    assert len(g) == 1 and g[0]["band"] == "steep"
    assert g[0]["relief_m"] == 900.0


def test_prefilter_sits_below_every_floor():
    """
    The climatology pre-filter defers an expensive fetch for dry nodes. If it
    sat above any window's floor it could hide a real watch.
    """
    from hew import rain_watcher
    assert rain_watcher.CLIM_PREFILTER_MM_72H <= min(
        rain_watch.DEFAULTS["min_mm"].values()) * 5


def test_quota_arithmetic_holds_for_the_real_cell_file():
    """Guards the constraint that actually kills the service if violated."""
    from hew import rain_watcher
    if not os.path.exists(CELLS):
        pytest.skip("terrain scan not run")
    cells, _ = rain_watch.load_cells(CELLS)
    nodes = rain_watcher.build_groups(cells)
    per_day = len(nodes) * (24 * 3600 // rain_watcher.DEFAULT_INTERVAL)
    assert per_day < 10000, (f"{len(nodes)} nodes x {24*3600//rain_watcher.DEFAULT_INTERVAL}"
                             f" cycles = {per_day}/day exceeds the free quota")


# -- Kerala routing --------------------------------------------------------

RIVERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "rivers_kerala.json")


@pytest.mark.skipif(not os.path.exists(RIVERS), reason="Kerala snapshot absent")
def test_kerala_network_includes_streams():
    """
    A river-only query routes Chooralmala to NOTHING: 27 of 30 mapped
    channels there are waterway=stream and the nearest river is 3.5 km away.
    Debris flows start in headwater streams.
    """
    with open(RIVERS) as f:
        els = json.load(f)["elements"]
    kinds = {(e.get("tags") or {}).get("waterway") for e in els}
    assert "stream" in kinds, "Kerala network has no streams; routing will fail"


@pytest.mark.skipif(not os.path.exists(RIVERS), reason="Kerala snapshot absent")
def test_chooralmala_routes_to_the_villages_that_died():
    """The founding case, end to end. Names, not coordinates."""
    from hew import routing
    net = routing.RiverNetwork.load(RIVERS)
    places = routing.load_settlements(
        os.path.join(os.path.dirname(RIVERS), "places_kerala.json"))
    br = routing.trace_branches(net, 11.4865, 76.1557, uncertainty_km=2.0)
    ex = routing.exposed_settlements_union(br, places, corridor_km=2.5)
    names = {s["name"].lower() for s in ex}
    for who in ("mundakai", "chooralmala", "attamala"):
        assert who in names, f"{who} missing from the corridor"


# -- the status banner must not assert what it has not read ----------------

def test_banner_says_not_running_without_a_ledger(tmp_path, monkeypatch):
    """
    The banner was once a hardcoded 'NOT OPERATIONAL' string. It was true when
    written and false the moment the service was deployed -- a status page
    that lies about the thing it exists to report. It must read the ledger.
    """
    from hew import rain_page
    monkeypatch.setattr(rain_page, "RAIN_DB_CANDIDATES",
                        (str(tmp_path / "absent.db"),))
    assert rain_page._live_state() is None
    assert "NOT RUNNING" in rain_page._state_banner()


def test_banner_says_watching_with_a_fresh_heartbeat(tmp_path, monkeypatch):
    from hew import rain_page
    from hew.store import Store
    db = tmp_path / "hew-rain.db"
    Store(str(db)).heartbeat("rain", True, "316 nodes, 0 watching")
    monkeypatch.setattr(rain_page, "RAIN_DB_CANDIDATES", (str(db),))
    st = rain_page._live_state()
    assert st and st["running"]
    b = rain_page._state_banner()
    assert "WATCHING" in b and "Dispatch is OFF" in b


def test_banner_always_states_the_ceiling():
    """Running or not, the page must say it is a watch and not a warning."""
    from hew import rain_page
    assert "WATCH, NOT WARNING" in rain_page._state_banner()


def test_cycle_bar_goes_stale_instead_of_sweeping_forever():
    """A sweeping animation on a dead service is a lie told every frame."""
    from hew import rain_page
    assert "stale" in rain_page._cycle_bar(10800 * 2)
    assert "stale" not in rain_page._cycle_bar(600)
    assert rain_page._cycle_bar(None) == ""


def test_cycle_bar_offset_reflects_real_age():
    from hew import rain_page
    assert "--offset:-3600s" in rain_page._cycle_bar(3600)


# -- notifications ---------------------------------------------------------

def test_header_values_survive_typography():
    """
    HTTP headers are latin-1 and ntfy carries the title in one. An em-dash in
    a title killed a real push at send time -- on the one message that
    mattered, not in review.
    """
    from hew import hew_operator
    for bad in ("a — b", "it’s", "“quoted”", "30°C",
                "x → y", "≥ 200mm"):
        out = hew_operator._header_safe(bad)
        out.encode("latin-1")          # must not raise
        assert "?" not in out, f"{bad!r} folded to {out!r}"


def test_rain_watch_message_is_not_an_evacuation_order():
    """
    This feed raises ~12 alerts/year. The push must say so in the body, not
    rely on the reader remembering what tier it is.
    """
    from hew import hew_operator
    sent = {}
    orig = hew_operator.send
    hew_operator.send = lambda t, b, **k: sent.update(title=t, body=b) or (True, None)
    try:
        hew_operator.rain_watch(99.4, 6, "steep", ["Mundakai", "Chooralmala"], 3)
    finally:
        hew_operator.send = orig
    assert "WATCH" in sent["body"]
    assert "NOT an evacuation order" in sent["body"]
    assert "Mundakai" in sent["body"]
    sent["title"].encode("latin-1")


def test_a_test_push_cannot_look_like_a_real_one():
    """
    A verification push naming Mundakai was mistaken for a live event. If a
    test is indistinguishable from a real alert, the channel is poisoned: the
    next genuine one gets dismissed as 'probably another test'. The marker
    must be in the TITLE, which is what a phone shows.
    """
    from hew import hew_operator
    got = {}
    orig = hew_operator.send
    hew_operator.send = lambda t, b, **k: got.update(title=t, body=b, kw=k) or (True, None)
    try:
        hew_operator.rain_watch(99.4, 6, "steep", ["Mundakai"], test=True)
        assert "TEST" in got["title"]
        assert "not a real event" in got["title"].lower()
        assert got["kw"].get("kind") == "drill"
        hew_operator.rain_watch(99.4, 6, "steep", ["Mundakai"])
        assert "TEST" not in got["title"]
        assert got["kw"].get("kind") == "detection"
    finally:
        hew_operator.send = orig


def test_rain_now_renders_with_real_conditions(tmp_path, monkeypatch):
    """
    THE gap that let a crash reach the Pi. This block only runs when a
    conditions file exists; the dev machine had none, so 180 passing tests
    said nothing about the code path the device actually executes. A format
    string with one argument too many took the whole page down.
    """
    from hew import rain_page
    cond = {"at": "2026-09-01T16:00:00+00:00", "nodes_polled": 316,
            "watching": 0, "any_rain_24h": 276,
            "wettest": [{"lat": 11.34, "lon": 76.32, "band": "steep",
                         "relief_m": 900.0, "at": "2026-09-01T21:00",
                         "mm_3h": 0.0, "mm_24h": 23.1, "mm_72h": 31.3,
                         "places": ["Edakkara", "Kakkapparutha SC Colony"]},
                        {"lat": 12.53, "lon": 75.68, "band": "steep",
                         "relief_m": 700.0, "at": "2026-09-01T21:00",
                         "mm_3h": 1.0, "mm_24h": 19.3, "mm_72h": 59.3,
                         "places": []}]}
    p = tmp_path / "latest_conditions.json"
    p.write_text(json.dumps(cond))
    monkeypatch.setattr(rain_page, "DATA_DIR", str(tmp_path))
    html = rain_page._rain_now()
    assert "Edakkara" in html
    assert "23.1" in html
    assert "316" in html
    # the entity must survive escaping, not print as "&amp;mdash;"
    assert "&amp;mdash;" not in html


def test_full_page_renders_with_conditions(tmp_path, monkeypatch):
    """Render the whole page the way the device does, not the way dev does."""
    from hew import rain_page, status
    cond = {"at": "2026-09-01T16:00:00+00:00", "nodes_polled": 316,
            "watching": 0, "any_rain_24h": 276,
            "wettest": [{"lat": 11.3, "lon": 76.3, "band": "steep",
                         "relief_m": 900.0, "at": "2026-09-01T21:00",
                         "mm_3h": 0.0, "mm_24h": 23.1, "mm_72h": 31.3,
                         "places": ["Edakkara"]}]}
    (tmp_path / "latest_conditions.json").write_text(json.dumps(cond))
    monkeypatch.setattr(rain_page, "DATA_DIR", str(tmp_path))
    html = rain_page.render(status.CSS)
    assert len(html) > 1000 and "Edakkara" in html
