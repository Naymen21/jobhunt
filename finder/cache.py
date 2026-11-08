"""Per-domain cache for MX lookups and catch-all results.

Kept in memory and mirrored to a small SQLite file so results survive restarts.
This is engine-internal and separate from the app's database.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


class DomainCache:
    def __init__(self, path: Optional[Path] = None):
        self._mem: dict[tuple[str, str], tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS domain_cache ("
                " domain TEXT, kind TEXT, value TEXT, stored_at REAL,"
                " PRIMARY KEY (domain, kind))"
            )
            self._db.commit()

    def get(self, domain: str, kind: str, ttl: float) -> Optional[Any]:
        key = (domain.lower(), kind)
        with self._lock:
            hit = self._mem.get(key)
            if hit is None and self._db is not None:
                row = self._db.execute(
                    "SELECT stored_at, value FROM domain_cache WHERE domain=? AND kind=?", key
                ).fetchone()
                if row:
                    hit = (row[0], json.loads(row[1]))
                    self._mem[key] = hit
            if hit is None or time.time() - hit[0] > ttl:
                return None
            return hit[1]

    def set(self, domain: str, kind: str, value: Any) -> None:
        key = (domain.lower(), kind)
        now = time.time()
        with self._lock:
            self._mem[key] = (now, value)
            if self._db is not None:
                self._db.execute(
                    "INSERT OR REPLACE INTO domain_cache (domain, kind, value, stored_at) VALUES (?, ?, ?, ?)",
                    (key[0], kind, json.dumps(value), now),
                )
                self._db.commit()

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
            if self._db is not None:
                self._db.execute("DELETE FROM domain_cache")
                self._db.commit()
