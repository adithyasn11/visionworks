# VisionWorks: Existing Features and Missing Safety-Compliance Features

## 1. Purpose of this document

This document maps the current VisionWorks codebase to the proposed project:

> **End-to-End Workplace Safety Compliance Monitoring System**

The proposed system should monitor workers across multiple camera views, verify
required PPE, track movement through configured zones, validate safety
procedures, and display traceable compliance results.

This document separates:

1. What is already implemented.
2. What is partially implemented and can be reused.
3. What is missing and must be developed.
4. What should be reduced or repositioned from the original workplace-analytics
   idea.
5. A recommended implementation sequence.

The existing codebase is not starting from zero. It already contains a working
video analytics platform. The main work is adding the safety-specific detection,
event, rule, journey, and reporting layers.

---

## 2. Current project identity

The current project is primarily:

> **Vision-Based Workplace Activity Analytics**

It currently measures:

- People detected in video.
- Occupancy in configured zones.
- Sitting, standing, and walking posture.
- Per-track dwell time.
- Movement and activity scores.
- Floor-plan positions and heatmaps.
- Appearance-based identity continuity.
- Cross-camera handoffs.
- Ergonomic and workplace activity alerts.
- Analytics dashboards and reports.

It does not yet provide a complete safety-compliance workflow for:

- Helmet detection.
- Safety-vest detection.
- PPE-to-person association.
- PPE checkpoint verification.
- Restricted-zone entry compliance.
- Multi-step safety journeys.
- Compliance, violation, and unverifiable decisions.

---

## 3. Proposed safety-monitoring workflow

The target system should follow this pipeline:

```text
Video or camera input
        ↓
Person and PPE detection
        ↓
Single-camera tracking
        ↓
PPE-to-person association
        ↓
Zone containment and entry/exit events
        ↓
Appearance identity stitching
        ↓
Cross-camera handoff
        ↓
Worker journey construction
        ↓
Safety-rule verification
        ↓
Compliance decision
        ↓
Safety event database
        ↓
Dashboard, alerts, evidence, and reports
```

Example scenario:

```text
Camera A: PPE checkpoint
Camera B: Warehouse corridor
Camera C: Restricted work zone

Required procedure:
Worker must be observed wearing a helmet and safety vest
before entering the restricted work zone.
```

Possible final outcomes:

```text
COMPLIANT
POTENTIAL_VIOLATION
UNVERIFIABLE
```

The `UNVERIFIABLE` state is important. The system should not accuse a worker
when the video, PPE evidence, or cross-camera identity match is insufficient.

---

## 4. What is already built

## 4.1 Video input and processing

### Status: Built

The backend can receive and process several video sources.

Relevant files:

- `backend/app/api/routers/video_upload.py`
- `backend/app/api/routers/websocket.py`
- `backend/app/cv/stream_reader.py`
- `backend/app/api/routers/pairing.py`
- `frontend/app/camera/[code]/CameraClient.jsx`
- `frontend/app/components/PhoneCameraPanel.jsx`

Supported input modes include:

- Uploaded video files.
- Server-side webcam processing.
- Browser camera frames.
- Phone camera pairing.
- WebSocket-based real-time processing.

The current processing flow is:

1. Receive a video or frame.
2. Decode it with OpenCV.
3. Run computer-vision inference.
4. Track detected people.
5. Generate numeric telemetry.
6. Send live results to the frontend.
7. Persist selected telemetry.
8. Discard frames after processing, subject to configured evidence/privacy
   behaviour.

### Reusable for safety monitoring

This already supports the intended multi-camera demonstration:

- Camera A for the PPE checkpoint.
- Camera B for corridor movement.
- Camera C for restricted-zone monitoring.

### Additional work needed

The safety version should add:

- Camera purpose or role values such as `PPE_CHECKPOINT`, `CORRIDOR`,
  `RESTRICTED_ZONE`, and `EXIT`.
- Camera clock/timestamp handling.
- Consistent camera identifiers across all events.
- Camera-to-camera transition configuration.
- Optional synchronization validation when multiple recordings are used.

---

## 4.2 Person detection

### Status: Built

The project uses Ultralytics YOLO to detect people.

Relevant files:

- `backend/app/cv/pose_estimator.py`
- `backend/app/cv/detector_tracker.py`

The person detector:

- Detects the COCO person class.
- Returns bounding boxes.
- Returns detection confidence.
- Uses CUDA when available.
- Falls back to CPU.
- Supports YOLO tracking with ByteTrack.

Typical detection data looks like:

```python
{
    "track_id": 12,
    "bbox": [x1, y1, x2, y2],
    "confidence": 0.91
}
```

The pose pipeline additionally returns:

```python
{
    "track_id": 12,
    "bbox": [x1, y1, x2, y2],
    "confidence": 0.91,
    "keypoints": ...,
    "posture": "STANDING"
}
```

### Reusable for safety monitoring

Person detection is the base for:

- Finding workers.
- Associating PPE with a particular worker.
- Monitoring zone occupancy.
- Detecting zone entry and exit.
- Building worker journeys.

### Important limitation

The existing person model does not automatically detect PPE. Detecting a
person is not the same as detecting:

- Helmet.
- Safety vest.
- Gloves.
- Goggles.
- Boots.
- Harness.

