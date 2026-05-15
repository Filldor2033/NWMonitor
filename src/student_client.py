import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

DEFAULT_INTERVAL = 30
DEFAULT_CONTROL_PORT = 8765
CLIENT_VERSION = "1.1.0"
TASK_NAME = "NWMonitorStudentClient"
_LOCK_HANDLE = None


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_config_path():
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "NWMonitor" / "client_config.json"
        return Path.home() / "AppData" / "Roaming" / "NWMonitor" / "client_config.json"
    return app_dir() / "client_config.json"


CONFIG_PATH = default_config_path()


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


def setup_wizard_window(existing=None):
    existing = existing or {}
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return None

    defaults = {
        "server_url": existing.get("server_url", "http://127.0.0.1:5000"),
        "room": existing.get("room", "3"),
        "row_no": existing.get("row_no", 1),
        "desk_no": existing.get("desk_no", 1),
        "pc_no": existing.get("pc_no", 1),
        "name": existing.get("name", socket.gethostname()),
        "interval": existing.get("interval", DEFAULT_INTERVAL),
        "client_control_port": existing.get(
            "client_control_port", DEFAULT_CONTROL_PORT
        ),
    }

    field_specs = [
        ("server_url", "URL сервера"),
        ("room", "Аудитория"),
        ("row_no", "Ряд (1-2)"),
        ("desk_no", "Парта (1-4)"),
        ("pc_no", "Компьютер (1 или 2)"),
        ("name", "Имя компьютера"),
        ("interval", "Интервал heartbeat (сек)"),
        ("client_control_port", "Порт admin-команд"),
    ]

    root = tk.Tk()
    root.title("NWMonitor - первичная настройка")
    root.resizable(False, False)

    frame = tk.Frame(root, padx=14, pady=14)
    frame.pack(fill="both", expand=True)

    entries = {}
    for idx, (key, label) in enumerate(field_specs):
        tk.Label(frame, text=label, anchor="w").grid(
            row=idx, column=0, sticky="w", pady=3
        )
        entry = tk.Entry(frame, width=42)
        entry.grid(row=idx, column=1, pady=3, padx=(10, 0))
        entry.insert(0, str(defaults[key]))
        entries[key] = entry

    result = {"config": None}

    def on_save():
        raw = {k: e.get().strip() for k, e in entries.items()}

        try:
            row_no = int(raw["row_no"])
            desk_no = int(raw["desk_no"])
            pc_no = int(raw["pc_no"])
            interval = max(5, int(raw["interval"]))
            control_port = int(raw["client_control_port"])
        except ValueError:
            messagebox.showerror("Ошибка", "Числовые поля заполнены некорректно.")
            return

        if not raw["server_url"]:
            messagebox.showerror("Ошибка", "Укажите URL сервера.")
            return

        if not raw["name"]:
            messagebox.showerror("Ошибка", "Укажите имя компьютера.")
            return

        result["config"] = {
            "server_url": raw["server_url"].rstrip("/"),
            "room": raw["room"] or "3",
            "row_no": row_no,
            "desk_no": desk_no,
            "pc_no": pc_no,
            "name": raw["name"],
            "interval": interval,
            "client_control_port": control_port,
            "client_control_token": existing.get("client_control_token")
            or secrets.token_hex(16),
        }
        root.destroy()

    def on_cancel():
        root.destroy()

    buttons = tk.Frame(frame, pady=8)
    buttons.grid(row=len(field_specs), column=0, columnspan=2, sticky="e")
    tk.Button(buttons, text="Сохранить", width=12, command=on_save).pack(
        side="left", padx=(0, 8)
    )
    tk.Button(buttons, text="Отмена", width=12, command=on_cancel).pack(side="left")

    root.mainloop()
    return result["config"]


def load_config():
    if not CONFIG_PATH.exists():
        return None

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
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


def pythonw_path():
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        return current

    candidate = current.with_name("pythonw.exe")
    if candidate.exists():
        return candidate
    return current


def notify_error(title, message):
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x10)
            return
        except Exception:
            pass
    log(f"{title}: {message}")


