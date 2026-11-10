"""System controls: volume, brightness, Wi-Fi/Bluetooth, power, media keys, status, settings pages."""
from __future__ import annotations

import ctypes
import subprocess
from datetime import datetime

import psutil

from . import B, N, S, tool
from ._util import NO_WINDOW, ps


# ---------------- volume ----------------
def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    comtypes.CoInitialize()
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):  # newer pycaw
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))


@tool(
    "Set the speaker volume. Give either an absolute level (0-100) or a relative change (e.g. -10 / +15), "
    "and/or mute. Returns the resulting volume.",
    {
        "level": N("Absolute volume 0-100"),
        "change": N("Relative change in percentage points, negative to lower"),
        "mute": B("true to mute, false to unmute"),
    },
    required=[],
)
def set_volume(level: float | None = None, change: float | None = None, mute: bool | None = None):
    ep = _endpoint()
    cur = ep.GetMasterVolumeLevelScalar() * 100
    if level is None and change is not None:
        level = cur + change
    if level is not None:
        ep.SetMasterVolumeLevelScalar(max(0.0, min(100.0, level)) / 100, None)
        if level > 0 and mute is None:
            ep.SetMute(0, None)
    if mute is not None:
        ep.SetMute(1 if mute else 0, None)
    return f"Volume {round(ep.GetMasterVolumeLevelScalar() * 100)}%" + (", muted" if ep.GetMute() else "")


# ---------------- brightness ----------------
@tool(
    "Set the screen brightness: an absolute level (0-100) or a relative change. Returns the resulting brightness.",
    {"level": N("Absolute brightness 0-100"), "change": N("Relative change, negative to dim")},
    required=[],
)
def set_brightness(level: float | None = None, change: float | None = None):
    import screen_brightness_control as sbc

    cur = sbc.get_brightness()[0]
    if level is None and change is not None:
        level = cur + change
    if level is not None:
        sbc.set_brightness(int(max(0, min(100, level))))
    return f"Brightness {sbc.get_brightness()[0]}%"


# ---------------- radios ----------------
_RADIO_PS = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
"""


@tool(
    "Turn Wi-Fi or Bluetooth on or off, or just report their state (omit 'on'). "
    "Note: turning Wi-Fi off also cuts Jarvis off from the internet.",
    {"radio": S("Which radio", enum=["wifi", "bluetooth"]), "on": B("true = on, false = off; omit to only check")},
    required=["radio"],
    danger=lambda a: "turn Wi-Fi off, which takes me offline" if a["radio"] == "wifi" and a.get("on") is False else None,
)
def set_radio(radio: str, on: bool | None = None):
    kind = "WiFi" if radio == "wifi" else "Bluetooth"
    script = _RADIO_PS + f"$r = $radios | Where-Object {{ $_.Kind -eq '{kind}' }}\n"
    script += "if (-not $r) { 'NO_RADIO'; exit }\n"
    if on is not None:
        script += f"foreach ($x in $r) {{ Await ($x.SetStateAsync('{'On' if on else 'Off'}')) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null }}\n"
    script += "$r | ForEach-Object { \"$($_.Name): $($_.State)\" }"
    _, out = ps(script, timeout=30)
    if "NO_RADIO" in out:
        raise LookupError(f"This PC has no {kind} radio.")
    if radio == "wifi" and on is not False:
        _, net = ps("(netsh wlan show interfaces) -match '^\\s+(SSID|Signal)\\s' -join '; '", timeout=15)
        out += ("\n" + net) if net else ""
    return out


# ---------------- power ----------------
_POWER = {
    "lock": ("lock the PC", None),
    "sleep": ("put the PC to sleep", "put the computer to sleep"),
    "shutdown": ("shut down in 30 seconds", "shut the computer down"),
    "restart": ("restart in 30 seconds", "restart the computer"),
    "sign_out": ("sign out", "sign you out, closing all your apps"),
    "cancel_shutdown": ("cancel a pending shutdown", None),
    "screen_off": ("turn the display off", None),
}


@tool(
    "Power actions: lock, sleep, shutdown, restart, sign_out, screen_off, or cancel_shutdown. "
    "Shutdown and restart happen after a 30 second delay so they can be cancelled.",
    {"action": S("Power action", enum=list(_POWER))},
    danger=lambda a: _POWER[a["action"]][1],
)
def power(action: str):
    if action == "lock":
        ctypes.windll.user32.LockWorkStation()
    elif action == "sleep":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=NO_WINDOW)
    elif action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "30"], creationflags=NO_WINDOW)
    elif action == "restart":
        subprocess.run(["shutdown", "/r", "/t", "30"], creationflags=NO_WINDOW)
    elif action == "sign_out":
        subprocess.run(["shutdown", "/l"], creationflags=NO_WINDOW)
    elif action == "cancel_shutdown":
        subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW)
    elif action == "screen_off":
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x0112, 0xF170, 2, 2, 1000, None)
    return f"OK: {_POWER[action][0]}."


# ---------------- media ----------------
@tool(
    "Control whatever is playing (Spotify, YouTube, a video...) using the keyboard media keys.",
    {"action": S("Media action", enum=["play_pause", "next", "previous", "stop"])},
)
def media_control(action: str):
    vk = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}[action]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return f"Sent {action}."


# ---------------- status ----------------
@tool("Report the PC's status: time, CPU, memory, disks, battery, GPU, volume and screen brightness.", {})
def system_status():
    info = {}
    for key, read in (("volume", set_volume), ("screen_brightness", set_brightness)):
        try:
            info[key] = read()
        except Exception:
            pass
    info |= {
        "time": datetime.now().strftime("%A %d %B %Y, %H:%M"),
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "ram": f"{psutil.virtual_memory().percent}% of {psutil.virtual_memory().total / 2**30:.0f} GB used",
        "disks": {},
    }
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            info["disks"][part.device] = f"{u.free / 2**30:.0f} GB free of {u.total / 2**30:.0f} GB"
        except OSError:
            pass
    bat = psutil.sensors_battery()
    if bat:
        info["battery"] = f"{bat.percent:.0f}%, {'charging' if bat.power_plugged else 'on battery'}"
    try:
        g = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW,
        ).stdout.strip()
        if g:
            info["gpu (name, load, vram used, vram total, temp C)"] = g
    except Exception:
        pass
    top = sorted(psutil.process_iter(["name", "memory_info"]), key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0, reverse=True)[:5]
    info["top_memory_apps"] = [f"{p.info['name']} {p.info['memory_info'].rss / 2**20:.0f} MB" for p in top if p.info["memory_info"]]
    return info


@tool(
    "Open a page of Windows Settings, e.g. display, sound, bluetooth, network-wifi, nightlight, "
    "personalization-background, windowsupdate, apps-features, batterysaver, privacy-microphone, about.",
    {"page": S("Settings page id (the part after 'ms-settings:'); empty string opens the Settings home")},
)
def open_settings(page: str):
    import os

    os.startfile(f"ms-settings:{page.strip()}")
    return f"Opened Settings {page}."
