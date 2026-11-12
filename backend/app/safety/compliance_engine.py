# backend/app/safety/compliance_engine.py
"""
From zone events and PPE evidence to a verdict with a reason.

Four kinds of rule are judged: PPE_ENTRY in mode AT_ENTRY ("helmet and vest before you
enter a restricted zone"), PPE_CONTINUOUS (the same items while inside), MAX_DWELL (a
time limit in a zone) and REQUIRED_EXIT (leave a restricted zone by a named exit). Every
other rule type parses and validates (rules.py) but is not evaluated yet, and
`unsupported` says which, so nothing is silently ignored. The three later kinds are
described at the end of this docstring.

HOW AN ENTRY IS JUDGED

  A worker's confirmed entry into a zone a rule covers opens a PENDING decision.
  Each frame the engine asks the PPE evidence for that worker (which already holds
  the frames from BEFORE the entry, since it starts at first sighting) and reads it
  for the items the rule requires:

    every required item PRESENT, and the newest
    frames agree                           -> COMPLIANT
    any required item confidently ABSENT   -> POTENTIAL_VIOLATION
    otherwise                              -> keep waiting; at `entry_evidence_wait_s`
                                              after the entry, UNVERIFIABLE, with the
                                              evidence layer's own reason

  Nothing here invents a threshold: "present" and "absent" are the evidence layer's
  60 % / 90 % rules (ppe_evidence.py). "The newest frames agree" means the last
  `ppe_min_clear_frames` clear frames also show each required item in at least the
  presence fraction: a worker who lost the helmet a second ago still averages well
  over a 30-frame window, and must not be cleared on that. A claim whose supporting
  fraction is below the rule's `min_evidence_confidence` is not made at once: the
  engine keeps looking until the wait is over, then abstains.

WHAT THE NUMBERS MEAN

  A COMPLIANT decision's confidence is the weakest required item's "seen in this
  share of clear frames". A POTENTIAL_VIOLATION's is the weakest MISSING item's
  "nothing there in this share of clear frames": the claim is about the missing item,
  so a vest nobody could judge does not weaken "no helmet in 28 of 29 frames".
  UNVERIFIABLE makes no claim and has confidence 0.

  Every sentence is built from the evidence dict stored with the decision, so the two
  cannot disagree.

ONE DECISION PER WORKER PER VISIT PER RULE

  A visit ends with the worker's exit (or loss, or the video jumping). Someone who
  steps out and straight back in (a jittery boundary, a brief occlusion mistaken for
  an exit) is not judged again until `realert_after_s` has passed since the entry
  they were last JUDGED on (a claim: COMPLIANT or POTENTIAL_VIOLATION). An entry that
  ended in an abstention does not start that clock: nothing was said about it, so a
  second look is exactly what is wanted.

  A worker who was already inside when first seen did not demonstrably enter. They
  are still judged (being in a restricted zone without PPE is what the rule is
  about), but the decision says so.

It is an engine component: when the timeline jumps, pending decisions close as
UNVERIFIABLE ("the video jumped before they could be judged"), stamped at the entry.

THE OTHER THREE RULES

  PPE_CONTINUOUS. While a worker is inside the zone the same evidence is read every frame.
  If a required item is confidently ABSENT for `grace_seconds` without a break, ONE
  POTENTIAL_VIOLATION is raised for that episode. Evidence that turns unclear, or a
  worker who drops out of view, breaks the run: absence of evidence is never an
  accusation, and nothing is decided about it. The episode ends when every required item
  is seen again (a later removal is a new episode, but not within `realert_after_s` of the
  last flag) or when the worker leaves the zone. A visit that simply ends raises nothing.

  MAX_DWELL. The zone tracker counts the visit (from the back-dated entry, through short
  gaps in view) and raises DWELL_WARNING (an event only: nothing to review) and
  DWELL_EXCEEDED. Reaching the limit raises ONE POTENTIAL_VIOLATION with the measured
  stay. A visit that ends within the limit is COMPLIANT; one that ends by the worker
  being lost, or by the video ending or jumping, is UNVERIFIABLE (the stay cannot be
  stated). So is a visit that was already under way when first seen and ended within the
  limit: only part of the stay was seen.

  REQUIRED_EXIT. A confirmed exit from the zone starts a timer of `max_seconds_to_exit`.
  Reaching the EXIT zone in time is COMPLIANT. Going confidently into another real zone
  first, or reaching the exit late, or being in view when the timer runs out without
  having reached it, is a POTENTIAL_VIOLATION. A worker who is simply out of view when the
  timer runs out is UNVERIFIABLE. Going back into the zone cancels the timer (they did not
  leave). An exit the tracker could not confirm (the worker was lost, the video ended)
  starts no timer: nobody can say they left.
"""

import logging
from typing import Dict, List, Optional, Tuple

from app.safety.config import SafetyConfig
from app.safety.schemas import COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE, Decision, Rule
from app.safety.zone_events import ZoneEvent

_log = logging.getLogger(__name__)

