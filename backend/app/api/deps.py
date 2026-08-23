# backend/app/api/deps.py
"""
Tenant resolution for the CV backend.

WHAT PROBLEM THIS SOLVES

Until now every row in `activity_logs` belonged to nobody. One SQLite file held
the telemetry of every organisation that had ever used this installation, and
every analytics query read all of it. Step 2 gives each row an owner.

WHY THE CLIENT IS NOT ASKED WHICH ORGANISATION IT IS

The obvious implementation is to accept `?org_id=...` and filter on it. That
produces a data model that looks tenant-scoped and a boundary that is not one:
this backend runs with `allow_origins=["*"]` and no session of its own, so a
self-asserted org id is a URL anyone can edit. The natural acceptance test
("pass a different org id, get zero rows") would pass with the system wide open,
which is the worst kind of green tick.

So the client sends what it legitimately has — its Supabase **access token** —
and the org is derived here:

    token ──▶ supabase.auth.get_user(token)     verifies the JWT signature
           ──▶ auth user id  (cannot be forged)
           ──▶ memberships WHERE profileId = uid AND status = 'ACTIVE'
           ──▶ org_id

The membership lookup runs *as that user*, with their own token, so Postgres RLS
applies to it: `membership_select` only returns rows the caller may see. The
backend holds the **anon** key, never the service-role key — it therefore cannot
read another tenant's rows even if this code were wrong. That is deliberate: the
key that defeats every policy must not live in the process that faces the
browser.

This mirrors the rule Step 1 arrived at the hard way — `createCamera()` reads
`currentOrgId` from the caller's profile rather than trusting the form field.
The value that reaches the database is the one the server established.

FAIL CLOSED

Every failure — no token, expired token, forged token, no membership, Supabase
unreachable — resolves to `None`, and callers treat `None` as "no tenant" and
return nothing. An unauthenticated request reading everything is precisely the
bug this module exists to prevent, so the safe default is emptiness, not
openness.
"""

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Verified identities are cached briefly. Without this, every analytics poll
# (the dashboard refreshes on a 15s timer) and every processing session would
# make a network round trip to Supabase before doing any work. 60s is short
# enough that revoking a membership takes effect promptly and long enough that
# a burst of requests costs one verification.
_TOKEN_CACHE_TTL_SECONDS = 60.0

# token -> (org_id, user_id, expires_at, role)
_token_cache: dict = {}
# The cache is touched from request handlers and from asyncio worker threads,
# so it needs a lock. Cheap: it is only held around a dict read or write.
_cache_lock = threading.Lock()

_client = None
_client_failed = False


def _supabase():
    """
    The Supabase client, built once.

    Returns None when the backend has no credentials, which is a legitimate
    state: the CV pipeline runs standalone for local development, and it should
    degrade to "no tenant" rather than refusing to start.
    """
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key or "your-" in url or "your-" in key:
        logger.warning(
            "SUPABASE_URL/SUPABASE_KEY not configured — telemetry will be written "
            "without an organisation and org-scoped queries will return nothing."
        )
        _client_failed = True
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        return _client
    except Exception as e:
        logger.error(f"Could not create Supabase client: {e}")
        _client_failed = True
        return None


def _cache_get(token: str):
    with _cache_lock:
        hit = _token_cache.get(token)
        if hit and hit[2] > time.time():
            return hit
        if hit:
            # Expired — drop it so the dict does not grow without bound.
            _token_cache.pop(token, None)
    return None


def _cache_put(token: str, org_id: Optional[str], user_id: Optional[str], role: Optional[str] = None):
    with _cache_lock:
        # Negative results are cached too, at the same TTL. A client polling
        # with a bad token would otherwise hammer the auth server every 15s.
        _token_cache[token] = (org_id, user_id, time.time() + _TOKEN_CACHE_TTL_SECONDS, role)
        # Crude bound. This process serves a handful of operators, not a public
        # API; if the map ever grows past this, the oldest entries are stale
        # anyway and dropping them costs one extra verification.
        if len(_token_cache) > 512:
            now = time.time()
            for k in [k for k, v in _token_cache.items() if v[2] <= now]:
                _token_cache.pop(k, None)


def resolve_org(token: Optional[str]) -> Optional[str]:
    """
    The organisation this access token belongs to, or None.

    Synchronous and network-bound, so async callers must run it through
    asyncio.to_thread() rather than calling it directly from the event loop —
    see resolve_org_async().
    """
    if not token or not isinstance(token, str):
        return None

    token = token.strip()
    # Accept a raw token or a full "Bearer <token>" header value, since this is
    # called from both a WebSocket query string and an HTTP header.
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return None

    cached = _cache_get(token)
    if cached is not None:
        return cached[0]

    client = _supabase()
    if client is None:
        return None

    try:
        # Verifies the JWT signature against the auth server. A forged or
        # expired token raises here — measured: AuthApiError "invalid JWT".
        response = client.auth.get_user(token)
        user = getattr(response, "user", None)
        user_id = getattr(user, "id", None) if user else None
        if not user_id:
            _cache_put(token, None, None)
            return None
    except Exception as e:
        logger.info(f"Token verification failed: {type(e).__name__}")
        _cache_put(token, None, None)
        return None

    resolved = _lookup_org(token, user_id)
    org_id, role = resolved if resolved else (None, None)
    _cache_put(token, org_id, user_id, role)
    return org_id


