"""The local reply log keeps enough context to explain a bad answer later, encrypted."""
import sqlite3

import pytest
from cryptography.fernet import Fernet

from config.config import Config
from src.reply_log import ReplyLog, main


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
        model="gpt-6-luna", reasoning_effort="none", needs_web=True, version="3.21.0",
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
    assert entry["model"] == "gpt-6-luna" and entry["reasoning_effort"] == "none"
    assert entry["needs_web"] is True and entry["version"] == "3.21.0"
    info = log.describe()
    assert info["path"] == str(path) and info["rows"] == 1 and info["keep_days"] == 30
    assert "REPLY_LOG_KEY" not in str(info)


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
    assert [entry["reply"] for entry in log.entries(user_id="42")] == ["keep me safe"]
    assert list(log.entries(user_id="99")) == []
    assert log.delete_user("404") == 0


def test_the_command_line_reads_the_newest_rows(tmp_path, monkeypatch, capsys):
    path = tmp_path / "replies.sqlite3"
    monkeypatch.setenv("REPLY_LOG_KEY", Fernet.generate_key().decode())
    log = ReplyLog(str(path))
    log.record(user_id="42", username="Kruskal", query="old question", reply="old answer")
    log.record(user_id="42", username="Kruskal", query="new question", reply="new answer",
               model="gpt-6-luna", reasoning_effort="none", needs_web=True, version="3.21.0")
    assert main(["--path", str(path), "--tail", "1"]) == 0
    out = capsys.readouterr().out
    assert "new question" in out and "new answer" in out
    assert "old question" not in out
    assert "model: gpt-6-luna, effort none, routing wanted web, v3.21.0" in out
    assert main(["--path", str(path), "--json", "--tail", "1"]) == 0
    assert '"query": "new question"' in capsys.readouterr().out


def test_the_command_line_explains_a_wrong_or_missing_log(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.sqlite3"
    assert main(["--path", str(missing)]) == 1
    assert "no reply log at" in capsys.readouterr().out

    path = tmp_path / "replies.sqlite3"
    monkeypatch.setenv("REPLY_LOG_KEY", Fernet.generate_key().decode())
    ReplyLog(str(path)).record(user_id="42", username="Kruskal", query="q", reply="a")
    monkeypatch.setenv("REPLY_LOG_KEY", Fernet.generate_key().decode())
    assert main(["--path", str(path)]) == 1
    assert "is the key the one that wrote it?" in capsys.readouterr().out


def test_a_failed_delete_is_raised_instead_of_reported_as_success(tmp_path, monkeypatch):
    log = _log(tmp_path / "replies.sqlite3")
    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(log, "_connect", locked)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        log.delete_user("42")


def test_an_unreadable_log_does_not_get_a_new_key(tmp_path, monkeypatch):
    path = tmp_path / "replies.sqlite3"
    key_path = tmp_path / "reply_log.key"
    path.write_bytes(b"not a database")
    monkeypatch.delenv("REPLY_LOG_KEY", raising=False)
    monkeypatch.setattr(Config, "REPLY_LOG_KEY_PATH", str(key_path))
    with pytest.raises(RuntimeError, match="could not check"):
        ReplyLog(str(path))
    assert not key_path.exists()


def test_a_missing_key_does_not_silently_replace_the_key(tmp_path, monkeypatch):
    path = tmp_path / "replies.sqlite3"
    key_path = tmp_path / "reply_log.key"
    monkeypatch.delenv("REPLY_LOG_KEY", raising=False)
    monkeypatch.setattr(Config, "REPLY_LOG_KEY_PATH", str(key_path))
    ReplyLog(str(path), key=Fernet.generate_key()).record(
        user_id="42", username="Kruskal", query="q", reply="a")
    with pytest.raises(RuntimeError, match="key file"):
        ReplyLog(str(path))
    assert not key_path.exists()


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
