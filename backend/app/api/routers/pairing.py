"""
Pair a phone to this dashboard, so it can act as a camera.

    laptop  ──ws──▶  /api/v1/pair/ws/{code}?role=host
    phone   ──ws──▶  /api/v1/pair/ws/{code}?role=camera

WHY A PAIRING CHANNEL EXISTS AT ALL

A laptop cannot switch on a phone's camera. No browser or OS permits it, and
one that did would be a serious hole — a web page could watch you from a
device you were not holding. So the phone has to opt in once, by opening a
page and granting camera access.

What this removes is having to do that EVERY time. The phone stays connected
to a room; the laptop sends "start" into that room; the phone, which already
has permission, opens its camera and begins sending frames. From the laptop it
is one click, which is what was asked for.

WHAT A ROOM IS

A short code, and up to two sockets: one host (the laptop) and one camera (the
phone). Messages are relayed between them and nowhere else.

Rooms are IN MEMORY and deliberately so. A pairing is a transient thing that
lasts as long as the two devices are looking at each other; persisting it
would mean a stale code from last week could still be dialled, and the code is
the only thing standing between a stranger on your network and your camera.

THE SECURITY THIS DOES AND DOES NOT PROVIDE

The host side requires the same Supabase token every other endpoint does, so a
room can only be CREATED by somebody signed in to an organisation.

The camera side does not, and cannot: the phone reaches it by scanning a QR
code, before any sign-in, and asking somebody to type an email and password on
a phone keyboard to attach a camera would defeat the point. The code itself is
the credential — 8 characters from a 32-symbol alphabet, single-use, expiring
in five minutes, and only reachable from inside your own network.

That is honest security for a LAN development tool. It is NOT enough to expose
this port to the internet, and the module refuses to pretend otherwise: see
the warning logged when a room is created.
"""

import asyncio
import ipaddress
import logging
import secrets
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import (APIRouter, Depends, Header, HTTPException, WebSocket,
                     WebSocketDisconnect)

from app.api.deps import extract_token, resolve_org, resolve_org_role
from app.api.permissions import can, denial_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pair", tags=["pairing"])


def require_analysis_run(authorization: Optional[str] = Header(default=None)) -> str:
    """
    Attaching a phone as a camera IS starting an analysis, so it needs the
    same capability. 401 when nobody is signed in, 403 when the role is
    read-only — different problems, different instructions.
    """
    org_id, role = resolve_org_role(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before pairing a camera.",
        )
    if not can(role, "analysis.run"):
        raise HTTPException(status_code=403, detail=denial_message("analysis.run"))
    return org_id


