# backend/app/safety/compliance_engine.py
"""
From zone events and PPE evidence to a verdict with a reason.

Only one rule is judged so far: PPE_ENTRY in mode AT_ENTRY ("helmet and vest before
you enter a restricted zone"). Every other rule type parses and validates (rules.py)
but is not evaluated yet, and `unsupported` says which, so nothing is silently
ignored.

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
"""

import logging
from typing import Dict, List, Optional, Tuple

from app.safety.config import SafetyConfig
from app.safety.schemas import COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE, Decision, Rule
from app.safety.zone_events import ZoneEvent

_log = logging.getLogger(__name__)

SUPPORTED = ("PPE_ENTRY",)

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


def _fmt_items(items: List[str]) -> str:
    names = [_NAMES.get(i, i) for i in items]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _count(fraction: float, total: int) -> int:
    return int(round(float(fraction) * int(total)))


class ComplianceEngine:
    def __init__(self, rules: List[Rule], ppe, config: Optional[SafetyConfig] = None):
        """
        rules   the session's rules (rules.load_rules). Disabled ones are ignored.
        ppe     anything with `verdict(worker_key, t) -> dict | None` and an
                `unavailable_reason` (the PpeRunner), or None when the session does
                no PPE checks, in which case PPE rules cannot be judged.
        """
        self.config = config or SafetyConfig()
        self.ppe = ppe
        self.rules = [r for r in rules if r.enabled]
        self.active = [r for r in self.rules if r.type in SUPPORTED and r.params.get("mode", "AT_ENTRY") == "AT_ENTRY"]
        self.unsupported = [r for r in self.rules if r not in self.active]
        if self.unsupported:
            _log.info("safety rules loaded but not evaluated yet: "
                      + ", ".join(sorted({f"{r.type}/{r.params.get('mode', '')}".rstrip('/') for r in self.unsupported})))
        self._by_zone: Dict[str, List[Rule]] = {}
        for r in self.active:
            self._by_zone.setdefault(r.zone_id, []).append(r)
        self._pending: Dict[Tuple[str, str], _Pending] = {}
        self._last_judged: Dict[Tuple[str, str], float] = {}         # (rule key, worker) -> entry time of the last CLAIM
        self.suppressed = 0                                          # re-entries inside the realert window
        self.decisions_made = 0

    # ── events ───────────────────────────────────────────────────────────────

    def on_event(self, event: ZoneEvent, now: float) -> List[Decision]:
        """Feed one zone event (never one from a replayed stretch). `now` is the current frame's video time."""
        out: List[Decision] = []
        if event.event_type == "ENTERED_ZONE":
            for rule in self._by_zone.get(event.zone_id, ()):
                self._open(rule, event, now, out)
        elif event.event_type == "EXITED_ZONE":
            for rule in self._by_zone.get(event.zone_id, ()):
                key = (rule.key, event.worker_key)
                pending = self._pending.get(key)
                if pending is not None:
                    why = event.details.get("reason")
                    where = "was lost from view" if why == "LOST" else "left the zone"
                    out.append(self._finish(key, self._abstain(
                        pending, now, f"{where} before the {_fmt_items(rule.params['required_ppe'])} could be judged")))
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

    def on_tick(self, t: float) -> List[Decision]:
        """Look again at every open decision with the evidence as it stands at video time `t`."""
        out: List[Decision] = []
        for key in list(self._pending):
            pending = self._pending.get(key)
            if pending is not None:
                self._try(pending, t, out)
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

    # ── jumps and the end ───────────────────────────────────────────────────

    def _close_all(self, why: str, t: float) -> List[Decision]:
        out = []
        for key in list(self._pending):
            pending = self._pending.pop(key)
            self.decisions_made += 1
            out.append(self._abstain(pending, t, why))
        return out

    def reset(self, reason: str, last_seen_s: float, resume_at_s: float) -> List[Decision]:
        """Engine component contract: the timeline jumped. Whoever was still being judged cannot be."""
        out = self._close_all("the video jumped before the PPE could be judged", last_seen_s)
        self._last_judged.clear()           # footage after a jump is a different stretch of time
        return out

    def close(self, t: float) -> List[Decision]:
        """The session is over: anyone still being judged stays unverified."""
        return self._close_all("the video ended before the PPE could be judged", t)

    def pending_workers(self) -> List[str]:
        return sorted({p.worker_key for p in self._pending.values()})
