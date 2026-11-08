# d:\major project\run_system.py
"""
Unified System Launcher — VisionWorks safety compliance monitoring
Starts FastAPI (port 8001) and Next.js (port 3000).
Keeps running until Ctrl+C. Kills children on exit.

    python run_system.py            http, laptop only
    python run_system.py --https    https, so a PHONE can be a camera

WHY THERE IS AN HTTPS MODE

Browsers refuse camera access on an insecure origin, with `localhost` the only
exception. That is why the laptop's own webcam works over plain http and a
phone at http://192.168.x.x:3000 is silently denied.

So --https exists for one purpose: pairing a phone as a camera. It generates a
certificate if there is not one, serves both processes over TLS, and prints
the address the PHONE must open — which is never localhost, because on a phone
localhost means the phone.

Everything else is identical. Use plain `python run_system.py` for normal work;
the certificate warning is a small tax you should not pay daily.
"""

import argparse
import ipaddress
import os
import socket
import sys
import time
import subprocess
import threading
import urllib.request

# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeError
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR     = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR  = os.path.join(ROOT_DIR, "backend")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")

CERT_DIR  = os.path.join(ROOT_DIR, "certs")
CERT_FILE = os.path.join(CERT_DIR, "dev-cert.pem")
KEY_FILE  = os.path.join(CERT_DIR, "dev-key.pem")

VENV_PYTHON = (
    os.path.join(ROOT_DIR, "venv", "Scripts", "python.exe")
    if sys.platform == "win32"
    else os.path.join(ROOT_DIR, "venv", "bin", "python")
)
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

# ── Helpers ─────────────────────────────────────────────────────────────────

