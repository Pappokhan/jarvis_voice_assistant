"""Send a WhatsApp message through the WhatsApp desktop app.

The app is driven through Windows UI Automation rather than blind key presses, so the tool can check that
the chat it opened really belongs to the person named before anything is typed, and it asks for a spoken
yes with the message already visible in the chat before it presses Enter.
"""
from __future__ import annotations

import difflib
import re
import time

from . import S, registry, tool

_uia = None


def _automation():
    global _uia
    import comtypes
    import comtypes.client

    try:
        comtypes.CoInitialize()  # tools run on worker threads
    except OSError:
        pass
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA

    if _uia is None:
        _uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    return _uia, UIA


def _windows() -> tuple[int | None, int | None]:
    """(outer app window, inner web view) of WhatsApp, or None for each that is missing."""
    import win32gui

    outer = inner = None

    def each(h, _):
        nonlocal outer, inner
        # When WhatsApp is closed to the tray both windows still exist but are hidden; only a visible one counts.
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).endswith("WhatsApp"):
            if win32gui.GetClassName(h).startswith("Chrome_WidgetWin"):
                inner = inner or h
            elif win32gui.GetClassName(h).startswith("WinUI"):
                outer = outer or h

    win32gui.EnumWindows(each, None)
    return outer, inner


def _to_front(hwnd: int) -> None:
    import pyautogui
    import win32con
    import win32gui

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    pyautogui.press("alt")  # Windows only lets a background program take the foreground after input
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.5)


def _edits(root, UIA, uia) -> list:
    found = root.FindAll(UIA.TreeScope_Descendants, uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_EditControlTypeId))
    return [found.GetElement(i) for i in range(found.Length)]


def _paste(text: str) -> None:
    import pyautogui
    import pyperclip

    old = pyperclip.paste()
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.35)
    pyperclip.copy(old)


def _clean(name: str) -> str:
    return re.sub(r"[^\w ]", "", name.lower()).strip()


def _best_chat(rows: list[tuple[str, object]], contact: str):
    """The chat row whose title matches the spoken name best, or None when nothing is close enough."""
    want = _clean(contact)
    scored = []
    for title, element in rows:
        have = _clean(title)
        if not have:
            continue
        first = have[: len(want)]
        if have == want:
            score = 3.0
        elif have.startswith(want + " ") or f" {want} " in f" {have} ":
            score = 2.0
        else:  # speech recognition often gets a name slightly wrong ("Akash" / "Aakash")
            score = max(difflib.SequenceMatcher(None, want, first).ratio(),
                        max((difflib.SequenceMatcher(None, want, w).ratio() for w in have.split()[:3]), default=0))
            score = score if score >= 0.8 else 0
        if score:
            scored.append((score, -len(have), title, element))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return scored[0][2], scored[0][3]


def _chat_rows(root, UIA, uia) -> list[tuple[str, object]]:
    """(chat title, row element) for every chat in the list.

    A row's accessible name is "title + time + last message", and matching a name against that would match
    words inside messages. The title is exposed separately as a cell of the row whose text the row's name
    begins with, so only that is used.
    """
    found = root.FindAll(UIA.TreeScope_Descendants, uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_DataItemControlTypeId))
    items = [(found.GetElement(i).CurrentName or "", found.GetElement(i)) for i in range(found.Length)]
    names = {n for n, _ in items if n}
    rows, seen, used = [], set(), set()
    for name, element in items:
        if not name or name in seen:
            continue
        titles = [t for t in names if t != name and name.startswith(t) and not name[len(t):len(t) + 1].isalnum()]
        if titles:
            seen.add(name)
            title = max(titles, key=len)
            used.update(titles)
            rows.append((_strip_when(title, names), element))
    for name, element in items:  # a contact with no messages yet is a row with nothing but its name
        if name and name not in seen and name not in used and len(name) <= 40 and not _WHEN.fullmatch(name.strip()) \
                and not any(other != name and other.startswith(name) for other in names):
            seen.add(name)
            rows.append((name, element))
    return rows


