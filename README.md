# Watchers — Himalayan Early Warning

Two hazard watchers that run on a Raspberry Pi and tell one operator what they
saw. A seismic-catalogue watcher for the Himalayan arc, and a rainfall watcher
for Kerala's Western Ghats.

## Read this before anything else

**This is a measurement instrument, not a warning service.** It does not warn
anybody, and it is not currently capable of doing so.

- **Dispatch is OFF and stays off.** Nothing is sent to any member of the
  public. Who gets told is a decision for a partner institution, not for
  software, and not for its authors.
- **The Phase 0 gate is NOT passed.** The false-alarm ceiling measures
  **3.77 alerts/year** against a gate of ≤2. That is the number that decides
  whether catalogue-only detection is viable, and it currently says no.
- **The seismic feed is thirteen hours too slow.** For the 26 August 2026
  Langtang collapse, the record that identifies it as a landslide arrived
  **13 h 06 m after the slope failed**. At the time it mattered, the feed
  carried an M4.4 earthquake at a default depth. That is a property of the
  feed and no threshold fixes it.
- **Entire event classes are invisible.** Replayed against the real Gorkha
  M7.8 window, the detector produces **zero** dispatches — it is silent
  during the period of maximum landslide risk.

The rainfall track is the one with real lead time — roughly a day's notice —
and it is explicitly an attention product: *"pay attention tonight"*, never
*"leave your home"*.

## What it actually does

| | |
|---|---|
| Hazard registry | 82,152 sites — RGI 7.0 glaciers + NSIDC HMA lakes, whole arc |
| Routing | 20,183 river ways, 115,075 settlements |
| Seismic watch | USGS catalogue, 60 s poll, 25.5–38 N / 70.5–96.5 E |
| Rainfall watch | 316 model-grid nodes over Kerala, 3-hourly, 10-year percentiles |
| Output | a read-only dashboard, and one push to one operator |

## Quick start

    python3 -m hew.watcher --once                  # one seismic cycle
    python3 -m hew.status --db hew.db --port 8080  # dashboard
    python3 -m pytest tests/ -q                    # 191 tests

The dashboard is at `http://localhost:8080/`, the rainfall tab at `/rain`,
and `/simulate` runs drills that write nothing to the ledger.

## Deploy to a Raspberry Pi

    bash deploy/install-pi.sh

Installs three systemd units — seismic watcher, rainfall watcher, dashboard.
Measured on a Pi 5: **231 MB resident**, 0.9 s startup, 1.7% of one core.
A 1 GB Pi 3 is comfortable; a 512 MB Pi Zero is not.

A fresh install has no history, because each poll looks back only 90 minutes:

    python3 scripts/backfill.py --db ~/.local/share/hew/hew.db --days 365

## Rebuilding the data

Large layers are not in git; every one is rebuilt by a committed script, with
provenance recorded in `data/manifest.json`.

    python3 scripts/build_hazard_registry.py --rgi RGI2000-v7.0-G-global-attributes.csv
    python3 scripts/fetch_himalaya_geodata.py --layers places rivers
    python3 scripts/compact_rivers.py data/rivers_himalaya.json
    python3 phase0_backtest.py --years 2015 2026     # the kill gate

## Operator alerts

Set `HEW_NTFY_TOPIC` to a long random string and subscribe to it in the
[ntfy](https://ntfy.sh) app. Topics are public to anyone who knows the name:
whoever guesses yours can read your detections **and publish fake ones**.

    HEW_NTFY_TOPIC=hew-<something-long-and-random> python3 -m hew.hew_operator --test

You are notified when a dispatch-tier decision is made, and when an event
with **unconstrained depth** lands on a mapped hazard — about 46 times a year.
That second case exists because on 26 August the first human signal was the
corrected record, thirteen hours late.

## Reading the dashboard

The banner answers *what is happening*, not *is the poller alive*:

    NOT LOOKING  >  WARNING  >  WATCH  >  NOTHING IN LAST 24 H

`NOT LOOKING` means the watcher has stopped polling — **not** that things are
quiet. A stopped watcher cannot make a claim about the ground. `LAST DETECTION`
sits below with no time window, so a detection cannot be hidden by having
happened yesterday.

## Where the bodies are buried

`CONSTRAINTS.md` is the honest record: protected invariants that must not be
"simplified", every finding with its status, and the measured numbers behind
each. Read it before changing anything. Highlights:

- **D5** — catalogue latency, 13 h 06 m. Open, and blocks Phase 1 as a warning path.
- **D9** — earthquake-triggered collapses structurally invisible. Open.
- **D15** — the registry and fetch box were the binding constraint on coverage.
  Kedarnath sat 510 km outside the old box, Chamoli 449 km. Mitigated; the
  gate is not passed.

## Licence and data

OpenStreetMap data under ODbL. RGI 7.0, NSIDC HMA glacial lakes, Copernicus
DEM via Open-Meteo, USGS FDSN, KSDMA bulletins — each cited in
`data/manifest.json` with its epoch.
