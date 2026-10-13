"""Nautilus radial dashboard: sensors, quota reader, script control and LCD output."""
import argparse
import atexit
import copy
import json
import logging
import os
import pathlib
import queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import hid
import psutil

from dashboard_core import DEFAULT_CONFIG, encode_image, image_packets, merge_config, render_frame, select_quotas, select_sensors
from codex_activity import CodexActivityMonitor

ROOT = pathlib.Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class ControlServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR allows two listeners to fight over the same port.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class RpcClient:
    def __init__(self, executable):
        command = executable if isinstance(executable,list) else [executable]
        self.process = subprocess.Popen([*command, "app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=CREATE_FLAGS)
        self.messages = queue.Queue()
        self.next_id = 0
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "nautilus_dashboard", "version": "1.0.0"}})
            self.send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass
        self.messages.put({"closed": True})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params=None):
        with self.lock:
            self.next_id += 1
            request = {"id": self.next_id, "method": method}
            if params is not None:
                request["params"] = params
            self.send(request)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    response = self.messages.get(timeout=1)
                except queue.Empty:
                    continue
                if response.get("closed"):
                    raise RuntimeError("Codex exited. Check runtime/dashboard.log and launch outside the sandbox.")
                if response.get("id") == self.next_id:
                    if "error" in response:
                        raise RuntimeError(response["error"].get("message", "Codex request failed"))
                    return response.get("result", {})
            raise TimeoutError("Codex quota request timed out")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()


