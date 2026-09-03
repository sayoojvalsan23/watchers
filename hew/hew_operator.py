"""
Operator notification — push to the person running the system.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT
---------------------------------------------
This notifies the OPERATOR. One person, who runs the watcher, and who has
asked to be told when it needs attention. That is the page the design
already requires: "loss of confidence -> no alert AND a page". On a headless
Pi that page was a log line and an LED.

This is NOT public dissemination. It must never become the channel by which
a community downstream is warned. That path requires:

  * a measured false-positive rate (Phase 0 -- done, 0.34/yr)
  * an institutional owner (DHM / NDRRMA -- not secured)
  * a human gate before any public message (Phase 4)
  * pre-translated templates reviewed by that institution

None of those exist. Sending a hazard notification to the operator's own
phone is a person choosing to be told about their own system. Sending one to
a village is an unauthorised warning from a system whose gate has not been
passed, and the difference is not a configuration flag.

WHY ntfy
--------
It needs no account and no credentials, which matters: the alternatives
(SMTP, Twilio) require a password or API key, and this project does not
handle those. A topic is just a name you choose and subscribe to in the
ntfy Android/iOS app.

    export HEW_NTFY_TOPIC=hew-<something-long-and-random>
    python3 -m hew.operator --test

CHOOSE AN UNGUESSABLE TOPIC. ntfy topics are public to anyone who knows the
name: no auth, no secrecy. Someone who guesses yours can read your alerts
and publish fake ones. Use a long random string, and treat anything sent
through it as public.
"""

import argparse
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("hew.operator")

NTFY_HOST = os.environ.get("HEW_NTFY_HOST", "https://ntfy.sh")
TIMEOUT = 15

# ntfy priority: 1 min .. 5 max. Faults get 5 so they break through a
# silenced phone; routine notices get 3.
# ntfy priority: 1 min .. 5 max. 5 breaks through Do Not Disturb and is
# reserved for "the watcher is dead". Drills MUST NOT use it: a random-drill
# session fires dozens of these, and if a drill screams like a real fault you
# learn to ignore the one signal that means the system has actually stopped.
PRIORITY = {"fault": "5", "detection": "4", "drill": "2", "info": "3"}


def topic():
    return os.environ.get("HEW_NTFY_TOPIC") or None


def configured():
    return bool(topic())


# HTTP headers are latin-1. ntfy carries the title in one, so a single em-dash
# or curly quote in a title kills the whole push -- and it fails at send time,
# on the one message that mattered, not in review. Fold the common typography
# down to ASCII and drop anything still unencodable.
_HEADER_FOLD = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00b7": "-",
    "\u2192": "->", "\u00b0": " deg", "\u2265": ">=", "\u2264": "<=",
}


