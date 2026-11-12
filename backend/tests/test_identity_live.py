"""
The caller-identity check (app/api/deps.py) against the real Supabase project.

Since the speed work, a token is verified LOCALLY (get_claims against the project's published ES256 key set)
instead of a round trip to the auth server on every cache miss, and concurrent misses for one token verify
once. This checks that nothing unsafe came with it:

  * no token, garbage, the public anon key, an expired token and a forged token (real key id, wrong
    signature) are all refused;
  * ten concurrent requests with one unknown token run ONE verification (single flight);
  * a refused token is cached as refused, so a client polling with it does not hit the network each time.

No real user token is needed or printed.

    venv/Scripts/python.exe backend/tests/test_identity_live.py
"""
import base64
import json
import os
import sys
import threading
import time

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from dotenv import load_dotenv                                            # noqa: E402
load_dotenv(os.path.join(BACKEND, ".env"))

import httpx                                                               # noqa: E402
from app.api import deps                                                   # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def fresh(token):
    deps._token_cache.clear()
    return deps.resolve_identity(token)


if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
    print("SKIP: SUPABASE_URL / SUPABASE_KEY not set")
    sys.exit(0)

kid = httpx.get(os.getenv("SUPABASE_URL").rstrip("/") + "/auth/v1/.well-known/jwks.json", timeout=15).json()["keys"][0]["kid"]
USER = "00000000-0000-0000-0000-0000000000aa"
header = b64({"alg": "ES256", "kid": kid, "typ": "JWT"})
forged = f"{header}.{b64({'sub': USER, 'role': 'authenticated', 'exp': int(time.time()) + 3600})}.{base64.urlsafe_b64encode(os.urandom(64)).rstrip(b'=').decode()}"
expired = f"{header}.{b64({'sub': USER, 'role': 'authenticated', 'exp': int(time.time()) - 60})}.{base64.urlsafe_b64encode(os.urandom(64)).rstrip(b'=').decode()}"

print("=" * 66)
print("CALLER IDENTITY (local token verification)")
print("=" * 66)
NONE = (None, None, None)
check("no token is refused", fresh(None) == NONE)
check("a garbage token is refused", fresh("not-a-jwt") == NONE)
check("the public anon key (no user) is refused", fresh(os.getenv("SUPABASE_KEY")) == NONE)
check("an expired token is refused", fresh(expired) == NONE)
check("a forged token with the real key id is refused", fresh(forged) == NONE)

# single flight: count how often the verification itself runs for one token hit by 10 threads at once
deps._token_cache.clear()
calls = {"n": 0}
real = deps._verify_and_cache


def counting(token):
    calls["n"] += 1
    time.sleep(0.2)                       # hold the lock long enough for the others to queue
    return real(token)


deps._verify_and_cache = counting
try:
    token = forged + "x"
    results = []
    threads = [threading.Thread(target=lambda: results.append(deps.resolve_identity(token))) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("ten concurrent requests with one token verify it once", calls["n"] == 1, f"{calls['n']} verifications")
    check("and all ten are refused", len(results) == 10 and all(r == NONE for r in results))
    before = calls["n"]
    deps.resolve_identity(token)
    check("a refused token is cached as refused (no second verification)", calls["n"] == before)
finally:
    deps._verify_and_cache = real

print()
if FAILS:
    print(f"FAILED: {len(FAILS)}")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
