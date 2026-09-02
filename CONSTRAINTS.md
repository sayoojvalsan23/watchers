# Constraints

Last reviewed: 2026-09-02 (D13, D14 closed; D15 mitigated -- **Phase 0 ceiling is 3.77 alerts/yr, TUNE. The gate is not passed.**)

This is a life-safety system. The rules below are not style preferences and
they are not subject to agent judgement. An agent that finds one of these
inconvenient must stop and surface it, not route around it.

## Floor (always enforced, no setup required)

- No new suppression comments: `# noqa`, `# type: ignore`, bare `except:`
- No unimplemented stubs or empty `except` blocks on the trigger path
- No skipped, xfail-flipped or deleted tests without a reason in the commit message
- No secrets in source
- This file does not get weakened to make a change pass

## Protected invariants — DO NOT SIMPLIFY

These are deliberately redundant. Redundancy is the feature. A general-purpose
simplification or refactoring pass will identify every one of them as dead
weight; that judgement is wrong here and must not be acted on.

| Invariant | Where | Why it stays |
|-----------|-------|--------------|
| Dispatch off by default | `watcher.py` `allow_dispatch=False` | Phase 1 measures; it does not warn |
| No agent on the trigger path | whole of `hew/` | Every trigger replayable in the latency budget |
| Alert text never generated at send time | `notify.py` `TEMPLATES` | An alert string is a reviewed safety artifact |
| No ETA in a dispatched alert | `notify.py` | Propagation unvalidated; a wrong ETA is worse than none |
| Fail-safe, never fail-open | `watcher.check_health` | Loss of confidence → no alert **and** a page |
| Circuit breaker | `watcher._breaker_tripped` | An alert storm permanently destroys credibility |
| Append-only decision log incl. negatives | `store.py` `decisions` | "Why did it not fire" must be answerable from the record |
| Canary through the full path | `watcher.canary` | A system that broke 6 months ago looks identical to a quiet one |
| Human gate on public alerts | policy, Phase 4 | Removal is the partner institution's call, not ours |

## CORRECTED: the 26 August timeline

The project brief states a **1h51m** gap and a first official warning at
**10:28**. Public reporting does not support this and the figure should not be
used. What the record shows:

| Nepal time | What happened | Since collapse |
|-----------|----------------|----------------|
| 08:37 | Collapse. M5.2 radiates worldwide within seconds. USGS auto-publishes M4.4 *earthquake*. | — |
| ~09:00–09:05 | Flood Forecasting Division learns of the flood — **by telephone**, from the Rasuwa District Administration Office and staff at the Betrabati hydrological station. Not from any seismic feed. | **~25 min** |
| 09:15–09:16 | SMS alerts dispatched to **679,295** residents. | **~38 min** |
| 21:44 | USGS publishes the landslide-typed origin. | +13h06m |

**The gap was ~38 minutes, not 1h51m, and 679,295 people were warned.**

This narrows the project's target but does not remove it. The addressable gap
is **08:37 → ~09:00**: roughly **25 minutes** during which the seismic signal
existed and nobody had yet phoned it in. That is what an automated detection
path could close. Every claim in the design docs resting on "1h51m" must be
restated against ~25 minutes.

Two details that also matter:

- The flood **entered the Bhote Koshi from Tibet**, i.e. the Lende branch —
  the one a single-branch corridor structurally misses. The branch-union
  work (D6) was necessary, not merely prudent.
- **Betrabati** (Betrawati, 52.0 km in our corridor) had a hydrological
  station, and its staff were part of the human reporting chain. A gauge
  existed; it was read by a person and relayed by phone.

Sources are public reporting, not the FFD technical report. Obtaining that
report remains open question #1, and it is now the thing that would confirm
or correct the ~25 minute figure.

## Source latency rule

**An input whose end-to-end latency exceeds the available warning window
cannot sit on the trigger path.** It may inform susceptibility, or confirm
an event after the fact, but it may not be what fires an alert.

| Source | Latency to usable record | Trigger path? |
|--------|--------------------------|---------------|
| Seismic waveform (SeedLink, local) | seconds | yes |
| Seismic catalogue, initial origin | ~20-40 min (unmeasured) | marginal |
| Seismic catalogue, **reviewed/characterised** | **13 h, measured (D5)** | **no** |
| River gauge, telemetered | station reporting interval, typ. 15 min | confirmation only |
| Rainfall AWS | typ. hourly | susceptibility only |
| Satellite precipitation (IMERG Early) | ~4 h | susceptibility only |
| River network, settlements, registry | years — static | startup load only |

**Corollary — under location uncertainty, widen rather than choose.** Routing
takes the union of every candidate channel within the error radius. Warning a
settlement that turns out to be safe is recoverable; routing the flood down
the wrong branch is not. This follows directly from the fail-safe rule and is
enforced by `source_uncertainty_km` in the config, recorded with the decision.

Poll frequency is not the constraint. A station's own reporting interval is.
Polling a 15-minute feed every 60 s returns the same value fifteen times.

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|-----------|------|-----------|---------|
| Tests | Zero failures | `python3 -m pytest tests/ -q` | every edit |
| Phase 0 false alarms | Ceiling ≤ 2.00 alerts/yr | `python3 phase0_backtest.py --years 2015 2026` | before any phase advance |
| Phase 0 recall | 100% of curated set fires, **on real catalogue records** | `python3 -m pytest tests/test_curated_events.py -q` | before any phase advance |

The Phase 0 gate is **paired**. A false-alarm rate alone is not a gate: a
detector that rejects everything scores 0.00 alerts/yr and passes. Both rows
must pass together or the gate has not been met.

**Both rows are measured on reviewed catalogue values.** D5 shows that for the
26 Aug 2026 event the reviewed record arrived 13 hours late, so the recall row
proves the filter is *correct*, not that it would have been *timely*. Recall
against a real-time feed can only be measured forward, by capturing
preliminary origins as they publish. Do not quote the recall row as evidence
of warning capability.

## Measured, not yet enforced

