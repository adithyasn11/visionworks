#!/usr/bin/env python3
"""
setup.py — prepares a fresh copy of VisionWorks so start_visionworks.bat can run it.

start_visionworks.bat runs this by itself the first time, so normally nobody calls it by hand.
It can be re-run safely at any time (every step skips what is already done):

    python setup.py

What it does:
  1. checks Python (3.10 to 3.13) and Node.js (18 or newer)
  2. creates the Python environment in venv/
  3. installs PyTorch (the NVIDIA GPU build when an NVIDIA card is present, otherwise the CPU build),
     then the backend's packages
  4. installs the dashboard's packages (npm install)
  5. writes backend/.env and frontend/.env.local from visionworks-keys.env
     (the keys file the project owner sends you privately; it is never in git)
  6. downloads the two public YOLOv8 models (people detection and pose)

The trained helmet/vest model is already in the repository (backend/models/ppe_yolov8m_ft1.pt).
"""

import os
import shutil
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
VENV_DIR = os.path.join(ROOT_DIR, "venv")
KEYS_FILE = os.path.join(ROOT_DIR, "visionworks-keys.env")
DONE_MARKER = os.path.join(VENV_DIR, ".visionworks-setup-done")

CYAN, GREEN, YELLOW, RED, RESET = "\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"


def log(msg, color=YELLOW):
    print(f"{color}{msg}{RESET}", flush=True)


def fail(msg):
    log(f"\n[ERROR] {msg}", RED)
    sys.exit(1)


def header(title):
    print(f"\n{CYAN}{'-' * 60}\n {title}\n{'-' * 60}{RESET}", flush=True)


def run(cmd, cwd=None):
    log(f"  $ {cmd}", CYAN)
    if subprocess.run(cmd, cwd=cwd, shell=True).returncode != 0:
        fail(f"This step failed: {cmd}\n        Check your internet connection and run start_visionworks.bat again; finished steps are skipped.")


def venv_python():
    return os.path.join(VENV_DIR, "Scripts", "python.exe") if sys.platform == "win32" else os.path.join(VENV_DIR, "bin", "python")


# ── 1. prerequisites ───────────────────────────────────────────────────────────

