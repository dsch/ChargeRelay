# Theo Bauer · ChargeRelay
"""
command_dispatcher.py – Sends queued station commands (configuration, reset, trigger).

The API only writes a 'queued' row into station_commands; this process owns the
charger websockets, so it claims the row, sends the OCPP message, and stores the
outcome ('done' with a JSON result, or 'failed' with a reason).  Configuration
results are also cached in station_config so the UI can show them offline.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import psycopg2
from ocpp.messages import Call

import charge_point as cp_module

logger = logging.getLogger("command-dispatcher")

POLL_INTERVAL_SECONDS = 1

# Rows not picked up / finished within this time are failed so the UI stops waiting.
STUCK_AFTER = timedelta(minutes=2)
KEEP_HISTORY = timedelta(days=30)

# Key names that may hold secrets (backend credentials, Wi-Fi passphrases ...).
# The API is unauthenticated, so their values are never stored or served.
# A secret-like word anywhere in the name hides the value.  Substring matching
# is used on purpose: word splitting cannot recognise run-together names such as
# "WPAPSK" or "authkey", and a leaked credential is worse than a hidden number.
# "Keys" (plural, e.g. GetConfigurationMaxKeys) is a count, not a secret.
_SECRET_PATTERN = re.compile(
    r"key|pass(?:word|wd|code|phrase)|secret|token|psk|credential"
    r"|(?:^|[._\-])(?:pin|puk)(?:$|[._\-])",
    re.IGNORECASE,
)
# CamelCase "SimPin" / "SimPuk": must stay case-sensitive, otherwise "Spinner",
# "Shipping" and "WebSocketPingInterval" would match.
_SECRET_CAMEL = re.compile(r"[a-z](?:Pin|Puk)(?:$|[A-Z])")
_NOT_SECRET = re.compile(r"keys", re.IGNORECASE)

_MAX_VALUE = 500
# OCPP limits keys to 50 characters, but real chargers use longer vendor keys
# (e.g. the eBox: "periphery.uiboard.bluetooth_button_pressed_duration").  Truncating
# would merge distinct keys, so keep them whole up to a generous cap.
_MAX_KEY = 128


def is_secret_key(key: str) -> bool:
    """True if the value of this configuration key must never be stored or served."""
    # Drop the harmless plural before matching so "MaxKeys" is not flagged, while
    # "AuthKeys" style names still match via their other words.
    cleaned = _NOT_SECRET.sub("", key)
    return bool(_SECRET_PATTERN.search(cleaned) or _SECRET_CAMEL.search(cleaned))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommandDispatcher:
    """Background worker that turns queued station_commands rows into OCPP calls."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ DB helpers

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def _claim_queued(self) -> list[tuple[int, str, str, str | None]]:
        """Atomically move 'queued' rows to 'sending'; returns (id, cp_id, command, params)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE station_commands
                SET status = 'sending', updated_at = %s
                WHERE id IN (
                    SELECT id FROM station_commands
                    WHERE status = 'queued'
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, charge_point_id, command, params
            """, (_now(),))
            rows = cur.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _finish(self, cmd_id: int, status: str, result: dict | None = None,
                detail: str | None = None) -> None:
        conn = self._connect()
        try:
            conn.cursor().execute(
                "UPDATE station_commands SET status = %s, result = %s, detail = %s, "
                "updated_at = %s WHERE id = %s",
                (status, json.dumps(result) if result is not None else None,
                 detail, _now(), cmd_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _store_configuration(self, cp_id: str, items: list[dict]) -> None:
        """Replace the cached configuration of a station in one transaction."""
        now = _now()
        rows = []
        for item in items:
            key = str(item.get("key", ""))
            if not key or len(key) > _MAX_KEY:
                continue   # unusable: skip rather than store a truncated, wrong key
            masked = is_secret_key(key)
            value = None if masked else (None if item.get("value") is None
                                         else str(item["value"])[:_MAX_VALUE])
            rows.append((cp_id, key, value, bool(item.get("readonly", False)), masked, now))

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM station_config WHERE charge_point_id = %s", (cp_id,))
            cur.executemany("""
                INSERT INTO station_config
                    (charge_point_id, key, value, readonly, masked, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (charge_point_id, key) DO NOTHING
            """, rows)
            cur.execute(
                "UPDATE charge_points SET config_fetched_at = %s WHERE id = %s", (now, cp_id)
            )
            conn.commit()
        finally:
            conn.close()

    def _store_changed_key(self, cp_id: str, key: str, value: str) -> None:
        """Reflect an accepted ChangeConfiguration in the cache (secrets stay hidden)."""
        masked = is_secret_key(key)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO station_config
                    (charge_point_id, key, value, readonly, masked, updated_at)
                VALUES (%s, %s, %s, FALSE, %s, %s)
                ON CONFLICT (charge_point_id, key) DO UPDATE SET
                    value = EXCLUDED.value, masked = EXCLUDED.masked,
                    updated_at = EXCLUDED.updated_at
            """, (cp_id, key, None if masked else value[:_MAX_VALUE], masked, _now()))
            # The API treats a station as offline after 3 heartbeat intervals, so it
            # has to follow an accepted HeartbeatInterval change.
            if key == "HeartbeatInterval" and value.isdigit() and 0 < int(value) < 86400:
                cur.execute(
                    "UPDATE charge_points SET heartbeat_interval = %s WHERE id = %s",
                    (int(value), cp_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _housekeeping(self) -> None:
        """Fail commands nobody finished and prune old history."""
        now = datetime.now(timezone.utc)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE station_commands
                SET status = 'failed', detail = 'timeout', updated_at = %s
                WHERE status IN ('queued', 'sending') AND updated_at < %s
            """, (_now(), (now - STUCK_AFTER).isoformat()))
            cur.execute(
                "DELETE FROM station_commands WHERE updated_at < %s",
                ((now - KEEP_HISTORY).isoformat(),),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ sending

    async def _execute(self, cmd_id: int, cp_id: str, command: str, params_json: str | None) -> None:
        loop = asyncio.get_running_loop()
        cp = cp_module.connected.get(cp_id)
        if cp is None:
            await loop.run_in_executor(None, self._finish, cmd_id, "failed", None, "station offline")
            return

        try:
            params = json.loads(params_json) if params_json else {}
            if command == "GetConfiguration":
                res = await cp.get_configuration()
                items = [
                    {"key": k.get("key"), "value": k.get("value"), "readonly": k.get("readonly", False)}
                    for k in (res.configuration_key or [])
                ]
                await loop.run_in_executor(None, self._store_configuration, cp_id, items)
                result = {"count": len(items), "unknown_key": res.unknown_key or []}
            elif command == "ChangeConfiguration":
                res = await cp.change_configuration(params["key"], params["value"])
                result = {"status": str(getattr(res.status, "value", res.status))}
                if result["status"] in ("Accepted", "RebootRequired"):
                    await loop.run_in_executor(
                        None, self._store_changed_key, cp_id, params["key"], params["value"]
                    )
            elif command == "Reset":
                res = await cp.reset(params["type"])
                result = {"status": str(getattr(res.status, "value", res.status))}
            elif command == "TriggerMessage":
                res = await cp.trigger_message(params["message"], params.get("connector_id"))
                result = {"status": str(getattr(res.status, "value", res.status))}
            else:
                await loop.run_in_executor(None, self._finish, cmd_id, "failed", None, "unknown command")
                return
        except asyncio.TimeoutError:
            await loop.run_in_executor(None, self._finish, cmd_id, "failed", None, "no response from station")
            return
        except Exception as exc:
            # The ocpp library raises an OCPPError subclass both when the STATION answers
            # with a CallError and when OUR request fails schema validation before it is
            # sent (e.g. a key over 50 characters).  Only the latter carries the outgoing
            # Call in details["ocpp_message"], so that tells them apart.
            logger.warning("[%s] %s failed: %s", cp_id, command, exc)
            local = isinstance((getattr(exc, "details", None) or {}).get("ocpp_message"), Call)
            detail = ("not sent: request violates the OCPP schema" if local
                      else f"rejected by station: {type(exc).__name__}")
            await loop.run_in_executor(None, self._finish, cmd_id, "failed", None, detail[:200])
            return

        await loop.run_in_executor(None, self._finish, cmd_id, "done", result, None)
        logger.info("[%s] %s done: %s", cp_id, command, result)

    async def _run_once(self) -> None:
        loop = asyncio.get_running_loop()
        for cmd_id, cp_id, command, params in await loop.run_in_executor(None, self._claim_queued):
            # One task per command: the ocpp library serialises calls per station, so a
            # slow station cannot block commands for the others.
            task = asyncio.create_task(self._execute(cmd_id, cp_id, command, params))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def run(self) -> None:
        """Infinite background loop – start with asyncio.create_task(dispatcher.run())."""
        logger.info("CommandDispatcher started")
        loop = asyncio.get_running_loop()
        ticks = 0
        while True:
            try:
                await self._run_once()
                ticks += 1
                if ticks % 30 == 0:  # roughly every 30 s
                    await loop.run_in_executor(None, self._housekeeping)
            except Exception as exc:
                logger.error("CommandDispatcher unexpected error: %s", exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