def _header_safe(text):
    """A header value that cannot throw at send time."""
    for bad, good in _HEADER_FOLD.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def send(title, body, kind="info", tags=None, click=None):
    """
    Push to the operator. Returns (ok, detail).

    Never raises. A notification channel that can crash the watcher is worse
    than no notification channel -- the same rule the alarm follows.
    """
    t = topic()
    if not t:
        log.debug("operator push not configured (HEW_NTFY_TOPIC unset)")
        return False, "not_configured"
    headers = {
        "Title": _header_safe(title)[:200],
        "Priority": PRIORITY.get(kind, "3"),
        "Tags": _header_safe(",".join(tags or (["rotating_light"]
                                              if kind == "fault"
                                              else ["warning"]))),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        headers["Click"] = click
    try:
        req = urllib.request.Request(f"{NTFY_HOST}/{t}",
                                     data=body.encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read()
        log.info("operator push sent (%s)", kind)
        return True, None
    except Exception as e:                      # never break the caller
        log.error("operator push failed: %s", e)
        return False, str(e)


def drill(tier, factors, towns, n_more=0):
    """
    A simulation result. Low priority and unmistakably labelled: this is the
    one notification the operator will see dozens of in a row while playing
    with the dashboard.
    """
    body = [f"tier {tier.upper()}", f"factors: {factors}"]
    if towns:
        body.append("exposed: " + towns + (f" and {n_more} more" if n_more else ""))
    body += ["", "SIMULATION — nothing was detected and nothing was recorded.",
             "No real event. Nobody has been warned."]
    return send(title=f"[SIMULATION] {tier.upper()}", body="\n".join(body),
                kind="drill", tags=["test_tube"])


def fault(reason, detail=""):
    """The watcher needs attention. Highest priority."""
    return send(
        title="HEW watcher fault",
        body=f"{reason}\n\n{detail}\n\nThis is a SYSTEM fault, not a hazard. "
             f"No flood is indicated. Check the watcher.".strip(),
        kind="fault", tags=["rotating_light", "wrench"])


def detection(tier, score, site, km, corridor_n=None, first=None):
    """
    A hazard decision, sent to the OPERATOR for review.

    Deliberately worded so it can never be forwarded as a public warning:
    it names no action, gives no arrival time, and says outright that
    nobody downstream has been told.
    """
    lines = [
        f"{tier.upper()} — score {score}",
        f"nearest mapped hazard: {site} ({km} km)",
    ]
    if corridor_n:
        lines.append(f"corridor: {corridor_n} settlements"
                     + (f", first {first}" if first else ""))
    lines += [
        "",
        "OPERATOR NOTICE — for review only.",
        "Dispatch is OFF. Nobody downstream has been notified.",
        "This is not a warning and must not be forwarded as one.",
    ]
    return send(title=f"HEW {tier} detected", body="\n".join(lines),
                kind="detection", tags=["ocean"])


def unresolved_watch(score, site, km, kind, depth, mag, factors):
    """
    A WATCH that was capped, not scored low -- sent to the operator.

    The gap this closes: on 26 August 2026 the feed carried the collapse as
    M4.4 / type=earthquake / 10 km, and 10 km is a catalogue default meaning
    depth UNCONSTRAINED. That record scores WATCH, is written to the ledger,
    and until now notified nobody -- the operator first heard about it when
    the characterised record arrived 13 h 06 m later. Waiting on USGS to
    re-type an event is not a plan.

    So: when depth is unknown AND the event sits on a mapped hazard, tell the
    operator. This is a heads-up, not a detection. Priority stays at info,
    BELOW detection, because roughly 46 of these are expected per year
    (531 such events in 11.67 years of real catalogue) and an operator notice
    that fires every eight days must never sound like the one that means the
    watcher has died.

    Sent at DETECTION priority, not info: the whole point is that the 08:37
    record must reach a human while it still matters, and a notice you do not
    look at for six hours is the same as no notice. Still not priority 5 --
    that one is reserved for "the watcher is dead", and a signal that fires
    every eight days would drown the one that fires once a year.

    It cannot become a dispatch. Nothing here touches the dispatch path.
    """
    lines = [
        f"WATCH — score {score} (capped, not scored low)",
        f"M{mag}, depth {depth} km — DEPTH UNCONSTRAINED (catalogue default)",
        f"nearest mapped hazard: {site} ({km} km, {kind})",
        f"factors: {', '.join(factors)}",
        "",
        "Why you are seeing this: depth is the discriminator between a",
        "surface collapse and an ordinary earthquake, and this record has",
        "none. It sits on a mapped hazard, so it is worth a human glance.",
        "",
        "OPERATOR NOTICE — for review only. Not a detection, not a warning.",
        "Dispatch is OFF. Nobody downstream has been notified.",
    ]
    return send(title="HEW watch — unknown depth on a mapped hazard",
                body="\n".join(lines), kind="detection", tags=["eyes"])


def rain_watch(pct, window_h, band, places, n_more=0, at=None, test=False):
    """
    A rainfall WATCH, pushed to the operator's own phone.

    This is deliberately NOT gated behind --allow-dispatch. That gate exists
    for PUBLIC alerting -- telling villages to act -- which is an institutional
    decision and not the software's to make. Telling the person who runs the
    system that their own detector just fired is the same class of thing as
    the fault alarm: it is operator awareness, not a public warning.

    The wording matters. This feed measured ~12 alerts/year against a gate of
    2, so the message must never read as an evacuation order, and it says so
    in the body rather than relying on the reader to remember.
    """
    # A test that is indistinguishable from a real alert poisons the channel:
    # the next genuine one gets dismissed as "probably another test". This was
    # not hypothetical -- a verification push naming Mundakai was mistaken for
    # a live event. Tests must be unmistakable, in the title, where a phone
    # notification actually shows it.
    who = ", ".join(places[:3]) if places else "no mapped settlement below"
    if n_more:
        who += f" +{n_more} more"
    body = (f"Rain in the top {100 - pct:.2f}% for this spot "
            f"({window_h} h window, {band} ground).\n"
            f"Downstream: {who}\n\n"
            f"This is a WATCH: pay attention, check conditions. It is NOT an "
            f"evacuation order -- this feed raises ~12 of these a year and "
            f"most come to nothing.")
    if test:
        return send("[TEST - not a real event] Heavy rain watch",
                    "*** THIS IS A TEST PUSH. No rain watch has fired. ***\n\n"
                    + body, kind="drill", tags="test_tube")
    return send("Heavy rain watch - Kerala Ghats", body, kind="detection",
                tags="cloud_with_rain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--fault", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    if not configured():
        raise SystemExit(
            "HEW_NTFY_TOPIC is not set.\n\n"
            "  1. install ntfy from the Play Store\n"
            "  2. subscribe to a LONG RANDOM topic name of your choosing\n"
            "  3. export HEW_NTFY_TOPIC=that-name\n\n"
            "Topics are public to anyone who knows the name -- no auth.")
    print(f"topic: {topic()}  host: {NTFY_HOST}")
    if a.fault:
        ok, err = fault("test fault", "triggered by hew.operator --fault")
    else:
        ok, err = send("HEW test", "Operator channel is working.\n"
                       "This is a test, not a hazard.", kind="info",
                       tags=["white_check_mark"])
    print("sent:", ok, err or "")


if __name__ == "__main__":
    main()
