"""
Marking a camera as a DOOR, which is what turns face matching on.

Face matching runs only on a DOOR camera, and until now nothing could set
that: POST does not accept a role and there was no PATCH. So an enrolled
face could never be recognised anywhere. These checks cover the new endpoint
and, more importantly, that the pipeline actually reads the change back.
"""
import json, os, ssl, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.abspath("backend"))
from dotenv import load_dotenv
load_dotenv("backend/.env")

CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
FAILS=[]
def check(n, ok, d=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {n}" + (f": {d}" if d else ""))
    if not ok: FAILS.append(n)

def discover():
    for sch in ("https","http"):
        try:
            urllib.request.urlopen(f"{sch}://localhost:8001/", timeout=6,
                                   context=CTX if sch=="https" else None)
            return sch
        except urllib.error.HTTPError: return sch
        except Exception: continue
    return None

PROBE="camrole-probe@visionworks.test"; PW="Pr0be-CamRole-2026-Delete-Me"
ORG="0fedf5a4-8994-4f37-ac21-e3b2dad32639"; _pid=None

def _admin(path, method="GET", body=None, base=None):
    u=os.getenv("SUPABASE_URL","").rstrip("/"); k=os.getenv("SUPABASE_SERVICE_ROLE_KEY","")
    r=urllib.request.Request(f"{u}/{base or 'rest/v1'}/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"apikey":k,"Authorization":f"Bearer {k}",
                 "Content-Type":"application/json","Prefer":"return=representation"},
        method=method)
    try:
        with urllib.request.urlopen(r,timeout=25) as x:
            raw=x.read().decode(); return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        return {"__error":e.code,"body":e.read().decode()[:200]}

def cleanup():
    global _pid
    if not _pid:
        us=_admin("admin/users?page=1&per_page=200",base="auth/v1")
        f=[u for u in (us.get("users",[]) if isinstance(us,dict) else []) if u.get("email")==PROBE]
        _pid=f[0]["id"] if f else None
    if not _pid:
        _admin(f"profiles?email=eq.{PROBE}","DELETE"); return
    _admin(f"memberships?profileId=eq.{_pid}","DELETE")
    _admin(f"admin/users/{_pid}","DELETE",base="auth/v1")
    _admin(f"profiles?id=eq.{_pid}","DELETE")
    _admin(f"profiles?email=eq.{PROBE}","DELETE")
    _pid=None

def token(role="ADMIN"):
    global _pid
    cleanup()
    m=_admin("admin/users","POST",{"email":PROBE,"password":PW,"email_confirm":True},base="auth/v1")
    if isinstance(m,dict) and m.get("__error"): return None
    _pid=m["id"]
    _admin("profiles","POST",[{"id":_pid,"email":PROBE,"fullName":"CamRole Probe",
                               "updatedAt":"2026-08-30T00:00:00Z"}])
    _admin("memberships","POST",[{"orgId":ORG,"profileId":_pid,"role":role,"status":"ACTIVE",
        "invitedEmail":PROBE,"acceptedAt":"2026-08-30T00:00:00Z",
        "updatedAt":"2026-08-30T00:00:00Z"}])
    u=os.getenv("SUPABASE_URL","").rstrip("/"); a=os.getenv("SUPABASE_KEY","")
    r=urllib.request.Request(f"{u}/auth/v1/token?grant_type=password",
        data=json.dumps({"email":PROBE,"password":PW}).encode(),
        headers={"apikey":a,"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(r,timeout=20) as x:
            return json.loads(x.read().decode())["access_token"]
    except Exception: return None

def call(base, path, method="GET", body=None, tok=None):
    r=urllib.request.Request(base+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type":"application/json",
                 **({"Authorization":f"Bearer {tok}"} if tok else {})},
        method=method)
    try:
        with urllib.request.urlopen(r,timeout=30,context=CTX) as x:
            raw=x.read().decode(); return x.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw[:200]

def main():
    print("="*68); print("CAMERA ROLE: TURNING FACE MATCHING ON"); print("="*68); print()
    sch=discover()
    if not sch: print("  backend not running"); return 1
    BASE=f"{sch}://localhost:8001/api/v1/cameras"
    tok=token()
    if not tok: print("  could not mint a probe session"); return 2

    print("1. THE ENDPOINT IS GUARDED")
    print("-"*68)
    st,_=call(BASE,"/floor5","PATCH",{"role":"DOOR"})
    check("an unauthenticated caller cannot change a role", st in (401,403), f"HTTP {st}")

    print()
    print("2. A BAD ROLE IS REFUSED")
    print("-"*68)
    st,b=call(BASE,"/floor5","PATCH",{"role":"FRONTDOOR"},tok)
    check("an invalid role is rejected", st==422, f"HTTP {st}")
    st,b=call(BASE,"/floor5","PATCH",{"inference_width":10},tok)
    check("an absurd inference width is rejected", st==422, f"HTTP {st}")

    print()
    print("3. A CAMERA THAT IS NOT YOURS IS A 404")
    print("-"*68)
    st,_=call(BASE,"/definitely-not-a-real-camera","PATCH",{"role":"DOOR"},tok)
    check("unknown id is 404, not 500", st==404, f"HTTP {st}")

    print()
    print("4. MARKING floor5 AS A DOOR")
    print("-"*68)
    st,b=call(BASE,"/floor5","PATCH",{"role":"DOOR"},tok)
    check("the change is accepted", st==200, f"HTTP {st}")
    if isinstance(b,dict):
        check("...and reports role DOOR", b.get("role")=="DOOR", str(b.get("role")))
        check("...and raises inference width to 1280 for faces",
              b.get("inference_width")==1280, str(b.get("inference_width")))

    print()
    print("5. THE PIPELINE READS THE CHANGE BACK")
    print("-"*68)
    # This is the check that matters: the endpoint writing a column is
    # worthless if resolve_camera_role() does not see it.
    from app.cv.face_identifier import resolve_camera_role
    role, width = resolve_camera_role("floor5", ORG)
    check("resolve_camera_role now returns DOOR", role=="DOOR", f"{role}")
    check("...at face-preserving width", width==1280, str(width))

    print()
    print("6. IT CAN BE TURNED BACK OFF")
    print("-"*68)
    st,b=call(BASE,"/floor5","PATCH",{"role":"AREA"},tok)
    check("back to AREA", st==200 and b.get("role")=="AREA", f"HTTP {st}")
    role,width = resolve_camera_role("floor5", ORG)
    check("...and the pipeline agrees", role=="AREA" and width==640, f"{role}/{width}")

    # leave it as DOOR: that is what the user needs
    call(BASE,"/floor5","PATCH",{"role":"DOOR"},tok)
    role,_=resolve_camera_role("floor5", ORG)
    print(f"\n  floor5 left as: {role}")

    print(); print("="*68)
    if FAILS: print(f"FAILED ({len(FAILS)}): "+", ".join(FAILS)); return 1
    print("CAMERA ROLE: ALL CHECKS PASSED"); return 0

try:
    rc=main()
finally:
    try: cleanup(); print("  (probe account removed)")
    except Exception as e: print("  cleanup warning:",e)
sys.exit(rc)
