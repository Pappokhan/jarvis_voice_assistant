"""Eyes and hands: look at the screen, click, type, press keys, scroll, clipboard."""
from __future__ import annotations

import base64
import io
import time

from . import N, S, tool

_scale = 1.0  # screen pixels per screenshot pixel, from the most recent look_at_screen
MAX_SIDE = 1280


def _gui():
    import pyautogui

    pyautogui.PAUSE = 0.05
    return pyautogui


@tool(
    "Take a screenshot and look at it. Use this to see what is on screen: to answer questions about it, "
    "to find where to click, or to check that an action worked. Coordinates for click/scroll refer to "
    "the most recent screenshot.",
    {},
)
def look_at_screen():
    global _scale
    img = _gui().screenshot()
    _scale = max(img.size) / MAX_SIDE if max(img.size) > MAX_SIDE else 1.0
    if _scale != 1.0:
        img = img.resize((round(img.width / _scale), round(img.height / _scale)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": f"Screenshot is {img.width}x{img.height}. Use these pixel coordinates for click and scroll."},
    ]


@tool(
    "Click the mouse at a position from the latest screenshot (call look_at_screen first).",
    {
        "x": N("X coordinate in the screenshot"),
        "y": N("Y coordinate in the screenshot"),
        "button": S("Mouse button", enum=["left", "right", "middle"]),
        "double": {"type": "boolean", "description": "Double-click"},
    },
    required=["x", "y"],
)
def click(x: float, y: float, button: str = "left", double: bool = False):
    _gui().click(round(x * _scale), round(y * _scale), clicks=2 if double else 1, button=button, interval=0.08)
    return f"Clicked {button} at ({x:.0f}, {y:.0f})."


@tool(
    "Type text into whatever window currently has keyboard focus, as if the user typed it. "
    "Good for dictation, filling fields, and search boxes.",
    {"text": S("Text to type"), "press_enter": {"type": "boolean", "description": "Press Enter afterwards"}},
    required=["text"],
)
def type_text(text: str, press_enter: bool = False):
    import pyperclip

    gui = _gui()
    if text.isascii() and len(text) < 80 and "\n" not in text:
        gui.write(text, interval=0.01)
    else:  # paste is faster and handles any language; restore the clipboard afterwards
        old = pyperclip.paste()
        pyperclip.copy(text)
        gui.hotkey("ctrl", "v")
        time.sleep(0.25)
        pyperclip.copy(old)
    if press_enter:
        gui.press("enter")
    return "Typed."


@tool(
    "Press a key or keyboard shortcut in the focused window, e.g. 'enter', 'ctrl+s', 'alt+tab', "
    "'win+shift+s', 'ctrl+shift+esc', 'f11', 'volumeup'. Several can be sent in order.",
    {"keys": {"type": "array", "items": {"type": "string"}, "description": "Shortcuts to press, in order"}},
)
def press_keys(keys: list[str]):
    gui = _gui()
    for combo in keys:
        parts = [k.strip().lower() for k in combo.split("+") if k.strip()]
        bad = [k for k in parts if k not in gui.KEYBOARD_KEYS]
        if bad:
            raise ValueError(f"Unknown key name(s): {bad}")
        gui.hotkey(*parts)
        time.sleep(0.15)
    return f"Pressed {', '.join(keys)}."


@tool(
    "Scroll the mouse wheel, optionally at a position from the latest screenshot.",
    {"amount": N("Notches to scroll: positive = up, negative = down (5 is about half a page)"), "x": N("Optional X"), "y": N("Optional Y")},
    required=["amount"],
)
def scroll(amount: float, x: float | None = None, y: float | None = None):
    gui = _gui()
    if x is not None and y is not None:
        gui.moveTo(round(x * _scale), round(y * _scale))
    gui.scroll(int(amount * 120))
    return "Scrolled."


@tool("Read the text currently on the clipboard.", {})
def get_clipboard():
    import pyperclip

    return pyperclip.paste() or "(clipboard is empty or holds non-text content)"


@tool("Put text on the clipboard so the user can paste it.", {"text": S("Text to copy")})
def set_clipboard(text: str):
    import pyperclip

    pyperclip.copy(text)
    return "Copied to clipboard."
