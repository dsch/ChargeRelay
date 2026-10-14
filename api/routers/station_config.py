# Theo Bauer · ChargeRelay
"""
routers/station_config.py – Station configuration, reset and trigger commands.

Routes
------
GET  /api/stations/{id}/config                 Cached configuration keys (works offline).
POST /api/stations/{id}/commands               Queue GetConfiguration / ChangeConfiguration /
                                               Reset / TriggerMessage.
GET  /api/stations/{id}/commands               Recent commands.
GET  /api/stations/{id}/commands/{command_id}  One command (poll until it is finished).

These endpoints have no authentication, like the rest of the API (private
home-network deployment).  The API never talks OCPP itself: POST only writes a
'queued' row that the OCPP server sends (backend/command_dispatcher.py).
Values of secret-looking keys are never stored, so they cannot be served.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from database import db, db_write, row_to_dict
from models import StationCommandCreate
from routers.stations import DEFAULT_HEARTBEAT_SECONDS, ONLINE_FACTOR, _parse_utc

router = APIRouter(prefix="/api/stations")

# Refuse new commands while this many are unfinished for a station.
MAX_PENDING = 10

# Keep in sync with backend/charge_point.py (FW_TERMINAL_STATUSES).
_FW_TERMINAL = ("Installed", "DownloadFailed", "InstallationFailed", "failed")


def _command_row(row: dict) -> dict:
    """Decode the JSON columns of a station_commands row for the response."""
    row["params"] = json.loads(row["params"]) if row["params"] else None
    row["result"] = json.loads(row["result"]) if row["result"] else None
    return row


# ---------------------------------------------------------------------------
# Cached configuration
# ---------------------------------------------------------------------------

@router.get("/{station_id}/config")
def get_config(station_id: str):
    """Return the last fetched configuration of a station."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT config_fetched_at FROM charge_points WHERE id = %s", (station_id,))
        station = cur.fetchone()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        cur.execute("""
            SELECT key, value, readonly, masked
            FROM station_config
            WHERE charge_point_id = %s
            ORDER BY key
        """, (station_id,))
        items = [row_to_dict(cur, r) for r in cur.fetchall()]
    return {"fetched_at": station[0], "items": items}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@router.post("/{station_id}/commands", status_code=201)
def queue_command(station_id: str, body: StationCommandCreate, request: Request):
    """Queue a command for a station (sent by the OCPP server)."""
    try:
        params = body.params()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    now = datetime.now(timezone.utc)
    with db_write() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT heartbeat_interval, last_seen FROM charge_points WHERE id = %s",
            (station_id,),
        )
        station = cur.fetchone()
        if not station:
            raise HTTPException(status_code=404, detail="Station not found")
        interval, last_seen = station

        seen = _parse_utc(last_seen)
        max_age = ONLINE_FACTOR * (interval or DEFAULT_HEARTBEAT_SECONDS)
        if seen is None or (now - seen).total_seconds() > max_age:
            raise HTTPException(status_code=409, detail="Station is offline")

        if body.command == "ChangeConfiguration":
            cur.execute(
                "SELECT readonly FROM station_config WHERE charge_point_id = %s AND key = %s",
                (station_id, params["key"]),
            )
            known = cur.fetchone()
            if known and known[0]:
                raise HTTPException(status_code=409, detail="Key is read-only")

        if body.command == "Reset":
            cur.execute(
                "SELECT 1 FROM sessions WHERE charge_point_id = %s AND stop_time IS NULL LIMIT 1",
                (station_id,),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Station has an active charging session")
            cur.execute(
                "SELECT 1 FROM firmware_updates WHERE charge_point_id = %s AND status NOT IN %s LIMIT 1",
                (station_id, _FW_TERMINAL),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="A firmware update is running")

        cur.execute(
            "SELECT COUNT(*) FROM station_commands "
            "WHERE charge_point_id = %s AND status IN ('queued', 'sending')",
            (station_id,),
        )
        if cur.fetchone()[0] >= MAX_PENDING:
            raise HTTPException(status_code=429, detail="Too many pending commands for this station")

        peer = request.client.host if request.client else None
        cur.execute("""
            INSERT INTO station_commands
                (charge_point_id, command, params, status, requested_at, requested_from, updated_at)
            VALUES (%s, %s, %s, 'queued', %s, %s, %s)
            RETURNING id, charge_point_id, command, params, status, result, detail,
                      requested_at, updated_at
        """, (station_id, body.command, json.dumps(params) if params else None,
              now.isoformat(), peer, now.isoformat()))
        return _command_row(row_to_dict(cur, cur.fetchone()))


@router.get("/{station_id}/commands")
def list_commands(station_id: str, limit: int = Query(20, ge=1, le=100)):
    """Return recent commands of a station, newest first."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, charge_point_id, command, params, status, result, detail,
                   requested_at, updated_at
            FROM station_commands
            WHERE charge_point_id = %s
            ORDER BY id DESC
            LIMIT %s
        """, (station_id, limit))
        return [_command_row(row_to_dict(cur, r)) for r in cur.fetchall()]


@router.get("/{station_id}/commands/{command_id}")
def get_command(station_id: str, command_id: int):
    """Return one command so the UI can poll it until it is finished."""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, charge_point_id, command, params, status, result, detail,
                   requested_at, updated_at
            FROM station_commands
            WHERE id = %s AND charge_point_id = %s
        """, (command_id, station_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Command not found")
        return _command_row(row_to_dict(cur, row))
