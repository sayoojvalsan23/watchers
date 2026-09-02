"""
Storage layer.

Phase 1 uses SQLite: no spatial queries are needed (the hazard registry is a
few hundred rows held in memory), and a single file is the most portable thing
to hand to a partner institution.

The schema mirrors the PostGIS design in the design doc. Swap to Postgres in
V2 when the river graph arrives and real spatial queries begin.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    version     TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    active      INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id  TEXT UNIQUE NOT NULL,      -- idempotency key
    observed_at  TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    depth_km     REAL,
    magnitude    REAL,
    source       TEXT NOT NULL,
    raw          TEXT
);

-- Append-only. Never updated, never deleted. Includes the negatives.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER NOT NULL REFERENCES candidates(id),
    config_version  TEXT NOT NULL,
    score           INTEGER NOT NULL,
    tier            TEXT NOT NULL,
    factors         TEXT NOT NULL,
    nearest_site    TEXT,
    nearest_km      REAL,
    suppressed      INTEGER DEFAULT 0,
    suppress_reason TEXT,
    decided_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   INTEGER NOT NULL REFERENCES decisions(id),
    reach_id      TEXT,
    channel       TEXT NOT NULL,
    dispatched_at TEXT,
    delivered     INTEGER DEFAULT 0,
    error         TEXT
);

-- Liveness. Absence of events is NOT evidence of health.
CREATE TABLE IF NOT EXISTS heartbeats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    detail      TEXT,
    at          TEXT NOT NULL
);

-- Downstream corridor for a decision. Append-only, like decisions.
-- Written AFTER the decision is recorded; routing never gates the trigger.
CREATE TABLE IF NOT EXISTS impact (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id  INTEGER NOT NULL REFERENCES decisions(id),
    settlement   TEXT NOT NULL,
    kind         TEXT,
    population   INTEGER,
    river_km     REAL NOT NULL,
    offset_km    REAL,
    channel      TEXT,
    snap_km      REAL,
    data_version TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cand_ext  ON candidates(external_id);
CREATE INDEX IF NOT EXISTS idx_imp_dec   ON impact(decision_id);
CREATE INDEX IF NOT EXISTS idx_dec_time  ON decisions(decided_at);
CREATE INDEX IF NOT EXISTS idx_dec_tier  ON decisions(tier);
CREATE INDEX IF NOT EXISTS idx_hb_time   ON heartbeats(at);
"""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path="hew.db"):
        self.path = path
        self._init()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init(self):
        with self.conn() as c:
            # WAL so a write never blocks the read-only status page. Under the
            # default rollback journal a writer takes an EXCLUSIVE lock, and
            # /health went dark for exactly as long as the watcher was healthy
            # and writing -- a live system and a dead one looked identical from
            # outside. Fail-safe means the liveness endpoint must answer.
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    # -- config ------------------------------------------------------------

    def put_config(self, version, payload, created_by="system", activate=True):
        with self.conn() as c:
            if activate:
                c.execute("UPDATE config SET active = 0")
            c.execute(
                "INSERT OR REPLACE INTO config"
                " (version, payload, active, created_at, created_by)"
                " VALUES (?,?,?,?,?)",
                (version, json.dumps(payload), 1 if activate else 0,
                 utcnow(), created_by))

    def active_config(self):
        with self.conn() as c:
            r = c.execute(
                "SELECT version, payload FROM config WHERE active = 1"
            ).fetchone()
            return (r["version"], json.loads(r["payload"])) if r else (None, None)

    # -- candidates --------------------------------------------------------

    def upsert_candidate(self, ext_id, observed_at, lat, lon,
                         depth_km, magnitude, source, raw):
        """
        Idempotent on external_id. Revisions UPDATE the row and never create
        a second candidate. Returns (candidate_id, is_new, changed).

        `changed` is the D2 fix. The old contract was "revisions never re-fire",
        which the watcher implemented as "revisions are never even looked at" --
        so an event first published at the 10 km catalogue default (capped at
        watch) and later revised to a measured shallow depth could never
        escalate. Eighty percent of events arrive at a default depth, and the
        26 August event was revised exactly this way, so this was the single
        most likely path to missing a real one.

        Position is included: a revised location moves the corridor, which is
        the whole downstream product.
        """
        with self.conn() as c:
            r = c.execute(
                "SELECT id, lat, lon, depth_km, magnitude FROM candidates"
                " WHERE external_id = ?", (ext_id,)).fetchone()
            if r:
                def moved(a, b, tol):
                    if a is None or b is None:
                        return a is not b
                    return abs(a - b) > tol
                changed = (moved(r["depth_km"], depth_km, 0.01)
                           or moved(r["magnitude"], magnitude, 0.01)
                           or moved(r["lat"], lat, 0.001)
                           or moved(r["lon"], lon, 0.001))
                c.execute(
                    "UPDATE candidates SET lat=?, lon=?, depth_km=?,"
                    " magnitude=?, raw=? WHERE id=?",
                    (lat, lon, depth_km, magnitude, json.dumps(raw), r["id"]))
                return r["id"], False, changed
            cur = c.execute(
                "INSERT INTO candidates (external_id, observed_at, ingested_at,"
                " lat, lon, depth_km, magnitude, source, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ext_id, observed_at, utcnow(), lat, lon,
                 depth_km, magnitude, source, json.dumps(raw)))
            return cur.lastrowid, True, True

    # -- decisions ---------------------------------------------------------

    def record_decision(self, candidate_id, config_version, score, tier,
                        factors, nearest_site, nearest_km,
                        suppressed=False, suppress_reason=None):
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO decisions (candidate_id, config_version, score,"
                " tier, factors, nearest_site, nearest_km, suppressed,"
                " suppress_reason, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (candidate_id, config_version, score, tier,
                 json.dumps(list(factors)), nearest_site, nearest_km,
                 1 if suppressed else 0, suppress_reason, utcnow()))
            return cur.lastrowid

    def recent_dispatched(self, tiers, since_iso):
        """Count of non-suppressed decisions at given tiers — circuit breaker input."""
        q = ("SELECT COUNT(*) n FROM decisions WHERE suppressed = 0"
             " AND decided_at > ? AND tier IN (%s)"
             % ",".join("?" * len(tiers)))
        with self.conn() as c:
            return c.execute(q, (since_iso, *tiers)).fetchone()["n"]

    # -- alerts ------------------------------------------------------------

    def record_alert(self, decision_id, channel, reach_id=None,
                     delivered=0, error=None):
        with self.conn() as c:
            c.execute(
                "INSERT INTO alerts (decision_id, reach_id, channel,"
                " dispatched_at, delivered, error) VALUES (?,?,?,?,?,?)",
                (decision_id, reach_id, channel, utcnow(), delivered, error))

    def dispatched_tiers(self, candidate_id):
        """Tiers already dispatched for this candidate, so a revision can
        escalate without the same alert going out twice."""
        with self.conn() as c:
            return {r["tier"] for r in c.execute(
                "SELECT DISTINCT d.tier FROM alerts a"
                " JOIN decisions d ON d.id = a.decision_id"
                " WHERE d.candidate_id = ?", (candidate_id,))}

    # -- impact ------------------------------------------------------------

    def record_impact(self, decision_id, settlements, snap_km=None,
                      data_version=None):
        """Append the downstream corridor for a decision. Never updates."""
        if not settlements:
            return 0
        with self.conn() as c:
            c.executemany(
                "INSERT INTO impact (decision_id, settlement, kind, population,"
                " river_km, offset_km, channel, snap_km, data_version,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(decision_id, x["name"], x.get("kind"), x.get("population"),
                  x["river_km"], x.get("offset_km"), x.get("channel"),
                  snap_km, data_version, utcnow()) for x in settlements])
        return len(settlements)

    def corridor(self, decision_id):
        with self.conn() as c:
            return c.execute(
                "SELECT settlement, kind, population, river_km, offset_km,"
                " channel FROM impact WHERE decision_id = ?"
                " ORDER BY river_km", (decision_id,)).fetchall()

    # -- heartbeats --------------------------------------------------------

    def heartbeat(self, source, ok, detail=None):
        with self.conn() as c:
            c.execute("INSERT INTO heartbeats (source, ok, detail, at)"
                      " VALUES (?,?,?,?)", (source, 1 if ok else 0,
                                            detail, utcnow()))

    def last_ok_heartbeat(self, source):
        with self.conn() as c:
            r = c.execute("SELECT at FROM heartbeats WHERE source=? AND ok=1"
                          " ORDER BY at DESC LIMIT 1", (source,)).fetchone()
            return r["at"] if r else None

    # -- reporting ---------------------------------------------------------

    def why_not_fire(self, external_id):
        """The design REQUIRES this question be answerable from the log alone."""
        with self.conn() as c:
            return c.execute(
                "SELECT c.external_id, c.observed_at, c.lat, c.lon, c.depth_km,"
                " c.magnitude, d.score, d.tier, d.factors, d.nearest_site,"
                " d.nearest_km, d.suppressed, d.suppress_reason"
                " FROM candidates c JOIN decisions d ON d.candidate_id = c.id"
                " WHERE c.external_id = ?", (external_id,)).fetchone()

    def tier_counts(self, since_iso):
        with self.conn() as c:
            return {r["tier"]: r["n"] for r in c.execute(
                "SELECT tier, COUNT(*) n FROM decisions WHERE decided_at > ?"
                " GROUP BY tier", (since_iso,)).fetchall()}