| Metric | Today (2026-08-31) | Direction |
|--------|--------------------|-----------|
| **Phase 0, REAL registry** | **0.34 /yr — GO** | the number that counts |
| Registry | **331 mapped glacial lakes** (14,394 HKH-wide) | was 8 placeholder sites |
| Calibrated hazard radius | 11.0 km, 95% CI [8.2, 20.9] | n=22; 15.0 sits inside the CI |
| 26 Aug 2026, real registry | WARNING, score 84, nearest lake 10.0 km | was 0.0 km by construction |
|--------|--------------------|-----------|
| Phase 0 ceiling false-alarm rate | 1.89 /yr | must not rise above 2.00 |
| Phase 0 floor (8-site registry) | 0.17 /yr | both alerts are true positives; not a false-alarm signal |
| Curated recall — USGS-reachable | **1 of 1** | holds at 1 of 1 |
| Curated recall — needs regional feed | **0 of 1** | blocked on feed, not on tuning |
| USGS magnitude completeness, bbox | **M4.0** | feed limitation, not tunable |
| Registry coverage vs ICIMOD inventory | unmeasured (8 placeholder sites) | must be measured before Phase 1 |
| Catalogue events at default depth | 80.6% (462/573) | now capped at watch, not discarded |
| Default-depth events within 15 km of a listed site | 4 of 462 | rises sharply with a real registry |
| Catalogue latency to characterised record | **13 h 06 m** (26 Aug 2026) | not tunable; feed property |
| Pipeline latency, record → corridor | 0.1 ms detect + 1.4 s route (2 branches) | not the bottleneck |
| Corridor distances (source-referenced) | Mailung 44.9 · Betrawati 57.9 · Bidur 69.5 · Devighat 75.6 km | was 39.0/52.0/63.6/69.6 before D10 |
| Corridor, 26 Aug source, 15 km uncertainty | 65 settlements over 103 km, 2 branches | superset of single-branch |

The curated set is split by feed reachability on purpose. Phase 0 is met for
the event class USGS can see, and cannot be met for the class it cannot. That
is a feed decision (D4), not a threshold to tune.

## Caveat on the D7 gate result

Smoothing left the Phase 0 gate at exactly 1.89 alerts/yr. **That is not
evidence the change is free.** The two models differ only between roughly 10
and 35 km from a listed site, and with the 8-site placeholder registry
**exactly one** of the 23 events that reach scoring lands in that ring — where
it stays a `log` under both. The equivalence is real but barely exercised.

With the real ICIMOD inventory (~2,000 lakes) far more events will sit in that
ring, the models will diverge, and **the gate must be re-run.** Do not carry
1.89/yr across a registry change.

## Calibrating hazard_radius_km

`hazard_radius_km = 15.0` is a guess fitted to one point: Rasuwagadhi 2025 sat
13.3 km from the nearest registered site. `scripts/calibrate_radius.py`
replaces it with a measured distribution once a real glacier/lake inventory
exists. Offline only — late labels are fine for fitting a constant, and are
not fine for training a real-time classifier on the same data.

Sample sizes available today, inside the study box:

| Source | n | Population | Use |
|--------|---|------------|-----|
| BIPAD avalanche (Nepal official) | 22 | high-altitude ice/snow mass movement | best available proxy |
| USGS landslide-typed | 2 | seismically-detectable mass movement | too few |
| BIPAD landslide (Nepal official) | 1,933 | **90% June–Sept — rainfall-triggered** | Phase 5 only, never pooled |
| BIPAD glacial lake outburst | **0** | the target class | **category exists, nothing filed** |

n=22 supports a median, not a 95th percentile. The script refuses quantiles
the sample cannot carry (a p95 needs ~200 samples) and reports a bootstrap
interval so the uncertainty stays visible. It emits no recommendation at all
against the placeholder registry.

**`hazard_radius_km` is on the trigger path. Re-run the Phase 0 gate after
changing it.**

## Finding 2 is closed

Registry completeness was named the binding constraint, and it was — but not
in the way the brief described. The 8-site placeholder was **circular**: its
2026 entry sat at exactly the event coordinates, so distance-to-hazard was
0.0 km by construction, for both curated events. The gate was scoring the
detector against a hazard list fitted to the answers.

Against the real NSIDC High Mountain Asia inventory (14,394 lakes, 331 in the
study box, 2015-2018 epoch — see data/manifest.json):

| | placeholder | real inventory |
|---|---|---|
| 26 Aug 2026, nearest hazard | 0.0 km (typed in) | **10.0 km** |
| Rasuwagadhi 2025, nearest hazard | 0.0 km (typed in) | **17.8 km** |
| Phase 0 dispatch rate | 0.17 /yr | **0.34 /yr — GO** |
| 26 Aug 2026 outcome | WARNING 95 | **WARNING 84** |

The synthetic "ceiling" (1.89/yr) was far too pessimistic: it assumed a hazard
at every event, whereas real glacial lakes cluster in high terrain and most
catalogued events are nowhere near one. **0.34/yr is the honest number.**

`hazard_radius_km` is now the **calibrated 11.0**, with its provenance stored
alongside it in the config (method, sources, n, CI, inventory, date) so a
decision logged with `config_version` also records where the number came from.

An earlier draft of this file argued for keeping 15.0 because it gave the
founding event more margin. That was wrong, and wrong in the same way the
placeholder registry was wrong: choosing a parameter because it flatters the
validation case. The calibrated estimate stands on its own or it does not
stand. Two tests now guard it — the value must lie inside its recorded CI,
and it must equal the calibrated point estimate.

## Thresholds, re-derived  (closed 2026-08-31)

The old set `{watch: 45, advisory: 60, warning: 75}` was chosen when the
registry was circular and every curated event scored 95 by construction. With
the real inventory the achievable maximum is **92**, and the founding event
landed on **exactly 75** — the threshold sitting on top of a data point.

Re-derived from what each tier *means*, using the weights:

| Tier | Anchor | Value |
|------|--------|-------|
| WARNING | surface + proximity better than even + typical magnitude | 35 + 22.5 + 15 = **72** |
| ADVISORY | surface + proximity better than even, one line missing | 35 + 22.5 = **57** |
| WATCH | one strong line | **45** (unchanged) |

Derived from the definition, not from a target alert count and not from the
sample. An empirical gap exists at 60–74 in the 573-event set, so anything in
65–72 gives identical dispatch behaviour; the anchor was preferred because the
gap is a feature of a sparse sample and will fill in, while the definition
will not. A test asserts each threshold still equals its anchor, so changing a
weight without revisiting the thresholds fails the suite.

### Proximity dispatch floor

`min_proximity_confidence_to_dispatch: 0.5`. A scalar threshold on a **sum**
cannot express "proximity must be better than even" — a weak-proximity event
reaches the same total by another route. The synthetic Rasuwagadhi case did
exactly that: score 58 from surface + magnitude + only 0.18 proximity
confidence. The floor makes the claim real, and like the unknown-depth cap it
is policy rather than arithmetic, so no reweighting bypasses it.

### Result

| | value |
|---|---|
| Phase 0 dispatch rate, real registry | **0.26 /yr — GO** |
| Dispatches in 12 years | 3 |
| 26 Aug 2026 | WARNING 75, margin +3 |
| Rasuwagadhi (synthetic) | WATCH — capped by the proximity floor |

The three dispatches are the two real landslide records and one 2021 M5.0 at
8.7 km depth, 10.8 km from a glacial lake. That third is not noise: a shallow
M5 beside a glacial lake is exactly the Finding 3 ambiguity, and "prepare and
inspect" is the correct response to it.

## Generalization test — six historical analogs (2026-08-31)

Run against the real catalogue. **0 of 6 detected.**

