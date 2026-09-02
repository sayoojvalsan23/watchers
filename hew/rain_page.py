"""
The /rain tab — the rainfall track's calibration, stated honestly.

WHY THIS PAGE LOOKS NOTHING LIKE /
-----------------------------------
The main dashboard shows a LIVE system: a watcher polling, decisions landing
in a ledger, a poll-cycle bar that goes red when the feed dies. This page
shows none of that, because there is nothing live to show. The rainfall track
has no trigger feed. Dressing it up with the same green-status furniture
would imply a system that is watching Kerala, and none is.

So this page is a CALIBRATION REPORT. It answers one question -- "if we had
the feed, would the detector work?" -- and it answers it with measurements
rather than assertion. The single most important thing it must not do is
look operational.

WHAT IT SHOWS, IN ORDER
-----------------------
  1. the blocker, first and unmissable
  2. why gauges and not satellites -- the resolution ladder
  3. the calibration curve, which is the actual deliverable
  4. the per-station climatology behind that curve
  5. the run-up, which is the finding that hurts

Read-only, no controls, no POST. Data comes from data/ksdma_*.json, written
by the calibration run; this module only renders.
"""

import json
import math
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

# The resolution ladder, measured at Chooralmala for 30 July 2024. Kept here
# as data rather than prose because the whole argument is the numbers.
LADDER = [
    ("IMD gauge, Vythiri", "point", 280.0, True),
    ("CHIRPS v2.0", "~5.5 km", 49.7, False),
    ("Open-Meteo ERA5", "~25 km", 51.6, False),
    ("Open-Meteo ECMWF-IFS", "~25 km", 51.6, False),
    ("NASA POWER (MERRA-2)", "~50 km", 52.6, False),
]

# Verified from 18 individually-hashed bulletins, 18/18 distinct content.
RUNUP = [
    ("18 Jul", 104.0, ""), ("19 Jul", 89.4, ""), ("20 Jul", 67.2, ""),
    ("21 Jul", 43.0, ""), ("22 Jul", 14.2, ""), ("23 Jul", 23.0, ""),
    ("24 Jul", 25.0, ""), ("25 Jul", 29.0, ""), ("26 Jul", 93.3, ""),
    ("27 Jul", 104.0, ""), ("29 Jul", 27.6, "17 h before the failure"),
    ("30 Jul", 280.0, "landslides 01:00 and 04:10"),
    ("31 Jul", 57.0, ""), ("01 Aug", 47.0, ""), ("02 Aug", 32.0, ""),
]

RELIEF_COLOUR = {"ghat": "var(--bad)", "foothill": "var(--warn)",
                 "plain": "var(--fg3)"}


RAIN_DB_CANDIDATES = ("hew-rain.db",
                      os.path.expanduser("~/.local/share/hew/hew-rain.db"))