SUPPORTED = ("PPE_ENTRY", "PPE_CONTINUOUS", "MAX_DWELL", "REQUIRED_EXIT")
OTHER_ENGINES = ("MAN_DOWN", "OCCUPANCY")          # evaluated, but not by this engine


def is_evaluated(rule_type: str, params: Optional[dict]) -> bool:
    """Does the engine act on a rule of this type and these parameters? (The one place that says so.)"""
    if rule_type == "PPE_ENTRY":
        return (params or {}).get("mode", "AT_ENTRY") == "AT_ENTRY"
    return rule_type in SUPPORTED or rule_type in ("MAN_DOWN", "OCCUPANCY")   # judged by man_down.py (pose keypoints) and occupancy.py (head counts)

# The PPE evidence item states (ppe_evidence.py).
PRESENT, ABSENT = "PRESENT", "ABSENT"

_NAMES = {"helmet": "helmet", "safety_vest": "vest"}


class _Pending:
    def __init__(self, rule: Rule, worker_key: str, entered_at: float, opened_at: float, first_sighting: bool):
        self.rule = rule
        self.worker_key = worker_key
        self.entered_at = entered_at
        self.opened_at = opened_at
        self.first_sighting = first_sighting


class _Cont:
    """One worker's presence inside a zone with a PPE_CONTINUOUS rule."""
    def __init__(self, rule: Rule, entered_at: float, first_sighting: bool):
        self.rule = rule
        self.entered_at = entered_at
        self.first_sighting = first_sighting
        self.streak_start: Optional[float] = None       # when the current unbroken run of "confidently absent" began
        self.flagged = False                            # this episode already raised its decision
        self.suppressed = False                         # this run began inside the re-alert window of the last flag: the same story
        self.last_flag_t: Optional[float] = None


class _Visit:
    """One worker's visit to a zone with a MAX_DWELL rule."""
    def __init__(self, rule: Rule, entered_at: float, first_sighting: bool):
        self.rule = rule
        self.entered_at = entered_at
        self.first_sighting = first_sighting
        self.judged = False                             # the limit was reached and its decision made


class _Timer:
    """A worker who has just left a zone with a REQUIRED_EXIT rule and must reach the exit in time."""
    def __init__(self, rule: Rule, worker_key: str, left_at: float):
        self.rule = rule
        self.worker_key = worker_key
        self.left_at = left_at
        self.deadline = left_at + float(rule.params["max_seconds_to_exit"])


def _dur(seconds: float) -> str:
    """12 seconds as '12 s', 125 as '2 min 5 s', 3700 as '1 h 1 min'."""
    s = float(seconds)
    if s < 60:
        return f"{round(s):d} s" if abs(s - round(s)) < 0.05 else f"{s:.1f} s"
    total = int(round(s))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m} min {sec} s" if sec else f"{m} min"


def _took(seconds: float) -> str:
    """'straight away' for under a second, else 'after 8 s'."""
    return "straight away" if float(seconds) < 1.0 else f"after {_dur(seconds)}"


def _fmt_items(items: List[str]) -> str:
    names = [_NAMES.get(i, i) for i in items]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _count(fraction: float, total: int) -> int:
    return int(round(float(fraction) * int(total)))