def kill_port(port: int):
    """Kill every process listening on the given port (Windows netstat + taskkill)."""
    if sys.platform != "win32":
        subprocess.run(f"fuser -k {port}/tcp", shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        out = subprocess.check_output(
            f'netstat -ano | findstr ":{port} "',
            shell=True, text=True, stderr=subprocess.DEVNULL
        )
        seen: set[str] = set()
        for line in out.strip().splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            p = parts[-1]
            if p.isdigit() and p != "0" and p not in seen:
                seen.add(p)
                subprocess.run(f"taskkill /F /T /PID {p}",
                               shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        pass


def check_http_ok(url: str, timeout: float = 3.0) -> bool:
    """
    Light GET to verify the server is answering.

    The certificate is deliberately not verified. It is self-signed by
    design, and this check is asking "is the port serving yet", not "do I
    trust this host" — with verification on, every HTTPS health check fails
    and the launcher reports a server that is running perfectly as dead.
    """
    try:
        ctx = None
        if url.startswith("https://"):
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status < 400
    except Exception:
        return False


def wait_port_up(url: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_http_ok(url):
            return True
        time.sleep(0.5)
    return False


def stream(proc, tag: str, color: str):
    """Stream subprocess stdout to console with a colored tag prefix."""
    reset = "\033[0m"
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            print(f"{color}[{tag}]{reset} {line.rstrip()}", flush=True)
    except Exception:
        pass


def kill_proc(proc, name: str):
    if proc is None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                f"taskkill /F /T /PID {proc.pid}",
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:
        pass
    print(f"[SHUTDOWN] {name} stopped.")


def lan_ip() -> str:
    """
    The address a phone must dial to reach this laptop.

    Found by asking the routing table which interface would carry a packet to
    the internet — no packet is actually sent. That is the Wi-Fi address in
    almost every case, and it is what belongs in the QR code: `localhost` in a
    QR sends the phone to ITSELF, which is the single most confusing way this
    can fail.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        addr = ipaddress.ip_address(ip)
        return ip if addr.is_private else ""
    except Exception:
        return ""
    finally:
        s.close()


def ensure_cert(verbose: bool = True) -> bool:
    """
    Make sure a usable certificate exists, generating one if not.

    Also regenerates when the current LAN address is missing from the cert.
    A laptop that joins a different network gets a different IP, and a
    certificate that does not name the address the phone dialled is rejected
    with an error that looks nothing like "your IP changed".
    """
    need = not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE))
    ip = lan_ip()

    if not need and ip:
        try:
            out = subprocess.check_output(
                ["openssl", "x509", "-in", CERT_FILE, "-noout", "-text"],
                text=True, stderr=subprocess.DEVNULL)
            if f"IP Address:{ip}" not in out:
                if verbose:
                    print(f"[SYSTEM] Certificate does not cover {ip} "
                          "(this laptop changed network). Regenerating ...")
                need = True
        except Exception:
            # openssl missing or the cert unreadable — regenerating is the
            # safe answer either way.
            need = True

    if not need:
        return True

    if verbose:
        print("[SYSTEM] Generating an HTTPS certificate ...")
    result = subprocess.run(
        [VENV_PYTHON, os.path.join(ROOT_DIR, "scripts", "make_dev_cert.py")],
        capture_output=True, text=True)
    if result.returncode != 0:
        print("[ERROR] Could not generate a certificate:")
        print(result.stdout.strip()[-500:] or result.stderr.strip()[-500:])
        return False
    return True


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Start the VisionWorks backend and dashboard.")
    parser.add_argument(
        "--https", action="store_true",
        help="serve over HTTPS so a phone can be used as a camera")
    args = parser.parse_args()

    CYAN   = "\033[96m"
    BLUE   = "\033[94m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RESET  = "\033[0m"

    print(f"{CYAN}{'='*72}{RESET}")
    print(f"{CYAN} >>> VISIONWORKS — WORKPLACE SAFETY COMPLIANCE — LAUNCHER <<<{RESET}")
    print(f"{CYAN}{'='*72}{RESET}\n")

    scheme = "https" if args.https else "http"
    ip = lan_ip()

    if args.https:
        if not ensure_cert():
            sys.exit(1)
        if not ip:
            print(f"{YELLOW}[SYSTEM]{RESET} No LAN address found — is this "
                  "laptop on Wi-Fi? A phone will not be able to reach it.")

    # ── 1. Free ports ──────────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Freeing ports 8001 and 3000 ...")
    kill_port(8001)
    kill_port(3000)
    time.sleep(1.0)

    # ── 2. Start Backend ───────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Starting FastAPI backend  -> {scheme}://localhost:8001")

    win_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    backend_cmd = [VENV_PYTHON, "-m", "uvicorn", "app.main:app",
                   "--host", "0.0.0.0", "--port", "8001", "--log-level", "info"]
    if args.https:
        backend_cmd += ["--ssl-certfile", CERT_FILE, "--ssl-keyfile", KEY_FILE]

    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        creationflags=win_flags,
    )
    threading.Thread(target=stream, args=(backend_proc, "BACKEND ", BLUE), daemon=True).start()

    print(f"{YELLOW}[SYSTEM]{RESET} Waiting for backend to bind port 8001 ...")
    if not wait_port_up(f"{scheme}://localhost:8001/", timeout=45):
        print("[ERROR] Backend did not start within 45 s. Check logs above.")
        kill_proc(backend_proc, "Backend")
        sys.exit(1)
    print(f"{YELLOW}[SYSTEM]{RESET} Backend is UP ✓")

    # ── 3. Start Frontend ──────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Starting Next.js frontend -> {scheme}://localhost:3000")

    frontend_cmd = "npx next dev -p 3000 -H 0.0.0.0"
    if args.https:
        # Next generates its own certificate unless pointed at one, and its
        # own would not name this laptop's LAN address — so the phone would
        # reject it.
        frontend_cmd += (" --experimental-https"
                         f' --experimental-https-key "{KEY_FILE}"'
                         f' --experimental-https-cert "{CERT_FILE}"')

    frontend_proc = subprocess.Popen(
        frontend_cmd,
        shell=True,
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        creationflags=win_flags,
    )
    threading.Thread(target=stream, args=(frontend_proc, "FRONTEND", GREEN), daemon=True).start()

    print(f"{YELLOW}[SYSTEM]{RESET} Waiting for frontend to bind port 3000 ...")
    if not wait_port_up(f"{scheme}://localhost:3000/", timeout=90):
        print("[ERROR] Frontend did not start within 60 s. Check logs above.")
        kill_proc(backend_proc,  "Backend")
        kill_proc(frontend_proc, "Frontend")
        sys.exit(1)
    print(f"{YELLOW}[SYSTEM]{RESET} Frontend is UP ✓")

    print(f"\n{CYAN}{'='*72}{RESET}")
    print(f"{CYAN} SYSTEM ONLINE — Press Ctrl + C to shut down everything{RESET}")
    print(f"{CYAN}{'='*72}{RESET}\n")

    if args.https and ip:
        # This address matters more than anything else printed here. Opening
        # the dashboard at localhost puts "localhost" into the pairing QR, and
        # on a phone that means the phone — the scan appears to work and then
        # nothing ever loads.
        # BOTH addresses, because forgetting the "s" is the first thing that
        # goes wrong: the port is serving TLS, so a plain http:// request gets
        # no response at all and the browser reports the site as simply down.
        # Somebody who has typed localhost:3000 a hundred times will type it
        # again, and needs to be told what changed rather than left debugging
        # a server that is running perfectly.
        print(f"{GREEN} On this laptop:{RESET}")
        print(f"{GREEN}   https://localhost:3000{RESET}"
              f"        <- note the S; plain http will NOT load")
        print()
        print(f"{GREEN} To pair a phone, open this one instead:{RESET}")
        print(f"{GREEN}   https://{ip}:3000{RESET}   <- NOT localhost")
        print()
        print(f"{YELLOW} Your browser will warn about the certificate. It is")
        print(f" self-signed by this laptop: click Advanced, then Proceed.{RESET}")
        print()
        print(" Then: Live feed -> Use my phone as a camera -> scan the QR.")
        print()
        print(" If the phone pairs but no video arrives, open")
        print(f"   https://{ip}:8001/docs")
        print(" on the PHONE once and accept that warning too — it treats the")
        print(" two ports as separate sites.")
        print()
    elif args.https:
        print(f"{YELLOW} HTTPS is on, but no LAN address was found, so a phone")
        print(f" cannot reach this laptop. Check the Wi-Fi connection.{RESET}")
        print()
    else:
        print(" Dashboard: http://localhost:3000")
        print(" To use your phone as a camera, restart with:")
        print("   python run_system.py --https")
        print()

    # ── 4. Keep system running until Ctrl + C ────────────────────────────
    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n\n{CYAN}{'='*72}{RESET}")
        print(f"{CYAN} Ctrl+C received — shutting down ...{RESET}")
        print(f"{CYAN}{'='*72}{RESET}")

    finally:
        kill_proc(backend_proc,  "Backend")
        kill_proc(frontend_proc, "Frontend")
        kill_port(8001)
        kill_port(3000)
        print(f"\n{YELLOW}[SYSTEM]{RESET} Both servers stopped. System offline.\n")


if __name__ == "__main__":
    main()