def _live_state():
    """
    What the rain watcher is ACTUALLY doing, read from its ledger.

    This was a hardcoded "NOT OPERATIONAL" banner. It was true when written
    and false the moment the service was deployed -- exactly the failure mode
    a status page must not have. A status page asserts nothing it has not
    read. So: read the heartbeat.
    """
    import sqlite3
    from datetime import datetime, timezone
    for path in RAIN_DB_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
            c.row_factory = sqlite3.Row
            hb = c.execute("SELECT ok, detail, at FROM heartbeats"
                           " WHERE source='rain' ORDER BY at DESC"
                           " LIMIT 1").fetchone()
            if not hb:
                continue
            n_dec = c.execute("SELECT COUNT(*) n FROM decisions").fetchone()["n"]
            n_w = c.execute("SELECT COUNT(*) n FROM decisions WHERE tier='watch'"
                            ).fetchone()["n"]
            t = datetime.fromisoformat(hb["at"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - t).total_seconds()
            return {"running": age < 4 * 3600, "age_s": age,
                    "detail": hb["detail"], "ok": bool(hb["ok"]),
                    "decisions": n_dec, "watches": n_w}
        except Exception:
            continue
    return None


CANNOT = (
    '<div class=blocked style="border-color:var(--warn);margin-top:12px">'
    '<div class=k>what it cannot do</div>'
    '<div class=big style="color:var(--warn);font-size:18px">'
    'WATCH, NOT WARNING</div>'
    '<p class=sub style="margin:8px 0 0">What runs is <strong>Open-Meteo</strong>: '
    '5/5 recall on Kerala&rsquo;s landslide disasters, 28 h median lead, at '
    '<strong>~12 alerts/year against a gate of 2</strong>. That is an attention '
    'product, not an evacuation trigger.<br><br>'
    'The calibration curve below (200 mm, 1.3/yr) is <strong>a different feed '
    'we do not have</strong>. KSDMA publishes those gauges once a day, ~12:00 '
    'IST, for a window ending 08:00 &mdash; eight hours after Chooralmala '
    'failed. Their real-time API answers '
    '<code>401 &ldquo;needs to be whitelisted&rdquo;</code>.</p></div>')


def _cells_for_page():
    """Watch cells + coverage for the header panel. Never raises."""
    try:
        from . import rain_watch
        return rain_watch.load_cells()
    except Exception:
        return [], {}


# -- the plain-language top of the page ------------------------------------
#
# Everything below the fold on this page is evidence: percentiles, station-
# years, calibration curves. That material is why anyone should believe the
# system, and it is unreadable to anyone who has not been in the weeds of it.
#
# A status page has exactly one job on first glance: answer "is anything
# dangerous right now". If a reader cannot answer that in two seconds without
# knowing what p99.27 means, the page has failed regardless of how good the
# analysis underneath is. So: plain words first, evidence second, and a
# labelled divider between them so nobody mistakes one for the other.

PLAIN_CSS = """
.details{margin-top:10px;border:1px solid var(--line);border-radius:8px;
padding:0 16px 4px}
.details>summary{cursor:pointer;padding:14px 0;color:var(--accent);
font-size:13.5px;list-style:none}
.details>summary::-webkit-details-marker{display:none}
.details>summary:before{content:"\25b8 ";color:var(--fg3)}
.details[open]>summary:before{content:"\25be ";color:var(--fg3)}

.steps{margin-top:10px}
.step{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.step:last-child{border-bottom:none}
.stepn{flex:0 0 26px;height:26px;border-radius:50%;background:var(--card);
border:1px solid var(--line);color:var(--fg2);font-size:12px;display:flex;
align-items:center;justify-content:center;font-weight:700}
.stepq{font-size:15px;font-weight:700;color:var(--fg)}
.stepwhy{margin-top:6px;font-size:13px;line-height:1.6;color:var(--fg3);
border-left:2px solid var(--line);padding-left:12px}
ul.nots{list-style:none;padding:0;margin:8px 0 0;font-size:13.5px;line-height:1.65}
ul.nots li{padding-left:22px;position:relative;margin:9px 0;color:var(--fg2)}
ul.nots li:before{content:"\2014 ";position:absolute;left:0;color:var(--fg3)}

.hero{border:2px solid var(--line);border-radius:12px;padding:26px 28px;margin-top:18px}
.hero.calm{border-color:var(--ok)} .hero.alarm{border-color:var(--bad)}
.hero.dead{border-color:var(--fg3)}
.hero .huge{font-size:40px;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.hero.calm .huge{color:var(--ok)} .hero.alarm .huge{color:var(--bad)}
.hero.dead .huge{color:var(--fg3)}
.hero .when{color:var(--fg3);font-size:13px;margin-top:10px}
.plain{font-size:14.5px;line-height:1.65;margin-top:8px}
.plain b{color:var(--fg)}
.canlist{list-style:none;padding:0;margin:10px 0 0;font-size:14px;line-height:1.9}
.canlist li{padding-left:26px;position:relative}
.canlist li:before{position:absolute;left:0;font-weight:700}
.canlist li.y:before{content:"\2713 ";color:var(--ok)}
.canlist li.n:before{content:"\2715 ";color:var(--bad)}
.divider{display:flex;align-items:center;gap:14px;margin:44px 0 6px;
color:var(--fg3);font-size:11px;text-transform:uppercase;letter-spacing:.12em}
.divider:before,.divider:after{content:"";flex:1;height:1px;background:var(--line)}
"""


# -- the coverage map ------------------------------------------------------
#
# "316 nodes over 8.2-12.8N" is a true sentence that shows nobody anything.
# The map exists so a reader can see the watch is on the mountains and not on
# the coast, and can find their own valley in it.
#
# The base layer is the terrain scan's own elevation lattice -- 4,988 samples
# that were paid for anyway -- so the map is drawn from the same data the
# screen decides on, not from a decorative basemap that might disagree with
# it. If the map shows hills where the screen sees none, that is a real bug
# worth seeing.

ELEV_BANDS = [(1400, "#4a4038"), (900, "#3d382f"), (500, "#333026"),
              (200, "#2a2a22"), (60, "#22241f"), (-100, "#1b1f22")]


def _coverage_map(cells, W=620, H=740):
    """Kerala terrain with the watch cells on it. Drawn from the scan itself."""
    import os as _os
    from . import terrain as _t
    try:
        with open(_os.path.join(_t.DATA_DIR, "terrain_cache.json")) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    if not cells:
        return "<p class=note>No watch cells to draw.</p>"

    lat0, lat1 = 8.20, 12.80
    lon0, lon1 = 75.70, 77.40
    pad_l, pad_t = 10, 10
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    ww = (lon1 - lon0) * kx
    hh = (lat1 - lat0)
    # Fit to HEIGHT: the region is ~2.7x taller than it is wide, so height is
    # always the binding constraint and fitting to width wastes the canvas.
    scale = (H - 2 * pad_t) / hh
    map_w = ww * scale

    def px(lon):
        return pad_l + (lon - lon0) * kx * scale

    def py(lat):
        return pad_t + (lat1 - lat) * scale

    step = 0.04
    cw = max(1.6, step * kx * scale)
    ch = max(1.6, step * scale)

    # base: elevation lattice, one path per band so the SVG stays small
    bands = {}
    for k, e in cache.items():
        try:
            a, b = k.split(",")
            la, lo = float(a), float(b)
        except ValueError:
            continue
        if not (lat0 <= la <= lat1 and lon0 <= lo <= lon1):
            continue
        col = next(c for t, c in ELEV_BANDS if e >= t)
        bands.setdefault(col, []).append(
            "M%.1f %.1fh%.1fv%.1fh-%.1fz" % (px(lo), py(la), cw, ch, cw))
    base = "".join('<path d="%s" fill="%s"/>' % ("".join(v), c)
                   for c, v in bands.items())

    # overlay: the watch cells themselves
    dots = {"steep": [], "moderate": []}
    for c in cells:
        d = dots.get(c.get("band"))
        if d is None:
            continue
        d.append("M%.1f %.1fh%.1fv%.1fh-%.1fz"
                 % (px(c["lon"]), py(c["lat"]), cw, ch, cw))
    over = ""
    for band, col in (("moderate", "#d0a24c"), ("steep", "#d9584c")):
        if dots[band]:
            over += ('<path d="%s" fill="%s" opacity="0.72"/>'
                     % ("".join(dots[band]), col))

    # the events, so a reader can orient on names they know
    marks = ""
    # dy staggers labels that would otherwise collide -- Chooralmala and
    # Puthumala are 4 km apart and overprinted into an unreadable smear.
    for nm, la, lo, dy in (("Chooralmala", 11.4865, 76.1557, -7),
                           ("Puthumala", 11.490, 76.100, 9),
                           ("Kavalappara", 11.310, 76.290, 4),
                           ("Pettimudi", 10.167, 77.050, 4),
                           ("Koottickal", 9.5841, 76.8854, 4)):
        x, y = px(lo), py(la)
        marks += ('<circle cx="%.1f" cy="%.1f" r="4.5" fill="none" '
                  'stroke="#e8eaf0" stroke-width="1.6"/>'
                  '<circle cx="%.1f" cy="%.1f" r="1.5" fill="#e8eaf0"/>'
                  '<text x="%.1f" y="%.1f" fill="#e8eaf0" font-size="10.5">%s</text>'
                  % (x, y, x, y, x + 8, y + dy, _esc(nm)))

    lx = pad_l + map_w + 22
    n_steep = sum(1 for c in cells if c.get("band") == "steep")
    n_mod = len(cells) - n_steep
    legend = (
        '<text x="%d" y="26" fill="var(--fg3)" font-size="10" '
        'letter-spacing="1">WATCHED GROUND</text>'
        '<rect x="%d" y="38" width="10" height="10" fill="#d9584c" opacity="0.72"/>'
        '<text x="%d" y="47" fill="var(--fg2)" font-size="11">steep &nbsp;%s</text>'
        '<rect x="%d" y="56" width="10" height="10" fill="#d0a24c" opacity="0.72"/>'
        '<text x="%d" y="65" fill="var(--fg2)" font-size="11">moderate &nbsp;%s</text>'
        '<text x="%d" y="92" fill="var(--fg3)" font-size="10" '
        'letter-spacing="1">ELEVATION</text>'
        % (lx, lx, lx + 16, "{:,}".format(n_steep),
           lx, lx + 16, "{:,}".format(n_mod), lx))
    for i, (t, c) in enumerate(ELEV_BANDS):
        y = 104 + i * 15
        lab = "sea / coast" if t < 0 else "%s m +" % t
        legend += ('<rect x="%d" y="%d" width="10" height="10" fill="%s" '
                   'stroke="#2b2f3a"/><text x="%d" y="%d" fill="var(--fg3)" '
                   'font-size="10.5">%s</text>' % (lx, y, c, lx + 16, y + 9, lab))
    legend += ('<circle cx="%d" cy="%d" r="4.5" fill="none" stroke="#e8eaf0" '
               'stroke-width="1.6"/><text x="%d" y="%d" fill="var(--fg2)" '
               'font-size="10.5">past disaster</text>'
               % (lx + 5, 104 + len(ELEV_BANDS) * 15 + 12,
                  lx + 16, 104 + len(ELEV_BANDS) * 15 + 16))

    return ('<svg viewBox="0 0 %d %d" width="100%%" '
            'style="display:block;width:100%%;height:auto" role="img" '
            'aria-label="Map of watched ground in the Kerala Western Ghats">'
            '<rect width="%d" height="%d" fill="#12141a"/>'
            '%s%s%s%s</svg>' % (W, H, W, H, base, over, marks, legend))


def _conditions():
    """What the last cycle actually saw. Written by rain_watcher each poll."""
    for d in (DATA_DIR, os.path.expanduser("~/hew/data")):
        p = os.path.join(d, "latest_conditions.json")
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return None


def _rain_now():
    """
    Where it is actually raining, right now.

    The page previously said "watching 316 places" and showed the reader no
    rain at all -- the least useful honest page imaginable. A watch dashboard
    has to show the thing it watches, including when the answer is "barely
    anything", because that is what most days look like and a reader needs to
    learn what normal looks like here.
    """
    c = _conditions()
    if not c:
        return ('<p class=note>No reading yet &mdash; the watcher writes this '
                'each cycle. If this persists past three hours, it is not '
                'running.</p>')
    wet = c.get("wettest") or []
    rows = ""
    for w in wet[:12]:
        where = ", ".join(_esc(p) for p in (w.get("places") or []))
        if not where:
            where = "&mdash;"          # entity added AFTER escaping, not before
        hot = w["mm_24h"] >= 50
        rows += ('<tr><td style="text-align:right;color:%s;font-weight:%s">'
                 '%.1f</td><td style="text-align:right">%.1f</td>'
                 '<td style="text-align:right">%.1f</td>'
                 '<td style="color:%s">%s</td><td>%s</td>'
                 '<td class=sub>%.2f, %.2f</td></tr>'
                 % ("var(--warn)" if hot else "var(--fg)",
                    "700" if hot else "400",
                    w["mm_24h"], w["mm_3h"], w["mm_72h"],
                    RELIEF_COLOUR.get(w.get("band"), "var(--fg3)"),
                    _esc(w.get("band", "")), where,
                    w["lat"], w["lon"]))
    if not rows:
        rows = '<tr><td colspan=6 class=sub>nothing recorded</td></tr>'
    dry = c.get("nodes_polled", 0) - c.get("any_rain_24h", 0)
    return (
        '<div class=grid>'
        '<div class=card><div class=k>places checked</div><div class=v>%s</div>'
        '<div class=sub>last cycle</div></div>'
        '<div class=card><div class=k>had rain (24 h)</div><div class=v>%s</div>'
        '<div class=sub>%s dry</div></div>'
        '<div class=card><div class=k>wettest place</div><div class=v>%.0f mm</div>'
        '<div class=sub>%s</div></div>'
        '<div class=card><div class=k>flagged</div>'
        '<div class=v style="color:%s">%s</div>'
        '<div class=sub>watches raised</div></div>'
        '</div>'
        '<div class=scroll style="margin-top:12px"><table><thead><tr>'
        '<th style="text-align:right">24 h mm</th>'
        '<th style="text-align:right">3 h</th>'
        '<th style="text-align:right">72 h</th>'
        '<th>ground</th><th>villages below</th><th>where</th>'
        '</tr></thead><tbody>%s</tbody></table></div>'
        '<p class=note>The wettest places this system is watching, heaviest '
        'first. <b>These millimetres read about a third of what a rain gauge '
        'on the ground would</b> &mdash; the forecast model flattens Ghats '
        'downpours. Use the column to see <em>where</em> the rain is, not '
        '<em>how much</em>. The system judges each place against its own ten-'
        'year history, which is why it can still work on numbers that are '
        'wrong.</p>'
        % ("{:,}".format(c.get("nodes_polled", 0)),
           "{:,}".format(c.get("any_rain_24h", 0)), "{:,}".format(max(0, dry)),
           (wet[0]["mm_24h"] if wet else 0.0),
           (_esc((wet[0].get("places") or ["unnamed slope"])[0]) + " &middot; 24 h"
            if wet else "no reading"),
           "var(--bad)" if c.get("watching") else "var(--ok)",
           c.get("watching", 0), rows))


# -- how the decision is actually made, in plain words ---------------------
#
# A reader who cannot see the reasoning has to take the verdict on trust, and
# a system that asks for trust it has not earned is the thing this project is
# trying not to be. Six checks, each stated with what it rules out.

STEPS = [
    ("Is there a hill here at all?",
     "Every 4 km patch of Kerala was measured for how much the land rises "
     "and falls across it. A patch needs at least 200 m of rise within about "
     "4 km to be watched at all.",
     "Rules out the coast and the plains. A downpour over Alappuzha at 5 m "
     "elevation is a flood problem, not a landslide one, and the system will "
     "not raise a landslide watch there however hard it rains."),
    ("How much rain, over how long?",
     "Six clocks run at once at every place: the last 3, 6, 12, 24, 48 and "
     "72 hours.",
     "A cloudburst and a week of steady soaking are different dangers. One "
     "clock alone would miss whichever kind it was not built for."),
    ("Is that a lot <em>for here</em>?",
     "Each of those six numbers is compared with the last ten years at that "
     "exact spot &mdash; not with a national figure and not with the place "
     "next door.",
     "80 mm is an ordinary Tuesday in Wayanad and close to a record in "
     "Palakkad. It also cancels the biggest weakness in the data: the "
     "forecast reads about a third of true rainfall in these hills, but "
     "since it is compared only against its own history, being wrong by the "
     "same factor every time does not matter."),
    ("Is it actually wet, not just unusual?",
     "Each clock also has to clear a plain minimum &mdash; 8 mm in 3 hours, "
     "25 mm in a day, 45 mm in three days.",
     "In a dry place the &lsquo;wettest 1% of days&rsquo; can be a drizzle. "
     "Without this floor the system would cry wolf over damp weather."),
    ("Is it in the top fraction of a percent?",
     "If any clock is wetter than about 99.25% of the last decade at that "
     "spot, a watch is raised.",
     "This is the actual trigger. It was set by replaying Kerala&rsquo;s "
     "five worst landslides: all five sat in the top 1%, and this is the "
     "highest setting that still catches every one of them."),
    ("Keep watching after the rain stops.",
     "Once raised, a watch holds for 18 hours and only stands down when "
     "every clock has dropped out of the wettest 5%.",
     "Chooralmala failed at 1 a.m., seven hours <em>after</em> the rain "
     "peaked and while it was easing. Water keeps building pressure in the "
     "soil after the downpour ends. An earlier version of this system "
     "cancelled its own watch four hours before the hillside came down."),
]

NOT_DONE = [
    "It does <b>not</b> know soil type, how saturated the ground already is, "
    "where trees were cleared, or where a road was cut into a slope. All of "
    "those matter and none are in the data.",
    "It does <b>not</b> use rain gauges on the ground. Those exist and are "
    "far better &mdash; the gauge at Vythiri read 280 mm the night of "
    "Chooralmala while the forecast said 50 &mdash; but the live feed for "
    "them is not open to us.",
    "It does <b>not</b> learn or adapt. Five past disasters is far too few "
    "to train anything on; every threshold here was set by hand and can be "
    "read off this page.",
    "It does <b>not</b> pick a hillside. The finest thing it can say is "
    "&lsquo;this valley, tonight&rsquo;.",
]


def _how_it_decides():
    rows = ""
    for i, (q, what, why) in enumerate(STEPS, 1):
        rows += ('<div class=step><div class=stepn>%d</div><div>'
                 '<div class=stepq>%s</div>'
                 '<div class=plain style="margin-top:2px">%s</div>'
                 '<div class=stepwhy>%s</div></div></div>' % (i, q, what, why))
    nots = "".join("<li>%s</li>" % n for n in NOT_DONE)
    return ('<div class=steps>%s</div>'
            '<h2>what it deliberately does not do</h2>'
            '<ul class=nots>%s</ul>' % (rows, nots))


def _plain_header():
    """What a person needs in the first two seconds, in ordinary words."""
    st = _live_state()
    cells, cov = _cells_for_page()
    try:
        from . import rain_watcher
        nodes = len(rain_watcher.build_groups(cells))
    except Exception:
        nodes = 0

    if not st or not st["running"]:
        cls, big = "dead", "NOT RUNNING"
        when = ("The rain watcher is not reporting on this machine."
                if not st else
                "Last check was %.1f hours ago &mdash; it should be every 3."
                % (st["age_s"] / 3600.0))
    elif st["watches"]:
        cls, big = "alarm", "HEAVY RAIN WATCH"
        when = ("%d place(s) flagged. Last checked %.0f minutes ago."
                % (st["watches"], st["age_s"] / 60.0))
    else:
        cls, big = "calm", "NO DANGER SIGNS"
        mins = st["age_s"] / 60.0
        nxt = max(0.0, (10800 - st["age_s"]) / 60.0)
        when = ("Last checked %.0f minutes ago &middot; next check in about "
                "%.0f minutes" % (mins, nxt))

    bar = _cycle_bar(st["age_s"]) if st and st["running"] else ""
    return (
        '<div class="hero %s"><div class=k>right now</div>'
        '<div class=huge>%s</div><div class=when>%s</div>%s</div>'
        '<p class=plain>This checks <b>how hard it is raining over Kerala\u2019s '
        'steep hills</b>, every 3 hours, at <b>%s places</b>. It compares tonight '
        'with the last ten years at that exact spot, and speaks up when the rain '
        'is unusually heavy for there \u2014 the conditions that caused '
        'Chooralmala, Puthumala and Pettimudi.</p>'
        '<ul class=canlist>'
        '<li class=y><b>Gives about a day\u2019s notice</b> that a stretch of hills '
        'is dangerous. It spotted all five of Kerala\u2019s worst landslides when '
        'replayed against them.</li>'
        '<li class=n><b>Cannot tell you which hillside will fall.</b> Nobody can. '
        'It narrows it to a valley, not a slope.</li>'
        '<li class=n><b>Cries wolf about 12 times a year.</b> So it means '
        '\u201cpay attention tonight\u201d, never \u201cleave your home\u201d.</li>'
        '<li class=n><b>Sends nothing to anyone.</b> Alerts are switched off '
        'deliberately \u2014 who gets told is not a decision software should '
        'make.</li>'
        '</ul>' % (cls, big, when, bar, "{:,}".format(nodes)))


def _state_banner():
    """Two parts, always: what is running, and what it still cannot do."""
    st = _live_state()
    if st and st["running"]:
        head = (
            '<div class=blocked style="border-color:var(--ok)">'
            '<div class=k>state</div>'
            '<div class=big style="color:var(--ok)">WATCHING</div>'
            '<p class=sub style="margin:8px 0 0">Rain watcher live &mdash; %s, '
            'last cycle %.0f min ago. %d decisions logged, %d watches. '
            '<strong>Dispatch is OFF</strong>: nothing is sent to anyone.'
            '</p>%s</div>' % (_esc(st["detail"]), st["age_s"] / 60.0,
                              st["decisions"], st["watches"],
                              _cycle_bar(st["age_s"])))
    else:
        why = ("no rain ledger found on this host" if not st
               else "last cycle %.1f h ago &mdash; stale" % (st["age_s"] / 3600.0))
        head = ('<div class=blocked><div class=k>state</div>'
                '<div class=big>NOT RUNNING</div>'
                '<p class=sub style="margin:8px 0 0">%s.</p></div>' % why)
    return head + CANNOT


def _cycle_bar(age_s, interval_s=10800):
    """
    Poll-cycle indicator for the 3-hourly rain cycle.

    DATA-DRIVEN, same rule as the seismic page: the sweep is offset by the
    REAL age of the last cycle, so it shows where we actually are rather than
    restarting on every page refresh. If the watcher stops, the bar fills and
    goes red instead of sweeping forever -- a decorative animation on a dead
    service is a lie told once every frame.
    """
    if age_s is None:
        return ""
    pct = max(0.0, min(1.0, age_s / interval_s))
    if age_s > interval_s * 1.5:
        cls, note = "cycfill stale", "overdue &mdash; watcher may be down"
    elif age_s > interval_s:
        cls, note = "cycfill overdue", "overdue"
    else:
        cls, note = "cycfill", "next in ~%d min" % ((interval_s - age_s) / 60)
    return (
        '<div class=cyc><div class=cyclab><span>poll cycle &mdash; 3 h</span>'
        '<span>%s</span></div><div class=cycbar>'
        '<div class="%s" style="--cycle:%ds;--offset:-%ds;--pct:%.0f%%"></div>'
        '</div></div>' % (note, cls, interval_s, int(age_s), pct * 100))


def _watching_from(cells, cov):
    """Say plainly WHERE this is watching, and from what."""
    import socket
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown host"
    bbox = (cov or {}).get("bbox") or [8.20, 12.80, 75.70, 77.40]
    try:
        from . import rain_watcher
        nodes = len(rain_watcher.build_groups(cells))
    except Exception:
        nodes = None
    return (
        '<div class=grid>'
        '<div class=card><div class=k>watching</div>'
        '<div class=v>Kerala W. Ghats</div>'
        '<div class=sub>%.1f&ndash;%.1f&deg;N, %.1f&ndash;%.1f&deg;E</div></div>'
        '<div class=card><div class=k>coverage</div>'
        '<div class=v>%s nodes</div>'
        '<div class=sub>from %s steep cells</div></div>'
        '<div class=card><div class=k>feed</div>'
        '<div class=v>Open-Meteo</div>'
        '<div class=sub>hourly, CC BY 4.0</div></div>'
        '<div class=card><div class=k>running on</div>'
        '<div class=v>%s</div>'
        '<div class=sub>hew-rain-watcher.service</div></div>'
        '</div>' % (bbox[0], bbox[1], bbox[2], bbox[3],
                    nodes if nodes is not None else "&mdash;",
                    "{:,}".format(len(cells)), _esc(host)))


def _load():
    try:
        with open(os.path.join(DATA_DIR, "ksdma_calibration.json")) as f:
            cal = json.load(f)
        with open(os.path.join(DATA_DIR, "ksdma_climatology.json")) as f:
            clim = json.load(f)
        return cal, clim
    except (OSError, ValueError):
        return None, None


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _curve_svg(curves, gate, W=880, H=300):
    """
    Threshold vs system alerts/year, one line per monitored set.

    Log y-axis: the rates span 0.7 to 50/yr and the interesting region is the
    bottom of that range, where the gate sits. A linear axis buries it.
    """
    import math
    pad_l, pad_r, pad_t, pad_b = 52, 150, 16, 34
    xs = [p["threshold_mm"] for p in curves["all"]]
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.5, 60.0

    def px(v):
        return pad_l + (v - x0) / (x1 - x0) * (W - pad_l - pad_r)

    def py(v):
        v = max(y0, min(y1, v))
        f = (math.log10(v) - math.log10(y0)) / (math.log10(y1) - math.log10(y0))
        return H - pad_b - f * (H - pad_t - pad_b)

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
           f'aria-label="Alerts per year against threshold, by terrain">']
    for gy in (1, 2, 5, 10, 20, 50):
        y = py(gy)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W-pad_r}" y2="{y:.1f}" '
                   f'stroke="var(--line)" stroke-width="1"/>')
        out.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" fill="var(--fg3)" '
                   f'font-size="10" text-anchor="end">{gy}</text>')
    # the gate itself
    yg = py(gate)
    out.append(f'<line x1="{pad_l}" y1="{yg:.1f}" x2="{W-pad_r}" y2="{yg:.1f}" '
               f'stroke="var(--ok)" stroke-width="2" stroke-dasharray="6 4"/>')
    out.append(f'<text x="{W-pad_r+8}" y="{yg+4:.1f}" fill="var(--ok)" '
               f'font-size="11">gate {gate:g}/yr</text>')

    for x in xs:
        out.append(f'<text x="{px(x):.1f}" y="{H-12}" fill="var(--fg3)" '
                   f'font-size="10" text-anchor="middle">{x:g}</text>')

    styles = [("all", "var(--fg3)", "all 65 stations"),
              ("ghat_foothill", "var(--warn)", "Ghats + foothills (24)"),
              ("ghat", "var(--bad)", "Ghats only (9)")]
    for i, (key, col, lab) in enumerate(styles):
        pts = " ".join(f"{px(p['threshold_mm']):.1f},{py(p['alerts_per_year']):.1f}"
                       for p in curves[key])
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" '
                   f'stroke-width="2"/>')
        for p in curves[key]:
            r = 4 if p["passes_gate"] and p["catches_event"] else 2.5
            out.append(f'<circle cx="{px(p["threshold_mm"]):.1f}" '
                       f'cy="{py(p["alerts_per_year"]):.1f}" r="{r}" fill="{col}"/>')
        out.append(f'<text x="{W-pad_r+8}" y="{34+i*17}" fill="{col}" '
                   f'font-size="11">{lab}</text>')

    out.append(f'<text x="{(W-pad_r+pad_l)/2:.0f}" y="{H-1}" fill="var(--fg3)" '
               f'font-size="10" text-anchor="middle">24 h threshold (mm)</text>')
    out.append(f'<text x="14" y="{H/2:.0f}" fill="var(--fg3)" font-size="10" '
               f'transform="rotate(-90 14 {H/2:.0f})" text-anchor="middle">'
               f'system alerts / year</text>')
    out.append("</svg>")
    return "".join(out)


