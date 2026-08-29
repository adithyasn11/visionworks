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
                 "employee_id", "confidence", "method", "zone_time", "last_zone")

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
        # zone_id -> seconds observed there. Step 7's binding rule reads this.
        self.zone_time = {}
        self.last_zone = None

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
        if zone_id:
            self.zone_time[zone_id] = self.zone_time.get(zone_id, 0.0) + max(0.0, dt)
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
        zid = max(self.zone_time, key=self.zone_time.get)
        return zid, self.zone_time[zid] / total


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
                    # 4. No match -> a genuinely new person.
                    ident = self._new_identity(tid, now)

            ident.observe(sig, now, zone_id=zone_by_track.get(tid), dt=dt)

            out[tid] = {
                "identity_id": ident.identity_id,
                "employee_id": ident.employee_id,
                "confidence": ident.confidence,
                "method": ident.method,
                "reattached": reattached,
            }

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
                self._lost[ident.identity_id] = ident

        return out

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
        }

    def identities(self):
        """Every identity created this session, for aggregation and tests."""
        return dict(self._all)
