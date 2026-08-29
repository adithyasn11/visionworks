"""
Step 9 verification — face enrolment and the quality gate.

The plan's criterion:

  "enrol yourself with 5 photos; confirm 5 rows with quality > 0.8."

Nobody's face is available to a test, so this uses a real public photograph
(OpenCV's lena.jpg, which is a genuine portrait) and derives the five
"captures" from it the way a webcam session would: slight crops and shifts, so
each is a distinct frame of the same person rather than five copies of one.

The part that matters more than the happy path is Part B. The plan says a bad
enrolment "poisons every later match" and makes that employee "unmatchable all
project long", so every gate is tested with a photo that should FAIL it. A
quality gate that has never rejected anything is not a gate.

Run:  venv/Scripts/python.exe backend/tests/test_face_enrolment.py
"""

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.cv.face_enroller import (            # noqa: E402
    FaceQualityError, assess_face, check_against_existing, cosine,
    quality_score, decode_image,
    MIN_DET_SCORE, MIN_EYE_DISTANCE_PX, MIN_SHARPNESS, MIN_FACE_PX,
    MIN_TEMPLATES, MAX_TEMPLATES, MAX_DUPLICATE_SIMILARITY,
)

FAILS = []
FACES_DIR = os.path.join(os.path.dirname(__file__), "_faces")


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def portrait():
    """A real photograph of a real face, fetched once and cached."""
    os.makedirs(FACES_DIR, exist_ok=True)
    path = os.path.join(FACES_DIR, "portrait.jpg")
    if not os.path.exists(path):
        import urllib.request
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg",
            path)
    # NOT upscaled. Interpolating 512 -> 1024 halves the measured sharpness
    # (537 -> 52) and the blur gate then rejects it, correctly: an upscaled
    # image really does carry no more detail than its source. Enrolment photos
    # are used at native resolution.
    return cv2.imread(path)