PPE detection must be added separately.

---

## 4.3 Single-camera tracking

### Status: Built

ByteTrack is configured through Ultralytics:

```python
tracker="bytetrack.yaml"
```

Relevant files:

- `backend/app/cv/pose_estimator.py`
- `backend/app/cv/detector_tracker.py`

Within one camera stream, a worker can be represented by a stable track ID:

```text
Camera A:
Track 12 → Track 12 → Track 12
```

### Reusable for safety monitoring

Single-camera tracking can be used to:

- Monitor a worker while inside a camera view.
- Detect when a worker enters a zone.
- Detect when a worker leaves a zone.
- Measure time spent in a restricted area.
- Associate several PPE observations with one tracked worker.

### Limitation

Track IDs are local to a camera. The following does not prove identity:

```text
Camera A / Track 12
Camera B / Track 4
```

The existing appearance and handoff layers help solve this problem, but the
result remains probabilistic and must be represented with confidence.

---

## 4.4 Pose estimation and posture classification

### Status: Built

`backend/app/cv/pose_estimator.py` uses YOLO pose inference and geometric
analysis.

It includes:

- 17 COCO keypoints.
- Shoulder, hip, knee, and ankle geometry.
- Bounding-box proportions.
- Torso/thigh relationships.
- Motion speed.
- Temporal smoothing.
- Sitting, standing, and walking classification.

The rolling history reduces posture flicker between frames.

### Reusable for safety monitoring

Posture is not the main safety feature, but it can support:

- Identifying whether a worker is walking through a corridor.
- Distinguishing a stationary worker from a moving worker.
- Supporting restricted-zone movement analysis.
- Filtering low-quality movement events.

### Current role in the safety project

Posture should be treated as supporting telemetry, not as the main compliance
decision. The safety system should not claim that posture measures productivity.

---

## 4.5 Polygon zones and spatial mapping

### Status: Built

`backend/app/cv/spatial_engine.py` provides:

- Polygon zone loading.
- Shapely point-in-polygon checks.
- Zone containment.
- Ground-point calculation.
- Homography transformation.
- Floor-plan projection.
- Proportional fallback when calibration is unavailable.

The current pipeline identifies a zone with logic equivalent to:

```python
zone_id = spatial_engine.check_zone_containment(centroid)
```

Points outside all configured polygons are assigned:

```text
TRANSIT_ZONE
```

The frontend already includes a zone editor:

- `frontend/app/components/ZoneEditor.jsx`

### Reusable for safety monitoring

Existing polygons can represent:

- PPE checkpoint.
- Corridor.
- Restricted work zone.
- Hazard zone.
- Exit/decontamination zone.
- Loading zone.
- No-entry zone.

The geometric operation is already implemented. A restricted zone is still a
polygon, just with different safety metadata.

### Additional work needed

Zone records need safety properties, for example:

```json
{
  "zone_id": "restricted_zone_1",
  "zone_name": "Restricted Work Zone",
  "zone_type": "RESTRICTED",
  "requires_ppe": true,
  "required_before": ["ppe_checkpoint"],
  "required_exit": "decontamination_zone",
  "max_dwell_seconds": 2700
}
```

The system also needs explicit zone transition events rather than only
periodic zone snapshots.

---

## 4.6 Dwell time and activity aggregation

### Status: Built, reusable with changes

`backend/app/cv/activity_aggregator.py` tracks:

- First-seen time.
- Last-seen time.
- Recent positions.
- Posture history.
- Posture transitions.
- Motion speed.
- Dwell time.
- Activity score.

`backend/app/db/activity_writer.py` persists sampled telemetry instead of
writing every frame.

Current stored activity information includes:

- Organisation ID.
- Camera ID.
- Zone ID.
- Track ID.
- Posture state.
- Activity score.
- Dwell duration.
- Floor-plan coordinates.

### Reusable for safety monitoring

This supports:

- Time spent in the restricted zone.
- Time between PPE checkpoint and restricted-zone entry.
- Time between restricted-zone entry and exit.
- Near-limit dwell warnings.
- Maximum-dwell violations.

### Missing safety behaviour

The current aggregator does not create events such as:

```text
entered_ppe_checkpoint
ppe_verified
left_ppe_checkpoint
entered_restricted_zone
restricted_zone_dwell_warning
restricted_zone_dwell_exceeded
exited_restricted_zone
entered_decontamination_zone
missing_required_exit
```

A safety event-transition layer must be added.

---

## 4.7 Appearance re-identification

### Status: Built

Relevant files:

- `backend/app/cv/appearance.py`
- `backend/app/cv/identity_tracker.py`
- `backend/app/cv/signature_registry.py`

The system uses appearance evidence such as:

- OSNet-style embeddings.
- Upper-body colour features.
- Lower-body colour features.
- Height estimates.
- Bounding-box information.

`identity_tracker.py` can:

1. Reuse an identity for an existing track.
2. Compare a new track with recently lost identities.
3. Apply an appearance similarity gate.
4. Apply a physical reachability check.
5. Reattach fragmented tracks.
6. Create a new identity when evidence is insufficient.

The identity code intentionally supports abstention:

> When confidence is too low, keep the identity unknown instead of guessing.

