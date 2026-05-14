import argparse
import json
import secrets
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

CONFIG_PATH = Path(__file__).with_name("client_config.json")
DEFAULT_INTERVAL = 30
DEFAULT_CONTROL_PORT = 8765
CLIENT_VERSION = "1.1.0"


def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return ""
    finally:
        sock.close()


def ask(prompt, default=None, caster=str):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        value = raw if raw else default

        try:
            if caster is int and value is not None:
                value = int(value)
            elif caster is str and value is not None:
                value = str(value).strip()
            return value
        except ValueError:
            print("Некорректное значение, попробуйте снова.")


def setup_wizard(existing=None):
    existing = existing or {}

    print("Первичная настройка клиента мониторинга")
    server_url = ask(
        "URL сервера (пример: http://192.168.1.10:5000)",
        default=existing.get("server_url", "http://127.0.0.1:5000"),
        caster=str,
    )

    room = ask("Аудитория", default=existing.get("room", "3"), caster=str)
    row_no = ask("Ряд (1-2)", default=existing.get("row_no", 1), caster=int)
    desk_no = ask("Парта (1-4)", default=existing.get("desk_no", 1), caster=int)
    pc_no = ask(
        "Компьютер (1=слева, 2=справа)", default=existing.get("pc_no", 1), caster=int
    )
    name = ask(
        "Имя компьютера",
        default=existing.get("name", socket.gethostname()),
        caster=str,
    )
    interval = ask(
        "Интервал отправки heartbeat (сек)",
        default=existing.get("interval", DEFAULT_INTERVAL),
        caster=int,
    )
    control_port = ask(
        "Порт для admin-команд",
        default=existing.get("client_control_port", DEFAULT_CONTROL_PORT),
        caster=int,
    )

    token = existing.get("client_control_token") or secrets.token_hex(16)

    return {
        "server_url": server_url.rstrip("/"),
        "room": room,
        "row_no": row_no,
        "desk_no": desk_no,
        "pc_no": pc_no,
        "name": name,
        "interval": interval,
        "client_control_port": control_port,
        "client_control_token": token,
    }


def load_config():
    if not CONFIG_PATH.exists():
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def ensure_control_fields(config):
    changed = False

    if not config.get("client_control_port"):
        config["client_control_port"] = DEFAULT_CONTROL_PORT
        changed = True

    if not config.get("client_control_token"):
        config["client_control_token"] = secrets.token_hex(16)
        changed = True

    if changed:
        save_config(config)


def send_heartbeat(config):
    url = config["server_url"].rstrip("/") + "/api/client/heartbeat"
    payload = {
        "name": config.get("name") or socket.gethostname(),
        "ip": get_local_ip(),
        "room": config.get("room", "3"),
        "row_no": config.get("row_no"),
        "desk_no": config.get("desk_no"),
        "pc_no": config.get("pc_no"),
        "client_version": CLIENT_VERSION,
        "client_control_port": config.get("client_control_port"),
        "client_control_token": config.get("client_control_token"),
        "online": True,  # explicit liveness flag carried in every heartbeat
    }

    response = requests.post(url, json=payload, timeout=5)
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Server error: {data}")

    return data


def start_control_server(config):
    token = config.get("client_control_token")
    port = int(config.get("client_control_port", DEFAULT_CONTROL_PORT))

    class ControlHandler(BaseHTTPRequestHandler):
        def _json_response(self, code, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/admin/delete_config":
                self._json_response(404, {"ok": False, "error": "Not found"})
                return

            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "Invalid JSON"})
                return

            if payload.get("token") != token:
                self._json_response(403, {"ok": False, "error": "Invalid token"})
                return

            try:
                if CONFIG_PATH.exists():
                    CONFIG_PATH.unlink()
                self._json_response(
                    200, {"ok": True, "message": "client_config.json deleted"}
                )
            except Exception as e:
                self._json_response(500, {"ok": False, "error": str(e)})

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), ControlHandler)

    def serve():
        log(f"Admin control server started on port {port}")
        server.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()


def main():
    parser = argparse.ArgumentParser(description="Student client for NWMonitor")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument(
        "--once", action="store_true", help="Send one heartbeat and exit"
    )
    args = parser.parse_args()

    config = load_config()

    if args.setup or not config:
        config = setup_wizard(existing=config)
        save_config(config)
        log(f"Конфиг сохранён: {CONFIG_PATH}")

    ensure_control_fields(config)

    interval = int(config.get("interval", DEFAULT_INTERVAL))
    interval = max(interval, 5)

    if not args.once:
        start_control_server(config)

    if args.once:
        data = send_heartbeat(config)
        log(f"Heartbeat отправлен. IP на сервере: {data.get('ip')}")
        return

    log("Клиент запущен. Для остановки нажмите Ctrl+C.")
    while True:
        try:
            data = send_heartbeat(config)
            log(
                "Heartbeat OK | "
                f"ауд. {config.get('room')}, ряд {config.get('row_no')}, "
                f"парта {config.get('desk_no')}, ПК {config.get('pc_no')} | "
                f"IP: {data.get('ip')}"
            )
        except Exception as e:
            log(f"Ошибка отправки heartbeat: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()