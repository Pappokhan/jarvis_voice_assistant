"""Timers and reminders. They persist in the memory database and are spoken aloud when due."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from . import N, S, registry, tool

log = logging.getLogger("jarvis.timers")


def _when(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else d.strftime("%A %d %B at %H:%M")


@tool(
    "Set a timer or reminder. Give either a delay in seconds, or an exact local date-time. "
    "Jarvis will say the message aloud when it is due (if the PC is on).",
    {
        "message": S("What to say when it goes off, e.g. 'Your tea is ready' or 'Call the dentist'"),
        "in_seconds": N("Delay from now in seconds"),
        "at": S("Exact local time in ISO format, e.g. 2026-11-10T17:30"),
    },
    required=["message"],
)
def set_reminder(message: str, in_seconds: float | None = None, at: str | None = None):
    if in_seconds is not None:
        due = time.time() + in_seconds
    elif at:
        due = datetime.fromisoformat(at).timestamp()
    else:
        raise ValueError("Give in_seconds or at.")
    if due < time.time() - 5:
        raise ValueError("That time is in the past.")
    rid = registry.ctx.memory.add_reminder(due, message)
    return f"Reminder {rid} set for {_when(due)}."


@tool("List pending timers and reminders.", {})
def list_reminders():
    rows = registry.ctx.memory.pending_reminders()
    return "\n".join(f"{rid}: {_when(due)} - {msg}" for rid, due, msg in rows) or "No pending reminders."


@tool("Cancel a pending timer or reminder by id (see list_reminders).", {"reminder_id": N("Reminder id")})
def cancel_reminder(reminder_id: float):
    ok = registry.ctx.memory.complete_reminder(int(reminder_id))
    return "Cancelled." if ok else "No pending reminder with that id."


def start_scheduler(stop: threading.Event) -> threading.Thread:
    """Background thread that announces reminders when they come due."""

    def loop():
        while not stop.wait(1.0):
            try:
                for rid, due, msg in registry.ctx.memory.pending_reminders():
                    if due <= time.time():
                        registry.ctx.memory.complete_reminder(rid)
                        late = time.time() - due
                        text = msg if late < 120 else f"A reminder you missed, from {_when(due)}: {msg}"
                        if registry.ctx.announce:
                            registry.ctx.announce(text)
            except Exception:
                log.exception("scheduler error")

    t = threading.Thread(target=loop, name="reminders", daemon=True)
    t.start()
    return t
