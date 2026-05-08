import csv
import os
import threading
import traceback

import matplotlib.pyplot as plt
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from database import get_conn, init_db
from monitor import run_checks

app = Flask(__name__)
app.secret_key = os.environ.get("NWMONITOR_SECRET_KEY", "nwmonitor-dev-secret")
init_db()

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.add_job(run_checks, "interval", seconds=60, max_instances=1, coalesce=True)
scheduler.start()

# --- Classroom constants ---
DEFAULT_ROOM = "3"
CLASSROOM_ROWS = 2
DESKS_PER_ROW = 4
PCS_PER_DESK = 2

# --- Scan status ---
scan_status = {
    "running": False,
    "progress": 0,
    "total": 1,
    "found": 0,
    "error": None,
}

# --- Ping refresh status ---
ping_status = {
    "running": False,
    "progress": 0,
    "total": 1,
    "error": None,
}

scan_status_lock = threading.Lock()
ping_status_lock = threading.Lock()


def to_int_or_none(value):
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def get_request_ip(req):
    forwarded = req.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return req.remote_addr or ""


def is_admin_mode():
    return bool(session.get("admin_mode", False))


def send_delete_config_command(ip, port, token):
    if not ip or not port or not token:
        return False, "Client control endpoint is not configured"

    url = f"http://{ip}:{int(port)}/admin/delete_config"
    try:
        response = requests.post(url, json={"token": token}, timeout=4)
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok"):
            return True, payload.get("message", "Config removed")
        return False, payload.get("error", "Client rejected command")
    except Exception as e:
        return False, str(e)


def background_ping_refresh():
    global ping_status

    with ping_status_lock:
        ping_status["running"] = True
        ping_status["progress"] = 0
        ping_status["total"] = 1
        ping_status["error"] = None

    try:

        def progress(done, total):
            with ping_status_lock:
                ping_status["progress"] = done
                ping_status["total"] = total

        run_checks(progress_callback=progress)

    except Exception as e:
        with ping_status_lock:
            ping_status["error"] = str(e)
        traceback.print_exc()
    finally:
        with ping_status_lock:
            ping_status["running"] = False


@app.route("/")
def index():
    selected_room = (request.args.get("room") or DEFAULT_ROOM).strip() or DEFAULT_ROOM
    admin_mode = is_admin_mode()
    admin_result = (request.args.get("admin_result") or "").strip()

    with get_conn() as conn:
        c = conn.cursor()

        rows = c.execute(
            """
            SELECT
                d.id, d.name, d.ip, d.type,
                d.room, d.row_no, d.desk_no, d.pc_no, d.last_heartbeat,
                d.client_control_port, d.client_control_token,
                c.status, c.response_time, c.timestamp
            FROM devices d
            LEFT JOIN checks c ON c.id = (
                SELECT id FROM checks
                WHERE device_id = d.id
                ORDER BY id DESC
                LIMIT 1
            )
            ORDER BY
                COALESCE(d.room, ''),
                COALESCE(d.row_no, 999),
                COALESCE(d.desk_no, 999),
                COALESCE(d.pc_no, 999),
                d.id
            """
        ).fetchall()

        rooms_raw = c.execute(
            """
            SELECT DISTINCT room
            FROM devices
            WHERE room IS NOT NULL AND TRIM(room) != ''
            ORDER BY room
            """
        ).fetchall()

    devices = []
    for r in rows:
        devices.append(
            {
                "id": r[0],
                "name": r[1],
                "ip": r[2],
                "type": r[3],
                "room": r[4],
                "row_no": r[5],
                "desk_no": r[6],
                "pc_no": r[7],
                "last_heartbeat": r[8],
                "client_control_port": r[9],
                "client_control_token": r[10],
                "is_up": bool(r[11]) if r[11] is not None else None,
                "response_time": r[12],
                "last_check_at": r[13],
            }
        )

    rooms = [room[0] for room in rooms_raw]
    if selected_room not in rooms:
        rooms.insert(0, selected_room)

    classroom = []
    for row_no in range(1, CLASSROOM_ROWS + 1):
        desk_list = []
        for desk_no in range(1, DESKS_PER_ROW + 1):
            pc_list = []
            for pc_no in range(1, PCS_PER_DESK + 1):
                device = next(
                    (
                        d
                        for d in devices
                        if str(d.get("room") or "") == selected_room
                        and d.get("row_no") == row_no
                        and d.get("desk_no") == desk_no
                        and d.get("pc_no") == pc_no
                    ),
                    None,
                )
                pc_list.append(
                    {
                        "row_no": row_no,
                        "desk_no": desk_no,
                        "pc_no": pc_no,
                        "device": device,
                    }
                )
            desk_list.append({"desk_no": desk_no, "pcs": pc_list})
        classroom.append({"row_no": row_no, "desks": desk_list})

    return render_template(
        "index.html",
        devices=devices,
        rooms=rooms,
        selected_room=selected_room,
        classroom=classroom,
        admin_mode=admin_mode,
        admin_result=admin_result,
    )