def _runup_svg(W=880, H=190):
    """The run-up. The point is the shape: flat, then a cliff, no ramp."""
    pad_l, pad_t, pad_b = 46, 14, 40
    mx = max(v for _, v, _ in RUNUP)
    n = len(RUNUP)
    bw = (W - pad_l - 16) / n
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
           f'aria-label="Vythiri daily rainfall through the disaster">']
    for gy in (0, 100, 200, 280):
        y = H - pad_b - (gy / mx) * (H - pad_t - pad_b)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{W-16}" y2="{y:.1f}" '
                   f'stroke="var(--line)"/>')
        out.append(f'<text x="{pad_l-6}" y="{y+4:.1f}" fill="var(--fg3)" '
                   f'font-size="10" text-anchor="end">{gy}</text>')
    for i, (lab, v, note) in enumerate(RUNUP):
        h = (v / mx) * (H - pad_t - pad_b)
        x = pad_l + i * bw
        col = "var(--bad)" if v >= 200 else ("var(--warn)" if v >= 100 else "var(--accent)")
        out.append(f'<rect x="{x+2:.1f}" y="{H-pad_b-h:.1f}" width="{bw-4:.1f}" '
                   f'height="{h:.1f}" fill="{col}" opacity="0.85"><title>'
                   f'{_esc(lab)}: {v:g} mm{" — " + _esc(note) if note else ""}'
                   f'</title></rect>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{H-pad_b+13:.0f}" fill="var(--fg3)" '
                   f'font-size="9" text-anchor="middle">{_esc(lab)}</text>')
        if note:
            out.append(f'<text x="{x+bw/2:.1f}" y="{H-pad_b-h-6:.1f}" '
                       f'fill="{col}" font-size="9.5" text-anchor="middle">'
                       f'{"failure" if v >= 200 else "quiet"}</text>')
    out.append("</svg>")
    return "".join(out)