class ComplianceEngine:
    def __init__(self, rules: List[Rule], ppe, config: Optional[SafetyConfig] = None, zone_names: Optional[Dict[str, str]] = None,
                 zone_types: Optional[Dict[str, str]] = None):
        """
        rules       the session's rules (rules.load_rules). Disabled ones are ignored.
        ppe         anything with `verdict(worker_key, t) -> dict | None` and an
                    `unavailable_reason` (the PpeRunner), or None when the session does
                    no PPE checks, in which case PPE rules cannot be judged.
        zone_names  {zone id: name}, to say where a worker went instead of the exit.
        zone_types  {zone id: type}. Going into another RESTRICTED zone after leaving is a wrong route; passing
                    through an approach or checkpoint zone on the way to the exit is not, so those do not settle
                    the timer (the worker is still expected at the exit). Unknown types count as a wrong route.
        """
        self.config = config or SafetyConfig()
        self.ppe = ppe
        self.zone_names = dict(zone_names or {})
        self.zone_types = {k: str(v).upper() for k, v in (zone_types or {}).items()}
        self.rules = [r for r in rules if r.enabled]
        self.active = [r for r in self.rules if is_evaluated(r.type, r.params) and r.type not in OTHER_ENGINES]
        self.unsupported = [r for r in self.rules if r not in self.active and r.type not in OTHER_ENGINES]
        if self.unsupported:
            _log.info("safety rules loaded but not evaluated yet: "
                      + ", ".join(sorted({f"{r.type}/{r.params.get('mode', '')}".rstrip('/') for r in self.unsupported})))
        self._by_zone: Dict[str, List[Rule]] = {}          # PPE_ENTRY rules by zone
        self._cont_by_zone: Dict[str, List[Rule]] = {}
        self._dwell_by_zone: Dict[str, List[Rule]] = {}
        self._exit_by_zone: Dict[str, List[Rule]] = {}
        groups = {"PPE_ENTRY": self._by_zone, "PPE_CONTINUOUS": self._cont_by_zone,
                  "MAX_DWELL": self._dwell_by_zone, "REQUIRED_EXIT": self._exit_by_zone}
        for r in self.active:
            groups[r.type].setdefault(r.zone_id, []).append(r)
        self._cont: Dict[Tuple[str, str], _Cont] = {}
        self._cont_flag_t: Dict[Tuple[str, str], float] = {}  # (rule key, worker) -> video time of the last continuous-PPE flag, across visits
        self._visits: Dict[Tuple[str, str], _Visit] = {}
        self._timers: Dict[Tuple[str, str], _Timer] = {}
        self._seen_at: Dict[str, float] = {}                # worker -> video time of the last frame they were in view
        self._pending: Dict[Tuple[str, str], _Pending] = {}
        self._last_judged: Dict[Tuple[str, str], float] = {}         # (rule key, worker) -> entry time of the last CLAIM
        self.suppressed = 0                                          # re-entries inside the realert window
        self.decisions_made = 0

    # ── events ───────────────────────────────────────────────────────────────

    def dwell_watches(self) -> List[Tuple[str, float, float]]:
        """(zone, limit seconds, warning fraction) for each MAX_DWELL rule: what the zone tracker must watch."""
        return [(r.zone_id, float(r.params["max_seconds"]), float(r.params["warn_fraction"]))
                for rules in self._dwell_by_zone.values() for r in rules]

    def _zone_name(self, zone_id: Optional[str], rule: Optional[Rule] = None) -> str:
        if zone_id in self.zone_names:
            return str(self.zone_names[zone_id])
        if rule is not None and zone_id == rule.zone_id and rule.zone_name:
            return rule.zone_name
        return str(zone_id)

    def on_event(self, event: ZoneEvent, now: float) -> List[Decision]:
        """Feed one zone event (never one from a replayed stretch). `now` is the current frame's video time."""
        out: List[Decision] = []
        if event.event_type == "ENTERED_ZONE":
            for rule in self._by_zone.get(event.zone_id, ()):
                self._open(rule, event, now, out)
            for rule in self._cont_by_zone.get(event.zone_id, ()):
                key = (rule.key, event.worker_key)
                st = _Cont(rule, event.video_time_s, bool(event.details.get("first_sighting")))
                st.last_flag_t = self._cont_flag_t.get(key)                # stepping out and back in is the same story
                self._cont[key] = st
                window = float(self.config.realert_after_s)
                for k in [k for k, v in self._cont_flag_t.items() if event.video_time_s - v >= window]:
                    del self._cont_flag_t[k]                               # forget what is long out of the window
            for rule in self._dwell_by_zone.get(event.zone_id, ()):
                self._visits[(rule.key, event.worker_key)] = _Visit(rule, event.video_time_s, bool(event.details.get("first_sighting")))
            self._arrived(event, now, out)
        elif event.event_type == "EXITED_ZONE":
            for rule in self._by_zone.get(event.zone_id, ()):
                key = (rule.key, event.worker_key)
                pending = self._pending.get(key)
                if pending is not None:
                    why = event.details.get("reason")
                    where = "was lost from view" if why == "LOST" else "left the zone"
                    out.append(self._finish(key, self._abstain(
                        pending, now, f"{where} before the {_fmt_items(rule.params['required_ppe'])} could be judged")))
            for rule in self._cont_by_zone.get(event.zone_id, ()):
                self._cont.pop((rule.key, event.worker_key), None)      # the visit is over; nothing is decided about a stay that simply ends
            for rule in self._dwell_by_zone.get(event.zone_id, ()):
                self._dwell_exit(rule, event, out)
            for rule in self._exit_by_zone.get(event.zone_id, ()):
                self._start_exit_timer(rule, event)
        elif event.event_type == "DWELL_EXCEEDED":
            self._dwell_exceeded(event, out)
        return out

    def _open(self, rule: Rule, event: ZoneEvent, now: float, out: List[Decision]) -> None:
        key = (rule.key, event.worker_key)
        if key in self._pending:
            return
        window = float(self.config.realert_after_s)
        if self._last_judged:                                          # forget what is long out of the window
            for k in [k for k, v in self._last_judged.items() if event.video_time_s - v >= window]:
                del self._last_judged[k]
        last = self._last_judged.get(key)
        if last is not None and event.video_time_s - last < window:
            self.suppressed += 1
            return
        pending = _Pending(rule, event.worker_key, event.video_time_s, now, bool(event.details.get("first_sighting")))
        self._pending[key] = pending
        self._try(pending, now, out)

    # ── frames ───────────────────────────────────────────────────────────────

    def on_tick(self, t: float, seen=None) -> List[Decision]:
        """
        Look again at every open decision with the evidence as it stands at video time `t`.

        `seen` is the set of worker keys visible on this frame. It is needed to tell "still in view"
        from "out of view" (a required exit; the continuous PPE run). None means nobody is known to be
        in view, which is read as out of view: the honest direction.
        """
        out: List[Decision] = []
        for w in (seen or ()):
            self._seen_at[w] = t
        if len(self._seen_at) > 2000:                               # forget workers long gone
            for w in [w for w, at in self._seen_at.items() if t - at > 600.0]:
                del self._seen_at[w]
        for key in list(self._pending):
            pending = self._pending.get(key)
            if pending is not None:
                self._try(pending, t, out)
        if self._cont:
            self._continuous_tick(t, set(seen or ()), out)
        if self._timers:
            self._exit_tick(t, out)
        return out

    def _try(self, pending: _Pending, t: float, out: List[Decision]) -> None:
        rule = pending.rule
        key = (rule.key, pending.worker_key)
        required = rule.params["required_ppe"]

        if self.ppe is None:
            out.append(self._finish(key, self._abstain(pending, t, "this session does no PPE checks")))
            return
        unavailable = getattr(self.ppe, "unavailable_reason", None)
        if unavailable:
            out.append(self._finish(key, self._abstain(pending, t, f"{unavailable}, so nobody can be judged")))
            return

        verdict = self.ppe.verdict(pending.worker_key, t)
        if not verdict:
            out.append(self._finish(key, self._abstain(pending, t, "there is no PPE evidence for this worker")))
            return
        items = verdict["items"]
        states = {i: items[i]["state"] for i in required}

        if any(s == ABSENT for s in states.values()):
            missing = [i for i in required if states[i] == ABSENT]
            support = min(float(items[i]["empty_fraction"]) for i in missing)
            decision = self._claim(pending, t, POTENTIAL_VIOLATION, verdict, support, missing)
        elif all(s == PRESENT for s in states.values()) and self._newest_agree(verdict, required):
            support = min(float(items[i]["seen_fraction"]) for i in required)
            decision = self._claim(pending, t, COMPLIANT, verdict, support, required)
        elif t - pending.opened_at >= float(self.config.entry_evidence_wait_s) - 1e-9:
            if all(s == PRESENT for s in states.values()):
                recent = (verdict.get("recent") or {}).get("seen_fraction", {})
                disagree = [i for i in required if recent.get(i, 1.0) < float(self.config.ppe_presence_fraction)]
                why = "; ".join(f"{_NAMES.get(i, i)}: the newest frames do not show it although the longer window does" for i in disagree)
            else:
                why = "; ".join(f"{_NAMES.get(i, i)}: {items[i]['why']}" for i in required if states[i] != PRESENT)
            decision = self._abstain(pending, t, f"could not tell whether the {_fmt_items(required)} "
                                                 f"{'was' if len(required) == 1 else 'were'} seen ({why})", verdict)
        else:
            return                                                   # still gathering evidence
        if decision is None:                                         # a claim below the rule's floor: keep looking
            if t - pending.opened_at < float(self.config.entry_evidence_wait_s) - 1e-9:
                return
            decision = self._floor_abstain(pending, t, verdict)
        out.append(self._finish(key, decision))

    def _newest_agree(self, verdict: dict, required: List[str]) -> bool:
        """Do the most recent clear frames also show every required item (see the module docstring)?"""
        recent = verdict.get("recent")
        if not recent or not recent.get("frames"):
            return True                                              # nothing to contradict the window
        floor = float(self.config.ppe_presence_fraction)
        return all(recent["seen_fraction"].get(i, 1.0) >= floor for i in required)

    def _finish(self, key, decision: Decision) -> Decision:
        pending = self._pending.pop(key, None)
        self.decisions_made += 1
        if decision.status != UNVERIFIABLE and pending is not None:
            self._last_judged[key] = pending.entered_at              # only a claim starts the re-alert clock
        return decision

    # ── building decisions ──────────────────────────────────────────────────

    def _evidence(self, pending: _Pending, t: float, verdict: Optional[dict]) -> dict:
        rule = pending.rule
        ev = {
            "mode": rule.params.get("mode", "AT_ENTRY"), "required": list(rule.params["required_ppe"]),
            "rule": rule.name or rule.key, "rule_source": rule.source,
            "entered_video_time_s": round(pending.entered_at, 3), "decided_video_time_s": round(t, 3),
            "waited_seconds": round(max(0.0, t - pending.opened_at), 3),
            "first_sighting": pending.first_sighting,
        }
        if verdict:
            ev["observed_frames"] = verdict.get("observed_frames")
            ev["items"] = {i: {k: verdict["items"][i][k] for k in
                               ("state", "why", "clear_frames", "seen_fraction", "empty_fraction", "mean_conf")}
                           for i in rule.params["required_ppe"]}
            if verdict.get("recent"):
                ev["recent"] = {"frames": verdict["recent"]["frames"],
                                "seen_fraction": {i: round(verdict["recent"]["seen_fraction"].get(i, 0.0), 3)
                                                  for i in rule.params["required_ppe"]}}
        return ev

    def _thresholds(self, pending: _Pending) -> dict:
        c = self.config
        return {
            "ppe_presence_fraction": c.ppe_presence_fraction, "ppe_absence_fraction": c.ppe_absence_fraction,
            "ppe_min_clear_frames": c.ppe_min_clear_frames, "ppe_window_frames": c.ppe_window_frames,
            "ppe_stale_s": c.ppe_stale_s, "entry_evidence_wait_s": c.entry_evidence_wait_s,
            "realert_after_s": c.realert_after_s,
            "min_evidence_confidence": pending.rule.min_evidence_confidence,
            "required_ppe": list(pending.rule.params["required_ppe"]),
        }

    def _decision(self, pending: _Pending, status: str, reason: str, confidence: float, evidence: dict) -> Decision:
        rule = pending.rule
        return Decision(
            status=status, reason=reason, confidence=confidence, evidence=evidence,
            rule_type=rule.type, severity=rule.severity, worker_key=pending.worker_key,
            zone_id=rule.zone_id, video_time_s=pending.entered_at, rule_id=rule.id,
            thresholds=self._thresholds(pending), snapshot_requested=(status == POTENTIAL_VIOLATION))

    def _floor_abstain(self, pending: _Pending, t: float, verdict: dict) -> Decision:
        rule = pending.rule
        items = verdict["items"]
        required = rule.params["required_ppe"]
        absent = [i for i in required if items[i]["state"] == ABSENT]
        support = min(float(items[i]["empty_fraction"] if absent else items[i]["seen_fraction"]) for i in (absent or required))
        return self._decision(
            pending, UNVERIFIABLE,
            f"Could not judge PPE for {rule.zone_name or rule.zone_id}: the evidence ({support:.0%}) is below this rule's "
            f"minimum ({rule.min_evidence_confidence:.0%}), so no claim is made.", 0.0, self._evidence(pending, t, verdict))

    def _claim(self, pending: _Pending, t: float, status: str, verdict: dict, support: float, about: List[str]) -> Optional[Decision]:
        rule = pending.rule
        evidence = self._evidence(pending, t, verdict)
        if support < rule.min_evidence_confidence:
            return None                                              # not enough support (yet): the caller keeps looking
        items = verdict["items"]
        parts = []
        for i in about:
            n = items[i]["clear_frames"]
            if status == COMPLIANT:
                parts.append(f"{_NAMES.get(i, i)} seen in {_count(items[i]['seen_fraction'], n)} of {n} clear frames")
            else:
                parts.append(f"no {_NAMES.get(i, i)} seen in {_count(items[i]['empty_fraction'], n)} of {n} clear frames")
        where = rule.zone_name or rule.zone_id
        lead = f"Was already inside {where} when first seen" if pending.first_sighting else f"Entered {where}"
        # "seen" / "not seen", never a statement about what someone is or is not wearing.
        reason = f"{lead}: " + "; ".join(parts) + "." if status == COMPLIANT else f"{lead} with " + "; ".join(parts) + "."
        return self._decision(pending, status, reason, support, evidence)

    def _abstain(self, pending: _Pending, t: float, why: str, verdict: Optional[dict] = None) -> Decision:
        where = pending.rule.zone_name or pending.rule.zone_id
        return self._decision(pending, UNVERIFIABLE, f"Could not judge PPE for {where}: {why}.", 0.0,
                              self._evidence(pending, t, verdict))

    # ── PPE_CONTINUOUS ───────────────────────────────────────────────────────

    def _continuous_tick(self, t: float, seen: set, out: List[Decision]) -> None:
        if self.ppe is None or getattr(self.ppe, "unavailable_reason", None):
            for st in self._cont.values():
                st.streak_start = None
            return
        for (_rule_key, worker), st in list(self._cont.items()):
            rule = st.rule
            required = rule.params["required_ppe"]
            if worker not in seen:                                   # out of view: the run is broken, nothing is decided
                st.streak_start = None
                continue
            verdict = self.ppe.verdict(worker, t)
            if not verdict:
                st.streak_start = None
                continue
            items = verdict["items"]
            states = {i: items[i]["state"] for i in required}
            absent = [i for i in required if states[i] == ABSENT]
            if absent:
                support = min(float(items[i]["empty_fraction"]) for i in absent)
                if support < rule.min_evidence_confidence:           # too weak to say: unclear, so no claim and the run is broken
                    st.streak_start = None
                    continue
                if st.streak_start is None:
                    st.streak_start = t
                    st.suppressed = st.last_flag_t is not None and t - st.last_flag_t < float(self.config.realert_after_s)
                if st.flagged or st.suppressed or t - st.streak_start < float(rule.params["grace_seconds"]) - 1e-9:
                    continue
                st.flagged = True
                st.last_flag_t = t
                self._cont_flag_t[(rule.key, worker)] = t
                out.append(self._emit(self._continuous_decision(st, worker, t, verdict, absent, support)))
            elif all(s == PRESENT for s in states.values()) and self._newest_agree(verdict, required):
                st.streak_start = None                               # everything is seen again: the episode is over
                st.flagged = False
            else:
                st.streak_start = None                               # unclear: no decision, and the run is broken

    def _continuous_decision(self, st: _Cont, worker: str, t: float, verdict: dict, absent: List[str], support: float) -> Decision:
        rule = st.rule
        items = verdict["items"]
        parts = []
        for i in absent:
            n = items[i]["clear_frames"]
            parts.append(f"no {_NAMES.get(i, i)} seen in {_count(items[i]['empty_fraction'], n)} of {n} clear frames")
        run = t - float(st.streak_start)
        where = rule.zone_name or rule.zone_id
        reason = f"In {where}, in the recent evidence window: " + "; ".join(parts) + f", without a break for {_dur(run)}."
        evidence = {
            "mode": "CONTINUOUS", "required": list(rule.params["required_ppe"]),
            "rule": rule.name or rule.key, "rule_source": rule.source,
            "entered_video_time_s": round(st.entered_at, 3), "first_sighting": st.first_sighting,
            "episode_started_video_time_s": round(float(st.streak_start), 3), "decided_video_time_s": round(t, 3),
            "run_seconds": round(run, 3), "grace_seconds": float(rule.params["grace_seconds"]),
            "observed_frames": verdict.get("observed_frames"),
            "items": {i: {k: items[i][k] for k in ("state", "why", "clear_frames", "seen_fraction", "empty_fraction", "mean_conf")}
                      for i in rule.params["required_ppe"]},
        }
        c = self.config
        thresholds = {
            "ppe_presence_fraction": c.ppe_presence_fraction, "ppe_absence_fraction": c.ppe_absence_fraction,
            "ppe_min_clear_frames": c.ppe_min_clear_frames, "ppe_window_frames": c.ppe_window_frames,
            "grace_seconds": float(rule.params["grace_seconds"]), "realert_after_s": c.realert_after_s,
            "min_evidence_confidence": rule.min_evidence_confidence, "required_ppe": list(rule.params["required_ppe"]),
        }
        return Decision(
            status=POTENTIAL_VIOLATION, reason=reason, confidence=support, evidence=evidence, rule_type=rule.type,
            severity=rule.severity, worker_key=worker, zone_id=rule.zone_id, video_time_s=float(st.streak_start),
            rule_id=rule.id, thresholds=thresholds, snapshot_requested=True)

    # ── MAX_DWELL ────────────────────────────────────────────────────────────

    def _dwell_decision(self, rule: Rule, status: str, reason: str, worker: str, video_time: float,
                        evidence: dict, picture: bool = False) -> Decision:
        c = self.config
        evidence = {"rule": rule.name or rule.key, "rule_source": rule.source,
                    "limit_seconds": float(rule.params["max_seconds"]), "warn_fraction": float(rule.params["warn_fraction"]),
                    **evidence}
        thresholds = {"max_seconds": float(rule.params["max_seconds"]), "warn_fraction": float(rule.params["warn_fraction"]),
                      "enter_confirm_s": c.enter_confirm_s, "exit_confirm_s": c.exit_confirm_s,
                      "lost_grace_s": c.lost_grace_s, "lost_ttl_s": c.lost_ttl_s}
        return Decision(
            status=status, reason=reason, confidence=1.0 if status != UNVERIFIABLE else 0.0, evidence=evidence,
            rule_type=rule.type, severity=rule.severity, worker_key=worker, zone_id=rule.zone_id,
            video_time_s=video_time, rule_id=rule.id, thresholds=thresholds, snapshot_requested=picture)

    def _dwell_exceeded(self, event: ZoneEvent, out: List[Decision]) -> None:
        limit = float(event.details.get("limit_s", 0.0))
        for rule in self._dwell_by_zone.get(event.zone_id, ()):
            if abs(float(rule.params["max_seconds"]) - limit) > 1e-6:
                continue
            visit = self._visits.get((rule.key, event.worker_key))
            if visit is None:
                # A visit that began in footage that was replayed (after a seek back) was never announced to
                # this engine, but the tracker has counted it all the same and this event is new: judge it.
                visit = _Visit(rule, event.video_time_s - limit, False)
                self._visits[(rule.key, event.worker_key)] = visit
            if visit.judged:
                continue
            visit.judged = True
            dwell = max(limit, event.video_time_s - visit.entered_at)
            where = rule.zone_name or rule.zone_id
            unobserved = bool(event.details.get("crossed_unobserved"))
            if dwell - limit < 1.0:
                reason = f"Reached the {_dur(limit)} limit in {where}"
            else:
                reason = f"In {where} for {_dur(dwell)}, over the {_dur(limit)} limit"
            notes = []
            if visit.first_sighting:
                notes.append("counted from when they were first seen")
            if unobserved:
                notes.append("they were out of view for part of this time")
            reason += (" (" + "; ".join(notes) + ")" if notes else "") + "."
            out.append(self._emit(self._dwell_decision(
                rule, POTENTIAL_VIOLATION, reason, event.worker_key, event.video_time_s,
                {"outcome": "EXCEEDED", "dwell_seconds": round(dwell, 3), "entered_video_time_s": round(visit.entered_at, 3),
                 "decided_video_time_s": round(event.video_time_s, 3), "first_sighting": visit.first_sighting,
                 "crossed_unobserved": unobserved}, picture=True)))

    def _dwell_exit(self, rule: Rule, event: ZoneEvent, out: List[Decision]) -> None:
        visit = self._visits.pop((rule.key, event.worker_key), None)
        if visit is None or visit.judged:                             # not seen begin, or the limit was already reached and judged
            return
        why = event.details.get("reason")
        dwell = float(event.details.get("dwell_s", max(0.0, event.video_time_s - visit.entered_at)))
        limit = float(rule.params["max_seconds"])
        where = rule.zone_name or rule.zone_id
        base = {"dwell_seconds": round(dwell, 3), "entered_video_time_s": round(visit.entered_at, 3),
                "decided_video_time_s": round(event.video_time_s, 3), "first_sighting": visit.first_sighting}
        if why == "LEFT" and not event.details.get("unverifiable"):
            if visit.first_sighting:
                out.append(self._emit(self._dwell_decision(
                    rule, UNVERIFIABLE, f"Could not state the length of the stay in {where}: the worker was already inside when first "
                                        f"seen, so only the last {_dur(dwell)} of it was observed.", event.worker_key, visit.entered_at,
                    {**base, "outcome": "PARTIAL"})))
            else:
                out.append(self._emit(self._dwell_decision(
                    rule, COMPLIANT, f"Stayed {_dur(dwell)} in {where}, within the {_dur(limit)} limit.", event.worker_key, visit.entered_at,
                    {**base, "outcome": "WITHIN_LIMIT"})))
        elif why == "LOST":
            out.append(self._emit(self._dwell_decision(
                rule, UNVERIFIABLE, f"Could not state the length of the stay in {where}: the worker was lost from view after {_dur(dwell)} "
                                    f"and did not come back.", event.worker_key, visit.entered_at, {**base, "outcome": "LOST"})))
        else:
            ended = {"SESSION_END": "the video ended", "CAMERA_LOST": "the camera stopped sending pictures"}.get(why, "the video jumped")
            out.append(self._emit(self._dwell_decision(
                rule, UNVERIFIABLE, f"Could not state the length of the stay in {where}: {ended} while the worker was still inside, "
                                    f"after {_dur(dwell)}.", event.worker_key, visit.entered_at, {**base, "outcome": "INCOMPLETE"})))

    # ── REQUIRED_EXIT ────────────────────────────────────────────────────────

    def _exit_decision(self, tm: _Timer, status: str, reason: str, evidence: dict, picture: bool = False) -> Decision:
        rule = tm.rule
        evidence = {"rule": rule.name or rule.key, "rule_source": rule.source,
                    "left_video_time_s": round(tm.left_at, 3), "deadline_video_time_s": round(tm.deadline, 3),
                    "max_seconds_to_exit": float(rule.params["max_seconds_to_exit"]),
                    "exit_zone": self._zone_name(rule.params["exit_zone_id"]), **evidence}
        thresholds = {"max_seconds_to_exit": float(rule.params["max_seconds_to_exit"]), "enter_confirm_s": self.config.enter_confirm_s,
                      "exit_confirm_s": self.config.exit_confirm_s, "exit_visible_slack_s": self.config.exit_visible_slack_s}
        return Decision(
            status=status, reason=reason, confidence=1.0 if status != UNVERIFIABLE else 0.0, evidence=evidence,
            rule_type=rule.type, severity=rule.severity, worker_key=tm.worker_key, zone_id=rule.zone_id,
            video_time_s=tm.left_at, rule_id=rule.id, thresholds=thresholds, snapshot_requested=picture)

    def _start_exit_timer(self, rule: Rule, event: ZoneEvent) -> None:
        if event.details.get("reason") != "LEFT" or event.details.get("unverifiable"):
            return                                                    # nobody can say they left
        self._timers[(rule.key, event.worker_key)] = _Timer(rule, event.worker_key, event.video_time_s)

    def _arrived(self, event: ZoneEvent, now: float, out: List[Decision]) -> None:
        """A confirmed entry into a zone: settle the exit timers of that worker."""
        for key in [k for k, tm in self._timers.items() if tm.worker_key == event.worker_key]:
            tm = self._timers.pop(key)
            rule = tm.rule
            if event.zone_id == rule.zone_id:
                continue                                              # back inside: they did not leave
            exit_zone = rule.params["exit_zone_id"]
            if event.zone_id != exit_zone and self.zone_types.get(event.zone_id) in ("APPROACH", "PPE_CHECKPOINT"):
                self._timers[key] = tm                                # on the way: the worker is still expected at the exit
                continue
            home = rule.zone_name or rule.zone_id
            exit_name = self._zone_name(exit_zone)
            limit = float(rule.params["max_seconds_to_exit"])
            elapsed = max(0.0, event.video_time_s - tm.left_at)
            base = {"elapsed_seconds": round(elapsed, 3), "decided_video_time_s": round(event.video_time_s, 3)}
            if event.zone_id == exit_zone:
                if event.video_time_s <= tm.deadline + 1e-9:
                    out.append(self._emit(self._exit_decision(
                        tm, COMPLIANT, f"Left {home} and reached {exit_name} {_took(elapsed)}, within {_dur(limit)}.",
                        {**base, "outcome": "REACHED_EXIT"})))
                else:
                    out.append(self._emit(self._exit_decision(
                        tm, POTENTIAL_VIOLATION, f"Left {home} and reached {exit_name} {_took(elapsed)}, over the {_dur(limit)} allowed.",
                        {**base, "outcome": "REACHED_EXIT_LATE"}, picture=True)))
            else:
                other = self._zone_name(event.zone_id)
                out.append(self._emit(self._exit_decision(
                    tm, POTENTIAL_VIOLATION, f"Left {home} and was next seen in {other}, not the required exit {exit_name}"
                                             + (f" ({_dur(elapsed)} after leaving)." if elapsed >= 1.0 else "."),
                    {**base, "outcome": "OTHER_ZONE", "went_to": other}, picture=True)))

    def _exit_tick(self, t: float, out: List[Decision]) -> None:
        # The deadline plus the time an arrival at the exit takes to be confirmed: someone who steps into the
        # exit zone just before the deadline must not be judged before that is known.
        wait = float(self.config.enter_confirm_s) + 0.5
        for key, tm in list(self._timers.items()):
            if t < tm.deadline + wait - 1e-9:
                continue
            del self._timers[key]
            rule = tm.rule
            home = rule.zone_name or rule.zone_id
            exit_name = self._zone_name(rule.params["exit_zone_id"])
            limit = float(rule.params["max_seconds_to_exit"])
            in_view = t - self._seen_at.get(tm.worker_key, float("-inf")) <= float(self.config.exit_visible_slack_s) + 1e-9
            base = {"elapsed_seconds": round(t - tm.left_at, 3), "decided_video_time_s": round(t, 3)}
            if in_view:
                out.append(self._emit(self._exit_decision(
                    tm, POTENTIAL_VIOLATION, f"Left {home} and was still in view {_dur(limit)} later without having reached {exit_name}.",
                    {**base, "outcome": "NOT_REACHED"}, picture=True)))
            else:
                out.append(self._emit(self._exit_decision(
                    tm, UNVERIFIABLE, f"Left {home} but was out of view before reaching {exit_name}, so the route cannot be judged.",
                    {**base, "outcome": "OUT_OF_VIEW"})))

    def _emit(self, decision: Decision) -> Decision:
        self.decisions_made += 1
        return decision

    # ── jumps and the end ───────────────────────────────────────────────────

    def _close_all(self, phrase: str, t: float) -> List[Decision]:
        """`phrase` is "the video jumped", "the video ended" or "the camera stopped sending pictures": everything still open ends here, unjudged."""
        out = []
        for key in list(self._pending):
            pending = self._pending.pop(key)
            self.decisions_made += 1
            out.append(self._abstain(pending, t, f"{phrase} before the PPE could be judged"))
        # Stays still under way: the tracker closes these visits itself, but its closing events are not
        # fed back here, so they are closed here.
        for key, visit in list(self._visits.items()):
            if visit.judged:
                continue
            rule = visit.rule
            where = rule.zone_name or rule.zone_id
            dwell = max(0.0, t - visit.entered_at)
            out.append(self._emit(self._dwell_decision(
                rule, UNVERIFIABLE, f"Could not state the length of the stay in {where}: {phrase} while the worker was still inside, "
                                    f"after {_dur(dwell)}.", key[1], visit.entered_at,
                {"outcome": "INCOMPLETE", "dwell_seconds": round(dwell, 3), "entered_video_time_s": round(visit.entered_at, 3),
                 "decided_video_time_s": round(t, 3), "first_sighting": visit.first_sighting})))
        for key, tm in list(self._timers.items()):
            home = tm.rule.zone_name or tm.rule.zone_id
            out.append(self._emit(self._exit_decision(
                tm, UNVERIFIABLE, f"Left {home}, but {phrase} before they could be followed to "
                                  f"{self._zone_name(tm.rule.params['exit_zone_id'])}.",
                {"outcome": "INCOMPLETE", "elapsed_seconds": round(max(0.0, t - tm.left_at), 3), "decided_video_time_s": round(t, 3)})))
        self._visits.clear()
        self._timers.clear()
        self._cont.clear()                                           # an unfinished stay raises nothing
        self._cont_flag_t.clear()
        self._seen_at.clear()
        return out

    def reset(self, reason: str, last_seen_s: float, resume_at_s: float) -> List[Decision]:
        """Engine component contract: the timeline jumped. Whoever was still being judged cannot be."""
        out = self._close_all("the camera stopped sending pictures" if reason == "CAMERA_LOST" else "the video jumped", last_seen_s)
        self._last_judged.clear()           # footage after a jump is a different stretch of time
        return out

    def close(self, t: float) -> List[Decision]:
        """The session is over: anyone still being judged stays unverified."""
        return self._close_all("the video ended", t)

    def pending_workers(self) -> List[str]:
        return sorted({p.worker_key for p in self._pending.values()})
