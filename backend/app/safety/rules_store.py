# backend/app/safety/rules_store.py
"""
The organisation's safety rules, from Postgres, for a processing session.

Until now every session used the built-in default (helmet and vest on entry to each RESTRICTED zone).
Managers can now write their own rules (the safety API, Step 11), and this is where a session reads them.

HOW THEY COMBINE WITH THE DEFAULT

  * Rules are read with the service key, filtered to the organisation EXPLICITLY (the service key
    bypasses Row Level Security, so the filter is the boundary).
  * A rule applies to a zone, and a zone belongs to ONE CAMERA (slugs are unique only per camera, so two
    cameras can both have a "zone_1"). A session therefore resolves its own Postgres camera first and takes
    only the zones of THAT camera; a rule for another camera's zone never reaches it, whatever the slug.
  * References to other zones inside a rule's parameters (the checkpoint or exit zone, the zones where lying
    down is allowed) are Postgres ids in the database and slugs in a session; they are translated here.
  * The built-in default still protects every RESTRICTED zone that has no helmet/vest-on-entry rule THE ENGINE
    CAN RUN, enabled or not: only a rule that parsed and is evaluated at entry counts as covering its zone
    (a disabled one is the deliberate off switch). A rule the engine cannot run (a VIA_CHECKPOINT rule, a rule
    that does not validate) never removes the default: a zone must not end up with no check at all because a
    rule beside it is unusable.
  * Disabling a zone's default is therefore a deliberate act: create an AT_ENTRY rule for that zone and
    switch it off. Deleted (soft-deleted) rules do not count.
  * If Postgres cannot be reached, the camera or organisation is unknown, the session runs on the default
    alone (and logs it) rather than refusing to run: a safety check that quietly stops is worse than the
    default one. That also means a zone whose default was switched off is protected again for that session.
  * Rules are read when a session STARTS. A running session keeps the rules it started with; a change applies
    from the next run.
"""

import json
import logging
from typing import List, Optional

from app.safety.rules import default_rules, parse_rules
from app.safety.schemas import Rule

_log = logging.getLogger(__name__)

# Parameters that name other zones.
_ZONE_REF_SCALARS = ("checkpoint_zone_id", "exit_zone_id")
_ZONE_REF_LISTS = ("lying_allowed_zone_ids",)


def _get(path: str, params: dict):
    from app.db.safety_sync import _call
    _, raw = _call("GET", path, params)
    return json.loads(raw or "[]")


def fetch_rule_rows(org_id: str) -> List[dict]:
    """Every live (not soft-deleted) rule of the organisation, enabled or not."""
    return _get("safety_rules", {
        "select": "id,type,zoneId,cameraId,name,params,severity,isEnabled,minEvidenceConfidence",
        "orgId": f"eq.{org_id}", "deletedAt": "is.null", "limit": "1000"})


def _translate_params(params, slug_of: dict):
    """Postgres zone ids inside a rule's parameters become the session's zone slugs."""
    if not isinstance(params, dict):
        return params
    out = dict(params)
    for key in _ZONE_REF_SCALARS:
        if out.get(key) is not None:
            out[key] = slug_of.get(str(out[key]), out[key])
    for key in _ZONE_REF_LISTS:
        if isinstance(out.get(key), list):
            out[key] = [slug_of.get(str(v), v) for v in out[key]]
    return out


def rules_for_session(org_id: Optional[str], zones_config, camera_name: Optional[str] = None) -> List[Rule]:
    """The rules this session runs: the organisation's own plus the default where a zone has none."""
    defaults = default_rules(zones_config)
    try:
        from app.db.safety_sync import service_role_configured
        if not org_id or not camera_name or not service_role_configured():
            return defaults
        cameras = _get("cameras", {"select": "id", "orgId": f"eq.{org_id}", "name": f"eq.{camera_name}", "limit": "1"})
        if not cameras:
            return defaults                              # an unregistered camera has no zones, so no rules, in Postgres
        camera_id = cameras[0]["id"]
        rows = fetch_rule_rows(org_id)
        if not rows:
            return defaults
        zones = _get("zones", {"select": "id,slug", "orgId": f"eq.{org_id}", "cameraId": f"eq.{camera_id}", "limit": "1000"})
    except Exception as e:
        _log.warning("The organisation's safety rules could not be read (%s: %s); running on the default rule",
                     type(e).__name__, e)
        return defaults

    slug_of = {str(z["id"]): z.get("slug") for z in zones if z.get("slug")}      # this camera's zones only
    local_ids = {str(z["zone_id"]) for z in (zones_config or [])}
    mapped = []
    for r in rows:
        zid = str(r.get("zoneId")) if r.get("zoneId") else None
        if zid is None and r.get("type") == "MAN_DOWN":
            # A whole-camera rule: it belongs to this camera, or (no camera named) to every camera of the organisation.
            if r.get("cameraId") in (None, camera_id):
                mapped.append(dict(r, zoneId=None, params=_translate_params(r.get("params"), slug_of)))
            continue
        if zid is None or zid not in slug_of or slug_of[zid] not in local_ids:
            continue                                     # a rule for a zone of another camera (or one this session does not have)
        mapped.append(dict(r, zoneId=slug_of[zid], params=_translate_params(r.get("params"), slug_of)))

    # parse everything, disabled rules included: a disabled AT_ENTRY rule is how a zone's default is switched off
    parsed, errors = [], []
    for row in mapped:
        rules, errs = parse_rules([dict(row, isEnabled=True)], zones_config)      # parse as enabled, then keep the real flag
        errors.extend(errs)
        for rule in rules:
            parsed.append((rule, bool(row.get("isEnabled", True))))
    for message in errors:
        _log.warning("safety rule refused: %s", message)

    covered = {rule.zone_id for rule, _enabled in parsed
               if rule.type == "PPE_ENTRY" and rule.params.get("mode", "AT_ENTRY") == "AT_ENTRY"}
    active = [rule for rule, enabled in parsed if enabled]
    return active + [d for d in defaults if d.zone_id not in covered]
