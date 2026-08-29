# backend/app/cv/identity_tracker.py
"""
Fragment stitching: turning ByteTrack fragments back into people.

Step 6 of IDENTITY_TRACKING_PLAN.md.

THE PROBLEM THIS SOLVES

`pose_estimator.py` tracks with `bytetrack.yaml`, which is motion-only — it has
no appearance memory at all. When someone is occluded for roughly 30 frames
(a pillar, a colleague walking past, a monitor) ByteTrack gives up on the track
and assigns a BRAND NEW id when they reappear. The plan states the consequence
bluntly: "track 7 left the chair 5 times" is counting occlusions, not chair
exits. Every number Phase B produces — desk time, chair exits, breaks — is
wrong until this is fixed.

THE ALGORITHM, exactly as the plan specifies it

    For each detection:
      1. Known track_id already bound to an identity?  -> reuse it
      2. New track_id -> compare signature to gallery of recently-lost
         identities (last 120 s), require cosine > 0.65 AND plausible position
      3. Match -> reattach (the occlusion case)
      4. No match -> create a new identity_id

WHY 0.65, AND WHY "AND PLAUSIBLE POSITION"

0.65 is not a guess — Step 5 measured it on real footage. At that gate, 97.9%
of same-person pairs are accepted and 5.7% of different-people pairs are
wrongly accepted. The position gate is what removes most of that remaining
5.7%: two people who look similar are usually not in the same place a moment
apart, so requiring BOTH a high appearance score and a physically reachable
position turns two independently-imperfect signals into one reliable decision.

THE ASYMMETRY THAT MATTERS

A wrong stitch is worse than a missed stitch. Missing one splits a person's day
into two identities, which under-counts their desk time and is visible as a
suspiciously short day. A WRONG one merges two people, silently attributing one
person's hours to another — invisible, and the kind of error that makes the
whole system untrustworthy. Every threshold here errs toward creating a new
identity rather than reattaching to the wrong one.

WHAT THIS DELIBERATELY DOES NOT DO

It does not name anyone. Binding an identity to a real employee is Step 7 (seat
prior) and Step 10 (face at the door). This module answers only "is this the
same person as before, within this camera session" — and the identities it
creates carry `employee_id = None`, which `identity_writer.py` records as
UNKNOWN. That is correct output, not an unfinished state.
"""

import logging
import time
import uuid
from collections import deque

import numpy as np

from app.cv.appearance import cosine, histogram_similarity

logger = logging.getLogger(__name__)

# Appearance gate for reattaching a new track to a lost identity. Measured in
# Step 5: 97.9% of same-person pairs clear it, 5.7% of different-person pairs do.
REID_COSINE_MIN = 0.65

# How long a vanished identity stays reattachable. The plan's figure. Long
# enough to cover a coffee run past a pillar; short enough that a desk vacated
# for two minutes is not silently merged with whoever sits down next.
GALLERY_TTL_SECONDS = 120.0

# A person cannot cross the frame instantly. Used with the gap since the
# identity was last seen to decide whether a reappearance is physically
# reachable. Generous on purpose — the cost of being too strict is a missed
# stitch, and the appearance gate is the primary filter.
MAX_SPEED_PX_PER_SEC = 900.0

# Floor on the reachable radius, so a reappearance 0.2 s later is not held to a
# near-zero distance budget when detection jitter alone moves a box.
MIN_REACH_PX = 120.0

# Detections smaller than this are too coarse for a trustworthy embedding: the
# crop is upscaled to 128x256 from almost nothing. They still get an identity,
# but they may not be used to REATTACH, because a vague signature matches
# everything.
MIN_AREA_FOR_REID = 900          # 30x30 px

# A track must be seen this many times before its signature is allowed into the
# gallery. A one-frame flicker is usually a false positive, and admitting it
# would put noise in the pool that future tracks match against.
MIN_HITS_FOR_GALLERY = 3

# ── Step 7: seat-assignment binding ─────────────────────────────────────────
#
# The plan's rule, verbatim: "If it spends > 60% of its observed time in zone Z,
# and exactly one employee has assignedZoneId == Z, bind, confidence =
# fraction_of_time_in_zone."
SEAT_BIND_MIN_FRACTION = 0.60

# ...but 60% of four seconds is not evidence of anything. Someone walking past a
# desk is briefly 100% inside it. A minimum observation window is what separates
# "sat here" from "passed through", and it is the difference between a seat
# prior worth 0.40 of the fusion score and a coin flip.
SEAT_BIND_MIN_SECONDS = 20.0

# Below this the binding is reported but should not be trusted as fact. Matches
# the `bindingConfidence < 0.6` threshold migration 020 documents for the UI.
SEAT_BIND_LOW_CONFIDENCE = 0.60

# ── Step 14: the abstention floor ────────────────────────────────────────────
#
# The plan's central rule: "When the system is not confident, it must output
# UNKNOWN rather than guess." Below this, an attribution is discarded — the
# identity keeps employee_id = None and method = "unknown", and its time lands
# in employee_day_stats.unknownMinutes instead of on somebody's record.
#
# 0.50 is the plan's number, and note what it is NOT. It is not a tuning knob
# for accuracy: raising it does not make the system more right, it makes it
# abstain more often, which is a different and usually better trade. The plan
# is explicit that "a system that is right 92% of the time and abstains 15% of
# the time is worth far more than one that guesses on everything and is right
# 70%".
IDENTITY_MIN_CONFIDENCE = 0.50

