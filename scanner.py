import subprocess
import platform
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_THREADS = 64


def ping(ip):
    ip = str(ip)
    system = platform.system().lower()

    try:
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", "300", ip]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", ip]

        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2
        )

        return ip if result.returncode == 0 else None

    except Exception:
        return None


def scan_full_network(progress_callback=None):
    network = ipaddress.ip_network("192.168.1.0/16", strict=False)

    alive = []
    hosts = list(network.hosts())
    total = len(hosts)
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = [executor.submit(ping, ip) for ip in hosts]

        for f in as_completed(futures):
            done += 1

            res = f.result()
            if res:
                alive.append(res)

            if progress_callback:
                progress_callback(done, total)

    return alive