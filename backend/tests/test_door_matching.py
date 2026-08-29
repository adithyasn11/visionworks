"""
Steps 10 and 11 — door-camera face matching, and the daily signature registry.

The plan's criteria:

  Step 10: "walk past the door camera. Confirm correct identification and that
            a signature is registered. Test with an unenrolled person → must
            return UNKNOWN, not a wrong name."

  Step 11: "identify at the door, then confirm the desk camera picks up the
            same person by appearance without any face match."

Walking past a camera is not something a test can do, so the WALK is
synthesised — a door session that sees a real face, then an area session that
sees the same person's body and no face at all. Everything being tested (the
detector, the embeddings, the thresholds, the registry) is the production code.

The unenrolled-person case gets as much attention as the happy path. The plan
is explicit that a wrong name is worse than no name, and a matcher that has
never abstained has not been tested.

Run:  venv/Scripts/python.exe backend/tests/test_door_matching.py
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.face_identifier import (           # noqa: E402
    FaceIdentifier, resolve_camera_role, _face_inside, _eye_distance,
    FACE_MATCH_MIN_COSINE, FACE_MATCH_MIN_MARGIN,
)
from app.cv.signature_registry import (        # noqa: E402
    DailySignatureRegistry, get_registry,
    REGISTRY_MATCH_MIN_COSINE, SIGNATURE_UPDATE_MIN_CONFIDENCE,
)
from app.cv.identity_tracker import IdentityTracker    # noqa: E402

FAILS = []
FACES = os.path.join(os.path.dirname(__file__), "_faces")


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def portrait():
    path = os.path.join(FACES, "portrait.jpg")
    if not os.path.exists(path):
        os.makedirs(FACES, exist_ok=True)
        import urllib.request
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg",
            path)
    return cv2.imread(path)


def embed(img):
    """The ArcFace vector for the one face in an image."""
    from app.cv.face_enroller import assess_face
    return assess_face(img)["embedding"]


def body_signature(seed, embedding=None):
    """An appearance signature as extract_batch() would produce one."""
    rng = np.random.default_rng(seed)
    v = embedding if embedding is not None else rng.normal(size=512).astype(np.float32)
    v = np.asarray(v, dtype=np.float32)
    v = v / np.linalg.norm(v)
    hist = rng.random(256).astype(np.float32)
    return {"embedding": v, "upper": hist, "lower": hist * 0.8,
            "height": 85.0, "bbox": [100, 100, 160, 260], "area": 9600}


def main():
    print("=" * 70)
    print("STEPS 10 & 11 — DOOR MATCHING AND THE DAILY SIGNATURE REGISTRY")
    print("=" * 70)

    base = portrait()

    # ══ A. CAMERA ROLE ═════════════════════════════════════════════════════
    print("\nA. CAMERA ROLE — face matching is off unless somebody enables it")
    print("-" * 70)
    role, width = resolve_camera_role("a-camera-that-does-not-exist")
    print(f"   unknown camera -> role={role}, inference width={width}")
    check("an unknown camera defaults to AREA", role == "AREA", role)
    check("...at 640 px, the throughput default", width == 640, str(width))

    from app.db.database import SessionLocal, apply_lightweight_migrations
    from app.db.models import CameraModel
    apply_lightweight_migrations()
    session = SessionLocal()
    try:
        session.query(CameraModel).filter(
            CameraModel.camera_id.in_(["test_door", "test_area"])).delete(
            synchronize_session=False)
        session.add(CameraModel(camera_id="test_door", name="Door", rtsp_url="",
                                role="DOOR", org_id="test-org"))
        session.add(CameraModel(camera_id="test_area", name="Desk", rtsp_url="",
                                role="AREA", org_id="test-org"))
        session.commit()
    finally:
        session.close()

    role, width = resolve_camera_role("test_door", "test-org")
    print(f"   DOOR camera    -> role={role}, inference width={width}")
    check("a DOOR camera is recognised", role == "DOOR", role)
    check("...and keeps 1280 px  <<< the plan's 'do not downscale'",
          width == 1280, str(width))

    role, width = resolve_camera_role("test_area", "test-org")
    print(f"   AREA camera    -> role={role}, inference width={width}")
    check("an AREA camera stays at 640", role == "AREA" and width == 640,
          f"{role}/{width}")

    # ══ B. MATCHING ════════════════════════════════════════════════════════
    print("\nB. FACE MATCHING — the enrolled person, and the stranger")
    print("-" * 70)
    fi = FaceIdentifier(org_id="test-org")

    # A gallery built from the real portrait, plus two unrelated people.
    me = embed(base)
    rng = np.random.default_rng(11)
    others = {}
    for i, name in enumerate(("emp-bob", "emp-carol")):
        v = rng.normal(size=512).astype(np.float32)
        others[name] = [v / np.linalg.norm(v)]
    fi._gallery = {"emp-me": [me], **others}
    fi._names = {"emp-me": "Me", "emp-bob": "Bob", "emp-carol": "Carol"}
    fi._loaded = True
    print(f"   gallery: {fi.gallery_size} employees, "
          f"{sum(len(v) for v in fi._gallery.values())} templates")

    # The enrolled person, from a slightly different angle.
    turned = cv2.warpAffine(base, cv2.getRotationMatrix2D((256, 256), 7, 1.0), (512, 512))
    emp, score, runner = fi.match_embedding(embed(turned))
    print(f"   enrolled person, turned 7 deg -> {emp}  "
          f"cosine {score:.3f} (runner-up {runner:.3f})")
    check("the enrolled person is identified correctly  <<< STEP 10",
          emp == "emp-me", str(emp))
    check("...comfortably above the 0.6 gate", score > FACE_MATCH_MIN_COSINE,
          f"{score:.3f} > {FACE_MATCH_MIN_COSINE}")

    # AN UNENROLLED PERSON. The plan's explicit requirement.
    stranger = rng.normal(size=512).astype(np.float32)
    stranger /= np.linalg.norm(stranger)
    emp, score, runner = fi.match_embedding(stranger)
    print(f"   UNENROLLED person             -> {emp}  cosine {score:.3f}")
    check("an unenrolled person returns UNKNOWN, not a wrong name  <<< STEP 10",
          emp is None, f"returned {emp}")

    # Two candidates equally close: a coin flip is not a match.
    # eps 0.005, not 0.02. Measured: 0.02 leaves the two templates 0.087 apart,
    # which CLEARS the 0.05 margin and is correctly accepted — the earlier
    # version of this test was not building an ambiguous pair at all. 0.005
    # puts them 0.006 apart, which is the genuinely undecidable case.
    a = rng.normal(size=512).astype(np.float32); a /= np.linalg.norm(a)
    tweak = a + 0.005 * rng.normal(size=512).astype(np.float32)
    tweak /= np.linalg.norm(tweak)
    twin = FaceIdentifier(org_id="t")
    twin._gallery = {"emp-x": [a], "emp-y": [tweak]}
    twin._names = {"emp-x": "X", "emp-y": "Y"}
    twin._loaded = True
    emp, score, runner = twin.match_embedding(a)
    print(f"   two near-identical templates  -> {emp}  "
          f"top {score:.3f}, runner-up {runner:.3f}, margin {score - runner:.3f}")
    check("an ambiguous match is refused rather than guessed",
          emp is None, f"returned {emp}")
    check("...counted as a margin rejection", twin.rejected_by_margin >= 1,
          str(twin.rejected_by_margin))

    # An empty gallery: nobody enrolled yet.
    empty = FaceIdentifier(org_id="none")
    empty._loaded = True
    emp, _, _ = empty.match_embedding(me)
    check("an empty gallery names nobody", emp is None, str(emp))

    # ══ C. FACE-TO-TRACK ASSOCIATION ═══════════════════════════════════════
    print("\nC. WHOSE FACE IS IT?")
    print("-" * 70)

    class FakeFace:
        def __init__(self, bbox):
            self.bbox = np.array(bbox, dtype=np.float32)

    faces = [FakeFace([120, 110, 160, 160]), FakeFace([400, 110, 440, 160])]
    picked = _face_inside(faces, [100, 100, 200, 400])
    print(f"   two people, two faces -> picked the one inside the left body box: "
          f"{picked.bbox[:2] if picked is not None else None}")
    check("a face is matched to the body that contains it",
          picked is not None and picked.bbox[0] == 120, "")
    check("a face outside every body box is not attached",
          _face_inside([FakeFace([900, 900, 940, 940])], [0, 0, 100, 100]) is None)

    # ══ D. THE REGISTRY ════════════════════════════════════════════════════
    print("\nD. THE DAILY SIGNATURE REGISTRY (Step 11)")
    print("-" * 70)
    reg = DailySignatureRegistry(org_id="test-org")
    reg.clear()

    sig_me = body_signature(1)
    ok = reg.register("emp-me", sig_me, confidence=0.88, source="face")
    print(f"   register at the door (confidence 0.88) -> {ok}")
    check("a confident door match registers a signature  <<< STEP 10", ok)

    weak = reg.register("emp-weak", body_signature(2), confidence=0.40, source="face")
    print(f"   register a weak match  (confidence 0.40) -> {weak}")
    check("a weak match does NOT register", not weak,
          f"below the {SIGNATURE_UPDATE_MIN_CONFIDENCE} bar")

    no_emb = reg.register("emp-x", {"embedding": None, "upper": None}, 0.95)
    check("a signature with no appearance vector is refused", not no_emb)

    print(f"   registry now holds: {reg.known_employees()}")
    check("only the confident one is stored", reg.known_employees() == ["emp-me"],
          str(reg.known_employees()))

    # ══ E. THE PLAN'S STEP 11 TEST ═════════════════════════════════════════
    print("\nE. THE DESK CAMERA RECOGNISES THEM — WITHOUT A FACE  <<< STEP 11")
    print("-" * 70)
    # The same person from a different camera.
    #
    # sigma 0.02, not 0.10. A unit vector in 512-d has components of about
    # 0.044, so sigma=0.10 noise is over TWICE the signal per component and
    # drives the cosine to 0.43 — nothing like the same person. Measured
    # against the real thing in Step 5: OSNet scores 0.877 median for the same
    # person across frames and 0.461 for different people. sigma 0.02 gives
    # 0.91, which sits in the right population.
    noise = np.random.default_rng(99).normal(size=512).astype(np.float32) * 0.02
    at_desk = dict(sig_me)
    at_desk["embedding"] = (sig_me["embedding"] + noise)
    at_desk["embedding"] /= np.linalg.norm(at_desk["embedding"])
    print(f"   appearance cosine against the stored signature: "
          f"{float(np.dot(at_desk['embedding'], reg.get('emp-me')['embedding'])):.3f} "
          f"(Step 5 measured 0.877 for a real same-person pair)")

    emp, conf = reg.match(at_desk)
    print(f"   the same person at a desk camera -> {emp} (confidence {conf})")
    check("the desk camera names them with no face match  <<< STEP 11",
          emp == "emp-me", str(emp))

    other_person = body_signature(555)
    emp2, conf2 = reg.match(other_person)
    print(f"   a different person               -> {emp2} (best {conf2:.3f})")
    check("a different person is not given somebody else's name",
          emp2 is None, str(emp2))

    # ══ F. DRIFT ═══════════════════════════════════════════════════════════
    print("\nF. THE SIGNATURE DRIFTS WITH THE PERSON")
    print("-" * 70)
    before = reg.get("emp-me")["embedding"].copy()
    jacket_off = dict(sig_me)
    # sigma 0.05: a change of clothing moves the appearance noticeably but does
    # not make somebody a different person. At 0.25 the "drifted" signature
    # would be orthogonal to the original, which is not drift, it is a swap.
    shift = np.random.default_rng(3).normal(size=512).astype(np.float32) * 0.05
    jacket_off["embedding"] = (sig_me["embedding"] + shift)
    jacket_off["embedding"] /= np.linalg.norm(jacket_off["embedding"])

    for _ in range(4):
        reg.register("emp-me", jacket_off, confidence=0.85, source="face")
    after = reg.get("emp-me")["embedding"]
    moved = 1.0 - float(np.dot(before, after))
    toward = float(np.dot(after, jacket_off["embedding"]))
    print(f"   after 4 confident sightings: moved {moved:.4f}, "
          f"now {toward:.3f} similar to the new appearance")
    check("the stored signature followed the person", moved > 0.001, f"{moved:.4f}")
    check("...toward the new appearance", toward > float(np.dot(before, jacket_off["embedding"])),
          f"{toward:.3f}")

    poisoner = body_signature(777)
    reg.register("emp-me", poisoner, confidence=0.30)
    still = reg.get("emp-me")["embedding"]
    check("a weak observation cannot move the signature at all",
          float(np.dot(after, still)) > 0.9999, "unchanged")

    # ══ G. MIDNIGHT ════════════════════════════════════════════════════════
    print("\nG. CLEARED AT MIDNIGHT")
    print("-" * 70)
    print(f"   today: {reg.stats()['day']}, {len(reg.known_employees())} signature(s)")
    reg._day = "2020-01-01"            # pretend it is yesterday
    reg._store["ghost"] = {"embedding": np.ones(512, np.float32) / np.sqrt(512),
                           "upper": None, "lower": None, "height": None,
                           "confidence": 0.9, "source": "face",
                           "first_seen": "", "last_seen": "", "updates": 1}
    remaining = reg.known_employees()
    print(f"   after the day rolls over: {len(remaining)} signature(s)")
    check("yesterday's signatures are cleared", remaining == [], str(remaining))
    check("...because a shirt is only today's evidence", reg.stats()["day"] != "2020-01-01")

    # ══ H. THROUGH THE TRACKER ═════════════════════════════════════════════
    print("\nH. END TO END THROUGH THE IDENTITY TRACKER")
    print("-" * 70)
    shared = DailySignatureRegistry(org_id="e2e")
    shared.clear()

    # The DOOR session: sees somebody, a face match names them.
    door = IdentityTracker(session_id="door")
    door.set_registry(shared, camera_role="DOOR")
    sig = body_signature(42)
    for f in range(10):
        door.assign([{**sig, "track_id": 1}], now=f * 0.5)
    door.apply_face_matches({1: {"employee_id": "emp-walker", "name": "Walker",
                                 "confidence": 0.91, "cosine": 0.88}})
    ident = list(door.identities().values())[0]
    print(f"   door session: {ident.identity_id} -> {ident.employee_id} "
          f"({ident.method}, {ident.confidence})")
    check("the door match named the identity", ident.employee_id == "emp-walker")
    check("...by face", ident.method == "face", ident.method)
    check("...and registered today's signature",
          "emp-walker" in shared.known_employees(), str(shared.known_employees()))

    # The AREA session: a different session, a different track id, no face.
    desk = IdentityTracker(session_id="desk")
    desk.set_registry(shared, camera_role="AREA")
    desk_sig = dict(sig)
    n2 = np.random.default_rng(8).normal(size=512).astype(np.float32) * 0.02
    desk_sig["embedding"] = (sig["embedding"] + n2)
    desk_sig["embedding"] /= np.linalg.norm(desk_sig["embedding"])
    for f in range(30):
        desk.assign([{**desk_sig, "track_id": 7}], now=f * 0.5)

    d_ident = list(desk.identities().values())[0]
    print(f"   desk session: {d_ident.identity_id} -> {d_ident.employee_id} "
          f"({d_ident.method}, {d_ident.confidence})")
    check("a SEPARATE session named the same person with no face  <<< STEP 11",
          d_ident.employee_id == "emp-walker", str(d_ident.employee_id))
    check("...by appearance fusion, not by face",
          d_ident.method == "fusion", d_ident.method)

    # A stranger at the desk camera must stay UNKNOWN.
    stranger_desk = IdentityTracker(session_id="stranger")
    stranger_desk.set_registry(shared, camera_role="AREA")
    s_sig = body_signature(31337)
    for f in range(30):
        stranger_desk.assign([{**s_sig, "track_id": 3}], now=f * 0.5)
    s_ident = list(stranger_desk.identities().values())[0]
    print(f"   a stranger  : {s_ident.identity_id} -> {s_ident.employee_id} "
          f"({s_ident.method})")
    check("a stranger at the desk camera stays UNKNOWN",
          s_ident.employee_id is None, str(s_ident.employee_id))

    # A DOOR session must not name people from the registry — it has the face.
    door2 = IdentityTracker(session_id="door2")
    door2.set_registry(shared, camera_role="DOOR")
    for f in range(30):
        door2.assign([{**desk_sig, "track_id": 9}], now=f * 0.5)
    d2 = list(door2.identities().values())[0]
    check("a DOOR camera does not fall back to the registry",
          d2.employee_id is None, str(d2.employee_id))

    # ══ I. DEGRADATION ═════════════════════════════════════════════════════
    print("\nI. DEGRADATION")
    print("-" * 70)
    bare = IdentityTracker(session_id="bare")
    bare.assign([{**body_signature(5), "track_id": 1}], now=1.0)
    check("no registry attached is fine", bare.resolve_from_registry() == [])
    check("no face matches is fine", bare.apply_face_matches({}) == [])

    off = FaceIdentifier(org_id="x", enabled=False)
    check("a disabled identifier matches nothing",
          off.identify(np.zeros((100, 100, 3), np.uint8),
                       [{"track_id": 1, "bbox": [0, 0, 50, 90]}]) == {})

    reg_stats = shared.stats()
    print(f"   registry: {reg_stats['backing']}-backed, "
          f"{reg_stats['employees']} employee(s), {reg_stats['matches']} match(es)")
    check("the registry reports where it is stored",
          reg_stats["backing"] in ("memory", "redis"), reg_stats["backing"])

    # Clean up the test cameras.
    session = SessionLocal()
    try:
        session.query(CameraModel).filter(
            CameraModel.camera_id.in_(["test_door", "test_area"])).delete(
            synchronize_session=False)
        session.commit()
    finally:
        session.close()

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEPS 10 & 11: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
