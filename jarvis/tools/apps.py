"""Open, close, list and arrange desktop applications and their windows."""
from __future__ import annotations

import ctypes
import difflib
import json
import os
import shutil
import subprocess
import time

import psutil
import win32con
import win32gui
import win32process

from . import S, tool
from ._util import NO_WINDOW, ps

# Spoken name -> Start-menu name
START_ALIASES = {
    "chrome": "google chrome", "edge": "microsoft edge", "browser": "microsoft edge",
    "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
    "explorer": "file explorer", "files": "file explorer", "cmd": "command prompt",
    "powershell": "windows powershell", "calc": "calculator", "paint": "paint",
    "task manager": "task manager", "store": "microsoft store",
}
# Spoken name -> process name (without .exe), for closing
PROC_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "vs code": "code", "vscode": "code", "visual studio code": "code",
    "file explorer": "explorer", "explorer": "explorer", "terminal": "windowsterminal",
    "command prompt": "cmd", "task manager": "taskmgr", "paint": "mspaint",
}

_start_cache: tuple[float, list[dict]] = (0.0, [])


def _start_apps() -> list[dict]:
    """Everything in the Start menu (desktop and Store apps), cached for 30 minutes (listing takes 2-3 s)."""
    global _start_cache
    if time.time() - _start_cache[0] < 1800 and _start_cache[1]:
        return _start_cache[1]
    code, out = ps("Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress", timeout=30)
    apps = json.loads(out) if out.startswith(("[", "{")) else []
    if isinstance(apps, dict):
        apps = [apps]
    _start_cache = (time.time(), apps)
    return apps


def _find_start_app(name: str) -> dict | None:
    q = START_ALIASES.get(name.lower().strip(), name.lower().strip())
    apps = _start_apps()
    by_name = {a["Name"].lower(): a for a in apps if a.get("Name")}
    if q in by_name:
        return by_name[q]
    starts = sorted((n for n in by_name if n.startswith(q)), key=len)
    if starts:
        return by_name[starts[0]]
    contains = sorted((n for n in by_name if q in n), key=len)
    if contains:
        return by_name[contains[0]]
    close = difflib.get_close_matches(q, list(by_name), n=1, cutoff=0.72)
    return by_name[close[0]] if close else None


def _is_cloaked(hwnd: int) -> bool:
    val = ctypes.c_int(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val))
    return bool(val.value)


def _windows() -> list[dict]:
    """Visible top-level windows the user would recognise as 'open apps'."""
    found: list[dict] = []

    def cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if not title or not win32gui.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            return
        if title in ("Program Manager", "Windows Input Experience", "Settings") and win32gui.GetClassName(hwnd) == "Progman":
            return
        if win32gui.GetClassName(hwnd) in ("Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            pname = psutil.Process(pid).name()
        except psutil.Error:
            pname = ""
        found.append({"hwnd": hwnd, "title": title, "pid": pid, "process": pname})

    win32gui.EnumWindows(cb, None)
    return found


def _match_windows(name: str) -> list[dict]:
    q = name.lower().strip()
    proc = PROC_ALIASES.get(q)
    hits = []
    for w in _windows():
        pname = w["process"].lower().removesuffix(".exe")
        if (proc and pname == proc) or q == pname or q in w["title"].lower() or (len(q) > 3 and q in pname):
            hits.append(w)
    return hits


@tool(
    "Open an application by name, the way the user would say it ('Chrome', 'Spotify', 'Word', "
    "'Calculator', 'Settings'). Finds it in the Start menu, including Store apps. To open a file, "
    "folder or website use open_path or open_url instead.",
    {"name": S("Application name")},
)
def open_app(name: str):
    app = _find_start_app(name)
    if app:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"], creationflags=NO_WINDOW)
        return f"Opened {app['Name']}."
    exe = shutil.which(name) or shutil.which(name + ".exe")
    if exe:
        subprocess.Popen([exe])
        return f"Started {exe}."
    names = [a["Name"] for a in _start_apps() if a.get("Name")]
    near = difflib.get_close_matches(name, names, n=5, cutoff=0.4)
    raise LookupError(f"No app called '{name}' is installed. Closest installed names: {near or 'none'}")


@tool(
    "Close an application gracefully (same as clicking its X, so apps with unsaved work will prompt). "
    "Set force=true only if it is frozen and the user wants it killed.",
    {"name": S("Application name or part of its window title"), "force": {"type": "boolean", "description": "Kill the process instead of asking it to close"}},
    required=["name"],
    danger=lambda a: f"force-kill {a['name']}, losing any unsaved work" if a.get("force") else None,
)
def close_app(name: str, force: bool = False):
    wins = _match_windows(name)
    if not wins:
        proc = PROC_ALIASES.get(name.lower().strip(), name.lower().strip())
        procs = [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().removesuffix(".exe") == proc]
        if not procs or proc == "explorer":
            raise LookupError(f"Nothing matching '{name}' is running. Open windows: {[w['title'] for w in _windows()]}")
        for p in procs:
            p.terminate()
        return f"Stopped {len(procs)} background process(es) named {proc}."
    if force:
        for pid in {w["pid"] for w in wins if w["process"].lower() != "explorer.exe"}:
            psutil.Process(pid).kill()
        return f"Killed {name}."
    for w in wins:
        win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    return "Asked to close: " + "; ".join(w["title"] for w in wins)


@tool("List the applications and windows currently open on the desktop.", {})
def list_open_windows():
    return [{"title": w["title"], "process": w["process"]} for w in _windows()]


@tool(
    "Arrange a window: bring it to the front, minimize, maximize or restore it. "
    "Use name='all' with action='minimize' to show the desktop.",
    {
        "name": S("Application name or part of the window title, or 'all'"),
        "action": S("What to do", enum=["focus", "minimize", "maximize", "restore"]),
    },
)
def window_action(name: str, action: str):
    if name.lower() == "all" and action == "minimize":
        import pyautogui

        pyautogui.hotkey("win", "d")
        return "Showing the desktop."
    wins = _match_windows(name)
    if not wins:
        raise LookupError(f"No window matching '{name}'. Open windows: {[w['title'] for w in _windows()]}")
    hwnd = wins[0]["hwnd"]
    if action == "minimize":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    elif action == "maximize":
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    else:
        if win32gui.IsIconic(hwnd) or action == "restore":
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Windows only lets a process steal focus right after input; a tap of Alt satisfies that.
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
    return f"{action}: {wins[0]['title']}"


@tool("List installed applications whose name contains some text (to find the exact name of an app).", {"query": S("Part of the app name; empty string lists everything")})
def find_installed_apps(query: str):
    names = sorted(a["Name"] for a in _start_apps() if a.get("Name") and query.lower() in a["Name"].lower())
    return names[:80] or "No installed app matches."