@app.route("/admin_mode", methods=["POST"])
def admin_mode_toggle():
    current_room = (request.form.get("room") or DEFAULT_ROOM).strip() or DEFAULT_ROOM
    desired = (request.form.get("enabled") or "").strip()
    session["admin_mode"] = desired == "1"
    return redirect(url_for("index", room=current_room))


@app.route("/assign/<int:device_id>", methods=["POST"])
def assign(device_id):
    room = (request.form.get("room") or "").strip()
    row_no = to_int_or_none(request.form.get("row_no"))
    desk_no = to_int_or_none(request.form.get("desk_no"))
    pc_no = to_int_or_none(request.form.get("pc_no"))

    if not room:
        room = None

    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE devices
                SET room = ?, row_no = ?, desk_no = ?, pc_no = ?
                WHERE id = ?
                """,
                (room, row_no, desk_no, pc_no, device_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    target_room = room if room else DEFAULT_ROOM
    return redirect(f"/?room={target_room}")


@app.route("/admin/remove_assignment/<int:device_id>", methods=["POST"])
def admin_remove_assignment(device_id):
    if not is_admin_mode():
        return jsonify({"ok": False, "error": "Admin mode is required"}), 403

    current_room = (request.form.get("room") or DEFAULT_ROOM).strip() or DEFAULT_ROOM

    with get_conn() as conn:
        c = conn.cursor()
        device = c.execute(
            """
            SELECT id, ip, client_control_port, client_control_token
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

    if not device:
        return redirect(url_for("index", room=current_room))

    _, ip, control_port, control_token = device
    ok, _ = send_delete_config_command(ip, control_port, control_token)

    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE devices
                SET room = NULL, row_no = NULL, desk_no = NULL, pc_no = NULL
                WHERE id = ?
                """,
                (device_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return redirect(
        url_for("index", room=current_room, admin_result="ok" if ok else "partial")
    )


@app.route("/api/client/heartbeat", methods=["POST"])
def client_heartbeat():
    payload = request.get_json(silent=True) or {}

    room = (payload.get("room") or "").strip() or DEFAULT_ROOM
    row_no = to_int_or_none(payload.get("row_no"))
    desk_no = to_int_or_none(payload.get("desk_no"))
    pc_no = to_int_or_none(payload.get("pc_no"))

    ip = (payload.get("ip") or "").strip() or get_request_ip(request)
    if not ip:
        return jsonify({"ok": False, "error": "IP is required"}), 400

    name = (payload.get("name") or "").strip() or f"Student PC {ip}"
    client_version = (payload.get("client_version") or "").strip() or "unknown"
    client_control_port = to_int_or_none(payload.get("client_control_port"))
    client_control_token = (payload.get("client_control_token") or "").strip() or None

    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO devices (
                    name, ip, type, room, row_no, desk_no, pc_no,
                    last_heartbeat, client_version, client_control_port, client_control_token
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?)
                ON CONFLICT(ip) DO UPDATE SET
                    name = excluded.name,
                    type = excluded.type,
                    room = excluded.room,
                    row_no = excluded.row_no,
                    desk_no = excluded.desk_no,
                    pc_no = excluded.pc_no,
                    last_heartbeat = datetime('now'),
                    client_version = excluded.client_version,
                    client_control_port = excluded.client_control_port,
                    client_control_token = excluded.client_control_token
                """,
                (
                    name,
                    ip,
                    "student_client",
                    room,
                    row_no,
                    desk_no,
                    pc_no,
                    client_version,
                    client_control_port,
                    client_control_token,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return jsonify({"ok": True, "ip": ip})


@app.route("/scan_status")
def status():
    return jsonify(scan_status)


@app.route("/refresh_ping")
def refresh_ping():
    if scan_status["running"]:
        return jsonify({"ok": False, "error": "Full scan is running"}), 409
    if not ping_status["running"]:
        threading.Thread(target=background_ping_refresh, daemon=True).start()
    return jsonify({"ok": True, "running": ping_status["running"]})


@app.route("/ping_status")
def ping_status_route():
    return jsonify(ping_status)


@app.route("/history/<int:id>")
def history(id):
    with get_conn() as conn:
        c = conn.cursor()

        rows = c.execute(
            """
            SELECT response_time, timestamp FROM checks WHERE device_id=?
            """,
            (id,),
        ).fetchall()

    times = [r[0] for r in rows]

    graph_path = f"static/graph_{id}.png"

    plt.clf()
    plt.plot(times)
    plt.savefig(graph_path)

    return render_template("history.html", rows=rows, graph=f"/{graph_path}")


@app.route("/export/<int:id>")
def export(id):
    with get_conn() as conn:
        c = conn.cursor()
        rows = c.execute("SELECT * FROM checks WHERE device_id=?", (id,)).fetchall()

    filename = f"export_device_{id}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    return send_file(filename, as_attachment=True)


if __name__ == "__main__":
    # Listen on all network interfaces so classroom clients can connect.
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
