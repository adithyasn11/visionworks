# backend/app/safety/ppe_crops.py
"""
Look at a small, distant worker up close.

A helmet or vest on someone 100-200 px tall is a handful of pixels once the whole
frame has been squeezed to the model's working size, and the detector misses it
(measured: on wide scenes a worn vest is read as absent about a quarter of the
time). Cutting the person out of the ORIGINAL frame, with a margin for the helmet
above and the arms either side, and enlarging the cut-out to about the size the
model was trained on gives it far more to work with.

Pure geometry and resizing, no model and no torch, so it is testable on its own:

    crop = person_crop(frame, bbox)            # a Crop (pixels + how to map back)
    boxes = crop.to_frame(detections)          # detections in the crop -> frame coordinates

Used by the PPE runner (live) and by ppe_training/eval_construction_ppe.py.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

# Margins around the person box, as fractions of its size. A helmet can sit above
# the box; a vest can bulge sideways; a little context helps the detector.
PAD_X = 0.30
PAD_TOP = 0.20
PAD_BOTTOM = 0.08

# The detector was trained at 640; a person cut-out is shown to it at about that
# height. Never enlarged more than this, so a few pixels are not blown up into mush.
TARGET_HEIGHT_PX = 640
MAX_ENLARGE = 4.0

# And never larger than this on the long side, whatever the box shape: a person lying
# down, or two merged boxes, would otherwise make a very wide cut-out that costs far
# more to run than it is worth (a 9000 px wide crop took ~200 ms).
MAX_SIDE_PX = 1280

# People already this tall in the frame the model sees are fine as they are.
DEFAULT_CROP_BELOW_PX = 360.0


@dataclass
class Crop:
    image: object                 # the cut-out, resized (a BGR ndarray)
    x0: float                     # where its top-left corner is in the frame
    y0: float
    scale: float                  # crop pixels per frame pixel, vertically (>= 1 means enlarged)
    scale_x: float = 0.0          # ...horizontally (differs from `scale` only by rounding); 0 = same

    def to_frame(self, detections: Sequence[dict]) -> List[dict]:
        """Detections found in the crop, expressed in the frame's own coordinates."""
        out = []
        for d in detections:
            x1, y1, x2, y2 = d["bbox"]
            sx = self.scale_x or self.scale
            out.append({**d, "bbox": [self.x0 + x1 / sx, self.y0 + y1 / self.scale,
                                      self.x0 + x2 / sx, self.y0 + y2 / self.scale]})
        return out


def needs_crop(person_height_px: float, below_px: float = DEFAULT_CROP_BELOW_PX) -> bool:
    return 0 < person_height_px < below_px


def crop_window(bbox: Sequence[float], frame_w: int, frame_h: int):
    """(x0, y0, x1, y1) of the cut-out in frame pixels, clamped to the frame; None if it would be empty."""
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    if w <= 1 or h <= 1:
        return None
    cx0 = max(0.0, x1 - PAD_X * w)
    cy0 = max(0.0, y1 - PAD_TOP * h)
    cx1 = min(float(frame_w), x2 + PAD_X * w)
    cy1 = min(float(frame_h), y2 + PAD_BOTTOM * h)
    if cx1 - cx0 < 2 or cy1 - cy0 < 2:
        return None
    return cx0, cy0, cx1, cy1


def person_crop(frame, bbox: Sequence[float], target_height: int = TARGET_HEIGHT_PX,
                max_enlarge: float = MAX_ENLARGE) -> Optional[Crop]:
    """
    Cut `bbox` (frame pixels) out of `frame` with margins and enlarge it. None when
    there is nothing sensible to cut. The cut is taken from `frame` as given, so
    pass the highest-resolution frame there is.
    """
    import cv2

    fh, fw = frame.shape[:2]
    window = crop_window(bbox, fw, fh)
    if window is None:
        return None
    x0, y0, x1, y1 = window
    ix0, iy0, ix1, iy1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    piece = frame[iy0:iy1, ix0:ix1]
    if piece.size == 0:
        return None
    scale = min(max_enlarge, target_height / float(piece.shape[0]),
                MAX_SIDE_PX / float(max(piece.shape[0], piece.shape[1])))
    scale = max(scale, 1e-6)
    if abs(scale - 1.0) > 0.02:
        piece = cv2.resize(piece, (max(1, int(round(piece.shape[1] * scale))), max(1, int(round(piece.shape[0] * scale)))),
                           interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    # the actual scales after rounding, so the mapping back is exact
    return Crop(image=piece, x0=float(ix0), y0=float(iy0),
                scale=piece.shape[0] / float(iy1 - iy0), scale_x=piece.shape[1] / float(ix1 - ix0))
