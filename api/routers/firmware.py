# Theo Bauer · ChargeRelay
"""
routers/firmware.py – Firmware upload, hosting and update requests.

Routes
------
GET    /api/firmware/files                         List uploaded firmware files.
POST   /api/firmware/files?filename=&note=         Upload a file (raw request body).
DELETE /api/firmware/files/{id}                    Delete a file.
GET    /api/firmware/download/{token}/{filename}   Download (used by the charger).
POST   /api/firmware/updates                       Queue an UpdateFirmware.
GET    /api/firmware/updates                       Recent update requests.

These endpoints have no authentication, like the rest of the API (private
home-network deployment).  The download URL is protected only by an
unguessable token.

The API never talks OCPP itself: POST /updates only writes a 'queued' row; the
OCPP server picks it up (backend/firmware_dispatcher.py).
"""
import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg2.errors
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from database import db, db_write, row_to_dict
from models import FirmwareUpdateCreate
from routers.stations import DEFAULT_HEARTBEAT_SECONDS, ONLINE_FACTOR, _parse_utc

router = APIRouter(prefix="/api/firmware")

STORAGE_DIR = Path(os.environ.get("FW_STORAGE_DIR", "/data/firmware"))
MAX_UPLOAD_BYTES = int(os.environ.get("FW_MAX_UPLOAD_MB", "100")) * 1024 * 1024
PUBLIC_BASE_URL = os.environ.get("FW_PUBLIC_BASE_URL", "").strip().rstrip("/")

CHUNK_SIZE = 1024 * 1024
MAX_NOTE_LENGTH = 200

# Keep in sync with FW_TERMINAL_STATUSES in backend/charge_point.py.
TERMINAL_STATUSES = ("Installed", "DownloadFailed", "InstallationFailed", "failed")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(name: str) -> str:
    """Reduce an uploaded name to a short, path-free, URL-safe filename."""
    base = os.path.basename(name.replace("\\", "/"))
    base = _SAFE_NAME.sub("_", base).lstrip(".")[:100]
    return base or "firmware.bin"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@router.get("/files")
def list_files():
    """Return uploaded firmware files, newest first (no tokens or URLs)."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, filename, size_bytes, sha256, note, uploaded_at
            FROM firmware_files
            ORDER BY id DESC
        """)
        return [row_to_dict(cur, r) for r in cur.fetchall()]


@router.post("/files", status_code=201)
async def upload_file(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    note: str | None = Query(None, max_length=MAX_NOTE_LENGTH),
):
    """Store the raw request body as a firmware file.

    The body is streamed to disk so the size limit is enforced while data
    arrives, not after the whole upload has been buffered.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    token = secrets.token_urlsafe(24)
    safe_name = _safe_filename(filename)
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    final_path = STORAGE_DIR / f"{token}.bin"
    tmp_path = STORAGE_DIR / f"{token}.part"

    digest = hashlib.sha256()
    size = 0
    try:
        with open(tmp_path, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File too large")
                digest.update(chunk)
                fh.write(chunk)
        if size == 0:
            raise HTTPException(status_code=422, detail="Empty file")
        os.replace(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise

    def _insert() -> dict:
        with db_write() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO firmware_files
                    (token, filename, size_bytes, sha256, note, uploaded_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, filename, size_bytes, sha256, note, uploaded_at
            """, (token, safe_name, size, digest.hexdigest(), (note or None), _now()))
            return row_to_dict(cur, cur.fetchone())

    try:
        return await run_in_threadpool(_insert)
    except Exception:
        final_path.unlink(missing_ok=True)
        raise


@router.delete("/files/{file_id}", status_code=204)
def delete_file(file_id: int):
    """Delete a firmware file and revoke its download URL."""
    with db_write() as conn:
        cur = conn.cursor()
        cur.execute("SELECT token FROM firmware_files WHERE id = %s", (file_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found")
        token = row[0]

        cur.execute(
            "SELECT 1 FROM firmware_updates WHERE file_id = %s AND status NOT IN %s LIMIT 1",
            (file_id, TERMINAL_STATUSES),
        )
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="File is used by a running update")

        cur.execute("DELETE FROM firmware_files WHERE id = %s", (file_id,))
    (STORAGE_DIR / f"{token}.bin").unlink(missing_ok=True)


@router.get("/download/{token}/{filename}")
def download_file(token: str, filename: str):
    """Serve a firmware file.  Called by the charger; the token is the only access control."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT filename FROM firmware_files WHERE token = %s", (token,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    # The path comes from the DB token, never from the URL's filename segment.
    path = STORAGE_DIR / f"{token}.bin"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="application/octet-stream", filename=row[0])


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------

@router.post("/updates", status_code=201)
def start_update(body: FirmwareUpdateCreate, request: Request):
    """Queue an UpdateFirmware for one station (sent by the OCPP server)."""
    if not PUBLIC_BASE_URL.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=503,
            detail="Disabled: set FW_PUBLIC_BASE_URL (a URL the charger can reach)",
        )

    now = datetime.now(timezone.utc)
    try:
        with db_write() as conn:
            cur = conn.cursor()

            cur.execute(
                "SELECT firmware, heartbeat_interval, last_seen FROM charge_points WHERE id = %s",
                (body.charge_point_id,),
            )
            station = cur.fetchone()
            if not station:
                raise HTTPException(status_code=404, detail="Station not found")
            firmware, interval, last_seen = station

            seen = _parse_utc(last_seen)
            max_age = ONLINE_FACTOR * (interval or DEFAULT_HEARTBEAT_SECONDS)
            if seen is None or (now - seen).total_seconds() > max_age:
                raise HTTPException(status_code=409, detail="Station is offline")

            cur.execute(
                "SELECT 1 FROM sessions WHERE charge_point_id = %s AND stop_time IS NULL LIMIT 1",
                (body.charge_point_id,),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Station has an active charging session")

            cur.execute(
                "SELECT token, filename FROM firmware_files WHERE id = %s", (body.file_id,)
            )
            file_row = cur.fetchone()
            if not file_row:
                raise HTTPException(status_code=404, detail="File not found")
            token, filename = file_row

            location = f"{PUBLIC_BASE_URL}/api/firmware/download/{token}/{quote(filename)}"
            peer = request.client.host if request.client else None
            cur.execute("""
                INSERT INTO firmware_updates
                    (charge_point_id, file_id, location, filename, status,
                     from_version, requested_at, requested_from, updated_at)
                VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s)
                RETURNING id, charge_point_id, filename, status, detail,
                          from_version, to_version, requested_at, sent_at, updated_at
            """, (body.charge_point_id, body.file_id, location, filename,
                  firmware, now.isoformat(), peer, now.isoformat()))
            return row_to_dict(cur, cur.fetchone())
    except psycopg2.errors.UniqueViolation:
        # firmware_updates_one_active_idx: an unfinished update already exists.
        raise HTTPException(status_code=409, detail="An update is already running for this station")


@router.get("/updates")
def list_updates(
    charge_point_id: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
):
    """Return recent update requests, newest first (the download URL is not exposed)."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, charge_point_id, filename, status, detail,
                   from_version, to_version, requested_at, sent_at, updated_at
            FROM firmware_updates
            WHERE (%s::text IS NULL OR charge_point_id = %s)
            ORDER BY id DESC
            LIMIT %s
        """, (charge_point_id, charge_point_id, limit))
        return [row_to_dict(cur, r) for r in cur.fetchall()]