def variants(img, n=5):
    """
    n distinct captures of the same person.

    Small head rotations and exposure changes — what somebody actually does
    when asked for five photos. Deliberately NOT crop-and-rescale: rescaling
    interpolates detail away and the sharpness gate rejects the result, which
    would be testing the resampler rather than the enrolment flow.
    """
    h, w = img.shape[:2]
    centre = (w // 2, h // 2)
    out = [img]
    for angle, beta in ((6, 0), (-6, 0), (3, 22), (-3, -18)):
        m = cv2.getRotationMatrix2D(centre, angle, 1.0)
        v = cv2.warpAffine(img, m, (w, h), borderMode=cv2.BORDER_REPLICATE)
        if beta:
            v = cv2.convertScaleAbs(v, alpha=1.0, beta=beta)
        out.append(v)
    return out[:n]


def main():
    print("=" * 70)
    print("STEP 9 — FACE ENROLMENT")
    print("=" * 70)

    base = portrait()
    print(f"\nsource portrait: {base.shape[1]}x{base.shape[0]}")

    # ══ A. THE PLAN'S TEST ═════════════════════════════════════════════════
    print("\nA. ENROL FIVE PHOTOS (the plan's criterion)")
    print("-" * 70)
    embeddings, qualities = [], []
    for i, v in enumerate(variants(base, 5), 1):
        try:
            r = assess_face(v)
            check_against_existing(r["embedding"], embeddings)
            embeddings.append(r["embedding"])
            qualities.append(r["quality"])
            m = r["measurements"]
            print(f"   photo {i}: quality {r['quality']:.3f}  "
                  f"det {m['det_score']:.3f}  eyes {m['eye_distance_px']:.0f}px  "
                  f"sharp {m['sharpness']:.0f}  face {m['face_width_px']}x{m['face_height_px']}")
        except FaceQualityError as e:
            print(f"   photo {i}: REJECTED — {e.code}: {e.message}")

    print(f"\n   accepted {len(embeddings)}/5 photos")
    check("five templates accepted", len(embeddings) == 5, f"{len(embeddings)}/5")
    if qualities:
        print(f"   quality: min {min(qualities):.3f}  mean {np.mean(qualities):.3f}  "
              f"max {max(qualities):.3f}")
        check("every quality > 0.8  <<< THE PLAN'S NUMBER",
              all(q > 0.8 for q in qualities),
              f"min {min(qualities):.3f}")

    # The five must agree with each other — they are one person.
    sims = [cosine(embeddings[i], embeddings[j])
            for i in range(len(embeddings)) for j in range(i + 1, len(embeddings))]
    if sims:
        print(f"   self-consistency: min {min(sims):.3f}  mean {np.mean(sims):.3f}")
        check("the five templates agree they are one person",
              min(sims) > 0.35, f"min pairwise cosine {min(sims):.3f}")
        check("...but are not identical copies",
              max(sims) < MAX_DUPLICATE_SIMILARITY, f"max {max(sims):.3f}")

    check("embeddings are 512-d and L2-normalised",
          all(e.shape == (512,) and abs(np.linalg.norm(e) - 1.0) < 0.01
              for e in embeddings), "512-d, ||e||=1")

    # ══ B. THE QUALITY GATE ════════════════════════════════════════════════
    print("\nB. THE QUALITY GATE — every rejection the plan calls for")
    print("-" * 70)

    def expect_reject(name, img, want_code):
        try:
            r = assess_face(img)
            print(f"   {name:<22} ACCEPTED (quality {r['quality']:.3f})  <-- should have failed")
            FAILS.append(name)
        except FaceQualityError as e:
            ok = e.code == want_code
            print(f"   {name:<22} rejected: {e.code}")
            print(f"     -> \"{e.message}\"")
            if not ok:
                print(f"     (expected code {want_code})")
                FAILS.append(name)

    # No face at all.
    expect_reject("blank image", np.full((640, 640, 3), 128, np.uint8), "no_face")

    # Too blurry — the §8.4 poisoning case.
    expect_reject("heavy motion blur", cv2.GaussianBlur(base, (61, 61), 0), "blurry")

    # Face too small in frame: the person is standing too far back.
    bh, bw = base.shape[:2]
    tiny = np.zeros((bh, bw, 3), np.uint8)
    small = cv2.resize(base, (110, 110))
    tiny[200:310, 200:310] = small
    try:
        r = assess_face(tiny)
        m = r["measurements"]
        print(f"   {'distant face':<22} accepted, eyes {m['eye_distance_px']:.0f}px "
              f"quality {r['quality']:.3f}")
        check("a distant face scores far lower than a close one",
              r["quality"] < min(qualities) if qualities else True,
              f"{r['quality']:.3f} vs {min(qualities):.3f}")
    except FaceQualityError as e:
        print(f"   {'distant face':<22} rejected: {e.code}")
        check("a distant face is rejected or scores low",
              e.code in ("face_too_small", "eyes_too_close", "no_face", "low_confidence"),
              e.code)

    # Two people in frame: there is no way to know who is being enrolled.
    two = np.zeros((bh, bw * 2, 3), np.uint8)
    two[:, :bw] = base
    two[:, bw:] = cv2.flip(base, 1)
    expect_reject("two people", two, "multiple_faces")

    # A postage stamp.
    expect_reject("32x32 image", np.zeros((32, 32, 3), np.uint8), "too_small")

    # Unreadable bytes.
    check("undecodable bytes are handled",
          decode_image(b"this is not an image") is None)

    # ══ C. CONSISTENCY CHECKS ══════════════════════════════════════════════
    print("\nC. IS THIS THE SAME PERSON?")
    print("-" * 70)
    if embeddings:
        # The identical photo again.
        try:
            check_against_existing(embeddings[0], [embeddings[0]])
            print("   exact duplicate        ACCEPTED  <-- should have failed")
            FAILS.append("duplicate rejected")
        except FaceQualityError as e:
            print(f"   exact duplicate        rejected: {e.code}")
            check("re-adding the same photo is rejected", e.code == "duplicate", e.code)

        # A different person entirely.
        other = np.random.default_rng(7).normal(size=512).astype(np.float32)
        other /= np.linalg.norm(other)
        try:
            check_against_existing(other, embeddings)
            print("   different person       ACCEPTED  <-- should have failed")
            FAILS.append("different person rejected")
        except FaceQualityError as e:
            print(f"   different person       rejected: {e.code} "
                  f"(similarity {e.detail.get('similarity')})")
            check("a different face is rejected", e.code == "different_person", e.code)

        # A genuine second angle of the same person must still be allowed.
        try:
            check_against_existing(embeddings[3], embeddings[:2])
            print("   another angle          accepted")
            check("a genuine second angle is accepted", True)
        except FaceQualityError as e:
            print(f"   another angle          REJECTED — {e.code}")
            FAILS.append("second angle accepted")

    # ══ D. THE SCORE ═══════════════════════════════════════════════════════
    print("\nD. THE QUALITY SCORE IS A REAL MEASUREMENT")
    print("-" * 70)
    rows = [
        ("perfect",        0.99, 100.0, 300.0),
        ("good",           0.90,  80.0, 150.0),
        ("just passing",   MIN_DET_SCORE, MIN_EYE_DISTANCE_PX, MIN_SHARPNESS),
    ]
    scores = []
    for label, d, e, s in rows:
        q = quality_score(d, e, s)
        scores.append(q)
        print(f"   {label:<14} det {d:.2f}  eyes {e:>5.0f}px  sharp {s:>5.0f} -> {q:.3f}")
    check("a perfect photo scores near 1.0", scores[0] > 0.95, f"{scores[0]:.3f}")
    check("a good photo clears the plan's 0.8 bar", scores[1] > 0.8, f"{scores[1]:.3f}")
    check("a barely-passing photo scores well below 0.8", scores[2] < 0.7,
          f"{scores[2]:.3f} — so 'quality > 0.8' is a real check, not a formality")
    check("the score is monotonic", scores[0] > scores[1] > scores[2],
          " > ".join(f"{s:.3f}" for s in scores))

    # ══ E. LIMITS ══════════════════════════════════════════════════════════
    print("\nE. TEMPLATE LIMITS")
    print("-" * 70)
    print(f"   the plan asks for {MIN_TEMPLATES}-{MAX_TEMPLATES} photos per person")
    check("limits match the plan's 3-5",
          MIN_TEMPLATES == 3 and MAX_TEMPLATES == 5,
          f"{MIN_TEMPLATES}-{MAX_TEMPLATES}")

    print("\n" + "=" * 70)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("STEP 9 (backend): ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
