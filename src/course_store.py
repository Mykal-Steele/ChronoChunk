"""
The course material the bot can search: slides, lecture transcripts, posts and
assignments, cut into chunks.

It is its own SQLite file, apart from the bot's database. The sync on a PC builds a
whole new file and swaps it in, and the bot only ever reads it, so a bad sync cannot
touch what the bot knows about people. This module is all both sides share: the
schema, the writing calls the sync uses, and the search the bot uses. It imports
nothing else from the bot.
"""
import logging
import os
import re
import sqlite3
import struct
import threading
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote

import sqlite_vec

logger = logging.getLogger(__name__)

COURSES_FILE = "courses.db"
# A file written with another number is not read. The sync and the bot must agree on it.
SCHEMA_VERSION = 1
# How many numbers the embedding of a chunk has
DIMENSIONS = 512
KINDS = ("slide", "transcript", "post", "assignment")

# How many results a search returns
RESULTS = 6
# How many of them may come from one deck or recording. A three-hour lecture can hold
# two places worth reading, and more than that would crowd out the other sources.
PER_SOURCE = 2
# How many candidates the vectors and the keywords each put forward
CANDIDATES = 50
# Weighs a rank when two rankings are merged. 60 is the usual value for reciprocal rank fusion.
RANK_WEIGHT = 60
# A chunk less like the question than this is not an answer. On real lectures the right
# chunk scored 0.47 or more, and questions about nothing in the material stayed under 0.35.
MIN_SIMILARITY = 0.40