| Event | USGS record | Detector output | Why |
|-------|-------------|-----------------|-----|
| Chamoli 2021 | **none** | nothing to evaluate | GFZ recovered the signal from waveforms (Science, 2021); the catalogue never carried it |
| Langtang 2015 | trigger only, M7.8 | REJECT `magnitude_out_of_range` | D9 — collapse produced no separate record |
| Kedarnath 2013 | **none** | nothing to evaluate | rainfall / moraine-dam breach — Phase 5, correctly out of scope |
| Huascarán 1970 | trigger only, M7.9 @ 58 km, 300 km away | REJECT `magnitude_out_of_range` | D10 | **Branch corridors numbered from their own snap point.** Each candidate branch measured `river_km` from where it met water, not from the source. For 26 Aug the two branches snap 5.9 km and 10.3 km out, so "0.0 km" meant two places ~10 km apart, five settlements on two different rivers all read as 0.0, and some `river_km` values were *less than the straight-line distance* — physically impossible. The public distance bands (0-20 / 20-55 / 55-105 km) sit directly on this number. | Public alert bands were meaningless across branches; responders would read unrelated places as co-located. | **CLOSED 2026-08-31.** `river_km` is re-based on the source by adding each branch's snap offset; raw along-channel distance kept as `channel_km`. Every town moved +5.9 km. Two tests guard it: branches must not share a start, and `river_km` must never be below straight-line distance. |
| D11 | **Settlements upstream of a branch start were reported as downstream.** A trace begins where the uncertainty circle meets water; settlements laterally within the corridor of that first vertex were matched to it regardless of which side they were on. Four villages beside the Lende snap point — 0.9 to 1.9 km off-channel and *behind* the start — were listed as downstream of a collapse 10 km away. | Telling people upstream of the source that water is coming at them. | **CLOSED 2026-08-31.** Settlements projecting behind the first segment's direction of travel are excluded. The filter must run **per branch**: on a merged path each branch's vertex 0 is buried mid-list and the test silently never fires — which is how the first attempt failed. Corridor 65 → 60. |
| D9 |
| Kolka 2002 | **none** | nothing to evaluate | plus **zero** mapped lakes within 50 km |
| Vajont 1963 | **none** | nothing to evaluate | bedrock into a reservoir, no glacier — permanently out of scope |

Registry coverage outside High Mountain Asia is effectively nil: Kolka 122 km
to the nearest mapped lake, Vajont 94 km, both with none inside 50 km. The
inventory's bounding box is global; its **content** is not.

**What this scopes the system to:** a moderate, shallow, isolated seismic
event near a mapped glacial lake, in a region USGS catalogues densely, that
USGS also characterises. The 26 Aug 2026 event fits. Almost nothing else in
the historical record does.

## Mode 2 — what happens after a large earthquake

`hew/cascade.py`. A M7.8 shakes **4,480** mapped hazards inside the empirical
landslide-distance limit. A handful fail. The pipeline is:

1. **Footprint** — magnitude → outer distance limit (Keefer-style table).
   *A distance proxy, not a ground-motion model.* Needs literature sourcing.
2. **Triage** — rank by shaking + mass + steepness, equal weights, every
   component visible. A queue, **not a probability of failure**.
3. **Exposure** — route the top N and attach downstream settlements and
   population. This is what separates a geology problem from an evacuation
   problem, and ranking alone cannot see it: for Gorkha the third-ranked
   hazard had 84 settlements and 27,050 people below it.

**Mode 2 output is an inspection list, never an alert.** It has no tier and
cannot reach the public templates — a test asserts this. Co-seismic failures
are scattered and lag the trigger by hours to weeks; warning everyone below
every shaken glacier would be a mass false alarm.

### The ranking took three attempts. All three failures are instructive.

| version | Langtang rank (Gorkha) | Gorkha vs Dolakha top-6 | verdict |
|---------|------------------------|--------------------------|---------|
| shaking + mass + steepness | #128 of 4,480 | — | consequence-blind: every top hazard had **zero** settlements within 8 km |
| + exposure ×2 | #5 | **identical** | epicentre-blind: a static map of where glaciers and villages coexist |
| + 1/R attenuation, region-wide places | #3 | 2% overlap | responds to the event |

Two root causes, both worth remembering:

1. **A linear `1 - d/R` shaking term is far too flat.** At R=295 km a hazard
   250 km out still scored 0.15, so the epicentre signal drowned under the
   static exposure term. Real ground motion falls off at least as 1/R.
2. **Exposure was computed against the Trishuli-basin settlement file** — the
   854 places fetched for *routing*. Hazards span 84.00–88.93 E; places
   covered 84.85–85.95 E. Khumbu, Rolwaling and Manaslu were invisible, so
   the only place the system could see people was the one valley it always
   named. Fixed by fetching 5,580 places across the whole domain.

The second is the more dangerous kind of bug: nothing errored, the output
looked plausible, and it took asking *"does this answer change when the
event changes?"* to expose it.

### Validated against Gorkha 2015

Replaying the real mainshock (M7.8, 28.15/84.71) against the 4,480 hazards
inside its footprint, and asking whether the ranking would have sent anyone
to Langtang, where ~350 people died under a 4–12 km² glacier:

| ranking | best rank near Langtang | appearing in the reported top 40 |
|---------|------------------------|----------------------------------|
| shaking + mass + steepness | **#128 of 4,480** | **0** |
| … + exposure, weighted ×2 | **#5 of 4,480** | **9** |

The first version optimised for the biggest ice. Every one of its top-ranked
20–35 km² glaciers had **zero settlements within 8 km**, while Langtang had
four. Exposure is weighted double because of this, and a test asserts a
modest inhabited hazard outranks a huge empty one.

This is a retrospective fit to one event and should not be mistaken for
validation. Gorkha has a published co-seismic landslide inventory; the
weights should be calibrated against it before anyone relies on the order.

**Known limit:** the river network covers the Trishuli basin only, while the
hazard registry covers the whole study box. Hazards outside the routed basin
return zero downstream exposure — absence there means "not routed", not "safe".

## Phase 5 (rainfall) fails its own kill gate on the open feed

Measured 2026-08-31 against Chooralmala / Mundakkai, 30 July 2024 (~250 dead),
using Open-Meteo ERA5 and ten years of that location's own climatology.

| | 24 h | 72 h antecedent |
|---|---|---|
| IMD stations (actual) | **373 mm** | **586 mm** |
| Open-Meteo ERA5 | 50.9 mm | 71.8 mm |

Not a grid-offset artefact: the best neighbouring cell was still 3x short.
ERA5 runs at ~25 km and the storm was a few km wide over the escarpment.

Against ten years of local climatology the event reads:

    12 h  39.9 mm  p98.51   <- the driving window
    24 h  50.9 mm  p96.34
    72 h  71.8 mm  p87.41   <- BELOW the local p99 of 220 mm

