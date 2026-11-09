# backend/app/safety/snapshots.py
"""
Evidence pictures: ONE blurred JPEG for each flagged decision, so a reviewer can see
what the system saw without the system keeping any video.

PRIVACY IS DONE HERE, NOT TRUSTED FROM ELSEWHERE

The session's privacy blur can be off (a viewer toggle, PRIVACY_BLUR_DEFAULT=false)
and, when on, it blurs the top quarter of each person's box, which is exactly where a
hard hat is. A saved picture must not depend on either. So the session hands this
module the CLEAN frame, taken before the pipeline's blur, and `render()` blurs every
person's FACE (from pose keypoints) in its own copy, whatever the session setting.
The helmet above the face and the vest below it stay visible. If fewer than two face
keypoints of the flagged person are confident, the head box is blurred instead and
the decision records `helmet_region_obscured`, because then the helmet may be hidden.

NO FILE UNLESS IT IS ALLOWED

Only flagged decisions (and, if `snapshot_unverifiable` is on, abstentions) get a
picture, only when the organisation has not switched evidence off, and every path
is built from validated uuids under one folder, so a decision id can never name a
file elsewhere.

LAYOUT   <evidence dir>/<org uuid or "local">/<decision uuid>.jpg
         `snapshot_ref` is the part after the evidence dir ("<org>/<id>.jpg").
"""

import logging
import os
import re
import tempfile
import time
import uuid as _uuid
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from app.cv.anonymizer import PrivacyAnonymizer

logger = logging.getLogger(__name__)

EVIDENCE_ENV = "SAFETY_EVIDENCE_DIR"
JPEG_QUALITY = 75
MAX_SIDE_PX = 1280                     # a saved picture is never larger than this on its longer side
LOCAL_ORG = "local"                    # standalone mode: no organisation to file the picture under

_TRACK_RE = re.compile(r"^trk-(\d+)$")

# Overlay colours (BGR): the product's red on white and black.
_RED = (38, 38, 220)
_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


# ── where files live ─────────────────────────────────────────────────────────

def evidence_dir() -> Path:
    """The folder pictures are stored under (git-ignored). `SAFETY_EVIDENCE_DIR` overrides it."""
    raw = os.getenv(EVIDENCE_ENV)
    base = Path(raw) if raw else Path(__file__).resolve().parents[2] / "evidence"
    return base.resolve()


def _uuid_text(value, what: str) -> str:
    try:
        parsed = _uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"{what} must be a uuid, got {value!r}")
    if str(parsed) != str(value):
        raise ValueError(f"{what} must be a canonical uuid, got {value!r}")
    return str(parsed)


def ref_for(org_id: Optional[str], decision_id: str) -> str:
    """The `snapshot_ref` for a decision. Raises ValueError on anything that is not a uuid."""
    org = _uuid_text(org_id, "org_id") if org_id else LOCAL_ORG
    return f"{org}/{_uuid_text(decision_id, 'decision_id')}.jpg"


def path_for_ref(ref: Optional[str]) -> Optional[Path]:
    """
    The file a `snapshot_ref` names, or None if the ref is not exactly "<org>/<id>.jpg"
    with a uuid (or "local") and a uuid, or if the result is not inside the evidence
    folder. Nothing a caller supplies can reach another path.
    """
    if not ref or not isinstance(ref, str):
        return None
    parts = ref.split("/")
    if len(parts) != 2 or not parts[1].endswith(".jpg"):
        return None
    try:
        org = LOCAL_ORG if parts[0] == LOCAL_ORG else _uuid_text(parts[0], "org")
        name = _uuid_text(parts[1][:-4], "id")
    except ValueError:
        return None
    base = evidence_dir()
    path = (base / org / f"{name}.jpg").resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path


# ── drawing ──────────────────────────────────────────────────────────────────

def _scaled(box, kx: float, ky: float) -> list:
    return [box[0] * kx, box[1] * ky, box[2] * kx, box[3] * ky]


