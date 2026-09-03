# Capability Map: Phase 2 — waveform detection

**Status: PROPOSED. Not approved. No module spec written yet.**

## Assumptions, and where each came from

| # | Assumption | Basis |
|---|---|---|
| 1 | Scope is **Nepal only** | Nearest open station to Chamoli is 625 km, to Kedarnath 635 km. Langtang 57 km, Rasuwagadhi 54 km, Melamchi 44 km. Measured against the EarthScope station list. |
| 2 | 4 usable stations near the founding event | KKN 57 km, KTNP2 65 km, KNSET 72 km, EVN 132 km — all currently open. |
| 3 | **Amplitude cannot discriminate** | Three background triggers in one week peaked at 68,812 counts; the collapse peaked at 49,082. No threshold separates them. Measured. |
| 4 | **Cross-station amplitude ratio cannot discriminate** | Response-corrected: collapse 0.57x, background 0.67x / 0.28x. Overlaps. Measured. |
| 5 | Association across stations is the discriminator | **MEASURED 2026-09-03, and it holds.** Collapse: KKN +55.7 s, EVN +79.1 s, moveout 23.4 s over 75 km = **3.2 km/s, physically valid**. Two background cases: moveout 593 s and 283 s = 0.1 and 0.3 km/s, **physically impossible**. Separation is an order of magnitude and needs no tuned threshold. |
| 6 | SeedLink gives seconds latency | FDSN dataselect measured at ~30 min. SeedLink is assumed faster and is UNVERIFIED. |
| 7 | Runs as a separate service from `hew.watcher` | Different latency budget (continuous stream vs 60 s poll) and different failure modes. Shares the ledger, not the process. |
| 8 | Dispatch stays OFF | Protected invariant. Phase 2 changes what is SEEN, not who is TOLD. |

Correct any of these now — 5 and 6 are the load-bearing unknowns.

## Modules

| Module id | Responsibility | Depends on |
|---|---|---|
| `waveform-ingest` | SeedLink client, ring buffer, gap and reconnect handling | — |
| `trigger` | STA/LTA per station, emits candidate onsets | `waveform-ingest` |
| `associate` | Group onsets across stations by moveout; reject single-station noise | `trigger` |
| `locate` | Coarse origin from associated arrivals | `associate` |
| `hazard-gate` | Is the origin on a mapped hazard? Reuses the existing registry and routing | `locate` |
| `escalate` | Operator notice; writes to the existing ledger | `hazard-gate` |

Build order: `waveform-ingest` → `trigger` → `associate` → `locate` → `hazard-gate` → `escalate`

## The kill gate for Phase 2

Paired, like Phase 0, and it is a gate not a target:

- **False-trigger rate** after association, measured on a month of real background
- **Recall** on Langtang 2026 and Melamchi 2021, on real waveforms

Measured baseline to beat: **8,030 raw triggers/year** at one station. If association
does not cut that by three orders of magnitude, this is a research project and
should be stopped rather than shipped.

## Stop condition

`associate` is the whole bet. If it does not separate the founding event from
background on real data, nothing downstream is worth building. It is second in
the build order for that reason: fail fast, cheaply.


## Result of the stop-condition test (2026-09-03)

`associate` PASSES its first test. Moveout consistency separates the founding
event from background where amplitude (headroom 0.71x) and long-period ratio
(2.43 vs 2.31) both failed. It is a physics test rather than a tuned
threshold, which is why it is worth building on.

Caveats, none of them fatal but all of them real:

- n = 1 event and 2 background cases. Enough to justify continuing, nowhere
  near enough to gate on.
- Only **2 of the 4 nearby stations returned data** (KTNP2 and KNSET gave
  nothing for these windows). Two stations test CONSISTENCY; they cannot
  LOCATE. A hyperbola is not a point. `locate` needs three.
- Envelope-peak picking is crude. A real onset picker will move these numbers.
- The false-trigger rate AFTER association is still unmeasured. The 8,030/yr
  single-station baseline is what it has to beat, and that needs a month of
  background, not an afternoon.

## Design note: a THREAT tier above WARNING

If waveform detection reaches operational quality, the dashboard banner
already has the precedence ladder to carry it:

    NOT LOOKING  >  THREAT  >  WARNING  >  WATCH  >  NOTHING DETECTED

THREAT would mean "a waveform-confirmed source, on a mapped hazard, seconds
after it happened" -- a different evidence class from WARNING, which is a
catalogue record that may be hours old. The subtitle should name the source
so the two are never confused, e.g. "waveform association, 2 stations,
3.2 km/s, 20 s after origin".

It stays unbuilt until `associate` and `locate` have a measured false rate.
A tier that sounds more urgent than WARNING must not be the least evidenced
thing on the page.