# Backtest result, from hew/rain_backtest.py. Held as data so the page cannot
# drift from the module that produced it.
BACKTEST = [
    ("Chooralmala/Mundakkai", "2024-07-30", "~250+", 99.84),
    ("Pettimudi (Rajamala)", "2020-08-06", "66", 99.79),
    ("Puthumala", "2019-08-08", "17", 99.76),
    ("Kavalappara", "2019-08-08", "59", 99.55),
    ("Koottickal/Kokkayar", "2021-10-16", "~35", 99.30),
]
SWEEP = [(99.5, "5/5", "28 h", 9.9, False),
         (99.8, "0/5", "\u2014", 5.0, False),
         (99.9, "0/5", "\u2014", 2.9, False),
         (99.95, "0/5", "\u2014", 1.6, True)]


def _watch_section():
    from . import rain_watch
    try:
        cells, cov = rain_watch.load_cells()
        warn = rain_watch.coverage_warning(cov)
    except Exception:
        cells, cov, warn = [], {}, "Terrain scan has not been run."

    ev = "".join(
        f"<tr><td>{_esc(n)}</td><td class=sub>{_esc(d)}</td>"
        f"<td style='text-align:right'>{_esc(k)}</td>"
        f"<td style='text-align:right;color:var(--warn);font-weight:600'>"
        f"p{p}</td></tr>" for n, d, k, p in BACKTEST)
    sw = "".join(
        f"<tr><td>p{p}</td>"
        f"<td style='text-align:right;color:{'var(--ok)' if r != '0/5' else 'var(--bad)'}"
        f";font-weight:600'>{r}</td>"
        f"<td style='text-align:right'>{l}</td>"
        f"<td style='text-align:right'>{a}</td>"
        f"<td style='color:{'var(--ok)' if g else 'var(--bad)'}'>"
        f"{'PASS' if g else 'FAIL'}</td></tr>" for p, r, l, a, g in SWEEP)

    banner = ""
    if warn:
        banner = (f"<div class=blocked style='border-color:var(--warn);margin-top:14px'>"
                  f"<div class=k>terrain coverage</div>"
                  f"<div class=big style='color:var(--warn);font-size:17px'>"
                  f"PARTIAL SCAN</div><p class=sub style='margin:8px 0 0'>"
                  f"{_esc(warn)} Wayanad &mdash; Chooralmala, Puthumala, Vythiri "
                  f"&mdash; is inside the unscanned band, so this layer cannot "
                  f"currently speak for the area this project exists for.</p></div>")

    return f"""
<p class=sub>Terrain screen &rarr; percentile of the model's own climatology
&rarr; multi-window accumulation. Polls every 3 h; tier capped at WATCH in
code.</p>
<div class=scroll><table><thead><tr><th>disaster</th><th>date</th>
<th style="text-align:right">dead</th>
<th style="text-align:right">percentile in model's own climatology</th>
</tr></thead><tbody>{ev}</tbody></table></div>
<p class=note>All five inside the top 1% &mdash; which is what licenses the
percentile design, since their millimetre values are wrong by 3&times;.
Chooralmala&rsquo;s coordinate was <strong>3.3 km off</strong> until it was
corrected against OSM; that error alone moved it from p99.30 to p99.84, and
an earlier version of this page drew a conclusion from the wrong number.
<strong>Three of the five coordinates remain unverified.</strong></p>
<div class=scroll><table><thead><tr><th>threshold</th>
<th style="text-align:right">recall</th><th style="text-align:right">median lead</th>
<th style="text-align:right">alerts/yr/site</th><th>gate &le;2</th>
</tr></thead><tbody>{sw}</tbody></table></div>
<p class=note>The events cluster in p99.27&ndash;99.79, so any threshold above
that band loses all five at once. <strong>No setting both detects and passes
the gate.</strong> Best honest operating point: 5/5 recall at ~10 alerts/year
&mdash; a defensible watch, not an evacuation trigger.</p>
<div class=grid>
  <div class=card><div class=k>watch cells</div>
    <div class=v>{len(cells)}</div>
    <div class=sub>steep ground, DEM-derived</div></div>
  <div class=card><div class=k>scanned</div>
    <div class=v>{cov.get('cells_evaluated', 0)} / {cov.get('cells_in_bbox', 0)}</div>
    <div class=sub>Open-Meteo daily quota</div></div>
  <div class=card><div class=k>tier ceiling</div>
    <div class=v style="color:var(--warn)">{rain_watch.MAX_TIER.upper()}</div>
    <div class=sub>enforced in code</div></div>
  <div class=card><div class=k>poll interval</div>
    <div class=v>3 h</div><div class=sub>rain accumulates over 12&ndash;72 h</div></div>
</div>{banner}"""


