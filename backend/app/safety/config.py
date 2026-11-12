# backend/app/safety/config.py
"""
Every safety threshold in one place (design doc §12), each overridable by an
environment variable named SAFETY_<FIELD_NAME_IN_CAPITALS>, for example
SAFETY_ENTER_CONFIRM_S=0.5.

Why one module:
  * a decision stores the thresholds it was made with (`as_dict()`), so it can
    be reproduced and explained later;
  * the threshold sweep in the evaluation (§14.G) changes these values from the
    environment without touching code.

An override that cannot be parsed, or is outside its allowed range, is ignored
with a warning and the default is used. A typo in .env must never silently
produce a rule that cannot fire or that fires on everything.

The PPE evidence defaults are imported from ppe_evidence.py rather than
repeated, so the two can never drift apart.
"""

import logging
import os
from dataclasses import dataclass, fields, asdict

from app.cv import ppe_evidence as _ppe

_log = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

ENV_PREFIX = "SAFETY_"


def safety_enabled() -> bool:
    """
    Whether the safety layer runs at all. OFF by default.

    Read at call time (not at import) so load_dotenv() in main.py has already
    run, and so tests can flip it without reimporting. Only an explicit truthy
    value turns it on; anything else, including typos, leaves it off. That is
    the safe direction: with the flag off the old product is untouched.
    """
    raw = os.getenv("SAFETY_ENABLED")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class SafetyConfig:
    # ── PPE detection and evidence (F3-F5) ──────────────────────────────────
    ppe_conf_floor: float = 0.20                         # detector returns boxes at/above this
    ppe_present_conf: float = _ppe.PRESENT_CONF          # "seen"
    ppe_weak_conf: float = _ppe.WEAK_CONF                # below this is "nothing there"
    ppe_window_frames: int = _ppe.WINDOW_FRAMES
    ppe_min_clear_frames: int = _ppe.MIN_CLEAR_FRAMES
    ppe_presence_fraction: float = _ppe.PRESENCE_FRACTION
    ppe_absence_fraction: float = _ppe.ABSENCE_FRACTION
    ppe_stale_s: float = _ppe.STALE_SECONDS
    ppe_min_person_height_px: float = _ppe.MIN_PERSON_HEIGHT_PX
    ppe_min_person_width_px: float = _ppe.MIN_PERSON_WIDTH_PX
    ppe_crowd_iou: float = _ppe.CROWD_IOU
    ppe_every_n_frames: int = 2                          # run the PPE model every Nth frame
    ppe_crop_below_px: float = 360.0                     # a person shorter than this (in the PPE frame) is also checked up close; 0 = never
    ppe_hivis_min_fraction: float = 0.10                 # hi-vis coloured share of a torso that stops 'no vest found' being read as 'no vest' (0 = off)
    ppe_max_crops: int = 10                              # close-up checks per frame, most distant first dropped
    pose_max_width: int = 1280                           # width the people detector sees (the original frame, shrunk only if wider)
    ppe_max_width: int = 1280                            # frame width the PPE model sees (a worker must be >= ppe_min_person_height_px tall in THIS frame)

    # ── Zone events (F7) ─────────────────────────────────────────────────────
    enter_confirm_s: float = 0.7
    exit_confirm_s: float = 1.0
    lost_grace_s: float = 5.0        # unseen this long inside a zone -> LOST_IN_ZONE
    lost_ttl_s: float = 8.0          # unseen this long -> visit closed (EXITED, reason LOST)
    zone_gap_tolerance_s: float = 0.5   # a missed detection this short does not restart a timed zone change

    # ── Decisions (F9, R1-R4) ────────────────────────────────────────────────
    entry_evidence_wait_s: float = 4.0
    continuous_ppe_grace_s: float = 3.0
    dwell_warn_fraction: float = 0.8
    realert_after_s: float = 60.0    # min gap between two episodes of one (rule, zone, worker)
    exit_visible_slack_s: float = 1.5   # required exit: a worker counts as still in view if seen this recently when the timer runs out

    # ── Man-down (F17) ───────────────────────────────────────────────────────
    man_down_angle_deg: float = 60.0         # torso angle from vertical counted as "down"
    man_down_min_s: float = 5.0
    man_down_upright_deg: float = 45.0       # SITTING is only excluded below this angle
    man_down_still_speed: float = 0.15       # person-heights per second

    # ── Lone worker / occupancy (F18) ───────────────────────────────────────
    occupancy_grace_s: float = 10.0
    occupancy_window_s: float = 2.0
    occupancy_min_clear_fraction: float = 0.6   # below this the count is UNVERIFIABLE
    occupancy_min_person_conf: float = 0.4      # a person detected less confidently than this is not "clearly" counted
    occupancy_overlap_iou: float = 0.3          # two boxes overlapping this much: neither is "clearly" counted
    occupancy_min_height_px: float = 60.0       # a person shorter than this (in the 640-px-wide working frame) is not "clearly" counted: small, distant people are the ones the detector misses

    # ── Evidence snapshots (F11) ─────────────────────────────────────────────
    evidence_retention_days: int = 7
    snapshot_unverifiable: bool = False

    # ── Local database retention ─────────────────────────────────────────────
    # Rows already copied to Postgres are deleted from the local file after this
    # many days; Postgres holds the durable copy.
    local_synced_retention_days: int = 7
    # Standalone mode only (no Supabase to sync to, so nothing will ever mark a
    # row as synced): rows older than this are deleted so the file cannot grow
    # for ever. With a service key configured, unsynced rows are NEVER deleted.
    local_standalone_retention_days: int = 30

    # ── Source robustness ────────────────────────────────────────────────────
    # A live camera that delivers no picture for this long has stopped: open visits are closed as
    # UNVERIFIABLE (nothing can be said about a stay nobody saw) instead of waiting for it to come back.
    camera_lost_s: float = 5.0

    def as_dict(self) -> dict:
        """Plain dict of every threshold, stored with each decision."""
        return asdict(self)


