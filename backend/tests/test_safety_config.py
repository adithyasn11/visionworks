"""
Safety configuration and the SAFETY_ENABLED switch (Step 0).

What matters:
  * the switch is OFF unless explicitly turned on, so the old product cannot
    change by accident;
  * every threshold has the documented default, and the PPE ones are exactly
    the values ppe_evidence.py uses;
  * a bad override (typo, wrong type, out of range, inconsistent pair) is
    ignored with a warning instead of producing a rule that misbehaves.

Run:  venv/Scripts/python.exe backend/tests/test_safety_config.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import fields                              # noqa: E402

from app.cv import ppe_evidence as ppe                      # noqa: E402
from app.safety.config import (                             # noqa: E402
    SafetyConfig, load_config, safety_enabled, env_name,
)

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


CAPTURE = _Capture()
logging.getLogger("app.safety.config").addHandler(CAPTURE)
logging.getLogger("app.safety.config").setLevel(logging.WARNING)


def load_with(env):
    CAPTURE.messages.clear()
    return load_config(env)


# ── 1. The switch ────────────────────────────────────────────────────────────
print("1. SAFETY_ENABLED")
saved = os.environ.pop("SAFETY_ENABLED", None)
try:
    check("unset -> off", safety_enabled() is False)
    for value in ("1", "true", "TRUE", " yes ", "on"):
        os.environ["SAFETY_ENABLED"] = value
        check(f"{value!r} -> on", safety_enabled() is True)
    for value in ("", "0", "false", "no", "off", "ture", "enabled", "2"):
        os.environ["SAFETY_ENABLED"] = value
        check(f"{value!r} -> off", safety_enabled() is False)
finally:
    os.environ.pop("SAFETY_ENABLED", None)
    if saved is not None:
        os.environ["SAFETY_ENABLED"] = saved

# ── 2. Defaults ──────────────────────────────────────────────────────────────
print("2. Defaults")
cfg = load_with({})
check("no env -> plain defaults", cfg == SafetyConfig())
check("no warnings with no env", CAPTURE.messages == [], str(CAPTURE.messages))
same_as_ppe = {
    "ppe_present_conf": ppe.PRESENT_CONF, "ppe_weak_conf": ppe.WEAK_CONF,
    "ppe_window_frames": ppe.WINDOW_FRAMES, "ppe_min_clear_frames": ppe.MIN_CLEAR_FRAMES,
    "ppe_presence_fraction": ppe.PRESENCE_FRACTION, "ppe_absence_fraction": ppe.ABSENCE_FRACTION,
    "ppe_stale_s": ppe.STALE_SECONDS, "ppe_min_person_height_px": ppe.MIN_PERSON_HEIGHT_PX,
    "ppe_min_person_width_px": ppe.MIN_PERSON_WIDTH_PX, "ppe_crowd_iou": ppe.CROWD_IOU,
}
for name, expected in same_as_ppe.items():
    check(f"{name} matches ppe_evidence", getattr(cfg, name) == expected,
          f"{getattr(cfg, name)} vs {expected}")
documented = {          # design doc §12
    "ppe_conf_floor": 0.20, "ppe_present_conf": 0.45, "ppe_weak_conf": 0.20,
    "ppe_window_frames": 30, "ppe_min_clear_frames": 8, "ppe_presence_fraction": 0.60,
    "ppe_absence_fraction": 0.90, "ppe_stale_s": 10.0, "ppe_min_person_height_px": 90.0,
    "ppe_min_person_width_px": 28.0, "ppe_crowd_iou": 0.30, "enter_confirm_s": 0.7,
    "exit_confirm_s": 1.0, "lost_grace_s": 5.0, "entry_evidence_wait_s": 4.0,
    "continuous_ppe_grace_s": 3.0, "dwell_warn_fraction": 0.8, "man_down_angle_deg": 60.0,
    "man_down_min_s": 5.0, "occupancy_grace_s": 10.0, "evidence_retention_days": 7,
}
for name, expected in documented.items():
    check(f"{name} = {expected} (§12)", getattr(cfg, name) == expected, str(getattr(cfg, name)))
check("snapshot_unverifiable off by default", cfg.snapshot_unverifiable is False)
check("as_dict has every field",
      set(cfg.as_dict()) == {f.name for f in fields(SafetyConfig)})
try:
    cfg.enter_confirm_s = 9.0                          # type: ignore[misc]
    check("config is immutable", False, "assignment succeeded")
except Exception:
    check("config is immutable", True)

# ── 3. Valid overrides ───────────────────────────────────────────────────────
print("3. Valid overrides")
cfg = load_with({
    "SAFETY_ENTER_CONFIRM_S": "0.5",
    "SAFETY_PPE_WINDOW_FRAMES": "45",
    "SAFETY_PPE_EVERY_N_FRAMES": "3.0",
    "SAFETY_SNAPSHOT_UNVERIFIABLE": "yes",
    "SAFETY_EVIDENCE_RETENTION_DAYS": " 14 ",
})
check("float override", cfg.enter_confirm_s == 0.5)
check("int override", cfg.ppe_window_frames == 45 and isinstance(cfg.ppe_window_frames, int))
check("'3.0' accepted as int 3", cfg.ppe_every_n_frames == 3)
check("bool override", cfg.snapshot_unverifiable is True)
check("whitespace trimmed", cfg.evidence_retention_days == 14)
check("untouched fields keep defaults", cfg.exit_confirm_s == 1.0)
check("no warnings for valid overrides", CAPTURE.messages == [], str(CAPTURE.messages))
check("env name format", env_name("enter_confirm_s") == "SAFETY_ENTER_CONFIRM_S")
check("documented env names exist",
      {"SAFETY_ENTER_CONFIRM_S", "SAFETY_REALERT_AFTER_S", "SAFETY_SNAPSHOT_UNVERIFIABLE"}
      <= {env_name(f.name) for f in fields(SafetyConfig)})

# ── 4. Invalid overrides fall back with a warning ────────────────────────────
print("4. Invalid overrides")
bad_cases = [
    ("SAFETY_ENTER_CONFIRM_S", "abc", "enter_confirm_s"),          # not a number
    ("SAFETY_ENTER_CONFIRM_S", "-1", "enter_confirm_s"),           # below range
    ("SAFETY_PPE_PRESENCE_FRACTION", "1.5", "ppe_presence_fraction"),
    ("SAFETY_PPE_WINDOW_FRAMES", "30.5", "ppe_window_frames"),     # not whole
    ("SAFETY_PPE_WINDOW_FRAMES", "0", "ppe_window_frames"),
    ("SAFETY_SNAPSHOT_UNVERIFIABLE", "maybe", "snapshot_unverifiable"),
    ("SAFETY_MAN_DOWN_MIN_S", "nan", "man_down_min_s"),
    ("SAFETY_MAN_DOWN_MIN_S", "inf", "man_down_min_s"),
]
for env_key, raw, field_name in bad_cases:
    cfg = load_with({env_key: raw})
    check(f"{env_key}={raw!r} -> default",
          getattr(cfg, field_name) == getattr(SafetyConfig(), field_name),
          str(getattr(cfg, field_name)))
    check(f"{env_key}={raw!r} warns", any(env_key in m for m in CAPTURE.messages))
cfg = load_with({"SAFETY_ENTER_CONFIRM_S": "   "})
check("blank value -> default, no warning",
      cfg.enter_confirm_s == 0.7 and CAPTURE.messages == [])

# ── 5. Inconsistent pairs reset both ─────────────────────────────────────────
print("5. Inconsistent pairs")
cfg = load_with({"SAFETY_PPE_WEAK_CONF": "0.5", "SAFETY_PPE_PRESENT_CONF": "0.4"})
check("weak >= present -> both defaults",
      cfg.ppe_weak_conf == 0.20 and cfg.ppe_present_conf == 0.45)
check("pair conflict warns", len(CAPTURE.messages) == 1, str(CAPTURE.messages))
cfg = load_with({"SAFETY_PPE_MIN_CLEAR_FRAMES": "40"})          # > window 30
check("min_clear > window -> defaults", cfg.ppe_min_clear_frames == 8 and cfg.ppe_window_frames == 30)
cfg = load_with({"SAFETY_LOST_GRACE_S": "20"})                  # > ttl 8
check("lost_grace > lost_ttl -> defaults", cfg.lost_grace_s == 5.0 and cfg.lost_ttl_s == 8.0)
cfg = load_with({"SAFETY_LOST_GRACE_S": "8", "SAFETY_LOST_TTL_S": "8"})
check("lost_grace == lost_ttl allowed", cfg.lost_grace_s == 8.0 and cfg.lost_ttl_s == 8.0)
cfg = load_with({"SAFETY_MAN_DOWN_UPRIGHT_DEG": "70"})          # >= angle 60
check("upright >= down angle -> defaults",
      cfg.man_down_upright_deg == 45.0 and cfg.man_down_angle_deg == 60.0)
cfg = load_with({"SAFETY_PPE_WINDOW_FRAMES": "60", "SAFETY_PPE_MIN_CLEAR_FRAMES": "40"})
check("consistent pair kept", cfg.ppe_window_frames == 60 and cfg.ppe_min_clear_frames == 40)

# ── 6. Every range contains its default ──────────────────────────────────────
print("6. Ranges")
from app.safety.config import _RANGES                      # noqa: E402
numeric = [f.name for f in fields(SafetyConfig) if not isinstance(getattr(SafetyConfig(), f.name), bool)]
check("every numeric field has a range", set(numeric) == set(_RANGES),
      str(set(numeric) ^ set(_RANGES)))
for name, (lo, hi) in _RANGES.items():
    d = getattr(SafetyConfig(), name)
    check(f"default {name}={d} inside {lo}..{hi}", lo <= d <= hi)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