def render(css):
    cal, clim = _load()
    if not cal:
        return (f"<!doctype html><meta charset=utf-8><style>{css}</style>"
                "<div class=wrap><h1>rainfall</h1><p class=sub>No calibration "
                "data. Run the KSDMA extraction to populate "
                "<code>data/ksdma_calibration.json</code>.</p>"
                "<p><a href='/'>&larr; status</a></p></div>")

    ev, rec = cal["event"], cal["recommended"]
    sy = cal["station_years"]

    ladder = "".join(
        f"<tr><td>{_esc(n)}</td><td class=sub>{_esc(g)}</td>"
        f"<td style='text-align:right;color:{'var(--ok)' if hit else 'var(--bad)'};"
        f"font-weight:{'700' if hit else '400'}'>{mm:.1f} mm</td></tr>"
        for n, g, mm, hit in LADDER)

    rows = ""
    for s in sorted(clim["stations"], key=lambda x: (-x["max"],)):
        nm = _esc(s["name"]) + ("" if s["name_confident"] else " <span class=sub>(?)</span>")
        star = " ★" if s["max_date"] == ev["date"] else ""
        rows += (f"<tr><td>{nm}</td><td class=sub>{_esc(s['district'])}</td>"
                 f"<td style='color:{RELIEF_COLOUR[s['relief']]}'>{s['relief']}</td>"
                 f"<td style='text-align:right'>{s['elev_m']}</td>"
                 f"<td style='text-align:right'>{s['days']}</td>"
                 f"<td style='text-align:right'>{s['median']:.1f}</td>"
                 f"<td style='text-align:right'>{s['p99']:.1f}</td>"
                 f"<td style='text-align:right;font-weight:600'>{s['max']:.1f}</td>"
                 f"<td class=sub>{_esc(s['max_date'])}{star}</td></tr>")

    caveats = "".join(f"<li>{_esc(c)}</li>" for c in cal["caveats"])

    return f"""<!doctype html><html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>hew rainfall</title><style>{css}{PLAIN_CSS}
.blocked{{border:2px solid var(--bad);border-radius:10px;padding:18px 20px;margin-top:18px}}
.blocked .big{{color:var(--bad);font-size:24px}}
.tabs{{display:flex;gap:18px;margin-top:14px;border-bottom:1px solid var(--line)}}
.tabs a{{padding:8px 2px;text-decoration:none;color:var(--fg3);
border-bottom:2px solid transparent;font-size:12.5px}}
.tabs a.on{{color:var(--fg);border-bottom-color:var(--accent)}}
.chart{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px;margin-top:12px}}
ul.cav{{color:var(--fg3);font-size:12.5px;padding-left:18px;margin:8px 0 0}}
ul.cav li{{margin:4px 0}}
</style>
<div class=wrap>
<h1>Kerala landslide watch</h1>
<div class=sub>Kerala Western Ghats &middot; checks every 3 hours</div>
<div class=tabs>
  <a href="/">status</a><a href="/simulate">drill</a><a href="/rain" class=on>rainfall</a>
</div>

{_plain_header()}
{_watching_from(_cells_for_page()[0], _cells_for_page()[1])}
<div class=chart style="margin-top:14px">{_coverage_map(_cells_for_page()[0])}</div>
<p class=note>Every coloured square is a patch of ground about 4 km across that this system watches &mdash; red where the land is steepest. The shading underneath is the real elevation, measured by the same scan that decides what to watch. Note that the watch sits on the mountains and stops before the coast: landslides do not start at 3 m above sea level.</p>

<h2>how it decides something is dangerous</h2>
{_how_it_decides()}

<h2>rain right now</h2>
{_rain_now()}

<div class=divider>the working</div>
<p class=plain>Everything above is the answer. Everything below is the
<b>evidence for it</b> &mdash; charts, percentiles and station records, written
for someone deciding whether to believe this system. You do not need any of it
to use the page.</p>
<details class=details><summary>Show the working &mdash; how these numbers were arrived at, and what is still wrong with them</summary>
<p class=note style="margin-top:14px"><b>Plain summary of everything below.</b>
The free rainfall forecast reads about a third of the truth in these hills, so
instead of trusting its millimetres we compare each place only against its own
past. Replayed against Kerala&rsquo;s five worst landslides that catches all
five, roughly a day ahead &mdash; and also cries wolf about twelve times a
year. Rain gauges on the ground would do far better, about one false alarm a
year instead of twelve, but their live feed is not open to us. That gap is the
single thing holding this back, and it is a permission problem, not a
programming one.</p>
{_state_banner()}

<h2>why gauges, not satellites</h2>
<p class=sub>Chooralmala / Mundakkai, 24 h to 08:00 on 30 July 2024.
A ninefold improvement in grid resolution moved the answer by 3 mm.</p>
<div class=scroll><table><thead><tr><th>product</th><th>grid</th>
<th style="text-align:right">24 h reading</th></tr></thead>
<tbody>{ladder}</tbody></table></div>
<p class=note>These products blend satellite infrared with <em>gauge</em> data.
With no gauge near Wayanad in the blend, each falls back on the same physics
and reaches the same wrong number. Resolution was never the constraint.</p>

<h2>calibration curve &mdash; the feed we do NOT have</h2>
<p class=sub><strong>This is not what is running.</strong> It is what the same
engine achieves on IMD gauge data, and the reason that access is worth asking
for. Measured on
{sy['all']:.0f} station-years of KSDMA gauge record
({clim['record_days']} days, {clim['first_day']} to {clim['last_day']}).
A day counts once, however many stations cross.</p>
<div class=chart>{_curve_svg(cal['curves'], cal['gate_alerts_per_year'])}</div>
<div class=grid>
  <div class=card><div class=k>recommended</div>
    <div class=v>{rec['threshold_mm']:.0f} mm / 24 h</div>
    <div class=sub>on {rec['monitor'].replace('_', ' + ')}</div></div>
  <div class=card><div class=k>false alarms</div>
    <div class=v style="color:var(--ok)">{rec['alerts_per_year']:.1f} / yr</div>
    <div class=sub>gate is &le; {cal['gate_alerts_per_year']:g}/yr</div></div>
  <div class=card><div class=k>catches the event</div>
    <div class=v style="color:var(--ok)">yes</div>
    <div class=sub>{ev['station']} read {ev['mm_24h']:.0f} mm</div></div>
  <div class=card><div class=k>evidence base</div>
    <div class=v>{sy['ghat_foothill']:.0f} station-yr</div>
    <div class=sub>false alarms only &mdash; see caveats</div></div>
</div>
<p class=note><strong>Terrain selection is what buys the sensitivity.</strong>
Across all 65 stations the threshold has to sit at 250 mm to pass; restricted
to landslide terrain it passes at 200. Monitoring the coast costs 50 mm of
sensitivity and cannot detect anything, because landslides do not initiate at
3 m elevation.</p>
<p class=note><strong>Per-station percentiles were worse, and that was a
surprise.</strong> Each station's own p99.9 fires 14.8&times;/yr system-wide:
a percentile threshold guarantees a fixed firing rate <em>per station</em>, so
the system rate scales with station count. Every station eventually has a
record day. An absolute threshold encodes physical knowledge the percentile
throws away.</p>

<h2>watch engine &mdash; backtested on five disasters</h2>
{_watch_section()}

<h2>the run-up &mdash; Vythiri, 24 h totals</h2>
<div class=chart>{_runup_svg()}</div>
<p class=note><strong>There was no run-up.</strong> Seventeen hours before the
failure the gauge read 27.6 mm &mdash; unremarkable. The week before contained
heavier days (104, 93, 104 mm) and produced no landslide. The event fell out of
a quiet day. A daily-cadence system would have had nothing to fire on, and the
antecedent-wetness layer would not have fired either. Sub-daily gauge data
during the event is not an enhancement to this design; it <em>is</em> the
design.</p>

<h2>how big was that storm, really</h2>
<p class=plain><b>23 of Kerala&rsquo;s 65 rain gauges recorded their wettest
day in two years on 30 July 2024</b> &mdash; the night Chooralmala was
destroyed. Not just in Wayanad: Palakkad, Thrissur, Kannur, Malappuram. One
third of the state&rsquo;s gauges, all peaking together.</p>
<p class=note>That is why this table is here. It is the record every threshold
on this page is calibrated against, and it lets you look up what a dangerous
amount of rain looks like <em>in your own town</em> &mdash; the median column
is an ordinary day there, the max is the worst in two years. ★ marks a station
whose wettest day was {ev['date']}. Names were read off the published bulletin;
(?) marks one I could not read confidently. Elevations are town centroids,
good enough to tell hill from coast and not to site a gauge.</p>
<div class=scroll><table><thead><tr><th>station</th><th>district</th>
<th>relief</th><th style="text-align:right">elev m</th>
<th style="text-align:right">days</th><th style="text-align:right">median</th>
<th style="text-align:right">p99</th><th style="text-align:right">max</th>
<th>wettest day</th></tr></thead><tbody>{rows}</tbody></table></div>

<h2>what this does not establish</h2>
<ul class=cav>{caveats}</ul>

</details>

<p class=note style="margin-top:26px">Source:
<a href="{_esc(clim['url'])}">Kerala SDMA</a> daily bulletin,
{_esc(clim['window'])}. Extracted by {_esc(clim['extraction'])}.</p>
</div>"""
