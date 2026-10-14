# Theo Bauer · ChargeRelay
"""
charge_point.py – OCPP 1.6 ChargePoint message handler.

One instance of ChargePoint is created per connected charging station.
It processes every incoming OCPP action, persists the data to PostgreSQL,
and optionally forwards raw frames to configured OCPP relay targets.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call_result
from ocpp.v16.enums import Action, AuthorizationStatus, RegistrationStatus

from database import db

logger = logging.getLogger("ocpp-server")

# The forwarder is injected by main.py after startup so that both modules
# can import without circular dependencies.
forwarder = None

# Heartbeat interval (seconds) announced to charge points in BootNotification.
# The API derives the online/offline state from it, so both must agree.
HEARTBEAT_INTERVAL = 30

# Minimum seconds between "last_seen" refreshes triggered by ordinary frames.
_TOUCH_THROTTLE_SECONDS = 10

# OCPP 1.6 CiString50 – everything the station sends is untrusted input.
_MAX_STR = 50


def _clip(value) -> str | None:
    """Return *value* as a string capped at the OCPP CiString50 length, or None."""
    if value is None:
        return None
    return str(getattr(value, "value", value))[:_MAX_STR]


class ChargePoint(CP):
    """OCPP 1.6 charge-point handler – one instance per WebSocket connection."""

    def __init__(self, cp_id: str, websocket):
        super().__init__(cp_id, websocket)
        self._cp_id = cp_id
        remote = getattr(websocket, "remote_address", None)
        self._remote_address = f"{remote[0]}:{remote[1]}" if remote else None
        self._connected_at = datetime.now(timezone.utc).isoformat()
        self._last_touch = 0.0
        # Maps connector_id → active session row id.
        self._active_sessions: dict[int, int] = {}
        # Last RFID tag accepted via Authorize (used as fallback when StartTransaction
        # sends a dummy/zero tag, as some chargers like ABL do).
        self._last_authorized_tag: str | None = None

    # -----------------------------------------------------------------------
    # Message interception
    # -----------------------------------------------------------------------

    async def route_message(self, raw_msg: str):
        """Forward every raw OCPP frame to relay targets before processing it locally."""
        self._touch()
        if forwarder is not None:
            asyncio.ensure_future(forwarder.forward(self._cp_id, raw_msg))
        return await super().route_message(raw_msg)

    def _touch(self) -> None:
        """Refresh last_seen / connection info for a known station (throttled).

        UPDATE only – a connection that never sends BootNotification must not
        create a charge_points row.
        """
        now_mono = time.monotonic()
        if now_mono - self._last_touch < _TOUCH_THROTTLE_SECONDS:
            return
        self._last_touch = now_mono
        try:
            with db() as conn:
                conn.cursor().execute(
                    """
                    UPDATE charge_points
                    SET last_seen = %s, connected_at = %s, remote_address = %s
                    WHERE id = %s
                    """,
                    (datetime.now(timezone.utc).isoformat(),
                     self._connected_at, self._remote_address, self._cp_id),
                )
        except Exception as exc:
            # Bookkeeping must never break OCPP message handling.
            logger.warning("[%s] last_seen update failed: %s", self._cp_id, exc)

    # -----------------------------------------------------------------------
    # BootNotification
    # -----------------------------------------------------------------------

    @on(Action.BootNotification)
    async def on_boot_notification(
        self,
        charge_point_model: str,
        charge_point_vendor: str,
        firmware_version: str | None = None,
        charge_point_serial_number: str | None = None,
        charge_box_serial_number: str | None = None,
        meter_serial_number: str | None = None,
        meter_type: str | None = None,
        iccid: str | None = None,
        imsi: str | None = None,
        **kwargs,
    ):
        """Register or update the charge point in the database and accept the boot."""
        now = datetime.now(timezone.utc).isoformat()
        with db() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO charge_points
                    (id, model, vendor, firmware, last_seen,
                     serial_number, box_serial_number, meter_serial_number,
                     meter_type, iccid, imsi,
                     heartbeat_interval, connected_at, remote_address)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    model               = EXCLUDED.model,
                    vendor              = EXCLUDED.vendor,
                    firmware            = EXCLUDED.firmware,
                    last_seen           = EXCLUDED.last_seen,
                    serial_number       = EXCLUDED.serial_number,
                    box_serial_number   = EXCLUDED.box_serial_number,
                    meter_serial_number = EXCLUDED.meter_serial_number,
                    meter_type          = EXCLUDED.meter_type,
                    iccid               = EXCLUDED.iccid,
                    imsi                = EXCLUDED.imsi,
                    heartbeat_interval  = EXCLUDED.heartbeat_interval,
                    connected_at        = EXCLUDED.connected_at,
                    remote_address      = EXCLUDED.remote_address
            """, (self._cp_id, _clip(charge_point_model), _clip(charge_point_vendor),
                  _clip(firmware_version), now,
                  _clip(charge_point_serial_number), _clip(charge_box_serial_number),
                  _clip(meter_serial_number), _clip(meter_type),
                  _clip(iccid), _clip(imsi),
                  HEARTBEAT_INTERVAL, self._connected_at, self._remote_address))

        logger.info("[%s] Boot: vendor=%s  model=%s  firmware=%s",
                    self._cp_id, charge_point_vendor, charge_point_model, firmware_version)

        return call_result.BootNotificationPayload(
            current_time=now,
            interval=HEARTBEAT_INTERVAL,
            status=RegistrationStatus.accepted,
        )

    # -----------------------------------------------------------------------
    # Heartbeat
    # -----------------------------------------------------------------------

    @on(Action.Heartbeat)
    async def on_heartbeat(self, **kwargs):
        """Update the charge point's last-seen timestamp and return server time."""
        now = datetime.now(timezone.utc).isoformat()
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE charge_points SET last_seen = %s WHERE id = %s",
                (now, self._cp_id),
            )
        return call_result.HeartbeatPayload(current_time=now)

    # -----------------------------------------------------------------------
    # Authorize
    # -----------------------------------------------------------------------

    @on(Action.Authorize)
    async def on_authorize(self, id_tag: str, **kwargs):
        """Accept all RFID tags – extend this method to enforce a whitelist."""
        self._last_authorized_tag = id_tag
        logger.info("[%s] Authorize: id_tag=%s", self._cp_id, id_tag)
        return call_result.AuthorizePayload(
            id_tag_info={"status": AuthorizationStatus.accepted}
        )

    # -----------------------------------------------------------------------
    # StartTransaction
    # -----------------------------------------------------------------------

    @on(Action.StartTransaction)
    async def on_start_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int,
        timestamp: str,
        reservation_id: int | None = None,
        **kwargs,
    ):
        """Create a new session row and auto-assign a vehicle when the RFID tag is known."""
        with db() as conn:
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO sessions
                    (charge_point_id, connector_id, id_tag, authorized_id_tag, start_time, start_meter_wh)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (self._cp_id, connector_id, id_tag, self._last_authorized_tag,
                  timestamp, meter_start))

            session_id     = cur.fetchone()[0]
            transaction_id = session_id   # re-use the row id as the transaction id
            cur.execute(
                "UPDATE sessions SET transaction_id = %s WHERE id = %s",
                (transaction_id, session_id),
            )

            # Look up vehicle by the transaction tag first; fall back to the last
            # authorized tag (some chargers send a dummy tag in StartTransaction but
            # use the real RFID in the preceding Authorize request).
            cur.execute("SELECT id FROM vehicles WHERE id_tag = %s", (id_tag,))
            vehicle_row = cur.fetchone()
            if not vehicle_row and self._last_authorized_tag and self._last_authorized_tag != id_tag:
                cur.execute("SELECT id FROM vehicles WHERE id_tag = %s", (self._last_authorized_tag,))
                vehicle_row = cur.fetchone()
            if vehicle_row:
                cur.execute(
                    "UPDATE sessions SET vehicle_id = %s WHERE id = %s",
                    (vehicle_row[0], session_id),
                )

        self._active_sessions[connector_id] = session_id
        logger.info("[%s] StartTransaction: connector=%d  id_tag=%s  meter=%d Wh  → tx=%d",
                    self._cp_id, connector_id, id_tag, meter_start, transaction_id)

        return call_result.StartTransactionPayload(
            transaction_id=transaction_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    # -----------------------------------------------------------------------
    # StopTransaction
    # -----------------------------------------------------------------------

    @on(Action.StopTransaction)
    async def on_stop_transaction(
        self,
        transaction_id: int,
        meter_stop: int,
        timestamp: str,
        reason: str | None = None,
        id_tag: str | None = None,
        **kwargs,
    ):
        """Close the session, calculate energy consumed, and record the stop reason."""
        with db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, connector_id, start_meter_wh FROM sessions WHERE transaction_id = %s",
                (transaction_id,),
            )
            row = cur.fetchone()

            if row:
                session_id, connector_id, start_wh = row
                energy_kwh = (meter_stop - start_wh) / 1000.0
                cur.execute("""
                    UPDATE sessions
                    SET stop_time = %s, stop_meter_wh = %s,
                        energy_kwh = %s, stop_reason = %s
                    WHERE id = %s
                """, (timestamp, meter_stop, energy_kwh, reason, session_id))
                self._active_sessions.pop(connector_id, None)

                logger.info(
                    "[%s] StopTransaction: tx=%d  id_tag=%s  energy=%.3f kWh  reason=%s",
                    self._cp_id, transaction_id, id_tag, energy_kwh, reason,
                )
            else:
                logger.warning("[%s] StopTransaction: unknown transaction_id=%d",
                               self._cp_id, transaction_id)

        return call_result.StopTransactionPayload()

    # -----------------------------------------------------------------------
    # MeterValues
    # -----------------------------------------------------------------------

    @on(Action.MeterValues)
    async def on_meter_values(
        self,
        connector_id: int,
        meter_value: list,
        transaction_id: int | None = None,
        **kwargs,
    ):
        """Persist all sampled meter values received during a transaction."""
        rows = []
        for mv in meter_value:
            ts = mv.get("timestamp")
            for sv in mv.get("sampled_value", []):
                rows.append((
                    self._cp_id,
                    transaction_id,
                    ts,
                    sv.get("measurand", "Energy.Active.Import.Register"),
                    sv.get("value"),
                    sv.get("unit", "Wh"),
                ))

        if rows:
            with db() as conn:
                cur = conn.cursor()
                cur.executemany("""
                    INSERT INTO meter_values
                        (charge_point_id, transaction_id, timestamp, measurand, value, unit)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, rows)
            logger.info("[%s] MeterValues: %d sample(s) for tx %s",
                        self._cp_id, len(rows), transaction_id)

        return call_result.MeterValuesPayload()

    # -----------------------------------------------------------------------
    # StatusNotification
    # -----------------------------------------------------------------------

    @on(Action.StatusNotification)
    async def on_status_notification(
        self,
        connector_id: int,
        error_code: str,
        status: str,
        info: str | None = None,
        timestamp: str | None = None,
        vendor_error_code: str | None = None,
        **kwargs,
    ):
        """Log the status change and keep the latest state per connector."""
        logger.info("[%s] Status: connector=%d  status=%s  error=%s",
                    self._cp_id, connector_id, status, error_code)
        now = datetime.now(timezone.utc).isoformat()
        with db() as conn:
            conn.cursor().execute("""
                INSERT INTO connector_status
                    (charge_point_id, connector_id, status, error_code, info,
                     vendor_error_code, status_timestamp, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (charge_point_id, connector_id) DO UPDATE SET
                    status            = EXCLUDED.status,
                    error_code        = EXCLUDED.error_code,
                    info              = EXCLUDED.info,
                    vendor_error_code = EXCLUDED.vendor_error_code,
                    status_timestamp  = EXCLUDED.status_timestamp,
                    updated_at        = EXCLUDED.updated_at
            """, (self._cp_id, connector_id, _clip(status), _clip(error_code),
                  _clip(info), _clip(vendor_error_code), _clip(timestamp), now))
        return call_result.StatusNotificationPayload()
