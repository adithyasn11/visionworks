# VisionWorks — Failure Cases

_Step 18 of IDENTITY_TRACKING_PLAN.md. Generated from the evaluation run in `backend/eval/`; re-run `python -m backend.eval.failures` after any change and this file reflects the new behaviour._

> **Provenance.** These failures come from the synthetic office in `backend/eval/simulate.py`. The pipeline, the stitching, the seat binding, the confidence floor and the aggregation are the real production code; the appearance vectors are generated to distributions measured from real OSNet output (same person 0.877, different people 0.31). What this cannot tell you is whether OSNet separates your actual colleagues in your actual lighting — that needs footage, and the same commands regenerate this document from it.

## What went wrong, in one table

| Failure | Severity | Count |
|---|---|---|
| Wrong name given | **SERIOUS** | 528 |
| Declined to name a known employee | **BY DESIGN** | 161 |

**528 serious failures** across 94% coverage of 2649 scored observations.

The distinction in that table is the whole point. An abstention and a wrong name are both "the system did not produce the right answer", and treating them as the same thing is how a system that guesses confidently comes to look better than one that admits doubt. Every abstention below is time the interface reports as *unattributed* — visible, and excluded from the figures rather than folded into them.

## The three failure modes the plan names

§8 of the plan predicts specific problems. Each was built into the evaluation as a scenario, so what follows is measurement rather than speculation.

### 0. Hot-desking — the one that actually breaks it

**Setup.** Three employees sitting at each other's desks. The seat prior's core assumption is "exactly one employee has assignedZoneId == Z", and this violates it while leaving every other signal intact.

**Result.** **This is the system's worst failure, and it fails confidently.** Every person is named as the owner of the desk they are sitting at, at confidence 1.00 — the seat prior is measuring time-in-zone, which is genuinely 100%, so the confidence is *correct about the wrong question*. Nothing in the interface would suggest a problem: the figures look like a normal, well-attributed day. The confidence floor cannot catch this, because the evidence for the wrong answer is strong. Only a second independent signal can — a face match at the door (Step 10) would contradict the seat prior and force an abstention. **Deployments with hot-desking need face enrolment; the seat prior alone is not sufficient and this measurement is the proof.**

| measured | value |
|---|---|
| correct | 0 |
| wrong names | 528 |
| abstained | 12 |
| accuracy of answers | 0.0% |
| coverage | 97.8% |
| fragments → identities | 3 → 3 for 3 people |
| wrong joins | 0 |

### 1. Similar clothing

**Setup.** Two employees in identical shirts, appearance vectors at cosine ~0.86 — far above the 0.65 re-ID gate, i.e. genuinely indistinguishable by appearance.

**Result.** **At their own desks the system is unaffected.** The seat prior settles it and appearance never has to arbitrate, which is the plan's §3 claim (85-95% from seating alone) demonstrated. **In a shared area the confidence falls** rather than the answer becoming wrong: the same people who read 0.9+ at their desks drop toward the 0.5-0.6 band once the seat prior is gone. That is the correct behaviour — the system is less sure, and says so.

| measured | value |
|---|---|
| correct | 232 |
| wrong names | 0 |
| abstained | 8 |
| accuracy of answers | 100.0% |
| coverage | 96.7% |
| fragments → identities | 2 → 2 for 2 people |
| wrong joins | 0 |

### 2. Occlusion

**Setup.** Somebody walks behind a pillar three times. ByteTrack issues a new track id on every reappearance.

**Result.** The identity survives. Raw fragments are stitched back into one person by the appearance gallery, which is the entire purpose of Step 6 — without it, every occlusion would read as a chair exit.

| measured | value |
|---|---|
| correct | 226 |
| wrong names | 0 |
| abstained | 8 |
| accuracy of answers | 100.0% |
| coverage | 96.6% |
| fragments → identities | 5 → 2 for 2 people |
| wrong joins | 0 |

### 3. Cross-camera colour shift

**Setup.** The same shirt reads differently on the pantry camera (histograms shifted 25%), which the plan calls out as the reason not to trust colour alone.

**Result.** The handoff still succeeds, because OSNet carries the match when colour degrades — exactly the asymmetry §8 predicts. The fusion weights (0.40 seat / 0.30 OSNet / 0.20 colour / 0.10 height) are what make this survivable.

| measured | value |
|---|---|
| correct | 571 |
| wrong names | 0 |
| abstained | 8 |
| accuracy of answers | 100.0% |
| coverage | 98.6% |
| handoffs correct | 10 of 10 |
| fragments → identities | 22 → 22 for 2 people |
| wrong joins | 0 |

## Where the system declines to answer

161 observations of a known employee produced UNKNOWN. These are not errors; they are the confidence floor doing its job. Grouped by scenario:

| scenario | abstentions | why |
|---|---|---|
| normal_day | 77 | brief absences, and the first ~20 s before the binding gate is met |
| chair_exits | 44 | away from the desk, so the seat prior weakens |
| hot_desking | 12 | — |
| occlusion | 8 | the frames immediately after a reappearance |
| similar_clothing | 8 | in the shared area, where only appearance is available and two people look alike |
| cross_camera | 8 | in the corridor and pantry, which are nobody's desk |
| stranger | 4 | the opening seconds before enough evidence accumulates |

