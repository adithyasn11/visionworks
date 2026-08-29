# VisionWorks Phase 2 — Per-Employee Analytics with an Agentic Reasoning Layer

**Author:** Adithya S Nayak (AD16)
**Status:** Plan — not yet implemented
**Relationship to Phase 1:** additive. The anonymous pipeline is not modified.

---

## 1. What changes, and the one thing that must not

Phase 1 built an anonymous pipeline. `zone_minute_stats` has no column that can
hold a person, and `minute_aggregator.py` discards track ids by design.

Phase 2 adds **consented, per-employee attribution** and an **agentic reasoning
layer** on top. The critical architectural decision:

> The anonymous path is not removed or modified. Per-employee data lives in a
> **separate table, with a separate retention policy, gated on a consent
> record.** An employee who has not consented flows through exactly the Phase 1
> path and is counted anonymously.

This matters for three reasons:

1. It is the only design that is lawful under India's DPDP Act 2023 — consent
   must be specific, informed, and revocable.
2. A consent revocation becomes a **row delete**, not a pipeline rewrite.
3. It preserves the literature-survey argument. We still claim structural
   privacy; we now claim it *per subject*, which is a stronger and more
   defensible claim than blanket anonymity.

---

## 2. The identity problem, and how it is solved

### Why the current tracker cannot do this

`pose_estimator.py:59` uses `tracker="bytetrack.yaml"`. ByteTrack associates
detections by **motion only** — it has no appearance memory. When a person is
occluded for more than roughly 30 frames, they return with a **new track id**.

So a raw ByteTrack id is a *fragment* of a person's presence, not a person.
Counting chair-exits from raw track ids would count every occlusion as an exit.

### The fix: a three-stage identity model

```
Stage A (existing, unchanged):  ByteTrack  ->  track_id      (a fragment)
Stage B (new):                  Re-ID      ->  identity_id   (a person)
Stage C (new):                  Enrolment  ->  employee_id   (a named person)
```

**Stage B — Re-identification.** A lightweight appearance embedding
(OSNet-x0.25, about 2 MB, roughly 1 ms per crop on your GPU) produces a 512-d
vector per person crop. Fragments are stitched by cosine similarity against a
short-lived gallery. This converts N fragments into one stable `identity_id`
for a session.

Chosen over face recognition deliberately:

- It works from behind, at an angle, and at CCTV distance. Faces mostly do not.
- Clothing-based embeddings are **naturally session-scoped** — they stop
  working the next day when clothes change. That is a *feature*: it bounds the
  surveillance window to a single day without needing a policy to enforce it.

**Stage C — Enrolment.** A one-time, consented registration binds an
`identity_id` to an `employee_id`. Two options; build (a), document (b):

- **(a) Seat assignment — build this.** An employee is assigned a desk zone.
  The identity that occupies that seat longest during the session is bound to
  that employee. Zero biometrics. Works today with the zones you already have.
- **(b) Badge/RFID correlation — document only.** A badge tap at a door
  supplies a timestamp; the identity crossing that door within ±2 s is bound.

> **Honest limitation, to state in the report and the viva:** seat-assignment
> binding is accurate in assigned-desk offices and degrades in hot-desking
> environments. Measure the binding accuracy and report it. Do not hide it.

---

## 3. The AI agent layer — the part that makes this more than a CV project

An agent here is not a chatbot bolted onto the dashboard. It is a component
that **observes state, decides on its own what to investigate, calls tools, and
produces a judgement it can defend.** Four agents, each doing a job that would
otherwise need a human analyst.

### Agent 1 — Behaviour Interpreter (the core contribution)

**Problem it solves.** The current `activity_score` in
`activity_aggregator.py` is `0.75 x centroid-wiggle + posture-shift bonus`.
That measures *movement*, not work. A developer in deep focus scores near zero.
This is the single most attackable number in the project.

**What the agent does.** It receives a *behavioural trace* for one
employee-day — not video, not coordinates, but a symbolic sequence:

```
09:12-10:47  SEATED   desk_04    motion=low   posture_shifts=3
10:47-10:52  WALKING  corridor
10:52-11:31  SEATED   meeting_2  motion=low   occupancy=5
11:31-11:38  ABSENT
```

It reasons about what the pattern *means* and emits a structured
classification with a stated confidence and an explicit rationale:

```json
{
  "focus_blocks":         [{"start": "09:12", "end": "10:47", "quality": "deep"}],
  "meetings_attended":    1,
  "break_count":          3,
  "longest_unbroken_sit": 95,
  "fragmentation_index":  0.34,
  "assessment":           "Sustained morning focus; afternoon fragmented by short absences averaging 6 min.",
  "confidence":           0.78,
  "caveats":              ["11:31-11:38 absence unexplained - may be off-camera work, not a break"]
}
```

