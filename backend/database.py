# Theo Bauer · ChargeRelay
"""
database.py – Database connection and schema initialisation for the OCPP server.

Provides a context manager for safe transactional access to PostgreSQL and
runs all CREATE TABLE / ALTER TABLE statements on startup.
"""
import logging
import os
from contextlib import contextmanager

import psycopg2

logger = logging.getLogger("ocpp-server")

# ---------------------------------------------------------------------------
# Connection string
# ---------------------------------------------------------------------------

DSN = (
    "host={host} port={port} dbname={dbname} user={user} password={password}"
    .format(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "ocpp"),
        user=os.environ.get("DB_USER", "ocpp"),
        password=os.environ.get("DB_PASSWORD", "ocpp"),
    )
)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@contextmanager
def db():
    """Open a database connection, commit on success, roll back on error."""
    conn = psycopg2.connect(DSN)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all required tables and run pending column migrations."""
    with db() as conn:
        cur = conn.cursor()

        # Core charge-point registry.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS charge_points (
                id          TEXT PRIMARY KEY,
                model       TEXT,
                vendor      TEXT,
                firmware    TEXT,
                last_seen   TEXT
            )
        """)

        # Migration: hardware identity and connection info for the Stations tab.
        for column, col_type in (
            ("serial_number",       "TEXT"),
            ("box_serial_number",   "TEXT"),
            ("meter_serial_number", "TEXT"),
            ("meter_type",          "TEXT"),
            ("iccid",               "TEXT"),
            ("imsi",                "TEXT"),
            ("heartbeat_interval",  "INTEGER"),
            ("connected_at",        "TEXT"),
            ("remote_address",      "TEXT"),
        ):
            cur.execute(
                f"ALTER TABLE charge_points ADD COLUMN IF NOT EXISTS {column} {col_type}"
            )

        # Latest StatusNotification per connector (connector 0 = the station itself).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS connector_status (
                charge_point_id   TEXT    NOT NULL,
                connector_id      INTEGER NOT NULL,
                status            TEXT,
                error_code        TEXT,
                info              TEXT,
                vendor_error_code TEXT,
                status_timestamp  TEXT,
                updated_at        TEXT,
                PRIMARY KEY (charge_point_id, connector_id)
            )
        """)

        # One row per charging session (start ↔ stop pair).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              SERIAL PRIMARY KEY,
                charge_point_id TEXT    NOT NULL,
                connector_id    INTEGER NOT NULL,
                transaction_id  INTEGER,
                id_tag          TEXT,
                start_time      TEXT,
                stop_time       TEXT,
                start_meter_wh  INTEGER,
                stop_meter_wh   INTEGER,
                energy_kwh      REAL,
                stop_reason     TEXT
            )
        """)

        # Periodic meter readings received during a session.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS meter_values (
                id              SERIAL PRIMARY KEY,
                charge_point_id TEXT    NOT NULL,
                transaction_id  INTEGER,
                timestamp       TEXT,
                measurand       TEXT,
                value           TEXT,
                unit            TEXT
            )
        """)

        # Known vehicles identified by their RFID tag.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                id_tag      TEXT UNIQUE,
                image_data  TEXT,
                created_at  TEXT
            )
        """)

        # Migration: link sessions to vehicles (added after initial schema).
        cur.execute("""
            ALTER TABLE sessions
            ADD COLUMN IF NOT EXISTS vehicle_id INTEGER REFERENCES vehicles(id)
        """)

        # Migration: store the RFID tag from Authorize (may differ from StartTransaction idTag).
        cur.execute("""
            ALTER TABLE sessions
            ADD COLUMN IF NOT EXISTS authorized_id_tag TEXT
        """)

        # Reports – named bundles of vehicles with scheduled deliveries.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)

        # Many-to-many: which vehicles belong to which report.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS report_vehicles (
                report_id   INTEGER NOT NULL REFERENCES reports(id)  ON DELETE CASCADE,
                vehicle_id  INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                PRIMARY KEY (report_id, vehicle_id)
            )
        """)

        # Delivery configurations attached to a report (mail or OCPP relay).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS report_deliveries (
                id          SERIAL  PRIMARY KEY,
                report_id   INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                type        TEXT    NOT NULL,
                email       TEXT,
                interval    TEXT,
                address     TEXT,
                port        INTEGER,
                last_sent   TEXT
            )
        """)

        # Firmware images uploaded through the UI and hosted by the API.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS firmware_files (
                id          SERIAL PRIMARY KEY,
                token       TEXT    NOT NULL UNIQUE,
                filename    TEXT    NOT NULL,
                size_bytes  BIGINT  NOT NULL,
                sha256      TEXT    NOT NULL,
                note        TEXT,
                uploaded_at TEXT    NOT NULL
            )
        """)

        # One row per UpdateFirmware request (queued by the API, sent by the
        # OCPP server, then advanced by FirmwareStatusNotification frames).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS firmware_updates (
                id              SERIAL PRIMARY KEY,
                charge_point_id TEXT    NOT NULL,
                file_id         INTEGER REFERENCES firmware_files(id) ON DELETE SET NULL,
                location        TEXT    NOT NULL,
                filename        TEXT    NOT NULL,
                status          TEXT    NOT NULL,
                detail          TEXT,
                from_version    TEXT,
                to_version      TEXT,
                requested_at    TEXT    NOT NULL,
                requested_from  TEXT,
                sent_at         TEXT,
                updated_at      TEXT    NOT NULL
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS firmware_updates_cp_idx
            ON firmware_updates (charge_point_id, id DESC)
        """)
        # At most one unfinished update per station, enforced by the database so
        # two simultaneous API requests cannot both slip past a check-then-insert.
        # Keep the list in sync with FW_TERMINAL_STATUSES in charge_point.py.
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS firmware_updates_one_active_idx
            ON firmware_updates (charge_point_id)
            WHERE status NOT IN ('Installed', 'DownloadFailed', 'InstallationFailed', 'failed')
        """)

        # Commands the UI asks the OCPP server to send to a station (the API only
        # queues a row; backend/command_dispatcher.py sends it and stores the result).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS station_commands (
                id              SERIAL PRIMARY KEY,
                charge_point_id TEXT NOT NULL,
                command         TEXT NOT NULL,
                params          TEXT,
                status          TEXT NOT NULL,
                result          TEXT,
                detail          TEXT,
                requested_at    TEXT NOT NULL,
                requested_from  TEXT,
                updated_at      TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS station_commands_cp_idx
            ON station_commands (charge_point_id, id DESC)
        """)

        # Last known configuration keys per station.  Secrets are never stored:
        # masked keys keep value = NULL.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS station_config (
                charge_point_id TEXT    NOT NULL,
                key             TEXT    NOT NULL,
                value           TEXT,
                readonly        BOOLEAN NOT NULL DEFAULT FALSE,
                masked          BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at      TEXT    NOT NULL,
                PRIMARY KEY (charge_point_id, key)
            )
        """)
        cur.execute("""
            ALTER TABLE charge_points ADD COLUMN IF NOT EXISTS config_fetched_at TEXT
        """)

        # Migration: add last_sent for mail delivery tracking.
        cur.execute("""
            ALTER TABLE report_deliveries
            ADD COLUMN IF NOT EXISTS last_sent TEXT
        """)

    logger.info("Database initialised")
