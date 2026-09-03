"""
Status page.

It reads the decision ledger of a life-safety system. The tests that matter
are that it cannot write to it, and that it reports staleness rather than
rendering a reassuring page over a dead watcher.
"""

import json
import pytest
from hew import status as st
from hew.watcher import build


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "s.db")
    feed = {"features": [{"type": "Feature", "id": "x1",
            "properties": {"mag": 5.2, "time": 1787799130000, "type": "landslide"},
            "geometry": {"type": "Point", "coordinates": [85.515, 28.271, 0.0]}}]}
    w = build(p, allow_dispatch=False, fetcher=lambda: feed)
    w.cycle(); w.check_health(); w.canary()
    return p


def test_connection_is_read_only(db):
    import sqlite3
    c = st._conn(db)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM decisions")


def test_snapshot_reports_the_ledger(db):
    s = st.snapshot(db)
    assert s["decisions"] >= 1 and s["candidates"] >= 1
    assert s["config_version"]
    assert s["poll_age"] is not None and s["poll_age"] < 60


def test_missing_database_is_an_error_not_a_healthy_page(tmp_path):
    s = st.snapshot(str(tmp_path / "nope.db"))
    assert "error" in s
    assert "NO DATA" in st.render(s)


def test_stale_watcher_renders_as_a_page_not_as_healthy(db):
    s = st.snapshot(db)
    s["poll_age"] = 4000              # far beyond STALE_SECONDS
    html = st.render(s)
    assert "STALE" in html and "HEALTHY" not in html


def test_gaps_are_detected(db):
    beats = [{"source": "usgs_catalogue", "ok": 1, "at": "2026-08-01T00:00:00+00:00"},
             {"source": "usgs_catalogue", "ok": 1, "at": "2026-08-01T00:01:00+00:00"},
             {"source": "usgs_catalogue", "ok": 1, "at": "2026-08-01T02:00:00+00:00"}]
    g = st.gaps(beats)
    assert len(g) == 1 and g[0]["seconds"] == pytest.approx(7140)


def test_rendered_page_never_leaks_a_none(db):
    html = st.render(st.snapshot(db))
    assert ">None<" not in html and "None km" not in html


# --- poll-cycle indicator -------------------------------------------------
# It must never imply liveness the data does not support. A bar that just
# sweeps forever would look identical whether the watcher is polling or dead.

def _page(age):
    return st.render({"poll_age": age, "canary_age": 60, "candidates": 0,
                      "decisions": 0, "corridor_rows": 0, "config_version": "v1",
                      "tiers": {}, "beats": [], "recent": []})


def test_indicator_sweeps_only_while_polls_are_arriving():
    h = _page(12)
    assert "cycfill stale" not in h and "cycfill overdue" not in h
    assert "next in ~48s" in h


def test_indicator_is_offset_by_the_real_poll_age():
    """Not restarted on page refresh — it shows where the cycle actually is."""
    assert "--offset:-12.0s" in _page(12)
    assert "--offset:-45.0s" in _page(45)


def test_a_dead_watcher_does_not_animate():
    for age in (4000, None):
        h = _page(age)
        assert "cycfill stale" in h
        assert "watcher may be down" in h


def test_an_overdue_poll_is_shown_as_overdue():
    h = _page(95)
    assert "cycfill overdue" in h and "overdue by 35s" in h


def test_reduced_motion_still_conveys_position():
    """With animation off the bar must still show progress, not sit at zero."""
    h = _page(30)
    assert "prefers-reduced-motion" in h
    assert "--pct:50%" in h


# --- the headline banner ---------------------------------------------------
# It answers "what is happening on the ground", with system health demoted to
# the subtitle. Two properties are load-bearing and are asserted separately
# below, because getting either backwards is a safety failure, not a cosmetic
# one:
#
#   1. "nothing detected" may NEVER be shown by a watcher that has stopped
#      polling. That is a claim about the ground made by something not
#      looking at it -- the same reason the canary exists.
#   2. A hazard already in the ledger OUTRANKS a stale poller. Hiding a real
#      WATCH behind an infrastructure complaint loses the thing the operator
#      most needs to see.

def _banner(base, **over):
    s = dict(base)
    s.update(over)
    return st.render(s)


@pytest.fixture
def snap(db):
    return st.snapshot(db)


def test_quiet_and_healthy_says_nothing_detected(snap):
    h = _banner(snap, poll_age=27, canary_age=300, recent_tiers={})
    assert "NOTHING DETECTED" in h
    assert "system healthy" in h


def test_a_watch_takes_the_headline(snap):
    """26 Aug 2026, 08:37 NPT: M4.4 / earthquake / 10 km default depth.
    Scores WATCH. The operator must see that from the banner, not by
    scrolling to a table."""
    h = _banner(snap, poll_age=27, canary_age=300, recent_tiers={"watch": 1})
    assert "WATCH" in h
    assert "NOTHING DETECTED" not in h


def test_a_dispatch_tier_decision_takes_the_headline(snap):
    """The same event at 21:43, once USGS re-typed it: M5.2 / landslide /
    0 km. Escalates to WARNING."""
    h = _banner(snap, poll_age=27, canary_age=300,
                recent_tiers={"warning": 1, "watch": 3})
    assert "WARNING" in h


def test_stale_watcher_never_claims_nothing_detected(snap):
    """PROPERTY 1. A watcher that stopped cannot speak for the ground."""
    h = _banner(snap, poll_age=4000, canary_age=300, recent_tiers={})
    assert "NOTHING DETECTED" not in h
    assert "NOT LOOKING" in h


def test_a_real_watch_outranks_a_stale_poller(snap):
    """PROPERTY 2. The hazard keeps the headline; staleness is still said."""
    h = _banner(snap, poll_age=4000, canary_age=300, recent_tiers={"watch": 2})
    assert "WATCH" in h
    assert "WATCHER STALE" in h


def test_a_warning_outranks_a_stale_poller(snap):
    h = _banner(snap, poll_age=4000, canary_age=300, recent_tiers={"warning": 1})
    assert "WARNING" in h
    assert "WATCHER STALE" in h


def test_canary_staleness_still_surfaces_when_quiet(snap):
    h = _banner(snap, poll_age=27, canary_age=99999, recent_tiers={})
    assert "CANARY STALE" in h
