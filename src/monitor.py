import asyncio
import concurrent.futures
import ping3

from database import get_conn

MAX_CHECK_CONCURRENCY = 256
PING_TIMEOUT_SEC = 1

EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CHECK_CONCURRENCY)


async def ping_async(ip):
    loop = asyncio.get_running_loop()
    try:
        delay_sec = await loop.run_in_executor(
            EXECUTOR,  # используем наш пул
            lambda: ping3.ping(ip, timeout=PING_TIMEOUT_SEC),
        )
        if delay_sec is None:
            return False, None
        return True, delay_sec * 1000
    except Exception:
        return False, None


async def check_device_async(d):
    device_id, _name, ip, _device_type = d[:4]
    if not ip:
        return device_id, False, None

    alive, rt = await ping_async(ip)
    return device_id, alive, rt


async def run_checks_async(progress_callback=None):
    with get_conn() as conn:
        c = conn.cursor()
        devices = c.execute("SELECT id, name, ip, type FROM devices").fetchall()

    if not devices:
        return

    sem = asyncio.Semaphore(MAX_CHECK_CONCURRENCY)

    async def guarded_check(device):
        async with sem:
            return await check_device_async(device)

    tasks = [asyncio.create_task(guarded_check(d)) for d in devices]

    results = []
    total = len(tasks)
    done = 0

    for finished in asyncio.as_completed(tasks):
        results.append(await finished)
        done += 1
        if progress_callback:
            progress_callback(done, total)

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

            conn.executemany(
                """
                INSERT INTO checks (device_id, status, response_time, timestamp)
                VALUES (?, ?, ?, datetime('now'))
                """,
                check_rows,
            )

            conn.executemany(
                """
                INSERT INTO alerts (device_id, message, timestamp)
                VALUES (?, ?, datetime('now'))
                """,
                alert_rows,
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise


def run_checks(progress_callback=None):
    asyncio.run(run_checks_async(progress_callback=progress_callback))
