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