**Why this beats a formula.** A threshold rule cannot distinguish a bathroom
break from leaving early, or deep focus from an empty chair with a jacket on
it. An LLM reasoning over a symbolic trace can weigh context — and, critically,
it can say **"I am not sure, and here is why."** A formula never abstains. That
abstention is the research contribution.

### Agent 2 — Anomaly Investigator (multi-step, tool-using)

Runs on the aggregator's existing 60-second tick. When a metric deviates from
that zone's own 14-day baseline, it does **not** fire an alert immediately. It
opens an *investigation* and calls tools to find the cause:

| Tool | Returns |
|---|---|
| `get_zone_history(zone, days)` | the baseline for this zone |
| `get_adjacent_zones(zone)` | did people move, or actually vanish? |
| `get_camera_health(camera)` | dropped frames, detection confidence |
| `get_calendar_context(time)` | is it lunch, a holiday, a known meeting? |
| `compare_to_peer_zones(zone)` | zone-specific or building-wide? |

It reaches one of three verdicts — **real event**, **data-quality artefact**,
or **expected variation** — and only the first becomes an alert.

**Why this matters.** `alerts_engine.py` already documents that the whole
problem is debounce: 120 buckets producing 120 alerts. But debounce suppresses
*duplicates*; it cannot suppress a **false** alert. An occupancy drop caused by
a camera dropping frames is indistinguishable from a real one at the rule
level. Only investigation separates them. This is a directly measurable
improvement over the Phase 1 engine — report false-alert rate before and after.

### Agent 3 — Privacy Guardian (the defensive agent)

Sits **in front of** every per-employee query as a mandatory gate. Before any
individual data is returned, it evaluates:

- Does a valid, unrevoked consent record exist for this subject?
- Does the requester's role permit individual-level access (MANAGER and above)?
- Is the request within the retention window (default 7 days)?
- Would this result set be small enough to re-identify someone in a view that
  is supposed to be anonymous? (**k-anonymity: suppress if k < 5**)
- Is the access pattern itself suspicious — one manager repeatedly pulling one
  employee?

Every decision writes to `AuditLog`, which already exists. Denials are logged
with their reason.

**Why this is a strong viva answer.** When an examiner asks *"isn't this
surveillance?"*, the answer is not a promise. It is: *"there is an agent whose
only job is to refuse. Here is its audit log. Here are the queries it denied."*
This agent converts your biggest vulnerability into a demonstrable feature.

### Agent 4 — Insights Narrator (manager-facing)

Weekly, per team — never per individual in the default view. Composes a
briefing from `zone_day_stats` plus Agent 1 output: what changed, why it
probably changed, what to do about it, and what the data cannot tell you.
Writes to the existing `Report` model.

### Why four agents rather than one prompt

They differ in trust level, trigger, and failure mode — which is exactly when
separation is warranted. This is the justification to give in the viva:

| Agent | Trigger | Failure mode | Trust level |
|---|---|---|---|
| Behaviour Interpreter | end of day, batch | wrong label | advisory |
| Anomaly Investigator | 60 s tick | missed or false alert | advisory |
| Privacy Guardian | every query, synchronous | **data leak** | **enforcing** |
| Insights Narrator | weekly | bad prose | advisory |

The Guardian is the only one that can block, so it must be
**deterministic-first: hard-coded rules decide, and the LLM only explains the
decision.** An LLM must never be the sole thing standing between a query and
personal data — a prompt injection placed in an employee's name field would be
enough to defeat it.

---

## 4. Schema additions

Four new tables. `zone_minute_stats` is **not touched**.

```prisma
model Employee {
  id             String  @id @default(uuid()) @db.Uuid
  orgId          String  @db.Uuid
  employeeCode   String  @db.VarChar(64)   // HR id, not a person's name
  displayName    String  @db.VarChar(160)
  assignedZoneId String? @db.Uuid          // for seat-based enrolment
  active         Boolean @default(true)

  @@unique([orgId, employeeCode])
  @@map("employees")
}

/// The gate. No row here means no per-person row is ever written.
model MonitoringConsent {
  id            String    @id @default(uuid()) @db.Uuid
  employeeId    String    @db.Uuid
  grantedAt     DateTime  @db.Timestamptz(6)
  revokedAt     DateTime? @db.Timestamptz(6)
  scope         Json      // which metrics were consented to
  policyVersion String    @db.VarChar(32)

  @@map("monitoring_consents")
}

/// One row per employee per day. Deliberately NOT per-minute: minute-level
/// per-person data is the surveillance-grade artefact, and is never persisted.
model EmployeeDayStat {
  id                String   @id @default(uuid()) @db.Uuid
  orgId             String   @db.Uuid
  employeeId        String   @db.Uuid
  statDate          DateTime @db.Date

  presentMinutes    Int
  seatedMinutes     Int
  awayFromDeskCount Int
  breakMinutes      Int
  longestFocusBlock Int
  fragmentationIdx  Float

  agentAssessment   Json?    // Agent 1 output, with confidence and caveats
  bindingConfidence Float    // how sure we are this is the right person

  /// Deleted by the retention job after retentionDays. Default 7.
  expiresAt         DateTime @db.Timestamptz(6)

  @@unique([employeeId, statDate])
  @@map("employee_day_stats")
}

model AgentRun {
  id         String @id @default(uuid()) @db.Uuid
  orgId      String @db.Uuid
  agentName  String @db.VarChar(64)
  input      Json
  toolCalls  Json   // full trace - this is your evaluation evidence
  output     Json
  confidence Float?
  latencyMs  Int
  tokenCost  Int

  @@map("agent_runs")
}
```