### Reusable for safety monitoring

This is a major reusable component. It can help connect:

```text
Camera A → Camera B → Camera C
```

It can carry PPE evidence from the checkpoint to a later restricted-zone
entry, provided the identity connection is sufficiently reliable.

### Limitation

The current identity system was designed around workplace activity analytics,
assigned zones, employees, and optional face recognition. It does not yet
store a safety journey such as:

```text
Worker passed PPE checkpoint at 10:00:01
Worker entered restricted zone at 10:00:15
```

The journey model must be added above the existing identity layer.

---

## 4.8 Cross-camera handoff

### Status: Built as a pairwise layer

Relevant file:

- `backend/app/cv/handoff.py`

The current handoff module considers:

- Declared camera topology.
- Minimum travel time.
- Maximum travel time.
- Appearance similarity.
- Clothing colour.
- Height.
- Existing identity evidence.
- Candidate margins.
- Confidence thresholds.

The current logic answers:

> Is the person arriving at camera B probably the person who left camera A?

It does not answer:

> Did this worker complete the full required PPE-to-restricted-zone journey?

### Reusable for safety monitoring

Camera topology and handoff constraints can represent:

```text
PPE checkpoint → corridor
corridor → restricted zone
restricted zone → exit
```

The existing abstention behaviour is particularly important because a wrong
worker match can produce a false safety accusation.

### Missing extension

A journey layer must:

- Collect accepted handoffs.
- Preserve handoff confidence.
- Reject impossible event order.
- Validate travel-time constraints across multiple hops.
- Combine confidence without simply multiplying every score into an unusable
  number.
- Keep uncertain journeys as `UNVERIFIABLE`.

---

## 4.9 Face enrolment and employee identity

### Status: Built, optional for safety

Relevant files:

- `backend/app/cv/face_enroller.py`
- `backend/app/cv/face_identifier.py`
- `backend/app/api/routers/enrolment.py`
- `frontend/app/dashboard/employees/`

The project supports employee records and door-camera face identification.
Camera roles include concepts such as `DOOR` and `AREA`.

### Reusable for safety monitoring

This can support:

- Optional named workers.
- Employee enrolment.
- A named safety audit trail.
- Review of repeated events for an employee.

### Recommendation

Do not require face recognition for the first safety prototype. A worker can
initially be represented as:

```text
identity_42
unknown_worker
```

The safety decision should be based on evidence and procedure compliance, not
on forcing a name when identity confidence is weak.

---

## 4.10 Privacy and anonymisation

### Status: Built

Relevant files:

- `backend/app/cv/anonymizer.py`
- `backend/app/cv/frame_pipeline.py`

The project already supports:

- Head/face blurring.
- In-memory frame processing.
- Privacy-by-default behaviour.
- Numeric telemetry storage.
- Per-session blur settings.
- Anonymised frames sent to the frontend.

The frame pipeline intentionally blurs the frame in place before it is
encoded and transmitted.

### Reusable for safety monitoring

This supports:

- Privacy-by-design.
- Safer workplace deployment.
- Reduced storage of personal data.
- Human review using anonymised evidence.

### Safety-specific decision

The current system discards most raw frames. Safety review may need evidence
for an alert. The recommended prototype approach is:

- Store only blurred event snapshots.
- Capture them only for safety events.
- Link them to a safety event.
- Apply a short retention period.
- Do not store continuous raw footage.

---

## 4.11 Backend API and application structure

### Status: Built

The backend is FastAPI-based.

Main application:

- `backend/app/main.py`

Registered routers include:

- Cameras.
- Zones.
- Analytics.
- WebSockets.
- Video upload.
- Employee enrolment.
- Phone pairing.

The application also:

- Creates local database tables.
- Applies lightweight migrations.
- Starts the minute aggregator.
- Starts identity-related persistence.
- Configures CORS.
- Handles Windows connection-disconnect logging.

### Reusable for safety monitoring

The current backend is an appropriate foundation for:

- PPE inference endpoints.
- Safety event endpoints.
- Rule configuration endpoints.
- Compliance result endpoints.
- Evidence retrieval.
- Safety dashboard APIs.

---

## 4.12 Local database and telemetry persistence

### Status: Built, requires safety tables

Relevant files:

- `backend/app/db/models.py`
- `backend/app/db/database.py`
- `backend/app/db/activity_writer.py`
- `backend/app/db/minute_aggregator.py`
- `backend/app/db/identity_writer.py`

Current local models include:

### `cameras`

Stores:

- Camera ID.
- Organisation ID.
- Camera name.
- RTSP URL.
- Target FPS.
- Status.
- Camera role.
- Inference width.

### `zones`

Stores:

- Zone ID.
- Organisation ID.
- Camera ID.
- Zone name.
- Zone type.
- Polygon coordinates.
- Homography matrix.

### `activity_logs`

Stores:

- Timestamp.
- Organisation ID.
- Camera ID.
- Zone ID.
- Track ID.
- Posture state.
- Activity score.
- Dwell duration.
- Normalised floor position.

### Supabase/PostgreSQL features

The project also includes:

- Authentication.
- Organisations.
- Memberships.
- Role-based access.
- Row-Level Security.
- Employees.
- Cameras and zones.
- Alerts.
- Audit history.
- Retention settings.