**Tier: WATCH.** The deadliest landslide in Kerala's recent history does not
reach advisory on this feed. And the cost of lowering the bar to catch it:

    threshold p98.51  ->  137 distinct days in 10 years  ->  14 alerts/year

against a Phase 0 gate of <= 2/year. **A 7x failure.**

The engine therefore works in PERCENTILES of each location's own climatology
rather than millimetres, so thresholds survive a feed swap (literature
thresholds are gauge-calibrated and never trip on a value 7x low). That fixes
miscalibration. It does not fix blindness.

### Resolution is not the problem. Gauges are.

Tested every openly-reachable gridded product at Chooralmala, 30 July 2024.
IMD gauges recorded **373 mm in 24 h**:

| product | grid | reading |
|---------|------|---------|
| NASA POWER (MERRA-2) | ~50 km | 52.6 mm |
| Open-Meteo ERA5 | ~25 km | 51.6 mm |
| Open-Meteo ECMWF-IFS | ~25 km | 51.6 mm |
| **CHIRPS v2.0** | **~5.5 km** | **49.7 mm** |

**A ninefold improvement in resolution changed the answer by 3 mm.** They all
converge on ~50 mm against 373 mm actual, so this is not a grid-cell
averaging problem and no higher-resolution gridded product will fix it.

The reason: these products blend satellite infrared with GAUGE data, and
there is no gauge in Wayanad. Satellite IR infers rain from cloud-top
temperature, which correlates poorly with extreme orographic rainfall on a
windward escarpment. With no local gauge to anchor the blend, every product
falls back on the same physics and reaches the same wrong number.

(CHIRPS is a UTC-day product; the failures were ~01:00 IST 30 July, i.e.
~19:30 UTC on 29 July, so 49.7 mm on the 29th is the event day. The 0.0 mm
on the 30th is a day-boundary artefact, not a second finding.)

**Phase 5 is blocked on data access, not on modelling** -- the same shape as
D4/D5 on the seismic side. What is reachable:

| source | access | resolution |
|--------|--------|-----------|
| Open-Meteo ERA5 | open | ~25 km, blind to the target |
| GPM IMERG | Earthdata account | ~11 km, ~4 h late |
| data.gov.in | free key, registration | station level -- **try this first** |
| IMD APIs | credentials (401) | station level |
| KSDMA | **open, extracted** | station level -- **see below** |

### CORRECTION: there ARE gauges in Wayanad, and they are public

The claim above -- "there is no gauge in Wayanad" -- was WRONG. It came from
the indianapi.in station list, which exposes 9 Kerala stations. Kerala SDMA
publishes a daily bulletin covering ~79, **five of them in Wayanad**:
Pookode, Kuppadi, Vythiri, Mananthavady, Ambalavayal. Vythiri is the taluk
containing Chooralmala and Mundakkai, ~10 km from the failure.

    https://sdma.kerala.gov.in/rainfall-2/          the daily bulletin
    /wp-json/wp/v2/media?search=prediction          a DATED archive, 800 PDFs

The bulletin is a PDF with no ToUnicode CMap, so text extraction and
copy-paste both return mojibake. `hew/ksdma.py` inverts the embedded
TrueType font's own `cmap` and recovers the numbers exactly -- no OCR.
Verified row-for-row against the rendered page.

### On the gauge feed, Phase 5 PASSES its kill gate

Vythiri, 30 July 2024 (08:00 29th -> 08:00 30th): **280.0 mm**, against
~50 mm from every gridded product. Verified archive: 816 bulletins, 586 with
extractable tables, 537 days with a Vythiri reading (1.47 years).

    30 July 2024      280 mm    <- the wettest day in the whole record
    next wettest      190 mm       (2025-05-26)
    p99               146 mm
    median            2.5 mm

All four Wayanad gauges peak on that same date. Threshold sensitivity:

    >= 150 mm     4 days    2.7 / year   FAIL
    >= 200 mm     1 day     0.7 / year   PASS  <- catches it, inside the gate

CAVEAT, and it is a real one: the passing threshold rests on **n = 1**. Only
one day in 537 exceeds 200 mm, and it is the event itself. The window that
both catches Chooralmala and passes the gate is 190-280 mm -- narrow, and
defined by a single observation. "0.7 alerts/year" therefore carries a wide
confidence interval, and the archive cannot be extended: KSDMA's media
library for this bulletin series begins in May 2024.

Treat the gate as PROVISIONALLY passed. It is strong enough to justify the
IMD access ask and far stronger than anything the gridded feeds support, but
it is not yet a calibration anyone should deploy on. More years, more
stations, or a curated multi-event inventory are needed to firm it up.

**On ERA5 catching this event cost 14 alerts/year. On the gauge feed it costs
0.7.** Same event, same site, same engine -- a 20x difference in false-alarm
rate that comes entirely from the instrument. The percentile design carries
over unchanged; it is re-derived from whichever feed is supplied.

### But the gauge feed is a CALIBRATION set, not a trigger

The bulletin covers 08:00 to 08:00 and publishes around 12:00 IST. The
Chooralmala landslides were ~01:00 and ~04:10 on 30 July; the PDF carrying
that night's 280 mm went up at **12:16 -- more than eight hours after**.

This is D5 again in a different feed: the record that proves the event is
published long after the people it concerns needed it. It is worth having
anyway, because it is what turns an uncalibrated detector into a calibrated
one, and because it makes the institutional ask precise.

The real-time feed of these same gauges is IMD's AWS API:

    mausam.imd.gov.in/api/current_wx_api.php   401  "needs to be whitelisted"
    mausam.imd.gov.in/api/aws_data_api.php     401  "needs to be whitelisted"

The feed exists and runs. **Access is a whitelist entry, not a research
problem.** That is now the single blocking item for the rainfall track.

Lead time on the real-time feed remains UNPROVEN. The rain fell overnight and
the slope failed at ~01:00. Whether an hourly gauge crosses 200 mm early
enough to be actionable cannot be answered from daily totals, and must not be
assumed. Test it before claiming it.

### The leaky integrator loses, and it loses in the now-familiar way

The physically-motivated alternative to fixed windows is a pore-pressure
proxy that decays rather than truncating:

    S_t = S_{t-1} * exp(-a) + R_t          a = ln2 / drainage_half_life

One parameter, real physics, and it should in principle beat a bank of
rolling sums. Swept across half-lives, showing each event's percentile:

| half-life | Chooralmala | Pettimudi | Puthumala | Kavalappara | Koottickal | MIN |
|-----------|-------------|-----------|-----------|-------------|------------|-----|
| 6 h | 98.46 | 99.72 | 99.77 | 99.46 | 99.20 | **98.46** |
| 24 h | 94.37 | 99.82 | 99.79 | 99.65 | 97.37 | 94.37 |
| 72 h | 92.13 | 99.76 | 99.42 | 99.01 | 95.94 | 92.13 |
| 168 h | 93.64 | 99.41 | 98.67 | 96.76 | 95.80 | 93.64 |

