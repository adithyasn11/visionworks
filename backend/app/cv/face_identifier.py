# backend/app/cv/face_identifier.py
"""
Door-camera face matching: putting a name to somebody, once a day.

Step 10 of IDENTITY_TRACKING_PLAN.md.

WHY THIS RUNS AT EXACTLY ONE CAMERA

The plan measures the optics rather than arguing about them. Face recognition
needs ~80 px between the eyes and degrades sharply below 40. At 640 px frame
width this pipeline produces 18 px at 2 m, 12 px at 3 m, 7 px at 5 m. Even at
full 1080p with no downscaling a face at 5 m is ~21 px. The information is not
in the frame — that is optics, not model quality.

So face matching happens where somebody walks straight at a camera 1-2 m away,
and nowhere else. Everywhere else, OSNet matches appearance, which it does
happily on 128x64 crops. `cameras.role` is what makes that structural: a camera
is AREA unless somebody deliberately marks it DOOR.

WHAT A MATCH IS WORTH, AND WHAT IT IS NOT

A match names an identity. It does not prove anything about the rest of the
day — that is Step 11's job, which takes the appearance signature at the moment
of the match and carries it to the other cameras.

The abstention rule is the whole design. The plan's central sentence is "when
the system is not confident, it must output UNKNOWN rather than guess", and the
plan's own verification for this step says an unenrolled person "must return
UNKNOWN, not a wrong name". Three separate gates below enforce that, and each
of them was set by measurement rather than by feel.
"""

import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

# ── Matching thresholds ─────────────────────────────────────────────────────

# The plan's figure. ArcFace cosine above this is the same person.
#
# Worth knowing what it means in practice: two DIFFERENT people typically score
# well under 0.2 with ArcFace, and two photos of the same person score 0.5-0.9
# depending on angle and lighting. 0.6 sits in the empty space between those
# populations, which is why it can be a single number rather than a tuned one.
FACE_MATCH_MIN_COSINE = 0.60

# How far clear of the runner-up the best match must be.
#
# A gate on the top score alone is not enough. If two enrolled employees both
# score 0.62, the top one is not a match — it is a coin flip that happens to
# have a number attached. Requiring a margin turns "who is closest" into "who
# is unambiguously closest", and everything else becomes UNKNOWN.
FACE_MATCH_MIN_MARGIN = 0.05

# Detector confidence below this is not a face worth matching.
MIN_DET_SCORE = 0.55

# Below this many pixels between the eyes the embedding describes noise. The
# plan's "degrades sharply below ~40". Lower than enrolment's gate, because a
# door camera catches people in motion and cannot insist on a posed photo — but
# the confidence reported for a marginal face is scaled down accordingly.
MIN_EYE_DISTANCE_PX = 30.0

# Faces are matched at most this often per track. A door camera sees somebody
# for a second or two at 30 FPS; embedding all sixty frames would spend the
# whole GPU budget re-answering a question already answered.
MIN_MATCH_INTERVAL_SECONDS = 1.5

# Once an identity is matched this confidently, stop re-matching it. Further
# attempts can only downgrade a good answer.
SETTLED_CONFIDENCE = 0.75


