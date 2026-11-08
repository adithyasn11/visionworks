# backend/app/safety/rules.py
"""
Safety rules: what to check, parsed and validated so that a rule which could never
fire is refused when it is saved, not discovered silently when nothing happens.

Seven rule types (design 24.6). Their parameters are checked here, table-driven:

    PPE_ENTRY       RESTRICTED zone  required_ppe, mode AT_ENTRY | VIA_CHECKPOINT, ...
    PPE_CONTINUOUS  RESTRICTED zone  required_ppe, grace_seconds
    MAX_DWELL       any real zone    max_seconds, warn_fraction
    REQUIRED_EXIT   RESTRICTED zone  exit_zone_id (an EXIT zone), max_seconds_to_exit
    OCCUPANCY       any real zone    min_people / max_people, grace_seconds, window_seconds
    MAN_DOWN        a zone or the whole camera
    WALK_ONLY       any real zone

Where the rules come from. They live in Postgres (`safety_rules`, Step 7) and are
handed in here as rows; until then, and for an organisation with none, a built-in
default applies so the demo works with no setup: every RESTRICTED zone requires
helmet and vest on entry (PPE_ENTRY, AT_ENTRY).

An invalid row is reported and skipped, never allowed to crash a video session.
"""

import logging
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

from app.db.safety_models import RULE_TYPES, SEVERITIES
from app.safety.schemas import Rule
from app.safety.zone_events import RULE_ZONE_TYPES

_log = logging.getLogger(__name__)

PPE_ITEMS = ("helmet", "safety_vest")
DEFAULT_MIN_EVIDENCE_CONFIDENCE = 0.6


class RuleError(ValueError):
    """A rule that cannot work, with a message safe to show whoever wrote it."""


def _zone_type(zones: Dict[str, dict], zone_id) -> Optional[str]:
    z = zones.get(str(zone_id)) if zone_id is not None else None
    return None if z is None else str(z.get("zone_type") or "GENERAL").upper()


def _number(params, name, default, lo, hi, *, integer=False, required=False):
    raw = params.get(name, None)
    if raw is None:
        if required:
            raise RuleError(f"{name} is required")
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw or raw in (float("inf"), float("-inf")):
        raise RuleError(f"{name} must be a number, got {raw!r}")
    if integer and float(raw) != int(raw):
        raise RuleError(f"{name} must be a whole number, got {raw!r}")
    if not lo <= raw <= hi:
        raise RuleError(f"{name} must be between {lo} and {hi}, got {raw}")
    return int(raw) if integer else float(raw)


def _ppe_list(params):
    raw = params.get("required_ppe", list(PPE_ITEMS))
    if not isinstance(raw, (list, tuple)) or not raw:
        raise RuleError("required_ppe must list at least one of helmet, safety_vest")
    items = []
    for item in raw:
        if item not in PPE_ITEMS:
            raise RuleError(f"required_ppe may only contain {', '.join(PPE_ITEMS)}, got {item!r}")
        if item not in items:
            items.append(item)
    return items