def _lookup_org(token: str, user_id: str):
    """
    The caller's ACTIVE organisation.

    Queried WITH THE USER'S OWN TOKEN, so the request carries their identity and
    RLS applies. Reading with the bare anon key returns nothing at all — the
    policies filter on auth.uid(), and `anon` has no SELECT grant on these
    tables — so this is not a precaution, it is what makes the query work.

    WHY A PLAIN HTTP CALL AND NOT THE SHARED SUPABASE CLIENT
    -------------------------------------------------------
    The obvious implementation is `client.postgrest.auth(token)` on the shared
    client. That is a CONCURRENCY BUG, and it was measured rather than guessed:
    the auth token is process-global state on the client object, so two threads
    resolving different users interleave their writes to it. Six concurrent
    threads produced ONE distinct org id across all of them plus "permission
    denied for table profiles" errors — one caller's identity was being used
    for another caller's query, and the restore-to-anon in the `finally` block
    made it worse by clearing a token another thread was still relying on.

    This matters here specifically because the three WebSocket handlers resolve
    the org through asyncio.to_thread(), so concurrent processing sessions hit
    exactly that path.

    PostgREST takes the token as an ordinary Authorization header, so issuing
    the request directly keeps the identity on the request where it belongs and
    shares no mutable state between callers.

    `profiles.currentOrgId` is the source of truth for "which org am I acting
    in", the same column the dashboard guard reads. It is verified against an
    ACTIVE membership rather than trusted alone: a suspended member still has a
    stale pointer, and honouring it would keep writing their telemetry into an
    org they were removed from.
    """
    import json
    import urllib.parse
    import urllib.request

    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    anon = os.getenv("SUPABASE_KEY") or ""
    if not base:
        return None

    def get(path: str, params: dict):
        url = f"{base}/rest/v1/{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                # `apikey` identifies the project; `Authorization` identifies
                # the USER. PostgREST reads auth.uid() from the latter, which
                # is what makes RLS apply to this query.
                "apikey": anon,
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8") or "[]")

    try:
        # The ROLE is fetched alongside the org, in the same request. It is what
        # Step 4 gates writes on: a VIEWER may read this organisation's
        # measurements but must not create zones or start an analysis, and the
        # endpoint needs to know that before it acts.
        memberships = get(
            "memberships",
            {"select": "orgId,role", "profileId": f"eq.{user_id}", "status": "eq.ACTIVE"},
        )
        by_org = {row["orgId"]: row.get("role") for row in memberships if row.get("orgId")}
        if not by_org:
            return None

        profiles = get("profiles", {"select": "currentOrgId", "id": f"eq.{user_id}"})
        current = profiles[0].get("currentOrgId") if profiles else None

        if current and current in by_org:
            return current, by_org[current]

        # Pointer missing or stale (e.g. membership revoked). Fall back to an
        # org they demonstrably still belong to, deterministically ordered so
        # repeated calls agree with each other.
        chosen = sorted(by_org)[0]
        return chosen, by_org[chosen]

    except Exception as e:
        logger.warning(f"Could not resolve organisation for user: {type(e).__name__}: {e}")
        return None


def resolve_org_role(token: Optional[str]):
    """
    (org_id, role) for this access token, or (None, None).

    Same verification path as resolve_org() — this is the role-aware form, used
    by endpoints that must distinguish "may read" from "may write". Shares the
    cache, so asking for both costs one lookup.
    """
    if not token or not isinstance(token, str):
        return None, None

    normalised = token.strip()
    if normalised.lower().startswith("bearer "):
        normalised = normalised[7:].strip()
    if not normalised:
        return None, None

    cached = _cache_get(normalised)
    if cached is not None:
        return cached[0], cached[3]

    # Populate the cache through the single verification path, then read it
    # back — duplicating the get_user()/lookup sequence here would be a second
    # place to keep correct.
    resolve_org(normalised)
    cached = _cache_get(normalised)
    return (cached[0], cached[3]) if cached else (None, None)


async def resolve_org_async(token: Optional[str]) -> Optional[str]:
    """
    resolve_org() off the event loop.

    The verification is two HTTPS round trips on a cache miss. Doing that inline
    in a WebSocket handler would stall every other session's frame delivery, the
    same reason activity_writer.py pushes its writes through a thread.
    """
    import asyncio
    if not token:
        return None
    return await asyncio.to_thread(resolve_org, token)


def extract_token(websocket) -> Optional[str]:
    """
    The access token from a WebSocket handshake.

    Browsers cannot set headers on a WebSocket, so the token travels in the
    query string — the standard workaround, and safe here because the value is
    a short-lived access token over TLS in deployment. The Sec-WebSocket-Protocol
    header is also accepted for non-browser clients that prefer it.
    """
    try:
        token = websocket.query_params.get("token")
        if token:
            return token
    except Exception:
        pass
    try:
        return websocket.headers.get("authorization")
    except Exception:
        return None
