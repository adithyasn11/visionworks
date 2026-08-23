# backend/app/db/alerts_engine.py
"""
Rule evaluation over minute buckets.

WHY THIS RUNS HERE

Alerts are evaluated where the buckets are produced — in the aggregator's
60-second tick — rather than in the frontend. The alternative, evaluating when
someone opens the dashboard, means an overnight sedentary condition never fires
because nobody was watching. An alert that only exists while observed is not an
alert.

THE DEBOUNCE IS THE WHOLE PROBLEM

A sedentary condition that holds for two hours spans 120 buckets. Firing per
bucket produces 120 alerts for one situation, and the feed becomes unreadable —
which is the failure mode that makes people mute alerting entirely and stop
seeing the real ones.

Three mechanisms keep it to one:

  sustainedMinutes  the condition must hold this long before it fires at all.
                    Stops a single noisy minute raising an alarm.
  cooldownMinutes   after firing, the rule stays quiet this long. This is the
                    one that turns 120 into 1.
  OPEN-state check  a rule with an alert still OPEN for the same zone does not
                    fire again. Acknowledging it is what re-arms the rule, so
                    the operator decides when they want to hear about it next.

All three come from columns that already existed on `alert_rules` — the schema
anticipated this, and the engine uses them rather than inventing its own.

WHY THE SERVICE-ROLE KEY

`alerts` has SELECT and UPDATE policies but **no INSERT policy**, exactly like
`zone_minute_stats`: a browser client must not be able to fabricate an alert any
more than it can fabricate a measurement. So the engine writes with the
service-role key, and the dashboard reads and acknowledges through RLS.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Rule types this engine evaluates. The schema's AlertType enum also has
# ZONE_EMPTY, which needs booking data the system does not collect, so it is
# deliberately not evaluated rather than silently never firing.
SUPPORTED_TYPES = ("SEDENTARY", "OVERCROWDING", "UNDERUTILISATION", "CAMERA_OFFLINE")


def _service_key() -> str | None:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return key if key and "your-" not in key else None


def _request(path: str, params: dict | None = None, method: str = "GET",
             payload=None, prefer: str | None = None):
    """One PostgREST call with the service-role key."""
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = _service_key()
    if not base or not key:
        return None

    url = f"{base}/rest/v1/{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if prefer:
        headers["Prefer"] = prefer

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else []


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _zone_names(org_id: str) -> dict:
    """zoneId -> name, so an alert message names the place, not a uuid."""
    rows = _request("zones", {"select": "id,name", "orgId": f"eq.{org_id}"}) or []
    return {row["id"]: row.get("name") or "a zone" for row in rows}


def _recent_alert_exists(rule_id: str, zone_id: str | None, cooldown_minutes: int) -> bool:
    """
    Has this rule already fired recently, or is an alert still open for it?

    Two questions, one lookup, because both answers mean "do not fire":

      - fired inside the cooldown window -> suppressed by design
      - still OPEN for this zone         -> the operator has not dealt with the
                                            first one yet; a second says nothing
                                            new and buries the first

    Scoped to the ZONE, not just the rule: a rule covering the whole org should
    still be able to report two different rooms independently.
    """
    since = _iso(datetime.now(timezone.utc) - timedelta(minutes=max(cooldown_minutes, 1)))
    params = {
        "select": "id,state,triggeredAt",
        "ruleId": f"eq.{rule_id}",
        "or": f"(triggeredAt.gte.{since},state.eq.OPEN)",
        "limit": "1",
    }
    if zone_id:
        params["zoneId"] = f"eq.{zone_id}"
    rows = _request("alerts", params) or []
    return len(rows) > 0


def _fire(rule: dict, zone_id: str | None, camera_id: str | None,
          measured: float, message: str) -> bool:
    """Write one alert. Returns True when a row was created."""
    payload = {
        "orgId": rule["orgId"],
        "ruleId": rule["id"],
        "zoneId": zone_id,
        "cameraId": camera_id,
        "state": "OPEN",
        "severity": rule.get("severity") or "WARNING",
        # Both values are COPIED, not read through the rule later: editing the
        # threshold tomorrow must not rewrite what this alert said today.
        "triggeredValue": round(float(measured), 2),
        "thresholdValue": float(rule["thresholdValue"]),
        "message": message[:500],
    }
    try:
        _request("alerts", method="POST", payload=[payload], prefer="return=minimal")
        return True
    except Exception as e:
        logger.warning(f"Could not write alert for rule {rule.get('name')}: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  RULE EVALUATORS
# ═══════════════════════════════════════════════════════════════════════════

def _evaluate_sedentary(rule: dict, window_start: str, zone_names: dict) -> list:
    """
    Sustained sitting beyond the threshold.

    Measured as the share of a zone's samples that are SITTING across the
    sustained window, together with the total minutes observed. Both matter: a
    zone with two samples that happen to be sitting is not a sedentary office.
    """
    params = {
        "select": "zoneId,sittingFrames,sampleFrames,cameraId",
        "bucketStart": f"gte.{window_start}",
        "orgId": f"eq.{rule['orgId']}",
    }
    if rule.get("zoneId"):
        params["zoneId"] = f"eq.{rule['zoneId']}"

    rows = _request("zone_minute_stats", params) or []
    per_zone: dict = {}
    for row in rows:
        zone = per_zone.setdefault(
            row["zoneId"], {"sitting": 0, "samples": 0, "minutes": 0, "camera": row.get("cameraId")}
        )
        zone["sitting"] += row.get("sittingFrames") or 0
        zone["samples"] += row.get("sampleFrames") or 0
        zone["minutes"] += 1

    fired = []
    for zone_id, totals in per_zone.items():
        if totals["samples"] <= 0:
            continue
        share = totals["sitting"] / totals["samples"]
        # thresholdValue is MINUTES for this type (schema comment). The
        # condition is "this zone has been predominantly seated for at least
        # that many minutes".
        if share >= 0.7 and totals["minutes"] >= float(rule["thresholdValue"]):
            fired.append((
                zone_id, totals["camera"], totals["minutes"],
                f"{zone_names.get(zone_id, 'A zone')} has been predominantly seated "
                f"for {totals['minutes']} minutes ({round(share * 100)}% of samples). "
                f"Threshold is {int(rule['thresholdValue'])} minutes.",
            ))
    return fired


def _evaluate_overcrowding(rule: dict, window_start: str, zone_names: dict) -> list:
    """Occupancy above the rule's person count."""
    params = {
        "select": "zoneId,occupancyMax,cameraId",
        "bucketStart": f"gte.{window_start}",
        "orgId": f"eq.{rule['orgId']}",
    }
    if rule.get("zoneId"):
        params["zoneId"] = f"eq.{rule['zoneId']}"

    rows = _request("zone_minute_stats", params) or []
    peaks: dict = {}
    for row in rows:
        zone = peaks.setdefault(row["zoneId"], {"peak": 0, "camera": row.get("cameraId")})
        zone["peak"] = max(zone["peak"], row.get("occupancyMax") or 0)

    return [
        (zone_id, totals["camera"], totals["peak"],
         f"{zone_names.get(zone_id, 'A zone')} reached {totals['peak']} people, "
         f"above its limit of {int(rule['thresholdValue'])}.")
        for zone_id, totals in peaks.items()
        if totals["peak"] > float(rule["thresholdValue"])
    ]