def render(
    clean_frame: np.ndarray,
    people: List[dict],
    work_shape: Tuple[int, int],
    *,
    target_track: Optional[int] = None,
    ppe_dets: Optional[List[dict]] = None,
    ppe_shape: Optional[Tuple[int, int]] = None,
    zone_polygon: Optional[list] = None,
    banner: Optional[List[str]] = None,
) -> Tuple[bytes, bool]:
    """
    A JPEG of `clean_frame` with every face blurred and the evidence drawn on top.

    people:      [{"track_id", "bbox", "keypoints"}] in the working frame's pixels (`work_shape`,
                 (height, width)), which is where the pose model, the zones and the tracker live.
    ppe_dets:    [{"cls", "conf", "bbox"}] in `ppe_shape` (height, width) pixels; only those on
                 the flagged person are drawn.
    Returns (jpeg bytes, helmet_region_obscured): the flag is True when the flagged person's
    face could not be located from keypoints and the whole head box was blurred instead.
    """
    frame = clean_frame
    fh, fw = frame.shape[:2]
    longest = max(fh, fw)
    if longest > MAX_SIDE_PX:
        k = MAX_SIDE_PX / float(longest)
        frame = cv2.resize(frame, (max(1, round(fw * k)), max(1, round(fh * k))), interpolation=cv2.INTER_AREA)
    else:
        frame = frame.copy()                      # never touch the caller's frame
    out_h, out_w = frame.shape[:2]
    work_h, work_w = work_shape
    kx, ky = out_w / float(work_w), out_h / float(work_h)

    # 1. Faces, for everyone in the picture, before anything is drawn.
    obscured = False
    for person in people:
        bbox = _scaled(person["bbox"], kx, ky)
        keypoints = person.get("keypoints")
        if keypoints is not None:
            keypoints = np.asarray(keypoints, dtype=float).copy()
            keypoints[:, 0] *= kx
            keypoints[:, 1] *= ky
        used_face = PrivacyAnonymizer.blur_face_keypoints(frame, keypoints, bbox)
        if not used_face and person.get("track_id") == target_track and target_track is not None:
            obscured = True

    # Heads the pose model did not find. The helmet/vest model sometimes sees an item on a person
    # the pose model missed; the face is under a helmet or above a vest, so anonymise there too.
    if ppe_dets and ppe_shape:
        ph, pw = ppe_shape
        px, py = out_w / float(pw), out_h / float(ph)
        person_boxes = [_scaled(p["bbox"], kx, ky) for p in people]
        for det in ppe_dets:
            bx1, by1, bx2, by2 = _scaled(det["bbox"], px, py)
            cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
            if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in person_boxes):
                continue                                           # a listed person: their face is already handled
            w_, h_ = bx2 - bx1, by2 - by1
            if det["cls"] == "helmet":
                PrivacyAnonymizer.anonymise_box(frame, (bx1 - 0.1 * w_, by1 + 0.4 * h_, bx2 + 0.1 * w_, by2 + 1.1 * h_))
            elif det["cls"] == "safety_vest":
                PrivacyAnonymizer.anonymise_box(frame, (cx - 0.3 * w_, by1 - 0.75 * w_, cx + 0.3 * w_, by1 + 0.05 * h_))

    thick = max(1, round(out_w / 640))
    scale = max(0.45, out_w / 1400.0)

    # 2. The zone, the people, and the helmet / vest boxes of the flagged person.
    if zone_polygon and len(zone_polygon) >= 3:
        poly = np.array([[p[0] * kx, p[1] * ky] for p in zone_polygon], dtype=np.int32)
        overlay = frame.copy()
        cv2.fillPoly(overlay, [poly], _RED)
        frame = cv2.addWeighted(overlay, 0.12, frame, 0.88, 0)
        cv2.polylines(frame, [poly], True, _RED, thick, cv2.LINE_AA)
    target_box = None
    for person in people:
        x1, y1, x2, y2 = [int(v) for v in _scaled(person["bbox"], kx, ky)]
        is_target = target_track is not None and person.get("track_id") == target_track
        cv2.rectangle(frame, (x1, y1), (x2, y2), _RED if is_target else _WHITE, thick * (2 if is_target else 1), cv2.LINE_AA)
        if is_target:
            target_box = (x1, y1, x2, y2)
    if ppe_dets and ppe_shape and target_box:
        ph, pw = ppe_shape
        px, py = out_w / float(pw), out_h / float(ph)
        x1, y1, x2, y2 = target_box
        margin_x, margin_y = 0.15 * (x2 - x1), 0.15 * (y2 - y1)
        for det in ppe_dets:
            bx1, by1, bx2, by2 = _scaled(det["bbox"], px, py)
            cx, cy = (bx1 + bx2) / 2.0, (by1 + by2) / 2.0
            if not (x1 - margin_x <= cx <= x2 + margin_x and y1 - margin_y <= cy <= y2 + margin_y):
                continue
            cv2.rectangle(frame, (int(bx1), int(by1)), (int(bx2), int(by2)), _WHITE, thick, cv2.LINE_AA)
            label = f"{det['cls'].replace('safety_', '')} {det['conf']:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale * 0.8, 1)
            ty = int(by1) - 4 if by1 > th + 8 else int(by2) + th + 4
            cv2.rectangle(frame, (int(bx1), ty - th - 3), (int(bx1) + tw + 6, ty + 3), _BLACK, -1)
            cv2.putText(frame, label, (int(bx1) + 3, ty), cv2.FONT_HERSHEY_SIMPLEX, scale * 0.8, _WHITE, 1, cv2.LINE_AA)

    # 3. The banner: what was decided, in words a reviewer can check against the picture.
    lines = [str(t) for t in (banner or []) if t]
    if target_track is not None and target_box is None:
        lines.append("The flagged person is not in view in this picture.")
    if lines:
        line_h = int(26 * scale) + 6
        bar_h = line_h * len(lines) + 6
        cv2.rectangle(frame, (0, 0), (out_w, bar_h), _BLACK, -1)
        cv2.rectangle(frame, (0, 0), (6, bar_h), _RED, -1)
        for i, line in enumerate(lines):
            fit = line
            while len(fit) > 4 and cv2.getTextSize(fit, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > out_w - 24:
                fit = fit[:-4] + "..."
            cv2.putText(frame, fit, (14, 6 + (i + 1) * line_h - 8), cv2.FONT_HERSHEY_SIMPLEX, scale, _WHITE, 1, cv2.LINE_AA)

    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("the evidence picture could not be encoded")
    return buffer.tobytes(), obscured


# ── storing ──────────────────────────────────────────────────────────────────

def save_snapshot(jpeg: bytes, org_id: Optional[str], decision_id: str) -> str:
    """Write the picture and return its `snapshot_ref`. Atomic: a reader never sees half a file."""
    ref = ref_for(org_id, decision_id)
    path = path_for_ref(ref)
    if path is None:                                         # cannot happen after ref_for, but never write unchecked
        raise ValueError("unsafe evidence path")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(jpeg)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return ref


# ── the organisation's switch ────────────────────────────────────────────────

def evidence_enabled(org_id: Optional[str]) -> bool:
    """
    May this organisation's pictures be stored? Read from `organisations.safetyEvidenceEnabled`.

    No organisation or no Postgres (standalone): yes, the pictures stay on this machine.
    With Postgres configured but unreachable: NO. A picture stored for an organisation that
    switched evidence off cannot be taken back, while a picture not stored can be had again
    from the next flag; the decision itself is stored either way.
    """
    if not org_id:
        return True
    try:
        from app.db.safety_sync import _call, service_role_configured
        if not service_role_configured():
            return True
        import json
        _, raw = _call("GET", "organisations", {"select": "safetyEvidenceEnabled", "id": f"eq.{org_id}", "limit": "1"})
        rows = json.loads(raw or "[]")
        if not rows:
            logger.warning("Evidence pictures are off: organisation %s was not found in Postgres", org_id)
            return False
        return bool(rows[0].get("safetyEvidenceEnabled", True))
    except Exception as e:
        logger.warning("Evidence pictures are off for this session: could not read the organisation's setting (%s)", e)
        return False


# ── retention ────────────────────────────────────────────────────────────────

def purge_evidence(days: Optional[int] = None, *, engine=None, now: Optional[float] = None) -> dict:
    """
    Delete pictures older than `days` (default `evidence_retention_days`), null their
    `snapshot_ref` locally, and clear it in Postgres too: the decision sync never updates an
    existing row (ignore-duplicates), so the reference there would otherwise point at nothing.
    Returns {"files": n, "decisions": [ids]}. Never raises.
    """
    result = {"files": 0, "decisions": []}
    try:
        if days is None:
            from app.safety.config import load_config
            days = int(load_config().evidence_retention_days)
        if days < 1:
            raise ValueError("evidence retention must be at least one day")
        base = evidence_dir()
        if not base.is_dir():
            return result
        cutoff = (now if now is not None else time.time()) - days * 86400
        expired = []
        for path in base.glob("*/*.jpg"):
            try:
                if path.stat().st_mtime < cutoff:
                    expired.append(path)
            except OSError as e:
                logger.warning("Could not inspect an evidence picture: %s", e)
        ids = [p.stem for p in expired if _is_uuid(p.stem)]
        if ids:
            # References first, files second: if clearing the references fails the files are kept
            # and the next pass tries again, so no reference is ever left pointing at nothing.
            _null_local_refs(ids, engine)
            _null_postgres_refs(ids)
        for path in expired:
            try:
                path.unlink()
                result["files"] += 1
            except OSError as e:
                logger.warning("Could not remove an expired evidence picture: %s", e)
        for stray in base.glob("*/*.tmp"):                  # a write that never finished
            try:
                if stray.stat().st_mtime < cutoff:
                    stray.unlink()
            except OSError:
                pass
        for folder in base.iterdir():                       # tidy empty organisation folders
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass
        result["decisions"] = ids
    except Exception as e:
        logger.error("Evidence purge failed: %s", e)
    return result


def _is_uuid(value) -> bool:
    try:
        return str(_uuid.UUID(str(value))) == str(value).lower()
    except (ValueError, TypeError):
        return False


def _null_local_refs(ids: list, engine=None) -> None:
    from sqlalchemy import text
    from app.db.database import engine as default_engine
    with (engine or default_engine).begin() as conn:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join(f":i{n}" for n in range(len(chunk)))
            conn.execute(text(f"UPDATE safety_decisions SET snapshot_ref = NULL WHERE id IN ({marks})"),
                         {f"i{n}": v for n, v in enumerate(chunk)})


def _null_postgres_refs(ids: list) -> None:
    try:
        from app.db.safety_sync import _call, service_role_configured
        if not service_role_configured():
            return
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            _call("PATCH", "safety_decisions", {"id": "in.(" + ",".join(chunk) + ")"},
                  {"snapshotRef": None}, "return=minimal")
    except Exception as e:
        logger.warning("Could not clear expired evidence references in Postgres (they will 410 harmlessly): %s", e)
