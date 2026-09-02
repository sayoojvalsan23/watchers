import json
from datetime import datetime, timezone
from hew.watcher import build

def ms(iso): return int(datetime.fromisoformat(iso).timestamp() * 1000)

def feed(events):
    return {"features": [
        {"type": "Feature", "id": e[0],
         "properties": {"mag": e[4], "time": ms("2026-08-26T02:52:00+00:00"),
                        "type": "earthquake"},
         "geometry": {"type": "Point", "coordinates": [e[1], e[2], e[3]]}}
        for e in events]}

def test_idempotent_revisions_do_not_refire(tmp_path):
    gj = feed([("e1", 85.515, 28.271, 2.0, 5.2)])
    w = build(str(tmp_path / "t.db"), allow_dispatch=True, fetcher=lambda: gj)
    assert w.cycle()["dispatched"] == 1
    assert w.cycle()["dispatched"] == 0   # same id, second poll

def test_circuit_breaker_caps_dispatch(tmp_path):
    gj = feed([(f"s{i}", 85.515, 28.271, 2.5, 4.2) for i in range(8)])
    w = build(str(tmp_path / "cb.db"), allow_dispatch=True, fetcher=lambda: gj)
    assert w.cycle()["dispatched"] == 3   # cap

def test_dispatch_disabled_by_default(tmp_path):
    gj = feed([("e1", 85.515, 28.271, 2.0, 5.2)])
    w = build(str(tmp_path / "d.db"), allow_dispatch=False, fetcher=lambda: gj)
    assert w.cycle()["dispatched"] == 0

def test_fetch_failure_records_bad_heartbeat(tmp_path):
    def boom(): raise RuntimeError("network down")
    w = build(str(tmp_path / "f.db"), fetcher=boom)
    assert "error" in w.cycle()
    assert w.check_health() is False      # must page, not stay silent

def test_canary_detects_broken_evaluation_path(tmp_path):
    w = build(str(tmp_path / "c.db"), fetcher=lambda: {"features": []})
    assert w.canary() is True
    w.registry = [{"name": "x", "lat": 0.0, "lon": 0.0, "reach_id": "z"}]
    assert w.canary() is False


# --- D2: revisions must be re-evaluated -----------------------------------

def _feed(eid, lon, lat, depth, mag):
    return {"features": [{"type": "Feature", "id": eid,
            "properties": {"mag": mag, "time": 1787799130000, "type": "earthquake"},
            "geometry": {"type": "Point", "coordinates": [lon, lat, depth]}}]}


def test_a_revision_that_escalates_is_re_evaluated_and_dispatches(tmp_path):
    """
    The D2 failure. An event first published at the 10 km catalogue default
    is capped at watch; when USGS revises it to a measured shallow depth it
    must be re-scored. 80% of events arrive at a default depth and the
    26 August event was revised exactly this way.
    """
    db = str(tmp_path / "rev.db")
    feed = _feed("r1", 85.515, 28.271, 10.0, 4.4)
    w = build(db, allow_dispatch=True, fetcher=lambda: feed)
    first = w.cycle()
    assert first["dispatched"] == 0, "default depth should not dispatch"

    w.fetcher = lambda: _feed("r1", 85.515, 28.271, 0.0, 5.2)   # revised
    second = w.cycle()
    assert second["revised"] == 1, "revision was not re-evaluated"
    assert second["dispatched"] == 1, "escalation did not dispatch"


def test_an_unchanged_record_is_not_re_evaluated(tmp_path):
    db = str(tmp_path / "same.db")
    feed = _feed("s1", 85.515, 28.271, 0.0, 5.2)
    w = build(db, allow_dispatch=True, fetcher=lambda: feed)
    assert w.cycle()["dispatched"] == 1
    again = w.cycle()
    assert again["revised"] == 0 and again["new"] == 0
    assert again["dispatched"] == 0


def test_the_same_tier_never_dispatches_twice(tmp_path):
    """Escalation is new information; repetition is spam."""
    db = str(tmp_path / "dup.db")
    w = build(db, allow_dispatch=True, fetcher=lambda: _feed("d1", 85.515, 28.271, 0.0, 5.2))
    assert w.cycle()["dispatched"] == 1
    # same tier, but the record moved slightly -> re-evaluated, not re-sent
    w.fetcher = lambda: _feed("d1", 85.515, 28.271, 0.5, 5.2)
    s = w.cycle()
    assert s["revised"] == 1 and s["dispatched"] == 0


def test_a_suppressed_revision_says_so_in_the_ledger(tmp_path):
    """'Why did it not fire' must be answerable from the record alone."""
    db = str(tmp_path / "led.db")
    w = build(db, allow_dispatch=True, fetcher=lambda: _feed("l1", 85.515, 28.271, 0.0, 5.2))
    w.cycle()
    w.fetcher = lambda: _feed("l1", 85.515, 28.271, 0.5, 5.2)
    w.cycle()
    with w.store.conn() as c:
        rows = c.execute("SELECT tier, suppressed, suppress_reason FROM decisions"
                         " ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[1]["suppressed"] == 1
    assert "already_dispatched" in (rows[1]["suppress_reason"] or "")
