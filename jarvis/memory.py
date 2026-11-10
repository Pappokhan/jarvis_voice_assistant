"""Persistent memory: long-term facts, a plain-text conversation log, and reminders (SQLite)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL);
            CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, ts REAL, role TEXT, text TEXT);
            CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, due REAL, message TEXT, done INTEGER DEFAULT 0);
            """
        )
        self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            self._last_id = cur.lastrowid
            return rows

    # ---- facts ----
    def remember(self, text: str) -> int:
        self._exec("INSERT INTO facts(text, created) VALUES(?, ?)", (text.strip(), time.time()))
        return self._last_id

    def facts(self, limit: int = 150) -> list[tuple[int, str]]:
        return self._exec("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (limit,))[::-1]

    def search_facts(self, query: str) -> list[tuple[int, str]]:
        words = [w for w in query.lower().split() if len(w) > 2] or [query.lower()]
        return [(i, t) for i, t in self.facts(1000) if any(w in t.lower() for w in words)]

    def forget(self, fact_id: int) -> bool:
        before = len(self._exec("SELECT id FROM facts WHERE id=?", (fact_id,)))
        self._exec("DELETE FROM facts WHERE id=?", (fact_id,))
        return bool(before)

    # ---- conversation log ----
    def log(self, role: str, text: str) -> None:
        if text.strip():
            self._exec("INSERT INTO log(ts, role, text) VALUES(?, ?, ?)", (time.time(), role, text.strip()))

    def recent_log(self, limit: int = 12) -> list[tuple[float, str, str]]:
        return self._exec("SELECT ts, role, text FROM log ORDER BY id DESC LIMIT ?", (limit,))[::-1]

    def search_log(self, query: str, limit: int = 15) -> list[tuple[float, str, str]]:
        return self._exec(
            "SELECT ts, role, text FROM log WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        )[::-1]

    # ---- reminders ----
    def add_reminder(self, due: float, message: str) -> int:
        self._exec("INSERT INTO reminders(due, message) VALUES(?, ?)", (due, message))
        return self._last_id

    def pending_reminders(self) -> list[tuple[int, float, str]]:
        return self._exec("SELECT id, due, message FROM reminders WHERE done=0 ORDER BY due")

    def complete_reminder(self, reminder_id: int) -> bool:
        before = len(self._exec("SELECT id FROM reminders WHERE id=? AND done=0", (reminder_id,)))
        self._exec("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))
        return bool(before)