SCHEMA = f"""
CREATE TABLE courses (
    code     TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    -- Other names people use for the course, one per line
    aliases  TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    site_url TEXT NOT NULL DEFAULT ''
);
-- One deck, recording, thread or assignment
CREATE TABLE sources (
    id           INTEGER PRIMARY KEY,
    course       TEXT NOT NULL REFERENCES courses(code),
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL,
    -- The folder it sits in, such as "Week 6"
    section      TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    -- The day of the lecture or post, as YYYY-MM-DD
    date         TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX sources_by_course ON sources(course, kind);
CREATE TABLE chunks (
    id        INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    -- Order inside the source, counted from 0
    position  INTEGER NOT NULL,
    -- Where in the source: "page 12" or "0:41:07"
    locator   TEXT NOT NULL DEFAULT '',
    -- English, which is what gets searched
    text      TEXT NOT NULL,
    -- The wording as written, kept only when text is a translation of it
    original  TEXT,
    UNIQUE (source_id, position)
);
CREATE VIRTUAL TABLE chunk_words USING fts5(body, content='', tokenize='porter unicode61');
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
    embedding float[{DIMENSIONS}] distance_metric=cosine,
    course text partition key,
    kind text,
    -- The source's date as a number, 20260804, so a search can leave out older sources. 0 when there is no date.
    day integer
);
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Words that say nothing about what a question is looking for
_STOP_WORDS = frozenset(
    "a am an and are as at be before by can did do does for from had has have how i if in is it its me my no of "
    "on or our should so that the their them they this to up us was we were what when where which who why will "
    "with you your".split())


@dataclass
class Hit:
    """One search result, with what the bot needs to say where it came from."""
    course: str
    kind: str
    title: str
    section: str
    locator: str
    url: str
    date: str
    text: str
    original: Optional[str]
    # The chunks right before and after in the same source, '' at either end
    before: str
    after: str
    similarity: float


def _pack(vector: Sequence[float]) -> bytes:
    if len(vector) != DIMENSIONS:
        raise ValueError(f"an embedding has {DIMENSIONS} numbers, this one has {len(vector)}")
    return struct.pack(f"{DIMENSIONS}f", *vector)


def _day(date: str) -> int:
    """2026-08-04 as 20260804, 0 for anything that is not a date."""
    digits = date[:10].replace("-", "")
    return int(digits) if len(digits) == 8 and digits.isdigit() else 0


def _keywords(query: str) -> str:
    """The query as an FTS5 search for any of its meaningful words, '' when it has none."""
    # Two letters can be a term (AI, F1) and a number can be the point ("assignment 2"), so both stay
    words = [word for word in re.findall(r"[a-z0-9]+", query.lower())
             if word not in _STOP_WORDS and (len(word) > 1 or word.isdigit())]
    return " OR ".join(f'"{word}"' for word in dict.fromkeys(words))


def _indexed_text(course: str, title: str, section: str, locator: str, text: str) -> str:
    """What the keyword index holds for a chunk: its text under a line that says where it is from."""
    return f"{course} {title} {section} {locator}\n{text}"


class CourseStore:
    """One course file, open. Calls are short and run under a lock, like the bot's database."""

    def __init__(self, path: str, writable: bool = False):
        self.path = path
        self._lock = threading.RLock()
        if writable:
            self._connection = sqlite3.connect(path, check_same_thread=False)
        else:
            # mode=ro fails on a missing file instead of creating an empty one
            self._connection = sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro", uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.enable_load_extension(True)
        sqlite_vec.load(self._connection)
        self._connection.enable_load_extension(False)

    @classmethod
    def create(cls, path: str) -> "CourseStore":
        """A new, empty file for the sync to fill. Refuses to write over an existing one."""
        if os.path.exists(path):
            raise FileExistsError(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        store = cls(path, writable=True)
        store._connection.executescript(SCHEMA)
        store._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        store._connection.commit()
        return store

    @classmethod
    def open(cls, path: str) -> "CourseStore":
        """An existing file, read-only. Raises ValueError when another version of the sync wrote it."""
        store = cls(path)
        version = store._connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            store.close()
            raise ValueError(f"{path} has schema version {version}, this bot reads version {SCHEMA_VERSION}")
        return store

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # Writing, used by the sync

    def add_course(self, code: str, name: str, aliases: Iterable[str] = (), group_id: str = "", site_url: str = "") -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO courses(code, name, aliases, group_id, site_url) VALUES (?, ?, ?, ?, ?)",
                (code, name, "\n".join(aliases), group_id, site_url))

    def add_source(self, course: str, kind: str, title: str, section: str = "", url: str = "", date: str = "",
                   content_hash: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO sources(course, kind, title, section, url, date, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (course, kind, title, section, url, date, content_hash))
            return int(cursor.lastrowid or 0)

    def add_chunk(self, source_id: int, position: int, locator: str, text: str, vector: Sequence[float],
                  original: Optional[str] = None) -> int:
        """Store one chunk with its embedding. text is English, original is the wording it was translated from."""
        blob = _pack(vector)
        with self._lock:
            source = self._connection.execute("SELECT course, kind, title, section, date FROM sources WHERE id = ?",
                                              (source_id,)).fetchone()
            if source is None:
                raise ValueError(f"no source {source_id}")
            cursor = self._connection.execute(
                "INSERT INTO chunks(source_id, position, locator, text, original) VALUES (?, ?, ?, ?, ?)",
                (source_id, position, locator, text, original))
            chunk_id = int(cursor.lastrowid or 0)
            self._connection.execute(
                "INSERT INTO chunk_words(rowid, body) VALUES (?, ?)",
                (chunk_id, _indexed_text(source["course"], source["title"], source["section"], locator, text)))
            self._connection.execute(
                "INSERT INTO chunk_vectors(rowid, embedding, course, kind, day) VALUES (?, ?, ?, ?, ?)",
                (chunk_id, blob, source["course"], source["kind"], _day(source["date"])))
            return chunk_id

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def save(self) -> None:
        """Write everything added so far to the file."""
        with self._lock:
            self._connection.commit()

    # Reading, used by the bot

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def courses(self) -> List[dict]:
        """Every course, with its other names as a list."""
        with self._lock:
            rows = self._connection.execute("SELECT code, name, aliases FROM courses ORDER BY code").fetchall()
        return [{"code": row["code"], "name": row["name"], "aliases": [a for a in row["aliases"].split("\n") if a]}
                for row in rows]

    def counts(self) -> Dict[str, Dict[str, int]]:
        """How many sources of each kind every course has."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT course, kind, COUNT(*) AS sources FROM sources GROUP BY course, kind").fetchall()
        result: Dict[str, Dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["course"], {})[row["kind"]] = row["sources"]
        return result

    def chunk_counts(self) -> Dict[str, int]:
        """How many chunks every course has."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT sources.course, COUNT(*) AS chunks FROM chunks JOIN sources ON sources.id = chunks.source_id "
                "GROUP BY sources.course").fetchall()
        return {row["course"]: row["chunks"] for row in rows}

    def search(self, query: str, vector: Sequence[float], course: Optional[str] = None, kind: Optional[str] = None,
               since: Optional[str] = None, limit: int = RESULTS) -> List[Hit]:
        """
        The chunks that best answer query, best first, at most PER_SOURCE from one source. vector is
        the embedding of query. course and kind narrow the search before ranking, and
        since (YYYY-MM-DD) drops older sources. An empty list means the material has no
        answer, and the bot should say so.
        """
        blob = _pack(vector)
        # Everything that narrows the search is applied before the candidates are counted,
        # so a pile of chunks that do not qualify cannot crowd out the ones that do
        narrow_vectors = (("" if course is None else " AND course = ?") + ("" if kind is None else " AND kind = ?")
                          + ("" if not since else " AND day >= ?"))
        narrow_sources = (("" if course is None else " AND sources.course = ?") + ("" if kind is None else " AND sources.kind = ?")
                          + ("" if not since else " AND sources.date >= ?"))
        narrowing = tuple(value for value in (course, kind) if value is not None)
        vector_narrowing = narrowing + ((_day(since),) if since else ())
        source_narrowing = narrowing + ((since,) if since else ())
        keywords = _keywords(query)
        with self._lock:
            near = self._connection.execute(
                f"SELECT rowid, distance FROM chunk_vectors WHERE embedding MATCH ? AND k = ?{narrow_vectors}",
                (blob, CANDIDATES) + vector_narrowing).fetchall()
            worded = self._connection.execute(
                "SELECT chunk_words.rowid FROM chunk_words "
                "JOIN chunks ON chunks.id = chunk_words.rowid JOIN sources ON sources.id = chunks.source_id "
                f"WHERE chunk_words MATCH ?{narrow_sources} ORDER BY chunk_words.rank LIMIT ?",
                (keywords,) + source_narrowing + (CANDIDATES,)).fetchall() if keywords else []

            # Reciprocal rank fusion: a chunk both rankings like beats one that tops a single ranking
            score: Dict[int, float] = {}
            for ranking in ([row["rowid"] for row in near], [row["rowid"] for row in worded]):
                for place, chunk_id in enumerate(ranking):
                    score[chunk_id] = score.get(chunk_id, 0.0) + 1.0 / (RANK_WEIGHT + place)
            if not score:
                return []

            similarity = {row["rowid"]: 1.0 - row["distance"] for row in near}
            keyword_only = [chunk_id for chunk_id in score if chunk_id not in similarity]
            if keyword_only:
                marks = ",".join("?" * len(keyword_only))
                for row in self._connection.execute(
                        f"SELECT rowid, vec_distance_cosine(embedding, ?) AS distance FROM chunk_vectors WHERE rowid IN ({marks})",
                        (blob, *keyword_only)):
                    similarity[row["rowid"]] = 1.0 - row["distance"]

            marks = ",".join("?" * len(score))
            rows = {row["id"]: row for row in self._connection.execute(
                "SELECT chunks.id, chunks.source_id, chunks.position, chunks.locator, chunks.text, chunks.original, "
                "sources.course, sources.kind, sources.title, sources.section, sources.url, sources.date "
                f"FROM chunks JOIN sources ON sources.id = chunks.source_id WHERE chunks.id IN ({marks})",
                tuple(score))}

            hits: List[Hit] = []
            # position of every chunk already chosen, by source
            chosen: Dict[int, List[int]] = {}
            # Two versions of one handout share most pages, and the same page twice tells the bot nothing new
            seen_texts = set()
            for chunk_id in sorted(score, key=lambda chunk_id: -score[chunk_id]):
                row = rows.get(chunk_id)
                if row is None or row["text"] in seen_texts:
                    continue
                taken = chosen.get(row["source_id"], [])
                # A chunk next to a chosen one is already in that result as its neighbour
                if len(taken) >= PER_SOURCE or any(abs(row["position"] - position) <= 1 for position in taken):
                    continue
                if similarity.get(chunk_id, 0.0) < MIN_SIMILARITY:
                    continue
                chosen.setdefault(row["source_id"], []).append(row["position"])
                seen_texts.add(row["text"])
                around = {neighbour["position"]: neighbour["text"] for neighbour in self._connection.execute(
                    "SELECT position, text FROM chunks WHERE source_id = ? AND position IN (?, ?)",
                    (row["source_id"], row["position"] - 1, row["position"] + 1))}
                hits.append(Hit(course=row["course"], kind=row["kind"], title=row["title"], section=row["section"],
                                locator=row["locator"], url=row["url"], date=row["date"], text=row["text"],
                                original=row["original"], before=around.get(row["position"] - 1, ""),
                                after=around.get(row["position"] + 1, ""), similarity=round(similarity[chunk_id], 3)))
                if len(hits) >= limit:
                    break
            return hits


class CourseLibrary:
    """
    The course file as the running bot sees it. The sync replaces the file while the bot
    is up, so every use checks whether it changed and opens the new one when it did.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._store: Optional[CourseStore] = None
        self._stamp: Optional[tuple] = None

    def store(self) -> Optional[CourseStore]:
        """The current course file, or None when there is none the bot can read."""
        with self._lock:
            try:
                found = os.stat(self.path)
                stamp: Optional[tuple] = (found.st_ino, found.st_mtime_ns, found.st_size)
            except OSError:
                stamp = None
            if stamp == self._stamp:
                return self._store
            if self._store is not None:
                self._store.close()
            self._store, self._stamp = None, stamp
            if stamp is not None:
                try:
                    self._store = CourseStore.open(self.path)
                    logger.info(f"Opened the course material at {self.path}")
                except (sqlite3.Error, ValueError) as error:
                    # Remembering the stamp means a broken file is reported once, not on every message
                    logger.error(f"The course material at {self.path} cannot be read: {error}")
            return self._store
