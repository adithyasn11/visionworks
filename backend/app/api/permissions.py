# backend/app/api/permissions.py
"""
Role capabilities for the CV backend — LAYER 2.

This is the Python mirror of `frontend/app/lib/permissions.js`. The two files
MUST agree, and the shared capability names are what make a disagreement
visible: `zones.edit` means the same thing on both sides of the wire.

WHY THE BACKEND NEEDS THIS AT ALL

Zone writes do not go through a Next.js Server Action. The ZoneEditor talks
directly to FastAPI, so gating only the Next actions would leave
`POST /api/v1/zones/` reachable by any authenticated member regardless of role.
Before this file, a VIEWER's zone save reached the database and was stopped
only by RLS — which works, but returns "new row violates row-level security
policy for table zones" to a person who simply is not allowed to draw zones.

THE THREE LAYERS, AND WHICH ONE ACTUALLY HOLDS

    1. UI          hides the editor from a VIEWER    (MembersScreen / dashboard)
    2. this file   refuses the request with a 403    (endpoint guard)
    3. Postgres    zone_insert USING manage_org_ids()  ← the real boundary

Layer 3 is the one that cannot be bypassed, and it was measured rather than
assumed: a VIEWER inserting a zone is rejected with a row-level security
violation, while MANAGER and ADMIN succeed. Layer 2 exists to turn that into an
honest status code and a readable sentence, and to fail before touching the
database at all.

If this file and the RLS policies ever disagree, the policy is right and this
file is the bug.
"""

from typing import Optional

# capability -> roles that hold it. Kept in the same order and with the same
# names as the JS table, so the two can be diffed by eye.
CAPABILITIES = {
    # Reading — every ACTIVE member, enforced by user_org_ids().
    "dashboard.view": ("ADMIN", "MANAGER", "VIEWER"),
    "analytics.view": ("ADMIN", "MANAGER", "VIEWER"),
    "reports.export": ("ADMIN", "MANAGER", "VIEWER"),
    "zones.view": ("ADMIN", "MANAGER", "VIEWER"),
    "members.view": ("ADMIN", "MANAGER", "VIEWER"),
    # enforcedBy: employee_select — membership-based, like every other roster.
    "employees.view": ("ADMIN", "MANAGER", "VIEWER"),

    # Configuring the space — enforced by manage_org_ids().
    "zones.edit": ("ADMIN", "MANAGER"),
    "cameras.edit": ("ADMIN", "MANAGER"),
    "sites.edit": ("ADMIN", "MANAGER"),
    # Running analysis WRITES telemetry, so it is a configuration act rather
    # than a read. A VIEWER watching a live feed would be creating data.
    "analysis.run": ("ADMIN", "MANAGER"),
    # enforcedBy: employee_insert/update and soft_delete_employee(). Also gates
    # face enrolment, which is the act that makes a named person recognisable
    # by camera — the most consequential write in the product.
    "employees.edit": ("ADMIN", "MANAGER"),

    # Governing the organisation — enforced by admin_org_ids().
    "members.invite": ("ADMIN",),
    "members.manage": ("ADMIN",),
    "org.settings": ("ADMIN",),
}


def can(role: Optional[str], capability: str) -> bool:
    """
    May this role perform this capability?

    Fails CLOSED on an unknown role (None, or a suspended member with no active
    membership) AND on an unknown capability. The second matters: if a typo
    returned True, a misspelled capability would silently grant everyone
    everything and still look like working code.
    """
    allowed = CAPABILITIES.get(capability)
    if not allowed:
        return False
    if not isinstance(role, str):
        return False
    return role in allowed


def denial_message(capability: str) -> str:
    """
    The refusal, phrased so the reader knows who to ask.

    "You do not have permission" leaves someone stuck; "only an administrator or
    manager can do that" tells them what to do next.
    """
    allowed = CAPABILITIES.get(capability)
    if not allowed:
        return "That action is not available."
    if allowed == ("ADMIN",):
        return "Only an administrator can do that."
    if "MANAGER" in allowed and "VIEWER" not in allowed:
        return "Only an administrator or manager can do that."
    return "You do not have permission to do that."