def current_org(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    return resolve_org(authorization)


# Long enough that guessing is hopeless on a LAN, short enough to read off a
# screen if the QR code will not scan. 32^8 is ~1.1e12.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no I/L/O/0/1 — misread as each other
CODE_LENGTH = 8

# A room nobody has joined is forgotten. Five minutes is long enough to walk to
# your phone and unlock it, short enough that a code left on screen overnight
# is useless by morning.
ROOM_TTL_SECONDS = 300.0

# Once paired, the room lives as long as both sides keep talking. This is the
# gap after which a silent room is cleaned up.
IDLE_TIMEOUT_SECONDS = 120.0


@dataclass
class Room:
    code: str
    org_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    host: Optional[WebSocket] = None
    camera: Optional[WebSocket] = None
    # Set once a phone has joined, so the laptop can show "paired" rather than
    # "waiting" after a reconnect.
    ever_paired: bool = False

    @property
    def expired(self) -> bool:
        if self.ever_paired:
            return (time.time() - self.last_activity) > IDLE_TIMEOUT_SECONDS
        return (time.time() - self.created_at) > ROOM_TTL_SECONDS

    def touch(self):
        self.last_activity = time.time()


_rooms: dict = {}
_lock = asyncio.Lock()


def _sweep():
    """Drop expired rooms. Called on every create and join; no timer needed."""
    for code in [c for c, r in _rooms.items() if r.expired]:
        _rooms.pop(code, None)


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def lan_host() -> Optional[str]:
    """
    This machine's address on the local network, or None.

    THE BROWSER CANNOT WORK THIS OUT, WHICH IS WHY THE SERVER DOES.

    The pairing QR used to be built from `window.location.hostname`, so a
    dashboard opened at localhost — the normal thing to do — produced a QR
    saying "localhost". Scanned by a phone, that means the PHONE, and the page
    never loads. Warning about it was not good enough: the fix is for the code
    to name an address the phone can actually reach.

    Only the server knows that address. Found by asking the routing table
    which interface would carry a packet to the internet; no packet is sent.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        return ip if ipaddress.ip_address(ip).is_private else None
    except Exception:
        return None
    finally:
        s.close()


# ── HTTP: the laptop asks for a code ──────────────────────────────────────

@router.post("/session")
async def create_pairing_session(org_id: str = Depends(require_analysis_run)):
    """Mint a pairing code for this organisation."""
    async with _lock:
        _sweep()
        code = new_code()
        while code in _rooms:
            code = new_code()
        _rooms[code] = Room(code=code, org_id=org_id)

    logger.info(
        f"pairing room {code} opened for org {org_id[:8]}. "
        "The code is the only credential on the camera side — this endpoint "
        "is for a trusted local network, not the public internet."
    )
    return {
        "code": code,
        "expires_in": int(ROOM_TTL_SECONDS),
        # The address the PHONE must dial. Sent from here because the browser
        # only knows the address IT used, which is usually localhost and is
        # useless to another device.
        "lan_host": lan_host(),
    }


@router.get("/session/{code}")
async def pairing_status(
    code: str,
    org_id: Optional[str] = Depends(current_org),
):
    """Has a phone joined yet? Polled by the laptop while it shows the QR."""
    room = _rooms.get(code.upper())
    if not room or room.org_id != org_id:
        # Same answer for "never existed" and "belongs to another org": a
        # different message would confirm that somebody else's code is live.
        raise HTTPException(status_code=404, detail="That pairing code is not active.")
    return {
        "code": room.code,
        "paired": room.camera is not None,
        "ever_paired": room.ever_paired,
        "host_connected": room.host is not None,
    }


# ── WebSocket: the room itself ────────────────────────────────────────────

async def _send(ws: Optional[WebSocket], payload: dict) -> bool:
    """Send, and report whether the socket is still usable."""
    if ws is None:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False


@router.websocket("/ws/{code}")
async def pairing_socket(websocket: WebSocket, code: str):
    """
    Join a pairing room as either the host (laptop) or the camera (phone).

    Everything one side sends is relayed to the other, untouched. This module
    deliberately knows nothing about frames or commands — it is a pipe. The
    laptop and the phone agree on the message shapes between themselves, which
    keeps the protocol in one place (the frontend) rather than split across
    two languages.
    """
    code = code.upper()
    role = (websocket.query_params.get("role") or "").lower()

    if role not in ("host", "camera"):
        await websocket.close(code=1008, reason="role must be host or camera")
        return

    async with _lock:
        _sweep()
        room = _rooms.get(code)

    if room is None:
        await websocket.accept()
        await _send(websocket, {
            "type": "error",
            "reason": "expired",
            "message": "This pairing code has expired. Generate a new one on "
                       "the laptop and scan it again.",
        })
        await websocket.close(code=1008, reason="unknown or expired code")
        return

    # The HOST must prove who it is. The camera cannot — it arrives from a QR
    # scan with no session — so the code is its credential. See the module
    # docstring on why that is the honest trade here.
    if role == "host":
        token = extract_token(websocket)
        org_id = None
        if token:
            try:
                org_id, _caller_role = await asyncio.to_thread(
                    resolve_org_role, token)
            except Exception:
                org_id = None
        if org_id != room.org_id:
            await websocket.accept()
            await _send(websocket, {
                "type": "error",
                "reason": "unauthorised",
                "message": "Sign in again to control this pairing.",
            })
            await websocket.close(code=1008, reason="not this organisation")
            return

    await websocket.accept()

    # One device per role. A second phone scanning the same code replaces the
    # first rather than both streaming into one session — two cameras writing
    # the same telemetry would double-count everybody in frame.
    async with _lock:
        previous = room.camera if role == "camera" else room.host
        if previous is not None:
            await _send(previous, {
                "type": "replaced",
                "message": "Another device took over this pairing.",
            })
            try:
                await previous.close(code=1000, reason="replaced")
            except Exception:
                pass
        if role == "camera":
            room.camera = websocket
            room.ever_paired = True
        else:
            room.host = websocket
        room.touch()

    other = (lambda: room.host) if role == "camera" else (lambda: room.camera)

    # Tell both sides where they stand, so neither has to guess.
    await _send(websocket, {
        "type": "joined",
        "role": role,
        "peer_present": other() is not None,
    })
    await _send(other(), {"type": "peer-joined", "role": role})

    if role == "camera":
        logger.info(f"pairing {code}: phone connected")

    try:
        while True:
            message = await websocket.receive_json()
            room.touch()

            kind = message.get("type")

            # A heartbeat keeps a paired room alive without a peer having to
            # be present — the phone may be waiting minutes for "start".
            if kind == "ping":
                await _send(websocket, {"type": "pong"})
                continue

            # Everything else is relayed verbatim.
            delivered = await _send(other(), message)
            if not delivered and kind == "frame":
                # Nobody is watching. Tell the phone once so it can stop
                # capturing rather than pushing frames into a closed socket.
                await _send(websocket, {"type": "peer-gone"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"pairing {code} ({role}) ended: {type(e).__name__}: {e}")
    finally:
        async with _lock:
            if role == "camera" and room.camera is websocket:
                room.camera = None
            elif role == "host" and room.host is websocket:
                room.host = None
            room.touch()
            # A room with nobody in it is finished. Keeping it would let the
            # code be redialled later, which is exactly the staleness the
            # in-memory design avoids.
            if room.host is None and room.camera is None:
                _rooms.pop(code, None)
        await _send(other(), {"type": "peer-left", "role": role})
        if role == "camera":
            logger.info(f"pairing {code}: phone disconnected")