**Every half-life is worse than the current window-max (worst event p99.27).**

And note the shape: the more MEMORY the integrator has, the worse Chooralmala
gets -- 98.46 at 6 h decaying to 92.13 at 72 h. At a 6 h half-life it has
degenerated into a short rolling sum, which is what we already have.

This is the FOURTH independent confirmation of the same fact:

    1. KSDMA gauge          27.6 mm at Vythiri the morning before
    2. antecedent windows   p94.82 at failure
    3. AND-rule backtest    misses Chooralmala
    4. leaky integrator     degrades monotonically with memory

**Chooralmala was a burst on unprimed ground.** Any feature that integrates
history dilutes exactly the event this project exists to catch. That is now a
robust finding rather than an observation, and it should be treated as a
design constraint: memory-based features are for OTHER events, never for the
founding one.

### Intensity-spike signatures: a real gain, deliberately NOT adopted

Tested whether a short-duration intensity "jump" discriminates better than
accumulation alone. Each signature's percentile at the five events (the MIN
is what a detector must fire at to catch all five):

| signature | worst event | verdict |
|-----------|------------|---------|
| **peak hourly mm** | **p99.13** | strongest |
| 3 h acceleration | p97.74 | moderate |
| jump ratio (hour / 24 h mean) | p87.58 | weak |
| burst share (6 h / 24 h) | p79.98 | weakest |

The intuitive "jump" framings -- ratio to baseline, burst share -- are the
WEAK ones. Raw peak hourly intensity is the strong one. Combining it with the
existing window test:

| rule | recall | alerts/yr |
|------|--------|-----------|
| windows >= p99.25 (current) | 5/5 | 12.5 |
| **windows >= p99.25 AND hourly >= p99.10** | **5/5** | **10.7** |
| peak hourly alone | 5/5 | 21.1 |
| windows p99.5 AND hourly p99.0 | 4/5 | 8.5 |

A 14% false-alarm reduction at no loss of recall. **Not adopted**, for three
reasons:

  1. The thresholds were chosen by looking at the five events that then
     score them. That is in-sample selection, and roughly six rules were
     tried. A 12.5 -> 10.7 move is well inside what that noise can produce.
  2. It is an AND. Relative to windows-alone it can only ever REDUCE recall
     on an event not in this set -- it adds a second condition that a future
     landslide must also satisfy.
  3. The asymmetry is stark. This is a watch: a false alarm costs attention,
     a miss costs lives. At 12.5/yr we are already 6x the gate, so shaving to
     10.7 does not change what the product IS -- it stays a watch, not an
     evacuation trigger. The gain buys nothing categorical while the risk of
     missing event six is real.

Revisit with more events. With five, a rule that improves the metric by
adding a condition is more likely to be overfitting than insight.

### The watch withdrew four hours before the hillside failed

Replaying Chooralmala hour by hour found a defect no threshold test could see.
The system fires 7 h ahead and then STANDS DOWN before the disaster:

    29 Jul 18:00   3 h at p99.30   WATCH   <- fires, 7 h ahead
    29 Jul 20:00   3 h at p99.52   WATCH
    29 Jul 21:00   3 h at p97.58   log     <- withdraws
    30 Jul 01:00                   log     <- failure. Silent.

The cause is physical. Rainfall PEAKED at 18:00 (9.6 mm/h) and was easing to
1.3 mm/h when the slope went:

    09:00-13:00   1.4 -> 1.9 mm/h    building
    18:00         9.6 mm/h           peak
    21:00-00:00   1.8, 2.5, 1.7, 1.9 easing
    01:00         1.3 mm/h           FAILURE

**Slopes fail after the rain, not during it** -- pore pressure keeps climbing
once the downpour stops. A detector driven by a 3 h window therefore stands
down exactly when risk is highest. The longer windows knew (12 h held p98.5,
24 h climbed through the failure) but `assess` takes the MAX across windows,
so a collapsing short window drags the verdict down with it.

Fixed by LATCHING: a raised watch holds for `hold_hours` (18) and releases
early only if every window drops below `release_percentile` (p95). Measured
across all five events:

| | raw | latched |
|---|---|---|
| mean alerts/yr/site | 12.9 | **12.5** |
| failure hour covered | 0/5 verified | **5/5** |

The latch slightly REDUCES the alert rate, because extending a firing merges
nearby episodes rather than creating new ones. Better coverage at no cost.

Withdrawing a warning is more consequential than raising one -- people go back
indoors -- and it must never happen because a 3 h average moved.

### The antecedent-saturation model passes the gate by missing Chooralmala

The standard landslide-EWS formulation couples antecedent soil saturation with
a rainfall burst. Implemented and backtested on the five events:

| rule | recall | alerts/yr/site | gate <=2 |
|------|--------|---------------|----------|
| any window >= p99.25 (OR) | 5/5 | 12.9 | FAIL |
| any window >= p99.5 (OR) | 5/5 | 9.9 | FAIL |
| ante>=p97.5 AND burst>=p99 | 3/5 | 3.6 | FAIL |
| **ante>=p99 AND burst>=p99** | **3/5** | **2.0** | **PASS** |
| ante>=p99.5 AND burst>=p99.5 | 2/5 | 1.1 | PASS |

The AND rule is the first thing tested here that PASSES the alert-rate gate.
It does so by dropping these two:

    Chooralmala   antecedent p94.82   burst p99.59   MISS
    Koottickal    antecedent p98.56   burst p99.55   MISS

**Chooralmala's slope was not unusually primed.** Its antecedent sits at
p94.82 -- ordinary -- while its burst is p99.59. The rain fell in one night.

This corroborates the KSDMA gauge record from a completely independent
dataset: Vythiri read 27.6 mm on the morning of 29 July, and the preceding
week (104, 93, 104 mm) was WETTER than the day before the disaster and
produced no landslide. Two unrelated measurements, same conclusion.

So a model that requires antecedent wetness would have passed its false-alarm
gate by being blind to the deadliest landslide in Kerala's recent history.
It is implemented as a CONFIDENCE CO-SIGNAL (`primed_and_triggered`,
`pattern`) and never as a suppressor. `test_burst_on_unprimed_ground_still_
watches` guards it.

The general lesson is worth stating plainly: **a rule that improves the
alert-rate metric by discarding the founding case has not improved the
system.** Check which events a gain is bought from.

### Kerala routing needed streams, not rivers -- and a 3.3 km coordinate error

Two defects found while wiring the Kerala downstream product, both silent.

**1. The waterway query.** Reusing Nepal's `waterway=river` filter returned
ZERO branches for Chooralmala. Around the site, 27 of 30 mapped channels are
`waterway=stream`; the nearest mapped river is 3.5 km away. Nepal's glacial
valleys happen to have their channels tagged river, so the assumption survived
untested until Kerala. Debris flows START in headwater channels, and in the
Ghats those are streams.

