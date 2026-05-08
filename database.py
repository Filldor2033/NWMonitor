import sqlite3

DB = "monitor.db"


def get_conn():
    return sqlite3.connect(
        DB,
        timeout=30,
        check_same_thread=False,
    )


def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute("PRAGMA busy_timeout=30000;")

        c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY,
            name TEXT,
            ip TEXT UNIQUE,
            type TEXT
        )
        """)

        # Lightweight migration support for old databases.
        existing_columns = {
            row[1] for row in c.execute("PRAGMA table_info(devices)").fetchall()
        }

        optional_columns = {
            "room": "TEXT",
            "row_no": "INTEGER",
            "desk_no": "INTEGER",
            "pc_no": "INTEGER",
            "last_heartbeat": "TEXT",
            "client_version": "TEXT",
            "client_control_port": "INTEGER",
            "client_control_token": "TEXT",
        }

        for column_name, column_type in optional_columns.items():
            if column_name not in existing_columns:
                c.execute(f"ALTER TABLE devices ADD COLUMN {column_name} {column_type}")

        c.execute("""
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY,
            device_id INTEGER,
            status INTEGER,
            response_time REAL,
            timestamp TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY,
            device_id INTEGER,
            message TEXT,
            timestamp TEXT
        )
        """)

        c.execute("""
        CREATE INDEX IF NOT EXISTS idx_checks_device_id_id
        ON checks(device_id, id DESC)
        """)

        c.execute("""
        CREATE INDEX IF NOT EXISTS idx_checks_device_id_timestamp
        ON checks(device_id, timestamp)
        """)

        c.execute("""
        CREATE INDEX IF NOT EXISTS idx_devices_room_place
        ON devices(room, row_no, desk_no, pc_no)
        """)
