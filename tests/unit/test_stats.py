"""/stats: everything the bot has used since its spending record began."""
from datetime import date, datetime, timezone

from src import backups, stats


def _use(database, day, feature, model, calls, tokens_in, tokens_out, cost, user_id="1"):
    database.execute(
        "INSERT INTO usage_daily(day, user_id, feature, model, requests, input_tokens, output_tokens, cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (day, user_id, feature, model, calls, tokens_in, tokens_out, cost))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def test_a_new_bot_has_nothing_to_count():
    assert stats.stats_summary() == "nothing to count yet, i havent made a single ai call"


def test_the_totals_cover_every_day_job_and_model(database, tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "backup_folder", lambda: str(tmp_path))
    today = _today().isoformat()
    _use(database, "2025-01-20", "", "gpt-5-mini", 1000, 4_000_000, 50_000, 1.00, user_id="")
    _use(database, "2025-01-21", "chat", "gpt-6-luna", 300, 900_000, 20_000, 0.30)
    _use(database, "2025-01-21", "facts", "gpt-5-nano", 200, 100_000, 5_000, 0.02)
    _use(database, today, "chat", "gpt-6-luna", 40, 150_000, 2_500, 0.05)
    _use(database, today, "pdf_notes", "gpt-6-luna", 10, 60_000, 9_000, 0.03)
    database.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES ('1', 'Kruskal', 'x', 'x')")
    database.execute("INSERT INTO facts(user_id, content, created_at) VALUES ('1', 'likes hiking', 'x')")

    text = stats.stats_summary()

    days = _today().toordinal() - date(2025, 1, 20).toordinal() + 1
    assert f"counting       since Jan 20, 2025, {days:,} days, used on 3 of them" in text
    assert "ai calls       1,550" in text
    assert "tokens         5.2M in, 86.5K out" in text
    assert "ai cost        $1.40, $0.0009 a call" in text
    assert "today          50 calls, $0.08" in text
    assert "last 7 days    50 calls, $0.08" in text
    assert "busiest day    Jan 20, 2025: 1,000 calls, $1.00" in text
    assert "  earlier      1,000 calls, $1.00" in text
    assert "  chat         340 calls, $0.35" in text
    assert "  memory       200 calls, $0.02" in text
    assert "  pdf pages    10 calls, $0.03" in text
    assert "  gpt-6-luna   350 calls, $0.38" in text
    assert "  gpt-5-mini   1,000 calls, $1.00" in text
    assert "people         1 remembered, 1 facts about them" in text
    assert "last copy      none yet" in text
    assert len(text) < 1900


def test_no_name_and_no_user_id_is_shown(database):
    _use(database, "2025-01-20", "chat", "gpt-6-luna", 5, 1000, 100, 0.01, user_id="424242424242")
    database.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES ('424242424242', 'Kruskal', 'x', 'x')")
    text = stats.stats_summary()
    assert "424242424242" not in text and "Kruskal" not in text


def test_forgetting_a_person_keeps_the_totals(database):
    from src.user_store import UserStore
    _use(database, "2025-01-20", "chat", "gpt-6-luna", 5, 1000, 100, 0.01, user_id="7")
    database.execute("INSERT INTO users(user_id, username, created_at, last_interaction) VALUES ('7', 'Prim', 'x', 'x')")
    UserStore(database).forget("7")
    text = stats.stats_summary()
    assert "ai calls       5" in text and "people         0 remembered" in text


def test_the_last_copy_is_named_by_its_day(database, tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "backup_folder", lambda: str(tmp_path))
    _use(database, "2025-01-20", "chat", "gpt-6-luna", 5, 1000, 100, 0.01)
    backups.backup_now(database, str(tmp_path))
    today = _today()
    assert f"last copy      {today:%b} {today.day}, {today.year}" in stats.stats_summary()