`AgentRun` is not optional. It is how you demonstrate the agents actually
reasoned rather than guessed, and it is the dataset for your accuracy chapter.

**Retention.** `employee_day_stats` defaults to **7 days**. Aggregate trends
survive in the anonymous `zone_day_stats`, which already exists. Say this
explicitly in the demo — short retention on individual data is the point.

---

## 5. Build order

| # | Task | Depends on | Est. |
|---|---|---|---|
| 1 | Re-ID embedder + gallery stitching (`reid_tracker.py`) | — | 3 d |
| 2 | Measure fragment-stitch accuracy on real footage | 1 | 1 d |
| 3 | Schema migration (4 tables + RLS policies) | — | 1 d |
| 4 | Seat-assignment enrolment + binding confidence | 1, 3 | 2 d |
| 5 | Consent UI and revoke flow (frontend) | 3 | 2 d |
| 6 | Per-employee day aggregator (mirrors `minute_aggregator`) | 4 | 2 d |
| 7 | **Agent runtime**: tool registry, `AgentRun` logging, retries | 3 | 2 d |
| 8 | Agent 3 Privacy Guardian (rules first, LLM explains) | 7 | 2 d |
| 9 | Agent 1 Behaviour Interpreter | 6, 7 | 3 d |
| 10 | Agent 2 Anomaly Investigator + its five tools | 7 | 3 d |
| 11 | Agent 4 Insights Narrator | 9 | 1 d |
| 12 | Employee dashboard page + consent indicators | 6 | 3 d |
| 13 | Evaluation: agent vs. formula, false-alert rate | 9, 10 | 3 d |

**Roughly 28 working days.** Steps 1 through 8 give you a complete,
defensible, demonstrable system. Steps 9 through 13 are what turn it into a
strong final-year project.

Do **step 8 before step 9**. The Guardian must exist before anything can query
per-person data, or you will build the query path without the gate — and
retro-fitting it is far harder than building it in.

---

## 6. Dependencies to add

```
torchreid>=0.2.5      # or a standalone OSNet ONNX export
anthropic>=0.40.0     # agent runtime
scikit-learn>=1.4.0   # cosine similarity / gallery clustering
```

`ANTHROPIC_API_KEY` goes in `backend/.env` alongside the service-role key —
**backend only, never `NEXT_PUBLIC_`**.

---

## 7. Risks, stated honestly

| Risk | Reality | Mitigation |
|---|---|---|
| Re-ID accuracy | 70–85% on CCTV footage is realistic, not 99% | Report `bindingConfidence` on every row; suppress the day below 0.6 |
| Agent hallucination | An LLM will invent a plausible-sounding reason | Agent output is *advisory*; every claim links to the buckets it came from |
| Latency | LLM calls take roughly 2–5 s | Agents 1 and 4 are batch. Only Agent 3 is synchronous, and it is rules-first |
| Cost | Roughly ₹200–400/month at demo scale | Cache Guardian decisions; batch Agent 1 nightly |
| Legal | DPDP Act 2023 | Consent table, 7-day retention, revocation, audit log |
| Scope | 28 days is tight | Steps 1–8 are the shippable core; 9–13 degrade gracefully |

---

## 8. What to tell your sir

> We are adding per-employee monitoring, gated on recorded consent, with a
> 7-day retention window on individual data. Identity comes from appearance
> re-identification plus seat assignment rather than face recognition — which
> keeps it lawful under the DPDP Act 2023, and means it works from CCTV angles
> where faces are not visible.
>
> The AI-agent layer is four agents. The important one interprets behaviour:
> instead of scoring productivity with a movement formula — which would call a
> focused developer idle — an agent reasons over the day's behavioural trace
> and produces an assessment *with a confidence and stated caveats*. It can say
> "I do not know", which a formula cannot.
>
> A second agent investigates anomalies before alerting, so a camera dropping
> frames does not raise a false occupancy alarm. A third is a Privacy Guardian
> that gates every individual query and logs every decision — that is our
> answer to "isn't this surveillance". A fourth writes the weekly manager
> briefing.
>
> We evaluate the agents against the formula baseline and report where they
> disagree. That comparison is the research contribution.
