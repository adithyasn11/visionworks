# d:\major project\run_system.py
"""
Unified System Launcher — Workplace Activity Analytics
Starts FastAPI (port 8001) and Next.js (port 3000).
Keeps running until Ctrl+C. Kills children on exit.
"""

import os
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
    """Performs a light HTTP GET request to verify server is answering."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    CYAN   = "\033[96m"
    BLUE   = "\033[94m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RESET  = "\033[0m"

    print(f"{CYAN}{'='*72}{RESET}")
    print(f"{CYAN} >>> VISION-BASED WORKPLACE ACTIVITY ANALYTICS — LAUNCHER <<<{RESET}")
    print(f"{CYAN}{'='*72}{RESET}\n")

    # ── 1. Free ports ──────────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Freeing ports 8001 and 3000 ...")
    kill_port(8001)
    kill_port(3000)
    time.sleep(1.0)

    # ── 2. Start Backend ───────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Starting FastAPI backend  -> http://localhost:8001")

    win_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    backend_proc = subprocess.Popen(
        [VENV_PYTHON, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8001", "--log-level", "info"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        creationflags=win_flags,
    )
    threading.Thread(target=stream, args=(backend_proc, "BACKEND ", BLUE), daemon=True).start()

    print(f"{YELLOW}[SYSTEM]{RESET} Waiting for backend to bind port 8001 ...")
    if not wait_port_up("http://localhost:8001/", timeout=45):
        print("[ERROR] Backend did not start within 45 s. Check logs above.")
        kill_proc(backend_proc, "Backend")
        sys.exit(1)
    print(f"{YELLOW}[SYSTEM]{RESET} Backend is UP ✓")

    # ── 3. Start Frontend ──────────────────────────────────────────────────
    print(f"{YELLOW}[SYSTEM]{RESET} Starting Next.js frontend -> http://localhost:3000")

    frontend_proc = subprocess.Popen(
        "npx next dev -p 3000",
        shell=True,
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        creationflags=win_flags,
    )
    threading.Thread(target=stream, args=(frontend_proc, "FRONTEND", GREEN), daemon=True).start()

    print(f"{YELLOW}[SYSTEM]{RESET} Waiting for frontend to bind port 3000 ...")
    if not wait_port_up("http://localhost:3000/", timeout=60):
        print("[ERROR] Frontend did not start within 60 s. Check logs above.")
        kill_proc(backend_proc,  "Backend")
        kill_proc(frontend_proc, "Frontend")
        sys.exit(1)
    print(f"{YELLOW}[SYSTEM]{RESET} Frontend is UP ✓")

    print(f"\n{CYAN}{'='*72}{RESET}")
    print(f"{CYAN} SYSTEM ONLINE — Press Ctrl + C to shut down everything{RESET}")
    print(f"{CYAN}{'='*72}{RESET}\n")

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
