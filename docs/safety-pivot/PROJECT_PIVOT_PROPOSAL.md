# Proposal: Multi-Zone Safety Compliance Tracking via Cross-Camera Sequence Verification

**A revised direction for the VisionWorks final-year project**

---

## 1. Why the current direction was sent back, stated plainly

The original pitch was "CCTV analytics for workplace productivity" — detect
people, work out if they're sitting/standing/walking, and report how long each
employee spends at their desk.

The guide's objection was specific and correct, and it has two separate parts:

1. **The core assumption doesn't hold in a real office.** The system decides
   *who* someone is mostly by checking which desk they're sitting at
   (`assignedZoneId`, "seat prior"). That only works if people mostly sit at
   one assigned desk. Real employees are in meetings, at whiteboards, moving
   between rooms, or hot-desking — none of which this model handles. It was
   even measured and documented as a failure inside this project's own
   evaluation report (`FAILURE_CASES.md`): when three employees sit at *each
   other's* desks, the system names every single one of them wrong, and does
   so at 100% confidence. It doesn't fail quietly — it fails
   confidently and incorrectly, 528 times out of 528 in that test.

2. **"Time at desk" is not "productivity."** Even where desk-binding works
   perfectly, sitting still is not the same as working, and standing/walking
   is not the same as slacking off. A developer deep in focused work looks
   identical, on camera, to someone doing nothing. This project's own
   documentation admits this outright: *"a focused developer at a desk
   scores low by design... any deployment that uses this data for
   individual performance management is a misuse of the product."*
   Building a "productivity tracker" on top of a metric your own
   documentation says can't measure productivity is not a defensible
   final-year pitch.

Both objections are about the **application** of the technology, not the
technology itself. The detection, tracking, posture-classification,
identity-fusion, and cross-camera-matching engine underneath is solid,
tested, and — this is the important part — was never actually the
problem. What needs to change is *what question the system is being
asked to answer.*

This document proposes a new question, in a domain where the exact same
underlying engine is not just adequate but genuinely well-suited, and
where the "not enough depth" and "not novel enough" objections that sink
most re-pitched projects are addressed head-on.

---

## 2. The new idea, in one sentence

> **Instead of asking "is this employee productive," ask "did this
> worker follow the safety procedure they were required to follow, in
> the order and time frame the rules require" — verified automatically
> across multiple cameras covering a facility with restricted and
> hazardous zones.**

Concretely: a factory, warehouse, or industrial site has zones with real
safety rules attached to them — a chemical storage area, a machine floor, a
loading dock. Rules like:

- Nobody may enter the hazard zone without first passing through the
  PPE-check zone (helmet, gloves, vest confirmed).
- Nobody may remain inside the hazard zone longer than N minutes without
  a supervisor also being present.
- Everybody who enters must exit through the designated
  decontamination/sign-out zone, not through a side door.

The system watches multiple cameras covering these zones and automatically
verifies, per worker, per visit: **did they do it in the right order,
within a plausible amount of time, and did they exit correctly** — instead
of a human checking a paper logbook or reviewing hours of raw CCTV footage
after an incident.

---

## 3. Why this is a genuinely different, harder problem — not a relabelling

This is the part worth being precise about, because a guide will
immediately ask "isn't this just the same code with new zone names?" The
honest answer is: **partly, and that's a strength, not a weakness** — but
there is a real, unsolved piece of engineering being added on top.

### 3.1 What is being reused (and why reuse is good engineering, not laziness)

| Existing capability | Where it lives today | Reused as |
|---|---|---|
| Person detection + tracking (YOLOv8-pose + ByteTrack) | `detector_tracker.py`, `pose_estimator.py` | Unchanged — detects and tracks any person in any zone |
| Zone-containment (is a person inside a drawn polygon) | `spatial_engine.py` | Unchanged — a "hazard zone" is drawn exactly like a "desk zone" |
| Per-zone dwell time, entry/exit, debounce (ignore a 2-second flicker) | `identity_tracker.py`, chair-exit logic in `employee_aggregator.py` | Reused as "time spent in restricted zone" and "left zone" detection |
| Appearance re-identification across occlusion (OSNet embeddings) | `appearance.py`, `identity_tracker.py` | Unchanged — still needed to keep track of one worker as they move |
| Cross-camera handoff (is the person leaving camera A the same person arriving at camera B) | `handoff.py` | **This is the piece being extended — see 3.2** |
| Confidence scoring + "abstain rather than guess" | `identity_tracker.py`, `handoff.py` | Directly reused — critical for a safety system, see section 5 |

Reusing a well-tested detection and tracking engine and pointing it at a
new problem is exactly what real engineering teams do — nobody rebuilds
YOLO for a new use case. What a panel is actually assessing is whether
**the new part is real, and whether you understand why it's hard.** That's
section 3.2.

### 3.2 What is genuinely new: turning single hops into a verified journey

