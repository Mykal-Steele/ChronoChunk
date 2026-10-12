"""Unit tests for the database wrapper, the user store and the one-time import of the old JSON files."""
import json
import sqlite3
import threading

import pytest

from src.database import MIGRATIONS, Database
from src.legacy_import import import_legacy_data
from src.usage_guard import UsageGuard
from src.user_store import UserStore


def test_a_new_database_has_every_table_and_remembers_its_version(tmp_path):
    path = str(tmp_path / "state" / "bot.db")
    db = Database(path)
    tables = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "facts", "topics", "conversations", "channel_settings", "usage_daily", "rate_events", "meta",
            "page_notes"} <= tables
    assert db.query("PRAGMA user_version")[0][0] == len(MIGRATIONS)
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"
    db.close()
    Database(path).close()  # opening it again runs no migration twice


def test_a_failed_transaction_changes_nothing(database):
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as db:
            db.execute("INSERT INTO meta(key, value) VALUES ('a', '1')")
            db.execute("INSERT INTO meta(key, value) VALUES ('a', '2')")
    assert database.get_meta("a") is None


def test_meta_values_can_be_overwritten(database):
    database.set_meta("k", "1")
    database.set_meta("k", "2")
    assert database.get_meta("k") == "2"


def test_many_threads_can_write_at_once(database):
    store = UserStore(database)
    store.get_profile("1", "A")

    def work(n):
        for i in range(20):
            store.change_facts("1", add=[f"fact {n}-{i}"])
    threads = [threading.Thread(target=work, args=(n,)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store.get_profile("1")["facts"]) == 100


def test_facts_are_not_written_for_a_user_who_was_forgotten_and_came_back(database):
    store = UserStore(database)
    before = store.get_profile("1", "A")
    store.forget("1")
    after = store.get_profile("1", "A")
    assert before["created_at"] != after["created_at"]
    assert store.change_facts("1", add=["old fact"], if_created_at=before["created_at"]) is False
    assert store.change_facts("1", add=["new fact"], if_created_at=after["created_at"]) is True
    assert [f["content"] for f in store.get_profile("1")["facts"]] == ["new fact"]


def test_one_users_facts_cannot_be_deleted_through_another(database):
    store = UserStore(database)
    store.get_profile("1", "A")
    store.get_profile("2", "B")
    store.change_facts("1", add=["mine"])
    fact_id = store.get_profile("1")["facts"][0]["id"]
    assert store.change_facts("2", remove_ids=[fact_id]) is False
    assert len(store.get_profile("1")["facts"]) == 1


# ── import of the old JSON files ───────────────────────────────────────────────

def _old_files(tmp_path):
    users, state = tmp_path / "user_data", tmp_path / "state"
    users.mkdir()
    state.mkdir()
    (users / "111.json").write_text(json.dumps({
        "user_id": "111", "username": "Alex", "created_at": "2025-03-24T10:00:00",
        "facts": [{"content": "You like cats", "extracted_from": "i like cats", "timestamp": "2025-03-25T09:00:00"}, "You are tall"],
        "topics_of_interest": ["hiking"],
        "conversation_history": [{"timestamp": f"2025-04-{d:02d}T00:00:00", "user_message": f"m{d}", "bot_response": "r"} for d in range(1, 26)],
        "last_interaction": "2025-04-25T00:00:00",
    }), encoding="utf-8")
    (users / "222.json").write_text(json.dumps({"username": "NoIdInside", "facts": []}), encoding="utf-8")
    (users / "333.json").write_text("{ broken", encoding="utf-8")
    (state / "ai_usage.json").write_text(json.dumps({
        "cycle_start": "2026-09-14", "cycle_cost": 0.50, "day": "2026-10-11", "day_cost": 0.10,
        "requests": 81, "input_tokens": 1000, "output_tokens": 200}), encoding="utf-8")
    (state / "channel_settings.json").write_text(json.dumps({"42": {"read_recent_posts": False}}), encoding="utf-8")
    return str(users), str(state)


def test_the_old_files_are_copied_in_once(database, tmp_path, monkeypatch):
    from datetime import date
    users_dir, state_dir = _old_files(tmp_path)
    assert import_legacy_data(database, users_dir, state_dir) == {"users": 2, "ledger": True, "channels": 1}

    store = UserStore(database)
    alex = store.get_profile("111")
    assert alex["username"] == "Alex" and alex["created_at"] == "2025-03-24T10:00:00"
    assert [f["content"] for f in alex["facts"]] == ["You like cats", "You are tall"]
    assert alex["facts"][0]["extracted_from"] == "i like cats"
    assert alex["topics_of_interest"] == ["hiking"]
    assert len(alex["conversation_history"]) == 20 and alex["conversation_history"][-1]["user_message"] == "m25"
    assert store.get_profile("222")["username"] == "NoIdInside"
    assert store.user_count() == 2  # the broken file was skipped

    from src.channel_settings import ChannelSettings
    assert ChannelSettings(database).reads_recent_posts(42) is False

    monkeypatch.setattr(UsageGuard, "_today", staticmethod(lambda: date(2026, 10, 11)))
    summary = UsageGuard(database, monthly_budget=15, daily_budget=2, reset_day=14).summary()
    assert summary["cycle_cost"] == pytest.approx(0.50) and summary["day_cost"] == pytest.approx(0.10)
    assert summary["requests"] == 81

    # a second run copies nothing, and the old files are still there
    assert import_legacy_data(database, users_dir, state_dir) == {}
    assert store.user_count() == 2
    assert (tmp_path / "user_data" / "111.json").exists()


def test_an_install_with_no_old_files_imports_nothing(database, tmp_path):
    assert import_legacy_data(database, str(tmp_path / "none"), str(tmp_path / "none")) == {"users": 0, "ledger": False, "channels": 0}
    assert import_legacy_data(database, str(tmp_path / "none"), str(tmp_path / "none")) == {}
