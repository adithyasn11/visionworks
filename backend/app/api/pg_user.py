# backend/app/api/pg_user.py
"""
Talk to Postgres (PostgREST) AS THE SIGNED-IN USER.

The safety API forwards the caller's own access token with the project's public (anon) key, so
every query runs under that user's Row Level Security: the database, not this file, decides what
they may read or change. A bug in an endpoint here can therefore only ever return what the caller
could have read directly. (The service-role key is used elsewhere only for what a user must NOT be
able to do: writing audit rows, syncing measurements.)

Errors from PostgREST are turned into `PgError`, and `to_http()` maps them to a status code and a
sentence a person can read, never the raw database text.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from fastapi import HTTPException

TIMEOUT_S = 20


class PgError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message

    def to_http(self) -> HTTPException:
        """The HTTP error to show the caller: a status and a message, never the database's own text for unexpected failures."""
        code = self.code or ""
        if self.status == 401 or code in ("PGRST301", "PGRST303"):
            # an expired or revoked token (it can pass the 60 s identity cache and still be refused here)
            return HTTPException(status_code=401, detail="Your session has expired. Sign in again.")
        if code == "42501" or self.status == 403:
            return HTTPException(status_code=403, detail="You do not have permission to do that.")
        if code in ("P0001",):                                   # an exception the database raises on purpose, with its own words
            return HTTPException(status_code=422, detail=self.message[:200])
        if code == "P0002":
            return HTTPException(status_code=404, detail=self.message[:200] or "Not found.")
        if code.startswith("23"):
            return HTTPException(status_code=409 if code == "23505" else 422, detail=self.message[:200] or "The change breaks a rule of the data.")
        if code.startswith("22"):
            return HTTPException(status_code=422, detail="One of the values is not valid.")
        if self.status == 404:
            return HTTPException(status_code=404, detail="Not found.")
        return HTTPException(status_code=502, detail="The database could not complete the request.")


def configured() -> bool:
    return bool(os.getenv("SUPABASE_URL")) and bool(os.getenv("SUPABASE_KEY"))


def bearer(authorization: Optional[str]) -> Optional[str]:
    """The bare access token from an Authorization header value, or None."""
    if not authorization or not isinstance(authorization, str):
        return None
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def user_request(method: str, path: str, token: str, *, params: Optional[dict] = None, body=None,
                 prefer: Optional[str] = None, rpc: bool = False) -> Tuple[int, dict, object]:
    """
    (status, headers, parsed JSON or None). Raises PgError on a non-2xx answer and on a network failure
    (as a 502). `path` is a table name, or a function name when rpc=True.
    """
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    anon = os.getenv("SUPABASE_KEY") or ""
    if not base or not anon:
        raise PgError(503, "", "the database is not configured")
    url = f"{base}/rest/v1/{'rpc/' if rpc else ''}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"apikey": anon, "Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw = response.read().decode("utf-8") or ""
            return response.status, dict(response.headers), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        code, message = "", ""
        try:
            parsed = json.loads(e.read().decode("utf-8"))
            code, message = str(parsed.get("code", "")), str(parsed.get("message", ""))
        except Exception:
            pass
        raise PgError(e.code, code, message)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PgError(502, "", f"{type(e).__name__}")
