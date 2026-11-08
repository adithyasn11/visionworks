# backend/app/safety/schemas.py
"""
The shapes the rule engine passes around: a Rule (what to check), a Decision (what
was concluded). Plain dataclasses, no database, no model.

A Decision is the unit of accountability. It carries the verdict, the sentence that
explains it, a confidence, and the numbers the sentence was made from, so a
supervisor can see WHY and a reviewer can check it. `status` is one of three, and
UNVERIFIABLE is a result in its own right, not an error: "we could not tell" is
reported, counted and shown, never hidden and never turned into a guess.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.db.safety_models import DECISION_STATUSES, RULE_TYPES, SEVERITIES

COMPLIANT = "COMPLIANT"
POTENTIAL_VIOLATION = "POTENTIAL_VIOLATION"
UNVERIFIABLE = "UNVERIFIABLE"

assert set(DECISION_STATUSES) == {COMPLIANT, POTENTIAL_VIOLATION, UNVERIFIABLE}


@dataclass(frozen=True)
class Rule:
    """One thing to check, anchored to a zone (or, for a few types, to the whole camera)."""
    type: str                                   # one of RULE_TYPES
    zone_id: Optional[str]                      # the local zone id it is anchored to
    params: dict                                # validated, defaults filled in (rules.py)
    id: Optional[str] = None                    # the safety_rules row's uuid; None for the built-in default
    name: str = ""
    zone_name: str = ""
    severity: str = "WARNING"
    enabled: bool = True
    min_evidence_confidence: float = 0.6
    source: str = "default"                     # "default" (built in) or "database"

    def __post_init__(self):
        if self.type not in RULE_TYPES:
            raise ValueError(f"unknown rule type {self.type!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")

    @property
    def key(self) -> str:
        """Stable identity for 'one decision per worker per visit per rule'."""
        if self.id:
            return self.id
        # Rules without an id (the built-in ones) on one zone are told apart by their parameters.
        digest = hashlib.sha1(json.dumps(self.params, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]
        return f"{self.source}:{self.type}:{self.zone_id}:{digest}"


@dataclass
class Decision:
    status: str
    reason: str
    confidence: float                           # 0 for UNVERIFIABLE: no claim, no confidence
    evidence: dict
    rule_type: str
    severity: str
    worker_key: str
    zone_id: Optional[str]
    video_time_s: float                         # when it HAPPENED (the entry), not when it was decided
    rule_id: Optional[str] = None
    thresholds: dict = field(default_factory=dict)
    snapshot_requested: bool = False            # a flagged decision wants an evidence picture (Step 9)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))   # made here, at the source: re-sending is harmless

    def __post_init__(self):
        if self.status not in DECISION_STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if self.status == UNVERIFIABLE:
            self.confidence = 0.0               # never a quietly-supplied confidence on an abstention

    def as_payload(self) -> dict:
        """What the dashboard is shown the moment it is made."""
        return {
            "id": self.id, "status": self.status, "reason": self.reason,
            "confidence": round(self.confidence, 3), "rule_type": self.rule_type,
            "severity": self.severity, "worker_key": self.worker_key,
            "zone_id": self.zone_id, "video_time_s": round(self.video_time_s, 3),
        }