def check_prerequisites():
    header("1/6  Checking Python and Node.js")
    v = sys.version_info
    log(f"  Python {v.major}.{v.minor}.{v.micro}", GREEN)
    if not ((3, 10) <= (v.major, v.minor) <= (3, 13)):
        fail("VisionWorks needs Python 3.10, 3.11, 3.12 or 3.13 (3.11 recommended).\n"
             "        Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH'.")
    r = subprocess.run("node --version", shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        fail("Node.js is not installed. Install the LTS version from https://nodejs.org, then run this again.")
    node = r.stdout.strip()
    log(f"  Node.js {node}", GREEN)
    try:
        if int(node.lstrip("v").split(".")[0]) < 18:
            fail("Node.js 18 or newer is needed. Install the LTS version from https://nodejs.org.")
    except ValueError:
        pass


# ── 2. Python environment ──────────────────────────────────────────────────────

def setup_venv():
    header("2/6  Python environment (venv)")
    if os.path.exists(venv_python()):
        log("  Already there.", GREEN)
        return
    run(f'"{sys.executable}" -m venv "{VENV_DIR}"')


# ── 3. backend packages ────────────────────────────────────────────────────────

def has_nvidia_gpu():
    return shutil.which("nvidia-smi") is not None and subprocess.run(
        "nvidia-smi", shell=True, capture_output=True).returncode == 0


def install_backend():
    header("3/6  Backend packages (PyTorch, YOLO, FastAPI) - the first time this downloads a few GB")
    py = venv_python()
    subprocess.run(f'"{py}" -m pip install --upgrade pip', shell=True)
    have_torch = subprocess.run(f'"{py}" -c "import torch, torchvision"', shell=True, capture_output=True).returncode == 0
    if have_torch:
        log("  PyTorch already installed.", GREEN)
    elif has_nvidia_gpu():
        log("  NVIDIA graphics card found: installing the GPU build of PyTorch (fast detection).", GREEN)
        run(f'"{py}" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121')
    else:
        log("  No NVIDIA graphics card: installing the CPU build of PyTorch. Everything works, video analysis is slower.", YELLOW)
        run(f'"{py}" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu')
    run(f'"{py}" -m pip install -r requirements.txt', cwd=BACKEND_DIR)


# ── 4. dashboard packages ──────────────────────────────────────────────────────

def install_frontend():
    header("4/6  Dashboard packages (npm install)")
    if os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules", "next")):
        log("  Already installed.", GREEN)
        return
    run("npm install", cwd=FRONTEND_DIR)


# ── 5. keys ────────────────────────────────────────────────────────────────────

def read_keys(path):
    keys = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip().strip('"').strip("'")
    return keys


def write_env_files():
    header("5/6  Supabase keys")
    backend_env = os.path.join(BACKEND_DIR, ".env")
    frontend_env = os.path.join(FRONTEND_DIR, ".env.local")
    if os.path.exists(backend_env) and os.path.exists(frontend_env):
        log("  backend/.env and frontend/.env.local already exist.", GREEN)
        return
    if not os.path.exists(KEYS_FILE):
        fail("The keys file is missing.\n"
             f"        Put visionworks-keys.env (sent to you privately by the project owner) in:\n        {ROOT_DIR}\n"
             "        then run start_visionworks.bat again.")
    keys = read_keys(KEYS_FILE)
    need = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    missing = [k for k in need if not keys.get(k)]
    if missing:
        fail(f"visionworks-keys.env is missing: {', '.join(missing)}")
    url, anon, service = (keys[k] for k in need)
    if not os.path.exists(backend_env):
        with open(backend_env, "w", encoding="utf-8") as f:
            f.write("# Written by setup.py from visionworks-keys.env. Never commit this file.\n"
                    f"SUPABASE_URL={url}\nSUPABASE_KEY={anon}\nSUPABASE_SERVICE_ROLE_KEY={service}\n"
                    "SAFETY_ENABLED=true\n")
        log("  Wrote backend/.env", GREEN)
    if not os.path.exists(frontend_env):
        with open(frontend_env, "w", encoding="utf-8") as f:
            f.write("# Written by setup.py from visionworks-keys.env. Never commit this file.\n"
                    f"NEXT_PUBLIC_SUPABASE_URL={url}\nNEXT_PUBLIC_SUPABASE_ANON_KEY={anon}\n"
                    f"SUPABASE_SERVICE_ROLE_KEY={service}\n")
        log("  Wrote frontend/.env.local", GREEN)


# ── 6. models ──────────────────────────────────────────────────────────────────

def download_models():
    header("6/6  Detection models")
    py = venv_python()
    for name in ("yolov8m.pt", "yolov8m-pose.pt"):
        if os.path.exists(os.path.join(BACKEND_DIR, name)):
            log(f"  {name} already there.", GREEN)
            continue
        log(f"  Downloading {name}...", YELLOW)
        run(f'"{py}" -c "from ultralytics import YOLO; YOLO(\'{name}\')"', cwd=BACKEND_DIR)
    ppe = os.path.join(BACKEND_DIR, "models", "ppe_yolov8m_ft1.pt")
    if not os.path.exists(ppe) or os.path.getsize(ppe) < 1_000_000:
        fail("backend/models/ppe_yolov8m_ft1.pt (the helmet/vest model) is missing or incomplete. Run: git pull")
    log("  Helmet/vest model present.", GREEN)
    os.makedirs(os.path.join(ROOT_DIR, "sample_videos"), exist_ok=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        os.system("")  # turn on colour codes in the Windows console
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(f"\n{CYAN}{'=' * 60}\n  VisionWorks - first-time setup\n{'=' * 60}{RESET}")
    check_prerequisites()
    setup_venv()
    install_backend()
    install_frontend()
    write_env_files()
    download_models()
    with open(DONE_MARKER, "w") as f:
        f.write("ok\n")
    header("Setup complete")
    log("  start_visionworks.bat will now start VisionWorks and open http://localhost:3000", GREEN)
