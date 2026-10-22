"""The local reply log keeps enough context to explain a bad answer later, encrypted."""
import sqlite3

from cryptography.fernet import Fernet

from src.reply_log import ReplyLog


def _log(path, keep_days=30):
    return ReplyLog(str(path), keep_days=keep_days, key=Fernet.generate_key())


def test_a_reply_is_stored_with_its_context_routing_and_tools_encrypted(tmp_path):
    path = tmp_path / "replies.sqlite3"
    log = _log(path)
    log.record(
        user_id="42", username="Kruskal", channel_id="99", query="what dev tool",
        conversation_history="Alex: earlier\nChronoChunk: answer", attached_context="file: notes.md\nhi",
        image_count=1, routing={"course": False, "rewrite": False},
        tool_calls=[{"name": "search_course", "arguments": {"query": "dev tool"}, "result": "found"}],
        reply="it is Teams", files=["notes.pdf"], sources={"10": ("Week 9", "https://example.test/9")},
        model_calls=2, cost=0.0123, latency_ms=1500, error="",
    )
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT user_hash, image_count, model_calls, cost, latency_ms, payload FROM replies"
        ).fetchone()
    assert row[1:5] == (1, 2, 0.0123, 1500)
    assert isinstance(row[5], bytes) and row[5]
    assert row[0] != "42"
    for secret in (b"what dev tool", b"Alex: earlier", b"it is Teams", b"notes.md"):
        assert secret not in row[5]

    entry = next(log.entries())
    assert entry["username"] == "Kruskal"
    assert entry["channel_id"] == "99"
    assert entry["query"] == "what dev tool"
    assert entry["conversation_history"] == "Alex: earlier\nChronoChunk: answer"
    assert entry["attached_context"] == "file: notes.md\nhi"
    assert entry["routing"] == {"course": False, "rewrite": False}
    assert entry["tool_calls"][0]["result"] == "found"
    assert entry["reply"] == "it is Teams"
    assert entry["files"] == ["notes.pdf"]
    assert entry["sources"] == {"10": ["Week 9", "https://example.test/9"]}
    assert (entry["model_calls"], entry["cost"], entry["latency_ms"]) == (2, 0.0123, 1500)


def test_rows_older_than_the_retention_window_are_removed(tmp_path):
    path = tmp_path / "replies.sqlite3"
    log = _log(path, keep_days=1)
    log.record(reply="old", user_id="1")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE replies SET created_at = '2000-01-01T00:00:00+00:00'")
    log.record(reply="new", user_id="1")
    assert [entry["reply"] for entry in log.entries()] == ["new"]


def test_deleting_a_user_removes_only_their_rows(tmp_path):
    path = tmp_path / "replies.sqlite3"
    log = _log(path)
    log.record(user_id="42", reply="keep me safe")
    log.record(user_id="99", reply="delete me")
    assert log.delete_user("99") == 1
    assert [entry["reply"] for entry in log.entries()] == ["keep me safe"]
    assert log.delete_user("404") == 0


def test_a_plaintext_log_from_before_encryption_is_migrated(tmp_path):
    path = tmp_path / "replies.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, user_id TEXT,
            username TEXT, channel_id TEXT, query TEXT, conversation_history TEXT,
            attached_context TEXT, image_count INTEGER, routing TEXT, tool_calls TEXT,
            reply TEXT, files TEXT, sources TEXT, model_calls INTEGER, cost REAL,
            latency_ms INTEGER, error TEXT)""")
        db.execute("CREATE INDEX replies_created_at ON replies(created_at)")
        db.execute("CREATE INDEX replies_user_id ON replies(user_id)")
        db.execute("""INSERT INTO replies
            (created_at, user_id, username, query, reply, image_count, model_calls, cost)
            VALUES ('2026-10-01T00:00:00+00:00', '42', 'Kruskal', 'old q', 'old a', 0, 1, 0.1)""")
    log = _log(path)
    entry = next(log.entries())
    assert entry["user_id"] == "42" and entry["query"] == "old q" and entry["reply"] == "old a"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM replies").fetchone()[0] == 1
        assert b"old q" not in db.execute("SELECT payload FROM replies").fetchone()[0]
        indexes = {row[1] for row in db.execute("PRAGMA index_list(replies)")}
        assert {"replies_created_at", "replies_user_hash"} <= indexes
