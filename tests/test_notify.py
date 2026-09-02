"""
Alert text.

An alert string is a life-safety artifact. These tests read the text a
resident would actually receive, which no other test did -- which is how a
registry swap silently turned a WARNING into

    "Upstream failure detected near HMA_GLI_761. A flood surge may travel
     down downstream."

a database identifier and a doubled word, in the most consequential sentence
the system produces.
"""

import re
import pytest

from hew.detect import evaluate
from hew.notify import Dispatcher, TEMPLATES
from hew.registry import load_registry
from hew.routing import (RiverNetwork, load_settlements, trace_branches,
                         exposed_settlements_union)

R = load_registry()
D = Dispatcher()


@pytest.fixture(scope="module")
def corridor():
    net = RiverNetwork.load()
    br = trace_branches(net, 28.271, 85.515, uncertainty_km=15.0)
    return exposed_settlements_union(br, load_settlements(), corridor_km=2.0)


@pytest.fixture(scope="module")
def warning_text(corridor):
    r = evaluate(28.271, 85.515, 0.0, 5.2, R)
    assert r["tier"] == "warning"
    return D.render("warning", r, corridor)


def test_alert_names_no_database_identifiers(warning_text):
    assert "HMA_GLI" not in warning_text
    assert not re.search(r"\b[A-Z]{2,}_[A-Z]{2,}_\d+", warning_text)


def test_alert_has_no_doubled_or_dangling_words(warning_text):
    words = warning_text.lower().replace(".", " ").replace(",", " ").split()
    doubled = [a for a, b in zip(words, words[1:]) if a == b]
    assert not doubled, f"doubled word in alert: {doubled}"
    assert "down downstream" not in warning_text.lower()


def test_alert_still_carries_the_required_instruction(warning_text):
    low = warning_text.lower()
    assert "high ground" in low
    assert "do not wait" in low


def test_alert_contains_no_arrival_time(warning_text):
    """The no-ETA rule, asserted against the text itself."""
    assert not re.search(r"\b\d+\s*(min|minute|hour|hr)", warning_text.lower())
    for w in ("arrive", "eta", "expected at"):
        assert w not in warning_text.lower()


def test_every_slot_is_filled_even_with_no_corridor():
    """Routing can fail. The template must never emit an empty or None slot."""
    r = evaluate(28.271, 85.515, 0.0, 5.2, R)
    for corridor in (None, []):
        t = D.render("warning", r, corridor)
        assert "None" not in t and "{" not in t
        assert "unknown site" not in t


def test_templates_are_fixed_strings_not_built_at_send_time():
    """Non-negotiable: alert text is never generated at dispatch."""
    for tier, langs in TEMPLATES.items():
        for lang, text in langs.items():
            assert isinstance(text, str)
            assert text.count("{") == text.count("}")


def test_alert_names_no_placeholder_features(warning_text):
    """"unnamed channel" is an OSM placeholder, not a place."""
    assert "unnamed" not in warning_text.lower()


def test_alert_names_a_real_channel_and_a_real_place(warning_text):
    low = warning_text.lower()
    assert any(r in low for r in ("khola", "trishuli", "koshi", "river")), warning_text


def test_candidate_branches_are_alternatives_not_a_sequence(warning_text):
    """Under location uncertainty the corridor lists candidate branches.
    "then" would assert a flow path that does not exist."""
    assert " then the " not in warning_text


def test_no_inventory_identifier_of_any_kind_reaches_the_alert(warning_text):
    """
    The guard was written for HMA_GLI_ ids and silently missed RGI ones when
    glacier outlines were merged in, putting RGI2000-v7.0-G-15-05746 into a
    public alert. Match the shape, not a list of prefixes.
    """
    import re as _re
    assert "RGI" not in warning_text and "HMA_GLI" not in warning_text
    assert not _re.search(r"[A-Za-z]{2,}\d*-v?[\d.]+-", warning_text)


def test_alert_names_settlements_but_never_implies_completeness(warning_text):
    """A list that reads as complete tells everyone not on it they are safe."""
    low = warning_text.lower()
    assert "include" in low
    assert ("more places downstream" in low or "other places downstream" in low
            or "all along this river" in low)


def test_settlement_list_is_short_enough_to_read_under_stress(warning_text):
    from hew.notify import NAME_LIMIT
    assert NAME_LIMIT <= 5
    body = warning_text.split("include", 1)[1].split(".")[0]
    assert body.count(",") <= NAME_LIMIT


def test_degraded_alert_still_reads_as_a_sentence():
    from hew.notify import Dispatcher
    t = Dispatcher().render("warning",
                            {"nearest_site": "Some Glacier", "reach_id": None}, None)
    assert "include settlements downstream" not in t
    assert "None" not in t and "{" not in t
