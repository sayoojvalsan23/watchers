# Himalayan Early Warning — Phase 1 watcher

Runnable reference implementation of the catalogue watcher described in
`hew-design-v0.2.md`.

**Phase 1 dispatches nothing by default.** It runs log-tier only, to measure
the false-positive rate. `--allow-dispatch` is deliberately opt-in.

## Run

    python3 -m hew.watcher --once                      # single cycle
    python3 -m hew.watcher                             # continuous
    python3 -m hew.watcher --replay replay_20260826.json --allow-dispatch
    python3 -m pytest tests/ -q

## Deploy

    docker compose -f deploy/docker-compose.yml up -d
    # or
    sudo cp systemd/hew-watcher.service /etc/systemd/system/
    sudo systemctl enable --now hew-watcher

## Before Phase 1 goes live

1. Run the full Phase 0 catalogue backtest. It is a kill gate.
2. Replace `hew/registry.py` with the real ICIMOD inventory, plus coverage
   for the Tibetan side of the border. Registry gaps are silent false
   negatives — they appear in no metric until an event is missed.
3. Confirm whether NSC already ingests seismic feeds (design doc §19.6).

## Storage

SQLite for Phase 1: no spatial queries are needed and a single file is the
most portable thing to hand a partner. Move to Postgres/PostGIS in V2 when
the river graph lands.
