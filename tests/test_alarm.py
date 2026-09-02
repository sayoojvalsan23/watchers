"""
Fault alarm.

The single most important property: it sounds for SYSTEM failure and never
for a hazard decision. An audible alert on advisory/warning would be a
dispatch channel, and Phase 1 does not warn anyone.
"""

import pytest
from hew import alarm
from hew.watcher import build


@pytest.fixture(autouse=True)
def _clean():
    alarm.reset()
    yield
    alarm.reset()


def test_alarm_is_not_wired_to_any_hazard_decision(tmp_path, monkeypatch):
    """The one that matters. A dispatch-tier decision must make no noise."""
    fired = []
    monkeypatch.setattr(alarm, "sound", lambda *a, **k: fired.append(a))
    feed = {"features": [{"type": "Feature", "id": "w1",
            "properties": {"mag": 5.2, "time": 1787799130000, "type": "landslide"},
            "geometry": {"type": "Point", "coordinates": [85.515, 28.271, 0.0]}}]}
    w = build(str(tmp_path / "a.db"), allow_dispatch=True, fetcher=lambda: feed)
    stats = w.cycle()
    assert stats["by_tier"].get("warning") == 1, "expected a warning to test against"
    assert fired == [], "alarm sounded on a hazard decision — that is a dispatch channel"


def test_alarm_sounds_when_the_feed_goes_stale(tmp_path, monkeypatch):
    fired = []
    monkeypatch.setattr(alarm, "sound", lambda *a, **k: fired.append(a[0]))
    def boom():
        raise RuntimeError("network down")
    w = build(str(tmp_path / "b.db"), fetcher=boom)
    w.cycle()
    assert w.check_health() is False
    assert fired and "poll" in fired[0].lower()


def test_alarm_sounds_when_the_canary_fails(tmp_path, monkeypatch):
    fired = []
    monkeypatch.setattr(alarm, "sound", lambda *a, **k: fired.append(a[0]))
    w = build(str(tmp_path / "c.db"), fetcher=lambda: {"features": []})
    assert w.canary() is True and not fired
    w.registry = [{"name": "x", "lat": 0.0, "lon": 0.0, "reach_id": "z"}]
    assert w.canary() is False
    assert fired and "canary" in fired[0].lower()


def test_repeated_faults_are_rate_limited(monkeypatch):
    """A fault lasting hours must not beep every minute; that is how alarms
    get taped over."""
    monkeypatch.setattr(alarm, "available", lambda: ("none", "test"))
    assert alarm.sound("same fault") is False       # method none -> no noise
    alarm._last.clear()
    monkeypatch.setattr(alarm, "available", lambda: ("gpio", "test"))
    monkeypatch.setattr(alarm, "_buzz_gpio", lambda *a, **k: None)
    monkeypatch.setattr(alarm, "gpio_pin", lambda: 17)
    assert alarm.sound("same fault") is True
    assert alarm.sound("same fault") is False       # rate limited
    assert alarm.sound("different fault") is True   # tracked per reason


def test_alarm_never_raises(monkeypatch):
    monkeypatch.setattr(alarm, "available", lambda: ("gpio", "test"))
    monkeypatch.setattr(alarm, "gpio_pin", lambda: 17)
    def explode(*a, **k):
        raise OSError("no such device")
    monkeypatch.setattr(alarm, "_buzz_gpio", explode)
    assert alarm.sound("boom") is False             # swallowed, not raised


def test_no_output_is_reported_honestly_not_silently(monkeypatch):
    monkeypatch.setattr(alarm, "gpio_pin", lambda: None)
    monkeypatch.setattr(alarm, "_usb_audio", lambda: False)
    monkeypatch.setattr(alarm, "_hdmi_connected", lambda: False)
    method, why = alarm.available()
    assert method == "none"
    assert "hdmi" in why.lower() or "no audio" in why.lower()
