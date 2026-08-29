# backend/app/api/routers/enrolment.py
"""
Face enrolment endpoints.

Step 9 of IDENTITY_TRACKING_PLAN.md.

WHY THIS LIVES ON THE PYTHON BACKEND RATHER THAN IN A SERVER ACTION

Two reasons, and both are structural:

1. The face models are here. buffalo_l is onnxruntime, ~300 MB, and already
   sitting in the same process as the rest of the CV pipeline. Shipping face
   detection to the Next.js side would mean a second copy of the models and a
   second thing to keep in step with the matching in Step 10.

2. Only the service role can write a usable template. Migration 020 revokes
   SELECT on `face_templates.embedding` from `authenticated` entirely — a
   browser can INSERT a vector but can never read one back. The write itself
   is fine from either side; doing it here keeps the vector from ever being
   serialised through a browser at all.

WHAT CROSSES THE WIRE

Up: the photo, once, as multipart. Down: a quality verdict and the row id.
The image is never written to disk and the embedding is never returned — the
UI has no legitimate use for either, and anything the API can return is
something an attacker with a session can ask for.
"""

import base64
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, Form

from app.api.deps import resolve_org_role
from app.api.permissions import can, denial_message

logger = logging.getLogger(__name__)
router = APIRouter()

# An enrolment photo has no business being larger than this. A phone photo is
# 3-6 MB; the cap stops a a 200 MB upload occupying the process.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


def require_employees_edit(authorization: Optional[str] = Header(default=None)) -> str:
    """
    The caller must hold `employees.edit` — ADMIN or MANAGER.

    Enrolling a face is the most consequential write in the product: it is the
    act that makes a named person recognisable by camera. It is gated on the
    same capability as editing the roster, which is what `employee_insert` and
    `soft_delete_employee()` already enforce in Postgres.

    401 with no verified organisation, 403 with one but a read-only role.
    """
    org_id, role = resolve_org_role(authorization)
    if org_id is None:
        raise HTTPException(
            status_code=401,
            detail="Sign in to an organisation before enrolling a face.")
    if not can(role, "employees.edit"):
        raise HTTPException(status_code=403, detail=denial_message("employees.edit"))
    return org_id


def _supabase():
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base or not key or key.startswith("your-"):
        raise HTTPException(
            status_code=503,
            detail="Face enrolment needs SUPABASE_SERVICE_ROLE_KEY configured on "
                   "the backend. See backend/.env.example.")
    return base, key