### Missing safety data structures

The current activity log is not sufficient for a safety audit. New structures
are required.

Minimum recommended structures:

#### `ppe_observations`

```text
id
organisation_id
camera_id
track_id
worker_identity_id
timestamp
helmet_detected
helmet_confidence
vest_detected
vest_confidence
overall_status
evidence_reference
```

#### `safety_events`

```text
id
organisation_id
worker_identity_id
camera_id
zone_id
event_type
timestamp
confidence
metadata
evidence_reference
```

#### `worker_journeys`

```text
id
organisation_id
worker_identity_id
started_at
ended_at
status
confidence
reason
```

#### `safety_rules`

```text
id
organisation_id
name
target_zone
required_ppe
required_previous_zone
required_exit_zone
min_transition_seconds
max_transition_seconds
max_dwell_seconds
enabled
```

#### `compliance_decisions`

```text
id
journey_id
rule_id
status
reason
confidence
created_at
review_state
```

---

## 4.13 Existing alert engine

### Status: Built, needs safety rule support

Relevant file:

- `backend/app/db/alerts_engine.py`

The current alert engine supports:

- `SEDENTARY`
- `OVERCROWDING`
- `UNDERUTILISATION`
- `CAMERA_OFFLINE`

It includes:

- Thresholds.
- Sustained-duration checks.
- Cooldowns.
- Open-alert suppression.
- Severity.
- Alert acknowledgement.
- Organisation-scoped alert writing.

### Reusable for safety monitoring

The alert mechanisms can be extended for:

- Missing PPE before restricted-zone entry.
- Restricted-zone dwell exceeded.
- Missing required exit.
- Unverifiable handoff.
- Camera coverage problems.

### Limitation

The current alerts are mostly based on minute-bucket analytics. Safety events
often need immediate evaluation at the time of zone entry. The safety system
needs event-triggered alerts in addition to periodic aggregation.

---

## 4.14 Frontend and dashboards

### Status: Built as a general analytics dashboard

Relevant frontend areas include:

- `frontend/app/dashboard/`
- `frontend/app/components/AnalyticsCharts.jsx`
- `frontend/app/components/FloorplanHeatmap.jsx`
- `frontend/app/components/ZoneEditor.jsx`
- `frontend/app/components/VideoCanvasPlayer.jsx`
- `frontend/app/dashboard/AlertsPanel.jsx`
- `frontend/app/dashboard/cameras/topology/`
- `frontend/app/dashboard/employees/`
- `frontend/app/dashboard/evaluation/`

Existing frontend functionality includes:

- Authenticated dashboard.
- Live camera display.
- Video playback and processing.
- Zone editing.
- Occupancy analytics.
- Posture charts.
- Heatmaps.
- Alerts.
- Reports.
- Camera topology management.
- Employee management.
- Evaluation views.
- Organisation and team administration.

### Reusable for safety monitoring

The existing UI provides the application shell and many reusable components.

### Missing safety-specific UI

The following should be added:

1. Safety overview.
2. Compliance counts.
3. Potential violation feed.
4. Unverifiable event feed.
5. PPE status display.
6. Worker journey timeline.
7. Camera-to-camera handoff evidence.
8. Restricted-zone dwell warnings.
9. Blurred evidence snapshots.
10. Safety-rule configuration.
11. Safety reports.
12. Human review and acknowledgement.

---

## 4.15 Authentication, organisations, roles, and security

### Status: Built

The current application includes:

- Supabase authentication.
- Cookie-based sessions.
- Organisation membership.
- Admin, manager, and viewer roles.
- Platform operator support.
- Server-side permission checks.
- PostgreSQL Row-Level Security.
- Audit records.
- Retention settings.

### Reusable for safety monitoring

These controls can be applied directly:

| Role | Safety access |
|---|---|
| Admin | Configure rules, cameras, zones, members, and retention |
| Manager/Safety officer | View alerts, review violations, run analysis, export reports |
| Viewer | Read safety dashboards and reports |
| Platform operator | Manage system health without reading organisation telemetry |

No major authentication redesign is required for the safety pivot.

---

## 5. What is missing and must be built

## 5.1 PPE detection

### Status: Missing

The system needs a model that detects at least:

- Person.
- Helmet.
- Safety vest.

Optional future classes:

- Gloves.
- Goggles.
- Boots.
- Harness.
- Face shield.

Possible approaches:

1. Use a suitable pretrained PPE model.
2. Fine-tune YOLO on a public PPE dataset.
3. Collect and label a small project-specific dataset.

The model must be tested on the actual camera viewpoints. A model trained on
clear close-up images may fail on distant workers or poor lighting.

Suggested new module:

```text
backend/app/cv/ppe_detector.py
```

---

## 5.2 PPE-to-person association

### Status: Missing

The system must decide which helmet or vest belongs to which person.

A basic association strategy can use:

- Bounding-box overlap.
- Helmet position in the upper region of a person box.
- Vest position in the torso region.
- Distance between detection centres.
- Detection confidence.

The system must not conclude that all workers are compliant merely because one
helmet exists somewhere in the frame.

Suggested new module:

```text
backend/app/cv/ppe_association.py
```

Example output:

