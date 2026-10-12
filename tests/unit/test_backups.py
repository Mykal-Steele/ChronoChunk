"""The daily copy of the database."""
import os
import sqlite3
from datetime import datetime, timezone

from src import backups
from src.database import Database


def test_a_copy_holds_everything_the_database_did(tmp_path):
    database = Database(str(tmp_path / "live.db"))
    database.execute(
        "INSERT INTO usage_daily(day, user_id, feature, model, requests, input_tokens, output_tokens, cost) "
        "VALUES ('2025-01-20', '1', 'chat', 'gpt-6-luna', 7, 1000, 100, 0.25)")

    path = backups.backup_now(database, str(tmp_path / "copies"))

    assert os.path.basename(path) == f"chronochunk-{datetime.now(timezone.utc):%Y-%m-%d}.db"
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT requests, cost FROM usage_daily").fetchall() == [(7, 0.25)]
    assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    copy.close()


def test_a_second_copy_on_the_same_day_replaces_the_first(tmp_path):
    database = Database(str(tmp_path / "live.db"))
    folder = str(tmp_path / "copies")
    backups.backup_now(database, folder)
    database.set_meta("after", "the first copy")
    path = backups.backup_now(database, folder)

    assert os.listdir(folder) == [os.path.basename(path)]
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT value FROM meta WHERE key = 'after'").fetchone() == ("the first copy",)
    copy.close()


def test_only_the_newest_week_of_copies_is_kept(tmp_path):
    folder = tmp_path / "copies"
    folder.mkdir()
    for day in range(1, 11):
        (folder / f"chronochunk-2025-01-{day:02d}.db").write_bytes(b"old")
    (folder / "notes.txt").write_text("not a copy")

    backups.backup_now(Database(str(tmp_path / "live.db")), str(folder))

    kept = sorted(name for name in os.listdir(folder) if name.endswith(".db"))
    assert len(kept) == backups.KEEP_DAYS
    assert kept[0] == "chronochunk-2025-01-05.db"
    assert (folder / "notes.txt").exists()


def test_the_last_copy_day_is_read_from_the_newest_file(tmp_path):
    assert backups.last_backup_day(str(tmp_path)) is None
    (tmp_path / "chronochunk-2025-01-03.db").write_bytes(b"x")
    (tmp_path / "chronochunk-2025-01-09.db").write_bytes(b"x")
    assert backups.last_backup_day(str(tmp_path)).isoformat() == "2025-01-09"
