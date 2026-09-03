# Capability Map: Phase 2 — waveform detection

**Status: PROPOSED. Not approved. No module spec written yet.**

## Assumptions, and where each came from

| # | Assumption | Basis |
|---|---|---|
| 1 | Scope is **Nepal only** | Nearest open station to Chamoli is 625 km, to Kedarnath 635 km. Langtang 57 km, Rasuwagadhi 54 km, Melamchi 44 km. Measured against the EarthScope station list. |
| 2 | 4 usable stations near the founding event | KKN 57 km, KTNP2 65 km, KNSET 72 km, EVN 132 km — all currently open. |
| 3 | **Amplitude cannot discriminate** | Three background triggers in one week peaked at 68,812 counts; the collapse peaked at 49,082. No threshold separates them. Measured. |
| 4 | **Cross-station amplitude ratio cannot discriminate** | Response-corrected: collapse 0.57x, background 0.67x / 0.28x. Overlaps. Measured. |
| 5 | Association across stations is the discriminator | A local source triggers several stations with consistent moveout; station noise triggers one. NOT yet measured — this is the first thing to test. |
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