```json
{
  "track_id": 12,
  "helmet": {
    "detected": true,
    "confidence": 0.91
  },
  "safety_vest": {
    "detected": false,
    "confidence": 0.38
  },
  "status": "UNVERIFIABLE"
}
```

Recommended PPE states:

```text
COMPLIANT
NON_COMPLIANT
UNVERIFIABLE
```

Low-confidence evidence should not automatically be classified as a definite
violation.

---

## 5.3 PPE evidence history

### Status: Missing

One frame may be blurred, occluded, or incorrectly detected. The system needs
to accumulate evidence across multiple frames.

The PPE evidence layer should:

- Store recent helmet observations.
- Store recent vest observations.
- Use temporal smoothing.
- Require sufficient observation duration.
- Record the strongest and average confidence.
- Mark evidence as stale after a configurable time.
- Preserve whether evidence was clear or uncertain.

Example:

```text
Helmet:
  detected in 8 of last 10 clear observations
  mean confidence: 0.88

Vest:
  detected in 7 of last 10 clear observations
  mean confidence: 0.84

PPE result: COMPLIANT
```

---

## 5.4 Safety zone event generation

### Status: Missing

The current system knows a person’s current zone but does not create a
complete event stream for safety procedures.

Required events include:

```text
ENTERED_PPE_CHECKPOINT
PPE_EVIDENCE_RECORDED
PPE_VERIFIED
PPE_UNVERIFIABLE
LEFT_PPE_CHECKPOINT
ENTERED_CORRIDOR
ENTERED_RESTRICTED_ZONE
RESTRICTED_DWELL_WARNING
RESTRICTED_DWELL_EXCEEDED
EXITED_RESTRICTED_ZONE
ENTERED_DECONTAMINATION_ZONE
MISSING_REQUIRED_EXIT
CAMERA_HANDOFF_ACCEPTED
CAMERA_HANDOFF_REJECTED
```

The event detector must handle:

- Zone changes.
- Brief boundary flicker.
- Track disappearance.
- Track reappearance.
- Camera transitions.
- Duplicate event suppression.
- Event timestamps.

Suggested new module:

```text
backend/app/safety/safety_events.py
```

---

## 5.5 Safety-rule configuration

### Status: Missing

The current alert rules do not describe multi-step safety procedures.

Rules should be configurable rather than hardcoded. Example:

```yaml
rules:
  - name: PPE before restricted-zone entry
    target_zone: restricted_zone
    required_previous_zone: ppe_checkpoint
    required_ppe:
      - helmet
      - safety_vest
    min_evidence_confidence: 0.75

  - name: Restricted-zone dwell limit
    target_zone: restricted_zone
    max_dwell_seconds: 2700

  - name: Required exit procedure
    target_zone: restricted_zone
    required_exit_zone: decontamination_zone
```

The rule configuration should support:

- Required PPE.
- Required previous zones.
- Required next zones.
- Correct event order.
- Minimum travel time.
- Maximum travel time.
- Maximum zone dwell time.
- Evidence confidence thresholds.
- Grace periods.
- Enabled/disabled status.
- Rule severity.

Suggested modules:

```text
backend/app/safety/rules.py
backend/app/safety/schemas.py
```

---

## 5.6 Worker journey construction

### Status: Missing

The existing handoff module solves individual camera transitions. A new
journey builder must combine those transitions into one worker visit.

Example journey:

```text
10:00:01  PPE checkpoint
10:00:08  Corridor
10:00:15  Restricted zone
10:27:20  Restricted-zone exit
10:27:40  Decontamination zone
```

The journey builder should retain:

- Worker identity.
- Camera IDs.
- Local track IDs.
- Zone IDs.
- Event timestamps.
- PPE evidence.
- Handoff confidence.
- Rejected handoffs.
- Missing stages.
- Dwell durations.

Suggested module:

```text
backend/app/safety/journey_tracker.py
```

---

## 5.7 Multi-hop confidence combination

### Status: Missing

The existing code has pairwise handoff scores. It does not yet define how
several uncertain hops become one journey confidence.

The new design must avoid two opposite errors:

1. Treating every weak match as definitely correct.
2. Multiplying many scores until every legitimate journey becomes nearly zero.

Possible design:

- Preserve each hop score separately.
- Require every critical hop to meet a minimum floor.
- Add corroborating evidence from repeated observations.
- Apply penalties for rejected or missing stages.
- Use a minimum confidence threshold for a named compliance decision.
- Return `UNVERIFIABLE` when evidence does not meet the threshold.

The system should show the evidence components rather than only one opaque
number.

Example:

```json
{
  "journey_confidence": 0.81,
  "handoffs": [
    {
      "from": "camera_a",
      "to": "camera_b",
      "score": 0.86
    },
    {
      "from": "camera_b",
      "to": "camera_c",
      "score": 0.78
    }
  ],
  "decision": "UNVERIFIABLE",
  "reason": "Restricted-zone entry identity match below safety threshold"
}
```

---

## 5.8 Compliance decision engine

### Status: Missing

The system needs an engine that evaluates a journey against a safety rule.

Required outputs:

### `COMPLIANT`

Use when:

- Required PPE evidence is sufficient.
- Required zone order is observed.
- Camera handoffs are sufficiently reliable.
- Timing constraints are satisfied.
- No violation condition occurred.

