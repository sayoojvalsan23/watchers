"""
Read-only status page for the Phase 1 watcher.

Phase 1's exit criterion is "60 days, no unexplained gaps". That is not
observable from a log tail on a headless Pi, so this serves the one view
that makes it checkable: when did we last poll, when did the canary last
pass, and where are the holes.

    python3 -m hew.status --db ~/.local/share/hew/hew.db --port 8080

DESIGN CONSTRAINTS, all deliberate:

  * SEPARATE PROCESS. It must not be able to slow, block or crash the
    watcher. Run it as its own unit; if it dies, detection continues.
  * READ-ONLY. The database is opened with mode=ro so a bug here cannot
    corrupt the decision ledger. There are no POST routes and no controls.
  * ZERO DEPENDENCIES. stdlib http.server, same as the rest of the project.
  * LAN ONLY by default. Binds 0.0.0.0 so you can reach it from a laptop on
    the same network; there is no authentication, so do not port-forward it.

It shows state. It does not change any.
"""

import argparse
import html
import json
import math
import os
import re
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

POLL_SECONDS = 60            # matches watcher.POLL_SECONDS
STALE_SECONDS = 300          # matches watcher.STALE_POLL_SECONDS
CANARY_STALE_SECONDS = 7200  # canary runs hourly; two missed is a problem


