# backend/app/safety/ppe_colour.py
"""
A second opinion on vests, from colour alone.

The vest detector has no "no vest" class: it says "absent" whenever it finds
nothing. At a distance, from above, or for a colour it saw little of in training
(orange overhead on a factory floor, for example) it finds nothing on someone who
is plainly wearing one, and the evidence layer turns that into "no vest seen",
which is an accusation.

Hi-vis vests are the one kind of PPE that is defined by colour: fluorescent
yellow-green or orange. So when the torso of a person the detector found no vest
on is full of those colours, "nothing found" is not evidence of absence. This
module measures that. It can only ever lower a verdict from "absent" to "cannot
tell"; it never says a vest is present.

Measured on the Construction-PPE labels (images shrunk to 1280 wide, people at least
90 px tall, labelled person boxes): a torso fraction >= 0.10 holds for about 79 % of
the 1,433 people labelled as wearing a vest (66 % of the 409 under 200 px tall)
and about 0.5 % of the 400 labelled as missing one. That dataset's "no vest" people
are mostly plainly dressed, so it says little about bystanders in bright orange or
yellow, who WILL trigger this: the cost is an abstention, not an accusation.

Pure numpy/OpenCV, no model.
"""

from typing import Optional, Sequence

# Fraction of the torso region that must be hi-vis coloured to count as a cue.
DEFAULT_MIN_FRACTION = 0.10

# Where the torso is, as fractions of the person box (clear of the head and the legs,
# and of the arms either side).
TORSO_X = (0.15, 0.85)
TORSO_Y = (0.15, 0.60)

# OpenCV HSV (hue 0-179). Saturation is set high on purpose: skin, khaki and dull
# orange clothing are less saturated than a fluorescent vest.
# Brightness matters as much as hue: fluorescent material is BRIGHT, while olive, tan,
# brown and khaki work clothes share its hue but not its brightness (a review swatch
# test caught them all at the first, looser limits). Bright yellow, bright orange or a
# lime polo shirt still pass: for those the verdict becomes "cannot tell", never "no
# vest", which is the safe direction.
YELLOW_GREEN = dict(h=(22, 45), s_min=110, v_min=150)
ORANGE = dict(h=(4, 21), s_min=190, v_min=160)


def hivis_fraction(frame, bbox: Sequence[float]) -> Optional[float]:
    """Share of the person's torso that is fluorescent yellow-green or orange, or None if there is no torso to look at."""
    import cv2

    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    if w < 4 or h < 8:
        return None
    ax, bx = int(max(0, x1 + TORSO_X[0] * w)), int(min(fw, x1 + TORSO_X[1] * w))
    ay, by = int(max(0, y1 + TORSO_Y[0] * h)), int(min(fh, y1 + TORSO_Y[1] * h))
    if bx - ax < 2 or by - ay < 2:
        return None
    hsv = cv2.cvtColor(frame[ay:by, ax:bx], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    yg = (hue >= YELLOW_GREEN["h"][0]) & (hue <= YELLOW_GREEN["h"][1]) & (sat >= YELLOW_GREEN["s_min"]) & (val >= YELLOW_GREEN["v_min"])
    og = (hue >= ORANGE["h"][0]) & (hue <= ORANGE["h"][1]) & (sat >= ORANGE["s_min"]) & (val >= ORANGE["v_min"])
    return float((yg | og).mean())
