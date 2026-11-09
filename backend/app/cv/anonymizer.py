# backend/app/cv/anonymizer.py
import os
import cv2
import numpy as np

# Values that count as "on" when read from the environment. Anything else —
# including the empty string, "0", "no" and typos — is off. Read at call time
# rather than at import so load_dotenv() in main.py is guaranteed to have run,
# and so tests can flip the variable without reimporting the module.
_TRUTHY = {"1", "true", "yes", "on"}


def privacy_blur_default() -> bool:
    """
    The blur setting a new session starts with, from `PRIVACY_BLUR_DEFAULT`.

    Defaults to False for development, where the raw picture is what you are
    debugging. Deployments that want privacy-by-default set
    PRIVACY_BLUR_DEFAULT=true; the runtime toggle is unaffected either way. (The
    safety layer forces blur on for its evidence snapshots regardless of this
    setting — see the design doc, Step 9.)
    """
    raw = os.getenv("PRIVACY_BLUR_DEFAULT")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


class PrivacyAnonymizer:
    """
    Privacy-by-Design Frame Anonymizer.
    Applies Gaussian Blur over face and head regions of detected human bounding boxes.
    """
    @staticmethod
    def blur_face_region(
        frame: np.ndarray,
        bbox: list,
        blur_kernel_size: int = 25,
        blur_enabled: bool = True,
    ) -> np.ndarray:
        """
        Estimates top 25% of bounding box as head region and applies Gaussian blur.
        Guarantees array boundary protection.

        `blur_enabled=False` returns the frame untouched. Callers that already
        branch on a session toggle can ignore it; a per-camera caller (the door
        camera, which must never blur) passes its own decision straight in
        rather than duplicating the branch at every call site.
        """
        if not blur_enabled:
            return frame

        x1, y1, x2, y2 = bbox
        frame_h, frame_w = frame.shape[:2]

        # Clamp bounding box coordinates within image bounds
        x1_c = max(0, min(frame_w - 1, x1))
        y1_c = max(0, min(frame_h - 1, y1))
        x2_c = max(0, min(frame_w - 1, x2))
        y2_c = max(0, min(frame_h - 1, y2))

        box_h = y2_c - y1_c
        if box_h <= 5 or (x2_c - x1_c) <= 5:
            return frame # Box too small to blur safely

        head_y2 = y1_c + int(box_h * 0.25)
        head_y2 = min(frame_h, max(y1_c + 1, head_y2))

        head_roi = frame[y1_c:head_y2, x1_c:x2_c]

        if head_roi.shape[0] > 0 and head_roi.shape[1] > 0:
            # Kernel size must be odd
            k_size = blur_kernel_size if blur_kernel_size % 2 != 0 else blur_kernel_size + 1
            blurred_head = cv2.GaussianBlur(head_roi, (k_size, k_size), 30)
            frame[y1_c:head_y2, x1_c:x2_c] = blurred_head

        return frame

    # COCO keypoint indices of the face: nose, left eye, right eye, left ear, right ear.
    FACE_KEYPOINTS = (0, 1, 2, 3, 4)

    @staticmethod
    def face_box(keypoints, bbox, min_conf: float = 0.3, pad: float = 0.2):
        """
        The box around a person's FACE, from their pose keypoints, or None when fewer
        than two face keypoints are confident (the caller then falls back to the head
        box). Returns (x1, y1, x2, y2) in the keypoints' pixel space, floats.

        Keypoints give the eyes, nose and ears but not the forehead, cheeks or chin, so
        the box is grown from them in proportion to the HEAD's width, which is estimated
        from whatever is confident: the eye spacing (a head is about 2.4 eye-spacings
        wide), the ear span, or the spread of the points. The person's height sets a
        floor, so a distant person is not "blurred" with a few pixels. Vertically the
        box starts a little above the eyes (a hard hat sits above the brow and must stay
        visible) and runs well below the nose, to the chin.
        """
        if keypoints is None or bbox is None:
            return None
        pts = {}
        for i in PrivacyAnonymizer.FACE_KEYPOINTS:
            try:
                x, y, c = (float(keypoints[i][0]), float(keypoints[i][1]),
                           float(keypoints[i][2]) if len(keypoints[i]) > 2 else 1.0)
            except (IndexError, TypeError, ValueError):
                continue
            if c > min_conf and x == x and y == y:
                pts[i] = (x, y)
        if len(pts) < 2:
            return None
        xs, ys = [p[0] for p in pts.values()], [p[1] for p in pts.values()]
        person_h = max(1.0, float(bbox[3]) - float(bbox[1]))
        spread = max(max(xs) - min(xs), max(ys) - min(ys))
        head_w = max(0.07 * person_h * (1 + 2 * pad), spread * (1 + 2 * pad))
        if 1 in pts and 2 in pts:                                   # both eyes: the most reliable ruler
            head_w = max(head_w, 2.4 * abs(pts[1][0] - pts[2][0]), 2.4 * abs(pts[1][1] - pts[2][1]))
        if 3 in pts and 4 in pts:                                   # both ears: the span is nearly the head's width
            head_w = max(head_w, 1.25 * abs(pts[3][0] - pts[4][0]))
        cx = (min(xs) + max(xs)) / 2.0
        half_w = head_w / 2.0
        top = min(ys) - 0.30 * head_w                               # just above the brow
        bottom = max(ys) + 0.62 * head_w                            # down to the chin
        return (cx - half_w, top, cx + half_w, bottom)

    @staticmethod
    def anonymise_region(region: np.ndarray) -> np.ndarray:
        """
        A region with its identity removed. Large regions (a close-up face can be hundreds of
        pixels wide) are reduced to a coarse mosaic, because a Gaussian blur of fixed
        strength leaves a big face recognisable; small ones get a strong blur.
        """
        h, w = region.shape[:2]
        if w >= 24 and h >= 24:
            cells_x = max(4, min(8, w // 6))
            cells_y = max(4, min(10, h // 6))
            small = cv2.resize(region, (cells_x, cells_y), interpolation=cv2.INTER_AREA)
            mosaic = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
            k = max(3, (min(w, h) // 8) * 2 + 1)
            return cv2.GaussianBlur(mosaic, (k, k), 0)               # soften the cell edges
        k = max(3, (min(w, h) // 2) * 2 + 1)
        return cv2.GaussianBlur(region, (k, k), 0)

    @staticmethod
    def blur_face_keypoints(
        frame: np.ndarray,
        keypoints,
        bbox: list,
        blur_kernel_size: int = 35,
        min_conf: float = 0.3,
    ) -> bool:
        """
        Anonymise ONLY the face, in place, so the hard hat above it and the vest below it
        stay visible to a reviewer (the head-box blur above covers the top quarter of the
        person, which is exactly where a helmet is).

        Returns True when the face box was used, False when fewer than two face
        keypoints were confident and the head box was blurred instead. In that case the
        helmet may be covered, and the caller records it (`helmet_region_obscured`).
        """
        box = PrivacyAnonymizer.face_box(keypoints, bbox, min_conf=min_conf)
        if box is None:
            x1, y1, x2, y2 = [float(v) for v in bbox]
            PrivacyAnonymizer.anonymise_box(frame, (x1, y1, x2, y1 + 0.25 * (y2 - y1)))     # the head box, as the live blur does
            return False
        PrivacyAnonymizer.anonymise_box(frame, box)
        return True

    @staticmethod
    def anonymise_box(frame: np.ndarray, box) -> None:
        """Anonymise the part of `frame` inside `box` (x1, y1, x2, y2), in place; the edges are clamped to the image."""
        h, w = frame.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(w, int(box[2]) + 1), min(h, int(box[3]) + 1)
        if x2 - x1 < 3 or y2 - y1 < 3:
            return
        frame[y1:y2, x1:x2] = PrivacyAnonymizer.anonymise_region(frame[y1:y2, x1:x2])