def _conn(db):
    uri = "file:" + urllib.parse.quote(os.path.abspath(db)) + "?mode=ro"
    c = sqlite3.connect(uri, uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


# How old a layer may get before the page says so. These are not arbitrary:
# glacial lakes FORM AND GROW as glaciers retreat, so a stale lake inventory
# is a growing set of unmapped hazards -- silent false negatives of exactly
# the kind that made the Nepal box invisible for so long. The registry's own
# sources are older than they look: RGI is nominally year 2000, and the HMA
# lake inventory is the 2015-2018 epoch.
_STALE_DAYS = {
    "glacial_lakes_hma": 730,      # lakes change; 2 years is generous
    "hazard_sites_himalaya": 730,
    "places_himalaya": 1095,       # settlements move slowly
    "rivers_himalaya": 1095,
    "places_region": 1095,
    "rivers_region": 1095,
}


def _data_age():
    """
    Age of each static layer, read from data/manifest.json.

    Nothing anywhere warned that a layer had gone stale. A status page that
    asserts nothing it has not read should also say how old what it read is.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "manifest.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            man = json.load(f)
    except (OSError, ValueError):
        return []

    now, out = datetime.now(timezone.utc), []
    for name, meta in sorted(man.items()):
        if not isinstance(meta, dict):
            continue
        stamp = meta.get("built") or meta.get("fetched_at") or meta.get("fetched")
        epoch = meta.get("epoch")
        days = None
        if stamp:
            try:
                d = datetime.fromisoformat(str(stamp))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                days = (now - d).days
            except ValueError:
                days = None
        # Age against the SOURCE EPOCH when there is one, not the build date.
        # glacial_lakes_hma has no fetch stamp and would otherwise read "ok"
        # forever -- and it is the single layer whose staleness matters most,
        # because the lakes it is missing are the ones that formed since.
        epoch_days = None
        if epoch:
            years = [int(y) for y in re.findall(r"\d{4}", str(epoch))]
            if years:
                epoch_days = (now - datetime(max(years), 12, 31,
                                             tzinfo=timezone.utc)).days

        limit = _STALE_DAYS.get(name)
        effective = max(d for d in (days, epoch_days) if d is not None) \
            if (days is not None or epoch_days is not None) else None
        out.append({"name": name, "epoch": epoch, "days": days,
                    "epoch_days": epoch_days,
                    "stale": bool(limit and effective is not None
                                  and effective > limit)})
    return out


# Local timezone of the machine, resolved once. Times are shown as UTC AND
# local: UTC because every record in the ledger and every upstream feed is
# UTC and that is what you quote when comparing notes with USGS, local
# because that is what the operator's day is in.
#
# Note this is the SERVER's local time, not the event's. A Langtang event
# happens in NPT (+05:45) while this Pi runs IST (+05:30) -- so the local
# column answers "when did I need to know", not "what did the clock say on
# the mountain". Labelling it avoids the confusion.
try:
    from zoneinfo import ZoneInfo
    _LOCAL = datetime.now().astimezone().tzinfo
    _LOCAL_NAME = datetime.now().astimezone().strftime("%Z") or "local"
except Exception:
    _LOCAL, _LOCAL_NAME = timezone.utc, "UTC"


def _when(iso, with_date=True):
    """'2026-08-26 02:52 UTC / 08:22 IST' from a stored ISO timestamp."""
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return str(iso)[:19].replace("T", " ")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    u = d.astimezone(timezone.utc)
    l = d.astimezone(_LOCAL)
    stamp = u.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")
    return "%s UTC <span class=sub>%s %s</span>" % (
        stamp, l.strftime("%H:%M"), _LOCAL_NAME)


def _age(iso):
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc)
                - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def snapshot(db):
    """Everything the page needs, in one read."""
    out = {"db": db, "now": datetime.now(timezone.utc).isoformat()}
    if not os.path.exists(db):
        out["error"] = "database not found — has the watcher ever run?"
        return out
    with _conn(db) as c:
        def one(q, *a):
            r = c.execute(q, a).fetchone()
            return r[0] if r else None

        out["last_poll"] = one(
            "SELECT at FROM heartbeats WHERE source='usgs_catalogue'"
            " AND ok=1 ORDER BY at DESC LIMIT 1")
        out["last_canary"] = one(
            "SELECT at FROM heartbeats WHERE source='canary' AND ok=1"
            " ORDER BY at DESC LIMIT 1")
        out["last_fail"] = one(
            "SELECT at FROM heartbeats WHERE ok=0 ORDER BY at DESC LIMIT 1")
        out["poll_age"] = _age(out["last_poll"])
        out["canary_age"] = _age(out["last_canary"])
        out["candidates"] = one("SELECT COUNT(*) FROM candidates") or 0
        out["decisions"] = one("SELECT COUNT(*) FROM decisions") or 0
        out["config_version"] = one(
            "SELECT version FROM config WHERE active=1")

        out["tiers"] = {r["tier"]: r["n"] for r in c.execute(
            "SELECT tier, COUNT(*) n FROM decisions GROUP BY tier")}

        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        out["beats"] = [dict(r) for r in c.execute(
            "SELECT source, ok, at FROM heartbeats WHERE at > ?"
            " ORDER BY at", (since,))]

        # Everything evaluated, rejects INCLUDED. The ledger already keeps
        # negatives -- that is a protected invariant, so "why did it not
        # fire" is answerable -- but the page hid them, so the answer was
        # only reachable by opening SQLite. Absence of dispatch and absence
        # of a working filter look identical without this.
        out["seen"] = [dict(r) for r in c.execute(
            "SELECT c.external_id, c.observed_at, c.magnitude, c.depth_km,"
            " c.lat, c.lon,"
            " d.score, d.tier, d.factors, d.nearest_site, d.nearest_km,"
            " d.suppressed, d.suppress_reason, d.decided_at"
            " FROM decisions d JOIN candidates c ON c.id = d.candidate_id"
            " ORDER BY d.decided_at DESC LIMIT 40")]

        # Rejects collapsed by reason: 40.6% of catalogue events are
        # fixed-depth, so listing them individually buries everything else.
        out["reject_reasons"] = [dict(r) for r in c.execute(
            "SELECT COALESCE(NULLIF(d.suppress_reason,''), d.factors,"
            " 'unspecified') reason, COUNT(*) n FROM decisions d"
            " WHERE d.tier = 'reject' GROUP BY reason"
            " ORDER BY n DESC LIMIT 8")]

        # Highest tier decided in the last 24 h, for the headline banner.
        since24 = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        # Keyed on when the EVENT happened, not when we evaluated it. A
        # backfilled decision is stamped decided_at = now, so keying on that
        # would put a week-old collapse in the last-24h window and light the
        # banner for history. observed_at is also the honest field for a live
        # event: the operator cares when the ground moved.
        out["recent_tiers"] = {r["tier"]: r["n"] for r in c.execute(
            "SELECT d.tier, COUNT(*) n FROM decisions d"
            " JOIN candidates c2 ON c2.id = d.candidate_id"
            " WHERE c2.observed_at > ? GROUP BY d.tier", (since24,))}

        # The most recent real decision, with NO time window. The banner
        # covers 24 h; without this, someone returning after two days sees
        # "nothing" and reads it as "nothing has happened", when what it
        # means is "nothing since yesterday". The window boundary must not
        # be able to hide a detection.
        r = c.execute(
            "SELECT d.tier, d.score, c.observed_at AS decided_at,"
            " d.nearest_site, d.nearest_km, c.magnitude, c.depth_km,"
            " c.lat, c.lon, c.external_id, d.factors"
            " FROM decisions d JOIN candidates c ON c.id = d.candidate_id"
            # watch/advisory/warning only. 'reject' is obvious, but 'log' is
            # the trap: it means evaluated and scored BELOW watch, i.e. the
            # detector looked and was unimpressed. Showing a log row as the
            # LAST DETECTION reports a non-event as a detection, which is the
            # opposite of the failure this line exists to prevent.
            " WHERE d.tier IN ('watch', 'advisory', 'warning')"
            " ORDER BY c.observed_at DESC LIMIT 1"
        ).fetchone()
        out["last_detection"] = dict(r) if r else None

        out["data_age"] = _data_age()

        out["recent"] = [dict(r) for r in c.execute(
            "SELECT c.external_id, c.observed_at, c.magnitude, c.depth_km,"
            " d.score, d.tier, d.nearest_site, d.nearest_km, d.suppressed,"
            " d.suppress_reason, d.decided_at"
            " FROM decisions d JOIN candidates c ON c.id = d.candidate_id"
            " WHERE d.tier != 'reject'"
            " ORDER BY d.decided_at DESC LIMIT 25")]

        try:
            out["corridor_rows"] = one("SELECT COUNT(*) FROM impact") or 0
        except sqlite3.OperationalError:
            out["corridor_rows"] = 0
    return out


def gaps(beats, expect_seconds=300, window_days=7):
    """
    Holes in the poll record. This is the Phase 1 metric: a gap is either a
    crash, a power failure, or a feed outage, and all three must be explained
    before the phase can be signed off.
    """
    polls = [b["at"] for b in beats
             if b["source"] == "usgs_catalogue" and b["ok"]]
    found = []
    for a, b in zip(polls, polls[1:]):
        try:
            d = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
        except ValueError:
            continue
        if d > expect_seconds:
            found.append({"from": a, "to": b, "seconds": d})
    return sorted(found, key=lambda g: -g["seconds"])


CSS = """
:root{--bg:#12141a;--card:#1b1e26;--line:#2b2f3a;--fg:#e8eaf0;--fg2:#a3a8b8;
--fg3:#6f7585;--ok:#4ea87a;--warn:#d0a24c;--bad:#d9584c;--accent:#6ea8e0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:ui-monospace,
SFMono-Regular,Menlo,monospace;font-size:14px;line-height:1.5}
.wrap{max-width:1000px;margin:0 auto;padding:28px 18px 70px}
h1{font-size:19px;margin:0 0 4px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3);
margin:32px 0 10px;font-weight:600}
.sub{color:var(--fg3);font-size:12.5px}
.big{font-size:34px;font-weight:700;letter-spacing:-.02em}
.state{border:2px solid var(--line);border-radius:10px;padding:20px 22px;margin-top:18px}
.state.ok{border-color:var(--ok)} .state.warn{border-color:var(--warn)}
.state.bad{border-color:var(--bad)}
.state.ok .big{color:var(--ok)} .state.warn .big{color:var(--warn)}
.state.bad .big{color:var(--bad)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:13px 15px}
.k{color:var(--fg3);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.v{font-size:19px;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;color:var(--fg3);font-weight:500;font-size:10.5px;
text-transform:uppercase;letter-spacing:.08em;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.t-warning{color:var(--bad);font-weight:700}
.t-advisory{color:var(--warn);font-weight:700}
.t-watch{color:var(--accent)} .t-log{color:var(--fg3)}
/* Why a decision landed where it did. The native title= tooltip needed a
   second of hover and never appeared at all on a touch screen, which is most
   of what an operator actually carries. tabindex makes it tap- and
   keyboard-reachable. */
.wh{position:relative;cursor:help;border-bottom:1px dotted currentColor}
.wh .wht{display:none;position:absolute;left:0;top:1.6em;z-index:50;
min-width:290px;max-width:420px;white-space:pre-line;text-align:left;
background:#12141a;border:1px solid var(--line);border-radius:8px;
padding:10px 12px;font:11.5px/1.55 ui-monospace,monospace;color:var(--fg2);
box-shadow:0 8px 24px rgba(0,0,0,.55)}
.wh:hover .wht,.wh:focus .wht,.wh:focus-within .wht{display:block}
.scroll{overflow-x:auto}
.bar{display:flex;gap:1px;height:26px;margin-top:8px}
.bar div{flex:1;border-radius:1px}
.note{color:var(--fg3);font-size:12px;margin-top:8px}

/* Poll-cycle indicator. DATA-DRIVEN, not decorative: the animation is
   offset by the real age of the last poll, so it shows where we actually
   are in the cycle rather than restarting on every page refresh. If the
   watcher stops, the bar does not keep sweeping -- it fills and goes red. */
.cyc{margin-top:14px}
.cyclab{display:flex;justify-content:space-between;font-size:11px;
color:var(--fg3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:5px}
.cycbar{height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.cycfill{height:100%;border-radius:3px;background:var(--ok);width:0%;
animation:sweep var(--cycle,60s) linear infinite;
animation-delay:var(--offset,0s)}
.cycfill.overdue{width:100%;background:var(--bad);animation:pulse 1.4s ease-in-out infinite}
.cycfill.stale{width:100%;background:var(--bad);animation:none}
@keyframes sweep{from{width:0%}to{width:100%}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@media (prefers-reduced-motion:reduce){
  .cycfill{animation:none;width:var(--pct,0%)}
  .cycfill.overdue,.cycfill.stale{animation:none;width:100%}
}
a{color:var(--accent)}
"""


_CORRIDOR_NET = None


def _corridor_for(lat, lon, limit=8):
    """
    Downstream settlements for one point, computed on demand.

    Cheap enough to do live only since the routing spatial index landed:
    0.14 s for the 26 August source, against ~17 s before. Loaded lazily so
    a dashboard that never shows a detection never pays for the network.

    Off the trigger path by construction -- this is display only, runs after
    the decision is long since in the ledger, and any failure here must show
    an incomplete panel rather than break the page.
    """
    global _CORRIDOR_NET
    try:
        from .routing import (RiverNetwork, load_settlements, trace_branches,
                              exposed_settlements_union)
        if _CORRIDOR_NET is None:
            _CORRIDOR_NET = (RiverNetwork.load(), load_settlements())
        net, places = _CORRIDOR_NET
        br = trace_branches(net, lat, lon, uncertainty_km=15.0)
        if not br:
            return None, []
        ex = exposed_settlements_union(br, places, corridor_km=2.0)
        return [b["name"] for b in br], ex[:limit], len(ex)
    except Exception:
        return None, [], 0


# Plain-English reasons a decision landed where it did. The ledger already
# answers "why did it not fire" -- that is a protected invariant -- but it
# answers in factor strings like capped_watch_unknown_depth, which is an
# answer only to someone who has read detect.py. This is the same answer for
# everyone else.
_WHY = {
    "very_shallow":  ("+35", "source at or near the surface — the signature of a collapse"),
    "shallow":       ("+20", "shallow source"),
    "very_near_hazard": ("+45", "sitting on a mapped hazard"),
    "near_hazard":   ("+30", "close to a mapped hazard"),
    "magnitude_band": ("+15", "magnitude in the collapse band"),
    "unknown_depth": ("cap", "DEPTH UNCONSTRAINED — the catalogue gave a default, "
                             "not a measurement, so a collapse cannot be told from "
                             "an ordinary earthquake. Capped at watch."),
    "too_deep":      ("0",   "too deep for a surface failure"),
    "magnitude_out_of_range": ("0", "magnitude outside the band this filter scores"),
    "outside_bbox":  ("0",   "outside the watched area"),
    "incomplete_record": ("0", "record missing depth or magnitude"),
}


def _place_cell(r):
    """
    Where it happened, as a map pin.

    Shown for EVERY row including log. A log is still a real event at a real
    place -- the detector looked and was unimpressed -- and "where was that?"
    is the first question anyone asks of any row. The registry id it used to
    show (RGI2000-v7.0-G-14-03432) answers that for nobody.
    """
    lat, lon = r.get("lat"), r.get("lon")
    if lat is None or lon is None:
        return html.escape(str(r.get("nearest_site") or "")[:20])
    return ('<a href="https://www.google.com/maps?q=%.4f,%.4f" target="_blank" '
            'rel="noopener">%.2f N, %.2f E</a>' % (lat, lon, lat, lon))



def _why_tier(tier, score, factors, nearest_km=None):
    """One line per reason, for a tooltip."""
    try:
        fs = json.loads(factors) if isinstance(factors, str) else list(factors or [])
        if isinstance(fs, str):
            fs = [x.strip() for x in fs.strip("()[]").replace("'", "").split(",")]
        # Defensive: a double-encoded write once stored factors as a list of
        # single characters, and this rendered a tooltip one letter per line.
        # Single-character entries are never a factor name, so rejoin them.
        if fs and all(isinstance(x, str) and len(x) <= 1 for x in fs):
            fs = [t.strip().strip('"') for t in
                  "".join(fs).strip("[]").split(",")]
        fs = [x for x in fs if isinstance(x, str) and x.strip()]
    except Exception:
        fs = []

    th = {"watch": 45, "advisory": 57, "warning": 72}
    lines = ["scored %s — watch needs %d, advisory %d, warning %d"
             % (score, th["watch"], th["advisory"], th["warning"])]
    for f in fs:
        base = f.split("_0.")[0]
        if base.startswith("proximity"):
            lines.append("proximity confidence %s%s" % (
                f.split("_")[-1],
                (" — nearest mapped hazard %s km" % nearest_km)
                if nearest_km is not None else ""))
        elif base.startswith("capped_"):
            lines.append("CAPPED: %s" % base.replace("capped_", "").replace("_", " "))
        elif base in _WHY:
            pts, txt = _WHY[base]
            lines.append("%s  %s" % (pts, txt))
        else:
            lines.append(f)
    if tier == "watch":
        lines.append("")
        lines.append("A watch is visible to an operator and is never dispatched.")
    return "\n".join(lines)



def _blind_spots(s):
    """
    What this system cannot see, stated plainly and in one place.

    The page is now good at saying "here is what I saw". That quietly raises
    the risk of someone reading silence as safety -- and the honest facts
    about what is invisible are scattered across a 900-line CONSTRAINTS.md,
    one amber word in a data table, and the edge of a map.

    The project's discipline is that a status page asserts nothing it has not
    read. This is the mirror of that rule: say plainly what it has not looked
    at. Every line below is a measured number, not a caveat someone felt they
    ought to add.
    """
    b = None
    try:
        from .detect import DEFAULT_CONFIG
        b = DEFAULT_CONFIG["bbox"]
    except Exception:
        pass

    lake_age = None
    for d in (s.get("data_age") or []):
        if d["name"] == "glacial_lakes_hma" and d.get("epoch_days"):
            lake_age = d["epoch_days"] / 365.0

    items = []
    if b:
        items.append(
            "Anything outside <b>%.0f&ndash;%.0f&deg;E, %.0f&ndash;%.0f&deg;N</b>. "
            "Not watched is not the same as safe."
            % (b["min_lon"], b["max_lon"], b["min_lat"], b["max_lat"]))
    items += [
        "<b>Landslides that leave no catalogue record.</b> Rasuwagadhi 2025 "
        "produced no USGS event of any magnitude within 40 km, in the whole "
        "year. There was nothing to detect, at any threshold.",

        "<b>Collapses below about M4.0.</b> That is where USGS magnitude "
        "completeness stops in this box &mdash; not where the danger stops.",

        "<b>Collapses triggered by a large earthquake.</b> Replayed against "
        "the real Gorkha M7.8 window &mdash; 76 catalogued events within "
        "80 km &mdash; this produces <b>zero</b> detections. It is silent "
        "during the period of maximum landslide risk.",

        "<b>Anything early enough to warn.</b> For 26 August 2026 the usable "
        "record arrived <b>13 h 06 m after the slope failed</b>. That is a "
        "property of the feed, and no threshold changes it.",
    ]
    if lake_age:
        items.append(
            "<b>Glacial lakes formed in the last %.0f years.</b> The "
            "inventory is the 2015&ndash;2018 survey. Lakes form and grow as "
            "glaciers retreat, so the newest ones &mdash; the dangerous ones "
            "&mdash; are not on the map." % lake_age)
    items += [
        "<b>Rain and river level.</b> The seismic watcher reads neither. The "
        "rainfall track covers Kerala only, not this box.",
    ]

    return (
        '<h2>what this cannot see <span class=sub>the honest half</span></h2>'
        '<div class=note>' + "".join(
            '<div style="margin:7px 0">&middot;&nbsp; %s</div>' % i for i in items) +
        '<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">'
        '<b>This will not call anyone.</b> Nothing here is sent to any member '
        'of the public. Dispatch is off, and who gets told is a decision for a '
        'partner institution, not for software.</div></div>')



def _coverage_svg(W=880, H=330):
    """
    What area is being watched, and where the hazards in it are.

    Nothing on the page said this. A reader could not tell whether their
    valley was inside the box at all -- and a system that is not watching
    your area and a system that sees nothing there look identical from
    outside. That is the canary argument one level up.

    Hazard density by half-degree cell, past decisions as pins, the box drawn
    as the boundary it is. Built from the registry the detector actually
    loads, so it cannot claim coverage the detector does not have.
    """
    from .detect import DEFAULT_CONFIG
    b = DEFAULT_CONFIG["bbox"]
    s_, n_ = b["min_lat"], b["max_lat"]
    w_, e_ = b["min_lon"], b["max_lon"]
    pad = 26
    kx = math.cos(math.radians((s_ + n_) / 2))
    sx = (W - 2 * pad) / ((e_ - w_) * kx)
    sy = (H - 2 * pad) / (n_ - s_)
    sc = min(sx, sy)

    def px(lon): return pad + (lon - w_) * kx * sc
    def py(lat): return pad + (n_ - lat) * sc

    try:
        from .registry import load_registry
        reg = load_registry()
    except Exception:
        reg = []

    cells = {}
    for h in reg:
        k = (round(h["lat"] * 2) / 2, round(h["lon"] * 2) / 2)
        cells[k] = cells.get(k, 0) + 1
    peak = max(cells.values()) if cells else 1
    cw = 0.5 * kx * sc
    ch = 0.5 * sc
    dens = "".join(
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#4ea8a0" '
        'opacity="%.2f"/>' % (px(lo - 0.25), py(la + 0.25), cw, ch,
                              0.12 + 0.72 * (n / peak) ** 0.4)
        for (la, lo), n in cells.items())

    grid = ""
    for lo in range(int(w_) + 1, int(e_) + 1, 5):
        grid += ('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#2b2f3a" '
                 'stroke-width="1"/><text x="%.1f" y="%d" fill="var(--fg3)" '
                 'font-size="9" text-anchor="middle">%d E</text>'
                 % (px(lo), pad, px(lo), H - pad, px(lo), H - pad + 12, lo))
    for la in range(int(s_) + 2, int(n_) + 1, 4):
        grid += ('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#2b2f3a" '
                 'stroke-width="1"/><text x="%d" y="%.1f" fill="var(--fg3)" '
                 'font-size="9">%d N</text>'
                 % (pad, py(la), W - pad, py(la), 4, py(la) + 3, la))

    anchors = [("Nanga Parbat", 35.24, 74.59), ("K2", 35.88, 76.51),
               ("Kedarnath", 30.73, 79.07), ("Kathmandu", 27.72, 85.32),
               ("Everest", 27.99, 86.93), ("Gangtok", 27.33, 88.61),
               ("Thimphu", 27.47, 89.64)]
    pins = "".join(
        '<circle cx="%.1f" cy="%.1f" r="2.6" fill="#e8eaf0"/>'
        '<text x="%.1f" y="%.1f" fill="#e8eaf0" font-size="10">%s</text>'
        % (px(lo), py(la), px(lo) + 5, py(la) + 3, html.escape(nm))
        for nm, la, lo in anchors if w_ <= lo <= e_ and s_ <= la <= n_)

    return (
        '<svg viewBox="0 0 %d %d" width="100%%" role="img" '
        'aria-label="Area watched by the seismic detector">'
        '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
        'stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="5 4"/>'
        '%s%s%s</svg>'
        % (W, H, px(w_), py(n_), (e_ - w_) * kx * sc, (n_ - s_) * sc,
           grid, dens, pins))



def _last_detection_panel(ld):
    """The last real detection, in terms a person can act on."""
    if not ld:
        return ('<div class=note style="margin-top:10px"><b>LAST DETECTION</b>'
                ' &nbsp;<span class=sub>none on record in this database'
                '</span></div>')

    d_age = _age(ld["decided_at"])
    if d_age is None:      ago = "unknown"
    elif d_age < 3600:     ago = "%.0f min ago" % (d_age / 60)
    elif d_age < 86400:    ago = "%.1f h ago" % (d_age / 3600)
    else:                  ago = "%.1f days ago" % (d_age / 86400)

    lat, lon = ld.get("lat"), ld.get("lon")
    where = ""
    towns_html = ""
    if lat is not None and lon is not None:
        # The registry id -- RGI2000-v7.0-G-14-03432 -- is an inventory
        # number and tells a reader nothing. notify.py already refuses to put
        # these in alert text; the dashboard was still showing them raw.
        # Coordinates and a map pin answer "where", which is the question.
        gmap = "https://www.google.com/maps?q=%.4f,%.4f" % (lat, lon)
        osm = "https://www.openstreetmap.org/?mlat=%.4f&mlon=%.4f#map=11/%.4f/%.4f" % (
            lat, lon, lat, lon)
        where = (' &nbsp;·&nbsp; <a href="%s" target="_blank" rel="noopener">'
                 '%.3f N, %.3f E &#8599; map</a>'
                 ' <span class=sub>(<a href="%s" target="_blank" '
                 'rel="noopener">OSM</a>)</span>' % (gmap, lat, lon, osm))

        res = _corridor_for(lat, lon)
        if res and res[0]:
            branches, towns, total = res
            rows = "".join(
                "<tr><td>%s</td><td style='text-align:right'>%.1f km</td></tr>"
                % (html.escape(str(t.get("name") or "unnamed")[:32]),
                   t.get("river_km", 0.0))
                for t in towns)
            towns_html = (
                '<div style="margin-top:8px"><b>DOWNSTREAM</b> '
                '<span class=sub>%d settlements along %s</span>'
                '<div class=scroll><table><tr><th>place</th>'
                '<th style="text-align:right">along channel</th></tr>%s</table></div>'
                '<div class=sub>Traced along mapped channels from the source, '
                'union of every branch within 15 km of it. Not a flood model '
                'and not an evacuation list.</div></div>'
            ) % (total, html.escape(", ".join(branches[:3])[:60]), rows)

    return (
        '<div class=note style="margin-top:10px">'
        '<b>LAST DETECTION</b> &nbsp;<span class="t-{t}"><span class=wh tabindex=0>{T}<span class=wht>{why}</span></span></span> &nbsp;{ago}'
        '{where}'
        '<div class=sub>score {sc} &nbsp;·&nbsp; M{mag}, depth {dep} km '
        '&nbsp;·&nbsp; {when}</div>'
        '{towns}'
        '</div>'
    ).format(t=ld["tier"], T=ld["tier"].upper(), ago=ago, where=where,
             why=html.escape(_why_tier(ld["tier"], ld["score"],
                                       ld.get("factors"), ld.get("nearest_km"))),
             sc=ld["score"], mag=ld["magnitude"], dep=ld["depth_km"],
             when=_when(ld["decided_at"]), towns=towns_html)



def render(s):
    if s.get("error"):
        return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
                f"<div class=wrap><h1>hew watcher</h1>"
                f"<div class='state bad'><div class=big>NO DATA</div>"
                f"<div class=sub>{s['error']}</div></div></div>")

    age, cage = s.get("poll_age"), s.get("canary_age")

    # The headline is the HAZARD state; system health moves to the subtitle.
    # But a health fault takes the headline back, and that is not a style
    # choice: "nothing detected" asserted by a watcher that stopped polling
    # is a claim the system has not earned. Absence of events is not health
    # -- the same reason the canary exists. Quiet is only meaningful from a
    # system that is demonstrably looking.
    rt = s.get("recent_tiers") or {}
    n_warn = rt.get("warning", 0) + rt.get("advisory", 0)
    n_watch = rt.get("watch", 0)

    stale = age is None or age > STALE_SECONDS
    blind = ("  ·  WATCHER STALE, nothing seen since"
             if stale else "")

    # A hazard that has already been decided outranks a stale poller. Showing
    # NOT LOOKING over a real WATCH hides the thing the operator most needs;
    # the staleness is still said, in the subtitle. The reverse case is the
    # one that must never happen: "nothing detected" from a watcher that
    # stopped, which is a claim about the ground made by something that is
    # not looking at it.
    if n_warn:
        cls, hdr, why = ("bad", "WARNING",
                         f"{n_warn} dispatch-tier decision(s) in 24 h" + blind
                         + (f"  ·  system healthy, last poll {age:.0f}s ago"
                            if not stale else ""))
    elif n_watch:
        cls, hdr, why = ("warn", "WATCH",
                         f"{n_watch} watch-tier decision(s) in 24 h" + blind
                         + (f"  ·  system healthy, last poll {age:.0f}s ago"
                            if not stale else ""))
    elif age is None:
        cls, hdr, why = "bad", "NOT LOOKING", "no successful poll on record — this is not 'quiet'"
    elif age > STALE_SECONDS:
        cls, hdr, why = ("bad", "NOT LOOKING",
                         f"watcher stale, last good poll {age/60:.0f} min ago — "
                         f"cannot speak for the last {age/60:.0f} min")
    elif cage is not None and cage > CANARY_STALE_SECONDS:
        cls, hdr, why = ("warn", "CANARY STALE",
                         f"polling, but the canary last passed {cage/3600:.1f} h ago")
    else:
        cls, hdr, why = ("ok", "NOTHING IN LAST 24 H",
                         f"system healthy · last poll {age:.0f}s ago · "
                         f"canary {('%.0f min' % (cage/60)) if cage is not None else 'n/a'} ago")

    last_line = _last_detection_panel(s.get("last_detection"))
    try:
        covmap = _coverage_svg()
        nsites = "{:,}".format(len(__import__("hew.registry", fromlist=["x"])
                                   .load_registry()))
    except Exception:
        covmap, nsites = "", "the"
    blind = _blind_spots(s)

    g = gaps(s.get("beats", []))
    beats = [b for b in s.get("beats", []) if b["source"] == "usgs_catalogue"]
    bar = "".join(
        f"<div style='background:{'var(--ok)' if b['ok'] else 'var(--bad)'}'></div>"
        for b in beats[-240:]) or "<div style='background:var(--line)'></div>"

    seen_rows = "".join(
        f"<tr><td>{_when(r['observed_at'])}</td>"
        f"<td class='t-{r['tier']}'><span class=wh tabindex=0>"
        f"{r['tier'].upper()}"
        f"<span class=wht>{html.escape(_why_tier(r['tier'], r['score'], r.get('factors'), r.get('nearest_km')))}</span>"
        f"</span></td>"
        f"<td>{r['score']}</td>"
        f"<td>M{r['magnitude']}</td><td>{r['depth_km']} km</td>"
        f"<td>{_place_cell(r)}</td>"
        f"<td>{r['nearest_km'] if r['nearest_km'] is not None else ''}</td>"
        f"<td class=sub>{(r['suppress_reason'] or r['factors'] or '')[:44]}</td></tr>"
        for r in s.get("seen", [])) or \
        "<tr><td colspan=8 class=sub>nothing evaluated yet in this database"\
        " — the backtested rate is ~1.2 dispatch-tier events a year, so an"\
        " empty table is the expected state, not a fault. The canary above"\
        " is what proves the path still works.</td></tr>"

    age_rows = "".join(
        f"<tr><td>{d['name']}</td>"
        f"<td>{d['epoch'] or ''}</td>"
        f"<td>{('%d days' % d['days']) if d['days'] is not None else '—'}</td>"
        f"<td class='{'t-warning' if d['stale'] else 'sub'}'>"
        f"{'STALE' if d['stale'] else 'ok'}</td></tr>"
        for d in s.get("data_age", [])) or \
        "<tr><td colspan=4 class=sub>no manifest</td></tr>"

    rej_rows = "".join(
        f"<tr><td>{(x['reason'] or '')[:60]}</td><td>{x['n']}</td></tr>"
        for x in s.get("reject_reasons", [])) or \
        "<tr><td colspan=2 class=sub>none yet</td></tr>"

    grows = "".join(
        f"<tr><td>{_when(x['from'])}</td>"
        f"<td>{_when(x['to'])}</td>"
        f"<td>{x['seconds']/60:.0f} min</td></tr>" for x in g[:12]) or \
        "<tr><td colspan=3 class=sub>none in the last 7 days</td></tr>"

    # Poll-cycle indicator, positioned from the REAL poll age.
    if age is None or age > STALE_SECONDS:
        cyc = ('<div class=cyc><div class=cyclab><span>poll cycle</span>'
               '<span>no poll — watcher may be down</span></div>'
               '<div class=cycbar><div class="cycfill stale"></div></div></div>')
    elif age > POLL_SECONDS * 1.5:
        cyc = (f'<div class=cyc><div class=cyclab><span>poll cycle</span>'
               f'<span>overdue by {age - POLL_SECONDS:.0f}s</span></div>'
               f'<div class=cycbar><div class="cycfill overdue"></div></div></div>')
    else:
        into = age % POLL_SECONDS
        pct = 100 * into / POLL_SECONDS
        cyc = (f'<div class=cyc><div class=cyclab><span>poll cycle</span>'
               f'<span>next in ~{POLL_SECONDS - into:.0f}s</span></div>'
               f'<div class=cycbar><div class=cycfill style="'
               f'--cycle:{POLL_SECONDS}s;--offset:-{into:.1f}s;--pct:{pct:.0f}%"'
               f'></div></div></div>')

    t = s.get("tiers", {})
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>hew watcher</title><meta http-equiv=refresh content=30>
<style>{CSS}</style>
<div class=wrap>
  <h1>Himalayan Early Warning — Phase 1 watcher</h1>
  <div class=sub>read-only status · refreshes every 30 s · dispatch is OFF ·
    <a href="/simulate">run a drill →</a> ·
    <a href="/rain">rainfall track →</a></div>

  <div class="state {cls}">
    <div class=big>{hdr}</div>
    <div class=sub>{why}</div>
  </div>
  {last_line}

  {cyc}

  <div class=grid>
    <div class=card><div class=k>last poll</div><div class=v>
      {f'{age:.0f}s ago' if age is not None else '—'}</div></div>
    <div class=card><div class=k>last canary pass</div><div class=v>
      {f'{cage/60:.0f}m ago' if cage is not None else '—'}</div></div>
    <div class=card><div class=k>candidates seen</div><div class=v>{s['candidates']}</div></div>
    <div class=card><div class=k>decisions logged</div><div class=v>{s['decisions']}</div></div>
    <div class=card><div class=k>corridor rows</div><div class=v>{s['corridor_rows']}</div></div>
    <div class=card><div class=k>config</div><div class=v style=font-size:14px>
      {s.get('config_version') or '—'}</div></div>
  </div>

  <h2>poll record — last 7 days</h2>
  <div class=bar>{bar}</div>
  <div class=note>each stripe is one heartbeat; red is a failed poll.
  Absence of events is not health — that is what the canary is for.</div>

  <h2>gaps &gt; 5 min <span class=sub>({len(g)} in 7 days)</span></h2>
  <div class=scroll><table>
    <tr><th>from</th><th>to</th><th>gap</th></tr>{grows}
  </table></div>
  <div class=note>Every gap must be explained before Phase 1 can be signed off.
  On a Pi, check <code>vcgencmd get_throttled</code> and
  <code>systemctl show hew-watcher -p NRestarts</code> before suspecting the software.</div>

  <h2>tiers <span class=sub>all time</span></h2>
  <div class=sub>{' · '.join(f'{k}={v}' for k, v in sorted(t.items())) or 'none yet'}</div>

  <h2>area watched <span class=sub>what is inside the box, and what is not</span></h2>
  <div class=note>Everything outside this boundary is <b>not looked at</b>.
  Shading is mapped-hazard density — {nsites} glaciers and lakes. A valley
  outside the box, or inside it with no shading, produces no decision, and
  that is not the same as it being safe.</div>
  {covmap}

  {blind}

  <h2>data age <span class=sub>static layers</span></h2>
  <div class=note>Glacial lakes <b>form and grow</b> as glaciers retreat, so a
  stale inventory is a growing set of unmapped hazards — the same silent
  false negative that hid Kedarnath and Chamoli until the box was widened.
  Note the source epochs: RGI glacier outlines are nominally <b>year 2000</b>
  and the HMA lake inventory is the <b>2015–2018</b> epoch, both older than
  the build date beside them.</div>
  <div class=scroll><table>
    <tr><th>layer</th><th>source epoch</th><th>built/fetched</th><th></th></tr>{age_rows}
  </table></div>

  <h2>what the detector saw <span class=sub>every evaluation, rejects included</span></h2>
  <div class=note><b>This is not a risk forecast.</b> A tier is a Phase 1
  measurement category, not a public advisory, and the feed cannot support one.
  For the 26 August 2026 event the characterised record arrived
  <b>13 h 06 m after the collapse</b> (D5). At 08:37, while it still mattered,
  the feed carried M4.4 / <code>type=earthquake</code> / <b>10 km depth</b> —
  and 10 km is not a measurement, it is one of four catalogue defaults meaning
  <b>depth unconstrained</b>. That record scores <b>WATCH</b>, not warning:
  visible to an operator, never dispatched. Depth is the whole discriminator
  between a surface collapse and an ordinary earthquake, 40.6% of catalogue
  events carry no real depth, and dispatching on those would mean broadcasting
  on a large share of every small quake in the range. The filter is right; the
  feed is thirteen hours late. A quiet table here means the catalogue was
  quiet, which is not the same as the ground being quiet.</div>
  <div class=scroll><table>
    <tr><th>observed</th><th>tier</th><th>score</th><th>mag</th><th>depth</th>
        <th>nearest hazard</th><th>km</th><th>why</th></tr>{seen_rows}
  </table></div>

  <h2>rejects by reason <span class=sub>all time</span></h2>
  <div class=scroll><table>
    <tr><th>reason</th><th>n</th></tr>{rej_rows}
  </table></div>

</div>"""


# -- drill view -------------------------------------------------------------
# GET only, and it writes NOTHING. Simulated events must never enter the
# decision ledger: that log exists so "why did it not fire" is answerable
# from the record alone, and drill rows would also land in the Phase 0
# false-alarm count. Everything below is computed in memory and discarded.

_NET = None


def _map_svg(branches, corridor, src_lat, src_lon, W=760, H=520):
    """
    Corridor map. Branches drawn in flow order, settlements coloured by
    distance band, the unmodelled overland reach from source to channel
    drawn dashed so it is never mistaken for routed water.
    """
    import math
    pts = [(v["lat"], v["lon"]) for b in branches for v in b["path"]]
    pts.append((src_lat, src_lon))
    if len(pts) < 2:
        return ""
    mila, mala = min(p[0] for p in pts), max(p[0] for p in pts)
    milo, malo = min(p[1] for p in pts), max(p[1] for p in pts)
    pad = max(0.02, (mala - mila) * 0.08)
    mila -= pad; mala += pad; milo -= pad; malo += pad
    kx = math.cos(math.radians((mila + mala) / 2))
    sc = min(W / max(1e-9, (malo - milo) * kx), H / max(1e-9, mala - mila))
    ox = (W - (malo - milo) * kx * sc) / 2
    oy = (H - (mala - mila) * sc) / 2

    def prj(la, lo):
        return (round(ox + (lo - milo) * kx * sc, 1),
                round(oy + (mala - la) * sc, 1))

    def band(km):
        return "b1" if km < 25 else "b2" if km < 60 else "b3"

    out = []
    for bi, b in enumerate(branches):
        step = max(1, len(b["path"]) // 300)
        for cls in ("b1", "b2", "b3"):
            seg = [prj(v["lat"], v["lon"]) for v in b["path"][::step]
                   if band(v["river_km"]) == cls]
            if len(seg) > 1:
                dash = ' stroke-dasharray="1 7"' if bi else ""
                out.append(f'<polyline class="rv {cls}" points="'
                           + " ".join(f"{x},{y}" for x, y in seg) + f'"{dash}/>')
    # overland reach: source -> each branch start
    sx, sy = prj(src_lat, src_lon)
    for b in branches:
        bx, by = prj(b["path"][0]["lat"], b["path"][0]["lon"])
        out.append(f'<line class="ovl" x1="{sx}" y1="{sy}" x2="{bx}" y2="{by}"/>')
    # settlements
    named = {"Rasuwa Gadhi", "Mailung", "Betrawati", "Bidur", "Devighat",
             "Galchhi", "Gajuri", "Shyaphru Bensi", "Dhunche"}
    for c in corridor:
        x, y = prj(c["lat"], c["lon"])
        big = c["name"] in named
        out.append(f'<circle class="pn {band(c["river_km"])}" cx="{x}" cy="{y}" '
                   f'r="{5 if big else 2.5}"/>')
        if big:
            an = "end" if x > W * 0.6 else "start"
            dx = -9 if an == "end" else 9
            out.append(f'<text class="pl" x="{x+dx}" y="{y+4}" text-anchor="{an}">'
                       f'{c["name"]}<tspan class="pk"> {c["river_km"]:.0f}km</tspan></text>')
    out.append(f'<circle class="srcm" cx="{sx}" cy="{sy}" r="9"/>'
               f'<circle class="srcm" cx="{sx}" cy="{sy}" r="3"/>'
               f'<text class="srct" x="{sx}" y="{sy-14}" text-anchor="middle">source</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="map" role="img" '
            f'aria-label="corridor map">' + "".join(out) + "</svg>")


def _drill(qs):
    from hew.detect import evaluate, DEFAULT_CONFIG
    from hew.registry import load_registry
    from hew.notify import Dispatcher
    import importlib
    sim = importlib.import_module("scripts.simulate") if False else None

    from hew.drill import SCENARIOS, random_event
    SC = {k: (v[0], v[1], v[2], v[3], v[4]) for k, v in SCENARIOS.items()}

    q = urllib.parse.parse_qs(qs)
    key = (q.get("s") or [None])[0]
    label = lat = lon = dep = mag = None
    note = seed = None
    if key == "random":
        import os as _os
        from hew.registry import load_registry as _lr
        from hew.routing import load_settlements as _ls, DATA_DIR as _DD
        wide = _os.path.join(_DD, "places_region.json")
        pl = _ls(wide if _os.path.exists(wide) else None)
        s_ = (q.get("seed") or [None])[0]
        label, lat, lon, dep, mag, note, seed = random_event(
            _lr(), pl, seed=int(s_) if s_ and s_.isdigit() else None)
    elif key in SC:
        label, lat, lon, dep, mag = SC[key]
        note = SCENARIOS[key][5]
    elif all(k in q for k in ("lat", "lon", "depth", "mag")):
        try:
            lat, lon = float(q["lat"][0]), float(q["lon"][0])
            dep, mag = float(q["depth"][0]), float(q["mag"][0])
            label = "custom"
        except ValueError:
            label = None

    links = ('<a class=sc href="/simulate?s=random" '
             'style="border-color:var(--accent)"><b>random drill</b>'
             '<span>synthetic event somewhere inhabited — new one each time</span>'
             '</a>')
    links += "".join(
        f'<a class=sc href="/simulate?s={k}"><b>{k}</b><span>{v[0]}</span></a>'
        for k, v in SC.items())

    body = ""
    if label:
        R = load_registry()
        r = evaluate(lat, lon, dep, mag, R)
        disp = r["tier"] in ("advisory", "warning")
        corridor, alert = [], None
        if disp:
            global _NET
            from hew.routing import (RiverNetwork, load_settlements,
                                     trace_branches, exposed_settlements_union)
            if _NET is None:
                _NET = (RiverNetwork.load(), load_settlements())
            net, places = _NET
            br = trace_branches(net, lat, lon,
                                uncertainty_km=DEFAULT_CONFIG.get("source_uncertainty_km", 0))
            corridor = exposed_settlements_union(br, places, corridor_km=2.0)
            alert = Dispatcher().render(r["tier"], r, corridor)
            try:                       # push the drill result to the operator
                from hew import hew_operator
                if hew_operator.configured():
                    picks = [c["name"] for c in corridor[:6]]
                    hew_operator.drill(r["tier"], ", ".join(r["factors"]),
                                       ", ".join(picks),
                                       max(0, len(corridor) - len(picks)))
            except Exception:
                pass                   # a drill must never break the page
        from hew.routing import population_summary
        pops = population_summary(corridor) if corridor else None
        rows = "".join(
            f"<tr><td>{c['river_km']:.1f} km</td><td>{c['name']}</td>"
            f"<td class=sub>{c.get('kind') or ''}</td>"
            f"<td class=sub>{('{:,}'.format(c['population'])) if c.get('population') else '—'}</td>"
            f"<td class=sub>{c['channel']}</td></tr>" for c in corridor[:14])
        svg = _map_svg(br, corridor, lat, lon) if corridor else ""
        body = f"""
      <div class="state {'bad' if r['tier']=='warning' else 'warn' if disp else 'ok'}">
        <div class=big>{r['tier'].upper()}</div>
        <div class=sub>{label} &middot; M{mag} &middot; depth {dep} km &middot;
          {lat}, {lon} &middot; score {r['score']}</div>
        {'<div class=sub style=margin-top:6px>' + note + '</div>' if note else ''}
        {'<div class=sub style=margin-top:4px><a href="/simulate?s=random">roll again</a> &middot; <a href="/simulate?s=random&amp;seed=' + str(seed) + '">permalink to this one</a></div>' if seed else ''}
      </div>
      <div class=grid>
        <div class=card><div class=k>factors</div><div class=v style=font-size:13px>
          {', '.join(r['factors'])}</div></div>
        <div class=card><div class=k>nearest mapped {r.get('nearest_kind') or 'hazard'}</div><div class=v>
          {r['nearest_km'] if r['nearest_km'] is not None else '—'} km</div></div>
        <div class=card><div class=k>proximity confidence</div><div class=v>
          {r['proximity_confidence'] if r['proximity_confidence'] is not None else '—'}</div></div>
        <div class=card><div class=k>would dispatch</div><div class=v>
          {'YES' if disp else 'no'}</div></div>
      </div>
      {'<h2>corridor map</h2><div class=mapwrap>' + svg + '</div><div class=note>'
       'red 0-25 km &middot; amber 25-60 km &middot; blue 60 km+ &middot; '
       'dotted = second candidate branch &middot; dashed = overland reach from '
       'the source to the channel, which is NOT modelled</div>' if svg else ''}
      {'<h2>corridor — ' + str(len(corridor)) + ' settlements</h2>'
       '<div class=grid style=margin-bottom:12px>'
       '<div class=card><div class=k>recorded population</div><div class=v>'
       + '{:,}'.format(pops["recorded"]) + '</div><div class=sub>across '
       + str(pops["recorded_places"]) + ' places</div></div>'
       '<div class=card><div class=k>no figure recorded</div><div class=v>'
       + str(pops["unknown_places"]) + '</div><div class=sub>places</div></div>'
       '<div class=card><div class=k>rough band</div><div class=v style=font-size:15px>'
       + '{:,}'.format(pops["estimated_low"]) + '–' + '{:,}'.format(pops["estimated_high"])
       + '</div><div class=sub>order of magnitude only</div></div>'
       '</div><div class=note>' + pops["caveat"] + '</div>'
       '<div class=scroll><table><tr><th>river km</th><th>settlement</th>'
       '<th>class</th><th>population</th><th>channel</th></tr>' + rows + '</table></div>'
       if corridor else ''}
      {'<h2>alert text</h2><div class=card style=line-height:1.6>' + alert + '</div>' if alert else ''}
      <div class=note>Computed in memory. <b>Nothing was written</b> — not to the
      decision ledger, not to the heartbeat table, not to the Phase 0 counts.</div>"""

    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>hew drill</title><style>{CSS}
.sc{{display:block;background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:11px 14px;margin-bottom:8px;text-decoration:none;color:var(--fg)}}
.sc:hover{{border-color:var(--accent)}}
.sc b{{display:block;font-size:13px}} .sc span{{color:var(--fg3);font-size:12px}}
.mapwrap{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:6px;overflow-x:auto}}
svg.map{{display:block;width:100%;height:auto;min-width:460px}}
.rv{{fill:none;stroke-width:3.5;stroke-linecap:round;stroke-linejoin:round}}
.rv.b1{{stroke:var(--bad)}} .rv.b2{{stroke:var(--warn)}} .rv.b3{{stroke:var(--accent)}}
.pn{{stroke:var(--card);stroke-width:1.2}}
.pn.b1{{fill:var(--bad)}} .pn.b2{{fill:var(--warn)}} .pn.b3{{fill:var(--accent)}}
.pl{{font-size:11px;font-weight:700;fill:var(--fg)}}
.pk{{font-weight:400;fill:var(--fg3);font-size:10px}}
.srcm{{fill:none;stroke:var(--bad);stroke-width:2}}
.srct{{font-size:11px;font-weight:700;fill:var(--bad)}}
.ovl{{stroke:var(--fg3);stroke-width:1.4;stroke-dasharray:4 4}}
.drill{{background:#3a2a12;border:2px solid var(--warn);border-radius:8px;
padding:12px 16px;margin-bottom:18px}}
</style>
<div class=wrap>
  <div class=drill><b style=color:var(--warn)>DRILL MODE</b>
    <div class=sub>Simulated events. Nothing here is real, nothing is dispatched,
    and nothing is recorded. <a href="/">← live status</a></div></div>
  <h1>Simulate an event</h1>
  <div class=note style="margin-bottom:14px"><b>See the dashboard, not just
  the corridor.</b> Append <code>/dashboard</code> to any scenario link below,
  or <a href="/simulate/dashboard?s=rasuwagadhi_border">open one now</a>, to
  render the LIVE status page exactly as it would appear during that event --
  same banner, same panel, same tooltips, because it is the same code path.
  A drill that does not look like the alarm cannot rehearse the alarm.</div>

  <h2>scenarios</h2>
  {links}
  <h2>custom</h2>
  <div class=sub>/simulate?lat=28.3&amp;lon=85.4&amp;depth=1.5&amp;mag=5.0</div>
  {body}
</div>"""


def _drill_dashboard(query):
    """
    The LIVE dashboard as it would look during this event.

    The drill page showed a map and a corridor; the dashboard showed a banner,
    a last-detection panel and a table. So a drill proved the DETECTOR works
    and proved nothing about what an operator would actually be looking at.
    You cannot rehearse an alarm you have never seen.

    This renders the real render() -- same banner precedence, same panel, same
    tooltips -- over a snapshot with the simulated decision spliced in. What
    you see here is what the page will do, because it is the same code path.

    WRITES NOTHING. The snapshot is built in memory and discarded, exactly as
    /simulate does: simulated events must never enter the decision ledger.
    """
    from .detect import evaluate, DEFAULT_CONFIG
    from .registry import load_registry
    from . import drill as _drill

    q = urllib.parse.parse_qs(query)
    scen = (q.get("s") or [None])[0]
    if scen and scen in _drill.SCENARIOS:
        _lab, lat, lon, depth, mag = _drill.SCENARIOS[scen][:5]
    else:
        try:
            lat = float((q.get("lat") or ["28.271"])[0])
            lon = float((q.get("lon") or ["85.515"])[0])
            depth = float((q.get("depth") or ["1.5"])[0])
            mag = float((q.get("mag") or ["5.2"])[0])
        except ValueError:
            lat, lon, depth, mag = 28.271, 85.515, 1.5, 5.2

    r = evaluate(lat, lon, depth, mag, load_registry(), DEFAULT_CONFIG)
    now = datetime.now(timezone.utc).isoformat()

    snap = snapshot(Handler.db)
    snap["recent_tiers"] = dict(snap.get("recent_tiers") or {})
    snap["recent_tiers"][r["tier"]] = snap["recent_tiers"].get(r["tier"], 0) + 1
    snap["last_detection"] = {
        "tier": r["tier"], "score": r["score"], "decided_at": now,
        "nearest_site": r["nearest_site"], "nearest_km": r["nearest_km"],
        "magnitude": mag, "depth_km": depth, "lat": lat, "lon": lon,
        "external_id": "DRILL", "factors": json.dumps(list(r["factors"])),
    }
    snap["seen"] = [{
        "external_id": "DRILL", "observed_at": now, "magnitude": mag,
        "depth_km": depth, "score": r["score"], "tier": r["tier"],
        "factors": json.dumps(list(r["factors"])),
        "nearest_site": r["nearest_site"], "nearest_km": r["nearest_km"],
        "suppressed": 1, "suppress_reason": "drill — nothing was dispatched",
        "decided_at": now,
    }] + list(snap.get("seen") or [])

    page = render(snap)
    banner = (
        '<div style="position:sticky;top:0;z-index:99;background:#3a2a12;'
        'border-bottom:2px solid #d0a24c;padding:10px 16px;font:13px ui-monospace,'
        'monospace;color:#f0d9a8">'
        '<b>DRILL — this is what the dashboard would look like.</b> '
        'Simulated M%.1f at %.3f, %.3f, depth %.1f km. '
        'Nothing was dispatched, nothing was written to the ledger, no '
        'notification was sent. <a href="/" style="color:#f0d9a8">live status &rarr;</a> '
        '&nbsp;·&nbsp; <a href="/simulate" style="color:#f0d9a8">other scenarios &rarr;</a>'
        '</div>' % (mag, lat, lon, depth))
    return page.replace("<div class=wrap>", banner + "<div class=wrap>", 1)



class Handler(BaseHTTPRequestHandler):
    db = "hew.db"
    # A browser opens speculative connections and sends nothing on them. On a
    # single-threaded HTTPServer the handler then blocks in readline() forever
    # and the WHOLE server stops answering -- /health included, which is the
    # one endpoint that must never go quiet. Observed here: one Chrome
    # preconnect socket wedged every request for 30 s at a time.
    # Threaded server so one stalled client cannot starve the rest, and a
    # read timeout so a silent socket is dropped instead of held.
    timeout = 10

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/health", "/healthz"):
            s = snapshot(self.db)
            age = s.get("poll_age")
            ok = age is not None and age <= STALE_SECONDS
            body = json.dumps({"ok": ok, "poll_age_seconds": age,
                               "decisions": s.get("decisions")}).encode()
            self._send(200 if ok else 503, body, "application/json")
        elif path == "/status.json":
            self._send(200, json.dumps(snapshot(self.db), default=str).encode(),
                       "application/json")
        elif path == "/simulate/dashboard":
            self._send(200, _drill_dashboard(
                urllib.parse.urlparse(self.path).query).encode(), "text/html")
        elif path == "/simulate":
            self._send(200, _drill(urllib.parse.urlparse(self.path).query).encode(),
                       "text/html")
        elif path == "/rain":
            from . import rain_page
            self._send(200, rain_page.render(CSS).encode(), "text/html")
        elif path == "/":
            self._send(200, render(snapshot(self.db)).encode(), "text/html")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass          # do not spam the journal the watcher shares


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="hew.db")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    Handler.db = a.db
    print(f"status page on http://{a.host}:{a.port}/  (read-only, db={a.db})")
    print("no authentication — LAN only, do not port-forward")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