### `POTENTIAL_VIOLATION`

Use when:

- PPE was confidently missing.
- The worker entered before the PPE checkpoint.
- The required zone was skipped.
- The worker exceeded the restricted-zone dwell limit.
- The required exit was not used.

### `UNVERIFIABLE`

Use when:

- PPE evidence is too weak.
- The worker is heavily occluded.
- A camera handoff is uncertain.
- A camera was offline.
- Timestamps are not reliable.
- Required evidence is missing.

Suggested module:

```text
backend/app/safety/compliance_engine.py
```

The engine must retain an explanation for every decision.

Example:

```json
{
  "status": "POTENTIAL_VIOLATION",
  "reason": "Worker entered restricted zone without sufficient helmet evidence",
  "confidence": 0.89,
  "supporting_events": [
    "ppe_checkpoint_observation",
    "restricted_zone_entry"
  ]
}
```

---

## 5.9 Safety event and decision persistence

### Status: Missing

Periodic activity logs are not enough for safety auditability.

The database should preserve:

- What happened.
- When it happened.
- Which camera observed it.
- Which worker identity was used.
- Which local track IDs contributed.
- What PPE was detected.
- Which rule was evaluated.
- What decision was made.
- Why it was made.
- What confidence was available.
- Which evidence supports the result.
- Whether a human reviewed it.

Suggested files:

```text
backend/app/db/safety_models.py
backend/app/db/safety_writer.py
backend/app/db/safety_schemas.py
```

---

## 5.10 Safety evidence snapshots

### Status: Missing

The system currently emphasises discarding frames and storing numeric
telemetry. A safety review workflow may need visual evidence.

Recommended prototype behaviour:

- Capture a blurred snapshot when a significant safety event occurs.
- Store only the snapshot associated with the event.
- Do not store continuous raw video.
- Apply retention limits.
- Restrict evidence access by organisation and role.
- Record when evidence was viewed or acknowledged.

Evidence may include:

- PPE checkpoint frame.
- Restricted-zone entry frame.
- Camera handoff frame.
- Restricted-zone exit frame.

If evidence storage is not acceptable, the system can store metadata only, but
the dashboard must clearly state that visual evidence is unavailable.

---

## 5.11 Safety alerts

### Status: Partially reusable

The existing alert engine can provide the base for safety alerts, but it needs
new event-driven evaluators.

Required safety alert types could include:

```text
MISSING_PPE
RESTRICTED_ZONE_ENTRY
RESTRICTED_ZONE_DWELL_EXCEEDED
REQUIRED_EXIT_MISSING
UNVERIFIABLE_JOURNEY
CAMERA_COVERAGE_FAILURE
```

The existing cooldown and acknowledgement behaviour should be reused to avoid
repeated alerts for one continuous situation.

Safety alerts should include:

- Severity.
- Worker identity or unknown identity.
- Rule name.
- Camera.
- Zone.
- Time.
- Reason.
- Confidence.
- Evidence link.
- Review state.

---

## 5.12 Safety dashboard

### Status: Missing as a dedicated experience

The current dashboard is designed for activity analytics. A safety dashboard
should include:

### Safety summary

- Total journeys.
- Compliant journeys.
- Potential violations.
- Unverifiable journeys.
- Active restricted-zone workers.
- Open safety alerts.
- Near-limit dwell cases.

### Event feed

Each event should show:

- Time.
- Camera.
- Zone.
- Worker identity or unknown identity.
- PPE status.
- Rule result.
- Confidence.
- Review status.

### Journey timeline

Display:

- PPE checkpoint.
- Corridor.
- Restricted-zone entry.
- Restricted-zone dwell.
- Restricted-zone exit.
- Decontamination or required exit.
- Travel time between events.
- Handoff scores.

### Evidence view

Display:

- Blurred event frame.
- Person bounding box.
- Helmet and vest detections.
- Camera and zone.
- Rule explanation.
- Confidence breakdown.

### Rule configuration

Allow authorised users to configure:

- Required PPE.
- Zone order.
- Timing windows.
- Restricted-zone dwell limits.
- Required exits.
- Thresholds.
- Alert severity.

---

## 5.13 Safety reports

### Status: Missing

The current reports focus on activity telemetry. Safety reports should include:

- Compliance rate.
- Potential violation count.
- Unverifiable case count.
- Missing helmet count.
- Missing vest count.
- Restricted-zone dwell violations.
- Missing-exit violations.
- Violations by camera.
- Violations by rule.
- Average journey duration.
- Handoff acceptance rate.
- PPE precision and recall.
- False-accusation rate.
- Abstention rate.

Reports should distinguish:

```text
Confirmed evidence
Potential violation
Insufficient evidence
Human-reviewed result
```

---

## 5.14 Safety evaluation

### Status: Missing

The existing evaluation material focuses on identity and activity analytics.
The safety project requires labelled safety scenarios.

Recommended scenarios:

### Scenario A: Correct PPE

```text
Worker wears a helmet and safety vest.
Worker passes the PPE checkpoint.
Worker enters the restricted zone.
Expected result: COMPLIANT.
```

### Scenario B: Missing helmet

```text
Worker wears a vest but no helmet.
Worker enters the restricted zone.
Expected result: POTENTIAL_VIOLATION.
```

