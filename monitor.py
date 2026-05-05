import subprocess
import platform
import time
from datetime import datetime
from database import get_conn


def ping(ip):
    param = "-n" if platform.system().lower() == "windows" else "-c"

    start = time.time()
    result = subprocess.run(
        ["ping", param, "1", ip],
        stdout=subprocess.DEVNULL
    )
    end = time.time()

    if result.returncode == 0:
        return True, (end - start) * 1000
    return False, None


def run_checks():
    # --- 1. Быстро читаем устройства
    with get_conn() as conn:
        c = conn.cursor()
        devices = c.execute("SELECT * FROM devices").fetchall()

    # --- 2. Проверяем вне БД
    results = []
    for d in devices:
        device_id, name, ip, _ = d
        alive, rt = ping(ip)
        results.append((device_id, alive, rt))

    # --- 3. Быстро записываем
    with get_conn() as conn:
        c = conn.cursor()

        for device_id, alive, rt in results:
            c.execute("""
            INSERT INTO checks (device_id, status, response_time, timestamp)
            VALUES (?, ?, ?, datetime('now'))
            """, (device_id, int(alive), rt or 0))

            if not alive:
                c.execute("""
                INSERT INTO alerts (device_id, message, timestamp)
                VALUES (?, ?, datetime('now'))
                """, (device_id, "DOWN"))

            elif rt and rt > 100:
                c.execute("""
                INSERT INTO alerts (device_id, message, timestamp)
                VALUES (?, ?, datetime('now'))
                """, (device_id, "HIGH LATENCY"))