import traceback
import threading
import csv
import matplotlib.pyplot as plt

from database import init_db, get_conn
from scanner import scan_full_network
from monitor import run_checks
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template, redirect, jsonify, send_file

app = Flask(__name__)
init_db()

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.add_job(run_checks, 'interval', seconds=60, max_instances=1, coalesce=True)
scheduler.start()

# --- Scan status ---
scan_status = {
    "running": False,
    "progress": 0,
    "total": 1,
    "found": 0,
    "error": None
}


def background_scan():
    global scan_status

    scan_status["running"] = True
    scan_status["progress"] = 0
    scan_status["total"] = 1
    scan_status["found"] = 0
    scan_status["error"] = None

    try:
        def progress(done, total):
            scan_status["progress"] = done
            scan_status["total"] = total

        hosts = scan_full_network(progress)
        scan_status["found"] = len(hosts)

        rows = [(f"Auto {ip}", ip, "auto") for ip in hosts]

        if rows:
            with get_conn() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")

                    conn.executemany("""
                    INSERT OR IGNORE INTO devices (name, ip, type)
                    VALUES (?, ?, ?)
                    """, rows)

                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    except Exception as e:
        scan_status["error"] = str(e)
        traceback.print_exc()

    finally:
        scan_status["running"] = False


@app.route("/")
def index():
    with get_conn() as conn:
        c = conn.cursor()

        rows = c.execute("""
        SELECT 
            d.id, d.name, d.ip, d.type,
            c.status, c.response_time
        FROM devices d
        LEFT JOIN checks c ON c.id = (
            SELECT id FROM checks
            WHERE device_id = d.id
            ORDER BY id DESC
            LIMIT 1
        )
        ORDER BY d.id
        """).fetchall()

        data = []
        for r in rows:
            device = r[:4]
            last = r[4:] if r[4] is not None else None
            data.append((device, last))

    return render_template("index.html", data=data)


@app.route("/scan")
def scan():
    if not scan_status["running"]:
        threading.Thread(target=background_scan, daemon=True).start()
    return redirect("/")


@app.route("/scan_status")
def status():
    return jsonify(scan_status)


@app.route("/history/<int:id>")
def history(id):
    with get_conn() as conn:
        c = conn.cursor()

        rows = c.execute("""
        SELECT response_time, timestamp FROM checks WHERE device_id=?
        """, (id,)).fetchall()

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
    app.run(debug=True, use_reloader=False)