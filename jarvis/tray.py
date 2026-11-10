"""System-tray icon so Jarvis can live in the background: mute the mic, open the log, quit."""
from __future__ import annotations

import logging
import os
import threading

from .config import LOG_DIR, ROOT

log = logging.getLogger("jarvis.tray")


def _icon(color: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), outline=color, width=6)
    d.ellipse((22, 22, 42, 42), fill=color)
    return img


def start_tray(assistant, ui=None) -> None:
    try:
        import pystray
    except Exception as e:
        log.warning("tray icon unavailable: %s", e)
        return

    on, off = _icon("#35c8ff"), _icon("#8a8a8a")

    def toggle_mute(icon, item):
        assistant.muted = not assistant.muted
        if assistant.muted:
            assistant.tts.stop()
        icon.icon = off if assistant.muted else on

    def quit_(icon, item):
        assistant.shutdown()
        icon.stop()

    def show(icon, item):
        from .ui import open_window

        open_window(ui.port)

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"Jarvis - {assistant.status}", None, enabled=False),
        pystray.MenuItem("Show window", show, default=True, visible=ui is not None),
        pystray.MenuItem("Mute microphone", toggle_mute, checked=lambda item: assistant.muted),
        pystray.MenuItem("Stop talking", lambda icon, item: assistant._interrupt()),
        pystray.MenuItem("Open log", lambda icon, item: os.startfile(LOG_DIR / "jarvis.log")),
        pystray.MenuItem("Open settings file", lambda icon, item: os.startfile(ROOT / "config.yaml")),
        pystray.MenuItem("Quit Jarvis", quit_),
    )
    icon = pystray.Icon("jarvis", on, "Jarvis", menu)
    threading.Thread(target=icon.run, name="tray", daemon=True).start()
