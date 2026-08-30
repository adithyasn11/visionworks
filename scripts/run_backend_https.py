"""
Run the FastAPI backend over HTTPS, so a phone can talk to it.

    venv/Scripts/python.exe scripts/run_backend_https.py

WHY THE BACKEND NEEDS HTTPS TOO

The phone loads the dashboard over https, and a page served over https cannot
open a plain `ws://` socket — browsers block the mixed content silently, which
looks exactly like the backend being down. So both ends have to move together.

Binds 0.0.0.0 rather than 127.0.0.1, because the phone reaches this machine by
its LAN address and a server listening only on loopback is invisible to it.
That is a deliberate exposure: anything on your network can now reach this
port. Fine on a home or lab network, not fine on a café's.

Run scripts/make_dev_cert.py first if certs/ is empty.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT = os.path.join(ROOT, "certs", "dev-cert.pem")
KEY = os.path.join(ROOT, "certs", "dev-key.pem")


def main() -> int:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        print("No certificate found. Generate one first:")
        print("  venv/Scripts/python.exe scripts/make_dev_cert.py")
        return 1

    sys.path.insert(0, ROOT)
    import uvicorn

    print("Backend on https://0.0.0.0:8001")
    print("Reachable from your phone at https://<this-laptop-ip>:8001")
    print()
    print("The certificate is self-signed, so the phone will warn once per")
    print("origin. It must be accepted for BOTH :3000 and :8001 — the phone")
    print("treats them as separate sites, and a rejected certificate on the")
    print("backend looks like a silent connection failure with no message.")
    print()

    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=8001,
        ssl_certfile=CERT,
        ssl_keyfile=KEY,
        # No --reload: the reloader spawns a child process, and on Windows the
        # child re-imports the CUDA models while the parent still holds them.
        # Measured earlier in this project as "CUDA error: unknown error".
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