def acquire_instance_lock():
    global _LOCK_HANDLE
    lock_path = CONFIG_PATH.with_name("student_client.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        import msvcrt

        handle = open(lock_path, "a+")
        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        _LOCK_HANDLE = handle
        return True

    _LOCK_HANDLE = open(lock_path, "a+")
    return True


def task_run_command():
    if is_frozen():
        return f'"{Path(sys.executable).resolve()}" --background'
    script_path = Path(__file__).resolve()
    python_exe = pythonw_path()
    return f'"{python_exe}" "{script_path}" --background'


def install_startup_task():
    task_command = task_run_command()

    cmd = [
        "schtasks",
        "/Create",
        "/SC",
        "ONLOGON",
        "/TN",
        TASK_NAME,
        "/TR",
        task_command,
        "/RL",
        "LIMITED",
        "/F",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return True, "Задача автозапуска в Планировщике задач настроена."

    err = (result.stderr or result.stdout or "").strip()
    return False, f"Не удалось создать задачу в Планировщике задач: {err}"


def startup_task_exists():
    cmd = ["schtasks", "/Query", "/TN", TASK_NAME]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0


def ensure_startup_task():
    if os.name != "nt":
        return True, "Планировщик задач поддерживается только на Windows."
    if startup_task_exists():
        return True, "Задача автозапуска уже есть в Планировщике задач."
    return install_startup_task()


def launch_background_instance():
    if is_frozen():
        command = [sys.executable, "--background"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--background"]
    env = dict(os.environ)
    env["NWMONITOR_BG"] = "1"

    kwargs = {
        "cwd": str(app_dir()),
        "env": env,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )

    subprocess.Popen(command, **kwargs)


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
        "online": True,
    }

    response = requests.post(url, json=payload, timeout=5)
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Server error: {data}")

    return data


def start_control_server(config):
    token = config.get("client_control_token")
    preferred_port = int(config.get("client_control_port", DEFAULT_CONTROL_PORT))

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

    server = None
    selected_port = None
    for candidate_port in range(preferred_port, preferred_port + 20):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", candidate_port), ControlHandler)
            selected_port = candidate_port
            break
        except OSError:
            continue

    if server is None:
        log(
            "Не удалось запустить admin control server: "
            "порт занят (диапазон проверки исчерпан)."
        )
        return False

    if selected_port != preferred_port:
        config["client_control_port"] = selected_port
        save_config(config)
        log(f"Порт admin control изменён: {preferred_port} -> {selected_port}")

    def serve():
        log(f"Admin control server started on port {selected_port}")
        server.serve_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return True


def main():
    parser = argparse.ArgumentParser(description="Student client for NWMonitor")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument(
        "--once", action="store_true", help="Send one heartbeat and exit"
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Run in background mode (used by Task Scheduler)",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Force run in foreground mode",
    )
    args = parser.parse_args()

    if not acquire_instance_lock():
        log("Клиент уже запущен в системе. Повторный запуск пропущен.")
        return

    config = load_config()
    first_run = config is None

    if args.setup or first_run:
        config = setup_wizard_window(existing=config)
        if config is None:
            if is_frozen():
                notify_error(
                    "NWMonitor",
                    "Не удалось открыть окно первичной настройки. "
                    "Проверьте сборку exe (должен быть доступен tkinter).",
                )
                return
            config = setup_wizard(existing=config)
        if not config:
            log("Настройка отменена пользователем.")
            return

        try:
            save_config(config)
        except Exception as e:
            notify_error(
                "NWMonitor",
                f"Не удалось сохранить конфиг:\n{CONFIG_PATH}\n\n{e}",
            )
            return
        log(f"Конфиг сохранён: {CONFIG_PATH}")

    ensure_control_fields(config)
    ok, task_message = ensure_startup_task()
    log(task_message)
    if not ok:
        if args.setup or first_run:
            notify_error("NWMonitor", task_message)
        log(
            "Подсказка: запустите программу от имени пользователя с правом "
            "добавления задач или создайте задачу вручную."
        )

    interval = int(config.get("interval", DEFAULT_INTERVAL))
    interval = max(interval, 5)

    should_daemonize = (
        not args.once
        and not args.setup
        and not args.background
        and not args.foreground
        and not first_run
        and os.environ.get("NWMONITOR_BG") != "1"
    )
    if should_daemonize:
        launch_background_instance()
        return

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
