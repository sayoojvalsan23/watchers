# Running the Phase 1 watcher

Measured footprint (2026-09-02, on a Pi 5, live against USGS):

    startup (82,152 sites + 20,183 river ways + 115,075 places)   0.9 s
    one poll cycle                                                1.03 s
    resident memory                                               231 MB
    duty cycle at 60 s polling                                    1.7% of one core
    USGS requests per day                                         1,440

The earlier figure here was **51 MB**, measured against 331 lakes, 388 river
ways and 854 places -- a single basin. Widening to the Himalayan arc took the
same process to **1,493 MB**, which is 37% of a 4 GB Pi and would not start at
all on a Pi Zero. It is back to 231 MB because the river layer is now loaded
from a packed binary instead of parsed from JSON; see the routing commit.

Anyone sizing hardware from the old number would have bought the wrong board,
which is why it is recorded here rather than quietly overwritten.

## Run a persistent process, not a scheduler

`watcher.run()` owns its own 60 s poll / 5 min heartbeat / 1 h canary loop and
handles SIGTERM. It wants to be left running.

Schedulers are worse *and* more complicated for this: GitHub Actions `schedule:`
has a five-minute minimum and is queue-delayed, and per-invocation platforms pay
a cold start 1,440 times a day to do a one-second job.

## Where

| Option | Cost | Verdict |
|--------|------|---------|
| Your laptop (`local.hew-watcher.plist`) | free | Start here. Only flaw is sleep gaps, which look identical to failures in the heartbeat log. |
| A machine you own, left on — old laptop, Raspberry Pi | ~$50 once | Best for the 60-day run. No third-party terms that can change under you. 231 MB with the full Himalayan layer, so a 1 GB Pi 3 is comfortable; a 512 MB Pi Zero is not. |
| Cheap VPS | ~$4/month | Fine. Buys away a class of failure. |
| **Oracle Cloud Always Free** | free | **No — see below.** |

## Why not Oracle Always Free

Their terms reclaim instances idle at **under 10% CPU and under 10% network
over a 7-day period**. This watcher runs at **1.7% CPU** and sends 1,440 small
requests a day: under both thresholds, on both axes. It would be classified as
idle and stopped.

The property that makes this system cheap to run is the one that gets it
reclaimed.

Also: on 15 June 2026 Oracle halved the Always Free Ampere limits to 2 OCPU /
12 GB and shut down existing instances until users resized them; and
"Out of host capacity" on ARM A1 is common enough that people automate retries
just to provision.

Phase 1's exit criterion is *60 days, no unexplained gaps*. A host that
silently stops the process makes that criterion unmeasurable — a reclamation
is indistinguishable from a crash in the heartbeat log.

Defeating the idle check with synthetic load is possible and is the wrong
answer: do not build a reliability measurement on a host that is trying to
stop you.

## Dispatch stays off

Phase 1 measures. It does not warn. Neither `local.hew-watcher.plist` nor
`hew-watcher.service` passes `--allow-dispatch`, and neither should.
