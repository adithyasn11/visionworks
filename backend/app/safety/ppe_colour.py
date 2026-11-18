# backend/app/safety/ppe_colour.py
"""
A second opinion on vests, and on helmets, from colour alone.

HELMETS (hardhat_fraction / soften_caps, at the end): the model reads a cap, a beanie
or a dark hood as a helmet, because to it a helmet is "something on the head". Hard
hats are white or a vivid colour (yellow, orange, red, blue, green); caps are mostly
dark, grey, navy or khaki. A helmet box whose shell is almost none of those colours
is not trusted as a helmet.

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
90 px tall, labelled person boxes): a torso fraction >= 0.10 holds for about 83 % of
the 1,433 people labelled as wearing a vest (72 % of the 409 under 200 px tall)
and about 1.2 % of the 400 labelled as missing one. On the factory CCTV clip in
sample_videos the orange-vest worker at the table scores 0.27. That dataset's "no
vest" people are mostly plainly dressed, so it says little about bystanders in
bright orange or yellow, who WILL trigger this: the cost is an abstention, never an
accusation. Olive, tan, brown, khaki and skin tones score 0.

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
# Safety orange is red-orange (OpenCV hue about 5-12; a real vest filmed from above on a
# factory floor measured 4-9). Brown, tan and skin sit at hue 14-17, so the hue band
# stops at 13, which is what keeps them out WITHOUT demanding the near-white brightness
# that compressed, shaded CCTV footage never has (a first attempt that did so lost
# exactly that vest: its torso scored 0.03 against the 0.10 floor).
ORANGE = dict(h=(3, 13), s_min=150, v_min=120)


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


# ── helmets ────────────────────────────────────────────────────────────────

# The shell is the top part of the helmet box; the bottom part is often forehead.
SHELL_TOP = 0.6
# White hard hats: little colour, bright. Vivid hard hats: saturated and not dark. Skin
# and brown hair share the orange hue band, so there only a strongly saturated (safety
# orange) pixel counts.
WHITE = dict(s_max=60, v_min=165)
VIVID = dict(s_min=110, v_min=110)
SKIN_HUE = (3, 22)
SKIN_HUE_S_MIN = 170
# Measured on the Construction-PPE labels (1,160 helmets, 615 heads labelled "no
# helmet"): a shell fraction under 0.06 holds for about 3 % of real helmets and about
# 40 % of uncovered heads; under 0.15 for about 6 % of helmets. A dark cap in the demo
# footage scored 0.00, the white helmet beside it 0.71.
CAP_BELOW = 0.06             # under this: a cap, not a helmet
UNSURE_BELOW = 0.15          # under this: maybe a dark helmet, maybe a cap: not "seen"
CAP_KEEP_CONF = 0.90         # a detection this sure is only lowered to "unsure", never dropped


def hardhat_fraction(frame, bbox: Sequence[float]) -> Optional[float]:
    """Share of the top of a helmet box that is hard-hat coloured (white or vivid), or None if it is too small."""
    import cv2

    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    y2 = y1 + max(2, int((y2 - y1) * SHELL_TOP))
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(fw, x2), min(fh, y2)
    if x2 - x1 < 3 or y2 - y1 < 3:
        return None
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    white = (sat < WHITE["s_max"]) & (val >= WHITE["v_min"])
    skin_band = (hue >= SKIN_HUE[0]) & (hue <= SKIN_HUE[1])
    vivid = (sat >= VIVID["s_min"]) & (val >= VIVID["v_min"]) & (~skin_band | (sat >= SKIN_HUE_S_MIN))
    return float((white | vivid).mean())


def soften_caps(detections: list, frame, present_conf: float, weak_conf: float) -> int:
    """
    Lower the confidence of helmet detections that look like caps (the list's items are replaced, not edited). Returns how many were changed.

      shell fraction < CAP_BELOW     -> below `weak_conf` (no helmet seen), unless the model is very sure,
                                        then just under `present_conf` (cannot tell)
      shell fraction < UNSURE_BELOW  -> just under `present_conf` (cannot tell)

    A detection is never raised, and vests are not touched.
    """
    changed = 0
    unsure = present_conf - 0.01
    for i, d in enumerate(detections):
        if d.get("cls") != "helmet":
            continue
        try:
            f = hardhat_fraction(frame, d["bbox"])
        except Exception:
            continue
        if f is None or f >= UNSURE_BELOW:
            continue
        conf = float(d.get("conf", 0.0))
        if f < CAP_BELOW and conf < CAP_KEEP_CONF:
            new = min(conf, weak_conf * 0.5)
        else:
            new = min(conf, unsure)
        if new < conf:
            # a new dict: the detector's own objects are never edited
            detections[i] = {**d, "conf": new, "conf_model": conf, "looks_like_cap": True}
            changed += 1
    return changed
