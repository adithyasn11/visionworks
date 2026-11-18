"""
The Python and JavaScript permission tables must agree (backend/app/api/permissions.py and
frontend/app/lib/permissions.js). They are the same promise made on both sides of the wire, and a
drift between them is a button that is shown but refused, or an endpoint that is open while the
screen says it is not. Also: the API exposes no face-naming routes (the enrolment and employee
screens were removed with the old product).

    venv/Scripts/python.exe backend/tests/test_permissions_parity.py
"""
import os
import re
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from app.api.permissions import CAPABILITIES, can            # noqa: E402
from app.main import app                                      # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


js = open(os.path.join(ROOT, "frontend", "app", "lib", "permissions.js"), encoding="utf-8").read()
block = js[js.index("const CAPABILITIES"):]
block = block[:block.index("});") if "});" in block else block.index("};")]
js_table = {}
for name, roles in re.findall(r"'([a-z.]+)'\s*:\s*\[([^\]]*)\]", block):
    js_table[name] = tuple(r.strip().strip("'\"") for r in roles.split(",") if r.strip())

print("PERMISSION TABLES")
check("the JavaScript table was read", len(js_table) >= 15, str(len(js_table)))
check("both tables name the same capabilities", set(js_table) == set(CAPABILITIES), f"only in js: {sorted(set(js_table) - set(CAPABILITIES))}; only in python: {sorted(set(CAPABILITIES) - set(js_table))}")
for name in sorted(set(js_table) & set(CAPABILITIES)):
    check(f"'{name}' lists the same roles in both", set(js_table[name]) == set(CAPABILITIES[name]), f"js {js_table[name]} vs python {CAPABILITIES[name]}")

print()
print("THE SAFETY CAPABILITIES")
expect = {"safety.view": {"ADMIN", "MANAGER", "VIEWER"}, "safety.review": {"ADMIN", "MANAGER"},
          "safety.rules.edit": {"ADMIN", "MANAGER"}, "safety.evidence.view": {"ADMIN", "MANAGER"}}
for cap, roles in expect.items():
    check(f"{cap}: {', '.join(sorted(roles))}", {r for r in ("ADMIN", "MANAGER", "VIEWER") if can(r, cap)} == roles)
check("an unknown role or capability is never allowed", not can("OWNER", "safety.view") and not can("ADMIN", "safety.everything") and not can(None, "safety.view"))

print()
print("THE API SURFACE")
paths = sorted(app.openapi()["paths"])
check("no employees / enrolment / face-naming route exists", not [p for p in paths if re.search(r"employee|enrol|face", p, re.I)], str([p for p in paths if re.search(r"employee|enrol|face", p, re.I)]))
expected_safety = ["/api/v1/safety/decisions", "/api/v1/safety/decisions/{decision_id}", "/api/v1/safety/decisions/{decision_id}/review",
                   "/api/v1/safety/decisions/{decision_id}/snapshot", "/api/v1/safety/events", "/api/v1/safety/export", "/api/v1/safety/rules",
                   "/api/v1/safety/rules/validate", "/api/v1/safety/rules/{rule_id}", "/api/v1/safety/summary"]
check("the safety endpoints of design section 10 are all there", all(p in paths for p in expected_safety), str([p for p in expected_safety if p not in paths]))

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for n in FAILS:
        print("  -", n)
    sys.exit(1)
print("ALL PASS")
