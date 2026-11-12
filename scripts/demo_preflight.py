"""
Demo preflight: one command that says whether the machine is ready to run the storyline in docs/DEMO_RUNBOOK.md.

    venv/Scripts/python.exe scripts/demo_preflight.py

Checks the model files, the settings, the GPU, that both servers answer, that evidence pictures can be written, and which
fallback clips are on disk. Prints OK / WARN / FAIL per line and exits non-zero on any FAIL. Changes nothing.
"""
import glob
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

FAILS = []


def line(level, text):
    print(f"  [{level:4}] {text}")
    if level == "FAIL":
        FAILS.append(text)


def env_value(path, name):
    try:
        for raw in open(path, encoding="utf-8"):
            raw = raw.strip()
            if raw.startswith(name + "="):
                return raw.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


print("Models")
pose = [p for p in (os.path.join(BACKEND, "yolov8m-pose.pt"), os.path.join(ROOT, "yolov8m-pose.pt")) if os.path.exists(p)]
line("OK" if pose else "FAIL", "people / pose detector (yolov8m-pose.pt)" + ("" if pose else " is missing: run setup.py"))
ppe = os.environ.get("PPE_MODEL_PATH") or os.path.join(ROOT, "ppe_training", "runs", "ppe_yolov8m_ft1", "weights", "best.pt")
line("OK" if os.path.exists(ppe) else "FAIL", f"PPE detector ({os.path.relpath(ppe, ROOT)})" + ("" if os.path.exists(ppe) else " is missing"))
try:
    import torch
    line("OK" if torch.cuda.is_available() else "WARN",
         f"GPU: {torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else "no GPU found: it will run on the CPU, slower than real time")
except Exception as e:                                               # noqa: BLE001
    line("WARN", f"could not check the GPU ({type(e).__name__})")

print("Settings")
envfile = os.path.join(BACKEND, ".env")
safety = (env_value(envfile, "SAFETY_ENABLED") or os.environ.get("SAFETY_ENABLED") or "").lower()
line("OK" if safety in ("1", "true", "yes", "on") else "FAIL", "SAFETY_ENABLED is on" if safety in ("1", "true", "yes", "on") else "SAFETY_ENABLED is off in backend/.env: the rules will not run")
line("OK" if env_value(envfile, "SUPABASE_SERVICE_ROLE_KEY") else "WARN", "service-role key set (decisions reach the dashboard)" if env_value(envfile, "SUPABASE_SERVICE_ROLE_KEY") else "no service-role key: decisions stay in the local file and never reach the dashboard")
cert = os.path.join(ROOT, "certs", "dev-cert.pem")
line("OK" if os.path.exists(cert) else "WARN", "dev certificate present (needed to pair a phone)" if os.path.exists(cert) else "no dev certificate: run `python run_system.py --https` once to create it")

print("Servers")
try:
    import requests
    import urllib3
    urllib3.disable_warnings()
    for name, urls in (("backend", ("https://localhost:8001/", "http://localhost:8001/")), ("frontend", ("https://localhost:3000/", "http://localhost:3000/"))):
        up = None
        for url in urls:
            try:
                r = requests.get(url, timeout=4, verify=False)
                if r.status_code < 500:
                    up = url
                    break
            except Exception:                                        # noqa: BLE001
                continue
        line("OK" if up else "FAIL", f"{name} answers at {up}" if up else f"{name} is not running: start it with python run_system.py (--https for a phone)")
except ImportError:
    line("WARN", "requests is not installed in this Python; skipped the server check")

print("Evidence pictures")
try:
    from app.safety.snapshots import evidence_dir
    d = evidence_dir()
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=d, delete=True):
        pass
    line("OK", f"evidence folder is writable ({d})")
except Exception as e:                                               # noqa: BLE001
    line("FAIL", f"evidence folder cannot be written: {type(e).__name__}: {e}")

print("Fallback clips (sample_videos/demo_fallback/)")
SCENES = ["compliant", "no_helmet", "unverifiable", "dwell", "lone_worker", "man_down"]
folder = os.path.join(ROOT, "sample_videos", "demo_fallback")
have = [os.path.basename(p) for p in glob.glob(os.path.join(folder, "*.mp4"))]
for scene in SCENES:
    found = [h for h in have if h.lower().startswith(scene)]
    line("OK" if found else "WARN", f"{scene}: {found[0]}" if found else f"{scene}: no clip yet (record one, name it {scene}.mp4)")

print()
if FAILS:
    print(f"NOT READY: {len(FAILS)} problem(s)")
    sys.exit(1)
print("READY (warnings are things to look at, not blockers)")