def validate_params(rule_type: str, zone_id: Optional[str], params: Optional[dict], zones: Dict[str, dict]) -> dict:
    """The parameters with defaults filled in, or a RuleError. `zones` maps zone id to its config dict."""
    if rule_type not in RULE_TYPES:
        raise RuleError(f"rule type must be one of {', '.join(RULE_TYPES)}, got {rule_type!r}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise RuleError("params must be an object")

    ztype = _zone_type(zones, zone_id)
    needs_zone = rule_type != "MAN_DOWN"
    if needs_zone and not zone_id:
        raise RuleError(f"{rule_type} must be anchored to a zone")
    if zone_id and ztype is None:
        raise RuleError(f"zone {zone_id!r} is not one of this camera's zones")
    restricted_only = rule_type in ("PPE_ENTRY", "PPE_CONTINUOUS", "REQUIRED_EXIT")
    if restricted_only and ztype != "RESTRICTED":
        raise RuleError(f"{rule_type} must be anchored to a RESTRICTED zone (this one is {ztype})")
    if zone_id and not restricted_only and ztype not in RULE_ZONE_TYPES:
        raise RuleError(f"{rule_type} needs a real zone (a General zone has no safety meaning), this one is {ztype}")

    out = {}
    if rule_type == "PPE_ENTRY":
        out["required_ppe"] = _ppe_list(params)
        mode = params.get("mode", "AT_ENTRY")
        if mode not in ("AT_ENTRY", "VIA_CHECKPOINT"):
            raise RuleError(f"mode must be AT_ENTRY or VIA_CHECKPOINT, got {mode!r}")
        out["mode"] = mode
        if mode == "VIA_CHECKPOINT":
            cz = params.get("checkpoint_zone_id")
            if not cz:
                raise RuleError("checkpoint_zone_id is required for VIA_CHECKPOINT")
            if _zone_type(zones, cz) != "PPE_CHECKPOINT":
                raise RuleError(f"checkpoint_zone_id must be a PPE_CHECKPOINT zone on this camera, got {cz!r}")
            out["checkpoint_zone_id"] = str(cz)
            out["max_seconds_since_checkpoint"] = _number(params, "max_seconds_since_checkpoint", 60.0, 5, 600)
        elif params.get("checkpoint_zone_id"):
            raise RuleError("checkpoint_zone_id only applies to VIA_CHECKPOINT")
    elif rule_type == "PPE_CONTINUOUS":
        out["required_ppe"] = _ppe_list(params)
        out["grace_seconds"] = _number(params, "grace_seconds", 3.0, 1, 30)
    elif rule_type == "MAX_DWELL":
        out["max_seconds"] = _number(params, "max_seconds", None, 10, 86400, required=True)
        out["warn_fraction"] = _number(params, "warn_fraction", 0.8, 0.1, 1.0)
    elif rule_type == "REQUIRED_EXIT":
        ez = params.get("exit_zone_id")
        if not ez:
            raise RuleError("exit_zone_id is required")
        if _zone_type(zones, ez) != "EXIT":
            raise RuleError(f"exit_zone_id must be an EXIT zone on this camera, got {ez!r}")
        out["exit_zone_id"] = str(ez)
        out["max_seconds_to_exit"] = _number(params, "max_seconds_to_exit", 30.0, 3, 600)
    elif rule_type == "OCCUPANCY":
        lo = _number(params, "min_people", None, 2, 1000, integer=True)
        hi = _number(params, "max_people", None, 1, 1000, integer=True)
        if lo is None and hi is None:
            raise RuleError("give min_people, max_people or both")
        if lo is not None and hi is not None and lo > hi:
            raise RuleError("min_people cannot be more than max_people")
        out["min_people"], out["max_people"] = lo, hi
        out["grace_seconds"] = _number(params, "grace_seconds", 10.0, 3, 120)
        out["window_seconds"] = _number(params, "window_seconds", 2.0, 0.1, 60)
    elif rule_type == "MAN_DOWN":
        out["min_seconds_down"] = _number(params, "min_seconds_down", 5.0, 3, 60)
        allowed = params.get("lying_allowed_zone_ids", [])
        if not isinstance(allowed, (list, tuple)):
            raise RuleError("lying_allowed_zone_ids must be a list")
        for z in allowed:
            if _zone_type(zones, z) is None:
                raise RuleError(f"lying_allowed_zone_ids names an unknown zone {z!r}")
        out["lying_allowed_zone_ids"] = [str(z) for z in allowed]
    elif rule_type == "WALK_ONLY":
        out["max_speed_heights_per_s"] = _number(params, "max_speed_heights_per_s", 1.8, 0.1, 20)
        out["min_seconds"] = _number(params, "min_seconds", 2.0, 0.5, 60)
    return out


def parse_rule(row: dict, zones: Dict[str, dict]) -> Rule:
    """One database row (or a plain dict) into a Rule, or a RuleError. Accepts camelCase or snake_case keys."""
    if not isinstance(row, dict):
        raise RuleError("a rule must be an object")

    def pick(*names, default=None):
        for n in names:
            if n in row and row[n] is not None:
                return row[n]
        return default

    rule_type = pick("type", "ruleType")
    zone_id = pick("zone_id", "zoneId", "zone", "zoneSlug")
    zone_id = None if zone_id is None else str(zone_id)
    severity = pick("severity", default="WARNING")
    if severity not in SEVERITIES:
        raise RuleError(f"severity must be one of {', '.join(SEVERITIES)}, got {severity!r}")
    floor = pick("min_evidence_confidence", "minEvidenceConfidence", default=DEFAULT_MIN_EVIDENCE_CONFIDENCE)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0.0 <= floor <= 1.0:
        raise RuleError(f"minEvidenceConfidence must be between 0 and 1, got {floor!r}")
    params = validate_params(rule_type, zone_id, pick("params", default={}), zones)
    z = zones.get(zone_id) if zone_id else None
    rule_id = pick("id")
    if rule_id is not None:
        try:
            rule_id = str(uuid.UUID(str(rule_id)))
        except ValueError:
            # Every decision stores its rule's id and the database column is a uuid: a rule with
            # any other id would silently lose every decision it makes.
            raise RuleError(f"id must be a uuid, got {rule_id!r}") from None
    enabled = pick("is_enabled", "isEnabled", "enabled", default=True)
    if isinstance(enabled, str):
        low = enabled.strip().lower()
        if low not in ("true", "false", "1", "0"):
            raise RuleError(f"isEnabled must be true or false, got {enabled!r}")
        enabled = low in ("true", "1")
    elif not isinstance(enabled, (bool, int)):
        raise RuleError(f"isEnabled must be true or false, got {enabled!r}")
    return Rule(
        type=rule_type, zone_id=zone_id, params=params,
        id=rule_id,
        name=str(pick("name", default="") or ""),
        zone_name=str((z or {}).get("zone_name") or zone_id or ""),
        severity=severity, enabled=bool(enabled),
        min_evidence_confidence=float(floor), source="database")


def _zones_by_id(zones_config) -> Dict[str, dict]:
    return {str(z["zone_id"]): z for z in (zones_config or []) if z.get("zone_id") is not None}


def default_rules(zones_config) -> List[Rule]:
    """
    The built-in rule: every RESTRICTED zone requires helmet and vest at the moment
    of entry. It exists so the system produces a verdict with zero configuration.
    """
    out = []
    for z in zones_config or []:
        if str(z.get("zone_type") or "GENERAL").upper() == "RESTRICTED":
            zid = str(z["zone_id"])
            out.append(Rule(
                type="PPE_ENTRY", zone_id=zid,
                params={"required_ppe": list(PPE_ITEMS), "mode": "AT_ENTRY"},
                name="Helmet and vest to enter", zone_name=str(z.get("zone_name") or zid),
                severity="WARNING", min_evidence_confidence=DEFAULT_MIN_EVIDENCE_CONFIDENCE, source="default"))
    return out


def parse_rules(rows: Iterable[dict], zones_config) -> Tuple[List[Rule], List[str]]:
    """(valid rules, one message per refused row). A bad row never stops the good ones."""
    zones = _zones_by_id(zones_config)
    rules, errors = [], []
    for i, row in enumerate(rows or []):
        try:
            rules.append(parse_rule(row, zones))
        except RuleError as e:
            errors.append(f"rule {i + 1}: {e}")
        except Exception as e:                                  # a malformed row of any kind
            errors.append(f"rule {i + 1}: unreadable ({type(e).__name__})")
    return rules, errors


def load_rules(org_id: Optional[str], zones_config, rows: Optional[Iterable[dict]] = None) -> List[Rule]:
    """
    The rules for a session.

    `rows` are the organisation's rules from Postgres once Step 7 supplies them. With
    none (the table does not exist yet, the database is unreachable, or the
    organisation has made none) the built-in default applies. Rows that are given
    are trusted over the default: if they are all invalid the session has no rules
    rather than silently falling back to ones the organisation did not write, and
    the problems are logged.
    """
    rows = list(rows) if rows is not None else []
    if not rows:
        return default_rules(zones_config)
    rules, errors = parse_rules(rows, zones_config)
    for message in errors:
        _log.warning(f"safety rule refused: {message}")
    return [r for r in rules if r.enabled]