class FaceIdentifier:
    """
    Matches faces in door-camera frames against enrolled templates.

    One instance per session. The gallery is loaded once at construction —
    enrolment is rare and a per-frame database read would be absurd — and can
    be refreshed explicitly if somebody enrols mid-session.

    Everything here degrades rather than fails. No insightface, no templates,
    no faces in frame: the result is an empty match list, the pipeline carries
    on, and every identity stays UNKNOWN. A door camera that cannot recognise
    anybody is a working AREA camera.
    """

    def __init__(self, org_id: str = None, min_cosine: float = FACE_MATCH_MIN_COSINE,
                 enabled: bool = True):
        self.org_id = org_id
        self.min_cosine = min_cosine
        self.enabled = enabled

        # employee_id -> list of 512-d template vectors
        self._gallery = {}
        self._names = {}
        self._loaded = False
        self._app = None
        self._lock = threading.Lock()
        self._failed = False

        # track_id -> (last attempt time, best confidence so far)
        self._attempts = {}

        self.matches = 0
        self.unknown = 0
        self.rejected_by_margin = 0
        self.rejected_by_quality = 0

    # ── the gallery ─────────────────────────────────────────────────────────

    def load_gallery(self, force: bool = False) -> int:
        """
        Read this org's face templates from Postgres.

        Returns how many employees have usable templates. Zero is a normal
        outcome — nobody enrolled yet — and simply means every face resolves to
        UNKNOWN, which is the correct answer rather than an error.

        Read with the service role because migration 020 revokes SELECT on
        `face_templates.embedding` from every browser session. The backend is
        the only thing that can read a vector, which is exactly the point.
        """
        if self._loaded and not force:
            return len(self._gallery)

        import json
        import os
        import urllib.parse
        import urllib.request

        base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not base or not key or key.startswith("your-") or not self.org_id:
            self._loaded = True
            return 0

        def get(table, params):
            url = f"{base}/rest/v1/{table}?" + urllib.parse.urlencode(params)
            request = urllib.request.Request(url, headers={
                "apikey": key, "Authorization": f"Bearer {key}",
                "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8") or "[]")

        try:
            employees = get("employees", {
                "select": "id,displayName", "orgId": f"eq.{self.org_id}",
                "active": "is.true", "deletedAt": "is.null"})
            if not employees:
                self._loaded = True
                return 0

            self._names = {e["id"]: e.get("displayName") or e["id"][:8] for e in employees}
            rows = get("face_templates", {
                "select": "employeeId,embedding,quality",
                "employeeId": f"in.({','.join(self._names)})"})

            gallery = {}
            for r in rows:
                vec = r.get("embedding")
                if not vec or len(vec) != 512:
                    continue
                v = np.asarray(vec, dtype=np.float32)
                n = float(np.linalg.norm(v))
                if n < 1e-6:
                    continue
                # Re-normalise on load. Enrolment stores normalised vectors, but
                # a float round-trip through JSON can drift the norm slightly,
                # and cosine below assumes unit length.
                gallery.setdefault(r["employeeId"], []).append(v / n)

            self._gallery = gallery
            self._loaded = True
            logger.info(
                f"face gallery for org {self.org_id}: {len(gallery)} employee(s), "
                f"{sum(len(v) for v in gallery.values())} template(s)")
            return len(gallery)
        except Exception as e:
            logger.warning(f"could not load the face gallery ({e}); faces stay UNKNOWN.")
            self._loaded = True
            return 0

    @property
    def gallery_size(self) -> int:
        return len(self._gallery)

    def name_of(self, employee_id: str) -> str:
        return self._names.get(employee_id, employee_id)

    # ── detection ───────────────────────────────────────────────────────────

    def _get_app(self):
        """The shared buffalo_l pack, loaded lazily and at most once."""
        if self._app is not None or self._failed:
            return self._app
        with self._lock:
            if self._app is not None or self._failed:
                return self._app
            try:
                from insightface.app import FaceAnalysis
                providers = ["CPUExecutionProvider"]
                try:
                    import onnxruntime as ort
                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
                except Exception:
                    pass
                app = FaceAnalysis(name="buffalo_l", providers=providers)
                app.prepare(ctx_id=0 if providers[0].startswith("CUDA") else -1,
                            det_size=(640, 640))
                self._app = app
                logger.info(f"door-camera face matching ready on {providers[0]}")
            except Exception as e:
                logger.warning(f"face matching unavailable ({e}); door camera behaves as AREA.")
                self._failed = True
        return self._app

    # ── matching ────────────────────────────────────────────────────────────

    def match_embedding(self, embedding) -> tuple:
        """
        Best employee for one face embedding.

        Returns (employee_id, confidence, runner_up_score) — or (None, best,
        runner_up) when nothing clears the gates, which is a successful
        abstention rather than a failure.

        Scores against the BEST template per employee, not the mean. Somebody
        enrolled from five angles should match on whichever angle they are
        currently presenting; averaging the five would blur exactly the
        variation the extra photos were taken to capture.
        """
        if not self._gallery:
            return None, 0.0, 0.0

        scores = []
        for employee_id, templates in self._gallery.items():
            best = max(float(np.dot(embedding, t)) for t in templates)
            scores.append((best, employee_id))
        scores.sort(reverse=True)

        top, top_id = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0.0

        if top < self.min_cosine:
            return None, top, runner_up
        if (top - runner_up) < FACE_MATCH_MIN_MARGIN:
            # Two people fit equally well. That is not a match, it is a guess.
            self.rejected_by_margin += 1
            return None, top, runner_up

        return top_id, top, runner_up

    def identify(self, frame, detections: list, now: float = None) -> dict:
        """
        Find and name the faces in one door-camera frame.

        Returns track_id -> {employee_id, confidence, name, face_score,
        eye_distance_px, runner_up}. Only tracks whose face was actually
        matched appear; everyone else is simply absent, which the caller reads
        as UNKNOWN.

        Faces are associated with tracks by CONTAINMENT, not by proximity: a
        face box must sit inside a person box for the two to be the same
        person. Nearest-centre matching would happily attach a face to the
        person standing behind its owner.

        Never raises.
        """
        if not self.enabled or not detections:
            return {}

        now = now if now is not None else time.time()

        # Which tracks are worth attempting this frame?
        candidates = []
        for det in detections:
            tid = det.get("track_id")
            if tid is None:
                continue
            last, best = self._attempts.get(tid, (0.0, 0.0))
            if best >= SETTLED_CONFIDENCE:
                continue                      # already known well enough
            if (now - last) < MIN_MATCH_INTERVAL_SECONDS:
                continue                      # throttled
            candidates.append(det)

        if not candidates:
            return {}

        if not self._loaded:
            self.load_gallery()
        if not self._gallery:
            return {}

        app = self._get_app()
        if app is None:
            return {}

        try:
            faces = app.get(frame)
        except Exception as e:
            logger.debug(f"face detection failed on this frame: {e}")
            return {}

        if not faces:
            for det in candidates:
                last, best = self._attempts.get(det["track_id"], (0.0, 0.0))
                self._attempts[det["track_id"]] = (now, best)
            return {}

        out = {}
        for det in candidates:
            tid = det["track_id"]
            bbox = det.get("bbox")
            last, best_so_far = self._attempts.get(tid, (0.0, 0.0))
            self._attempts[tid] = (now, best_so_far)
            if not bbox:
                continue

            face = _face_inside(faces, bbox)
            if face is None:
                continue

            det_score = float(face.det_score)
            eye_px = _eye_distance(face)
            if det_score < MIN_DET_SCORE or eye_px < MIN_EYE_DISTANCE_PX:
                # Too small or too uncertain to trust. Counted, so a door camera
                # that is mounted too far away shows up as a statistic rather
                # than as silence.
                self.rejected_by_quality += 1
                continue

            emb = np.asarray(face.normed_embedding, dtype=np.float32)
            employee_id, score, runner_up = self.match_embedding(emb)

            if employee_id is None:
                self.unknown += 1
                continue

            # Confidence is the cosine, scaled down when the face was marginal.
            # A 0.7 match on a 32 px face is not worth the same as a 0.7 match
            # on an 80 px one, and Step 11 uses this number to decide whether
            # the signature it captures is trustworthy.
            quality_factor = min(1.0, eye_px / 60.0) * min(1.0, det_score / 0.85)
            confidence = round(float(score) * (0.7 + 0.3 * quality_factor), 4)

            self.matches += 1
            self._attempts[tid] = (now, max(best_so_far, confidence))
            out[tid] = {
                "employee_id": employee_id,
                "name": self.name_of(employee_id),
                "confidence": confidence,
                "cosine": round(float(score), 4),
                "runner_up": round(float(runner_up), 4),
                "face_score": round(det_score, 4),
                "eye_distance_px": round(eye_px, 1),
                "embedding": emb,
            }
            logger.info(
                f"door match: track {tid} -> {self.name_of(employee_id)} "
                f"(cosine {score:.3f}, runner-up {runner_up:.3f}, "
                f"{eye_px:.0f}px between the eyes)")

        return out

    def stats(self) -> dict:
        return {
            "gallery_employees": len(self._gallery),
            "gallery_templates": sum(len(v) for v in self._gallery.values()),
            "matches": self.matches,
            "unknown": self.unknown,
            "rejected_by_margin": self.rejected_by_margin,
            "rejected_by_quality": self.rejected_by_quality,
        }


