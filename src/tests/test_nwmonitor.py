"""
Tests for NWMonitor.

Run: pytest tests/ -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is on path when running from tests/ or from root
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point database.DB at a temp file so tests are isolated."""
    db_file = str(tmp_path / "test_monitor.db")
    monkeypatch.setattr(database, "DB", db_file)
    database.init_db()
    return db_file


@pytest.fixture()
def flask_app(tmp_db):
    """Create a Flask test client with an isolated database."""
    import app as application  # import after DB is patched
    application.app.config["TESTING"] = True
    application.app.config["SECRET_KEY"] = "test-secret"
    # Disable background scheduler jobs during tests
    try:
        application.scheduler.pause()
    except Exception:
        pass
    with application.app.test_client() as client:
        yield client
    try:
        application.scheduler.resume()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# database.py tests
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_init_creates_tables(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "devices" in tables
        assert "checks" in tables
        assert "alerts" in tables

    def test_init_adds_optional_columns(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(devices)").fetchall()
        }
        conn.close()
        expected = {
            "room", "row_no", "desk_no", "pc_no",
            "last_heartbeat", "client_version",
            "client_control_port", "client_control_token",
            "heartbeat_online",
        }
        assert expected.issubset(columns)

    def test_init_is_idempotent(self, tmp_db):
        """Calling init_db twice must not raise."""
        database.init_db()
        database.init_db()

    def test_get_conn_returns_connection(self, tmp_db):
        conn = database.get_conn()
        assert conn is not None
        conn.close()

    def test_migration_adds_missing_column(self, tmp_path, monkeypatch):
        """Simulate an old DB without heartbeat_online; init_db should add it."""
        db_file = str(tmp_path / "old.db")
        monkeypatch.setattr(database, "DB", db_file)

        # Create DB without heartbeat_online
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE devices (id INTEGER PRIMARY KEY, name TEXT, ip TEXT UNIQUE, type TEXT)"
        )
        conn.commit()
        conn.close()

        database.init_db()

        conn = sqlite3.connect(db_file)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(devices)").fetchall()}
        conn.close()
        assert "heartbeat_online" in columns


# ---------------------------------------------------------------------------
# app.py — heartbeat endpoint
# ---------------------------------------------------------------------------

