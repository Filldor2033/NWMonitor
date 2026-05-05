import subprocess
import platform
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_THREADS = 300


def ping(ip):
    param = "-n" if platform.system().lower() == "windows" else "-c"

    try:
        result = subprocess.run(
            ["ping", param, "1", "-w", "300", str(ip)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return str(ip) if result.returncode == 0 else None
    except:
        return None


def scan_full_network(progress_callback=None):
    network = ipaddress.ip_network("192.168.0.0/16", strict=False)

    alive = []
    total = network.num_addresses
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = {executor.submit(ping, ip): ip for ip in network.hosts()}

        for f in as_completed(futures):
            done += 1

            res = f.result()
            if res:
                alive.append(res)

            if progress_callback:
                progress_callback(done, total)

    return alive