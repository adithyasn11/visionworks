# backend/app/cv/frame_pipeline.py
"""
The per-detection loop, extracted once.

WHY THIS EXISTS

`video_upload.py` carried the identical 40-line loop three times — once for the
uploaded-file socket, once for the server-camera socket, once for the
browser-frame socket. They were byte-for-byte the same, verified by comparing
the three blocks before this module was written, not assumed from a glance.

Three copies is fine while nothing changes. It stops being fine the moment
identity resolution goes in, because that lands *inside* this loop (between
reading `det["track_id"]` and appending to `tracked_entities`), and writing it
three times means three chances to write it differently. This module is Step 3
of IDENTITY_TRACKING_PLAN.md and exists so Steps 5-14 have exactly one place to
edit.

WHAT THIS IS NOT

It is not a redesign. `process_detections()` performs the same operations, in
the same order, with the same rounding, and returns the same dicts the three
call sites used to build inline. Step 3 is specified as a pure refactor and the
before/after payloads were compared field-by-field to confirm it.

THE ONE SUBTLETY: `frame` IS MUTATED

`blur_face_region()` writes into the frame's pixels in place and returns the
same array. The old code wrote `frame = anonymizer.blur_face_region(frame, ...)`
inside the loop, which read as a rebind but was really an in-place edit of the
caller's array — which is exactly why the callers could go on to encode `frame`
and get a blurred image. That behaviour is preserved: this function blurs the
array it is handed, and the caller encodes the same object afterwards. It is
called out here because a future reader who "cleans up" the mutation would
silently disable the privacy blur on every endpoint at once.
"""

from app.cv.spatial_engine import SpatialEngine


def process_detections(
    detections,
    frame,
    spatial_engine,
    activity_aggregator,
    anonymizer,
    zones_config,
    blur_enabled,
):
    """
    Turn one frame's raw detections into the wire format, updating telemetry
    and anonymising the frame on the way.

    Args:
        detections:          output of `PostureEstimator.process_frame_single_pass`
        frame:               the BGR frame; BLURRED IN PLACE when `blur_enabled`
        spatial_engine:      zone containment + floor projection
        activity_aggregator: per-track motion/dwell state, updated here
        anonymizer:          `PrivacyAnonymizer`
        zones_config:        the camera's zones, used to seed the occupancy tally
        blur_enabled:        this session's privacy state, already resolved

    Returns:
        (tracked_entities, zone_summary)

        `tracked_entities` is the list the WebSocket payload carries and
        `persist_frame()` writes. `zone_summary` counts people per zone, with
        anything outside a defined zone falling into TRANSIT_ZONE.
    """
    tracked_entities = []

    # Seeded with every configured zone so a zone with nobody in it reports 0
    # rather than being absent from the payload — the dashboard distinguishes
    # "empty" from "not measured".
    zone_summary = {z["zone_id"]: 0 for z in zones_config}
    zone_summary["TRANSIT_ZONE"] = 0

    for det in detections:
        track_id = det["track_id"]
        bbox = det["bbox"]
        posture = det["posture"]

        centroid = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
        zone_id = spatial_engine.check_zone_containment(centroid)

        if zone_id in zone_summary:
            zone_summary[zone_id] += 1
        else:
            zone_summary["TRANSIT_ZONE"] += 1

        activity_aggregator.update_track(track_id, centroid, posture)
        activity_score = activity_aggregator.calculate_activity_score(track_id)
        dwell_seconds = activity_aggregator.get_dwell_time_seconds(track_id)

        # Project the person's ground point onto the floorplan. This is
        # what feeds the top-down heatmap.
        floor_point = spatial_engine.project_to_floor(bbox, frame.shape)

        # Privacy-by-design: blur the head region before the frame is
        # encoded and sent, so an anonymised frame is the only thing
        # that ever leaves the server. Whether it runs is the session's
        # privacy_state, which starts from PRIVACY_BLUR_DEFAULT and
        # follows the viewer's toggle from there.
        #
        # In place — see the module docstring. The caller encodes this same
        # array once the loop is done.
        anonymizer.blur_face_region(frame, bbox, blur_enabled=blur_enabled)

        tracked_entities.append({
            "track_id": track_id,
            "bbox": bbox,
            "posture": posture,
            "activity_score": round(activity_score, 2),
            "zone_id": zone_id,
            "dwell_duration_seconds": dwell_seconds,
            "floor_point": [round(floor_point[0], 1), round(floor_point[1], 1)],
            "floor_size": [SpatialEngine.FLOOR_WIDTH, SpatialEngine.FLOOR_HEIGHT]
        })

    return tracked_entities, zone_summary
