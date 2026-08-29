# backend/app/cv/handoff.py
"""
Cross-camera handoff: the same person, seen next door.

Step 13 of IDENTITY_TRACKING_PLAN.md.

READ THE PLAN'S OWN WARNING FIRST

"This is the impressive part AND the least reliable. Scope it honestly." The
plan's expectation table puts cross-camera handoff at 60-75%, against 85-95%
for desk time on one camera, and its verification says: walk A to B ten times,
"expect 6-8 out of 10. That is a normal result."

That is not pessimism, it is arithmetic. The same shirt reads differently under
different lighting on different sensors (§8.2), and two colleagues in similar
clothing are genuinely hard to tell apart from behind. So this module is built
to ABSTAIN loudly rather than to score well: a missed handoff splits one
person's day in two, which is visible and correctable, while a wrong one
silently gives one person's afternoon to somebody else.

THE ALGORITHM, exactly as the plan specifies

    plausible = CameraLink(A->B) exists
                AND minSeconds <= gap <= maxSeconds
    score     = 0.40*seat + 0.30*osnet + 0.20*colour + 0.10*height
    accept if plausible AND score >= 0.75

WHY TOPOLOGY IS A GATE AND NOT A SIGNAL

The physical layout is the one piece of evidence that cannot be faked by a
coincidence of appearance. Two strangers may look alike; they cannot both have
walked through a door that only connects two specific rooms, in the time it
actually takes to walk it. So `CameraLink` is checked FIRST and absolutely — a
perfect appearance score across two unconnected cameras is still rejected.

That also means the quality of the answer depends on the quality of the
topology somebody drew, which is why Step 12 exists as a real editor rather
than a config file.

WHAT "TUNING" MEANS HERE

The plan says to label 200 handoffs by hand and grid-search the four weights.
`grid_search_weights()` below does the search; the labels have to come from
real footage across two real cameras, which is yours to record. Until then the
§3 weights stand, and the module reports the weights it used with every
decision so a later tuning run can be compared against this one.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# The plan's §3 fusion weights. A STARTING POINT, not an answer — §3 says so
# explicitly, and grid_search_weights() below is how they get replaced with
# something measured on real footage.
DEFAULT_WEIGHTS = {"seat": 0.40, "osnet": 0.30, "colour": 0.20, "height": 0.10}

# The plan's acceptance threshold.
HANDOFF_MIN_SCORE = 0.75

# How long a departure stays a handoff candidate. Beyond this the person has
# been gone long enough that "they walked next door" is no longer the simplest
# explanation — they went home, or the tracker lost them for an unrelated
# reason. Also bounds the candidate pool so matching stays cheap.
DEPARTURE_TTL_SECONDS = 300.0

# A candidate must beat the runner-up by this much. Same rule as face matching
# and the signature registry, for the same reason: two people who both score
# 0.78 is not a match, it is a coin flip with a number attached.
HANDOFF_MIN_MARGIN = 0.05

# An identity must have been seen at least this often before its departure is
# worth remembering. A two-frame flicker leaving camera A is not a person
# walking to camera B.
MIN_HITS_TO_DEPART = 3


class Departure:
    """
    Somebody who has just left a camera, and might turn up on another.

    Holds the signature rather than the Identity so the two cameras' trackers
    stay independent — a session must never reach into another session's live
    state, only into what it deliberately published here.
    """

    __slots__ = ("identity_id", "camera_id", "employee_id", "confidence",
                 "method", "signature", "zone_id", "left_at", "hits", "claimed")

    def __init__(self, identity_id, camera_id, signature, left_at,
                 employee_id=None, confidence=0.0, method="unknown",
                 zone_id=None, hits=0):
        self.identity_id = identity_id
        self.camera_id = camera_id
        self.employee_id = employee_id
        self.confidence = confidence
        self.method = method
        self.signature = signature
        self.zone_id = zone_id
        self.left_at = left_at
        self.hits = hits
        # Once a departure has been handed off it cannot be handed off again:
        # one person cannot arrive at two cameras.
        self.claimed = False


class HandoffRegistry:
    """
    Departures from every camera in one organisation, and the topology between
    them.

    Process-wide and shared, exactly like the signature registry — camera A's
    session writes departures and camera B's session reads them, and those are
    different sessions. Thread-safe, because they run concurrently.
    """

    def __init__(self, org_id: str = None, weights: dict = None):
        self.org_id = org_id or "default"
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self._lock = threading.RLock()
        # identity_id -> Departure
        self._departures = {}
        # (from_camera, to_camera) -> (min_seconds, max_seconds)
        self._links = {}
        self._links_loaded = False

        self.departures_recorded = 0
        self.handoffs = 0
        self.rejected_no_link = 0
        self.rejected_timing = 0
        self.rejected_score = 0
        self.rejected_margin = 0

    # ── topology ────────────────────────────────────────────────────────────

    def set_links(self, links: dict):
        """
        Set the topology directly. `links` is {(from, to): (min_s, max_s)}.

        Used by the tests and by anything that already has the links in hand.
        `load_links()` is the normal path.
        """
        with self._lock:
            self._links = dict(links or {})
            self._links_loaded = True

    def load_links(self, force: bool = False) -> int:
        """
        Read this org's camera topology from Postgres.

        Cameras are keyed by NAME rather than UUID, because that is what the
        pipeline carries: SQLite telemetry says `camera_id = 'floor5'` while
        Postgres holds a UUID, and `cameras.name` is the bridge — the same one
        `minute_aggregator._resolve_ids()` already uses.

        Zero links is a normal outcome. It means nobody has drawn the topology
        yet, and every handoff is therefore rejected — which is correct, not a
        failure. Without a declared layout there is no evidence that two
        cameras are connected at all.
        """
        if self._links_loaded and not force:
            return len(self._links)

        import json
        import os
        import urllib.parse
        import urllib.request

        base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not base or not key or key.startswith("your-") or not self.org_id:
            self._links_loaded = True
            return 0

        def get(table, params):
            url = f"{base}/rest/v1/{table}?" + urllib.parse.urlencode(params)
            request = urllib.request.Request(url, headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8") or "[]")

        try:
            cameras = get("cameras", {"select": "id,name", "orgId": f"eq.{self.org_id}"})
            by_uuid = {c["id"]: c["name"] for c in cameras}
            rows = get("camera_links", {
                "select": "fromCameraId,toCameraId,minSeconds,maxSeconds",
                "orgId": f"eq.{self.org_id}"})

            links = {}
            for r in rows:
                a = by_uuid.get(r["fromCameraId"])
                b = by_uuid.get(r["toCameraId"])
                if a and b:
                    links[(a, b)] = (int(r["minSeconds"]), int(r["maxSeconds"]))

            self._links = links
            self._links_loaded = True
            if links:
                logger.info(f"camera topology for org {self.org_id}: "
                            f"{len(links)} link(s) — "
                            + ", ".join(f"{a}->{b} {lo}-{hi}s"
                                        for (a, b), (lo, hi) in list(links.items())[:4]))
            return len(links)
        except Exception as e:
            logger.warning(f"could not load the camera topology ({e}); "
                           "cross-camera handoff is disabled.")
            self._links_loaded = True
            return 0

    def link_between(self, from_camera: str, to_camera: str):
        """(min_seconds, max_seconds) for a declared link, or None."""
        with self._lock:
            return self._links.get((from_camera, to_camera))

    # ── departures ──────────────────────────────────────────────────────────

    def record_departure(self, identity, camera_id: str, now: float = None) -> bool:
        """
        Publish that an identity has left this camera.

        Called when a tracker moves an identity into its lost gallery. The
        signature is snapshotted here, not referenced, so a later update on
        camera A cannot silently change what camera B is matching against.
        """
        if identity is None or not camera_id:
            return False
        if getattr(identity, "hits", 0) < MIN_HITS_TO_DEPART:
            return False
        signature = identity.prototype()
        if signature.get("embedding") is None:
            # Nothing for another camera to match against.
            return False

        now = now if now is not None else time.time()
        zone_id, _ = identity.dominant_zone()

        with self._lock:
            self._expire(now)
            self._departures[identity.identity_id] = Departure(
                identity_id=identity.identity_id,
                camera_id=camera_id,
                signature=signature,
                left_at=now,
                employee_id=identity.employee_id,
                confidence=identity.confidence,
                method=identity.method,
                zone_id=zone_id,
                hits=identity.hits,
            )
            self.departures_recorded += 1
        return True

    def _expire(self, now: float):
        dead = [k for k, d in self._departures.items()
                if now - d.left_at > DEPARTURE_TTL_SECONDS]
        for k in dead:
            del self._departures[k]

    # ── matching ────────────────────────────────────────────────────────────

    def score_candidate(self, arrival_sig: dict, departure: Departure,
                        arrival_zone=None) -> dict:
        """
        The plan's four-signal fusion, component by component.

        Returns every component alongside the total, because a handoff that was
        accepted on colour alone and one accepted on appearance alone are
        different claims, and Step 17's evaluation needs to tell them apart.

        Components that cannot be measured are EXCLUDED and the weights
        renormalised over the rest, rather than scored zero. A seated person
        has no height; scoring that as 0.10*0 would cap every seated handoff at
        0.90 and make it look worse than it is.
        """
        from app.cv.appearance import cosine, histogram_similarity

        parts = {}

        # OSNet appearance — the workhorse.
        a, b = arrival_sig.get("embedding"), departure.signature.get("embedding")
        if a is not None and b is not None:
            parts["osnet"] = max(0.0, cosine(a, b))

        # Colour, upper and lower body.
        sims = [histogram_similarity(arrival_sig.get(k), departure.signature.get(k))
                for k in ("upper", "lower")
                if arrival_sig.get(k) is not None
                and departure.signature.get(k) is not None]
        if sims:
            parts["colour"] = float(np.mean(sims))

        # Height, as a relative difference.
        ha, hb = arrival_sig.get("height"), departure.signature.get("height")
        if ha and hb and ha > 0 and hb > 0:
            parts["height"] = max(0.0, 1.0 - abs(ha - hb) / max(ha, hb) / 0.20)

        # The seat prior. Across cameras this is not "same zone" — the two
        # cameras see different rooms. It is whether the person is a KNOWN
        # employee whose desk we can reason about: a named departure arriving
        # somewhere plausible is stronger evidence than an anonymous one.
        if departure.employee_id:
            parts["seat"] = float(departure.confidence)

        total_weight = sum(self.weights.get(k, 0.0) for k in parts)
        if total_weight <= 0:
            score = 0.0
        else:
            score = sum(self.weights.get(k, 0.0) * v for k, v in parts.items()) / total_weight

        return {"score": round(float(score), 4), "components": parts,
                "weights": dict(self.weights)}

    def find_handoff(self, arrival_sig: dict, to_camera: str,
                     now: float = None, arrival_zone=None) -> dict:
        """
        Did this new arrival just walk here from another camera?

        Returns a verdict dict — always, whether it matched or not, because
        "rejected because no link exists" and "rejected because it scored 0.71"
        are different facts and Step 17 needs both.

        Order matters: topology first, then timing, then appearance. The first
        two are cheap and absolute, and checking them first means a wrong
        appearance match cannot be rescued by a coincidence.
        """
        now = now if now is not None else time.time()
        verdict = {"matched": False, "reason": None, "identity_id": None,
                   "employee_id": None, "score": 0.0, "gap": None,
                   "from_camera": None, "components": {}, "candidates": 0}

        if arrival_sig is None or arrival_sig.get("embedding") is None:
            verdict["reason"] = "no appearance signature"
            return verdict

        with self._lock:
            self._expire(now)
            if not self._links:
                verdict["reason"] = "no camera topology declared"
                self.rejected_no_link += 1
                return verdict

            scored = []
            saw_link = False
            saw_timing = False

            for departure in self._departures.values():
                if departure.claimed:
                    continue
                if departure.camera_id == to_camera:
                    # Left this camera and came back to it. That is Step 6's
                    # occlusion stitching, not a handoff.
                    continue

                window = self._links.get((departure.camera_id, to_camera))
                if window is None:
                    continue
                saw_link = True

                gap = now - departure.left_at
                lo, hi = window
                if not (lo <= gap <= hi):
                    saw_timing = True
                    continue

                result = self.score_candidate(arrival_sig, departure, arrival_zone)
                scored.append((result["score"], departure, result))

            verdict["candidates"] = len(scored)

            if not scored:
                if saw_timing:
                    verdict["reason"] = "no candidate within the plausible walk time"
                    self.rejected_timing += 1
                elif saw_link:
                    verdict["reason"] = "no departure from a linked camera"
                else:
                    verdict["reason"] = "no link to this camera"
                    self.rejected_no_link += 1
                return verdict

            scored.sort(key=lambda x: x[0], reverse=True)
            top_score, departure, result = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0.0

            verdict.update({
                "score": top_score,
                "gap": round(now - departure.left_at, 1),
                "from_camera": departure.camera_id,
                "components": result["components"],
            })

            if top_score < HANDOFF_MIN_SCORE:
                verdict["reason"] = (f"score {top_score:.3f} below the "
                                     f"{HANDOFF_MIN_SCORE} threshold")
                self.rejected_score += 1
                return verdict

            if (top_score - runner_up) < HANDOFF_MIN_MARGIN:
                verdict["reason"] = (f"ambiguous: {top_score:.3f} vs "
                                     f"{runner_up:.3f} runner-up")
                self.rejected_margin += 1
                return verdict

            departure.claimed = True
            self.handoffs += 1
            verdict.update({
                "matched": True,
                "reason": "handoff accepted",
                "identity_id": departure.identity_id,
                "employee_id": departure.employee_id,
                "confidence": departure.confidence,
                "method": departure.method,
            })
            logger.info(
                f"handoff: {departure.camera_id} -> {to_camera} after "
                f"{verdict['gap']:.1f}s, score {top_score:.3f} "
                f"({departure.identity_id}"
                + (f", employee {departure.employee_id}" if departure.employee_id else "")
                + ")")
            return verdict

    def stats(self) -> dict:
        with self._lock:
            attempted = (self.handoffs + self.rejected_no_link + self.rejected_timing
                         + self.rejected_score + self.rejected_margin)
            return {
                "links": len(self._links),
                "pending_departures": len(self._departures),
                "departures_recorded": self.departures_recorded,
                "handoffs": self.handoffs,
                "rejected_no_link": self.rejected_no_link,
                "rejected_timing": self.rejected_timing,
                "rejected_score": self.rejected_score,
                "rejected_margin": self.rejected_margin,
                "accept_rate": round(self.handoffs / attempted, 3) if attempted else 0.0,
                "weights": dict(self.weights),
            }

    def clear(self):
        with self._lock:
            self._departures = {}
            self.departures_recorded = self.handoffs = 0
            self.rejected_no_link = self.rejected_timing = 0
            self.rejected_score = self.rejected_margin = 0


# ── Weight tuning ───────────────────────────────────────────────────────────

def grid_search_weights(labelled: list, step: float = 0.05,
                        threshold: float = HANDOFF_MIN_SCORE) -> dict:
    """
    Search the four weights against hand-labelled handoffs.

    The plan: "Label 200 handoffs by hand, grid-search the four weights, pick
    what maximises accuracy at >= 0.75 confidence. The §3 weights are a
    starting point, not an answer."

    `labelled` is a list of {"components": {...}, "correct": bool} — the
    per-signal scores for a handoff that was attempted, and whether it was
    actually the same person. Producing that list requires footage of two real
    cameras and somebody watching it, which is why this function takes labels
    rather than making them.

    OPTIMISES FOR THE RIGHT THING. Not raw accuracy: a wrong handoff attributes
    one person's time to another and is invisible afterwards, while a missed
    one merely splits a day. So the objective penalises false accepts three
    times as heavily as false rejects, and the function reports both rates so
    the trade can be seen rather than assumed.

    Returns the best weights plus the full picture at those weights.
    """
    if not labelled:
        return {"weights": dict(DEFAULT_WEIGHTS), "reason": "no labelled data",
                "samples": 0}

    keys = ("seat", "osnet", "colour", "height")
    grid = [round(x * step, 4) for x in range(int(1 / step) + 1)]

    def evaluate(weights):
        tp = fp = tn = fn = 0
        for row in labelled:
            comps = row["components"]
            total = sum(weights[k] for k in keys if k in comps)
            score = (sum(weights[k] * comps[k] for k in keys if k in comps) / total
                     if total > 0 else 0.0)
            accepted = score >= threshold
            if accepted and row["correct"]:
                tp += 1
            elif accepted and not row["correct"]:
                fp += 1
            elif not accepted and row["correct"]:
                fn += 1
            else:
                tn += 1
        return tp, fp, tn, fn

    best = None
    for a in grid:
        for b in grid:
            for c in grid:
                d = round(1.0 - a - b - c, 4)
                if d < -1e-9 or d > 1.0:
                    continue
                weights = {"seat": a, "osnet": b, "colour": c, "height": max(0.0, d)}
                tp, fp, tn, fn = evaluate(weights)
                # A false accept costs three times a false reject.
                cost = 3 * fp + fn
                accuracy = (tp + tn) / max(1, tp + fp + tn + fn)
                key = (cost, -accuracy)
                if best is None or key < best[0]:
                    best = (key, weights, (tp, fp, tn, fn, accuracy))

    _, weights, (tp, fp, tn, fn, accuracy) = best
    return {
        "weights": weights,
        "samples": len(labelled),
        "accuracy": round(accuracy, 4),
        "true_accepts": tp, "false_accepts": fp,
        "true_rejects": tn, "false_rejects": fn,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "threshold": threshold,
        "note": ("A false accept costs 3x a false reject: a wrong handoff gives "
                 "one person's time to another and is invisible, while a missed "
                 "one only splits a day."),
    }


# ── One registry per organisation, shared across sessions ───────────────────

_registries = {}
_lock = threading.Lock()


def get_handoff_registry(org_id: str = None) -> HandoffRegistry:
    key = org_id or "default"
    with _lock:
        registry = _registries.get(key)
        if registry is None:
            registry = HandoffRegistry(org_id=key)
            _registries[key] = registry
        return registry
