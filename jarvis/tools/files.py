"""Files, folders and documents."""
from __future__ import annotations

import fnmatch
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import B, N, S, tool
from ._util import guard_write, known_folder, resolve

PATH_HELP = (
    "Absolute path, or relative to the user's home. May start with a known folder name: "
    "desktop, documents, downloads, pictures, music, videos (e.g. 'desktop/notes.txt')."
)
SKIP_DIRS = {"appdata", "node_modules", ".git", "__pycache__", ".venv", "venv", "$recycle.bin", "windows", "program files", "program files (x86)", "programdata"}


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@tool("List the contents of a folder (name, type, size, modified date).", {"path": S(PATH_HELP)})
def list_folder(path: str):
    p = resolve(path)
    rows = []
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    for e in entries[:200]:
        try:
            st = e.stat()
            rows.append(f"{'[dir] ' if e.is_dir() else '      '}{e.name}"
                        + ("" if e.is_dir() else f"  ({_size(st.st_size)})")
                        + f"  {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
        except OSError:
            rows.append(e.name)
    more = f"\n...and {len(entries) - 200} more" if len(entries) > 200 else ""
    return f"{p}\n" + ("\n".join(rows) or "(empty)") + more


@tool(
    "Search for files or folders by name. Use wildcards for patterns ('*.pdf', 'report*2024*'), "
    "otherwise any name containing the text matches. Searches the user's home folder unless a folder is given.",
    {"query": S("File name text or wildcard pattern"), "folder": S("Where to search. " + PATH_HELP)},
    required=["query"],
)
def search_files(query: str, folder: str = "~"):
    root = resolve(folder)
    q = query.lower()
    wild = any(c in q for c in "*?[")
    hits: list[tuple[float, str]] = []
    deadline = time.time() + 20
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames + dirnames:
            low = name.lower()
            if fnmatch.fnmatch(low, q) if wild else q in low:
                full = os.path.join(dirpath, name)
                try:
                    hits.append((os.path.getmtime(full), full))
                except OSError:
                    pass
        if len(hits) >= 300 or time.time() > deadline:
            break
    hits.sort(reverse=True)
    if not hits:
        return f"Nothing matching '{query}' under {root}."
    return "Most recently modified first:\n" + "\n".join(
        f"{datetime.fromtimestamp(m):%Y-%m-%d}  {f}" for m, f in hits[:40]
    )


@tool(
    "Read a document's text: plain text, code, CSV, Markdown, PDF or Word (.docx).",
    {"path": S(PATH_HELP), "max_chars": N("Maximum characters to return (default 8000)")},
    required=["path"],
)
def read_file(path: str, max_chars: float = 8000):
    p = resolve(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:40])
    elif ext == ".docx":
        import docx

        text = "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    else:
        if p.stat().st_size > 5_000_000:
            raise ValueError(f"{p.name} is {_size(p.stat().st_size)}; too large to read as text.")
        raw = p.read_bytes()
        if b"\x00" in raw[:2000]:
            raise ValueError(f"{p.name} looks like a binary file, not text.")
        text = raw.decode("utf-8", errors="replace")
    n = int(max_chars)
    return text[:n] + (f"\n...[{len(text) - n} more characters]" if len(text) > n else "")


def _overwrite_warning(a: dict) -> str | None:
    p = resolve(a["path"])
    if p.exists() and not a.get("append"):
        return f"overwrite the existing file {p.name}"
    return None


@tool(
    "Create a text file, or write/append text to one. Creates missing folders. "
    "Use a .docx extension to produce a Word document.",
    {"path": S(PATH_HELP), "content": S("Text to write"), "append": B("Add to the end instead of replacing")},
    required=["path", "content"],
    danger=_overwrite_warning,
)
def write_file(path: str, content: str, append: bool = False):
    p = resolve(path)
    guard_write(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".docx":
        import docx

        doc = docx.Document(str(p)) if (append and p.exists()) else docx.Document()
        for para in content.split("\n"):
            doc.add_paragraph(para)
        doc.save(str(p))
    else:
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(content)
    return f"Saved {p}"


@tool("Create a folder (and any missing parent folders).", {"path": S(PATH_HELP)})
def create_folder(path: str):
    p = resolve(path)
    guard_write(p)
    p.mkdir(parents=True, exist_ok=True)
    return f"Folder ready: {p}"


def _dest(src: Path, destination: str) -> Path:
    d = resolve(destination)
    return d / src.name if d.is_dir() else d


@tool(
    "Move or rename a file or folder. If the destination is an existing folder, the item is moved into it.",
    {"source": S(PATH_HELP), "destination": S("New path or target folder. " + PATH_HELP)},
)
def move_path(source: str, destination: str):
    src = resolve(source)
    dst = _dest(src, destination)
    guard_write(src), guard_write(dst)
    if dst.exists():
        raise FileExistsError(f"{dst} already exists; choose another name or delete it first.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved to {dst}"


@tool(
    "Copy a file or folder. If the destination is an existing folder, the copy goes inside it.",
    {"source": S(PATH_HELP), "destination": S("Copy path or target folder. " + PATH_HELP)},
)
def copy_path(source: str, destination: str):
    src = resolve(source)
    dst = _dest(src, destination)
    guard_write(dst)
    if dst.exists():
        raise FileExistsError(f"{dst} already exists.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return f"Copied to {dst}"


@tool(
    "Delete a file or folder by sending it to the Recycle Bin (recoverable).",
    {"path": S(PATH_HELP)},
    danger=lambda a: f"send {resolve(a['path']).name} to the Recycle Bin",
)
def delete_path(path: str):
    from send2trash import send2trash

    p = resolve(path)
    guard_write(p)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p == Path.home() or p in {known_folder(k) for k in ("desktop", "documents", "downloads")}:
        raise PermissionError(f"Refusing to delete the whole {p.name} folder.")
    send2trash(str(p))
    return f"Moved {p.name} to the Recycle Bin."


@tool(
    "Open a file or folder with its default application (a document in Word, a folder in File Explorer, "
    "a video in the media player...).",
    {"path": S(PATH_HELP)},
)
def open_path(path: str):
    p = resolve(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    os.startfile(str(p))  # noqa: S606 - opening user files is the point
    return f"Opened {p}"