The existing cross-camera code (`handoff.py`) answers exactly one
question: *"is the person who just left camera A the same person who just
arrived at camera B?"* It does this using four combined signals — the
declared physical layout between cameras (a "handoff" is only even
considered if the two cameras are known to be connected, and only within a
plausible walking-time window), appearance similarity (OSNet embeddings,
weight 0.30), clothing colour (weight 0.20), and estimated height (weight
0.10), plus whatever the seat/identity signal already established (weight
0.40). These are combined into a single score, and a decision is made
*for that one hop only*.

The project's own design document is explicit that this is the least
reliable part of the whole system — expected accuracy of 60-75% for a
single hop, against 85-95% for simpler single-camera questions like "how
long were they at this desk." That is not a flaw to hide; it is the
reason this is a good thing to build a project around; it is a real,
acknowledged, partially-solved problem, not a solved one.

What does not exist anywhere in the current codebase is any logic for
**chaining several of these hops into one verified journey** — PPE-zone →
hazard-zone → exit-zone, in that specific order, within physically
sensible time gaps. This sounds like a small addition. It is not, for
three concrete reasons:

**Reason 1 — uncertainty compounds.**
If each individual hop is, say, 80% reliable, then naively multiplying
confidences across a four-step journey (0.8 × 0.8 × 0.8 × 0.8 ≈ 0.41)
makes almost every real, correct journey look unreliable by the time you
reach the end of it — even though nothing actually went wrong. A part of
this project is designing a sensible way to combine multi-step evidence
that doesn't collapse to near-zero for legitimate journeys, while still
being strict enough to catch genuine violations. This requires actual
design work: should later hops be weighted differently? Should corroborating
evidence (the same identity, confirmed at multiple points) count as
reinforcing rather than just multiplying? There is no existing answer to
copy — this is the open problem.

**Reason 2 — order and timing both have to be checked, not just presence.**
It is not enough to see that a worker visited all three zones at some
point during their shift. The system has to check they were visited in
the *correct order*, and that the *time between visits* was physically
plausible (not so fast it implies they skipped a step, not so slow it
implies they took an unauthorized detour). This is a sequence-validation
problem layered on top of the existing pairwise-matching problem —
genuinely a different kind of logic than anything currently in the
codebase.

**Reason 3 — a wrong accusation is worse than a missed one, and that has
to be designed for, not bolted on afterward.**
In a safety-compliance system, incorrectly telling a supervisor "worker
14 skipped the PPE check" when they didn't is a real workplace-discipline
problem — arguably worse here than in the original desk-tracking version,
because the consequence of being wrong is someone getting formally
reprimanded rather than just a wrong number on a dashboard. The existing
codebase already has an "abstain rather than guess" design principle
built deeply into it (see section 5) — extending that principle correctly
into a multi-step sequence, so the system says "insufficient evidence to
confirm or deny" rather than a false accusation, is itself a real design
contribution, not a rehash.

---

## 4. What the system would actually do, end to end

A concrete walkthrough, in plain terms:

1. **Setup (once):** cameras are placed to cover each zone that matters —
   entrance, PPE-check area, hazard zone, exit/decontamination area. Each
   camera's view is divided into zones exactly the way desks are divided
   into zones today, using the existing zone-drawing tool.

2. **Rules are declared (once, per facility):** a simple configuration
   describes the required sequence and constraints, for example:
   *"Zone: Hazard-1. Required-before: PPE-Check. Required-after:
   Decontamination. Max-time-inside: 45 minutes. Min-walk-time
   PPE-Check→Hazard-1: 5 seconds. Max-walk-time: 60 seconds."*

3. **Live operation:** as workers move through the facility, the existing
   detection and tracking engine sees them in each zone, exactly as it
   already does. The existing cross-camera handoff logic links their
   appearance across camera boundaries, exactly as it already does for a
   single hop.

4. **New logic — the journey checker:** for each worker's session, the
   system assembles the sequence of zones they were confidently linked
   through, checks it against the declared rule, and produces one of three
   outcomes:
   - **Compliant** — correct order, correct timing, confidently verified.
   - **Violation** — a required step was skipped, the order was wrong, or
     time inside a restricted zone exceeded the limit — flagged for a
     supervisor to review, with the supporting evidence (which cameras,
     what confidence, at what times).
   - **Unverifiable** — the evidence linking the steps together was too
     weak to be confident either way (e.g., a very crowded frame, or a
     handoff score below the acceptance threshold) — reported honestly as
     "could not confirm," not silently assumed either compliant or
     violating.

5. **Reporting:** a dashboard for safety officers showing violations,
   near-misses (time inside a hazard zone approaching the limit), and an
   audit trail per worker per shift — the kind of record that's actually
   useful after an incident, or for a compliance audit.

---

## 5. Why "abstain rather than guess" matters even more here

This project already has a well-tested design principle running through
every layer of its identity system: when the evidence isn't strong
enough, the system is built to say **"I don't know"** rather than make a
confident-sounding guess. This shows up as a hard confidence floor
(`IDENTITY_MIN_CONFIDENCE`, currently 0.5) below which an identity is
left unnamed rather than attributed to the nearest guess, and it is
tested explicitly — there are automated tests confirming that low-confidence
observations correctly land in an "unknown" bucket rather than being
silently folded into someone's record.

This principle transfers directly, and arguably matters *more*, in a
safety-compliance context:

- In the original desk-tracking framing, a wrong guess produces a wrong
  number on an analytics chart.
- In this framing, a wrong guess produces a false safety violation
  attributed to a real, named worker — a disciplinary or legal event.

So the same abstention design that was originally built as a privacy and
accuracy safeguard becomes the central *safety* safeguard of the new
system: it is specifically designed to never confidently accuse someone
of a safety violation on weak evidence. That is a strong, honest, and
genuinely defensible answer to give a guide or panel who asks "what if
your system is wrong?" — because the answer is "it says so, instead of
guessing," and that is provably true of the existing code, not just a
promise.

---

## 6. Is this realistic to build and demo?

Yes, with a scope that is achievable in the time available, because the
hardest and most time-consuming component — reliable detection, tracking,
and appearance-based re-identification — already exists, is already
tested, and does not need to be rebuilt.

**What genuinely needs to be built:**

1. A rule-definition format (a simple structured config — not a new UI
   framework, just a small schema).
2. The multi-hop confidence-combination logic (the core novel
   contribution — see section 3.2, reason 1).
3. Sequence and timing validation logic (section 3.2, reason 2).
4. A violation/compliance reporting view (a new screen, similar in
   complexity to dashboards that already exist in this codebase).
5. An evaluation methodology: since real factory footage with known ground
   truth is not available, the same approach this project already uses for
   evaluating its identity system applies directly — build a synthetic
   test facility with known correct answers (the existing evaluation
   harness in `backend/eval/` already does exactly this for identity
   tracking, including deliberately hard and adversarial scenarios) and
   measure accuracy, false-accusation rate, and abstention rate honestly,
   the same way `FAILURE_CASES.md` already does for the current system.

**What does not need to change:**

Detection, pose estimation, zone containment, single-hop cross-camera
matching, appearance re-identification, and the confidence-floor design —
all reused as-is.

**Demo feasibility:** this does not require an actual factory. Three or
four cameras (webcams or phone cameras, which this project already
supports via its existing phone-pairing feature) positioned around a
room, with zones marked out to represent "PPE check," "hazard zone," and
"exit," is a standard and fully legitimate way to demonstrate a system
like this for a final-year project — panels expect a scaled-down physical
demo, not an actual industrial site.

---

## 7. Why this satisfies "final year engineering project" expectations

To be direct about what a project panel is actually checking for, and how
this addresses each point:

- **Real-world relevance:** Workplace safety-zone compliance (PPE
  enforcement, restricted-area dwell-time limits, mandatory
  entry/exit sequencing) is an active, currently-growing commercial
  category — companies are already selling computer-vision safety
  compliance products to warehouses and factories today. This is not a
  contrived problem invented for a project; it is a real one with real
  commercial and safety motivation.

- **Technical depth beyond "I used YOLO":** The core technical
  contribution — combining uncertain multi-step evidence into a single
  trustworthy journey verdict, while remaining honest about what it
  doesn't know — is a real algorithm-design problem with no
  off-the-shelf answer, worth a full chapter of analysis and multiple
  design iterations, not a weekend's work.

- **Clear, defensible scope:** The line between "reused, tested
  infrastructure" and "new contribution" is explicit and honest (section
  3.1 vs 3.2) — nothing is being claimed as novel that isn't, and nothing
  genuinely new is being hidden as if it were trivial.

- **Doesn't repeat the previous objection:** This system does not depend
  on anyone staying at one assigned desk all day. It depends on workers
  passing through a small number of clearly-defined safety checkpoints in
  a required order — which is not an assumption about behaviour, it is
  the actual safety rule the facility is enforcing regardless of whether
  a camera is watching. The system is not squeezing a person into a model
  that doesn't fit them; it is checking compliance with a real,
  independently-existing rule.

- **Doesn't repeat the "productivity" measurement problem:** Nothing here
  claims to measure how well someone is working. It measures whether a
  specific, binary, safety-relevant procedure was followed — a
  fundamentally different and much more defensible kind of claim.

---

## 8. Suggested one-line project title

*"Sequence-Verified Multi-Camera Safety Compliance Monitoring: Confidence-Aware
Cross-Zone Worker Path Tracking for Industrial Safety Enforcement"*

(A shorter working title for everyday conversation: **"Safety Path
Verification"** or **"Zone Sequence Compliance System."**)

---

## 9. Suggested next steps

1. Confirm this direction with the guide before writing any code —
   specifically walk through sections 1, 3, and 7 of this document, since
   those directly answer why this is different from the rejected idea.
2. Define 3-5 realistic safety rules to use as the target scenarios (e.g.,
   PPE-before-hazard, max-dwell-time-in-hazard-zone,
   correct-exit-required) — keep the first version small and provably
   correct before adding more rules.
3. Design the multi-hop confidence formula on paper first, with worked
   examples, before writing code — this is the part a guide will want to
   see reasoned through carefully, not just implemented.
4. Reuse the existing evaluation-harness pattern
   (`backend/eval/simulate.py`, `backend/eval/metrics.py`) to build a
   synthetic test facility with known-correct answers, the same
   disciplined way the current identity system was evaluated — this is
   what makes the final report credible rather than anecdotal.