def _evaluate_underutilisation(rule: dict, window_start: str, zone_names: dict) -> list:
    """
    Utilisation below the floor across the window.

    Utilisation is mean occupancy against the zone's declared capacity. A zone
    with no capacity is SKIPPED rather than assumed — for a corridor the
    percentage is meaningless, which is exactly why the column is nullable.
    """
    zones = _request("zones", {
        "select": "id,name,capacity", "orgId": f"eq.{rule['orgId']}",
    }) or []
    capacities = {z["id"]: z.get("capacity") for z in zones if z.get("capacity")}
    if not capacities:
        return []

    params = {
        "select": "zoneId,occupancyAvg,cameraId",
        "bucketStart": f"gte.{window_start}",
        "orgId": f"eq.{rule['orgId']}",
    }
    if rule.get("zoneId"):
        params["zoneId"] = f"eq.{rule['zoneId']}"

    rows = _request("zone_minute_stats", params) or []
    totals: dict = {}
    for row in rows:
        if row["zoneId"] not in capacities:
            continue
        zone = totals.setdefault(row["zoneId"], {"sum": 0.0, "count": 0, "camera": row.get("cameraId")})
        zone["sum"] += float(row.get("occupancyAvg") or 0)
        zone["count"] += 1

    fired = []
    for zone_id, agg in totals.items():
        # Require a real window of evidence before calling a zone underused —
        # ten minutes of quiet over lunch is not a real-estate finding.
        if agg["count"] < 30:
            continue
        utilisation = (agg["sum"] / agg["count"]) / capacities[zone_id] * 100
        if utilisation < float(rule["thresholdValue"]):
            fired.append((
                zone_id, agg["camera"], round(utilisation, 1),
                f"{zone_names.get(zone_id, 'A zone')} ran at {round(utilisation)}% "
                f"of its {capacities[zone_id]}-person capacity over "
                f"{agg['count']} minutes, below the {int(rule['thresholdValue'])}% floor.",
            ))
    return fired


