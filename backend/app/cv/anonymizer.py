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

    Defaults to False. Blur destroys the face signal, and the identity pipeline
    (the door camera in particular) needs that signal to exist at all — a
    development default of ON means every face experiment silently measures
    blurred pixels. Deployments that want privacy-by-default set
    PRIVACY_BLUR_DEFAULT=true, which is the one line the deployment docs call
    out; the runtime toggle is unaffected either way.
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