Fixed by querying `^(river|stream)$` for Kerala: 2,305 ways -> 96,553 ways
(96 MB). Routing then produces exactly the right answer:

    0.1 km  Mundakai
    1.0 km  Puthumala
    1.5 km  Chooralmala
    2.4 km  Attamala

Those are the settlements destroyed on 30 July 2024, in channel order.
Koottickal likewise returns Kokkayar and Mundakkayam.

**2. The event coordinate was 3.3 km off**, and it changed a conclusion. At the
OSM Mundakai node instead of my approximation:

| | old coord | corrected |
|---|---|---|
| Chooralmala best percentile | p99.59 | **p99.84** |

Which reverses the ranking. This document previously said "Chooralmala, by far
the deadliest, has the faintest signal of the five" and reasoned from it. That
was an artefact. Chooralmala is the STRONGEST of the five; the weakest is now
Koottickal at p99.30 versus the old p99.27, so the operating point barely
moves -- but the narrative was wrong and had been repeated several times.

The finding that DOES survive: Chooralmala's antecedent is still low at the
corrected coordinate (48 h p94.01, 72 h p89.22 against 3 h p99.84). It remains
a burst on unprimed ground.

**Three of the five coordinates are still UNVERIFIED**, and name-matching
against OSM does not fix it: there are two Puthumalas in Kerala and the
searchable one is 268 km from the landslide. Kavalappara's coordinate reads
58 m of relief, implausible for a slope that killed 59. Correct coordinates
need a landslide inventory (GSI/KSDMA), not a gazetteer.

### Open-Meteo's free quota cannot fund a backtest and a terrain scan the same day

Building Phase 5b hit the rate limit twice, and the reason matters for anyone
operating this: **Open-Meteo prices requests by LOCATION, not by HTTP call.**
A 100-coordinate elevation request costs ~100 against the allowance, so
batching saves round-trips and no quota at all. Free tier is roughly
600 locations/min, 5,000/hour, 10,000/day.

Two design errors followed from not knowing that:

  1. sampling a 3x3 grid PER CELL re-fetched the same ground for every
     neighbour -- 29,286 points for 3,255 cells. Sampling one shared lattice
     and computing relief from neighbouring nodes costs 9x less for the same
     answer.
  2. no backoff on 429, so the scan died instead of waiting.

Both fixed (`terrain.PACE_SECONDS`, exponential backoff, lattice scan). But
the ten-year hourly archives the five-event backtest needs are themselves
expensive, and after those the daily allowance was gone. **The Kerala terrain
scan is 60% complete and the unscanned band is 11.0-12.8N -- which is
Wayanad.** Chooralmala, Puthumala and Vythiri have no watch cell.

That is recorded structurally, not just in prose: `data/kerala_cells.json`
carries a coverage record, `rain_watch.load_cells` returns it alongside the
cells, `coverage_warning()` renders it, and the /rain tab shows it as a
banner. The failure mode being guarded against is specific and dangerous --
**absence of a cell reads as "safe" when it means "not scanned"** -- and a
test asserts the warning exists whenever the scan is partial.

Finish the scan on a later day; it resumes from cache.

### Backtest: five Kerala disasters, not one. Recall is no longer n=1.

The n=1 recall problem is fixed by using Kerala's other major
rainfall-triggered landslide disasters. Replayed on Open-Meteo ERA5 hourly
with ~10 years of each site's OWN prior climatology (no leakage: the last 14
days before each event are excluded from the ladder).

Where each disaster sits in the model's own climatology, best window, using
the peak value in the 24 h before failure:

| event | dead | best percentile |
|-------|------|-----------------|
| Pettimudi (Rajamala) 2020-08-06 | 66 | p99.79 |
| Puthumala 2019-08-08 | 17 | p99.73 |
| Kavalappara 2019-08-08 | 59 | p99.56 |
| **Chooralmala/Mundakkai 2024-07-30** | **~250+** | **p99.30** |
| Koottickal/Kokkayar 2021-10-16 | ~35 | p99.27 |

**All five are inside the top 1%.** The signal is real and consistent -- this
is a much better result than the single-event work suggested, and it vindicates
percentile-of-own-climatology as the right transform.

But note which event is WEAKEST: Chooralmala, by a wide margin the deadliest,
sits at p99.30 while Pettimudi at p99.79. The model saw the worst disaster
least clearly. That is the orographic flattening again, and it is why absolute
thresholds were abandoned.

THE OPERATING POINT DOES NOT EXIST
-----------------------------------
To catch all five a detector must fire at p99.27, i.e. on 0.73% of all hours
= **64 hours/year at every monitored site**. Grouped into episodes (firing
hours within 48 h counted as one alert):

| percentile | recall | median lead | episodes/yr/site | gate <=2 |
|-----------|--------|-------------|------------------|----------|
| p99.5 | **5/5** | **28 h** | 9.9 | FAIL |
| p99.8 | 0/5 | -- | 5.0 | FAIL |
| p99.9 | 0/5 | -- | 2.9 | FAIL |
| p99.95 | 0/5 | -- | 1.6 | PASS |

The cliff between p99.5 and p99.8 is not noise: the events cluster at
p99.27-99.79, so any threshold above that band loses all of them at once.
**There is no setting that both detects and passes the gate.** Best case is
5/5 recall at 28 h median lead for ~10 alerts/year/site -- five times the gate.

WHAT THIS ACTUALLY LICENSES
----------------------------
    WATCH        yes. ~10 alerts/yr/site, 28 h median lead, 5/5 recall.
                 That is roughly IMD's own heavy-rain alert cadence.
    EVACUATION   no. Not at 10 false alarms a year.

The architecture is sound and the backtest is the evidence: terrain screen ->
percentile of own climatology -> multi-window accumulation -> 5/5 recall on
real disasters. What fails is the FEED, in the same way it has failed at every
previous step. On the KSDMA gauge the same design costs 0.7 alerts/year.

An earlier version of this backtest reported "43.8 fires/year" at every
percentile. That number was tautological -- p99.5 of hourly values fires on
0.5% of hours BY DEFINITION -- and counted hours rather than episodes. Count
episodes, or you are just restating the percentile.

### Every global model flattens the Ghats peak by the same factor

The earlier Open-Meteo verdict was reached on the ERA5 ARCHIVE, which is not
the product a live system would run. Re-tested on the operational forecast
API, six models, 5-9 Ghats stations, 92 days against KSDMA gauge truth.

Mean ratio to gauge on the three heaviest gauge days in the window:

    ecmwf_ifs025     0.38x        gem_seamless     0.31x
    gfs_seamless     0.23x        icon_seamless    0.43x
    jma_seamless     0.23x        ukmo_seamless    0.34x
    -------------------------------------------------------
    MULTI-MODEL MAX  0.47x   <- the most generous combination possible