def find_codex():
    executable = shutil.which("codex")
    if executable and pathlib.Path(executable).suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return executable
    if executable:
        # npm's Windows launcher is a CMD file, not a native executable. Run its
        # JS entrypoint with Node directly, without interpolating a shell command.
        entrypoint = pathlib.Path(executable).parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if node and entrypoint.is_file():
            return [node,str(entrypoint)]
    matches = []
    for directory in (".vscode/extensions", ".vscode-insiders/extensions"):
        matches.extend((pathlib.Path.home() / directory).glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return str(matches[0])
    raise RuntimeError("Codex is missing. Install the Codex CLI or the OpenAI VS Code extension.")


def icue_running():
    return any(p.info["name"] and p.info["name"].lower() == "icue.exe"
               for p in psutil.process_iter(["name"]))


class Dashboard:
    def __init__(self, demo=False):
        RUNTIME.mkdir(exist_ok=True)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config_path = ROOT / "settings.json"
        self.lock = threading.RLock()
        self._config_signature = None
        if not self.config_path.exists():
            example = ROOT / "settings.example.json"
            initial = merge_config(json.loads(example.read_text(encoding="utf-8-sig"))) if example.exists() else self.config
            self.config_path.write_text(json.dumps(initial,indent=2) + "\n",encoding="utf-8")
        self.reload_config()
        self.lcd_lock = threading.Lock()
        self.stop = threading.Event()
        self.quota_wake = threading.Event()
        self.sensor = None
        self.rpc = None
        self.lcd = None
        self.token = secrets.token_urlsafe(32)
        self.elevated_file = RUNTIME / ("sensors-" + str(os.getpid()) + ".json")
        self.state = {"demo": demo, "hardware": {}, "quotas": {"five_hour":None,"weekly":None},
            "codex_activity": {"status": "unknown", "active_sessions": 0},
            "pid": os.getpid(),
            "project_root": str(ROOT),
            "hardware_status": "Starting sensors…", "quota_status": "Connecting to Codex…",
            "quota_stale": False, "lcd_status": "LCD off", "frames_sent": 0,
            "hardware_updated": None, "quota_updated": None}
        if demo:
            self.state.update(hardware=dict(cpu_temp=52,cpu_load=24,gpu_temp=61,gpu_load=78),
                quotas={"five_hour":{"used":60,"resets_at":time.time()+8040},
                        "weekly":{"used":40,"resets_at":time.time()+280800}},
                hardware_status="Sample readings",quota_status="Sample quotas")
        else:
            threading.Thread(target=self._sensors, daemon=True).start()
            threading.Thread(target=self._quotas, daemon=True).start()
            threading.Thread(target=self._activity, daemon=True).start()
        threading.Thread(target=self._frames, daemon=True).start()

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            config = copy.deepcopy(self.config)
        age = time.time() - (state["hardware_updated"] or time.time())
        if not state["demo"] and age > 5:
            for key in ("cpu_temp","cpu_load","gpu_temp","gpu_load"):
                state["hardware"][key] = None
            state["hardware_status"] = "Sensor readings are stale; reconnecting…"
        state["icue_running"] = icue_running() if not state["demo"] else False
        return state, config

    def update_config(self, patch):
        with self.lock:
            self.config = merge_config(patch,self.config)
            temp = self.config_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.config,indent=2),encoding="utf-8")
            temp.replace(self.config_path)
        if patch.get("lcd_enabled") is False:
            self.close_lcd()

    def reload_config(self):
        try:
            stat = self.config_path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self._config_signature:
                return
            self._config_signature = signature
            config = merge_config(json.loads(self.config_path.read_text(encoding="utf-8-sig")))
            with self.lock:
                self.config = config
        except FileNotFoundError:
            pass
        except (OSError,ValueError,TypeError) as error:
            logging.warning("Invalid settings.json; keeping previous settings: %s",error)

    def _sensors(self):
        bridge = ROOT / "vendor/LibreHardwareMonitor/SensorBridge.exe"
        def elevated_sample():
            try:
                if time.time() - self.elevated_file.stat().st_mtime > 5:
                    return None
                sample = json.loads(self.elevated_file.read_text(encoding="utf-8"))
                return sample if sample.get("parent_pid") == os.getpid() else None
            except (OSError,ValueError):
                return None
        def update(message):
            readings = select_sensors(message.get("sensors",[]))
            with self.lock:
                self.state["hardware"] = readings
                self.state["hardware_updated"] = time.time()
                self.state["hardware_status"] = "Live sensors" if readings["cpu_temp"] is not None else "CPU temperature needs driver setup and administrator access to the sensor helper."
        while not self.stop.is_set():
            try:
                message = elevated_sample()
                if message:
                    update(message)
                    self.stop.wait(1)
                    continue
                self.sensor = subprocess.Popen([str(bridge)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                    text=True,encoding="utf-8",creationflags=CREATE_FLAGS)
                for line in self.sensor.stdout:
                    if self.stop.is_set():
                        break
                    message = json.loads(line)
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    elevated = elevated_sample()
                    update(elevated or message)
                    if elevated:
                        break
                if not self.stop.is_set() and not elevated_sample():
                    raise RuntimeError("Sensor reader stopped")
            except Exception as error:
                logging.warning("Sensor reader: %s", error)
                with self.lock:
                    self.state["hardware_status"] = str(error)
            finally:
                if self.sensor and self.sensor.poll() is None:
                    self.sensor.terminate()
                    self.sensor.wait(timeout=5)
                self.sensor = None
            self.stop.wait(3)

    def _quotas(self):
        while not self.stop.is_set():
            try:
                if self.rpc is None:
                    self.rpc = RpcClient(find_codex())
                account = self.rpc.call("account/read", {"refreshToken":False}).get("account")
                if not account or account.get("type") != "chatgpt":
                    raise RuntimeError("Sign in to Codex with your ChatGPT account, then refresh.")
                quotas = select_quotas(self.rpc.call("account/rateLimits/read"))
                with self.lock:
                    self.state.update(quotas=quotas,quota_updated=time.time(),quota_stale=False,
                        quota_status="Live Codex / ChatGPT Work quotas" if any(quotas.values()) else "Your account did not return five-hour or weekly quotas.")
            except Exception as error:
                logging.warning("Quota reader: %s",error)
                if self.rpc:
                    self.rpc.close()
                    self.rpc = None
                with self.lock:
                    self.state.update(quota_stale=True,quota_status=str(error))
            self.quota_wake.wait(60)
            self.quota_wake.clear()

    def _activity(self):
        monitor = CodexActivityMonitor()
        while not self.stop.is_set():
            with self.lock:
                settings = self.config["codex_activity"].copy()
            if settings["enabled"]:
                try:
                    activity = monitor.poll()
                except Exception:
                    logging.exception("Could not read Codex activity")
                    activity = {"status": "unknown", "active_sessions": 0}
                with self.lock:
                    self.state["codex_activity"] = activity
            self.stop.wait(settings["poll_interval_seconds"])

    def _frames(self):
        last_attempt = 0
        while not self.stop.is_set():
            try:
                self.reload_config()
                state, config = self.snapshot()
                image = render_frame(state,config)
                if config["rotation"]:
                    image = image.rotate(-config["rotation"])
                png, jpeg = encode_image(image), encode_image(image,"JPEG")
                with self.lock:
                    self.png, self.jpeg = png, jpeg
                if config["lcd_enabled"] and not state["demo"]:
                    with self.lcd_lock:
                        if icue_running():
                            self._release_lcd()
                            with self.lock:
                                self.state["lcd_status"] = "iCUE owns the screen. Quit iCUE from its tray icon, then connect."
                        else:
                            if self.lcd is None and time.monotonic() - last_attempt >= 5:
                                last_attempt = time.monotonic()
                                self._open_lcd()
                            if self.lcd:
                                brightness = bytes([3,11,config["brightness"],1])
                                if getattr(self,"_brightness",None) != brightness:
                                    self.lcd.send_feature_report(brightness)
                                    self._brightness = brightness
                                for packet in image_packets(jpeg):
                                    if self.lcd.write(packet) != len(packet):
                                        raise IOError("Incomplete LCD frame write")
                                with self.lock:
                                    self.state["frames_sent"] += 1
                                    self.state["lcd_status"] = "Live on Nautilus LCD"
                elif not config["lcd_enabled"]:
                    self.close_lcd()
            except Exception as error:
                logging.warning("LCD: %s",error)
                self.close_lcd()
                with self.lock:
                    self.state["lcd_status"] = "LCD unavailable: " + str(error)
            self.stop.wait(1)

    def _open_lcd(self):
        devices = [d for d in hid.enumerate(0x1b1c) if d["product_id"] in (0x0c55,0x0c57)]
        if not devices:
            raise RuntimeError("Nautilus cap not found; check the USB connection")
        device = hid.device()
        try:
            device.open_path(devices[0]["path"])
            device.send_feature_report(bytes([3,12,0,1]))
        except Exception:
            device.close()
            raise
        self.lcd = device
        self._brightness = None

    def _release_lcd(self):
        if self.lcd:
            try:
                for report in (bytes([3,30,1,1]),bytes([3,29,0,1])):
                    self.lcd.send_feature_report(report)
            except OSError:
                pass
            finally:
                self.lcd.close()
                self.lcd = None

    def close_lcd(self):
        with self.lcd_lock:
            self._release_lcd()
        with self.lock:
            self.state["lcd_status"] = "LCD off"

    def restart_sensors(self):
        if self.sensor and self.sensor.poll() is None:
            self.sensor.terminate()

    def close(self):
        self.stop.set()
        self.quota_wake.set()
        if self.sensor and self.sensor.poll() is None:
            self.sensor.terminate()
        if self.rpc:
            self.rpc.close()
        self.close_lcd()


def handler_for(dashboard):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def reply(self,data,kind="application/json",code=200):
            if kind == "application/json":
                data = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type",kind)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.valid_host():
                return self.reply({"error":"Invalid host"},code=403)
            path = urlparse(self.path).path
            if path == "/api/state":
                state,config = dashboard.snapshot()
                return self.reply({"state":state,"config":config,"token":dashboard.token})
            if path == "/frame.png":
                with dashboard.lock:
                    data = getattr(dashboard,"png",None)
                if data:
                    return self.reply(data,"image/png")
                return self.reply({"error":"Starting renderer"},code=503)
            return self.reply({"error":"Not found"},code=404)

        def do_POST(self):
            if not self.valid_host() or self.headers.get("X-Dashboard-Token") != dashboard.token:
                return self.reply({"error":"Invalid session"},code=403)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                return self.reply({"error":"Invalid origin"},code=403)
            try:
                length = int(self.headers.get("Content-Length",0))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/settings":
                    dashboard.update_config(payload)
                elif path == "/api/connect":
                    if not isinstance(payload,dict) or type(payload.get("close_icue",False)) is not bool:
                        raise ValueError("Invalid connect request")
                    if payload.get("close_icue"):
                        for process in psutil.process_iter(["name"]):
                            if (process.info["name"] or "").lower() == "icue.exe":
                                try:
                                    process.terminate()
                                except psutil.NoSuchProcess:
                                    pass
                    dashboard.update_config({"lcd_enabled":True})
                elif path == "/api/refresh":
                    dashboard.quota_wake.set()
                elif path == "/api/restart-sensors":
                    dashboard.restart_sensors()
                elif path == "/api/elevate-sensors":
                    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File",str(ROOT / "tools/start_sensor.ps1"),"-DashboardPid",str(os.getpid())],creationflags=CREATE_FLAGS)
                elif path == "/api/shutdown":
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:
                    return self.reply({"error":"Not found"},code=404)
                self.reply({"ok":True})
            except (ValueError,TypeError) as error:
                self.reply({"error":str(error)},code=400)
            except Exception:
                logging.exception("Control request failed")
                self.reply({"error":"Request failed; see runtime/dashboard.log"},code=500)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo",action="store_true")
    parser.add_argument("--no-browser",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--port",type=int,default=8765)
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    logging.basicConfig(filename=RUNTIME / "dashboard.log",level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",encoding="utf-8")
    try:
        server = ControlServer(("127.0.0.1",args.port),BaseHTTPRequestHandler)
    except OSError:
        return
    dashboard = Dashboard(args.demo)
    atexit.register(dashboard.close)
    server.RequestHandlerClass = handler_for(dashboard)
    try:
        server.serve_forever()
    finally:
        dashboard.close()
        server.server_close()


if __name__ == "__main__":
    main()
