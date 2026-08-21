#!/usr/bin/env python3
"""
setup.py — One-command project bootstrap for VisionWorks.

What this does:
  1. Creates a Python virtual environment (venv)
  2. Installs all backend Python dependencies
  3. Installs all frontend Node.js dependencies
  4. Copies .env.example → .env files if they don't exist yet
  5. Downloads the required YOLOv8 model weights from Ultralytics
  6. Gives you clear instructions for the next step

Usage:
  python setup.py
"""

import os
import sys
import shutil
import subprocess

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
VENV_DIR = os.path.join(ROOT_DIR, "venv")

CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

def log(msg, color=YELLOW):
    print(f"{color}{msg}{RESET}")

def run(cmd, cwd=None, check=True):
    log(f"  $ {cmd}", CYAN)
    result = subprocess.run(cmd, cwd=cwd, shell=True)
    if check and result.returncode != 0:
        log(f"[ERROR] Command failed: {cmd}", RED)
        sys.exit(1)
    return result

def header(title):
    print(f"\n{CYAN}{'─'*60}{RESET}")
    print(f"{CYAN} {title}{RESET}")
    print(f"{CYAN}{'─'*60}{RESET}")

# ──────────────────────────────────────────────────────────────────────────────

def check_prerequisites():
    header("Checking prerequisites")
    
    # Python
    log(f"  Python: {sys.version}", GREEN)
    if sys.version_info < (3, 10):
        log("[ERROR] Python 3.10+ is required.", RED)
        sys.exit(1)

    # Node.js
    result = subprocess.run("node --version", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log("[ERROR] Node.js is not installed. Download from https://nodejs.org", RED)
        sys.exit(1)
    log(f"  Node.js: {result.stdout.strip()}", GREEN)

    # npm
    result = subprocess.run("npm --version", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log("[ERROR] npm is not installed.", RED)
        sys.exit(1)
    log(f"  npm: {result.stdout.strip()}", GREEN)


def setup_venv():
    header("Setting up Python virtual environment")
    if os.path.exists(VENV_DIR):
        log("  Virtual environment already exists — skipping creation.", GREEN)
    else:
        run(f'"{sys.executable}" -m venv "{VENV_DIR}"')
        log("  Virtual environment created.", GREEN)


def get_venv_python():
    if sys.platform == "win32":
        return os.path.join(VENV_DIR, "Scripts", "python.exe")
    return os.path.join(VENV_DIR, "bin", "python")


def install_backend():
    header("Installing Python backend dependencies")
    venv_python = get_venv_python()
    run(f'"{venv_python}" -m pip install --upgrade pip', check=False)
    run(f'"{venv_python}" -m pip install -r requirements.txt', cwd=BACKEND_DIR)
    log("  Backend dependencies installed.", GREEN)


def install_frontend():
    header("Installing Node.js frontend dependencies")
    run("npm install", cwd=FRONTEND_DIR)
    log("  Frontend dependencies installed.", GREEN)


def copy_env_files():
    header("Setting up environment variable files")
    
    # Backend
    backend_env = os.path.join(BACKEND_DIR, ".env")
    backend_example = os.path.join(BACKEND_DIR, ".env.example")
    if os.path.exists(backend_env):
        log("  backend/.env already exists — skipping.", GREEN)
    else:
        shutil.copy(backend_example, backend_env)
        log("  Created backend/.env from .env.example", YELLOW)
        log("  ⚠  Please edit backend/.env and add your Supabase credentials!", RED)

    # Frontend
    frontend_env = os.path.join(FRONTEND_DIR, ".env.local")
    frontend_example = os.path.join(FRONTEND_DIR, ".env.local.example")
    if os.path.exists(frontend_env):
        log("  frontend/.env.local already exists — skipping.", GREEN)
    else:
        shutil.copy(frontend_example, frontend_env)
        log("  Created frontend/.env.local from .env.local.example", YELLOW)
        log("  ⚠  Please edit frontend/.env.local and add your Supabase credentials!", RED)


def download_models():
    header("Downloading YOLOv8 model weights")
    
    venv_python = get_venv_python()
    
    # Must match the weights the code actually loads: the CV pipeline requests
    # the Medium variants (see app/cv/pose_estimator.py and detector_tracker.py).
    models = [
        ("yolov8m.pt", "Object Detection"),
        ("yolov8m-pose.pt", "Pose Estimation"),
    ]
    
    for model_file, description in models:
        model_path = os.path.join(BACKEND_DIR, model_file)
        if os.path.exists(model_path):
            log(f"  {model_file} ({description}) already exists — skipping.", GREEN)
        else:
            log(f"  Downloading {model_file} ({description})...", YELLOW)
            # Use ultralytics to auto-download
            download_cmd = f'"{venv_python}" -c "from ultralytics import YOLO; YOLO(\\"{model_file}\\")"'
            result = subprocess.run(download_cmd, cwd=BACKEND_DIR, shell=True)
            if result.returncode == 0:
                log(f"  {model_file} downloaded.", GREEN)
            else:
                log(f"  [WARNING] Could not auto-download {model_file}. Download manually from https://github.com/ultralytics/assets/releases", YELLOW)


def create_sample_videos_dir():
    header("Creating sample_videos directory")
    sample_dir = os.path.join(ROOT_DIR, "sample_videos")
    os.makedirs(sample_dir, exist_ok=True)
    readme_path = os.path.join(sample_dir, "README.md")
    if not os.path.exists(readme_path):
        with open(readme_path, "w") as f:
            f.write("# Sample Videos\n\nPlace your `.mp4` test video files here.\n\nThese are excluded from git via `.gitignore`.\n")
    log("  sample_videos/ directory ready.", GREEN)


def print_next_steps():
    header("Setup complete!")
    print(f"""
{GREEN}Everything is set up! Here's what to do next:{RESET}

{YELLOW}Step 1 — Add your Supabase credentials:{RESET}
   Edit {CYAN}backend/.env{RESET}          → add SUPABASE_URL and SUPABASE_KEY
   Edit {CYAN}frontend/.env.local{RESET}   → add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY
   (Get these from: https://supabase.com → Your Project → Settings → API)

{YELLOW}Step 2 — Add a test video (optional):{RESET}
   Drop any .mp4 file into the {CYAN}sample_videos/{RESET} directory.

{YELLOW}Step 3 — Start the system:{RESET}
   {CYAN}python run_system.py{RESET}

   This will start:
     Backend  → http://localhost:8001
     Frontend → http://localhost:3000

{YELLOW}Press Ctrl+C to stop all services.{RESET}
""")

# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{CYAN}{'='*60}{RESET}")
    print(f"{CYAN}  VisionWorks — Project Setup{RESET}")
    print(f"{CYAN}{'='*60}{RESET}\n")

    # Force UTF-8 on Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    check_prerequisites()
    setup_venv()
    install_backend()
    install_frontend()
    copy_env_files()
    download_models()
    create_sample_videos_dir()
    print_next_steps()
