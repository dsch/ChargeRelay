# Theo Bauer · ChargeRelay
"""
routers/stations.py – Charging station (EVSE) status endpoint.

Routes
------
GET /api/stations   Return every known station with identity, connection state,
                    per-connector status and usage statistics.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from database import db, row_to_dict
from routers.sessions import get_active_sessions

router = APIRouter(prefix="/api")

# A station counts as online while its last frame is younger than this many
# heartbeat intervals.
ONLINE_FACTOR = 3
DEFAULT_HEARTBEAT_SECONDS = 30


def _parse_utc(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp; naive values are treated as UTC."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# GET /api/stations
# ---------------------------------------------------------------------------

@router.get("/stations")
def get_stations():
    """Return all stations with connection state, connectors and statistics."""
    now = datetime.now(timezone.utc)
    since_30 = (now - timedelta(days=30)).isoformat()

    # Live data (latest meter values, running energy) for active sessions.
    active_by_cp: dict[str, list[dict]] = {}
    for s in get_active_sessions():
        active_by_cp.setdefault(s["charge_point_id"], []).append(s)

    with db() as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, vendor, model, firmware,
                   serial_number, box_serial_number, meter_serial_number,
                   meter_type, iccid, imsi,
                   heartbeat_interval, connected_at, remote_address, last_seen
            FROM charge_points
            ORDER BY id
        """)
        stations = [row_to_dict(cur, r) for r in cur.fetchall()]

        cur.execute("""
            SELECT charge_point_id, connector_id, status, error_code, info,
                   vendor_error_code, status_timestamp, updated_at
            FROM connector_status
            ORDER BY charge_point_id, connector_id
        """)
        connectors_by_cp: dict[str, list[dict]] = {}
        for r in cur.fetchall():
            c = row_to_dict(cur, r)
            connectors_by_cp.setdefault(c.pop("charge_point_id"), []).append(c)

        cur.execute("""
            SELECT charge_point_id,
                   COUNT(*) FILTER (WHERE start_time >= %s AND stop_time IS NOT NULL),
                   COALESCE(SUM(energy_kwh) FILTER (WHERE start_time >= %s), 0),
                   COALESCE(SUM(energy_kwh), 0),
                   MAX(start_time)
            FROM sessions
            GROUP BY charge_point_id
        """, (since_30, since_30))
        stats_by_cp = {
            r[0]: {
                "sessions_30d": r[1],
                "kwh_30d": round(float(r[2]), 2),
                "kwh_total": round(float(r[3]), 2),
                "last_session_start": r[4],
            }
            for r in cur.fetchall()
        }

        # Newest firmware update per station (progress shown on the card).
        cur.execute("""
            SELECT DISTINCT ON (charge_point_id)
                   id, charge_point_id, filename, status, detail,
                   from_version, to_version, requested_at, sent_at, updated_at
            FROM firmware_updates
            ORDER BY charge_point_id, id DESC
        """)
        fw_update_by_cp = {r[1]: row_to_dict(cur, r) for r in cur.fetchall()}

    result = []
    for st in stations:
        cp_id = st["id"]
        interval = st["heartbeat_interval"] or DEFAULT_HEARTBEAT_SECONDS

        last_seen = _parse_utc(st["last_seen"])
        age = int((now - last_seen).total_seconds()) if last_seen else None
        st["last_seen_seconds"] = age
        st["online"] = age is not None and age <= ONLINE_FACTOR * interval

        active = active_by_cp.get(cp_id, [])
        active_by_connector = {s["connector_id"]: s for s in active}

        connectors = connectors_by_cp.get(cp_id, [])
        known = {c["connector_id"] for c in connectors}
        # A running session on a connector we have no status for still shows up.
        for conn_id in sorted(set(active_by_connector) - known):
            connectors.append({
                "connector_id": conn_id, "status": None, "error_code": None,
                "info": None, "vendor_error_code": None,
                "status_timestamp": None, "updated_at": None,
            })

        for c in connectors:
            s = active_by_connector.get(c["connector_id"])
            c["active_session"] = None if s is None else {
                "session_id": s["session_id"],
                "vehicle_name": s["vehicle_name"],
                "duration_seconds": s["duration_seconds"],
                "session_energy_kwh": s["session_energy_kwh"],
                "latest_meter_values": s["latest_meter_values"],
            }

        st["connectors"] = connectors
        st["firmware_update"] = fw_update_by_cp.get(cp_id)
        st["stats"] = {
            "sessions_30d": 0, "kwh_30d": 0.0, "kwh_total": 0.0,
            "last_session_start": None,
            **stats_by_cp.get(cp_id, {}),
            "active_sessions": len(active),
        }
        result.append(st)

    return result