class TestHeartbeatEndpoint:
    def test_heartbeat_registers_device(self, flask_app):
        resp = flask_app.post(
            "/api/client/heartbeat",
            json={
                "name": "PC-01",
                "ip": "192.168.1.10",
                "room": "3",
                "row_no": 1,
                "desk_no": 1,
                "pc_no": 1,
                "client_version": "1.1.0",
                "client_control_port": 8765,
                "client_control_token": "abc123",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["ip"] == "192.168.1.10"

    def test_heartbeat_upserts_on_same_ip(self, flask_app):
        payload = {
            "name": "PC-01",
            "ip": "192.168.1.10",
            "room": "3",
            "row_no": 1,
            "desk_no": 1,
            "pc_no": 1,
        }
        flask_app.post("/api/client/heartbeat", json=payload)
        payload["name"] = "PC-01-renamed"
        resp = flask_app.post("/api/client/heartbeat", json=payload)
        assert resp.status_code == 200

    def test_heartbeat_sets_heartbeat_online(self, flask_app, tmp_db):
        flask_app.post(
            "/api/client/heartbeat",
            json={"name": "PC-02", "ip": "10.0.0.2", "room": "3"},
        )
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT heartbeat_online FROM devices WHERE ip='10.0.0.2'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 1

    def test_heartbeat_missing_ip_falls_back_to_remote_addr(self, flask_app):
        resp = flask_app.post(
            "/api/client/heartbeat",
            json={"name": "NoIP"},
            environ_base={"REMOTE_ADDR": "10.0.0.99"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True


# ---------------------------------------------------------------------------
# app.py — device_statuses endpoint
# ---------------------------------------------------------------------------

class TestDeviceStatusesEndpoint:
    def _register(self, client, ip, room="3"):
        client.post("/api/client/heartbeat", json={"name": ip, "ip": ip, "room": room})

    def test_returns_heartbeat_status_for_fresh_device(self, flask_app):
        self._register(flask_app, "10.0.1.1")
        resp = flask_app.get("/api/device_statuses")
        assert resp.status_code == 200
        statuses = resp.get_json()
        assert "heartbeat" in statuses.values()

    def test_returns_json_object(self, flask_app):
        resp = flask_app.get("/api/device_statuses")
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert isinstance(data, dict)

    def test_empty_when_no_devices(self, flask_app):
        resp = flask_app.get("/api/device_statuses")
        assert resp.get_json() == {}


# ---------------------------------------------------------------------------
# app.py — index page
# ---------------------------------------------------------------------------

class TestIndexPage:
    def test_index_returns_200(self, flask_app):
        resp = flask_app.get("/")
        assert resp.status_code == 200

    def test_index_with_room_param(self, flask_app):
        resp = flask_app.get("/?room=3")
        assert resp.status_code == 200

    def test_index_shows_device_after_heartbeat(self, flask_app):
        flask_app.post(
            "/api/client/heartbeat",
            json={"name": "Lab-PC", "ip": "192.168.5.5", "room": "3"},
        )
        resp = flask_app.get("/")
        assert b"Lab-PC" in resp.data or b"192.168.5.5" in resp.data


# ---------------------------------------------------------------------------
# app.py — assign endpoint
# ---------------------------------------------------------------------------

class TestAssignEndpoint:
    def _device_id(self, tmp_db, ip):
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT id FROM devices WHERE ip=?", (ip,)).fetchone()
        conn.close()
        return row[0] if row else None

    def test_assign_device_to_seat(self, flask_app, tmp_db):
        flask_app.post(
            "/api/client/heartbeat",
            json={"name": "PC-A", "ip": "10.1.1.1", "room": "3"},
        )
        device_id = self._device_id(tmp_db, "10.1.1.1")
        resp = flask_app.post(
            f"/assign/{device_id}",
            data={"room": "3", "row_no": "1", "desk_no": "2", "pc_no": "1"},
        )
        # Should redirect
        assert resp.status_code in (301, 302)

        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT room, row_no, desk_no, pc_no FROM devices WHERE id=?", (device_id,)
        ).fetchone()
        conn.close()
        assert row == ("3", 1, 2, 1)


# ---------------------------------------------------------------------------
# app.py — export endpoint
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    def test_export_returns_csv(self, flask_app, tmp_db):
        flask_app.post(
            "/api/client/heartbeat",
            json={"name": "PC-EXP", "ip": "10.2.2.2", "room": "3"},
        )
        conn = sqlite3.connect(tmp_db)
        device_id = conn.execute(
            "SELECT id FROM devices WHERE ip='10.2.2.2'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO checks (device_id, status, response_time, timestamp) "
            "VALUES (?, 1, 12.5, datetime('now'))",
            (device_id,),
        )
        conn.commit()
        conn.close()

        resp = flask_app.get(f"/export/{device_id}")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type or resp.headers.get("Content-Disposition", "")


# ---------------------------------------------------------------------------
# app.py — helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_to_int_or_none_valid(self):
        import app as application
        assert application.to_int_or_none("5") == 5
        assert application.to_int_or_none(3) == 3

    def test_to_int_or_none_invalid(self):
        import app as application
        assert application.to_int_or_none("abc") is None
        assert application.to_int_or_none(None) is None
        assert application.to_int_or_none("") is None

    def test_is_admin_mode_default_false(self, flask_app):
        with flask_app.session_transaction() as sess:
            sess.clear()
        resp = flask_app.get("/")
        assert resp.status_code == 200  # no crash — admin_mode defaults to False


# ---------------------------------------------------------------------------
# student_client.py — unit tests (no network)
# ---------------------------------------------------------------------------

class TestStudentClientHelpers:
    def test_get_local_ip_returns_string(self):
        from student_client import get_local_ip
        ip = get_local_ip()
        assert isinstance(ip, str)

    def test_load_config_returns_none_when_missing(self, tmp_path, monkeypatch):
        import student_client as sc
        monkeypatch.setattr(sc, "CONFIG_PATH", tmp_path / "nonexistent.json")
        assert sc.load_config() is None

    def test_save_and_load_config(self, tmp_path, monkeypatch):
        import student_client as sc
        cfg_path = tmp_path / "client_config.json"
        monkeypatch.setattr(sc, "CONFIG_PATH", cfg_path)
        config = {"server_url": "http://localhost:5000", "room": "3", "row_no": 1}
        sc.save_config(config)
        loaded = sc.load_config()
        assert loaded["room"] == "3"
        assert loaded["server_url"] == "http://localhost:5000"

    def test_ensure_control_fields_adds_missing(self, tmp_path, monkeypatch):
        import student_client as sc
        cfg_path = tmp_path / "client_config.json"
        monkeypatch.setattr(sc, "CONFIG_PATH", cfg_path)
        config = {"server_url": "http://localhost:5000"}
        sc.ensure_control_fields(config)
        assert "client_control_port" in config
        assert "client_control_token" in config
        assert config["client_control_port"] == sc.DEFAULT_CONTROL_PORT

    def test_ensure_control_fields_preserves_existing(self, tmp_path, monkeypatch):
        import student_client as sc
        cfg_path = tmp_path / "client_config.json"
        monkeypatch.setattr(sc, "CONFIG_PATH", cfg_path)
        config = {
            "server_url": "http://localhost:5000",
            "client_control_port": 9999,
            "client_control_token": "mytoken",
        }
        sc.ensure_control_fields(config)
        assert config["client_control_port"] == 9999
        assert config["client_control_token"] == "mytoken"
