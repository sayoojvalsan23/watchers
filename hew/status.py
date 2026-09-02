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
import json
import os
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


def render(s):
    if s.get("error"):
        return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
                f"<div class=wrap><h1>hew watcher</h1>"
                f"<div class='state bad'><div class=big>NO DATA</div>"
                f"<div class=sub>{s['error']}</div></div></div>")

    age, cage = s.get("poll_age"), s.get("canary_age")
    if age is None:
        cls, hdr, why = "bad", "NEVER POLLED", "no successful poll on record"
    elif age > STALE_SECONDS:
        cls, hdr, why = "bad", "STALE", f"last good poll {age/60:.0f} min ago — this is a page"
    elif cage is not None and cage > CANARY_STALE_SECONDS:
        cls, hdr, why = "warn", "CANARY STALE", f"canary last passed {cage/3600:.1f} h ago"
    else:
        cls, hdr, why = "ok", "HEALTHY", f"last poll {age:.0f}s ago"

    g = gaps(s.get("beats", []))
    beats = [b for b in s.get("beats", []) if b["source"] == "usgs_catalogue"]
    bar = "".join(
        f"<div style='background:{'var(--ok)' if b['ok'] else 'var(--bad)'}'></div>"
        for b in beats[-240:]) or "<div style='background:var(--line)'></div>"

    rows = "".join(
        f"<tr><td>{r['decided_at'][:19].replace('T',' ')}</td>"
        f"<td class='t-{r['tier']}'>{r['tier'].upper()}</td>"
        f"<td>{r['score']}</td>"
        f"<td>M{r['magnitude']}</td><td>{r['depth_km']} km</td>"
        f"<td>{(r['nearest_site'] or '')[:22]}</td>"
        f"<td>{r['nearest_km'] if r['nearest_km'] is not None else ''}</td>"
        f"<td>{'yes — ' + (r['suppress_reason'] or '') if r['suppressed'] else ''}</td></tr>"
        for r in s.get("recent", [])) or \
        "<tr><td colspan=8 class=sub>no non-reject decisions yet — expected; "\
        "six of twelve backtested years had none</td></tr>"

    grows = "".join(
        f"<tr><td>{x['from'][:19].replace('T',' ')}</td>"
        f"<td>{x['to'][:19].replace('T',' ')}</td>"
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

  <h2>recent decisions <span class=sub>rejects hidden</span></h2>
  <div class=scroll><table>
    <tr><th>decided</th><th>tier</th><th>score</th><th>mag</th><th>depth</th>
        <th>nearest hazard</th><th>km</th><th>suppressed</th></tr>{rows}
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
  <h2>scenarios</h2>
  {links}
  <h2>custom</h2>
  <div class=sub>/simulate?lat=28.3&amp;lon=85.4&amp;depth=1.5&amp;mag=5.0</div>
  {body}
</div>"""


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