Six independent global NWP systems, four national weather services, and they
all under-read Western Ghats orographic extremes by 2-4x. That is not a model
defect to be worked around by choosing a better one; it is a shared
resolution and physics limit. Taking the MAXIMUM of all six -- which is not a
defensible estimator, just an upper bound on what this data can offer --
still reaches only 47% of the gauge.

Bias correction cannot fix it: the ratio ranges 0.16x to 1.23x across the same
nine stations in the same 90 days. Munnar was over-read while Peermade was
under-read 6x two days apart. There is no stable bias to remove.

Operationally, against the calibrated gauge trigger (>=200 mm):

| feed | best achievable | gate <=2/yr |
|------|----------------|-------------|
| Open-Meteo ERA5 archive (1.43 yr) | 6.3 alerts/yr | FAIL |
| Multi-model max, live API (0.19 yr) | 5.1 alerts/yr | FAIL |
| KSDMA gauge (1.49 yr) | 0.7 alerts/yr | PASS |

Two independent estimates converge on **roughly 3x the gate**, not 10x. The
live multi-model max is the best free option that exists and it is close --
close enough that the remaining question is about the GATE, not the physics.

    For EVACUATION, 5/yr is not acceptable. Compliance collapses.
    For a district-level WATCH, 5/yr may well be, and Kerala already
    lives with more than that from IMD.

So the honest position is not "Open-Meteo is useless". It is: **it can support
a watch, it cannot support a village-level evacuation trigger**, because the
quantity it destroys is exactly the peak that separates a landslide night from
a wet one. Note also that the 0.19-year window contains ONE firing day, so
5.1/yr carries a very wide interval; do not quote it as precise.

Open-Meteo is CC BY 4.0 -- attribution is required in any product that uses it.

### The percentile hedge was right, and it is still not enough

The engine works in percentiles of local climatology specifically so that a
feed reading 7x low can stay usable IF it ranks days correctly. With 524 days
of KSDMA gauge truth at Vythiri that hedge is now testable rather than hoped
for. Open-Meteo (ERA5), same 08->08 windows, same location:

    Spearman rank correlation, gauge vs Open-Meteo    0.619

Real skill, but not enough skill. What it costs each feed to catch the event:

| feed | threshold | days fired | alerts/year | gate <=2 |
|------|-----------|-----------|-------------|----------|
| Open-Meteo ERA5 | 74.1 mm | 9 | **6.3** | FAIL |
| KSDMA gauge | 280.0 mm | 1 | **0.7** | PASS |

30 July 2024 is the wettest day of 524 by gauge. Open-Meteo ranks it **9th**.

And it does not merely under-read -- it invents. Two of Open-Meteo's ten
wettest days recorded **0.0 mm at the gauge**:

    2025-05-27   Open-Meteo 101.0 mm   gauge 0.0 mm
    2024-10-08   Open-Meteo  95.8 mm   gauge 0.0 mm

So the failure is two-sided: it misses the disaster and fabricates storms that
did not happen. No percentile transform repairs a ranking that is wrong in
both directions. **Rainfall triggering on open gridded data is closed off, and
this is the measurement that closes it** -- not resolution, not calibration,
not thresholds.

Open-Meteo keeps ONE defensible role: the slow antecedent-wetness variable,
where multi-day soil moisture is a large-scale signal and a 25 km grid is
appropriate. It must never be the trigger.

Note the shape of the two feeds, which is exactly inverted:

    seismic (USGS)   sees the event, publishes +13 h    -- timely: NO
    rainfall (grid)  publishes hourly, cannot see it    -- timely: YES

### There was no run-up. The signal existed only on the night itself.

Verified from 18 individually-hashed bulletins (18/18 distinct content):

    18 Jul   104.0 mm    <- HIGHER than the day before the disaster
    26 Jul    93.3 mm
    27 Jul   104.0 mm
    29 Jul    27.6 mm    <- 17 hours before the failure. Unremarkable.
    30 Jul   280.0 mm    <- landslides at 01:00 and 04:10

The week before the disaster contained heavier days than the day before it,
and produced no landslide. **The event fell out of a quiet day.** Two
consequences, both hard:

  1. A DAILY-cadence system cannot warn. Seventeen hours out, every Wayanad
     gauge read normal. There was nothing to fire on.
  2. The antecedent-wetness layer -- the one role left for Open-Meteo after
     the ranking test -- would not have fired either. It is not a fallback.

Sub-daily gauge data during the event is therefore not an enhancement to the
rainfall design. It IS the design; everything else is calibration.

### Defect: cache keys derived from URLs, twice

#### 1. keyed on basename

First run keyed the disk cache on the PDF basename.
KSDMA restarts its filename counter every month, so `Actual-Vs-Prediction-12`
exists under `/2024/07/` and `/2024/12/`: **754 of 800 URLs collided.** The
resulting climatology showed Vythiri at exactly 280.0 mm in December and
January, which is what exposed it. Fixed by keying on the full URL path.

#### 2. keyed on the full path -- still collided

macOS filesystems are case-insensitive and KSDMA's capitalisation is
inconsistent (`ACTUAL-Vs-PREDICTION-1.pdf` vs `Actual-Vs-Prediction-1.pdf`),
so **463 of 816 URLs still shared a cache file.** This one corrupted the
run-up table specifically -- the 29 July bulletin was in a collision group.

Fixed by keying on `sha256(url)`. Verified: 816 URLs -> 816 distinct cache
files; 768 distinct by content, the 48 repeats being genuine republishes.

The tell both times was physical implausibility, not a stack trace --
monsoon-magnitude rain in the Kerala dry season, then five consecutive
identical days. A cache that silently returns the wrong record produces
confident, wrong numbers and no error. Same class as the earlier overwrite of
a cached API response. **Derive cache keys by hashing the full identifier,
never by transforming it into a filename.**

There is no open, no-auth, real-time rainfall API for Kerala equivalent to
USGS FDSNWS. IMD publishes rainfall as JPEG bar charts.

## Known open defects

