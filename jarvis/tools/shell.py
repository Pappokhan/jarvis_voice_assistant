"""Escape hatch: run a PowerShell command for anything the dedicated tools don't cover."""
from __future__ import annotations

import re

from . import N, S, tool
from ._util import ps

# Read-only commands run without asking; anything else is confirmed with the user first.
_SAFE = re.compile(
    r"^\s*(get-[\w-]+|ipconfig|whoami|hostname|systeminfo|tasklist|ping|nslookup|where\.exe|winget\s+(list|search|show)|"
    r"netsh\s+wlan\s+show|test-connection|test-path|resolve-dnsname|date|dir|ls|pwd)\b",
    re.IGNORECASE,
)
_UNSAFE_CHARS = re.compile(r"[;&|><`]|\$\(")


def _needs_confirmation(a: dict) -> str | None:
    cmd = a["command"]
    if _SAFE.match(cmd) and not _UNSAFE_CHARS.search(cmd):
        return None
    spoken = cmd if len(cmd) < 90 else cmd[:90] + " and so on"
    return f"run this PowerShell command: {spoken}"


@tool(
    "Run a PowerShell command on the PC and return its output. Use for things no other tool covers "
    "(installing software with winget, network diagnostics, changing a setting, batch file operations). "
    "Prefer the dedicated tools when one fits. Commands that change anything are confirmed with the user first.",
    {"command": S("PowerShell command or short script"), "timeout_seconds": N("Time limit, default 60, max 600")},
    required=["command"],
    danger=_needs_confirmation,
)
def run_powershell(command: str, timeout_seconds: float = 60):
    code, out = ps(command, timeout=int(max(5, min(600, timeout_seconds))))
    return f"exit code {code}\n{out or '(no output)'}"