### Scenario C: Missing vest

```text
Worker wears a helmet but no vest.
Worker enters the restricted zone.
Expected result: POTENTIAL_VIOLATION.
```

### Scenario D: Poor evidence

```text
Worker is too far away or occluded at the checkpoint.
Expected result: UNVERIFIABLE.
```

### Scenario E: Similar clothing

```text
Two workers look similar across cameras.
Expected result: abstention or UNVERIFIABLE when the match is weak.
```

### Scenario F: Excessive dwell

```text
Worker remains in the restricted zone beyond the configured limit.
Expected result: RESTRICTED_ZONE_DWELL_EXCEEDED.
```

### Required evaluation metrics

- Person detection precision and recall.
- Helmet detection precision and recall.
- Vest detection precision and recall.
- PPE-to-person association accuracy.
- Zone-entry detection accuracy.
- Zone-exit detection accuracy.
- Cross-camera handoff accuracy.
- Journey reconstruction accuracy.
- Compliance classification accuracy.
- Violation precision.
- False-accusation rate.
- Unverifiable/abstention rate.
- Alert latency.

---

## 6. Features to reduce or reposition

## 6.1 Activity score

The current activity score measures movement and posture transitions. It should
not be presented as worker productivity.

For the safety version, it may be used as:

- Movement telemetry.
- A corridor-motion indicator.
- A supporting signal for event validation.

It should not be used to judge work performance.

## 6.2 Sitting and standing analytics

These are useful to the original workplace analytics idea but are not central
to PPE compliance.

They can remain as secondary analytics, but the main dashboard should focus on:

- PPE.
- Zone procedures.
- Restricted-zone access.
- Dwell limits.
- Compliance decisions.

## 6.3 Desk and seat identity binding

The current identity code includes assigned-zone or seat-prior logic. This is
not suitable as the primary identity mechanism in a warehouse or factory.

The safety version should prefer:

- Appearance matching.
- Camera topology.
- Travel-time constraints.
- Optional door-camera recognition.
- Explicit confidence thresholds.
- Abstention when evidence is insufficient.

## 6.4 Sedentary alerts

Existing sedentary alerts may remain for the original analytics area, but they
should not be described as the main safety contribution.

The main alert system should focus on:

- Missing PPE.
- Restricted-zone violations.
- Maximum dwell violations.
- Missing procedure steps.
- Unverifiable journeys.
- Camera failures affecting safety coverage.

---

## 7. Recommended target architecture

The proposed safety architecture is:

```text
OpenCV video capture
        ↓
YOLO person detector
        ↓
YOLO PPE detector
        ↓
ByteTrack per camera
        ↓
PPE-to-person association
        ↓
SpatialEngine zone containment
        ↓
Zone transition event generator
        ↓
Appearance identity tracker
        ↓
Cross-camera HandoffRegistry
        ↓
Worker JourneyTracker
        ↓
Safety RuleEngine
        ↓
ComplianceEngine
        ↓
Safety event and decision persistence
        ↓
FastAPI safety endpoints
        ↓
Next.js safety dashboard
```

Existing modules to reuse:

```text
backend/app/cv/pose_estimator.py
backend/app/cv/detector_tracker.py
backend/app/cv/spatial_engine.py
backend/app/cv/activity_aggregator.py
backend/app/cv/appearance.py
backend/app/cv/identity_tracker.py
backend/app/cv/handoff.py
backend/app/cv/frame_pipeline.py
backend/app/api/routers/video_upload.py
backend/app/api/routers/analytics.py
backend/app/db/activity_writer.py
backend/app/db/alerts_engine.py
frontend/app/components/ZoneEditor.jsx
frontend/app/dashboard/AlertsPanel.jsx
frontend/app/dashboard/cameras/topology/
```

Suggested new modules:

```text
backend/app/cv/ppe_detector.py
backend/app/cv/ppe_association.py
backend/app/safety/safety_events.py
backend/app/safety/journey_tracker.py
backend/app/safety/rules.py
backend/app/safety/compliance_engine.py
backend/app/db/safety_models.py
backend/app/db/safety_writer.py
backend/app/api/routers/safety.py
frontend/app/dashboard/safety/
```

---

## 8. Recommended implementation order

## Phase 1: Convert the zone model to safety zones

Reuse the current zone editor and spatial engine.

Add:

- PPE checkpoint zone.
- Corridor zone.
- Restricted zone.
- Exit/decontamination zone.
- Zone safety metadata.
- Entry and exit transitions.

Deliverable:

```text
The system can reliably record who entered and left each configured zone.
```

## Phase 2: Add PPE detection

Add a PPE model for:

- Person.
- Helmet.
- Safety vest.

Implement:

- PPE-to-person association.
- Confidence thresholds.
- Temporal evidence.
- Compliant, non-compliant, and unverifiable PPE states.

Deliverable:

```text
The system can assess PPE for one worker in one camera view.
```

## Phase 3: Persist safety events

Create:

- PPE observations.
- Zone entries.
- Zone exits.
- Dwell warnings.
- Dwell violations.
- Handoffs.
- Identity uncertainty events.

Deliverable:

```text
The system has a traceable safety event history.
```

## Phase 4: Implement the first rule