A pattern worth stating plainly: **almost every abstention is somebody away from their assigned desk.** The seat prior is the strongest signal this system has, and without it the confidence falls below the floor rather than the answer becoming wrong. Somebody with no desk assigned at all is measured, but rarely named — which the team view shows as a dash rather than a zero.

## Serious failures, individually

Grouped by where they came from:

| scenario | serious failures |
|---|---|
| hot_desking | 528 |

**Every serious failure in this run comes from one scenario.** Outside hot-desking the system produced no wrong names at all. That is worth stating precisely rather than averaging away: the weakness is not general unreliability, it is a specific unsupported deployment pattern, and the fix for it (face enrolment at a door camera) already exists in Step 10.

A sample of the individual cases:

| # | scenario | t | truth | said | confidence | method |
|---|---|---|---|---|---|---|
| 1 | hot_desking | 20s | emp-a | emp-b | 1.00 | seat |
| 2 | hot_desking | 20s | emp-b | emp-c | 1.00 | seat |
| 3 | hot_desking | 20s | emp-c | emp-a | 1.00 | seat |
| 4 | hot_desking | 25s | emp-a | emp-b | 1.00 | seat |
| 5 | hot_desking | 25s | emp-b | emp-c | 1.00 | seat |
| 6 | hot_desking | 25s | emp-c | emp-a | 1.00 | seat |
| 7 | hot_desking | 30s | emp-a | emp-b | 1.00 | seat |
| 8 | hot_desking | 30s | emp-b | emp-c | 1.00 | seat |
| 9 | hot_desking | 30s | emp-c | emp-a | 1.00 | seat |
| 10 | hot_desking | 35s | emp-a | emp-b | 1.00 | seat |
| 11 | hot_desking | 35s | emp-b | emp-c | 1.00 | seat |
| 12 | hot_desking | 35s | emp-c | emp-a | 1.00 | seat |
| 13 | hot_desking | 40s | emp-a | emp-b | 1.00 | seat |
| 14 | hot_desking | 40s | emp-b | emp-c | 1.00 | seat |
| 15 | hot_desking | 40s | emp-c | emp-a | 1.00 | seat |
| 16 | hot_desking | 45s | emp-a | emp-b | 1.00 | seat |
| 17 | hot_desking | 45s | emp-b | emp-c | 1.00 | seat |
| 18 | hot_desking | 45s | emp-c | emp-a | 1.00 | seat |
| 19 | hot_desking | 50s | emp-a | emp-b | 1.00 | seat |
| 20 | hot_desking | 50s | emp-b | emp-c | 1.00 | seat |
| 21 | hot_desking | 50s | emp-c | emp-a | 1.00 | seat |
| 22 | hot_desking | 55s | emp-a | emp-b | 1.00 | seat |
| 23 | hot_desking | 55s | emp-b | emp-c | 1.00 | seat |
| 24 | hot_desking | 55s | emp-c | emp-a | 1.00 | seat |
| 25 | hot_desking | 60s | emp-a | emp-b | 1.00 | seat |
| 26 | hot_desking | 60s | emp-b | emp-c | 1.00 | seat |
| 27 | hot_desking | 60s | emp-c | emp-a | 1.00 | seat |
| 28 | hot_desking | 65s | emp-a | emp-b | 1.00 | seat |
| 29 | hot_desking | 65s | emp-b | emp-c | 1.00 | seat |
| 30 | hot_desking | 65s | emp-c | emp-a | 1.00 | seat |
| 31 | hot_desking | 70s | emp-a | emp-b | 1.00 | seat |
| 32 | hot_desking | 70s | emp-b | emp-c | 1.00 | seat |
| 33 | hot_desking | 70s | emp-c | emp-a | 1.00 | seat |
| 34 | hot_desking | 75s | emp-a | emp-b | 1.00 | seat |
| 35 | hot_desking | 75s | emp-b | emp-c | 1.00 | seat |
| 36 | hot_desking | 75s | emp-c | emp-a | 1.00 | seat |
| 37 | hot_desking | 80s | emp-a | emp-b | 1.00 | seat |
| 38 | hot_desking | 80s | emp-b | emp-c | 1.00 | seat |
| 39 | hot_desking | 80s | emp-c | emp-a | 1.00 | seat |
| 40 | hot_desking | 85s | emp-a | emp-b | 1.00 | seat |


## Limits this evaluation does not cover

Stated here because a failure document that only lists what was measured implies everything else was checked.

- **No real footage.** Every figure above comes from simulation. The tooling to label and measure real video exists (`python -m backend.eval.label --video <file> --extract`) and has been run against real video end to end, but no office recording with known people at known desks exists yet.
- **Face recognition is not evaluated.** Step 10's door-camera matching needs enrolled faces and door footage. The gates are unit-tested (`backend/tests/test_door_matching.py`, 35 checks) but no end-to-end face accuracy figure is claimed.
- **A full 8-hour day is untested.** The longest scenario is 25 minutes. The plan's §0 says a full day with zero identity swaps is rare, and nothing here contradicts or confirms that.
- **One person per desk is assumed, and the assumption is not self-checking.** The seat prior refuses a zone with two registered claimants, but it cannot tell that the person sitting there is not the person registered to it — see the hot-desking measurement above, where it is confidently wrong 528 times. A deployment that hot-desks needs face enrolment for this to be safe.

---

_Generated from `backend/eval/results/evaluation.json`. Overall: 76% accurate on the 88% of cases above 0.75 confidence, correctly abstaining on the rest._
