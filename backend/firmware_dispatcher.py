# Theo Bauer · ChargeRelay
"""
firmware_dispatcher.py – Sends queued firmware updates to connected stations.

The API only writes a 'queued' row into firmware_updates; this process owns the
charger websockets, so it picks the row up, sends OCPP UpdateFirmware and marks
it 'sent' (or 'failed' with a reason).  Later FirmwareStatusNotification frames
are handled in charge_point.py.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import psycopg2

import charge_point as cp_module
from charge_point import FW_TERMINAL_STATUSES

logger = logging.getLogger("firmware-dispatcher")

POLL_INTERVAL_SECONDS = 2

# An update that has not reached a terminal state after this long is failed so
# the UI never shows it as running forever.
STUCK_AFTER = timedelta(hours=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FirmwareDispatcher:
    """Background worker that turns queued firmware_updates rows into UpdateFirmware calls."""

    def __init__(self, dsn: str):
        self._dsn = dsn

    # ------------------------------------------------------------------ DB helpers

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def _claim_queued(self) -> list[tuple[int, str, str]]:
        """Atomically move every 'queued' row to 'sending' and return (id, cp_id, location)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE firmware_updates
                SET status = 'sending', updated_at = %s
                WHERE id IN (
                    SELECT id FROM firmware_updates
                    WHERE status = 'queued'
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, charge_point_id, location
            """, (_now(),))
            rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _set_status(self, update_id: int, status: str, detail: str | None = None) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            if status == "sent":
                cur.execute(
                    "UPDATE firmware_updates SET status = %s, detail = %s, sent_at = %s, "
                    "updated_at = %s WHERE id = %s",
                    (status, detail, _now(), _now(), update_id),
                )
            else:
                cur.execute(
                    "UPDATE firmware_updates SET status = %s, detail = %s, updated_at = %s "
                    "WHERE id = %s",
                    (status, detail, _now(), update_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _fail_stuck(self) -> None:
        """Fail updates that have been unfinished for longer than STUCK_AFTER."""
        cutoff = (datetime.now(timezone.utc) - STUCK_AFTER).isoformat()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE firmware_updates
                SET status = 'failed', detail = 'timeout: no final status from station',
                    updated_at = %s
                WHERE status NOT IN %s AND updated_at < %s
            """, (_now(), FW_TERMINAL_STATUSES, cutoff))
            if cur.rowcount:
                logger.warning("Marked %d stuck firmware update(s) as failed", cur.rowcount)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ sending

    async def _send(self, update_id: int, cp_id: str, location: str) -> None:
        cp = cp_module.connected.get(cp_id)
        if cp is None:
            self._set_status(update_id, "failed", "station offline")
            return
        try:
            await cp.send_update_firmware(location)
        except asyncio.TimeoutError:
            self._set_status(update_id, "failed", "no response from station")
        except Exception as exc:
            # ocpp raises an OCPPError subclass for a CallError response.
            self._set_status(update_id, "failed", f"rejected by station: {type(exc).__name__}"[:200])
            logger.warning("[%s] UpdateFirmware failed: %s", cp_id, exc)
        else:
            self._set_status(update_id, "sent")
            logger.info("[%s] UpdateFirmware sent (update_id=%d)", cp_id, update_id)

    async def _run_once(self) -> None:
        loop = asyncio.get_running_loop()
        for update_id, cp_id, location in await loop.run_in_executor(None, self._claim_queued):
            await self._send(update_id, cp_id, location)

    async def run(self) -> None:
        """Infinite background loop – start with asyncio.create_task(dispatcher.run())."""
        logger.info("FirmwareDispatcher started")
        loop = asyncio.get_running_loop()
        ticks = 0
        while True:
            try:
                await self._run_once()
                ticks += 1
                if ticks % 30 == 0:  # roughly once a minute
                    await loop.run_in_executor(None, self._fail_stuck)
            except Exception as exc:
                logger.error("FirmwareDispatcher unexpected error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