| ID | Defect | Impact | Status |
|----|--------|--------|--------|
| D1 | ~~`FIXED_DEPTHS` includes `0.0`~~ | ~~Founding event does not fire~~ | **CLOSED 2026-08-31.** `0.0` removed: it marks a surface source, not an unconstrained one. Cost across 12 years: **2 alerts, both true positives, zero new false positives.** `us7000tbwb` and `us7000tc90` now fire WARNING at score 95. |
| D2 | Revised catalogue records skip re-evaluation (`watcher.py` `if not is_new: continue`) | Default-depth event later revised shallow can never fire | Open |
| D3 | `usgs_type` parsed then discarded (`watcher.py`) | Only 2 `landslide` records in 573; strongest discriminator is unused and unaudited | Open |
| D4 | `min_magnitude=3.0` implies sensitivity the feed lacks (USGS floor is M4.0 here) | Sub-M4 collapses are structurally invisible | Open |
| D9 | **Earthquake-triggered collapses are structurally invisible.** The filter rejects anything above `max_magnitude` (6.5) as tectonic — correct in isolation, but it removes exactly the events that trigger the largest collapses, and the collapse itself produces no separate catalogued record. Langtang 2015 (~350 dead, triggered by Gorkha M7.8) and Huascarán 1970 (~20,000 dead, triggered by an offshore M7.9) both fail this way. Replayed against the real 36 hours around Gorkha — 76 catalogued events within 80 km — the detector produces **zero dispatches**. It is silent during the period of maximum landslide risk. | An entire event class, arguably the deadliest, cannot be seen. | **Open.** Needs a post-earthquake mode: after a large regional quake, *lower* the bar near mapped hazards rather than rejecting. |
| D8 | **Unconstrained depth was thrown away entirely.** Finding 1 rejected all 462 default-depth events outright, discarding location information that still mattered — a default-depth event sitting on a glacier is worth an operator's attention even though its depth is unknown. | 460 events per 12 years vanished from the record with no trace beyond a reject reason. | **CLOSED 2026-08-31.** Policy is now `cap`, not `reject`: unknown-depth events are scored on location and size, but capped at `watch` so they can never dispatch. **Dispatch rate unchanged (1.89/yr ceiling, GO); 453 previously-discarded events are now visible.** The cap is policy, not arithmetic — no reweighting can lift one into a dispatch tier. |
| D7 | **Hard thresholds compared against an uncertain location, and boundaries hidden in code.** `dist <= 15.0` made a perfect collapse signal a WARNING at 14.9 km and a WATCH at 15.1 km — 30 points across 200 m — while the location itself is uncertain by ~15 km. Separately, the inner ring (5 km), depth split (5 km) and magnitude band (3.5–6.0) were literals, so `config_version` could not reproduce a decision. | A hazard just outside the radius could never produce a warning; audit trail incomplete. | **CLOSED 2026-08-31.** All boundaries moved to config. Proximity now scores `P(source within radius)` under Gaussian location error (`proximity_model: smooth`); `step` retained for comparison. Gate unchanged at 1.89/yr — **but see the caveat below.** |
| D6 | **Source-location uncertainty routes the flood down the wrong branch.** The 2026 registry entry `"Langtang Lirung / Lhende"` names two drainages ~13 km apart across a divide; its coordinates pick Langtang. The Lende Khola / Bhote Koshi is a sibling tributary joining at the same confluence, so a single-branch corridor structurally excludes Rasuwagadhi — where the brief places the largest casualty concentration. Below the confluence 46 of 53 settlements are shared; above it, none are. | Near-field corridor can be 100% wrong while looking 87% right. | **MITIGATED 2026-08-31.** Routing now takes the union of every channel within `source_uncertainty_km` (15 km). Corridor 53 → 65 settlements; Rasuwa Gadhi, Resuo, Lingling, Khangjim now included. Registry entry still needs verification with ICIMOD/GFZ. |
| D5 | **Catalogue publication latency.** The record the detector fires on (M5.2, depth 0, `type=landslide`) was published **+13h06m** after the collapse — 12 hours *after* the SMS broadcast had already gone out (see the corrected timeline below). At 08:37 the feed carried M4.4 / `type=earthquake` / default depth, which the filter **rejects**. USGS's own note: the landslide characterisation required long-period analysis *and satellite imagery*. | **Catalogue-only detection cannot warn anyone for this event class.** Phase 1 is a measurement exercise only — it can never become a warning service on this feed. | **Open — blocks Phase 1 as a warning path.** Run `scripts/scenario_20260826.py`. |

| D13 | **The kill gate cached on the year alone.** `phase0_backtest.fetch_year` keyed its catalogue cache `usgs_{year}.json`, ignoring bbox and `min_magnitude`. Widening the box from the Nepal box to the Himalayan arc -- a 20x area change -- and re-running the gate replayed the OLD box's 573 events from disk and printed **GO**, with the new bbox printed in the header directly above the stale verdict. It was caught only because the totals came back as exactly 573 events and exactly 1.89/yr, the previously published numbers. The real fetch found **3,982 events**: the cache had been hiding 5x the data. | A kill gate that answers for a configuration you are no longer running is worse than no gate -- it launders a stale pass as a fresh one, and every downstream number inherits the laundering. | **CLOSED 2026-09-02.** Cache key now hashes bbox + `min_magnitude` + `end_cap`. Any config change that alters the result set misses the cache and refetches. |
| D14 | **One idle browser socket silenced the whole status page.** `hew.status` ran on a single-threaded `HTTPServer` with no read timeout. A browser opens speculative connections and sends nothing on them; the handler then blocked in `readline()` and the entire server stopped answering -- `/health` included -- for 30 s at a time. Confirmed with `lsof`: a Chrome preconnect socket held `127.0.0.1:8770` ESTABLISHED while every request timed out. A separate defect with the same symptom (SQLite rollback-journal lock contention, writer blocking the read-only page for its full 5 s busy timeout) masked the diagnosis; WAL fixed that one and the hang remained. | **Fail-safe, never fail-open.** The liveness endpoint went dark precisely while the watcher was healthy and polling, so an uptime check could not distinguish a working system from a dead one. | **CLOSED 2026-09-02.** `ThreadingHTTPServer` + `Handler.timeout = 10`, and `store.py` opens the database in WAL. `/health` 12/12 at 0.75 ms under live write load; `/rain` 27 s -> 38 ms. |
| D15 | **Registry and fetch box were the binding constraint on coverage, and were invisible as such.** The hazard registry and `config["bbox"]` both stopped at 27-30 N, 84-89 E. Events outside were never fetched, so they produced no candidate, no reject reason and no metric. Replayed: Kedarnath 2013 sat **510 km** outside, Chamoli/Rishiganga 2021 **449 km**, Namcha Barwa **602 km**, Nanga Parbat **1,059 km**. | An entire mountain range could not be seen, and nothing in any dashboard said so. A green HEALTHY badge over Uttarakhand meant only that nothing was looking. | **MITIGATED 2026-09-02.** Registry rebuilt Himalaya-wide from RGI 7.0 + NSIDC HMA: 5,906 -> **82,152** sites, all four events above now within 3.3 km of a mapped hazard, Langtang unchanged at 2.2 km. Box widened with a 0.5 deg margin so edge hazards keep a full detection radius. Built by `scripts/build_hazard_registry.py` -- the previous inventory had no build script, which is why its extent was never audited. **Phase 0 ceiling moved 1.89 -> 3.77 alerts/yr: TUNE, not GO. The gate is not passed.** |


## Exceptions

| ID | Rule | Path | Reason | Expires |
|----|------|------|--------|---------|
| — | none | | | |
