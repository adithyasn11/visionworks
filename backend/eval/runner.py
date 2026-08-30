"""
Drives a scenario through the real pipeline and collects what it said.

The point of keeping this separate from both the simulator and the metrics is
that it is the only file that knows about `IdentityTracker`. Swap the source of
frames — synthetic today, decoded video tomorrow — and neither the scenario
definitions nor the measurement code changes.

WHAT IS REAL HERE

Everything downstream of the signature: `IdentityTracker.assign()`, the gallery
stitching, the seat binding, the confidence floor, the handoff registry, and
`analyse_timeline()` for chair exits. None of it is reimplemented or mocked. If
the tracker has a bug, these numbers show it — which is the only reason to run
the exercise at all.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.identity_tracker import IdentityTracker          # noqa: E402
from app.cv.handoff import HandoffRegistry                   # noqa: E402
from app.db.employee_aggregator import (                     # noqa: E402
    analyse_timeline, CHAIR_EXIT_MIN_SECONDS)

from .groundtruth import Prediction, SAMPLE_SECONDS          # noqa: E402


def _seat_map_for_tracker(gt) -> dict:
    """
    Ground truth stores {zone: employee}; the tracker wants {zone: [employee]}.

    The list form is not decoration — the binding rule refuses a zone with two
    claimants, and flattening to a scalar here would make that rule
    untestable.
    """
    out = defaultdict(list)
    for zone, emp in (gt.seat_map or {}).items():
        if isinstance(emp, (list, tuple, set)):
            out[zone].extend(emp)
        else:
            out[zone].append(emp)
    return dict(out)


def run_scenario(gt, frames, use_handoff: bool = None) -> dict:
    """
    Feed `frames` to the real tracker, one camera at a time.

    Returns {"predictions": [...], "exits": [...], "trackers": {...}}.

    ONE TRACKER PER CAMERA, which is how the system actually deploys: a
    tracker's gallery is within-camera by design (Step 6), and crossing
    cameras is the handoff registry's job (Step 13). Running one tracker over
    every camera would quietly make the handoff test trivial and meaningless.
    """
    cameras = sorted({cam for _, cam, _ in frames})
    seat_map = _seat_map_for_tracker(gt)

    if use_handoff is None:
        use_handoff = len(cameras) > 1 and bool(gt.camera_links)

    handoff = None
    if use_handoff:
        handoff = HandoffRegistry(org_id=f"eval-{id(gt)}")
        links = {}
        for link in gt.camera_links:
            links[(link["from"], link["to"])] = (link["min_seconds"],
                                                 link["max_seconds"])
        handoff.set_links(links)

    trackers = {}
    for cam in cameras:
        tk = IdentityTracker(session_id=f"eval-{cam}")
        tk.set_seat_map(seat_map)
        if handoff is not None:
            tk.set_handoff(handoff, cam)
        trackers[cam] = tk

    predictions = []
    # Per-employee timelines, for the chair-exit measurement. Built from what
    # the SYSTEM believed, not from the truth — otherwise the exit metric
    # would grade the truth against itself.
    timelines = defaultdict(list)

    # EVERY CAMERA IS RUNNING ALL THE TIME
    #
    # A real deployment has one tracker per camera, each processing frames
    # continuously whether or not anybody is in view. That is how a tracker
    # notices somebody has LEFT: it sees a frame where their track is absent,
    # moves them into the lost gallery, and publishes the departure the handoff
    # registry needs.
    #
    # Feeding each tracker only the frames that contain people breaks that.
    # Measured: the desk camera never ran again after somebody walked away, so
    # no departure was ever recorded and cross-camera handoff scored 0/10 —
    # a artefact of the harness, not a fault in the tracker.
    #
    # So every camera is ticked at every timestep, with an empty frame when
    # nothing is in view.
    by_time = defaultdict(dict)
    for t, cam, batch in frames:
        by_time[t][cam] = batch
    timesteps = sorted(by_time)

    for t in timesteps:
        for cam in cameras:
            batch = by_time[t].get(cam, [])
            tk = trackers[cam]
            signatures = [sig for (_p, _tid, sig, _z, _post) in batch]
            zone_by_track = {tid: zone for (_p, tid, _sig, zone, _post) in batch}

            result = tk.assign(signatures, zone_by_track=zone_by_track, now=t)

            # Sample at the pipeline's real rate rather than every frame: the
            # system only writes an identity_event every 5 s, so scoring every
            # frame would measure something the database never sees.
            if abs(t % SAMPLE_SECONDS) >= 0.25:
                continue

            for (_person, tid, _sig, zone, posture) in batch:
                r = result.get(tid) or {}
                predictions.append(Prediction(
                    t=t,
                    employee_id=r.get("employee_id"),
                    camera_id=cam,
                    zone_id=zone,
                    confidence=float(r.get("confidence") or 0.0),
                    method=r.get("method") or "unknown",
                    track_id=tid,
                    identity_id=r.get("identity_id"),
                ))
                if r.get("employee_id"):
                    timelines[r["employee_id"]].append({
                        "observed_at": _as_dt(t),
                        "zone_id": zone,
                        "posture": posture,
                        "confidence": r.get("confidence") or 0.0,
                    })

    # Chair exits, through the real aggregator rather than a reimplementation.
    exits = []
    for emp, samples in timelines.items():
        desks = {z for z, e in (gt.seat_map or {}).items() if e == emp}
        if not desks:
            continue
        stats = analyse_timeline(samples, desks, set())
        for ex in stats.get("chair_exits", []):
            exits.append((emp, _to_seconds(ex["from"])))

    return {
        "predictions": predictions,
        "exits": exits,
        "trackers": trackers,
        "cameras": cameras,
    }


# ── time helpers ──────────────────────────────────────────────────────────
#
# `analyse_timeline` works in real datetimes because that is what the database
# stores. The simulator works in seconds-from-start because that is what a
# labeller writes down. These two convert between them against a fixed epoch,
# so the round trip is exact and reproducible.

from datetime import datetime, timedelta, timezone       # noqa: E402

_EPOCH = datetime(2026, 8, 30, 9, 0, 0, tzinfo=timezone.utc)


def _as_dt(seconds: float) -> datetime:
    return _EPOCH + timedelta(seconds=float(seconds))


def _to_seconds(iso: str) -> float:
    try:
        return (datetime.fromisoformat(iso) - _EPOCH).total_seconds()
    except Exception:
        return 0.0
