"""
Operator notification.

The property that matters: this notifies ONE PERSON who runs the system. It
must never read like, or become, a public warning.
"""

import pytest
from hew import hew_operator as operator


@pytest.fixture(autouse=True)
def _no_topic(monkeypatch):
    monkeypatch.delenv("HEW_NTFY_TOPIC", raising=False)
    yield


def test_unconfigured_is_a_no_op_not_an_error():
    assert operator.configured() is False
    ok, why = operator.send("t", "b")
    assert ok is False and why == "not_configured"


def test_send_never_raises(monkeypatch):
    monkeypatch.setenv("HEW_NTFY_TOPIC", "x")
    def explode(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(operator.urllib.request, "urlopen", explode)
    ok, err = operator.send("t", "b")
    assert ok is False and "network down" in err


def test_detection_text_cannot_be_read_as_a_public_warning(monkeypatch):
    sent = {}
    monkeypatch.setenv("HEW_NTFY_TOPIC", "x")
    monkeypatch.setattr(operator, "send",
                        lambda title, body, **k: sent.update(t=title, b=body) or (True, None))
    operator.detection("warning", 92, "Some Glacier", 2.2, 60, "Gumba")
    b = sent["b"].lower()
    assert "operator notice" in b
    assert "nobody downstream has been notified" in b
    assert "must not be forwarded" in b
    # no instruction, no arrival time -- those belong only to reviewed templates
    for forbidden in ("move to high ground", "evacuate", "arrive", "minutes"):
        assert forbidden not in b


def test_fault_says_it_is_not_a_hazard(monkeypatch):
    sent = {}
    monkeypatch.setenv("HEW_NTFY_TOPIC", "x")
    monkeypatch.setattr(operator, "send",
                        lambda title, body, **k: sent.update(b=body) or (True, None))
    operator.fault("feed stale", "detail")
    assert "not a hazard" in sent["b"].lower()
    assert "no flood is indicated" in sent["b"].lower()


def test_faults_get_the_highest_priority():
    assert operator.PRIORITY["fault"] == "5"
    assert operator.PRIORITY["fault"] > operator.PRIORITY["info"]


def test_drills_never_use_the_fault_priority():
    """
    A random-drill session fires dozens of these. If a drill screams like a
    dead watcher, the operator learns to ignore the one signal that means the
    system has actually stopped.
    """
    assert operator.PRIORITY["drill"] < operator.PRIORITY["fault"]
    assert operator.PRIORITY["drill"] < operator.PRIORITY["detection"]


def test_drill_text_says_it_is_a_simulation(monkeypatch):
    sent = {}
    monkeypatch.setenv("HEW_NTFY_TOPIC", "x")
    monkeypatch.setattr(operator, "send",
                        lambda title, body, **k: sent.update(t=title, b=body, k=k)
                        or (True, None))
    operator.drill("warning", "very_shallow, proximity_0.92", "Gumba, Bidur", 58)
    assert sent["t"].startswith("[SIMULATION]")
    assert sent["k"]["kind"] == "drill"
    low = sent["b"].lower()
    assert "simulation" in low and "nothing was recorded" in low
    assert "nobody has been warned" in low