# Allowed range per field (inclusive). An override outside it falls back to the
# default. Ranges are deliberately generous: they reject nonsense (negative
# times, fractions above 1), not unusual-but-valid tuning.
_RANGES = {
    "ppe_conf_floor": (0.0, 1.0),
    "ppe_present_conf": (0.0, 1.0),
    "ppe_weak_conf": (0.0, 1.0),
    "ppe_window_frames": (3, 300),
    "ppe_min_clear_frames": (1, 300),
    "ppe_presence_fraction": (0.0, 1.0),
    "ppe_absence_fraction": (0.0, 1.0),
    "ppe_stale_s": (0.5, 600.0),
    "ppe_min_person_height_px": (1.0, 4000.0),
    "ppe_min_person_width_px": (1.0, 4000.0),
    "ppe_crowd_iou": (0.0, 1.0),
    "ppe_every_n_frames": (1, 30),
    "ppe_max_width": (320, 3840),
    "ppe_crop_below_px": (0.0, 4000.0),
    "ppe_max_crops": (0, 50),
    "ppe_hivis_min_fraction": (0.0, 1.0),
    "pose_max_width": (320, 3840),
    "enter_confirm_s": (0.0, 30.0),
    "exit_confirm_s": (0.0, 30.0),
    "lost_grace_s": (0.0, 600.0),
    "lost_ttl_s": (0.0, 3600.0),
    "zone_gap_tolerance_s": (0.0, 5.0),
    "entry_evidence_wait_s": (0.0, 60.0),
    "continuous_ppe_grace_s": (0.0, 60.0),
    "dwell_warn_fraction": (0.1, 1.0),
    "realert_after_s": (0.0, 86400.0),
    "exit_visible_slack_s": (0.0, 30.0),
    "man_down_angle_deg": (10.0, 90.0),
    "man_down_min_s": (1.0, 120.0),
    "man_down_upright_deg": (0.0, 90.0),
    "man_down_still_speed": (0.0, 10.0),
    "occupancy_grace_s": (0.0, 600.0),
    "occupancy_window_s": (0.1, 60.0),
    "occupancy_min_clear_fraction": (0.0, 1.0),
    "occupancy_min_person_conf": (0.0, 1.0),
    "occupancy_overlap_iou": (0.0, 1.0),
    "occupancy_min_height_px": (0.0, 2000.0),
    "evidence_retention_days": (1, 365),
    "local_synced_retention_days": (1, 365),
    "local_standalone_retention_days": (1, 3650),
    "camera_lost_s": (1.0, 600.0),
}

# Pairs that must hold between fields: (smaller, larger, strictly_less).
# If violated, BOTH fields go back to their defaults — keeping one override and
# not the other could still leave an inconsistent pair.
_ORDERED_PAIRS = [
    ("ppe_conf_floor", "ppe_weak_conf", False),   # the detector must report everything the evidence layer calls "weak"
    ("ppe_weak_conf", "ppe_present_conf", True),
    ("ppe_min_clear_frames", "ppe_window_frames", False),
    ("lost_grace_s", "lost_ttl_s", False),
    ("man_down_upright_deg", "man_down_angle_deg", True),
]


def env_name(field_name: str) -> str:
    return ENV_PREFIX + field_name.upper()


def _parse(raw: str, kind):
    text = raw.strip()
    if kind is bool:
        low = text.lower()
        if low in _TRUTHY:
            return True
        if low in _FALSY:
            return False
        raise ValueError(f"not a boolean: {raw!r}")
    if kind is int:
        value = float(text)              # accept "30" and "30.0", reject "30.5"
        if not value.is_integer():
            raise ValueError(f"not a whole number: {raw!r}")
        return int(value)
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite number: {raw!r}")
    return value


def load_config(environ=None) -> SafetyConfig:
    """
    Build the config from defaults plus SAFETY_* overrides.

    `environ` defaults to os.environ; tests pass a plain dict.
    """
    env = os.environ if environ is None else environ
    defaults = SafetyConfig()
    values = {}

    for f in fields(SafetyConfig):
        default = getattr(defaults, f.name)
        values[f.name] = default
        raw = env.get(env_name(f.name))
        if raw is None or raw.strip() == "":
            continue
        try:
            parsed = _parse(raw, type(default))
        except ValueError as e:
            _log.warning(f"{env_name(f.name)} ignored ({e}); using default {default}")
            continue
        lo_hi = _RANGES.get(f.name)
        if lo_hi is not None and not (lo_hi[0] <= parsed <= lo_hi[1]):
            _log.warning(f"{env_name(f.name)}={parsed} is outside {lo_hi[0]}..{lo_hi[1]}; "
                         f"using default {default}")
            continue
        values[f.name] = parsed

    for small, large, strict in _ORDERED_PAIRS:
        a, b = values[small], values[large]
        if (a >= b) if strict else (a > b):
            _log.warning(f"{env_name(small)}={a} must be {'<' if strict else '<='} "
                         f"{env_name(large)}={b}; using defaults for both")
            values[small] = getattr(defaults, small)
            values[large] = getattr(defaults, large)

    return SafetyConfig(**values)