# Bookkeeping key for time observed outside every drawn zone. It belongs in the
# DENOMINATOR of the seat fraction — a person is only "at their desk 90% of the
# time" relative to everywhere else they were — but it is not a place and can
# never itself be somebody's seat.
OUTSIDE_ZONE = "__outside__"

# How many signatures to keep per identity. The median of several is far more
# stable than the latest one, which may be motion-blurred or half-occluded.
SIGNATURE_HISTORY = 12


def _box_centre(bbox):
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


class Identity:
    """
    One person, as far as this camera session can tell.

    Holds a short history of signatures rather than a single current one: a
    person who turns away, is briefly blurred, or is half-occluded produces one
    bad embedding, and matching against the median of several is what stops
    that single bad frame from breaking the reattachment.
    """

    __slots__ = ("identity_id", "track_ids", "embeddings", "uppers", "lowers",
                 "heights", "last_bbox", "last_seen", "first_seen", "hits",
                 "employee_id", "confidence", "method", "zone_time", "last_zone",
                 "rejected_low_confidence", "seat_withdrawn_from")

    def __init__(self, identity_id: str, track_id: int, now: float):
        self.identity_id = identity_id
        self.track_ids = [track_id]
        self.embeddings = deque(maxlen=SIGNATURE_HISTORY)
        self.uppers = deque(maxlen=SIGNATURE_HISTORY)
        self.lowers = deque(maxlen=SIGNATURE_HISTORY)
        self.heights = deque(maxlen=SIGNATURE_HISTORY)
        self.last_bbox = None
        self.first_seen = now
        self.last_seen = now
        self.hits = 0
        # Filled by Step 7 (seat prior) and Step 10 (face). Until then every
        # identity is honestly UNKNOWN.
        self.employee_id = None
        self.confidence = 0.0
        self.method = "unknown"
        # Set when a SEAT binding is withdrawn only because the measured
        # fraction dipped under the floor. It records who this identity was,
        # so the same evidence can reinstate them when they come back to the
        # desk. Withdrawals for any other reason leave this None and are final.
        self.seat_withdrawn_from = None
        # zone_id -> seconds observed there. Step 7's binding rule reads this.
        self.zone_time = {}
        self.last_zone = None
        # How many times this identity was ALMOST named and the evidence was
        # too weak. Surfaced in stats(), because an identity rejected nine
        # times is a camera placement problem, not a model problem.
        self.rejected_low_confidence = 0

    def attribute(self, employee_id, confidence: float, method: str) -> bool:
        """
        Name this identity — or refuse to, and say so.

        THE ONE PLACE an attribution is set. Four different call sites can name
        somebody (face, fusion, seat, handoff) and putting the floor in each of
        them would be four chances to forget it; a fifth added later would be a
        fifth. Here it cannot be bypassed.

        Returns True when the attribution was accepted. False means the evidence
        was too weak and the identity stays UNKNOWN, which is a successful
        outcome rather than an error — the plan's rule is that the system
        abstains rather than guesses, and this is where it does that.
        """
        if not employee_id:
            return False
        confidence = float(confidence or 0.0)

        if confidence < IDENTITY_MIN_CONFIDENCE:
            # Below the floor. The identity is left UNKNOWN and the rejection
            # is recorded, so "we saw somebody we could not name" is a number
            # rather than a silence — that is what unknownMinutes is built from.
            self.rejected_low_confidence += 1
            logger.debug(
                f"{self.identity_id}: refusing to attribute to {employee_id} "
                f"at confidence {confidence:.3f} (floor {IDENTITY_MIN_CONFIDENCE})")
            return False

        self.employee_id = employee_id
        self.confidence = round(min(1.0, max(0.0, confidence)), 3)
        self.method = method
        return True

    def revoke(self, reason: str = "confidence collapsed",
           reinstatable: bool = False):
        """
        Take a name back off an identity.

        Used when a stronger claim arrives for the same employee, and when a
        confidence that once cleared the floor has since fallen below it. The
        identity survives; only the name is withdrawn.
        """
        if self.employee_id is None:
            return
        logger.debug(f"{self.identity_id}: releasing {self.employee_id} ({reason})")
        # A seat binding withdrawn for a dip is provisional: the same desk, the
        # same person, the same evidence may reinstate it. Every other
        # withdrawal (a stronger claim, a contradiction) is final, and must not
        # leave a name here for the seat rule to pick back up.
        self.seat_withdrawn_from = (
            self.employee_id if reinstatable and self.method == "seat" else None)
        self.employee_id = None
        self.confidence = 0.0
        self.method = "unknown"

    def observe(self, signature: dict, now: float, zone_id=None, dt: float = 0.0):
        """Fold one frame's observation into this identity."""
        self.hits += 1
        self.last_seen = now
        if signature.get("bbox"):
            self.last_bbox = signature["bbox"]
        if signature.get("embedding") is not None:
            self.embeddings.append(signature["embedding"])
        if signature.get("upper") is not None:
            self.uppers.append(signature["upper"])
        if signature.get("lower") is not None:
            self.lowers.append(signature["lower"])
        if signature.get("height"):
            self.heights.append(signature["height"])
        # Time is accumulated for EVERY observation, including the ones outside
        # any drawn zone. That "outside" time is the denominator of Step 7's
        # fraction, and dropping it was a real bug: with only in-zone time
        # counted, someone at their desk 42% of the session computed as 100%
        # and bound with full confidence. Measured — every confidence came back
        # exactly 1.000 before this line changed.
        #
        # OUTSIDE_ZONE is a bookkeeping key, never a place: dominant_zone()
        # excludes it from being chosen as somebody's seat.
        key = zone_id or OUTSIDE_ZONE
        self.zone_time[key] = self.zone_time.get(key, 0.0) + max(0.0, dt)
        if zone_id:
            self.last_zone = zone_id

    def prototype(self) -> dict:
        """
        The identity's representative signature.

        Embeddings are averaged then re-normalised (the standard way to pool
        L2-normalised features); histograms and height take the median, which
        ignores an outlier frame rather than being dragged by it.
        """
        emb = None
        if self.embeddings:
            m = np.mean(np.stack(list(self.embeddings)), axis=0)
            n = float(np.linalg.norm(m))
            emb = (m / n).astype(np.float32) if n > 1e-8 else None
        return {
            "embedding": emb,
            "upper": np.median(np.stack(list(self.uppers)), axis=0).astype(np.float32)
                     if self.uppers else None,
            "lower": np.median(np.stack(list(self.lowers)), axis=0).astype(np.float32)
                     if self.lowers else None,
            "height": float(np.median(list(self.heights))) if self.heights else None,
            "bbox": self.last_bbox,
        }

    def dominant_zone(self):
        """
        (zone_id, fraction_of_observed_time) for the zone this identity spends
        most of its time in — the input to Step 7's seat binding.
        """
        if not self.zone_time:
            return None, 0.0
        total = sum(self.zone_time.values())
        if total <= 0:
            return None, 0.0

        # Only REAL zones can be a seat. OUTSIDE_ZONE is bookkeeping, and
        # TRANSIT_ZONE is SpatialEngine's name for "inside the frame but inside
        # no drawn zone" — a corridor is not a desk, and naming someone because
        # they loitered in one would be exactly the confident-wrong-answer the
        # plan says to avoid. Both still count toward `total`, so the fraction
        # stays honest.
        real = {z: t for z, t in self.zone_time.items()
                if z not in (OUTSIDE_ZONE, "TRANSIT_ZONE")}
        if not real:
            return None, 0.0
        zid = max(real, key=real.get)
        return zid, real[zid] / total


