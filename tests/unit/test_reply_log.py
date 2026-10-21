"""The local reply log keeps enough context to explain a bad answer later."""
import json
import sqlite3

from src.reply_log import ReplyLog


def test_a_reply_is_stored_with_its_context_routing_and_tools(tmp_path):
    path = tmp_path / "replies.sqlite3"
    log = ReplyLog(str(path), keep_days=30)
    log.record(
        user_id="42", username="Kruskal", channel_id="99", query="what dev tool",
        conversation_history="Alex: earlier\nChronoChunk: answer", attached_context="file: notes.md\nhi",
        image_count=1, routing={"course": False, "rewrite": False},
        tool_calls=[{"name": "search_course", "arguments": {"query": "dev tool"}, "result": "found"}],
        reply="it is Teams", files=["notes.pdf"], sources={"10": ("Week 9", "https://example.test/9")},
        model_calls=2, cost=0.0123, latency_ms=1500,
    )
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT user_id, channel_id, query, conversation_history, attached_context, image_count, "
            "routing, tool_calls, reply, files, sources, model_calls, cost, latency_ms FROM replies"
        ).fetchone()
    assert row[:6] == ("42", "99", "what dev tool", "Alex: earlier\nChronoChunk: answer", "file: notes.md\nhi", 1)
    assert json.loads(row[6]) == {"course": False, "rewrite": False}
    assert json.loads(row[7])[0]["result"] == "found"
    assert row[8:11] == ("it is Teams", '["notes.pdf"]', '{"10": ["Week 9", "https://example.test/9"]}')
    assert row[11:] == (2, 0.0123, 1500)


def test_rows_older_than_the_retention_window_are_removed(tmp_path):
    path = tmp_path / "replies.sqlite3"
    log = ReplyLog(str(path), keep_days=1)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO replies (created_at, reply) VALUES ('2000-01-01T00:00:00+00:00', 'old')")
    log.record(reply="new")
    with sqlite3.connect(path) as db:
        assert [row[0] for row in db.execute("SELECT reply FROM replies")] == ["new"]
