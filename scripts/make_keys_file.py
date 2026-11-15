"""
Write visionworks-keys.env from this computer's backend/.env and frontend/.env.local, to send PRIVATELY to someone
setting VisionWorks up on another computer (WhatsApp, email, a USB stick: never git, never a public place).

    venv\Scripts\python scripts\make_keys_file.py

It holds three values: the Supabase project address, its public (anon) key, and its service-role key. The
service-role key can read and change everything in the database, so only give it to people you trust with that.
The database password (DATABASE_URL) is NOT included: running the app does not need it.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


backend = read(os.path.join(ROOT, "backend", ".env"))
frontend = read(os.path.join(ROOT, "frontend", ".env.local"))
values = {
    "SUPABASE_URL": frontend.get("NEXT_PUBLIC_SUPABASE_URL") or backend.get("SUPABASE_URL"),
    "SUPABASE_ANON_KEY": frontend.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or backend.get("SUPABASE_KEY"),
    "SUPABASE_SERVICE_ROLE_KEY": backend.get("SUPABASE_SERVICE_ROLE_KEY") or frontend.get("SUPABASE_SERVICE_ROLE_KEY"),
}
missing = [k for k, v in values.items() if not v]
if missing:
    sys.exit(f"Could not find {', '.join(missing)} in backend/.env or frontend/.env.local")

dest = os.path.join(ROOT, "visionworks-keys.env")
with open(dest, "w", encoding="utf-8") as f:
    f.write("# VisionWorks keys. PRIVATE: do not post or commit this file.\n"
            "# Put it in the VisionWorks folder (next to start_visionworks.bat), then run start_visionworks.bat.\n")
    for k, v in values.items():
        f.write(f"{k}={v}\n")
print(f"Wrote {dest}")
