from flask import Flask, render_template, redirect, jsonify, send_file
import threading
import csv
import matplotlib.pyplot as plt

from database import init_db, get_conn
from scanner import scan_full_network
from monitor import run_checks
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
init_db()

# --- Scheduler ---
scheduler = BackgroundScheduler()
scheduler.add_job(run_checks, 'interval', seconds=60, max_instances=1, coalesce=True)
scheduler.start()

# --- Scan status ---
scan_status = {"running": False, "progress": 0, "total": 1}


def background_scan():
    global scan_status
    scan_status["running"] = True

    def progress(done, total):
        scan_status["progress"] = done
        scan_status["total"] = total

    hosts = scan_full_network(progress)

    # --- Запись одним блоком
    with get_conn() as conn:
        c = conn.cursor()

        for ip in hosts:
            c.execute("""
            INSERT OR IGNORE INTO devices (name, ip, type)
            VALUES (?, ?, ?)
            """, (f"Auto {ip}", ip, "auto"))

    scan_status["running"] = False


@app.route("/")
def index():
    with get_conn() as conn:
        c = conn.cursor()

        devices = c.execute("SELECT * FROM devices").fetchall()

        data = []
        for d in devices:
            last = c.execute("""
            SELECT status, response_time FROM checks
            WHERE device_id=? ORDER BY id DESC LIMIT 1
            """, (d[0],)).fetchone()

            data.append((d, last))

    return render_template("index.html", data=data)


@app.route("/scan")
def scan():
    if not scan_status["running"]:
        threading.Thread(target=background_scan).start()
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

    plt.clf()
    plt.plot(times)
    plt.savefig("static/graph.png")

    return render_template("history.html", rows=rows, graph="/static/graph.png")


@app.route("/export/<int:id>")
def export(id):
    with get_conn() as conn:
        c = conn.cursor()
        rows = c.execute("SELECT * FROM checks WHERE device_id=?", (id,)).fetchall()

    with open("export.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    return send_file("export.csv", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)