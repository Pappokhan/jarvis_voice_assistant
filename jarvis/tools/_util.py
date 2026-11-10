"""Helpers shared by tool modules."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

_KNOWN = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
}


def ps(command: str, timeout: int = 30) -> tuple[int, str]:
    """Run a PowerShell command hidden; returns (exit_code, combined output) as UTF-8."""
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    p = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, timeout=timeout, creationflags=NO_WINDOW,
    )
    out = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip()
    return p.returncode, out


def known_folder(name: str) -> Path | None:
    """Real location of Desktop/Documents/... (they are often redirected into OneDrive)."""
    key = name.lower().strip()
    if key not in _KNOWN:
        return None
    try:
        from win32com.shell import shell, shellcon  # type: ignore

        fid = getattr(shellcon, f"FOLDERID_{_KNOWN[key]}")
        return Path(shell.SHGetKnownFolderPath(fid))
    except Exception:
        return Path.home() / _KNOWN[key]


def resolve(path: str) -> Path:
    """Turn what the model gives us ('desktop/notes.txt', '~/x', '%TEMP%\\y') into an absolute path."""
    p = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    parts = Path(p).parts
    # Small models invent profile paths such as C:\Users\YourUsername\Desktop\x; send those to the real folder.
    if len(parts) >= 3 and parts[1].lower() == "users" and not Path(*parts[:3]).exists():
        for i in range(3, len(parts)):  # also covers ...\YourUsername\OneDrive\Desktop\x
            if known_folder(parts[i]):
                return known_folder(parts[i]).joinpath(*parts[i + 1:]).resolve()
        return Path.home().joinpath(*parts[3:]).resolve()
    if parts and not Path(p).is_absolute():
        base = known_folder(parts[0])
        if base is not None:
            return base.joinpath(*parts[1:]).resolve()
        return (Path.home() / p).resolve()
    return Path(p).resolve()


_PROTECTED = [os.environ.get("SystemRoot", r"C:\Windows"), r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData"]


def guard_write(path: Path) -> None:
    """Refuse to modify system locations, whatever the model asks."""
    s = str(path).lower()
    for root in _PROTECTED:
        if s == root.lower() or s.startswith(root.lower() + "\\"):
            raise PermissionError(f"{path} is a protected system location; Jarvis will not modify it.")
    if path.parent == path:
        raise PermissionError("Refusing to operate on a drive root.")
