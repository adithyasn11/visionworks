"""
Generate a local HTTPS certificate so a phone can use its camera.

    venv/Scripts/python.exe scripts/make_dev_cert.py

WHY THIS IS NEEDED AT ALL

Browsers refuse `getUserMedia` on an insecure origin. `localhost` is exempt —
which is why the laptop webcam works today — but a phone reaching the dev
server at `http://192.168.1.5:3000` is not localhost, and the camera is simply
denied with no useful message.

So the dev server has to speak HTTPS. That needs a certificate, and a
certificate has to name the address it is served from: a cert for "localhost"
is rejected when the phone connects to an IP.

WHAT THIS PRODUCES

A self-signed cert covering localhost, 127.0.0.1 and every LAN address this
machine currently has. Self-signed means the phone shows a warning the first
time — tap through it once and it is remembered. That is the trade the setup
guide explains rather than hides: the alternative is installing a root CA on
your phone, which is a bigger security decision than this is worth.

The IPs are read at generation time, so if your laptop changes network the
cert stops matching and must be regenerated. The script says so when it runs.

Nothing here is secret. The key never leaves this machine and the cert is
valid only for private addresses; both are gitignored so they cannot be
committed by accident.
"""

import ipaddress
import os
import socket
import subprocess
import sys
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(ROOT, "certs")
CERT = os.path.join(CERT_DIR, "dev-cert.pem")
KEY = os.path.join(CERT_DIR, "dev-key.pem")


def local_ips() -> list:
    """
    Every IPv4 address this machine answers on.

    Tries the routing table first (the address a packet to the internet would
    leave from — almost always the Wi-Fi address the phone must reach), then
    falls back to whatever the hostname resolves to. Both, because a laptop on
    Wi-Fi and Ethernet has two, and the phone might be reachable on either.
    """
    found = set()

    # The address used to reach the outside world. No packet is actually sent;
    # connect() on a UDP socket just picks the route.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        found.add(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except Exception:
        pass

    # Only private ranges. A public address in a dev cert would be a mistake.
    out = []
    for ip in sorted(found):
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_private and not addr.is_loopback:
                out.append(ip)
        except ValueError:
            continue
    return out


def main() -> int:
    openssl = shutil.which("openssl")
    if not openssl:
        print("openssl was not found on PATH.")
        print("Git for Windows ships it at C:\\Program Files\\Git\\usr\\bin\\openssl.exe")
        return 1

    ips = local_ips()
    if not ips:
        print("No private LAN address found. Is this machine on Wi-Fi?")
        print("The certificate can still be generated, but a phone will not")
        print("be able to reach this laptop.")

    os.makedirs(CERT_DIR, exist_ok=True)

    # Subject Alternative Names. The browser checks the address it CONNECTED to
    # against this list — a cert naming only "localhost" is rejected when the
    # phone dials an IP, which is the whole failure this script exists to
    # prevent.
    alts = ["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in ips]
    san = ",".join(alts)

    print("Generating a development certificate for:")
    for a in alts:
        print(f"  {a}")
    print()

    cmd = [
        openssl, "req", "-x509",
        "-newkey", "rsa:2048",
        "-keyout", KEY,
        "-out", CERT,
        "-days", "365",
        "-nodes",                       # no passphrase: a dev server cannot type one
        "-subj", "/CN=VisionWorks Dev",
        "-addext", f"subjectAltName={san}",
        # Chrome on Android rejects certs without these on a modern TLS stack.
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,digitalSignature,keyCertSign",
        "-addext", "extendedKeyUsage=serverAuth",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("openssl failed:")
        print(result.stderr.strip()[:600])
        return 1

    print(f"  certificate -> {CERT}")
    print(f"  private key -> {KEY}")
    print()
    print("Valid for 365 days, and only for the addresses listed above.")
    print("If this laptop joins a different network its IP changes and you")
    print("will need to run this again.")
    print()
    print("Next:  npm run dev:https   (in frontend/)")
    print("       venv/Scripts/python.exe scripts/run_backend_https.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
