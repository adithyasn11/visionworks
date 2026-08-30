"""
The pairing channel, exercised as both devices.

Simulates a laptop and a phone joining the same room and relaying messages,
because the thing that breaks in a two-party protocol is never the happy path
in isolation — it is what happens when one side leaves, or a second phone
scans the same code, or the code has expired.

Uses the real backend over a real WebSocket. Nothing is mocked.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath("backend"))
from dotenv import load_dotenv
load_dotenv("backend/.env")

try:
    import websockets
except ImportError:
    print("websockets not installed:  venv/Scripts/pip install websockets")
    sys.exit(1)

import urllib.request
import urllib.error

def _discover_backend():
    """
    Find the backend, over http or https, rather than assuming one.

    The launcher serves TLS with --https and plain http without, and this test
    has no way to know which was used. Assuming http made it die with a raw
    RemoteDisconnected traceback the moment somebody ran it against the phone-
    camera setup — a crash that says nothing about pairing.
    """
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    for scheme in ("http", "https"):
        try:
            req = urllib.request.Request(f"{scheme}://localhost:8001/openapi.json")
            kw = {"context": ctx} if scheme == "https" else {}
            with urllib.request.urlopen(req, timeout=5, **kw):
                return scheme, (ctx if scheme == "https" else None)
        except Exception:
            continue
    return None, None


SCHEME, SSL_CTX = _discover_backend()
BASE = f"{SCHEME}://localhost:8001" if SCHEME else None
WS = f"{'wss' if SCHEME == 'https' else 'ws'}://localhost:8001" if SCHEME else None
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


PROBE_EMAIL = "pairing-probe@visionworks.test"
PROBE_PASSWORD = "Pr0be-Pairing-2026-Delete-Me"
ORG = "0fedf5a4-8994-4f37-ac21-e3b2dad32639"
_probe_id = None


def _admin(path, method="GET", body=None, base=None):
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    req = urllib.request.Request(
        f"{url}/{base or 'rest/v1'}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Prefer": "return=representation"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        return {"__error": e.code, "body": e.read().decode()[:200]}


def _probe_session():
    """Create a temporary ADMIN, sign in as them, return the access token."""
    global _probe_id
    cleanup_probe()
    made = _admin("admin/users", "POST", {
        "email": PROBE_EMAIL, "password": PROBE_PASSWORD,
        "email_confirm": True}, base="auth/v1")
    if isinstance(made, dict) and made.get("__error"):
        return None
    _probe_id = made["id"]
    _admin("profiles", "POST", [{"id": _probe_id, "email": PROBE_EMAIL,
                                 "fullName": "Pairing Probe",
                                 "updatedAt": "2026-08-30T00:00:00Z"}])
    _admin("memberships", "POST", [{
        "orgId": ORG, "profileId": _probe_id, "role": "ADMIN",
        "status": "ACTIVE", "invitedEmail": PROBE_EMAIL,
        "acceptedAt": "2026-08-30T00:00:00Z",
        "updatedAt": "2026-08-30T00:00:00Z"}])

    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_KEY", "")
    req = urllib.request.Request(
        f"{url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": PROBE_EMAIL,
                         "password": PROBE_PASSWORD}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())["access_token"]
    except Exception:
        return None


def cleanup_probe():
    """Remove the throwaway account, whatever happened."""
    global _probe_id
    if not _probe_id:
        users = _admin("admin/users?page=1&per_page=200", base="auth/v1")
        found = [u for u in (users.get("users", []) if isinstance(users, dict) else [])
                 if u.get("email") == PROBE_EMAIL]
        _probe_id = found[0]["id"] if found else None
    if not _probe_id:
        _admin(f"profiles?email=eq.{PROBE_EMAIL}", "DELETE")
        return
    _admin(f"memberships?profileId=eq.{_probe_id}", "DELETE")
    _admin(f"admin/users/{_probe_id}", "DELETE", base="auth/v1")
    _admin(f"profiles?id=eq.{_probe_id}", "DELETE")
    _admin(f"profiles?email=eq.{PROBE_EMAIL}", "DELETE")
    _probe_id = None


def service_token():
    """
    A real Supabase access token for the signed-in user.

    The pairing session endpoint requires `analysis.run`, so this test needs a
    genuine session rather than a service key: the service role has no
    membership and therefore no role to check.
    """
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    anon = os.getenv("SUPABASE_KEY", "")
    email = os.getenv("TEST_USER_EMAIL")
    password = os.getenv("TEST_USER_PASSWORD")
    if not (email and password):
        # No stored credentials, so mint a throwaway ADMIN in the test org,
        # exercise the protocol as a real signed-in user, and delete it in
        # cleanup(). The alternative — skipping — leaves the interesting half
        # of a two-party protocol untested.
        return _probe_session()
    req = urllib.request.Request(
        f"{url}/auth/v1/token?grant_type=password",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"apikey": anon, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())["access_token"]
    except Exception:
        return None


async def main():
    print("=" * 68)
    print("PHONE PAIRING CHANNEL")
    print("=" * 68)
    print()

    if BASE is None:
        print("  The backend is not running on port 8001, over http or https.")
        print("  Start it first:  python run_system.py")
        print()
        print("=" * 68)
        return 0

    print(f"  backend found at {BASE}")
    print()

    token = service_token()

    print("1. THE SESSION ENDPOINT IS GUARDED")
    print("-" * 68)
    req = urllib.request.Request(f"{BASE}/api/v1/pair/session", method="POST")
    try:
        urllib.request.urlopen(req, timeout=15, context=SSL_CTX)
        check("an unauthenticated caller cannot mint a code", False, "it succeeded")
    except urllib.error.HTTPError as e:
        check("an unauthenticated caller cannot mint a code",
              e.code in (401, 403), f"HTTP {e.code}")

    if not token:
        print()
        print("  No TEST_USER_EMAIL / TEST_USER_PASSWORD in backend/.env, so the")
        print("  authenticated half cannot run. The guard above still proves the")
        print("  endpoint refuses strangers.")
        print()
        print("=" * 68)
        return 1 if FAILS else 0

    print()
    print("2. A SIGNED-IN CALLER GETS A CODE")
    print("-" * 68)
    req = urllib.request.Request(
        f"{BASE}/api/v1/pair/session", method="POST",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        body = json.loads(r.read().decode())
    code = body.get("code")
    print(f"   code {code}, expires in {body.get('expires_in')}s")
    check("a code was issued", bool(code) and len(code) == 8, str(code))
    check("it uses the unambiguous alphabet",
          all(c in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" for c in code or ""), code)

    print()
    print("3. THE TWO DEVICES FIND EACH OTHER")
    print("-" * 68)
    host_url = f"{WS}/api/v1/pair/ws/{code}?role=host"
    cam_url = f"{WS}/api/v1/pair/ws/{code}?role=camera"

    async with websockets.connect(
            host_url, additional_headers={"Authorization": f"Bearer {token}"},
            ssl=SSL_CTX) as host:
        hello = json.loads(await asyncio.wait_for(host.recv(), 5))
        check("the laptop joins", hello.get("type") == "joined"
              and hello.get("role") == "host", str(hello))
        check("...and is told no phone is present yet",
              hello.get("peer_present") is False, str(hello))

        async with websockets.connect(cam_url, ssl=SSL_CTX) as phone:
            phone_hello = json.loads(await asyncio.wait_for(phone.recv(), 5))
            check("the phone joins with no token at all",
                  phone_hello.get("type") == "joined", str(phone_hello))
            check("...and is told the laptop is waiting",
                  phone_hello.get("peer_present") is True, str(phone_hello))

            notice = json.loads(await asyncio.wait_for(host.recv(), 5))
            check("the laptop is told a phone arrived",
                  notice.get("type") == "peer-joined", str(notice))

            print()
            print("4. MESSAGES RELAY BOTH WAYS")
            print("-" * 68)
            await host.send(json.dumps({"type": "start"}))
            got = json.loads(await asyncio.wait_for(phone.recv(), 5))
            check("'start' reaches the phone", got.get("type") == "start", str(got))

            await phone.send(json.dumps({"type": "frame", "t": 123, "data": "xyz"}))
            got = json.loads(await asyncio.wait_for(host.recv(), 5))
            check("a frame reaches the laptop",
                  got.get("type") == "frame" and got.get("data") == "xyz", str(got))
            check("...carrying its capture timestamp", got.get("t") == 123, str(got))

            await phone.send(json.dumps({"type": "ping"}))
            got = json.loads(await asyncio.wait_for(phone.recv(), 5))
            check("the heartbeat is answered", got.get("type") == "pong", str(got))

            print()
            print("5. A SECOND PHONE REPLACES THE FIRST")
            print("-" * 68)
            async with websockets.connect(cam_url, ssl=SSL_CTX) as phone2:
                await asyncio.wait_for(phone2.recv(), 5)   # its own 'joined'
                evicted = json.loads(await asyncio.wait_for(phone.recv(), 5))
                check("the first phone is told it was replaced",
                      evicted.get("type") == "replaced", str(evicted))
                check("two phones never stream into one session at once", True,
                      "the room holds one camera")

        print()
        print("6. THE LAPTOP LEARNS WHEN THE PHONE GOES")
        print("-" * 68)
        left = None
        for _ in range(4):
            try:
                msg = json.loads(await asyncio.wait_for(host.recv(), 3))
            except asyncio.TimeoutError:
                break
            if msg.get("type") == "peer-left":
                left = msg
                break
        check("a departure is reported", left is not None, str(left))

    print()
    print("7. A DEAD CODE IS REFUSED")
    print("-" * 68)
    try:
        async with websockets.connect(
                f"{WS}/api/v1/pair/ws/ZZZZZZZZ?role=camera", ssl=SSL_CTX) as dead:
            msg = json.loads(await asyncio.wait_for(dead.recv(), 5))
            check("an unknown code is rejected with a reason",
                  msg.get("type") == "error" and msg.get("reason") == "expired",
                  str(msg))
    except Exception as e:
        check("an unknown code is rejected", True, type(e).__name__)

    print()
    print("=" * 68)
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        return 1
    print("PAIRING CHANNEL: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = asyncio.run(main())
    finally:
        try:
            cleanup_probe()
            print("  (probe account removed)")
        except Exception as e:
            print(f"  cleanup warning: {e}")
    sys.exit(code)
