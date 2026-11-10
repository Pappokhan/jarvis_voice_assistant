"""The Jarvis window: a small local web page shown in its own app window (Edge/Chrome "app mode").

The page polls this server for the assistant's state and sends typed requests and button presses back.
The server listens on 127.0.0.1 only, and every call must carry a token that only the page is given,
so a website open in a browser cannot send commands to Jarvis.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import DATA_DIR, ROOT

log = logging.getLogger("jarvis.ui")
PAGE = Path(__file__).parent / "ui" / "index.html"
PORT_FILE = DATA_DIR / "ui.port"
_BROWSERS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
)


def open_window(port: int) -> None:
    """Show the page as a chromeless app window; fall back to a normal browser tab."""
    url = f"http://127.0.0.1:{port}/"
    for candidate in _BROWSERS:
        exe = os.path.expandvars(candidate)
        if os.path.exists(exe):
            # A profile of its own makes the browser honour the window size and keeps it apart from the user's tabs.
            subprocess.Popen([exe, f"--app={url}", "--window-size=470,820", f"--user-data-dir={DATA_DIR / 'ui-profile'}",
                              "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"])
            return
    webbrowser.open(url)


def open_running_window() -> bool:
    """Used when Jarvis is started a second time: bring up the window of the copy that is already running."""
    try:
        open_window(int(PORT_FILE.read_text()))
        return True
    except (OSError, ValueError):
        return False


class UI:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.assistant = None
        self.token = secrets.token_urlsafe(24)
        self.events: deque[dict] = deque(maxlen=300)
        self.seq = 0
        self.lock = threading.Lock()
        self.stats: dict = {}
        self.boot = "Starting up"
        self.port = self._free_port(int(cfg["ui"]["port"]))
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _allowed(self) -> bool:
                host = (self.headers.get("Host") or "").split(":")[0]
                return host in ("127.0.0.1", "localhost") and secrets.compare_digest(self.headers.get("X-Jarvis-Token") or "", ui.token)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == "/":
                    html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", ui.token).replace("__NAME__", cfg["assistant"]["name"])
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/state" and self._allowed():
                    since = int(query.split("since=")[-1] or 0) if "since=" in query else 0
                    return self._send(200, json.dumps(ui.snapshot(since)).encode("utf-8"))
                self._send(404, b"{}")

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, b"{}")
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                except ValueError:
                    return self._send(400, b"{}")
                if self.path == "/ask":
                    ui.ask(str(body.get("text", ""))[:2000])
                elif self.path == "/action":
                    ui.action(str(body.get("name", "")))
                self._send(200, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="ui-server", daemon=True).start()
        threading.Thread(target=self._watch_stats, name="ui-stats", daemon=True).start()
        try:
            PORT_FILE.write_text(str(self.port))
        except OSError:
            pass
        log.info("window available at http://127.0.0.1:%d/", self.port)

    @staticmethod
    def _free_port(preferred: int) -> int:
        for port in (preferred, 0):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return s.getsockname()[1]
                except OSError:
                    continue
        return preferred

    # ---------- fed by the assistant ----------
    def attach(self, assistant) -> None:
        self.assistant = assistant
        assistant.on_event = self.push

    def push(self, kind: str, text: str = "") -> None:
        with self.lock:
            self.seq += 1
            self.events.append({"id": self.seq, "kind": kind, "text": text, "at": time.strftime("%H:%M")})

    # ---------- read by the page ----------
    def snapshot(self, since: int) -> dict:
        a = self.assistant
        with self.lock:
            events = [e for e in self.events if e["id"] > since]
        if a is None:
            return {"status": "starting", "detail": self.boot, "level": 0, "muted": False, "events": events, "stats": self.stats, "seq": self.seq}
        status = a.status
        if status in ("idle", "thinking") and a.tts.speaking:
            status = "speaking"
        if a.muted and status == "idle":
            status = "muted"
        elif a.dictating and status in ("idle", "listening", "transcribing"):
            status = "dictating"
        return {
            "status": status, "detail": "", "level": round(min(a.level / 3000.0, 1.0), 3), "muted": a.muted,
            "events": events, "stats": self.stats, "seq": self.seq,
            "brain": getattr(a.brain, "model", ""), "hotkey": self.cfg["assistant"].get("hotkey") or "",
        }

    # ---------- sent by the page ----------
    def ask(self, text: str) -> None:
        if self.assistant is not None and text.strip():
            self.assistant.submit_text(text.strip())

    def action(self, name: str) -> None:
        a = self.assistant
        if a is None:
            return
        if name == "mute":
            a.muted = not a.muted
            if a.muted:
                a.tts.stop()
        elif name == "stop":
            a.dictating = False
            a._interrupt()
            a.status = "idle"
        elif name == "listen":
            a.muted = False
            a._hotkey.set()
        elif name == "quit":
            a.shutdown()
        elif name == "log":
            os.startfile(ROOT / "logs" / "jarvis.log")
        elif name == "settings":
            os.startfile(ROOT / "config.yaml")

    # ---------- system gauges ----------
    def _watch_stats(self) -> None:
        import psutil

        smi = shutil.which("nvidia-smi")
        psutil.cpu_percent(None)
        while True:
            stats = {"cpu": round(psutil.cpu_percent(None)), "ram": round(psutil.virtual_memory().percent)}
            bat = psutil.sensors_battery()
            if bat:
                stats["battery"], stats["plugged"] = round(bat.percent), bool(bat.power_plugged)
            if smi:
                try:
                    out = subprocess.run([smi, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                                         capture_output=True, text=True, timeout=5, creationflags=0x08000000).stdout.strip().splitlines()[0]
                    load, used, total, temp = (float(x) for x in out.split(","))
                    stats["gpu"], stats["vram"], stats["temp"] = round(load), round(100 * used / total), round(temp)
                except Exception:
                    pass
            self.stats = stats
            time.sleep(2.5)
