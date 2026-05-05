import subprocess
import platform
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from database import get_conn


MAX_CHECK_THREADS = 32


def ping(ip):
    system = platform.system().lower()

    try:
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", "500", ip]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", ip]

        start = time.time()

        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2
        )

        end = time.time()

        if result.returncode == 0:
            return True, (end - start) * 1000

        return False, None

    except Exception:
        return False, None


def check_device(d):
    device_id, name, ip, device_type = d
    alive, rt = ping(ip)
    return device_id, alive, rt


def run_checks():
    with get_conn() as conn:
        c = conn.cursor()
        devices = c.execute("SELECT * FROM devices").fetchall()

    if not devices:
        return

    results = []

    with ThreadPoolExecutor(max_workers=MAX_CHECK_THREADS) as executor:
        futures = [executor.submit(check_device, d) for d in devices]

        for f in as_completed(futures):
            results.append(f.result())

    check_rows = []
    alert_rows = []

    for device_id, alive, rt in results:
        check_rows.append((device_id, int(alive), rt or 0))

        if not alive:
            alert_rows.append((device_id, "DOWN"))
        elif rt and rt > 100:
            alert_rows.append((device_id, "HIGH LATENCY"))

    with get_conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            conn.executemany("""
            INSERT INTO checks (device_id, status, response_time, timestamp)
            VALUES (?, ?, ?, datetime('now'))
            """, check_rows)

            conn.executemany("""
            INSERT INTO alerts (device_id, message, timestamp)
            VALUES (?, ?, datetime('now'))
            """, alert_rows)

            conn.commit()
        except Exception:
            conn.rollback()
            raise