def _rest(method: str, path: str, key: str, base: str, payload=None, extra_headers=None):
    import json
    import urllib.request
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(f"{base}/rest/v1/{path}", data=data,
                                     headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else []


def _employee_in_org(base, key, employee_id: str, org_id: str):
    """
    The employee, or None if they do not belong to this organisation.

    Checked HERE rather than trusted from the request: the endpoint runs with
    the service role, which bypasses RLS, so the tenant boundary that RLS
    would normally enforce has to be enforced explicitly. Without this, a
    manager of one organisation could enrol a face against another
    organisation's employee by guessing a UUID.
    """
    import urllib.parse
    rows = _rest("GET", "employees?" + urllib.parse.urlencode({
        "select": "id,displayName,orgId,deletedAt",
        "id": f"eq.{employee_id}",
        "limit": "1",
    }), key, base)
    if not rows:
        return None
    row = rows[0]
    if row.get("orgId") != org_id or row.get("deletedAt"):
        return None
    return row


def _existing_embeddings(base, key, employee_id: str):
    """This employee's stored vectors, for the consistency check."""
    import urllib.parse
    rows = _rest("GET", "face_templates?" + urllib.parse.urlencode({
        "select": "id,embedding,quality,createdAt",
        "employeeId": f"eq.{employee_id}",
        "order": "createdAt.asc",
    }), key, base)
    return rows


@router.get("/{employee_id}/templates")
def list_templates(employee_id: str, org_id: str = Depends(require_employees_edit)):
    """
    What is enrolled for this employee — counts and quality, never vectors.

    The UI needs to show "3 of 5 photos, quality 0.91". It has no use for the
    embedding itself, so it does not get one: the API cannot return what it
    does not send, and that is the cheapest possible protection for the most
    sensitive column in the schema.
    """
    base, key = _supabase()
    if not _employee_in_org(base, key, employee_id, org_id):
        raise HTTPException(status_code=404, detail="Employee not found.")

    rows = _existing_embeddings(base, key, employee_id)
    from app.cv.face_enroller import MIN_TEMPLATES, MAX_TEMPLATES
    return {
        "employee_id": employee_id,
        "count": len(rows),
        "min_templates": MIN_TEMPLATES,
        "max_templates": MAX_TEMPLATES,
        "enrolled": len(rows) >= MIN_TEMPLATES,
        "templates": [
            {"id": r["id"], "quality": r["quality"], "created_at": r["createdAt"]}
            for r in rows
        ],
    }


@router.post("/{employee_id}/templates")
async def enrol_face(
    employee_id: str,
    file: UploadFile = File(default=None),
    image_base64: str = Form(default=None),
    org_id: str = Depends(require_employees_edit),
):
    """
    Grade one enrolment photo and, if it passes, store its embedding.

    Accepts either a file upload or a base64 data URL, because the UI offers
    both a file picker and a webcam capture and the second has no file to send.

    A rejected photo returns 422 with a message that says what to change. That
    is the plan's "reject at capture time, with a clear message" — a marginal
    photo accepted quietly would make this person unmatchable for the whole
    deployment, and it would look like a model problem rather than a data one.
    """
    from app.cv.face_enroller import (
        FaceQualityError, assess_face, check_against_existing, decode_image,
        MAX_TEMPLATES,
    )

    base, key = _supabase()
    employee = _employee_in_org(base, key, employee_id, org_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # ── Read the image ──────────────────────────────────────────────────────
    if file is not None:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"That image is {len(data) / 1e6:.1f} MB. "
                       f"Keep enrolment photos under {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    elif image_base64:
        raw = image_base64.split(",", 1)[-1]
        try:
            data = base64.b64decode(raw)
        except Exception:
            raise HTTPException(status_code=400, detail="That capture could not be decoded.")
    else:
        raise HTTPException(status_code=400, detail="No image was supplied.")

    image = decode_image(data)
    # `data` is not referenced again. The photo exists only as this in-memory
    # array, and it is discarded when the request returns.

    existing = _existing_embeddings(base, key, employee_id)
    if len(existing) >= MAX_TEMPLATES:
        raise HTTPException(
            status_code=409,
            detail=f"{employee.get('displayName') or 'This employee'} already has "
                   f"{len(existing)} photos, the maximum. Remove one first.")

    # ── Grade it ────────────────────────────────────────────────────────────
    try:
        result = assess_face(image)
        check_against_existing(
            result["embedding"],
            [e["embedding"] for e in existing if e.get("embedding")],
        )
    except FaceQualityError as e:
        # 422: the request was well-formed, the photo was not good enough.
        raise HTTPException(status_code=422, detail={
            "code": e.code, "message": e.message, "measurements": e.detail,
        })
    except Exception as e:
        logger.error(f"face enrolment failed for {employee_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="The face could not be processed. Try a different photo.")

    # ── Store the VECTOR, never the image ───────────────────────────────────
    try:
        rows = _rest("POST", "face_templates", key, base, payload=[{
            "employeeId": employee_id,
            "embedding": [float(x) for x in result["embedding"]],
            "quality": result["quality"],
        }], extra_headers={"Prefer": "return=representation"})
    except Exception as e:
        logger.error(f"could not store face template for {employee_id}: {e}")
        raise HTTPException(status_code=502, detail="Could not save the template.")

    template_id = rows[0]["id"] if rows else None
    total = len(existing) + 1
    logger.info(
        f"enrolled a face template for employee {employee_id} "
        f"(quality {result['quality']:.3f}, {total} total)")

    return {
        "ok": True,
        "template_id": template_id,
        "quality": result["quality"],
        "measurements": result["measurements"],
        "count": total,
        "max_templates": MAX_TEMPLATES,
    }


@router.delete("/{employee_id}/templates/{template_id}")
def delete_template(employee_id: str, template_id: str,
                    org_id: str = Depends(require_employees_edit)):
    """
    Remove one template.

    Reachable because it is how somebody withdraws consent to be recognised,
    and how a bad enrolment gets corrected. Scoped to the employee AND the
    organisation, so a guessed template id cannot delete another tenant's data.
    """
    import urllib.parse
    base, key = _supabase()
    if not _employee_in_org(base, key, employee_id, org_id):
        raise HTTPException(status_code=404, detail="Employee not found.")

    _rest("DELETE", "face_templates?" + urllib.parse.urlencode({
        "id": f"eq.{template_id}",
        "employeeId": f"eq.{employee_id}",
    }), key, base)
    return {"ok": True, "template_id": template_id}