def _eye_distance(face) -> float:
    kps = getattr(face, "kps", None)
    if kps is None or len(kps) < 2:
        return 0.0
    return float(np.linalg.norm(np.asarray(kps[0]) - np.asarray(kps[1])))


def _face_inside(faces, person_bbox):
    """
    The face belonging to this person box, or None.

    Containment of the face's CENTRE inside the person box, then the largest
    such face. Centre rather than the whole box because a face detector's box
    routinely overhangs a tight person box at the forehead; largest because a
    person box that contains two face centres is a person standing in front of
    somebody, and the near one is theirs.
    """
    x1, y1, x2, y2 = [float(v) for v in person_bbox[:4]]
    best, best_area = None, 0.0
    for f in faces:
        fx1, fy1, fx2, fy2 = [float(v) for v in f.bbox]
        cx, cy = (fx1 + fx2) / 2.0, (fy1 + fy2) / 2.0
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        area = (fx2 - fx1) * (fy2 - fy1)
        if area > best_area:
            best, best_area = f, area
    return best


def resolve_camera_role(camera_id: str, org_id: str = None) -> tuple:
    """
    (role, inference_width) for a camera, from the local registry.

    Defaults to ("AREA", 640): a camera nobody has marked as the door is not
    the door, and face matching stays off. That default is deliberate — the
    failure mode of guessing DOOR is running face recognition on a camera
    pointed at a whole room, which is both wasteful and exactly the
    surveillance posture this system is built to avoid.
    """
    from app.db.database import SessionLocal
    from app.db.models import CameraModel

    session = SessionLocal()
    try:
        query = session.query(CameraModel).filter(CameraModel.camera_id == camera_id)
        if org_id is not None:
            query = query.filter(CameraModel.org_id == org_id)
        row = query.first()
        if row is None:
            return "AREA", 640
        role = (row.role or "AREA").upper()
        if role not in ("AREA", "DOOR"):
            role = "AREA"
        width = row.inference_width or (1280 if role == "DOOR" else 640)
        return role, int(width)
    except Exception as e:
        logger.debug(f"could not resolve camera role for {camera_id}: {e}")
        return "AREA", 640
    finally:
        session.close()
