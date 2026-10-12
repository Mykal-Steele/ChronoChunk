"""
What earlier runs already paid for: downloaded files, transcripts, page descriptions,
translations and embeddings. A run that finds its work here does not download it or
call a model again, so a rerun with nothing new is fast and free, and a run that was
stopped picks up where it left off. The cache can be deleted at any time: the next
run rebuilds it.
"""
import hashlib
import json
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    kind  TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS embeddings (
    model    TEXT NOT NULL,
    text_sha TEXT NOT NULL,
    vector   BLOB NOT NULL,
    PRIMARY KEY (model, text_sha)
);
"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Cache:
    """One SQLite file. Every write is saved at once, so a killed run loses nothing it finished."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, kind: str, key: str) -> Optional[Any]:
        with self._lock:
            row = self._connection.execute("SELECT value FROM entries WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value: Any) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO entries(kind, key, value) VALUES (?, ?, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value",
                (kind, key, json.dumps(value, ensure_ascii=False)))

    def vectors(self, model: str, texts: Iterable[str]) -> Dict[str, List[float]]:
        """The stored embeddings of these texts, by text. Texts never embedded are left out."""
        found: Dict[str, List[float]] = {}
        with self._lock:
            for text in texts:
                row = self._connection.execute("SELECT vector FROM embeddings WHERE model = ? AND text_sha = ?",
                                               (model, sha(text))).fetchone()
                if row:
                    found[text] = list(struct.unpack(f"{len(row[0]) // 4}f", row[0]))
        return found

    def put_vectors(self, model: str, texts: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        rows = [(model, sha(text), struct.pack(f"{len(vector)}f", *vector)) for text, vector in zip(texts, vectors, strict=True)]
        with self._lock:
            self._connection.execute("BEGIN")
            self._connection.executemany("INSERT OR REPLACE INTO embeddings(model, text_sha, vector) VALUES (?, ?, ?)", rows)
            self._connection.execute("COMMIT")