Start with one rule:

> A worker must have sufficient helmet and safety-vest evidence before
> entering the restricted zone.

Deliverable:

```text
The system can produce COMPLIANT, POTENTIAL_VIOLATION, or UNVERIFIABLE.
```

## Phase 5: Add journey verification

Combine:

- PPE checkpoint evidence.
- Camera handoff.
- Restricted-zone entry.
- Restricted-zone exit.
- Confidence and timing.

Deliverable:

```text
The system can reconstruct and validate one multi-camera worker journey.
```

## Phase 6: Add more safety rules

Add:

- Restricted-zone maximum dwell.
- Required exit zone.
- Minimum and maximum transition times.
- Multiple PPE requirements.
- Camera coverage failure handling.

## Phase 7: Build the safety dashboard

Reuse the current dashboard shell and add:

- Safety summary.
- Violation feed.
- Unverifiable cases.
- Journey timeline.
- PPE evidence.
- Rule configuration.
- Safety reports.

## Phase 8: Evaluate and document

Create labelled scenarios and measure:

- PPE accuracy.
- Handoff accuracy.
- Journey accuracy.
- Violation precision.
- False accusations.
- Abstention rate.

---

## 9. Final implementation-status table

| Capability | Status | Reuse in safety system | Additional work |
|---|---|---|---|
| Video upload | Built | Directly reusable | Add safety event hooks |
| Live webcam | Built | Directly reusable | Add camera role metadata |
| Browser camera | Built | Directly reusable | Add safety event hooks |
| Phone camera pairing | Built | Useful for multi-camera demo | Add synchronization metadata |
| OpenCV frame processing | Built | Directly reusable | No major change |
| Person detection | Built | Directly reusable | No major change |
| ByteTrack tracking | Built | Directly reusable | Preserve local track IDs |
| Pose estimation | Built | Supporting feature | Not central to compliance |
| Sitting/standing/walking | Built | Optional supporting signal | Reposition in dashboard |
| Polygon zones | Built | Directly reusable | Add safety zone types |
| Zone editor | Built | Directly reusable | Add rule metadata fields |
| Homography/floor mapping | Built | Useful for visualisation | Optional for first prototype |
| Dwell-time tracking | Built | Directly reusable | Add safety violation events |
| Activity aggregation | Built | Partially reusable | Do not call it productivity |
| Appearance ReID | Built | Directly reusable | Feed journeys and handoffs |
| Identity tracker | Built | Directly reusable | Reduce reliance on seat binding |
| Cross-camera handoff | Built | Pairwise foundation | Add multi-hop journeys |
| Camera topology | Built | Directly reusable | Add safety route constraints |
| Face enrolment | Built | Optional | Do not require initially |
| Privacy blur | Built | Directly reusable | Store only blurred evidence |
| Local SQLite telemetry | Built | Reusable for high-frequency data | Add safety tables |
| Supabase/PostgreSQL | Built | Directly reusable | Add safety schema and RLS |
| Authentication | Built | Directly reusable | No major change |
| Organisation roles | Built | Directly reusable | Add safety permissions if needed |
| Existing alerts | Built | Reusable foundation | Add event-driven safety types |
| Analytics dashboard | Built | Reusable shell | Add safety pages |
| Report export | Built | Reusable mechanism | Add safety report formats |
| PPE detection | Missing | Required | Add model and inference path |
| PPE association | Missing | Required | Associate PPE with workers |
| PPE evidence history | Missing | Required | Add temporal aggregation |
| Zone entry/exit events | Missing | Required | Add transition detector |
| Safety event database | Missing | Required | Add tables and writer |
| Safety rule configuration | Missing | Required | Add schema and UI/API |
| Worker journey builder | Missing | Required | Add multi-camera sequence layer |
| Multi-hop confidence | Missing | Required | Define and evaluate fusion |
| Compliance engine | Missing | Required | Produce three decision states |
| Safety evidence snapshots | Missing | Recommended | Add blurred event evidence |
| Safety alerts | Partial | Required | Add immediate safety triggers |
| Safety dashboard | Missing | Required | Add compliance interface |
| Safety reports | Missing | Required | Add compliance analytics |
| Safety evaluation dataset | Missing | Required | Record or create labelled cases |
| Safety evaluation metrics | Missing | Required | Measure accuracy and false alerts |

---

## 10. Final conclusion

The existing VisionWorks project already contains the platform foundation:

```text
video input
→ person detection
→ tracking
→ posture
→ zones
→ dwell time
→ appearance identity
→ camera handoff
→ persistence
→ authentication
→ dashboards
```

The missing safety-specific layer is:

```text
PPE detection
→ PPE-to-person association
→ safety events
→ worker journeys
→ sequence and timing verification
→ compliance decisions
→ safety alerts
→ evidence
→ safety dashboard
→ safety evaluation
```

Therefore, the project can be credibly repositioned as a Workplace Safety
Compliance Monitoring System, but only after the missing PPE, safety-event,
journey-verification, rule-engine, and safety-reporting features are built and
evaluated.

The most important implementation principle is:

> **Do not guess when evidence is weak.**

The system should produce a potential violation only when the evidence is
sufficient. Otherwise, it should explicitly report `UNVERIFIABLE`.
