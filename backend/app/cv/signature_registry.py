# backend/app/cv/signature_registry.py
"""
The daily signature registry: what each person looks like TODAY.

Step 11 of IDENTITY_TRACKING_PLAN.md, and the hinge the whole architecture
turns on.

THE PROBLEM IT SOLVES

Step 10 can name somebody, but only at the door, where a face is 80+ px across.
Steps 5-6 can tell whether two people at a desk camera are the same person, but
have no idea who either of them is. Neither is useful alone.

This module joins them. When the door camera recognises Prajwal at 09:02, it
records what Prajwal LOOKS like this morning — his OSNet vector, the colour of
what he is wearing, his height. Every other camera then matches against that,
which OSNet does happily at 128x64. One face match at the door buys named
tracking across the whole floor for the rest of the day.

WHY IT IS PER-DAY AND NOT PERSISTENT

Because a person's appearance is only stable for about a day. Tomorrow they
wear a different shirt, and yesterday's colour histogram is worse than nothing
— it would actively match them to whoever is wearing yesterday's colour today.
The registry is cleared at midnight, and the next morning's door match rebuilds
it. That is not a limitation to work around; it is the correct lifetime for the
data.

WHY IT DRIFTS

Somebody takes their jacket off at eleven. If the signature were frozen at the
door, every match after that would degrade. So a high-confidence match updates
the stored signature, and it follows the person through the day. The plan calls
this out explicitly.

The drift is one-directional in trust: only a match ABOVE the update threshold
is allowed to change the signature. A weak match cannot pull somebody's
signature toward whoever it half-matched, which is how a registry poisons
itself.

REDIS IF AVAILABLE, MEMORY OTHERWISE

The plan says "in-memory (Redis-backed if available)". In-memory is the
default and is correct for a single-process deployment. Redis matters when two
worker processes run cameras in the same building: without it, the door
camera's process would learn Prajwal's signature and the desk camera's process
would never see it. The interface is identical either way, so nothing above
this module knows which is in use.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# A signature must be at least this good before it is allowed to overwrite what
# is already stored. Drift should follow the person, not follow a bad guess:
# below this, the observation is ignored rather than blended in.
SIGNATURE_UPDATE_MIN_CONFIDENCE = 0.70

# How strongly a new observation pulls the stored signature. 0.25 means a
# quarter of the way each time, so a change of clothing takes a handful of
# confident sightings to fully register and one anomalous frame moves almost
# nothing. An exponential moving average rather than a replacement, for exactly
# that reason.
SIGNATURE_DRIFT_ALPHA = 0.25

# Matching an area-camera identity against the day's registry. Higher than
# Step 6's 0.65 stitching gate, deliberately: stitching only has to answer "is
# this the same person as a moment ago", while this answers "is this a specific
# NAMED person", and a wrong answer here attributes one employee's whole day to
# another.
REGISTRY_MATCH_MIN_COSINE = 0.72

# The margin rule, as in face matching: the best candidate must be clearly
# better than the second, or nobody is named.
REGISTRY_MATCH_MIN_MARGIN = 0.06


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailySignatureRegistry:
    """
    employee_id -> today's appearance signature.

    Process-wide by design: the door camera's session writes it and the desk
    camera's session reads it, and those are different sessions. Thread-safe,
    because those sessions run concurrently.
    """

    def __init__(self, org_id: str = None, redis_url: str = None):
        self.org_id = org_id or "default"
        self._lock = threading.RLock()
        self._day = _today()
        # employee_id -> {embedding, upper, lower, height, confidence,
        #                 source, first_seen, last_seen, updates}
        self._store = {}
        self._redis = self._connect_redis(redis_url)

        self.registered = 0
        self.updates = 0
        self.matches = 0
        self.rejected_by_margin = 0

    # ── backing store ───────────────────────────────────────────────────────

    def _connect_redis(self, url):
        """
        Redis, or None.

        Absence is not an error and is not logged as one — a single-process
        deployment does not need it, and the in-memory path is the default.
        """
        url = url or os.getenv("REDIS_URL")
        if not url:
            return None
        try:
            import redis
            client = redis.Redis.from_url(url, socket_timeout=2)
            client.ping()
            logger.info("daily signature registry is Redis-backed")
            return client
        except Exception as e:
            logger.info(f"Redis unavailable ({e}); the signature registry stays in memory.")
            return None

    def _key(self) -> str:
        return f"visionworks:sig:{self.org_id}:{self._day}"

    def _roll_day_if_needed(self):
        """
        Clear at midnight.

        Checked on access rather than on a timer: a timer in a long-running
        process is one more thing to cancel on shutdown, and the cost here is a
        string comparison. Yesterday's signatures are not merely stale, they are
        actively wrong — the shirt has changed.
        """
        today = _today()
        if today != self._day:
            logger.info(
                f"signature registry rolling over {self._day} -> {today}; "
                f"clearing {len(self._store)} signature(s)")
            self._day = today
            self._store = {}
            self.registered = 0
            self.updates = 0

    # ── writing ─────────────────────────────────────────────────────────────

    def register(self, employee_id: str, signature: dict, confidence: float,
                 source: str = "face") -> bool:
        """
        Record (or drift) today's signature for one employee.

        Returns True when the signature was stored or updated. False means the
        observation was ignored, which is the common and correct outcome for a
        weak match.

        `source` is "face" for a door match and "seat" for a desk binding — both
        are legitimate ways to learn who somebody is, and the plan uses the
        first while Step 7 already provides the second.
        """
        if not employee_id or not signature:
            return False
        if confidence < SIGNATURE_UPDATE_MIN_CONFIDENCE:
            return False
        if signature.get("embedding") is None:
            # Without an appearance vector there is nothing for another camera
            # to match against. Colour alone is not enough to name somebody.
            return False

        with self._lock:
            self._roll_day_if_needed()
            now = datetime.now(timezone.utc).isoformat()
            existing = self._store.get(employee_id)

            if existing is None:
                self._store[employee_id] = {
                    "embedding": _unit(signature["embedding"]),
                    "upper": _as_array(signature.get("upper")),
                    "lower": _as_array(signature.get("lower")),
                    "height": signature.get("height"),
                    "confidence": float(confidence),
                    "source": source,
                    "first_seen": now,
                    "last_seen": now,
                    "updates": 1,
                }
                self.registered += 1
                logger.info(f"registered today's signature for employee {employee_id} "
                            f"({source}, confidence {confidence:.3f})")
                self._persist()
                return True

            # Drift toward the new observation rather than replacing. See
            # SIGNATURE_DRIFT_ALPHA — one odd frame must not rewrite somebody.
            a = SIGNATURE_DRIFT_ALPHA
            existing["embedding"] = _unit(
                (1 - a) * existing["embedding"] + a * _unit(signature["embedding"]))
            for key in ("upper", "lower"):
                new = _as_array(signature.get(key))
                if new is None:
                    continue
                old = existing.get(key)
                existing[key] = new if old is None or old.shape != new.shape \
                    else ((1 - a) * old + a * new).astype(np.float32)
            if signature.get("height"):
                existing["height"] = (
                    signature["height"] if not existing.get("height")
                    else (1 - a) * existing["height"] + a * signature["height"])

            existing["confidence"] = max(existing["confidence"], float(confidence))
            existing["last_seen"] = now
            existing["updates"] += 1
            if source == "face":
                existing["source"] = "face"     # a face match is the stronger claim
            self.updates += 1
            self._persist()
            return True

    def _persist(self):
        """
        Mirror to Redis, best effort.

        Never raises and never blocks the caller on a failure: the in-memory
        copy is authoritative for this process, and Redis is only how a second
        process learns about it.
        """
        if self._redis is None:
            return
        try:
            payload = {
                eid: {
                    "embedding": s["embedding"].tolist(),
                    "upper": s["upper"].tolist() if s.get("upper") is not None else None,
                    "lower": s["lower"].tolist() if s.get("lower") is not None else None,
                    "height": s.get("height"),
                    "confidence": s["confidence"],
                    "source": s["source"],
                    "first_seen": s["first_seen"],
                    "last_seen": s["last_seen"],
                    "updates": s["updates"],
                }
                for eid, s in self._store.items()
            }
            # Expire after 36 hours: long enough to survive a night shift,
            # short enough that a crashed process cannot leave a stale day
            # behind for the next one to match against.
            self._redis.setex(self._key(), 36 * 3600, json.dumps(payload))
        except Exception as e:
            logger.debug(f"could not mirror the signature registry to Redis: {e}")

    def _hydrate(self):
        """Load another process's signatures for today, if Redis has them."""
        if self._redis is None or self._store:
            return
        try:
            raw = self._redis.get(self._key())
            if not raw:
                return
            data = json.loads(raw)
            for eid, s in data.items():
                self._store[eid] = {
                    "embedding": _unit(np.asarray(s["embedding"], dtype=np.float32)),
                    "upper": _as_array(s.get("upper")),
                    "lower": _as_array(s.get("lower")),
                    "height": s.get("height"),
                    "confidence": s.get("confidence", 0.0),
                    "source": s.get("source", "face"),
                    "first_seen": s.get("first_seen"),
                    "last_seen": s.get("last_seen"),
                    "updates": s.get("updates", 1),
                }
            logger.info(f"hydrated {len(self._store)} signature(s) from Redis")
        except Exception as e:
            logger.debug(f"could not hydrate from Redis: {e}")

    # ── reading ─────────────────────────────────────────────────────────────

    def match(self, signature: dict) -> tuple:
        """
        Which employee does this appearance belong to?

        Returns (employee_id, confidence) or (None, best_score). This is the
        payoff of the whole phase: a desk camera that has never seen a face can
        now name somebody, because the door camera did the naming and left this
        behind.

        Both gates from face matching apply again — an absolute floor and a
        margin over the runner-up — for the same reason. Two people in similar
        clothing at 0.73 and 0.72 is not a match.
        """
        if signature is None or signature.get("embedding") is None:
            return None, 0.0

        with self._lock:
            self._roll_day_if_needed()
            self._hydrate()
            if not self._store:
                return None, 0.0

            probe = _unit(signature["embedding"])
            scores = []
            for employee_id, stored in self._store.items():
                appearance = float(np.dot(probe, stored["embedding"]))
                # Colour is a modifier, not a gate. It is the signal most
                # likely to be right for the wrong reason (a shared uniform),
                # so it can nudge a decision but never make one.
                colour = _colour_similarity(signature, stored)
                combined = appearance if colour is None else 0.85 * appearance + 0.15 * colour
                scores.append((combined, employee_id))

            scores.sort(reverse=True)
            top, top_id = scores[0]
            runner_up = scores[1][0] if len(scores) > 1 else 0.0

            if top < REGISTRY_MATCH_MIN_COSINE:
                return None, top
            if (top - runner_up) < REGISTRY_MATCH_MIN_MARGIN:
                self.rejected_by_margin += 1
                return None, top

            self.matches += 1
            # Never report more confidence than the registry entry itself has.
            # A perfect appearance match against a shakily-identified person is
            # still a shaky identification.
            entry_conf = self._store[top_id]["confidence"]
            return top_id, round(float(min(top, entry_conf)), 4)

    def get(self, employee_id: str):
        with self._lock:
            self._roll_day_if_needed()
            return self._store.get(employee_id)

    def known_employees(self) -> list:
        with self._lock:
            self._roll_day_if_needed()
            self._hydrate()
            return sorted(self._store)

    def stats(self) -> dict:
        with self._lock:
            return {
                "day": self._day,
                "backing": "redis" if self._redis is not None else "memory",
                "employees": len(self._store),
                "registered": self.registered,
                "updates": self.updates,
                "matches": self.matches,
                "rejected_by_margin": self.rejected_by_margin,
                "sources": {eid: s["source"] for eid, s in self._store.items()},
            }

    def clear(self):
        """Drop everything. Used by the tests and by an explicit reset."""
        with self._lock:
            self._store = {}
            self.registered = self.updates = self.matches = 0
            if self._redis is not None:
                try:
                    self._redis.delete(self._key())
                except Exception:
                    pass