_WHEN = re.compile(r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}:\d{2}(?:\s?[ap]\.?m\.?)?|yesterday|today|"
                   r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)", re.I)


def _strip_when(title: str, names: set[str]) -> str:
    """'Akash AIRIL 23/06/2026' -> 'Akash AIRIL': the title cell ends with the time of the last message."""
    for other in names:
        if other != title and len(other) <= 14 and title.endswith(other) and _WHEN.fullmatch(other.strip()):
            return title[: -len(other)].strip()
    return _WHEN.sub("", title).strip() if _WHEN.search(title[-12:]) else title


def open_chat(contact: str) -> tuple[str, object]:
    """Open the chat with this person. Returns (chat title as WhatsApp shows it, the message box)."""
    import pyautogui

    uia, UIA = _automation()
    outer, inner = _windows()
    if inner is None:
        from .apps import open_app

        open_app("WhatsApp")
        for _ in range(40):
            time.sleep(0.5)
            outer, inner = _windows()
            if inner is not None:
                time.sleep(1.5)  # let the window finish appearing before clicking in it
                break
        else:
            raise RuntimeError("WhatsApp did not open. Is the WhatsApp desktop app installed and signed in?")
    _to_front(outer or inner)
    root = uia.ElementFromHandle(inner)
    search = None
    for _ in range(20):  # the app builds its accessibility tree on the first request
        # The search box is the first text box in the window. (Its label is "Search or start a new chat" only
        # while it is empty, so it cannot be recognised by name.)
        search = next(iter(_edits(root, UIA, uia)), None)
        if search is not None:
            break
        time.sleep(0.5)
    if search is None:
        raise RuntimeError("I could not find WhatsApp's search box. The app may still be loading or showing a sign-in screen.")
    search.SetFocus()
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")
    _paste(contact)
    best = None
    for _ in range(8):
        time.sleep(0.5)
        best = _best_chat(_chat_rows(root, UIA, uia), contact)
        if best:
            break
    if not best:
        search.SetFocus()
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        raise LookupError(f"No WhatsApp chat or contact matches '{contact}'. Ask the user for the name as it is saved in WhatsApp.")
    title, row = best
    r = row.CurrentBoundingRectangle
    pyautogui.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    box = None
    for _ in range(12):
        time.sleep(0.4)
        edits = _edits(root, UIA, uia)
        if len(edits) >= 2:  # the search box, then the message box of the chat that just opened
            box = edits[-1]
            break
    if box is None:
        raise RuntimeError(f"I opened WhatsApp and found '{title}', but the message box did not appear.")
    return title, box


@tool(
    "Send a WhatsApp message to a person or group using the WhatsApp desktop app. Use this whenever the user asks to "
    "message, text or WhatsApp someone. Pass the message exactly as the user worded it. The user is asked to confirm before it is sent.",
    {"contact": S("Name of the person or group as saved in WhatsApp, e.g. 'Akash'"), "message": S("The message text")},
)
def send_whatsapp(contact: str, message: str):
    import pyautogui

    contact, message = contact.strip(), message.strip()
    if not contact or not message:
        raise ValueError("Both the contact name and the message are needed. Ask the user for the missing one.")
    title, box = open_chat(contact)
    name = title.split(",")[0].strip()[:60]
    box.SetFocus()
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")  # replace any unsent draft
    _paste(message)
    if registry.confirm_dangerous and registry.confirm_handler is not None:
        if not registry.confirm_handler(f"send this WhatsApp message to {name}: {message}"):
            box.SetFocus()
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            return f"Not sent. The message to {name} was cleared."
        outer, inner = _windows()
        _to_front(outer or inner)
        box.SetFocus()
        time.sleep(0.2)
    pyautogui.press("enter")
    return f"Sent to {name} on WhatsApp: {message}"