def _evaluate_camera_offline(rule: dict, window_start: str, _zone_names: dict) -> list:
    """
    An ACTIVE camera that produced no buckets in the window.

    Operational rather than occupancy-derived: it says the measurement stopped,
    which is the one failure that makes every other number quietly wrong.
    """
    cameras = _request("cameras", {
        "select": "id,name", "orgId": f"eq.{rule['orgId']}",
        "status": "eq.ACTIVE", "deletedAt": "is.null",
    }) or []
    if not cameras:
        return []

    rows = _request("zone_minute_stats", {
        "select": "cameraId", "bucketStart": f"gte.{window_start}",
        "orgId": f"eq.{rule['orgId']}",
    }) or []
    seen = {row["cameraId"] for row in rows}

    return [
        (None, camera["id"], 0,
         f"Camera “{camera.get('name') or 'unnamed'}” has produced no measurements "
         f"since {window_start[:16].replace('T', ' ')} UTC.")
        for camera in cameras if camera["id"] not in seen
    ]


_EVALUATORS = {
    "SEDENTARY": _evaluate_sedentary,
    "OVERCROWDING": _evaluate_overcrowding,
    "UNDERUTILISATION": _evaluate_underutilisation,
    "CAMERA_OFFLINE": _evaluate_camera_offline,
}


def evaluate_rules_sync() -> dict:
    """
    Evaluate every enabled rule once. Runs in a worker thread.

    Never raises: alert evaluation is a side effect of aggregation, and a rule
    that cannot be evaluated must not stop buckets being written.
    """
    if not _service_key():
        return {"evaluated": 0, "fired": 0, "reason": "no service-role key configured"}

    try:
        rules = _request("alert_rules", {
            "select": "id,orgId,name,type,severity,thresholdValue,sustainedMinutes,"
                      "cooldownMinutes,zoneId,cameraId,isEnabled",
            "isEnabled": "is.true",
            "deletedAt": "is.null",
        }) or []
    except Exception as e:
        logger.warning(f"Could not load alert rules: {e}")
        return {"evaluated": 0, "fired": 0, "error": str(e)}

    evaluated = fired = suppressed = 0
    names_by_org: dict = {}

    for rule in rules:
        rule_type = rule.get("type")
        evaluator = _EVALUATORS.get(rule_type)
        if evaluator is None:
            continue  # ZONE_EMPTY and anything new — not silently mis-evaluated.

        evaluated += 1
        sustained = max(1, int(rule.get("sustainedMinutes") or 5))
        window_start = _iso(datetime.now(timezone.utc) - timedelta(minutes=sustained))

        try:
            org_id = rule["orgId"]
            if org_id not in names_by_org:
                names_by_org[org_id] = _zone_names(org_id)

            for zone_id, camera_id, measured, message in evaluator(
                rule, window_start, names_by_org[org_id]
            ):
                # THE DEBOUNCE. Without this one check, a two-hour condition
                # writes 120 alerts instead of 1.
                if _recent_alert_exists(rule["id"], zone_id,
                                        int(rule.get("cooldownMinutes") or 30)):
                    suppressed += 1
                    continue
                if _fire(rule, zone_id, camera_id, measured, message):
                    fired += 1
        except Exception as e:
            logger.warning(f"Rule '{rule.get('name')}' failed to evaluate: {e}")

    return {"evaluated": evaluated, "fired": fired, "suppressed": suppressed}


async def evaluate_rules() -> dict:
    """evaluate_rules_sync() off the event loop."""
    import asyncio
    return await asyncio.to_thread(evaluate_rules_sync)