def _unit(v):
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-8 else v


def _as_array(v):
    if v is None:
        return None
    a = np.asarray(v, dtype=np.float32).ravel()
    return a if a.size else None


def _colour_similarity(signature, stored):
    """Mean histogram similarity over whichever body halves both sides have."""
    import cv2
    parts = []
    for key in ("upper", "lower"):
        a, b = signature.get(key), stored.get(key)
        if a is None or b is None:
            continue
        a = np.asarray(a, dtype=np.float32).ravel()
        b = np.asarray(b, dtype=np.float32).ravel()
        if a.size != b.size or a.size == 0:
            continue
        d = cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA)
        if np.isfinite(d):
            parts.append(max(0.0, 1.0 - float(d)))
    return float(np.mean(parts)) if parts else None


# ── One registry per organisation, shared across sessions ───────────────────
#
# This is what lets the door camera's session and the desk camera's session see
# the same data. Keyed by org so two tenants on one box cannot read each
# other's signatures, which would be the worst possible bug in this file.

_registries = {}
_registry_lock = threading.Lock()


def get_registry(org_id: str = None) -> DailySignatureRegistry:
    key = org_id or "default"
    with _registry_lock:
        registry = _registries.get(key)
        if registry is None:
            registry = DailySignatureRegistry(org_id=key)
            _registries[key] = registry
        return registry