class IdentityTracker:
    """
    Stitches ByteTrack fragments into stable identities, per camera session.

    One instance per session. Identity ids are namespaced with the session so
    "track 3" from today's upload can never collide with "track 3" from
    tomorrow's — the plan calls this out as failure mode §8.5.

    Not thread-safe: driven from one session's frame loop, like the writers.
    """

    def __init__(self, session_id: str = None,
                 cosine_min: float = REID_COSINE_MIN,
                 gallery_ttl: float = GALLERY_TTL_SECONDS,
                 enabled: bool = True):
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.cosine_min = cosine_min
        self.gallery_ttl = gallery_ttl
        self.enabled = enabled

        # track_id -> Identity, for tracks currently being followed
        self._by_track = {}
        # identity_id -> Identity, for identities not seen in the current frame
        self._lost = {}
        # every identity ever created this session, for the metrics
        self._all = {}

        self._seq = 0
        self._last_frame_time = None

        # Step 7 state. zone_id -> [employee_id, ...]; empty until the caller
        # supplies a seat map, and the binding is simply never attempted while
        # it is empty.
        self._seat_map = {}
        self._bound_employees = set()
        # Binding is re-attempted periodically rather than every frame: it is a
        # scan over every identity, and time-in-zone changes by milliseconds
        # between frames. 5 s matches the telemetry sampling interval.
        self._last_bind_attempt = 0.0
        self.seat_bindings = []

        # Steps 10-11. The registry is process-wide and shared between the door
        # camera's session and every area camera's session — that sharing IS
        # the mechanism, not an optimisation.
        self._registry = None
        self._camera_role = "AREA"
        self._last_registry_attempt = 0.0
        self.face_bindings = []
        self.registry_bindings = []

        # Step 13. The handoff registry is shared across every camera's session
        # in this process, the same way the signature registry is — camera A
        # publishes a departure and camera B reads it.
        self._handoff = None
        self._camera_id = None
        self.handoffs = []

        # Stitch-rate metrics — the plan asks for the ratio of raw ids to
        # stitched identities as the headline number for this step.
        self.raw_track_ids = set()
        self.reattachments = 0
        self.new_identities = 0
        self.rejected_by_appearance = 0
        self.rejected_by_position = 0

    # ── identity lifecycle ──────────────────────────────────────────────────

    def _new_identity(self, track_id: int, now: float) -> Identity:
        self._seq += 1
        ident = Identity(f"{self.session_id}::{self._seq}", track_id, now)
        self._by_track[track_id] = ident
        self._all[ident.identity_id] = ident
        self.new_identities += 1
        return ident

    def _expire(self, now: float):
        """
        Drop identities that have been gone longer than the gallery TTL, and
        release the track ids that were still pointing at them.

        Releasing the bindings is what makes it safe for `assign()` to keep a
        track -> identity mapping alive while the identity sits in the gallery.
        ByteTrack recycles ids; without this, a recycled id could walk straight
        into a long-dead person's identity with no appearance check at all.
        """
        dead = [iid for iid, i in self._lost.items()
                if now - i.last_seen > self.gallery_ttl]
        if not dead:
            return
        dead_set = set(dead)
        for iid in dead:
            del self._lost[iid]
        for tid in [t for t, i in self._by_track.items()
                    if i.identity_id in dead_set]:
            del self._by_track[tid]

    def _plausible_position(self, ident: Identity, bbox, now: float):
        """
        Could this identity physically be here?

        Distance between the last known centre and the candidate's, against a
        walking-speed budget over the elapsed gap. Returns (ok, distance,
        allowance) so callers and tests can see the numbers, not just a verdict.

        Missing data means "no objection" rather than "reject": an identity with
        no recorded bbox should be gated by appearance alone, not silently
        excluded.
        """
        if ident.last_bbox is None or bbox is None:
            return True, 0.0, float("inf")
        ax, ay = _box_centre(ident.last_bbox)
        bx, by = _box_centre(bbox)
        dist = float(np.hypot(ax - bx, ay - by))
        gap = max(0.0, now - ident.last_seen)
        allowance = max(MIN_REACH_PX, MAX_SPEED_PX_PER_SEC * gap)
        return dist <= allowance, dist, allowance

    def _match_lost(self, signature: dict, now: float):
        """
        Best reattachment candidate for an unseen track, or None.

        Scores every lost identity, keeps the best that clears BOTH gates, and
        records why the others were rejected so the metrics can distinguish
        "looked wrong" from "was in the wrong place".
        """
        if signature.get("embedding") is None:
            return None, 0.0
        if (signature.get("area") or 0) < MIN_AREA_FOR_REID:
            return None, 0.0

        best, best_score = None, 0.0
        appearance_fail = position_fail = False

        for ident in self._lost.values():
            if ident.hits < MIN_HITS_FOR_GALLERY:
                continue
            proto = ident.prototype()
            if proto["embedding"] is None:
                continue

            score = cosine(signature["embedding"], proto["embedding"])
            if score < self.cosine_min:
                appearance_fail = True
                continue

            ok, _, _ = self._plausible_position(ident, signature.get("bbox"), now)
            if not ok:
                position_fail = True
                continue

            # Colour is a tie-breaker, not a gate. Two identities that both
            # clear the appearance bar are separated by what they are wearing.
            colour = 0.0
            parts = [histogram_similarity(signature.get(k), proto.get(k))
                     for k in ("upper", "lower")
                     if signature.get(k) is not None and proto.get(k) is not None]
            if parts:
                colour = float(np.mean(parts))
            combined = 0.75 * score + 0.25 * colour

            if combined > best_score:
                best, best_score = ident, combined

        if best is None:
            if position_fail:
                self.rejected_by_position += 1
            elif appearance_fail:
                self.rejected_by_appearance += 1
        return best, best_score

    # ── the per-frame entry point ───────────────────────────────────────────

    def assign(self, signatures: list, zone_by_track: dict = None, now: float = None) -> dict:
        """
        Resolve one frame's signatures to identities.

        Returns track_id -> {identity_id, employee_id, confidence, method,
        reattached}. `reattached` is True only on the frame where a stitch
        happened, which is what makes the event visible in a log or a test.

        Safe to call with an empty list. Never raises: identity resolution is
        an enrichment of the pipeline, and a failure here must degrade to
        "everyone is a new identity", never stop the video.
        """
        now = now if now is not None else time.time()
        dt = 0.0 if self._last_frame_time is None else max(0.0, now - self._last_frame_time)
        self._last_frame_time = now
        zone_by_track = zone_by_track or {}
        out = {}

        if not self.enabled:
            for sig in signatures:
                tid = sig.get("track_id")
                out[tid] = {"identity_id": f"{self.session_id}::{tid}",
                            "employee_id": None, "confidence": 0.0,
                            "method": "unknown", "reattached": False}
            return out

        self._expire(now)
        seen_tracks = set()

        for sig in signatures:
            tid = sig.get("track_id")
            if tid is None:
                continue
            seen_tracks.add(tid)
            self.raw_track_ids.add(tid)
            reattached = False

            # 1. Known track: reuse its identity.
            ident = self._by_track.get(tid)

            if ident is None:
                # 2. New track: try the gallery of recently-lost identities.
                candidate, score = self._match_lost(sig, now)
                if candidate is not None:
                    # 3. Match -> reattach. This is the occlusion case.
                    ident = candidate
                    ident.track_ids.append(tid)
                    self._by_track[tid] = ident
                    self._lost.pop(ident.identity_id, None)
                    self.reattachments += 1
                    reattached = True
                    logger.debug(
                        f"stitched track {tid} -> {ident.identity_id} (score {score:.3f})"
                    )
                else:
                    # 4. No local match. Before calling this a new person, ask
                    # whether they walked here from another camera (Step 13).
                    handed = self._try_handoff(sig, tid, now)
                    ident = handed if handed is not None else self._new_identity(tid, now)

            ident.observe(sig, now, zone_id=zone_by_track.get(tid), dt=dt)

            out[tid] = {
                "identity_id": ident.identity_id,
                "employee_id": ident.employee_id,
                "confidence": ident.confidence,
                "method": ident.method,
                "reattached": reattached,
            }

        # Step 7: try to bind identities to seats. Throttled, because it scans
        # every identity and the inputs barely move between frames.
        if self._seat_map and (now - self._last_bind_attempt) >= 5.0:
            self._last_bind_attempt = now
            made = self.resolve_seats(now)
            if made:
                self.seat_bindings.extend(made)
                # Reflect a fresh binding in THIS frame's output, so the writer
                # records the named attribution from the moment it is known
                # rather than a frame later.
                for tid, res in out.items():
                    ident = self._by_track.get(tid)
                    if ident is not None and ident.employee_id:
                        res["employee_id"] = ident.employee_id
                        res["confidence"] = ident.confidence
                        res["method"] = ident.method

        # Step 11: name identities from the day's registry. Same 5-second
        # throttle as the seat binding, and for the same reason — it scans
        # every identity and the inputs barely move between frames.
        if (self._registry is not None and self._camera_role != "DOOR"
                and (now - self._last_registry_attempt) >= 5.0):
            self._last_registry_attempt = now
            if self.resolve_from_registry(now):
                for tid, res in out.items():
                    ident = self._by_track.get(tid)
                    if ident is not None and ident.employee_id:
                        res["employee_id"] = ident.employee_id
                        res["confidence"] = ident.confidence
                        res["method"] = ident.method

        # Identities not seen this frame join the gallery, where they stay
        # reattachable for gallery_ttl seconds.
        #
        # The track -> identity binding is KEPT, not dropped. Evicting it made a
        # track that missed a single frame look brand new on its return, so it
        # had to re-match through the gallery — measured on clean footage, one
        # track "reattached" three separate times for what was really one
        # continuous person. Worse than the inflated count: every one of those
        # round-trips was a fresh chance to match the WRONG identity, on nothing
        # more than a one-frame detector flicker.
        #
        # The binding is cleared by _expire() when the identity finally ages out
        # of the gallery, so a track id that Bytetrack later recycles for a
        # different person cannot inherit a stale identity.
        for tid, ident in self._by_track.items():
            if tid not in seen_tracks and ident.hits >= MIN_HITS_FOR_GALLERY:
                newly_lost = ident.identity_id not in self._lost
                self._lost[ident.identity_id] = ident
                # Step 13: tell the other cameras somebody just left here. Only
                # on the frame they actually disappear, not every frame after —
                # otherwise the departure time keeps resetting and the walk-time
                # window never elapses.
                if newly_lost and self._handoff is not None and self._camera_id:
                    self._handoff.record_departure(ident, self._camera_id, now)

        return out

    # ── Step 13: cross-camera handoff ───────────────────────────────────────

    def _try_handoff(self, signature: dict, track_id: int, now: float):
        """
        Did this new track just walk in from another camera?

        Returns a NEW identity carrying the other camera's attribution, or None.

        A new identity rather than the departed one, deliberately. The two
        cameras' trackers are independent, and reaching across to mutate
        another session's live object would make one camera's state depend on
        another camera's frame timing. What crosses the boundary is the
        ATTRIBUTION — who this is, how confident, and by what method — which is
        the only part that matters downstream.

        Never raises. A handoff failure must leave the pipeline producing a new
        UNKNOWN identity, which is exactly what it would have done anyway.
        """
        if self._handoff is None or not self._camera_id:
            return None
        try:
            verdict = self._handoff.find_handoff(signature, self._camera_id, now)
        except Exception as e:
            logger.debug(f"handoff lookup failed: {e}")
            return None

        if not verdict.get("matched"):
            return None

        ident = self._new_identity(track_id, now)
        employee_id = verdict.get("employee_id")
        if employee_id and employee_id not in self._bound_employees:
            # "handoff" is one of the five methods migration 020 accepts, and
            # it says something the others do not: this attribution came from
            # another camera, so its reliability is the handoff's, not a face
            # match's. Step 17 can separate them because of this.
            #
            # attribute() applies the Step 14 floor: a handoff whose confidence
            # arrived below 0.50 leaves the identity UNKNOWN rather than
            # carrying a weak claim across a camera boundary.
            if ident.attribute(employee_id,
                               verdict.get("confidence") or 0.0, "handoff"):
                self._bound_employees.add(employee_id)

        record = {
            "identity_id": ident.identity_id,
            "from_identity": verdict.get("identity_id"),
            "from_camera": verdict.get("from_camera"),
            "employee_id": employee_id,
            "score": verdict.get("score"),
            "gap_seconds": verdict.get("gap"),
            "components": verdict.get("components"),
        }
        self.handoffs.append(record)
        return ident

    # ── Steps 10-11: face matches and the daily signature registry ──────────

    def set_handoff(self, registry, camera_id: str):
        """
        Attach the cross-camera handoff registry, and say which camera this is.

        Without a camera id a departure cannot be published — "somebody left"
        is only useful to another camera if it knows WHICH camera they left.
        """
        self._handoff = registry
        self._camera_id = camera_id

    def set_registry(self, registry, camera_role: str = "AREA"):
        """
        Attach the day's signature registry, and say what kind of camera this is.

        DOOR sessions WRITE to the registry: a face match records what that
        person looks like today. AREA sessions READ from it: an unnamed
        identity is compared against the day's signatures and can be named
        without any face ever being seen. That asymmetry is the whole of
        Phase C.
        """
        self._registry = registry
        self._camera_role = (camera_role or "AREA").upper()

    def apply_face_matches(self, matches: dict, now: float = None) -> list:
        """
        Bind identities named by the door camera, and register their signature.

        `matches` is Step 10's output: track_id -> {employee_id, confidence,
        ...}. Returns the bindings made, so a caller can log them.

        A face match OVERRIDES a seat binding. Both name somebody, but a face
        is direct evidence while a seat is an inference from where they sat —
        and when the two disagree, one of them is wrong about a person's whole
        day. The stronger evidence wins.
        """
        if not matches:
            return []
        now = now if now is not None else time.time()
        made = []

        for track_id, match in matches.items():
            ident = self._by_track.get(track_id)
            if ident is None:
                continue
            employee_id = match.get("employee_id")
            confidence = float(match.get("confidence") or 0.0)
            if not employee_id:
                continue

            # An employee already bound to a DIFFERENT identity. Rather than
            # naming two people the same, release the weaker claim: the face
            # match is direct evidence and the other was almost certainly a
            # seat inference or a failed stitch.
            for other in self._all.values():
                if other is not ident and other.employee_id == employee_id:
                    if other.method != "face" or other.confidence < confidence:
                        other.employee_id = None
                        other.confidence = 0.0
                        other.method = "unknown"

            previous = ident.employee_id
            if not ident.attribute(employee_id, confidence, "face"):
                # Below the Step 14 floor. A face match this weak is not
                # evidence, and the plan is explicit that an unenrolled or
                # uncertain person must come back UNKNOWN rather than as the
                # nearest name.
                continue
            self._bound_employees.add(employee_id)

            made.append({
                "identity_id": ident.identity_id,
                "employee_id": employee_id,
                "name": match.get("name"),
                "confidence": ident.confidence,
                "cosine": match.get("cosine"),
                "replaced": previous,
            })

            # Step 11: capture what they look like right now. Taken from the
            # identity's pooled prototype rather than a single frame, so a
            # motion-blurred crop at the moment of the match does not become
            # the thing every other camera matches against all day.
            if self._registry is not None:
                self._registry.register(
                    employee_id, ident.prototype(), confidence, source="face")

        self.face_bindings.extend(made)
        return made

    def resolve_from_registry(self, now: float = None) -> list:
        """
        Name unbound identities from the day's registry — no face needed.

        This is Step 11's verification: the door camera identified somebody, and
        now a desk camera picks up the same person by appearance alone.

        Only runs on AREA cameras. On a DOOR camera the face IS available, and
        preferring appearance there would be choosing the weaker signal while
        standing in front of the stronger one.
        """
        if self._registry is None or self._camera_role == "DOOR":
            return []
        now = now if now is not None else time.time()
        made = []

        for ident in self._all.values():
            if ident.employee_id is not None:
                continue
            if ident.hits < MIN_HITS_FOR_GALLERY:
                # Too few observations for a stable prototype. Naming somebody
                # off two frames is how a registry match becomes a wrong name.
                continue

            employee_id, confidence = self._registry.match(ident.prototype())
            if not employee_id:
                continue
            if employee_id in self._bound_employees:
                # Already claimed by another identity in this session. Two
                # identities cannot be the same person at once.
                continue

            # "fusion": named by combining appearance signals against a
            # registry entry, rather than by seeing a face or inferring a seat.
            # One of the five methods migration 020's CHECK accepts.
            if not ident.attribute(employee_id, confidence, "fusion"):
                continue
            self._bound_employees.add(employee_id)
            made.append({
                "identity_id": ident.identity_id,
                "employee_id": employee_id,
                "confidence": ident.confidence,
                "method": "fusion",
            })
            logger.info(
                f"registry match: {ident.identity_id} -> employee {employee_id} "
                f"(confidence {confidence:.3f}, no face seen)")

        self.registry_bindings.extend(made)
        return made

    # ── Step 7: seat-assignment binding ─────────────────────────────────────

    def set_seat_map(self, seat_map: dict):
        """
        Tell the tracker which employee sits in which zone.

        `seat_map` is `{zone_id: [employee_id, ...]}`. A LIST, not a single id,
        deliberately: the binding rule requires "exactly one employee has
        assignedZoneId == Z", so the tracker has to be able to see that a zone
        has two claimants and refuse to bind rather than pick one. Migration
        020's `employees_one_active_per_zone` index makes that impossible to
        create through the UI, but a seat map assembled from stale data could
        still contain it, and guessing would silently attribute one person's
        hours to another.
        """
        self._seat_map = {z: list(e) for z, e in (seat_map or {}).items()}
        # Which employees are already claimed, so two identities in the same
        # zone cannot both bind to the same person.
        self._bound_employees = {
            i.employee_id for i in self._all.values() if i.employee_id
        }

    def resolve_seats(self, now: float = None) -> list:
        """
        Bind identities to employees by where they sit. No biometrics involved.

        Returns the bindings MADE by this call, so a caller can log or emit them
        as events rather than diffing state.

        THE RULE, and why each clause is there:

          fraction > 0.60      the person is at that desk rather than passing
          observed  > 20 s     60% of a moment is not evidence (see the constant)
          exactly one employee assigned to the zone
          that employee not already bound to a different identity

        The last clause is the one the plan does not spell out but the data
        demands. If stitching split one person into two identities, both would
        satisfy the first three clauses for the same desk, and binding both
        would double-count that person's day. First past the post wins, and the
        second identity stays UNKNOWN — under-counting, which is visible, rather
        than double-counting, which is not.

        Idempotent: an identity that is already bound is left alone. Rebinding
        mid-session would make a day's attribution depend on when the aggregator
        happened to run.
        """
        if not getattr(self, "_seat_map", None):
            return []

        now = now if now is not None else time.time()
        made = []

        for ident in self._all.values():
            if ident.employee_id is not None:
                # ALREADY BOUND — but keep the confidence current.
                #
                # Binding fires as soon as the gates pass, which for a person
                # who arrives and sits down is about 21 seconds in, when they
                # are still 100% at their desk. Freezing the fraction there
                # would report 1.00 for someone who then spent half the
                # afternoon in meetings. Measured: an identity that ended the
                # session at 90% reported 1.000 before this.
                #
                # The BINDING is never revisited — see the docstring, rebinding
                # mid-session would make attribution depend on when the
                # aggregator ran — only the number describing it.
                zid, frac = ident.dominant_zone()
                if zid is not None:
                    ident.confidence = round(float(frac), 3)
                    # Step 14: an attribution whose evidence has since collapsed
                    # below the floor is withdrawn, not merely flagged. Keeping
                    # a 0.30 seat binding would attribute a whole day to
                    # somebody on evidence the system would refuse to act on if
                    # it saw it fresh — and that time belongs in
                    # unknownMinutes instead.
                    if ident.method == "seat" and ident.confidence < IDENTITY_MIN_CONFIDENCE:
                        self._bound_employees.discard(ident.employee_id)
                        ident.revoke("seat fraction fell below the floor",
                                     reinstatable=True)
                    # The confidence is allowed to FALL below the threshold that
                    # justified the binding, and the binding is kept anyway.
                    #
                    # Both halves of that are deliberate. Revoking mid-session
                    # would make a person's morning vanish retroactively the
                    # moment they went to a long meeting. But the number must
                    # tell the truth: someone bound at 100% who ends the day at
                    # 42% reports 0.42, and migration 020 already defines what
                    # to do with that — below 0.6 the UI shows the row as
                    # low-confidence rather than as fact.
                    #
                    # `method` stays "seat" because that IS how it was bound,
                    # and it is one of the five values the database CHECK
                    # accepts. Inventing a "seat_weak" here would be silently
                    # rejected at write time and recorded as UNKNOWN instead.
                continue

            zone_id, fraction = ident.dominant_zone()
            if zone_id is None:
                continue

            observed = sum(ident.zone_time.values())
            if observed < SEAT_BIND_MIN_SECONDS:
                continue

            # REINSTATEMENT
            #
            # Withdrawing at 0.50 while only binding above 0.60 leaves a dead
            # band: somebody who dips to 0.48 during a long meeting and comes
            # back to finish the day at 0.55 would stay UNKNOWN for the rest of
            # the session, and their afternoon at their own desk would land in
            # unknownMinutes. That is not caution, it is a threshold gap.
            #
            # So a binding withdrawn for a dip is restored as soon as the
            # fraction clears the floor again, provided it is the same desk and
            # the same claimant. The stricter 0.60 gate governs naming somebody
            # for the FIRST time, where the cost of being wrong is a stranger's
            # name on a day; reinstating an identity we already had evidence for
            # is a weaker claim and the floor is the right bar for it.
            if (ident.seat_withdrawn_from
                    and fraction >= IDENTITY_MIN_CONFIDENCE
                    and (self._seat_map.get(zone_id) or [None])[0]
                        == ident.seat_withdrawn_from
                    and ident.seat_withdrawn_from not in self._bound_employees):
                if ident.attribute(ident.seat_withdrawn_from, fraction, "seat"):
                    self._bound_employees.add(ident.employee_id)
                    ident.seat_withdrawn_from = None
                    logger.info(
                        f"seat-reinstated {ident.identity_id} -> "
                        f"{ident.employee_id} in {zone_id} ({fraction:.0%})")
                    continue

            if fraction <= SEAT_BIND_MIN_FRACTION:
                continue

            claimants = self._seat_map.get(zone_id) or []
            if len(claimants) != 1:
                # Zero: nobody sits there — a corridor, or an unassigned desk.
                # Two or more: ambiguous, and Step 7's rule explicitly requires
                # exactly one. Either way, abstain.
                continue

            employee_id = claimants[0]
            if employee_id in self._bound_employees:
                continue

            # Confidence IS the fraction of time in the zone, as the plan
            # specifies. It is a real measurement, not a tuned score: 0.95 means
            # they were at that desk 95% of the time they were observed.
            if not ident.attribute(employee_id, fraction, "seat"):
                continue
            self._bound_employees.add(employee_id)
            made.append({
                "identity_id": ident.identity_id,
                "employee_id": employee_id,
                "zone_id": zone_id,
                "confidence": ident.confidence,
                "observed_seconds": round(observed, 1),
                "low_confidence": ident.confidence < SEAT_BIND_LOW_CONFIDENCE,
            })
            logger.info(
                f"seat-bound {ident.identity_id} -> employee {employee_id} "
                f"in {zone_id} ({ident.confidence:.0%} of {observed:.0f}s)"
            )

        return made

    def binding_report(self) -> list:
        """
        Every identity and why it is (or is not) bound — for the Step 7
        verification and for anyone debugging an attribution.
        """
        out = []
        for ident in self._all.values():
            zone_id, fraction = ident.dominant_zone()
            observed = sum(ident.zone_time.values())
            # Report the CURRENT fraction, not the one cached at bind time.
            # resolve_seats() runs on a 5-second throttle, so the stored value
            # can trail the last few seconds of a session; a report that
            # disagreed with the data it was computed from would be worse than
            # useless when someone is checking an attribution.
            if ident.employee_id and zone_id is not None:
                ident.confidence = round(float(fraction), 3)
            claimants = (getattr(self, "_seat_map", {}) or {}).get(zone_id) or []
            if ident.employee_id:
                reason = "bound"
            elif zone_id is None:
                reason = "never inside a zone"
            elif observed < SEAT_BIND_MIN_SECONDS:
                reason = f"only observed {observed:.0f}s (need {SEAT_BIND_MIN_SECONDS:.0f}s)"
            elif fraction <= SEAT_BIND_MIN_FRACTION:
                reason = f"only {fraction:.0%} in {zone_id} (need >{SEAT_BIND_MIN_FRACTION:.0%})"
            elif len(claimants) == 0:
                reason = f"no employee assigned to {zone_id}"
            elif len(claimants) > 1:
                reason = f"{len(claimants)} employees assigned to {zone_id} — ambiguous"
            else:
                reason = f"employee {claimants[0]} already bound to another identity"
            out.append({
                "identity_id": ident.identity_id,
                "employee_id": ident.employee_id,
                "method": ident.method,
                "confidence": ident.confidence,
                "dominant_zone": zone_id,
                "fraction": round(float(fraction), 3),
                "observed_seconds": round(observed, 1),
                "reason": reason,
            })
        return sorted(out, key=lambda r: r["identity_id"])

    # ── metrics ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """
        The stitch-rate metric the plan asks for.

        `stitch_rate` is raw ByteTrack ids divided by distinct identities. 1.0
        means nothing was stitched (every fragment became its own person); 2.0
        means each person was, on average, reassembled from two fragments. The
        useful signal is the direction: a rate above 1 is the module doing its
        job, and the reattachment count says how often.
        """
        raw = len(self.raw_track_ids)
        identities = len(self._all)
        return {
            "raw_track_ids": raw,
            "identities": identities,
            "stitch_rate": (raw / identities) if identities else 0.0,
            "reattachments": self.reattachments,
            "new_identities": self.new_identities,
            "rejected_by_appearance": self.rejected_by_appearance,
            "rejected_by_position": self.rejected_by_position,
            "active": len(self._by_track),
            "in_gallery": len(self._lost),
            "seat_bound": len([i for i in self._all.values() if i.employee_id]),
            "by_method": {
                m: len([i for i in self._all.values() if i.method == m])
                for m in ("face", "fusion", "seat", "handoff", "unknown")
            },
            "handoffs_in": len(self.handoffs),
            # Step 14: how often the system declined to name somebody it had a
            # candidate for. A high number here is the system working, not
            # failing — but a persistently high one means a camera is placed
            # where it cannot see enough to be sure.
            "rejected_low_confidence": sum(
                i.rejected_low_confidence for i in self._all.values()),
            "unattributed": len([i for i in self._all.values() if not i.employee_id]),
            "confidence_floor": IDENTITY_MIN_CONFIDENCE,
        }

    def identities(self):
        """Every identity created this session, for aggregation and tests."""
        return dict(self._all)
